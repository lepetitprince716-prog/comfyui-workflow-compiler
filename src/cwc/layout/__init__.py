"""Layout: places nodes and groups by a preset. Writes ``pos``, ``size`` (width only grows;
height grows only on the preview nodes of a preset's preview area) and ``groups``; never
anything else.

Contract types are in :mod:`cwc.layout.types`, presets in :mod:`cwc.layout.presets`;
``docs/ARCHITECTURE.md`` has the rules. Inside a group: :mod:`cwc.layout.inner`; between
groups: :mod:`cwc.layout.place`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from cwc.annotate.graph import GraphView, group_ranks
from cwc.annotate.palette import group_colour, zone_class
from cwc.annotate.types import COCKPIT_KEY, Annotations, CockpitSlot, GroupPlan, Source, Zone
from cwc.canvas.direction import Direction, direction
from cwc.canvas.envelope import Renderer, shows_preview
from cwc.canvas.geometry import Rect, snap_up
from cwc.canvas.links import canvas_splines
from cwc.canvas.model import Canvas
from cwc.layout.inner import Fill, Graph, Part, group_parts, segment_zone
from cwc.layout.items import Item
from cwc.layout.place import place
from cwc.layout.section import SCREEN_W, Placed
from cwc.layout.types import Axis, Column, Corridor, CorridorKind, LayoutResult, Numbering, Point, Preset, Widening
from cwc.prompt.slots import DefLookup

__all__ = ["layout"]

GUIDE_HEIGHT = 900
GUIDE_GAP = 40
FONT_SIZE = 24  # the frontend's default group title size
TRACK = 20


def layout(
    canvas: Canvas, annotations: Annotations, preset: Preset, defs: DefLookup, *, widen: Widening | None = None
) -> LayoutResult:
    """Place ``canvas`` by ``annotations`` and ``preset``. ``defs`` are the node definitions the
    checks get (``check_geometry(canvas, defs, ...)``): node boxes come from
    ``cwc.canvas.envelope.box_facts(node, defs.get(node.type))``, computed after ``apply_notes``
    because a cockpit title changes the title width. Round 1 has ``widen=None``; round 2
    (M3 routing) passes the round-1 result and the counted tracks, and layout keeps every
    round-1 decision, moving groups only to widen corridors."""
    if widen is not None:
        raise NotImplementedError("layout: round 2 (widen) is M3 routing work")
    if annotations.source is Source.KEPT:
        raise NotImplementedError("layout: tidy of kept groups is not implemented yet")
    missing = _unimplemented(preset)
    if missing:
        raise NotImplementedError(f"layout: preset {preset.name!r} uses {', '.join(missing)}, which layout does not implement yet")
    graph = Graph.of(canvas, annotations.nodes, defs)
    cockpit_core = None
    if preset.cockpit_split is not None:
        cockpit_plan = next((g for g in annotations.groups if g.key == COCKPIT_KEY), None)
        if cockpit_plan is not None and len(cockpit_plan.members) >= preset.cockpit_split.min_nodes:
            cockpit_core = preset.cockpit_split.core
    units = _units(graph, annotations, cockpit_core, _fill_of(preset, defs, graph))
    tops, top_of, cut = _segments(list(annotations.children(None)), units, _top_of(annotations), graph)
    tops = _frames_after_cockpit(_ranked(tops, top_of, GraphView(canvas, defs)))
    cockpit = frozenset(g.key for g in tops if g.zone is Zone.COCKPIT)
    preview_groups = frozenset(
        top_of[key]
        for key, node in graph.nodes.items()
        if key in top_of
        and not node.collapsed
        and (nodedef := defs.get(node.type)) is not None
        and nodedef.output_node
        and shows_preview(nodedef)
    )
    links: dict[tuple[str, str], int] = {}
    for _, origin, target in graph.edges:
        a, b = top_of.get(origin), top_of.get(target)
        if a is not None and b is not None and a != b:
            links[(a, b)] = links.get((a, b), 0) + 1
    x0 = float(preset.guide_width + GUIDE_GAP) if annotations.reserve_guide else 0.0
    guide = float(GUIDE_HEIGHT) if annotations.reserve_guide else 0.0
    placed = place(
        preset,
        tops,
        units,
        links,
        x0=x0,
        exempt=cockpit,
        fits=_fits(canvas, graph, top_of, cockpit, wrap_rows=preset.wrap_rows),
        guide=guide,
        preview_groups=preview_groups,
    )
    return _write(canvas, annotations, preset, placed, x0, cockpit, cut)


def _unimplemented(preset: Preset) -> list[str]:
    """The preset features placement does not read yet. Each would be silently ignored, giving a
    canvas that looks like the preset and is not, so layout refuses the preset instead; remove a
    line here when its feature is implemented."""
    out = []
    if preset.axis is not Axis.X:
        out.append(f"axis {preset.axis.value}")
    if any(slot.under_guide for band in preset.bands for slot in band.slots):
        out.append("under_guide")
    # Instances repeated along y stack at one x, so a later stage's links run back left.
    if any(slot.repeat is Axis.Y for band in preset.bands for slot in band.slots):
        out.append("repeat y")
    if preset.tray is not None:
        out.append("tray")
    reserved = (CorridorKind.BUS, CorridorKind.SKYBRIDGE, CorridorKind.SPINE)  # the corridors _write reserves
    kinds = {rule.kind.value for rule in preset.corridors if rule.kind not in reserved}
    out.extend(f"a {kind} corridor" for kind in sorted(kinds))
    return out


def _fits(
    canvas: Canvas, graph: Graph, top: Mapping[str, str], cockpit: frozenset[str], *, wrap_rows: bool = False
) -> Callable[[Placed], bool]:
    """Whether a placement keeps G5 within its limit, counted as G5 counts it
    (:mod:`cwc.canvas.direction`), the links into the cockpit frames left out: into their own
    members and into the members of groups nested in them, as ``check_geometry`` leaves out the
    links into every group in ``LayoutResult.cockpit`` and the groups nested in them. ``top``:
    each grouped node's placed group (a segment's key for a member of a cut group), which
    ``cockpit`` names. When ``wrap_rows`` is true, links descending strictly from an upper
    top-level group to a lower one (``target.y >= origin.bottom``) are also left out as
    carriage-return row wraps, matching G5.

    A link runs right to left when its output anchor's x exceeds its input anchor's x, and an
    anchor's x is the node's x plus an offset fixed by the node's size and slots. The offsets
    and the counted links are read once from the canvas as it stands; each placement then only
    adds node x positions, with no curve built (the wrap search asks once per candidate cut)."""
    inside = {key for key in top if top[key] in cockpit}
    exempt = frozenset(link for link, _, target in graph.edges if target in inside)
    nodes = canvas.node_map()
    curves = canvas_splines(canvas, dict(graph.facts), Renderer.CLASSIC)
    base = direction(canvas, curves, exempt)
    by_id = {link.id: link for link in canvas.links}
    counted = []
    for link_id in base.considered:
        link, curve = by_id[link_id], curves[link_id]
        origin, target = str(link.origin_id), str(link.target_id)
        counted.append((link_id, origin, curve.p0[0] - nodes[origin].pos[0], target, curve.p3[0] - nodes[target].pos[0]))

    def fits(placed: Placed) -> bool:
        x = {key: node.pos[0] for key, node in nodes.items()}
        span_y: dict[str, tuple[float, float]] = {}

        def walk(part: Part, at: float, band_y: tuple[float, float]) -> None:
            for key, (px, _) in part.nodes.items():
                x[key] = at + px
                span_y[key] = band_y
            for child, (cx, _) in part.children:
                walk(child, at + cx, band_y)

        for part, at, ay in placed.parts:
            walk(part, at, (ay, ay + part.box.h))
        if wrap_rows:
            active = [
                row
                for row in counted
                if not (
                    row[1] in span_y
                    and row[3] in span_y
                    and span_y[row[3]][0] >= span_y[row[1]][1]
                )
            ]
            considered = tuple(row[0] for row in active)
            backward = tuple(i for i, a, out_dx, b, in_dx in active if x[a] + out_dx > x[b] + in_dx)
            return not Direction(considered=considered, backward=backward, feedback=base.feedback).over
        backward = tuple(i for i, a, out_dx, b, in_dx in counted if x[a] + out_dx > x[b] + in_dx)
        return not Direction(considered=base.considered, backward=backward, feedback=base.feedback).over

    return fits


def _segments(
    tops: list[GroupPlan], units: dict[str, list[Part]], top_of: dict[str, str], graph: Graph
) -> tuple[list[GroupPlan], dict[str, str], frozenset[str]]:
    """The top-level groups as placement goes through them: a group cut into segments
    (:func:`cwc.layout.inner.segments`) becomes one placed group per segment, keyed
    ``<key>#<segment>``, consecutive in plan order, so a group joining two segments sits
    between them as any bridge does. Each segment takes the latest zone of its own steering
    members (:func:`cwc.layout.inner.segment_zone`), so it goes to the slot of that zone, or to
    a later main-row slot when :func:`cwc.layout.place._slots` moves it: a
    group the author drew across the data flow is cut, a member of an earlier zone that cannot
    move right with the rest stays in a segment of its own zone, and each piece then has a
    place that does not run back. ``units`` gains the segments' parts; ``top_of`` maps each
    node to its placed group. Also the keys of the cut groups."""
    out: list[GroupPlan] = []
    cut: set[str] = set()
    for group in tops:
        parts = units[group.key]
        count = max(part.segment for part in parts) + 1
        if count == 1:
            out.append(group)
            continue
        cut.add(group.key)
        for s in range(count):
            key = _segment_key(group.key, s)
            units[key] = [part for part in parts if part.segment == s]
            members = [node for part in units[key] for node in part.nodes]
            out.append(replace(group, key=key, zone=segment_zone(graph, members)))
            for node in members:
                top_of[node] = key
    return out, top_of, frozenset(cut)


def _segment_key(key: str, segment: int) -> str:
    return f"{key}#{segment}"


def _placed_key(part: Part, cut: frozenset[str]) -> str:
    """The placed group a top-level part belongs to: its plan group, or for a group in ``cut``
    its segment."""
    return _segment_key(part.plan.key, part.segment) if part.plan.key in cut else part.plan.key


def _ranked(tops: list[GroupPlan], top_of: dict[str, str], view: GraphView) -> list[GroupPlan]:
    """The placed groups with ``stage`` ranked again over the graph of placed groups, segments
    included, by annotate's ``group_ranks`` over the same links and execution order, and
    sorted by it, plan order breaking ties. A segment shares its plan group's stage in the
    plan; ranked on its own it follows the groups that feed it, so a group fed from one
    segment and feeding the next ranks between them. With no group cut, the graph is the
    plan's and nothing changes."""
    members: dict[str, list[str]] = {g.key: [] for g in tops}
    for node, key in top_of.items():
        members[key].append(node)
    rank = group_ranks(members, [(e.origin, e.target) for e in view.edges], view.order)
    position = {g.key: i for i, g in enumerate(tops)}
    ordered = sorted(tops, key=lambda g: (rank[g.key], position[g.key]))
    return [g if g.stage == rank[g.key] else replace(g, stage=rank[g.key]) for g in ordered]


