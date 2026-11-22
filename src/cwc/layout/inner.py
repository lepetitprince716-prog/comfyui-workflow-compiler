"""Inside a group: layers, the C1 split, in-layer order, widths and positions.

Every position here is relative to the top-left corner of the part's box, which sits at
``(0, 0)``. :mod:`cwc.layout.place` moves whole parts; nothing here knows where a part goes.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Callable, Collection, Mapping

from cwc.annotate.notes import steering_members
from cwc.annotate.rules import flow_level, latest_zone
from cwc.annotate.types import CockpitSlot, GroupPlan, NodeNote, Zone
from cwc.canvas.envelope import VUE_MIN_WIDTH, BoxFacts, box_facts, envelope, expanded_centre, readable_width
from cwc.canvas.geometry import GRID, Rect, snap_down, snap_up
from cwc.canvas.model import Canvas, Node, id_order
from cwc.layout.types import Point
from cwc.normalize.types import Role

if TYPE_CHECKING:
    from cwc.prompt.slots import DefLookup

NODE_GAP = 40  # between envelopes in a layer
LAYER_GAP = 80  # between layers, plus TRACK per link through the gap
TRACK = 20
DUMMY = 40  # reserved slot height for a link spanning a layer
PAD = 20  # group box margin on every side
TITLE_BAND = 30  # extra margin on top for the group title
MAX_LAYERS = 5  # C1 split thresholds
MAX_EXPANDED = 12
LAST = 1 << 30  # sort position of a missing order

_SLOTS = list(CockpitSlot)


@dataclass(frozen=True)
class Graph:
    """What the inside of a group needs from the canvas: nodes, box facts, notes and links
    (``(link id, origin key, target key)``, self-links left out)."""

    nodes: Mapping[str, Node]
    facts: Mapping[str, BoxFacts]
    notes: Mapping[str, NodeNote]
    edges: tuple[tuple[int, str, str], ...]

    @classmethod
    def of(cls, canvas: Canvas, notes: Mapping[str, NodeNote], defs: DefLookup) -> Graph:
        """The graph of ``canvas`` as it stands, with annotate's ``notes``; box facts from
        ``defs``. Links to a node not on the canvas are left out."""
        nodes = canvas.node_map()
        return cls(
            nodes=nodes,
            facts={key: box_facts(node, defs.get(node.type)) for key, node in nodes.items()},
            notes=notes,
            edges=tuple(
                (link.id, str(link.origin_id), str(link.target_id))
                for link in sorted(canvas.links, key=lambda link: link.id)
                if str(link.origin_id) in nodes and str(link.target_id) in nodes and link.origin_id != link.target_id
            ),
        )

    @cached_property
    def reach(self) -> dict[str, frozenset[str]]:
        """Every node to the nodes it reaches along links, itself left out."""
        out: dict[str, list[str]] = {key: [] for key in self.nodes}
        for _, origin, target in self.edges:
            out[origin].append(target)
        reach: dict[str, frozenset[str]] = {}
        for start in self.nodes:
            seen: set[str] = {start}
            todo = [start]
            while todo:
                for nxt in out[todo.pop()]:
                    if nxt not in seen:
                        seen.add(nxt)
                        todo.append(nxt)
            seen.discard(start)
            reach[start] = frozenset(seen)
        return reach


@dataclass(frozen=True)
class Fill:
    """A preview area (``Preset.preview``): a part holding any of ``nodes`` (the expanded nodes
    that show a preview, ``cwc.canvas.envelope.shows_preview``) is made at least ``width`` wide
    and ``height`` high, and those nodes grow, width and height, to fill it."""

    width: int
    height: int
    nodes: frozenset[str]


@dataclass
class Part:
    """One written group box. ``key``: the plan key, or the plan key and ``/`` and the split
    letter. ``letter``: ``""`` for an unsplit group. ``box``: the bounding at the origin.
    ``nodes``: member key to relative ``pos``. ``children``: nested parts with their offsets.
    ``segment``: which of the group's segments (:func:`segments`) the part belongs to; parts
    of different segments are placed as separate items, so a group outside can sit between.
    ``tier``: which of the cockpit's tiers (:func:`tiers`) the part belongs to, 0 above."""

    key: str
    plan: GroupPlan
    letter: str
    box: Rect
    nodes: dict[str, Point]
    waypoints: dict[int, tuple[Point, ...]] = field(default_factory=dict)
    children: list[tuple[Part, Point]] = field(default_factory=list)
    segment: int = 0
    tier: int = 0


