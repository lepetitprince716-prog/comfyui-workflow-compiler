"""In-canvas plain-language guide note (M3): summary, Markdown and placement.

Builds a concise MarkdownNote placed in the top-left guide column reserved by
``layout`` when an authoring document carries a workflow title, group notes or
node notes. Uses ``Behavior.NOTE`` from node definitions so no node type name
appears in code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Mapping

from cwc.annotate.types import AUTHOR_GROUP_PREFIX, Annotations, Zone
from cwc.canvas.envelope import TITLE_FONT, TITLE_ROOM, title_text
from cwc.canvas.geometry import GRID, snap_up
from cwc.canvas.model import Canvas, NodeMode, id_order
from cwc.canvas.textwidth import text_width
from cwc.layout.types import LayoutResult, Preset
from cwc.nodedefs.types import Behavior, NodeDef, WidgetKind
from cwc.normalize.types import Authoring
from cwc.prompt.slots import DefLookup
from cwc.verify.geometry_screen import GUIDE_MAX_HEIGHT, GUIDE_ROLE

GUIDE_X = 0
GUIDE_Y = 40
GUIDE_MIN_H = 180
GUIDE_MAX_BODY_H = GUIDE_MAX_HEIGHT - 80  # keeps classic & Vue envelopes <= 860 inside first screen
GUIDE_LINE_H = 22
GUIDE_PAD_H = 56
GUIDE_TITLE = "Quick Guide"
_MAX_CONTROLS = 8
_MAX_NOTES = 10
_MAX_OPTIONALS = 6
_CHARS_PER_LINE = 44

__all__ = ["GuideSummary", "guide_markdown", "place_guide", "summary"]


@dataclass(frozen=True)
class GuideSummary:
    title: str
    flow: tuple[str, ...]
    controls: tuple[str, ...]
    notes: tuple[tuple[str, str], ...]
    optional_modules: tuple[tuple[str, str | None], ...]


def _clean_inline(text: str) -> str:
    cleaned = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    return " ".join(cleaned.split())


def _group_titles_by_author_id(plan: Annotations, laid: LayoutResult, canvas: Canvas) -> dict[str, str]:
    """Author group id -> rendered canvas group title."""
    out: dict[str, str] = {}
    groups = canvas.groups
    for gplan in plan.groups:
        if not gplan.key.startswith(AUTHOR_GROUP_PREFIX):
            continue
        gid = gplan.key.removeprefix(AUTHOR_GROUP_PREFIX)
        for member in gplan.members:
            idx = laid.membership.get(member)
            if idx is not None and 0 <= idx < len(groups):
                out[gid] = groups[idx].title
                break
    return out


def _optional_group_notes(authoring: Authoring, plan: Annotations, laid: LayoutResult, canvas: Canvas) -> list[tuple[str, str | None]]:
    groups = canvas.groups
    seen: set[int] = set()
    items: list[tuple[str, str | None]] = []
    for gplan in plan.groups:
        if gplan.zone is not Zone.OPTIONAL:
            continue
        idx = None
        for member in gplan.members:
            cand = laid.membership.get(member)
            if cand is not None and 0 <= cand < len(groups):
                idx = cand
                break
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        note: str | None = None
        for member in gplan.members:
            intent = authoring.intents.get(member)
            if intent is None:
                continue
            if intent.ui_group and intent.ui_group in authoring.workflow.groups:
                gnote = authoring.workflow.groups[intent.ui_group].note
                if gnote and gnote.strip():
                    note = gnote.strip()
                    break
            if intent.ui_note and intent.ui_note.strip():
                note = intent.ui_note.strip()
                break
        items.append((groups[idx].title, note))
    return items


def summary(
    authoring: Authoring,
    plan: Annotations,
    laid: LayoutResult,
    canvas: Canvas,
    defs: DefLookup,
) -> GuideSummary | None:
    """Build a plain-language guide summary, or None when the document has no title or notes."""
    wf = authoring.workflow
    has_title = bool(wf.title and wf.title.strip())
    group_notes_raw = {gid: info.note.strip() for gid, info in wf.groups.items() if info.note and info.note.strip()}
    node_notes_raw = {
        pid: intent.ui_note.strip()
        for pid, intent in authoring.intents.items()
        if intent.ui_note and intent.ui_note.strip()
    }
    if not has_title and not group_notes_raw and not node_notes_raw:
        return None

    nodes = canvas.node_map()
    groups = canvas.groups
    opt_indices = {
        laid.membership[m]
        for g in plan.groups
        if g.zone is Zone.OPTIONAL
        for m in g.members
        if laid.membership.get(m) is not None
    }
    flow = tuple(
        group.title
        for idx, group in enumerate(groups)
        if idx not in opt_indices and group.title.strip()
    )

    cockpit_set = set(laid.cockpit)
    cockpit_keys = [
        key
        for key, idx in laid.membership.items()
        if idx in cockpit_set and key in nodes
    ]
    cockpit_keys.sort(key=lambda k: (nodes[k].pos[1], nodes[k].pos[0], id_order(k)))
    controls = tuple(title_text(nodes[k], defs.get(nodes[k].type)) for k in cockpit_keys)

    optional_modules = tuple(_optional_group_notes(authoring, plan, laid, canvas))
    opt_note_texts = {n for _, n in optional_modules if n}

    gid_to_title = _group_titles_by_author_id(plan, laid, canvas)
    notes: list[tuple[str, str]] = []
    for gid in sorted(group_notes_raw):
        text = group_notes_raw[gid]
        if text in opt_note_texts:
            continue
        label = gid_to_title.get(gid) or (wf.groups[gid].title or gid)
        notes.append((label, text))

    for pid in sorted(node_notes_raw, key=id_order):
        text = node_notes_raw[pid]
        if text in opt_note_texts:
            continue
        node = nodes.get(pid)
        label = title_text(node, defs.get(node.type)) if node is not None else pid
        notes.append((label, text))

    title = wf.title.strip() if has_title and wf.title else GUIDE_TITLE
    return GuideSummary(
        title=title,
        flow=flow,
        controls=controls,
        notes=tuple(notes),
        optional_modules=optional_modules,
    )


def _estimate_lines(lines: list[str], chars_per_line: int = _CHARS_PER_LINE) -> int:
    total = 0
    for line in lines:
        if not line:
            total += 1
            continue
        plain = re.sub(r"[#*`]", "", line)
        w = sum(2 if ord(ch) > 0x2E7F else 1 for ch in plain)
        total += max(1, (w + chars_per_line - 1) // chars_per_line)
    return total


def guide_markdown(info: GuideSummary, *, max_visual_lines: int = 28) -> str:
    """Render ``info`` into concise Markdown compatible with Classic Tiptap and Vue marked."""
    lines: list[str] = [f"# {_clean_inline(info.title)}"]
    if info.flow:
        lines.extend(["", f"**Flow:** {' → '.join(_clean_inline(t) for t in info.flow)}"])

    if info.notes:
        lines.extend(["", "**Quick Notes:**"])
        shown_notes = info.notes[:_MAX_NOTES]
        for label, text in shown_notes:
            lines.append(f"- **{_clean_inline(label)}**: {_clean_inline(text)}")
        if len(info.notes) > _MAX_NOTES:
            lines.append(f"- ... (+{len(info.notes) - _MAX_NOTES} more)")

    if info.controls:
        lines.extend(["", "**Controls (edit here):**"])
        shown_ctrl = info.controls[:_MAX_CONTROLS]
        for ctrl in shown_ctrl:
            lines.append(f"- {_clean_inline(ctrl)}")
        if len(info.controls) > _MAX_CONTROLS:
            lines.append(f"- ... (+{len(info.controls) - _MAX_CONTROLS} more)")

    if info.optional_modules:
        lines.extend(["", "**Optional (select group + `Ctrl+B`):**"])
        shown_opt = info.optional_modules[:_MAX_OPTIONALS]
        for name, note in shown_opt:
            tail = f": {_clean_inline(note)}" if note else ""
            lines.append(f"- **{_clean_inline(name)}**{tail}")
        if len(info.optional_modules) > _MAX_OPTIONALS:
            lines.append(f"- ... (+{len(info.optional_modules) - _MAX_OPTIONALS} more)")

    while len(lines) > 3 and _estimate_lines(lines) > max_visual_lines:
        lines.pop()
    return "\n".join(lines)


def _find_note_def(defs: DefLookup) -> NodeDef | None:
    """Find the builtin Markdown note definition via Behavior.NOTE (no hardcoded type names)."""
    fallback: NodeDef | None = None
    if not hasattr(defs, "__iter__"):
        return None
    for item in defs:  # type: ignore[union-attr]
        nd = defs.get(item) if isinstance(item, str) else item
        if nd is None or nd.behavior is not Behavior.NOTE:
            continue
        if any(w.kind is WidgetKind.VALUE and w.type == "MARKDOWN" for w in nd.widgets):
            return nd
        if fallback is None:
            fallback = nd
    return fallback


def _fit_title(text: str, width: float) -> str:
    if text_width(text, TITLE_FONT) + TITLE_ROOM <= width:
        return text
    return GUIDE_TITLE


def place_guide(
    canvas: Canvas,
    authoring: Authoring,
    plan: Annotations,
    laid: LayoutResult,
    preset: Preset,
    defs: DefLookup,
) -> tuple[str | None, LayoutResult]:
    """Insert a MarkdownNote guide card in the reserved left guide lane when title/notes exist."""
    if not plan.reserve_guide:
        return None, laid
    info = summary(authoring, plan, laid, canvas, defs)
    if info is None:
        return None, laid
    note_def = _find_note_def(defs)
    if note_def is None:
        return None, laid

    md = guide_markdown(info)
    width = float(snap_up(preset.guide_width))
    visual_lines = _estimate_lines(md.splitlines())
    raw_h = GUIDE_PAD_H + visual_lines * GUIDE_LINE_H
    height = float(min(GUIDE_MAX_BODY_H, max(GUIDE_MIN_H, snap_up(raw_h))))

    existing_max = max((int(n.id) for n in canvas.nodes if isinstance(n.id, int)), default=0)
    last_id = int(canvas.raw.get("last_node_id") or 0)
    new_id = max(existing_max, last_id) + 1
    canvas.raw["last_node_id"] = new_id

    widget_name = note_def.widgets[0].name if note_def.widgets and note_def.widgets[0].name else "text"
    node_raw = {
        "id": new_id,
        "type": note_def.type,
        "pos": [GUIDE_X, GUIDE_Y],
        "size": [int(width), int(height)],
        "flags": {},
        "order": len(canvas.nodes),
        "mode": int(NodeMode.ALWAYS),
        "inputs": [],
        "outputs": [],
        "title": _fit_title(info.title, width),
        "properties": {"cwc_role": GUIDE_ROLE},
        "widgets_values": [md],
        "widgets_values_named": {widget_name: md},
        "color": "#432",
        "bgcolor": "#653",
    }
    canvas.raw.setdefault("nodes", []).append(node_raw)
    new_membership: Mapping[str, int | None] = {**laid.membership, str(new_id): None}
    return str(new_id), replace(laid, membership=new_membership)