def _frames_after_cockpit(tops: list[GroupPlan]) -> list[GroupPlan]:
    """The placed groups with every other cockpit frame (a top-level group of the cockpit zone)
    moved right after the cockpit group, in their order; nothing else moves. The frames share
    the cockpit slot, which stacks them in plan order, so the panel opens with the cockpit
    group. Without a cockpit group, or with one frame, the order is unchanged."""
    if not any(g.key == COCKPIT_KEY for g in tops):
        return tops
    others = [g for g in tops if g.zone is Zone.COCKPIT and g.key != COCKPIT_KEY]
    rest = [g for g in tops if g not in others]
    at = next(i for i, g in enumerate(rest) if g.key == COCKPIT_KEY) + 1
    return rest[:at] + others + rest[at:]


def _top_of(annotations: Annotations) -> dict[str, str]:
    parent = {g.key: g.parent for g in annotations.groups}
    out = {}
    for group in annotations.groups:
        top = group.key
        while parent[top] is not None:
            top = parent[top]  # type: ignore[assignment]
        for key in group.members:
            out[key] = top
    return out


def _fill_of(preset: Preset, defs: DefLookup, graph: Graph) -> Callable[[Zone], Fill | None]:
    """The preview area a top-level group of each zone gets: the preset's preview, for the zones
    homed in the preview zone's slot, sized ``share`` of the first screen's width by
    ``min_height``; the preview nodes are the expanded nodes whose definition shows a preview."""
    preview = preset.preview
    if preview is None:
        return lambda zone: None
    nodes = frozenset(
        key
        for key, node in graph.nodes.items()
        if not node.collapsed and (nodedef := defs.get(node.type)) is not None and shows_preview(nodedef)
    )
    # rounded first: 0.55 * 1600 is 880.0000000000001 in floating point, which would snap to 900
    fill = Fill(snap_up(round(preview.share * SCREEN_W, 6)), preview.min_height, nodes)
    home = preset.home(preview.zone)
    allowed = {z for z in Zone if preset.home(z) == home} | set(preview.extra_zones)
    return lambda zone: fill if zone in allowed else None


