"""Input limits and the one JSON reader every ingest path uses.

The limits keep a hostile or broken file from exhausting memory or the stack. They are
far above any real workflow: large production canvases have a few hundred nodes and are
a few megabytes.
"""

from __future__ import annotations

import json
import os
import re
from itertools import accumulate
from pathlib import Path
from typing import Any

from cwc.report import Code, CwcError, ExitCode, Finding, Severity

MAX_JSON_BYTES = 64 * 1024 * 1024  # a JSON document, or the text of one PNG chunk
MAX_PNG_BYTES = 256 * 1024 * 1024  # a whole PNG file, image data included
# Nesting of JSON objects and arrays. The deepest real documents are node definitions, 26 levels
# in all; each nested dynamic combo adds 6, so 64 leaves room for about nine of them.
MAX_DEPTH = 64
MAX_NODES = 5000  # canvas nodes, or authoring entries
MAX_LINKS = 20000  # canvas link tuples
MAX_REPORTED = 20  # shape problems listed before the rest are summarised


def input_error(code: Code, message: str, *, hint: str = "", nodes: tuple[str, ...] = ()) -> CwcError:
    return input_errors([Finding(code, Severity.ERROR, message, nodes=nodes, hint=hint)])


def input_errors(findings: list[Finding]) -> CwcError:
    return CwcError(ExitCode.INPUT_ERROR, findings)


def read_file(path: str | os.PathLike[str], what: str) -> bytes:
    """The bytes of an input file; a file larger than any input may be is refused unread."""
    file = Path(path)
    try:
        size = file.stat().st_size
        if size > MAX_PNG_BYTES:
            raise input_error(Code.I3, f"{what} {file} is {size} bytes; the limit is {MAX_PNG_BYTES}")
        return file.read_bytes()
    except OSError as exc:
        raise input_error(Code.I1, f"cannot read {what} {file}: {exc.strerror or exc}") from None


def parse_json(data: bytes, what: str) -> Any:
    """Decode UTF-8 JSON (a leading BOM is allowed) within the size and depth limits.

    Rejects what ``JSON.parse`` rejects but Python accepts: ``NaN`` and ``Infinity``.
    """
    if len(data) > MAX_JSON_BYTES:
        raise input_error(Code.I3, f"{what} is {len(data)} bytes; the limit is {MAX_JSON_BYTES}")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise input_error(Code.I1, f"{what} is not UTF-8 text: {exc.reason} at byte {exc.start}") from None
    if exceeds_depth(text):
        raise input_error(Code.I3, f"{what} nests deeper than the limit of {MAX_DEPTH} levels")
    try:
        return json.loads(text, parse_constant=reject_constant)
    except ValueError as exc:
        raise input_error(Code.I1, f"{what} is not valid JSON: {exc}") from None


_BRACKETS = re.compile(r"[\[\]{}]")
_STEP = {"[": 1, "{": 1, "]": -1, "}": -1}
_WINDOW = 1 << 20  # characters scanned at a time, so the scan's own memory stays small


def exceeds_depth(text: str) -> bool:
    """Whether ``text`` opens more than ``MAX_DEPTH`` arrays and objects at once.

    Every reader of external JSON asks this before it parses, so the parser never sees a deeper
    document: its C scanner recurses once per level and, on some Python versions and systems,
    runs off the end of the C stack instead of raising ``RecursionError``. The count ignores
    whether the document is valid JSON (that is the parser's to report) and counts every
    bracket outside a string.

    The scan is linear and runs in C. Escaped backslashes and escaped quotes are cut out (of a
    run of backslashes the odd one is left, and that is the one that escapes), so every
    remaining quote opens or closes a string. The text between a closing quote and the next
    opening one lies outside all strings, and only its brackets are counted. The text is cut
    into windows, with the open-or-closed state carried across, to keep the pieces few.
    """
    plain = text.replace("\\\\", "").replace('\\"', "")
    inside = False
    depth = 0
    for start in range(0, len(plain), _WINDOW):
        pieces = plain[start : start + _WINDOW].split('"')
        outside = pieces[1::2] if inside else pieces[::2]
        inside ^= len(pieces) % 2 == 0
        brackets = _BRACKETS.findall("".join(outside))
        levels = list(accumulate(map(_STEP.__getitem__, brackets), initial=depth))
        if max(levels) > MAX_DEPTH:
            return True
        depth = levels[-1]
    return False


def reject_constant(name: str) -> Any:
    """``json.loads`` ``parse_constant`` hook: ``NaN`` and ``Infinity`` are not JSON."""
    raise ValueError(f"{name} is not allowed in JSON")
