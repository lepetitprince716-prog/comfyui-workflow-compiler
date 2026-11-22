"""E5: literal widget values against their declared type, range and options.

Basic types and ``min``/``max`` are errors. A combo value missing from the option list
is only a warning: the list names files on the machine that made the definitions, and an
offline snapshot carries no file lists at all (an empty list is not checked).

A string for a widget the frontend runs through ``processDynamicPrompt`` must have a
spelling that exports unchanged (:func:`cwc.prompt.dynamic.escape`); an odd number of
backslashes at the end has none. :func:`check_dynamic_default` applies the same check to
the defaults that fill in for an omitted or linked input, which instantiate also writes.
"""

from __future__ import annotations

from typing import Any

from cwc.ingest.limits import is_finite
from cwc.nodedefs.types import InputSpec
from cwc.normalize.diagnostics import Findings, listing
from cwc.prompt.dynamic import escape, is_dynamic_input
from cwc.prompt.model import values_equal
from cwc.report import Code

INVALID = object()  # returned for a value that failed a blocking check

INT, FLOAT, BOOLEAN, STRING, COMBO = "INT", "FLOAT", "BOOLEAN", "STRING", "COMBO"
_FILE_SUFFIXES = (
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".gguf",
    ".onnx",
    ".sft",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".mp4",
    ".webm",
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
)


def _looks_like_file_list(options: list[Any]) -> bool:
    for item in options:
        if not isinstance(item, str):
            continue
        low = item.lower()
        if "/" in low or "\\" in low or low.endswith(_FILE_SUFFIXES):
            return True
    return False


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_literal(spec: InputSpec, value: Any, author_id: str, out: Findings, *, portable: bool = False) -> Any:
    """The value to keep (integral floats become ints for INT), or :data:`INVALID`."""
    where = f"node {author_id!r} input {spec.name!r}"

    def bad(expected: str, hint: str = "", data: dict[str, Any] | None = None) -> Any:
        out.error(Code.E5, f"{where} expects {expected}, got {value!r}", [author_id], hint, data)
        return INVALID

    if spec.dynamic_options:
        keys = [key for key, _ in spec.dynamic_options]
        if isinstance(value, str) and value in keys:
            return value
        return bad("one of its options", f"options: {listing(keys)}", {"options": keys})
    kind = spec.type
    if kind == INT:
        if not _number(value) or not is_finite(value) or (isinstance(value, float) and not value.is_integer()):
            return bad("an integer")
        return _range(spec, int(value), where, author_id, out)
    if kind == FLOAT:
        if not _number(value) or not is_finite(value):
            return bad("a number")
        return _range(spec, value, where, author_id, out)
    if kind == BOOLEAN:
        return value if isinstance(value, bool) else bad("true or false")
    if kind == STRING:
        if not isinstance(value, str):
            return bad("a string")
        if is_dynamic_input(spec) and escape(value) is None:
            out.error(Code.E5, _unspellable(where, value), [author_id], hint="end the text with another character, or write the backslash before one")
            return INVALID
        return value
    if kind == COMBO:
        return _combo(spec, value, where, author_id, out, bad, portable=portable)
    return value  # custom widget types carry their own value shapes


def _unspellable(where: str, value: str) -> str:
    return (
        f"{where} cannot be exported unchanged: the frontend runs this text through its dynamic-prompt processing, "
        f"and an odd number of backslashes at the end has no spelling (the last one exports as '\\undefined'), got {value!r}"
    )


def check_dynamic_default(spec_name: str, value: Any, author_id: str, out: Findings, *, linked: bool) -> bool:
    """False, with an E5 error, when the declared default ``value`` of a dynamic-prompt input has no spelling.

    The default is written to the canvas whenever the author leaves the input out or links it.
    """
    if not isinstance(value, str) or escape(value) is not None:
        return True
    where = f"node {author_id!r} input {spec_name!r} (declared default)"
    hint = (
        "the input is linked, but the node still stores its default: correct the default in the node definitions"
        if linked
        else "write this input as a literal text, or correct the default in the node definitions"
    )
    out.error(Code.E5, _unspellable(where, value), [author_id], hint=hint)
    return False


def _range(spec: InputSpec, value: int | float, where: str, author_id: str, out: Findings) -> Any:
    low, high = spec.options.get("min"), spec.options.get("max")
    low = low if _number(low) else None
    high = high if _number(high) else None
    if (low is not None and value < low) or (high is not None and value > high):
        span = f"{'-inf' if low is None else low} .. {'inf' if high is None else high}"
        out.error(Code.E5, f"{where} value {value!r} is outside {span}", [author_id], f"allowed range: {span}", {"min": low, "max": high})
        return INVALID
    return value


def _combo(spec: InputSpec, value: Any, where: str, author_id: str, out: Findings, bad: Any, *, portable: bool = False) -> Any:
    options = list(spec.combo_values or ())
    flag = spec.options.get("multi_select")
    multi = flag is not None and flag is not False and flag != 0 and flag != ""  # JavaScript truthiness
    if multi:
        if not isinstance(value, list):
            return bad('a list of options written as {"__value__": [...]}')
        picked = value
    elif isinstance(value, (list, dict)) or value is None:
        return bad("one option value")
    else:
        picked = [value]
    missing = [v for v in picked if options and not any(values_equal(v, o) for o in options)]
    if missing and not (portable and _looks_like_file_list(options)):
        out.warning(
            Code.E5,
            f"{where}: {', '.join(repr(v) for v in missing)} is not in the current option list",
            [author_id],
            hint=f"known options: {listing(options)}; a file missing here may still exist on the target machine",
            data={"options": options},
        )
    return value
