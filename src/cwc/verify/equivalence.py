"""Equivalence checks Q1-Q2: the canvas, read back by the frontend's own export rules
(:func:`cwc.prompt.from_canvas.canvas_to_prompt`), gives the strict prompts.

* Q1, all enabled: a copy with every bypassed node (mode 4) set to mode 0 exports
  ``authoring.full``.
* Q2, factory state: the canvas as written exports ``authoring.factory``.

The two exports are also what the bypass-closure checks read, so they are made once here.
"""

from __future__ import annotations

from dataclasses import dataclass

from cwc.canvas.model import Canvas, NodeMode
from cwc.nodedefs.derive import widget_layout
from cwc.normalize.types import Authoring
from cwc.prompt.from_canvas import canvas_to_prompt
from cwc.prompt.model import Prompt, prompt_diff
from cwc.report import Code, Finding, Severity
from cwc.verify.graph import CanvasFacts


@dataclass(frozen=True)
class Export:
    """One state's prompt, or why the frontend rules give none."""

    prompt: Prompt | None
    error: str | None = None


@dataclass(frozen=True)
class Exports:
    full: Export  # every mode 4 node enabled
    factory: Export  # modes as written


def export_states(facts: CanvasFacts) -> Exports:
    return Exports(full=_export(facts, all_enabled(facts.canvas)), factory=_export(facts, facts.canvas))


def all_enabled(canvas: Canvas) -> Canvas:
    """A copy of ``canvas`` with every bypassed node set to mode 0 (Ctrl+B on all of them)."""
    copy = Canvas(canvas.to_json())
    for raw in copy.raw.get("nodes", []):
        if raw.get("mode") == NodeMode.BYPASS:
            raw["mode"] = int(NodeMode.ALWAYS)
    return copy


def _export(facts: CanvasFacts, canvas: Canvas) -> Export:
    try:
        return Export(canvas_to_prompt(canvas, facts.defs, widget_layout=widget_layout))
    except ValueError as exc:  # ResolveError included
        return Export(None, str(exc))


def check_equivalence(exports: Exports, authoring: Authoring) -> list[Finding]:
    return _compare(Code.Q1, "all-enabled", exports.full, authoring.full) + _compare(
        Code.Q2, "factory-state", exports.factory, authoring.factory
    )


def _compare(code: Code, state: str, export: Export, expected: Prompt) -> list[Finding]:
    if export.prompt is None:
        return [Finding(code, Severity.ERROR, f"the {state} canvas cannot be exported to a prompt: {export.error}")]
    lines = prompt_diff(expected, export.prompt)
    if not lines:
        return []
    return [
        Finding(
            code,
            Severity.ERROR,
            f"the {state} canvas exports a prompt that differs from the strict prompt in {len(lines)} place(s)",
            data={"diff": lines},
        )
    ]