def _id_key(key: str) -> tuple[int, int | str]:
    return id_order(key)


def _inside(graph: Graph, keys: list[str]) -> list[tuple[int, str, str]]:
    members = set(keys)
    return [edge for edge in graph.edges if edge[1] in members and edge[2] in members]


def _reversed(keys: list[str], edges: list[tuple[int, str, str]]) -> set[int]:
    """Links that close a cycle in a depth-first search, roots and neighbours in id order."""
    out: dict[str, list[tuple[tuple[int, int | str], int, str]]] = {key: [] for key in keys}
    for link, origin, target in edges:
        out[origin].append((_id_key(target), link, target))
    for items in out.values():
        items.sort()
    state: dict[str, int] = {}
    back: set[int] = set()
    for root in sorted(keys, key=_id_key):
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(out[root]))]
        while stack:
            key, it = stack[-1]
            step = next(it, None)
            if step is None:
                state[key] = 2
                stack.pop()
                continue
            _, link, target = step
            if state.get(target) == 1:
                back.add(link)
            elif target not in state:
                state[target] = 1
                stack.append((target, iter(out[target])))
    return back


def _dag(keys: list[str], edges: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    """The edges with the cycle-closing ones reversed (for layering only)."""
    back = _reversed(keys, edges)
    return [(link, target, origin) if link in back else (link, origin, target) for link, origin, target in edges]


def _order(graph: Graph, keys: list[str], dag: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    """``dag`` plus an order edge ``a -> b`` for every two members where ``a`` reaches ``b``
    through the whole graph and ``b`` does not reach ``a`` (link id -1): a chain broken by a
    group outside keeps its order. For layering only; these edges get no dummies. The result
    is acyclic: every edge of it runs from a node to one it reaches, and an order edge joins
    two nodes that do not reach each other both ways."""
    reach = graph.reach
    members = sorted(keys, key=_id_key)
    extra = [(-1, a, b) for a in members for b in members if b in reach[a] and a not in reach[b]]
    return dag + extra


def layers_of(keys: list[str], dag: list[tuple[int, str, str]]) -> dict[str, int]:
    """Longest-path layer of every key, sources at 0."""
    preds: dict[str, list[str]] = {key: [] for key in keys}
    succs: dict[str, list[str]] = {key: [] for key in keys}
    indeg = {key: 0 for key in keys}
    for _, origin, target in dag:
        succs[origin].append(target)
        preds[target].append(origin)
        indeg[target] += 1
    heap = [(_id_key(key), key) for key in keys if indeg[key] == 0]
    heapq.heapify(heap)
    layer: dict[str, int] = {}
    while heap:
        _, key = heapq.heappop(heap)
        layer[key] = max((layer[p] + 1 for p in preds[key]), default=0)
        for nxt in succs[key]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                heapq.heappush(heap, (_id_key(nxt), nxt))
    return layer


def _order_key(note: NodeNote, key: str) -> tuple:
    if note.cockpit is not None:
        return (_SLOTS.index(note.cockpit), note.order if note.order is not None else LAST, _id_key(key))
    return (len(_SLOTS), note.order if note.order is not None else LAST, _id_key(key))


def segments(graph: Graph, keys: list[str]) -> list[list[str]]:
    """The members cut where a path leaves the group: when a member reaches another along a
    path through at least one node outside the group, and the other does not reach it back,
    the second goes to a later segment than the first (an optional LoRA group inside the
    required model chain), whether or not another path joins them inside the group. So no
    segment reaches itself through a node outside it, and the top-level graph of segments
    has no loop through a cut group. A member's segment is the least that keeps every such
    pair apart and every link inside the group between two such members within one segment
    or forward. Then each such segment is cut again by its members' levels in the data flow
    (:func:`_by_zone`): the members of an earlier level that cannot move right with the
    segment's latest stay in a part of their own level, and no link inside the segment runs
    from a later part to an earlier one. So any link between two members of the group stays
    within one segment or runs forward. Segments keep the members' order; one segment when
    nothing is cut."""
    return [part for run in _along_flow(graph, keys) for part in _by_zone(graph, run)]


def _along_flow(graph: Graph, keys: list[str]) -> list[list[str]]:
    """The first cut of :func:`segments`, where a path leaves the group."""
    members = frozenset(keys)
    succs: dict[str, list[str]] = {}
    for _, origin, target in graph.edges:
        succs.setdefault(origin, []).append(target)

    def outside(start: str) -> set[str]:
        """The members ``start`` reaches along a path through a node outside the group."""
        within, beyond = {start}, set()
        todo = [(start, False)]
        while todo:
            key, out = todo.pop()
            for nxt in succs.get(key, ()):
                left = out or nxt not in members
                seen = beyond if left else within
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append((nxt, left))
        return beyond & members

    far = {key: outside(key) for key in keys}
    reach = graph.reach
    layer = layers_of(keys, _order(graph, keys, _dag(keys, _inside(graph, keys))))
    segment: dict[str, int] = {}
    for b in sorted(keys, key=lambda key: (layer[key], _id_key(key))):
        found = 0
        for a in segment:
            if b in reach[a] and a not in reach[b]:
                found = max(found, segment[a] + (b in far[a]))
        segment[b] = found
    count = max(segment.values(), default=0) + 1
    return [[key for key in keys if segment[key] == s] for s in range(count)]


def _steering(graph: Graph, keys: Collection[str]) -> list[str]:
    """The members of ``keys`` that set a segment's zone (:func:`cwc.annotate.notes.steering_members`),
    from their notes: a member's ``zone`` is its zone on its own, its role tells plumbing."""
    notes = graph.notes
    plumbing = {key for key in keys if notes[key].role is Role.PLUMBING}
    return steering_members(list(keys), plumbing, {key: notes[key].zone for key in keys})


def segment_zone(graph: Graph, keys: Collection[str]) -> Zone:
    """The zone of one segment of a cut group: the latest zone of its steering members
    (:func:`_steering`, :func:`cwc.annotate.rules.latest_zone`), as annotate gives an explicit
    group that is not a cockpit frame. The segment goes to the slot of this zone, or to a later
    main-row slot when a group strictly upstream of it has one (:func:`cwc.layout.place._slots`);
    its title, number and colour stay its group's.

    Cockpit members do not steer a segment because annotate gives cockpit titles before layout,
    from the zone of the whole group: a segment that mixed them with other members and became a
    cockpit frame would hold nodes without a cockpit title. They are knobs at the head of the
    data flow (a seed, a size, a prompt): placed with the rest of their segment, their links
    stay forward as long as what feeds them, if anything, lies upstream of that segment. A
    main-row group they feed that does not reach the segment back, and whose own zone's slot
    comes earlier than the segment's, is moved to the segment's slot or a later one
    (:func:`cwc.layout.place._slots`), so those links stay forward too. A segment of cockpit
    members only is a cockpit frame."""
    return latest_zone([graph.notes[key].zone for key in _steering(graph, keys)])


def _by_zone(graph: Graph, keys: list[str]) -> list[list[str]]:
    """One segment of a cut group (``keys``) in the parts it is placed as, in data-flow order.
    The segment goes to the slot of its latest steering zone (:func:`segment_zone`), or to a
    later main-row slot when :func:`cwc.layout.place._slots` moves it.

    A node's own zone can be earlier than the zone of a node feeding it (a first-pass decode
    feeding a tile ControlNet is of the control zone, fed by the sampler), so the members are
    not parted by their own zone level but by their cut level, which never falls along a link
    inside the segment. A steering member's cut level is the latest of its own zone level and
    the cut levels of the members feeding it within the segment, through any members; a member
    that does not steer has no level of its own and passes on its feeders'.

    A steering member of the segment's latest cut level is in the last part. One of an earlier
    level moves there too when it can: when each of its links goes to a steering member of the
    segment of the latest cut level or to a member that can move itself (an output node, with
    no link out, always can). A steering member that cannot, whose link leaves the segment (to
    another segment of the group too) or feeds a member that cannot move, stays behind: those
    members form one part per cut level, ahead of the last part, earliest first. Nothing is cut
    when every member can move, so an author's group of outputs from several stages stays one
    box.

    A member that does not steer (plumbing, a cockpit member) goes with the earliest part
    among the steering members it reaches within the segment through non-steering members;
    reaching none, with the latest part among the members feeding it directly; else with the
    first. So no link inside the segment runs from a later part to an earlier one."""
    steering = _steering(graph, keys)
    own = {key: flow_level(graph.notes[key].zone) for key in steering}
    top = max(own.values(), default=0)
    if all(level == top for level in own.values()):
        return [keys]
    members = frozenset(keys)
    succs: dict[str, list[str]] = {key: [] for key in keys}
    preds: dict[str, list[str]] = {key: [] for key in keys}
    for _, origin, target in graph.edges:
        if origin in members:
            succs[origin].append(target)
            if target in members:
                preds[target].append(origin)
    flow = _upstream_first(keys, preds)
    levels: dict[str, int] = {}
    for key in flow:
        levels[key] = max([own.get(key, 0), *(levels.get(p, 0) for p in preds[key])])
    late = {key for key in steering if levels[key] == top}
    movable: set[str] = set()
    changed = True
    while changed:  # the least fixed point; the links form no loop, so it is the only one
        changed = False
        for key in keys:
            if key not in late and key not in movable and all(t in late or t in movable for t in succs[key]):
                movable.add(key)
                changed = True
    fixed = sorted({levels[key] for key in steering if key not in late and key not in movable})
    if not fixed:
        return [keys]
    part = {key: fixed.index(levels[key]) if key not in late and key not in movable else len(fixed) for key in steering}
    steers = frozenset(steering)

    def ahead(start: str) -> list[int]:
        """The parts of the steering members ``start`` reaches through members that do not
        steer."""
        seen, todo, out = {start}, [start], []
        while todo:
            for nxt in succs[todo.pop()]:
                if nxt in members and nxt not in seen:
                    seen.add(nxt)
                    if nxt in steers:
                        out.append(part[nxt])
                    else:
                        todo.append(nxt)
        return out

    for key in keys:
        if key not in steers and (reached := ahead(key)):
            part[key] = min(reached)
    for key in flow:  # feeders first, so a member's feeders have their parts
        if key not in part:
            part[key] = max((part[p] for p in preds[key] if p in part), default=0)
    return [[key for key in keys if part[key] == p] for p in range(len(fixed) + 1)]


def _upstream_first(keys: list[str], preds: Mapping[str, list[str]]) -> list[str]:
    """``keys`` with every member after the members feeding it (``preds``, within ``keys``),
    in ``keys`` order where the links leave a choice. Normalize rejects a loop of links; the
    members of one, if a canvas brings it, follow the rest in ``keys`` order."""
    left = {key: len(set(preds[key])) for key in keys}
    succs: dict[str, list[str]] = {key: [] for key in keys}
    for key in keys:
        for p in set(preds[key]):
            succs[p].append(key)
    rank = {key: i for i, key in enumerate(keys)}
    ready = [rank[key] for key in keys if not left[key]]
    heapq.heapify(ready)
    out: list[str] = []
    while ready:
        key = keys[heapq.heappop(ready)]
        out.append(key)
        for nxt in succs[key]:
            left[nxt] -= 1
            if not left[nxt]:
                heapq.heappush(ready, rank[nxt])
    done = frozenset(out)
    return out + [key for key in keys if key not in done]


def tiers(graph: Graph, keys: list[str], core: Collection[CockpitSlot] | None) -> list[list[str]]:
    """The cockpit's members in tiers (a ``cockpit_split`` preset): the members whose cockpit
    slot is in ``core`` above, the rest below, each in the members' order. One tier, all the
    members, when ``core`` is ``None`` or either tier would be empty: no empty box is written."""
    if core is None:
        return [keys]
    upper = [key for key in keys if graph.notes[key].cockpit in core]
    lower = [key for key in keys if graph.notes[key].cockpit not in core]
    return [upper, lower] if upper and lower else [keys]


def split(graph: Graph, keys: list[str]) -> list[list[str]]:
    """C1: a set with more than 5 layers or 12 expanded nodes is cut at the layer midpoint,
    recursively; a set in one layer is cut into chunks of at most 12 by ``(order, id)``."""
    edges = _inside(graph, keys)
    layer = layers_of(keys, _order(graph, keys, _dag(keys, edges)))
    count = max(layer.values(), default=-1) + 1
    expanded = sum(1 for key in keys if not graph.nodes[key].collapsed)
    if count <= MAX_LAYERS and expanded <= MAX_EXPANDED:
        return [keys]
    if count >= 2:
        mid = (count + 1) // 2
        return split(graph, [k for k in keys if layer[k] < mid]) + split(graph, [k for k in keys if layer[k] >= mid])
    ordered = sorted(keys, key=lambda key: _order_key(graph.notes[key], key))
    return [ordered[i : i + MAX_EXPANDED] for i in range(0, len(ordered), MAX_EXPANDED)]


Item = tuple  # ("n", key) for a node, ("d", link id, layer) for a dummy slot


def _item_id(item: Item) -> tuple:
    return (0, _id_key(item[1])) if item[0] == "n" else (1, item[1], item[2])


def _arrange(
    graph: Graph, keys: list[str], cockpit: bool
) -> tuple[list[list[Item]], list[tuple[Item, Item]], dict[int, list[Item]], set[int]]:
    """Layers of items in their final order, the layer-to-layer segments, the dummy chain of
    each long link (from the link's origin side), and the reversed link ids."""
    edges = _inside(graph, keys)
    dag = _dag(keys, edges)
    back = {link for (link, o, t), (_, o2, _t2) in zip(edges, dag) if o != o2}
    layer = layers_of(keys, _order(graph, keys, dag))
    count = max(layer.values(), default=-1) + 1
    position = {key: i for i, key in enumerate(keys)}
    layers: list[list[Item]] = [[] for _ in range(count)]
    for key in sorted(keys, key=lambda k: position[k]):
        layers[layer[key]].append(("n", key))
    segments: list[tuple[Item, Item]] = []
    chains: dict[int, list[Item]] = {}
    for link, origin, target in dag:
        prev: Item = ("n", origin)
        chain = []
        for at in range(layer[origin] + 1, layer[target]):
            dummy: Item = ("d", link, at)
            layers[at].append(dummy)
            chain.append(dummy)
            segments.append((prev, dummy))
            prev = dummy
        segments.append((prev, ("n", target)))
        if chain:
            chains[link] = chain if link not in back else chain[::-1]
    if cockpit:
        for row in layers:
            row.sort(key=lambda item: _order_key(graph.notes[item[1]], item[1]) if item[0] == "n" else (LAST, LAST, _item_id(item)))
        return layers, segments, chains, back
    preds: dict[Item, list[Item]] = {}
    succs: dict[Item, list[Item]] = {}
    for a, b in segments:
        succs.setdefault(a, []).append(b)
        preds.setdefault(b, []).append(a)
    for sweep in range(4):
        index = {item: i for row in layers for i, item in enumerate(row)}
        down = sweep % 2 == 0
        order = range(1, count) if down else range(count - 2, -1, -1)
        for at in order:
            near = preds if down else succs
            row = layers[at]

            def bary(item: Item) -> float:
                linked = near.get(item, [])
                return sum(index[x] for x in linked) / len(linked) if linked else float(index[item])

            row.sort(key=lambda item: (bary(item), _item_id(item)))
            for i, item in enumerate(row):
                index[item] = i
    return layers, segments, chains, back


def place_part(graph: Graph, plan: GroupPlan, key: str, letter: str, keys: list[str], fill: Fill | None = None) -> Part:
    """Lay out ``keys`` as one box: widen expanded nodes per layer, stack each layer, and
    space the layers by the links through each gap. Writes node sizes (width up only; height
    up only for the preview nodes of ``fill``)."""
    layers, segments, chains, _ = _arrange(graph, keys, plan.zone is Zone.COCKPIT)
    layer_of = {item: at for at, row in enumerate(layers) for item in row}
    through = [0] * len(layers)
    for a, _b in segments:
        through[layer_of[a]] += 1
    widths = _widths(graph, layers)
    if fill is not None:
        _fill(graph, layers, widths, through, fill)
    nodes, dummy_at, rects, _ = _stack(graph, layers, widths, through)
    box = _box(rects)
    shift = (-box.x, -box.y)
    return Part(
        key=key,
        plan=plan,
        letter=letter,
        box=Rect(0, 0, box.w, box.h),
        nodes={k: (px + shift[0], py + shift[1]) for k, (px, py) in nodes.items()},
        waypoints={
            link: tuple((dummy_at[d][0] + shift[0], dummy_at[d][1] + shift[1]) for d in chain) for link, chain in chains.items()
        },
    )


def _widths(graph: Graph, layers: list[list[Item]]) -> list[float]:
    """Each layer's width; every expanded node in it is widened to the layer's snapped need."""
    widths: list[float] = []
    for row in layers:
        need = 0.0
        for item in row:
            if item[0] != "n" or graph.nodes[item[1]].collapsed:
                continue
            node, facts = graph.nodes[item[1]], graph.facts[item[1]]
            need = max(need, node.size[0], readable_width(facts), VUE_MIN_WIDTH)
        target = snap_up(need) if need else 0
        width = float(target)
        for item in row:
            if item[0] != "n":
                continue
            node = graph.nodes[item[1]]
            if not node.collapsed and target > node.size[0]:
                node.set_size(target, node.size[1])
            width = max(width, _rel_envelope(node, graph.facts[item[1]]).w)
        widths.append(max(width, DUMMY))
    return widths


def _stack(
    graph: Graph, layers: list[list[Item]], widths: list[float], through: list[int]
) -> tuple[dict[str, Point], dict[Item, Point], list[Rect], list[float]]:
    """Node positions, dummy points, the rects the box must hold, and each layer's bottom."""
    nodes: dict[str, Point] = {}
    dummy_at: dict[Item, Point] = {}
    rects: list[Rect] = []
    bottoms: list[float] = []
    x = float(PAD)
    for at, row in enumerate(layers):
        cursor = float(PAD + TITLE_BAND)
        for item in row:
            if item[0] == "d":
                dummy_at[item] = (x + widths[at] / 2, cursor + DUMMY / 2)
                cursor += DUMMY + NODE_GAP
                continue
            node, facts = graph.nodes[item[1]], graph.facts[item[1]]
            rel = _rel_envelope(node, facts)
            px, py = x, float(snap_up(cursor - rel.y))
            nodes[item[1]] = (px, py)
            rects.append(Rect(px + rel.x, py + rel.y, rel.w, rel.h))
            if node.collapsed:
                cx, cy = _rel_centre(node, facts)
                rects.append(Rect(px + cx, py + cy, 0, 0))
            cursor = py + rel.y + rel.h + NODE_GAP
        bottoms.append(cursor - NODE_GAP)
        x = float(snap_up(x + widths[at] + LAYER_GAP + TRACK * through[at]))
    for dx, dy in dummy_at.values():
        rects.append(Rect(dx, dy - DUMMY / 2, 0, DUMMY))
    return nodes, dummy_at, rects, bottoms


def _fill(graph: Graph, layers: list[list[Item]], widths: list[float], through: list[int], fill: Fill) -> None:
    """Grow the preview nodes of ``fill`` so the part is at least ``fill.width`` by
    ``fill.height`` and they fill it: the missing width is shared by the layers holding preview
    nodes, each of which then takes its layer's full width; in each such layer the preview
    nodes share the height left below the layer, down to the box's inner bottom. Growth comes
    in whole grid steps, so the nodes below and the layers to the right move by exactly that
    much; only a preview node last in its layer takes the remainder below the last step."""
    shows = [[item for item in row if item[0] == "n" and item[1] in fill.nodes] for row in layers]
    held = [at for at, row in enumerate(shows) if row]
    if not held:
        return
    box = _box(_stack(graph, layers, widths, through)[2])
    extra = snap_up(max(0.0, fill.width - box.w) / len(held))
    for at in held:
        widths[at] = float(snap_up(widths[at] + extra))
        for item in shows[at]:
            node = graph.nodes[item[1]]
            node.set_size(max(node.size[0], widths[at]), node.size[1])
    _, _, rects, bottoms = _stack(graph, layers, widths, through)
    box = _box(rects)
    inner_bottom = box.y + max(snap_up(fill.height), box.h) - PAD
    for at in held:
        spare = inner_bottom - bottoms[at]
        count = len(shows[at])
        steps = max(0, int(spare // GRID))
        for i, item in enumerate(shows[at]):
            # whole steps dealt out evenly, the earlier nodes taking the odd ones
            grow: float = GRID * (steps // count + (1 if i < steps % count else 0)) if spare > 0 else 0.0
            if spare > 0 and i == count - 1 and item == layers[at][-1]:
                grow += spare - steps * GRID
            node = graph.nodes[item[1]]
            rel = _rel_envelope(node, graph.facts[item[1]])
            # the envelope's height below pos is the live or the Vue body, whichever is taller;
            # sizing from it makes the envelope grow by exactly ``grow`` and lifts classic previews
            # up to match Vue's viewport even when stacked previews already fill ``fill.height``
            target = rel.y + rel.h + grow
            if target > node.size[1]:
                node.set_size(node.size[0], target)


def _rel_envelope(node: Node, facts: BoxFacts) -> Rect:
    px, py = node.pos
    env = envelope(node, facts)
    return Rect(env.x - px, env.y - py, env.w, env.h)


def _rel_centre(node: Node, facts: BoxFacts) -> Point:
    px, py = node.pos
    cx, cy = expanded_centre(node, facts)
    return (cx - px, cy - py)


def _box(rects: list[Rect]) -> Rect:
    """The union grown by the margins and the title band, snapped outward to the grid."""
    left = min(r.x for r in rects) - PAD
    top = min(r.y for r in rects) - PAD - TITLE_BAND
    right = max(r.right for r in rects) + PAD
    bottom = max(r.bottom for r in rects) + PAD
    x, y = snap_down(left), snap_down(top)
    return Rect(x, y, snap_up(right) - x, snap_up(bottom) - y)


def group_parts(
    graph: Graph,
    plan: GroupPlan,
    children: list[list[Part]],
    core: Collection[CockpitSlot] | None = None,
    fill: Fill | None = None,
    fill_of: Callable[[Zone], Fill | None] | None = None,
) -> list[Part]:
    """The parts of one plan group. A leaf group at top level is cut into :func:`segments`
    when it is not the cockpit (whose inbound links run backward by design), and into
    :func:`tiers` by ``core`` (the preset's ``cockpit_split``) when it is; each segment or tier
    is then split by C1, so a part never mixes tiers. The parts are ``key/a``, ``key/b``... in
    order, the upper tier's first. A group holding nested groups is one box: its own members
    first, then each child's parts in a row to their right, all inside its margins. ``fill``
    (a preview area) applies to every part laid out here, except that the parts of a group cut
    into several segments take ``fill_of`` the segment's zone (:func:`segment_zone`) when it is
    given: each segment goes to the slot of its own zone, or to a later main-row slot when
    :func:`cwc.layout.place._slots` moves it."""
    members = list(plan.members)
    if not children:
        if not members:
            return [place_part(graph, plan, plan.key, "", members, fill)]
        if plan.parent is None and plan.zone is Zone.COCKPIT:
            runs = [(0, t, run, fill) for t, run in enumerate(tiers(graph, members, core))]
        elif plan.parent is None:
            cut = segments(graph, members)
            if len(cut) > 1 and fill_of is not None:
                runs = [(s, 0, run, fill_of(segment_zone(graph, run))) for s, run in enumerate(cut)]
            else:
                runs = [(s, 0, run, fill) for s, run in enumerate(cut)]
        else:
            runs = [(0, 0, members, fill)]
        pieces = [(s, t, piece, f) for s, t, run, f in runs for piece in split(graph, run)]
        if len(pieces) <= 1:
            return [place_part(graph, plan, plan.key, "", members, fill)]
        out = []
        for i, (s, t, piece, f) in enumerate(pieces):
            part = place_part(graph, plan, f"{plan.key}/{_letter(i)}", _letter(i), piece, f)
            part.segment, part.tier = s, t
            out.append(part)
        return out
    own = place_part(graph, plan, plan.key, "", members, fill) if members else None
    rects: list[Rect] = []
    nodes: dict[str, Point] = {}
    waypoints: dict[int, tuple[Point, ...]] = {}
    x = float(PAD)
    if own is not None:
        inner = own.box.inflate(-PAD, -(PAD + TITLE_BAND), -PAD, -PAD)
        dx, dy = 0.0, 0.0
        nodes = {k: (px + dx, py + dy) for k, (px, py) in own.nodes.items()}
        waypoints = dict(own.waypoints)
        rects.append(inner)
        x = own.box.right - PAD + NODE_GAP
    placed: list[tuple[Part, Point]] = []
    for row in children:
        for part in row:
            at = (float(snap_up(x)), float(PAD + TITLE_BAND))
            placed.append((part, at))
            rects.append(Rect(at[0], at[1], part.box.w, part.box.h))
            x = at[0] + part.box.w + NODE_GAP
    box = _box(rects)
    shift = (-box.x, -box.y)
    return [
        Part(
            key=plan.key,
            plan=plan,
            letter="",
            box=Rect(0, 0, box.w, box.h),
            nodes={k: (px + shift[0], py + shift[1]) for k, (px, py) in nodes.items()},
            waypoints={link: tuple((px + shift[0], py + shift[1]) for px, py in pts) for link, pts in waypoints.items()},
            children=[(part, (at[0] + shift[0], at[1] + shift[1])) for part, at in placed],
        )
    ]


def _letter(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(ord("a") + rem) + out
    return out