def _units(
    graph: Graph, annotations: Annotations, core: tuple[CockpitSlot, ...] | None, fill_of: Callable[[Zone], Fill | None]
) -> dict[str, list[Part]]:
    """Every group's parts, children before parents; ``core`` tiers the cockpit group
    (``Preset.cockpit_split``) and no other cockpit frame; ``fill_of`` gives the preview area by
    the top-level group's zone, which its nested groups share, and by each segment's zone for a
    cut group."""
    out: dict[str, list[Part]] = {}

    def build(group: GroupPlan, fill: Fill | None) -> list[Part]:
        children = [build(child, fill) for child in annotations.children(group.key)]
        core_of = core if group.key == COCKPIT_KEY else None
        out[group.key] = group_parts(graph, group, children, core_of, fill, fill_of if group.parent is None else None)
        return out[group.key]

    for top in annotations.children(None):
        build(top, fill_of(top.zone))
    return out


def _label(preset: Preset, number: int, letter: str) -> str:
    if preset.numbering is Numbering.STEP:
        return f"Step {number}{letter}"
    return f"{number:02d}{letter}"


def _write(
    canvas: Canvas,
    annotations: Annotations,
    preset: Preset,
    placed: Placed,
    x0: float,
    cockpit: frozenset[str],
    cut: frozenset[str],
) -> LayoutResult:
    """Write the groups and positions. ``cockpit``: the placed keys of the cockpit frames, a
    segment's for a frame that is a segment of a group in ``cut``."""
    nodes = canvas.node_map()
    entries: list[dict[str, Any]] = []
    labels: dict[str, str] = {}
    index: dict[str, int] = {}
    membership: dict[str, int | None] = {key: None for key in annotations.ungrouped}
    waypoints: dict[int, tuple[Point, ...]] = {}
    numbers: dict[str, int] = {}
    cockpit_index: list[int] = []

    def emit(part: Part, x: float, y: float, top: bool) -> None:
        plan = part.plan
        if plan.key not in numbers:
            numbers[plan.key] = len(numbers) + 1
        label = _label(preset, numbers[plan.key], part.letter)
        colour = group_colour(preset.palette, plan.colour or zone_class(plan.zone))
        index[part.key] = len(entries)
        if top and _placed_key(part, cut) in cockpit:  # a nested group is found by containment
            cockpit_index.append(len(entries))
        labels[part.key] = label
        entries.append(
            {
                "id": len(entries) + 1,
                "title": f"{label} {plan.name}",
                "bounding": [_num(x), _num(y), _num(part.box.w), _num(part.box.h)],
                "color": colour,
                "font_size": FONT_SIZE,
                "flags": {},
            }
        )
        for key, (px, py) in part.nodes.items():
            nodes[key].set_pos(x + px, y + py)
            membership[key] = index[part.key]
        for link, points in part.waypoints.items():
            waypoints[link] = tuple((x + px, y + py) for px, py in points)
        for child, (cx, cy) in part.children:
            emit(child, x + cx, y + cy, False)

    for part, x, y in placed.parts:
        emit(part, x, y, True)
    canvas.set_groups(entries)
    columns = tuple(
        Column(
            band=placed.band_of[i],
            slot=item.slot,
            rect=Rect(item.x, item.y, item.w, item.h),
            groups=tuple(dict.fromkeys(part.plan.key for part in _parts(item))),
            stage=item.stage,
        )
        for i, item in enumerate(placed.items)
    )
    kinds = {rule.kind for rule in preset.corridors}
    corridors = tuple(
        Corridor(id=f"bus:{i}", kind=CorridorKind.BUS, rect=gap, tracks=int(gap.h // TRACK))
        for i, gap in enumerate(placed.gaps)
        if CorridorKind.BUS in kinds
    ) + tuple(
        Corridor(id=f"skybridge:{i}", kind=CorridorKind.SKYBRIDGE, rect=strip, tracks=int(strip.h // TRACK))
        for i, strip in enumerate(placed.skybridges)
    ) + tuple(Corridor(id=f"spine:{s}", kind=CorridorKind.SPINE, rect=gap, tracks=int(gap.w // TRACK)) for s, gap in placed.spines)
    guide = Rect(0, 0, preset.guide_width, GUIDE_HEIGHT) if annotations.reserve_guide else None
    return LayoutResult(
        preset=preset.name,
        bands=tuple(placed.bands),
        columns=columns,
        corridors=corridors,
        guide=guide,
        labels=labels,
        group_index=index,
        membership=membership,
        waypoints=waypoints,
        cockpit=tuple(cockpit_index),
    )


def _parts(item: Item) -> list[Part]:
    """The top-level parts an item places, in order (a segment's key is not a plan key)."""
    out: list[tuple[Part, float, float]] = []
    item.content.place(0.0, 0.0, out)
    return [part for part, _, _ in out]


def _num(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value
