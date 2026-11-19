"""Slots into items: one slot instance (a column), or, in a slot stacked along x, one group.

A placed thing is an *item*: one slot instance (a column), or, in a slot stacked along x,
one group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from cwc.annotate.types import GroupPlan
from cwc.canvas.geometry import snap_up
from cwc.layout.inner import Part
from cwc.layout.types import Axis, Slot

GAP = 40  # between items, units, stacked groups and the rows of a lower band


@dataclass
class Seq:
    """Boxes laid out along ``axis``, ``GAP`` apart, aligned at the start of the other axis."""

    axis: Axis
    boxes: list[Seq | Part]

    def size(self) -> tuple[float, float]:
        sizes = [_size(box) for box in self.boxes]
        if not sizes:
            return (0.0, 0.0)
        if self.axis is Axis.X:
            return (sum(w for w, _ in sizes) + GAP * (len(sizes) - 1), max(h for _, h in sizes))
        return (max(w for w, _ in sizes), sum(h for _, h in sizes) + GAP * (len(sizes) - 1))

    def place(self, x: float, y: float, out: list[tuple[Part, float, float]]) -> None:
        for box in self.boxes:
            w, h = _size(box)
            if isinstance(box, Seq):
                box.place(x, y, out)
            else:
                out.append((box, x, y))
            if self.axis is Axis.X:
                x = float(snap_up(x + w + GAP))
            else:
                y = float(snap_up(y + h + GAP))


def _size(box: Seq | Part) -> tuple[float, float]:
    return box.size() if isinstance(box, Seq) else (box.box.w, box.box.h)


@dataclass(eq=False)
class Item:
    """``columns``: when ``content`` is a stack of several sub-columns, the placed groups of each
    (``content.boxes`` holds one box per entry, in order); empty otherwise."""

    band: int  # preset band
    slot: int
    instance: int
    stage: int | None
    groups: list[str]
    content: Seq
    x: float = 0.0
    y: float = 0.0
    columns: list[list[str]] = field(default_factory=list)
    w: float = field(init=False)
    h: float = field(init=False)

    def __post_init__(self) -> None:
        w, h = self.content.size()
        self.w, self.h = float(snap_up(w)), float(snap_up(h))


def _unit(parts: list[Part]) -> Seq:
    """A group's parts in a row along x; a tiered cockpit's tiers (``Part.tier``) stacked along
    y, the upper first, each tier a row of its parts."""
    count = max(part.tier for part in parts) + 1
    if count == 1:
        return Seq(Axis.X, list(parts))
    return Seq(Axis.Y, [Seq(Axis.X, [part for part in parts if part.tier == t]) for t in range(count)])


def _downstream(start: str, edges: Mapping[str, set[str]]) -> set[str]:
    seen = {start}
    todo = [start]
    while todo:
        for nxt in sorted(edges.get(todo.pop(), ())):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    seen.discard(start)
    return seen


def _segment(units: Mapping[str, list[Part]], key: str) -> tuple[str, int]:
    """The plan group a placed group belongs to and which of its segments it is."""
    part = units[key][0]
    return part.plan.key, part.segment


def _stack(
    groups: list[GroupPlan], units: Mapping[str, list[Part]], edges: Mapping[str, set[str]], exempt: frozenset[str]
) -> tuple[Seq, list[list[str]]]:
    """Groups stacked along y, in sub-columns: a group goes one sub-column right of every
    earlier group of the stack that links to it, or reaches it and is not reached back, and one right of
    its group's previous segment; the others stack in the first sub-column. Reach follows the
    links between top-level groups, through groups outside the stack too: a path that leaves
    the stack and comes back puts its outside groups between the two ends, and stacked in one
    sub-column the ends leave them no x to sit at. A link into ``exempt`` (the cockpit frames,
    whose inbound links run right to left by design) is not followed. One sub-column is the plain
    stack. Also the groups of each sub-column (``Item.columns``), empty for one."""
    forward = {a: {b for b in targets if b not in exempt} for a, targets in edges.items()}
    reach = {g.key: _downstream(g.key, forward) for g in groups}
    column: dict[str, int] = {}
    for i, g in enumerate(groups):
        plan, segment = _segment(units, g.key)
        after = [
            column[p.key] + 1
            for p in groups[:i]
            if (g.key not in exempt and g.key in edges.get(p.key, ())) or (g.key in reach[p.key] and p.key not in reach[g.key])
        ]
        after += [column[p.key] + 1 for p in groups[:i] if _segment(units, p.key) == (plan, segment - 1)]
        column[g.key] = max(after, default=0)
    count = max(column.values(), default=0) + 1
    if count == 1:
        return Seq(Axis.Y, [_unit(units[g.key]) for g in groups]), []
    columns = [[g for g in groups if column[g.key] == c] for c in range(count)]
    return Seq(Axis.X, [Seq(Axis.Y, [_unit(units[g.key]) for g in c]) for c in columns]), [[g.key for g in c] for c in columns]


def _slot_items(
    b: int, s: int, slot: Slot, groups: list[GroupPlan], units: Mapping[str, list[Part]], edges: Mapping[str, set[str]], exempt: frozenset[str] = frozenset()
) -> list[Item]:
    """The items of one slot. A slot stacked along y lays its groups out by ``_stack``, and so
    does each instance of a repeated slot stacked along y.

    A repeated slot (along x; layout refuses a slot repeated along y) opens one instance per
    stage (``GroupPlan.stage``) of its opening groups, in ascending order; an attached group
    joins the instance of the lowest-ranked opening group downstream of it, the last instance
    when it feeds none; with no opening group in the slot, the attached groups open one
    instance together, at their lowest stage. The stages are
    the ranks of the placed groups, segments included, so a segment and the groups bridging
    it to its group's previous segment have stages of their own, in data-flow order.

    ``groups`` may hold groups of other zones that placement moved here from an earlier
    main-row slot, being downstream of a group of this slot (:func:`cwc.layout.place._slots`).
    Their zones are not this slot's, so they are not attached: in a repeated slot each opens
    the instance of its own stage."""
    if not groups:
        return []

    def content(members: list[GroupPlan]) -> tuple[Seq, list[list[str]]]:
        if slot.stack is Axis.Y:
            return _stack(members, units, edges, exempt)
        return Seq(slot.stack, [_unit(units[g.key]) for g in members]), []

    def item(i: int, stage: int | None, members: list[GroupPlan]) -> Item:
        seq, columns = content(members)
        return Item(b, s, i, stage, [g.key for g in members], seq, columns=columns)

    if slot.repeat is None:
        if slot.stack is Axis.X:
            return [Item(b, s, 0, None, [g.key], Seq(Axis.X, [_unit(units[g.key])])) for g in groups]
        return [item(0, None, groups)]
    opening = [g for g in groups if g.zone not in slot.attach]
    if not opening:
        # Only attach groups in the slot: there is no instance to join, so they open one
        # together, at the lowest stage among them.
        return [item(0, min(g.stage for g in groups), groups)]
    rank = {g.key: g.stage for g in opening}
    stages = sorted(set(rank.values()))
    at: dict[str, int] = {}
    for g in groups:
        if g.zone in slot.attach:
            below = [rank[k] for k in _downstream(g.key, edges) if k in rank]
            at[g.key] = min(below) if below else stages[-1]
        else:
            at[g.key] = rank[g.key]
    return [item(i, stage, [g for g in groups if at[g.key] == stage]) for i, stage in enumerate(sorted(set(at.values())))]
