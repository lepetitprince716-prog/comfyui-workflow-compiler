"""Dynamic prompts: the text the frontend exports for a string widget.

Frontend 1.59.1 replaces ``serializeValue`` of every widget whose input spec has
``dynamicPrompts: true`` (``src/extensions/core/dynamicPrompts.ts``, flag set for STRING
inputs in ``useStringWidget.ts``) with ``processDynamicPrompt(widget.value)``
(``packages/shared-frontend-utils/src/formatUtil.ts``). ``graphToPrompt`` uses
``serializeValue``, so the exported prompt holds the processed text while the canvas keeps
the raw value. This module replays that function and inverts it for authored text:

* :func:`process` is a port of ``processDynamicPrompt``; the random pick of a choice
  block ``{a|b}`` takes the first option and ``random`` tells whether the pick matters.
* :func:`escape` writes a widget value that exports exactly the authored text.
* :func:`dynamic_widgets` names the widgets of a node that go through the function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from cwc.nodedefs.types import InputSpec, NodeDef, WidgetKind, WidgetSpec

__all__ = ["DynamicText", "process", "escape", "is_dynamic_input", "dynamic_widgets"]

# JavaScript ``/\/\*[\s\S]*?\*\/|\/\/.*/g``: ``.`` stops at the four line terminators.
_COMMENT = re.compile(r"/\*[\s\S]*?\*/|//[^\n\r\u2028\u2029]*")
_ESCAPED_SPECIAL = re.compile(r"\\([{}|])")
_SPECIAL = "{}|"


@dataclass(frozen=True)
class DynamicText:
    text: str  # the export with the first option taken at every choice block
    random: bool  # the frontend's pick can change the export


def process(text: str) -> DynamicText:
    """What ``processDynamicPrompt(text)`` returns, with the first option at every choice block.

    ``random`` is true when some choice block has options whose processed results differ,
    or an option is itself random. Raises ``ValueError`` when blocks nest too deep for the
    recursion (the frontend overflows its stack on the same text).
    """
    try:
        result, random = _process(text)
    except RecursionError:
        raise ValueError("dynamic prompt blocks are nested too deep") from None
    return DynamicText(result, random)


def _process(source: str) -> tuple[str, bool]:
    source = _COMMENT.sub("", source)  # comments go before any escape is read
    size = len(source)
    out: list[str] = []
    random = False
    i = 0
    while i < size:
        char = source[i]
        i += 1
        if char == "\\":
            out.append("\\" + (source[i] if i < size else "undefined"))  # input[i++] past the end
            i += 1
        elif char == "{":
            options: list[str] = []
            choice: list[str] = []
            depth = 0
            while i < size:
                char = source[i]
                i += 1
                if char == "\\":
                    choice.append("\\" + (source[i] if i < size else "undefined"))
                    i += 1
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    if not depth:
                        break
                    depth -= 1
                elif char == "|" and not depth:
                    options.append("".join(choice))
                    choice = []
                    continue
                choice.append(char)
            options.append("".join(choice))  # an unterminated block still keeps its option
            results = [_process(option) for option in options]
            first = results[0][0]
            random = random or any(flag or text != first for text, flag in results)
            out.append(first)
        else:
            out.append(char)
    return _ESCAPED_SPECIAL.sub(r"\1", "".join(out)), random


def escape(text: str) -> str | None:
    """A widget value that exports exactly ``text`` and is not random, or None.

    Braces and pipes get a backslash and a ``/`` that would start a comment is written
    ``{/}`` (a one-option block); the rest stays as typed. A run of backslashes before a
    brace or a pipe is written one backslash longer, because the export drops the one
    next to the character (``\\{`` needs a block to stay a literal). The candidate is
    checked with :func:`process`; None means no spelling was found, which happens for an odd
    number of backslashes at the end (the export reads past the end of the text).
    """
    parts: list[str] = []
    paired = False  # the last character written is a backslash that would pair with the next one
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            end = i
            while end < len(text) and text[end] == "\\":
                end += 1
            run = end - i
            following = text[end] if end < len(text) else ""
            if following and following in _SPECIAL:
                # run + 1 backslashes leave ``run`` after the export drops the last one; an
                # even count pairs up completely, so a ``{`` after it has to come from a block.
                parts.append("\\" * (run + 1) + ("{\\{}" if following == "{" and run % 2 else following))
                i = end + 1
                paired = False
            else:
                parts.append("\\" * run)
                i = end
                paired = run % 2 == 1
            continue
        following = text[i + 1] if i + 1 < len(text) else ""
        i += 1
        if char in _SPECIAL:
            parts.append("\\" + char)
        elif char == "/" and following in ("/", "*"):
            if paired:
                # ``\{/}`` would escape the brace: keep this slash and wrap the next character
                parts.append("/{" + following + "}")
                i += 1
            else:
                parts.append("{/}")
        else:
            parts.append(char)
        paired = False
    candidate = "".join(parts)
    try:
        checked = process(candidate)
    except ValueError:
        return None
    return candidate if checked == DynamicText(text, False) else None


def is_dynamic_input(spec: InputSpec) -> bool:
    """A STRING input whose widget the frontend runs through ``processDynamicPrompt``."""
    return spec.type == "STRING" and spec.options.get("dynamicPrompts") is True


def dynamic_widgets(nodedef: NodeDef, layout: Sequence[WidgetSpec]) -> frozenset[str]:
    """Names of the widgets in ``layout`` that go through ``processDynamicPrompt``.

    The flag is read from the node's own input specs, which every tier keeps (a truth or
    template entry only replaces the widgets). The sub-inputs of a DynamicCombo are not
    counted, although they can carry the flag. This is read from frontend 1.59.1, not
    measured, because no core type has a flagged STRING under a DynamicCombo:
    ``Comfy.DynamicPrompts`` replaces ``serializeValue`` once, in ``nodeCreated``
    (``src/extensions/core/dynamicPrompts.ts``), while the DynamicCombo value setter
    removes and adds every sub-widget again (``src/core/graph/widgets/dynamicWidgets.ts``)
    and ``configure`` sets that value on load. A loaded sub-widget therefore keeps the
    flag but exports its raw text, and neither escaping nor processing applies to it.
    """
    names = {spec.name for spec in nodedef.inputs if spec.widget and is_dynamic_input(spec)}
    return frozenset(
        widget.name
        for widget in layout
        if widget.kind is WidgetKind.VALUE and widget.name is not None and widget.name in names
    )
