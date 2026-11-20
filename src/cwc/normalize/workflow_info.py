"""The top-level ``_workflow`` key: graph-level information stripped from the prompt.

Shape (every key but ``format`` optional)::

    {"format": 1, "title": str, "locale": str, "layout": {"preset": str},
     "groups": {group_id: {"title": str, "note": str}},
     "models": [{"name": str, "url": str, "directory": str}]}

Unknown keys are errors: a misspelt key would otherwise vanish without a trace.
"""

from __future__ import annotations

from typing import Any

from cwc.ingest.authoring import WORKFLOW_INFO_KEY
from cwc.normalize.diagnostics import Findings, no_effect
from cwc.normalize.types import GroupInfo, ModelSource, WorkflowInfo
from cwc.report import Code

FORMAT = 1
TOP_KEYS = ("format", "title", "locale", "layout", "groups", "models")
LAYOUT_KEYS = ("preset",)
GROUP_KEYS = ("title", "note")
MODEL_KEYS = ("name", "url", "directory")


class _Reader:
    def __init__(self, out: Findings):
        self.out = out

    def fail(self, path: str, message: str, hint: str = "") -> None:
        self.out.error(Code.E7, f"{path}: {message}", hint=hint)

    def object(self, path: str, value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            self.fail(path, "is not an object")
            return None
        unknown = sorted(k for k in value if k not in keys)
        if unknown:
            self.fail(path, f"unknown keys {', '.join(unknown)}", hint=f"allowed keys: {', '.join(keys)}")
        return value

    def string(self, path: str, value: Any) -> str | None:
        if not isinstance(value, str):
            self.fail(path, "is not a string")
            return None
        return value


def parse_workflow(raw: Any, out: Findings) -> WorkflowInfo:
    """Parse ``_workflow`` (None when the document has none). Problems are E7 errors."""
    if raw is None:
        return WorkflowInfo()
    read = _Reader(out)
    top = read.object(WORKFLOW_INFO_KEY, raw, TOP_KEYS)
    if top is None:
        return WorkflowInfo()
    fmt = top.get("format")
    if isinstance(fmt, bool) or fmt != FORMAT:
        read.fail(f"{WORKFLOW_INFO_KEY}.format", f"must be {FORMAT}, got {fmt!r}")
    title = read.string(f"{WORKFLOW_INFO_KEY}.title", top["title"]) if "title" in top else None
    locale = read.string(f"{WORKFLOW_INFO_KEY}.locale", top["locale"]) if "locale" in top else None
    for key, value in (("title", title), ("locale", locale)):
        if value is not None:
            no_effect(out, f"{WORKFLOW_INFO_KEY}.{key}")
    preset = None
    if "layout" in top:
        layout = read.object(f"{WORKFLOW_INFO_KEY}.layout", top["layout"], LAYOUT_KEYS)
        if layout is not None and "preset" in layout:
            preset = read.string(f"{WORKFLOW_INFO_KEY}.layout.preset", layout["preset"])
    groups = _groups(read, top.get("groups", {}))
    models = _models(read, top.get("models", []))
    return WorkflowInfo(
        format=FORMAT,
        title=title,
        locale=locale if locale is not None else WorkflowInfo.locale,
        preset=preset,
        groups=groups,
        models=models,
    )


def _groups(read: _Reader, raw: Any) -> dict[str, GroupInfo]:
    path = f"{WORKFLOW_INFO_KEY}.groups"
    if not isinstance(raw, dict):
        read.fail(path, "is not an object mapping group ids to {title, note}")
        return {}
    groups: dict[str, GroupInfo] = {}
    for group_id in sorted(raw):
        entry = read.object(f"{path}.{group_id}", raw[group_id], GROUP_KEYS)
        if entry is None:
            continue
        title = read.string(f"{path}.{group_id}.title", entry["title"]) if "title" in entry else None
        note = read.string(f"{path}.{group_id}.note", entry["note"]) if "note" in entry else None
        if note is not None:
            no_effect(read.out, f"{path}.{group_id}.note")
        groups[group_id] = GroupInfo(title=title, note=note)
    return groups


def _models(read: _Reader, raw: Any) -> tuple[ModelSource, ...]:
    path = f"{WORKFLOW_INFO_KEY}.models"
    if not isinstance(raw, list):
        read.fail(path, "is not a list of {name, url, directory}")
        return ()
    models: list[ModelSource] = []
    for index, item in enumerate(raw):
        entry = read.object(f"{path}[{index}]", item, MODEL_KEYS)
        if entry is None:
            continue
        fields = [read.string(f"{path}[{index}].{key}", entry.get(key)) for key in MODEL_KEYS]
        if all(isinstance(value, str) for value in fields):
            models.append(ModelSource(*fields))  # type: ignore[arg-type]
    return tuple(models)
