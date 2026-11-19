"""Routing: native reroutes that keep links out of node boxes. Writes ``extra.reroutes`` and
``extra.linkExtensions`` in the canonical form of ``docs/m2-frontend-facts.md`` section 6.6, and
nothing else.

Which links get reroutes:

- a link inside a group that spans layers takes the dummy slots layout reserved for it
  (``LayoutResult.waypoints``) as its reroutes, snapped to the 20 px grid;
- any other link whose plain spline crosses a node box that is not one of its ends, under either
  renderer, is routed through the free space between boxes (:mod:`cwc.route.grid`);
- every other link stays a plain spline.

A route is a path of grid points that leaves the output heading right and reaches the input
heading right. Its reroutes are the turns and, on each side of a turn whose run is longer than
``FLANK_RUN`` grid steps, one point a grid step away along the run. A reroute between two
collinear neighbours points along the run, so the long segments between flanks are straight
lines whatever the reroute spline cap; only the short segments around a turn bend. The first and
last grid points are left out when the run beside them already has a flank and they lie on the
grid line nearest their anchors, since the segment to the anchor is then straight anyway; a point
the search had to move off that line (a box there) stays, or the segment would run slanted to
the flank and through that box.

A route starts in the first grid column right of the output anchors that the grid's clearance
leaves free of the source box, and ends in the last one left of the input anchors.

Every routed link is checked as drawn (:func:`cwc.canvas.reroutes.paths_between`) under both
renderers at the default cap of 20 and at an unlimited cap (``0.25 d`` on every segment). A route
that still crosses a box is tried again with a wider clearance; one that fails both keeps its
plain spline and is listed in ``RouteResult.unresolved``. A dummy chain that crosses a box is
routed like any other link.

Links of one output share reroutes as a bus tree: two links share a reroute while their points up
to it and the direction to their next point are the same, so sharing never changes how either
link is drawn. Reroutes are never shared across outputs.

Each straight run of a route lies in a channel, named by what bounds it: a band corridor's id
(``bus:<i>``) when the run lies in that corridor, otherwise ``h:<above>|<below>`` for a
horizontal run and ``v:<left>|<right>`` for a vertical one, each side the key of the node whose
box bounds the free strip there, or ``edge``. ``Channel.tracks`` counts the outputs routed
through it and ``Channel.free`` the 20 px tracks the strip holds; a channel short of tracks is
routed anyway (outputs then share grid lines) and shows as ``Channel.shortfall``.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from cwc.canvas.envelope import BoxFacts, Point, Renderer, box_facts, classic_box, envelope, vue_box
from cwc.canvas.geometry import GRID, Rect
from cwc.canvas.links import SEGMENTS, Anchors, crosses, link_spline
from cwc.canvas.model import Canvas, Link, id_order
from cwc.canvas.reroutes import REROUTE_CAP, own_directions, segment_controls
from cwc.layout.types import LayoutResult
from cwc.prompt.slots import DefLookup
from cwc.route.grid import EAST, STEPS, Cell, Grid, Starts

__all__ = ["Channel", "RouteResult", "route"]

CLEARANCES = (10.0, 20.0)  # px kept between a route and every box; the second is the retry
FLANK_RUN = 3  # grid steps: a run at least this long gets a flank reroute next to its turn
EPSILON = 1e-6  # as G2: a curve within this of a box edge is on the edge
BUCKET = 400  # px: the box index's square


@dataclass(frozen=True)
class Channel:
    """One free strip routes ran through: ``tracks`` outputs used it, ``free`` 20 px tracks fit."""

    id: str
    tracks: int
    free: int

    @property
    def shortfall(self) -> int:
        return max(0, self.tracks - self.free)


@dataclass(frozen=True)
class RouteResult:
    """``rerouted``: the links written with reroutes, by id. ``dummy``: how many of them took
    their dummy chain. ``unresolved``: links whose plain spline crosses a box and that no route
    cleared; they keep the plain spline. ``channels``: by id. ``shared_edges``: grid edges two
    outputs run along, for want of a free track."""

    rerouted: tuple[int, ...]
    dummy: int
    unresolved: tuple[int, ...]
    channels: tuple[Channel, ...]
    shared_edges: int

    @property
    def shortfalls(self) -> tuple[Channel, ...]:
        return tuple(channel for channel in self.channels if channel.shortfall)


Ends = dict[Renderer, tuple[Point, Point]]


def route(canvas: Canvas, result: LayoutResult, defs: DefLookup) -> RouteResult:
    """Write the reroutes of ``canvas`` as laid out by ``result``; ``defs`` give the box model."""
    nodes = canvas.node_map()
    facts = {key: box_facts(node, defs.get(node.type)) for key, node in nodes.items()}
    boxes = {
        Renderer.CLASSIC: _Index([(key, classic_box(node, facts[key])) for key, node in nodes.items()]),
        Renderer.VUE: _Index([(key, vue_box(node, facts[key])) for key, node in nodes.items()]),
    }
    canvas.set_reroutes([], [])
    anchors: dict[Renderer, Anchors] = {renderer: {} for renderer in Renderer}
    drawn = sorted(((link, _ends(link, nodes, facts, anchors)) for link in canvas.links), key=lambda pair: _net_order(pair[0]))
    drawn = [(link, e) for link, e in drawn if e]
    links = [link for link, _ in drawn]
    ends = {link.id: e for link, e in drawn}
    plain = {link.id for link in links if _crossing(link, (), ends[link.id], boxes)}
    chains: dict[int, list[Point]] = {}
    dummy = 0
    for link in links:
        points = result.waypoints.get(link.id)
        if points:
            snapped = [(float(round(x / GRID) * GRID), float(round(y / GRID) * GRID)) for x, y in points]
            if not _crossing(link, snapped, ends[link.id], boxes):
                chains[link.id] = snapped
                dummy += 1
                continue
            plain.add(link.id)
    unresolved: list[int] = []
    runs: dict[tuple[object, Cell, Cell], None] = {}  # a run once, however many links of its output take it
    owners = sorted(((key, envelope(node, facts[key])) for key, node in nodes.items()), key=lambda kv: id_order(kv[0]))
    grids = [Grid.build(owners, CLEARANCES[0])]
    # one track table: a track taken is taken at any clearance
    grids.extend(Grid.build(owners, c, grids[0].used) for c in CLEARANCES[1:])
    # per output: each point and heading on a routed path, to that path, the point's index, its reroutes and its corners
    trees: dict[object, dict[tuple[Cell, int], tuple[list[Cell], int, list[Point], list[int]]]] = {}
    # per grid and output: the tree's points and headings as search states, and how many of the tree's entries that covers
    coded: dict[tuple[int, object], tuple[int, Starts]] = {}
    for link in links:
        if link.id not in plain or link.id in chains:
            continue
        net = _net(link)
        tree = trees.setdefault(net, {})
        for g, grid in enumerate(grids):
            start = _start(ends[link.id], grid)
            goal = _goal(ends[link.id], grid)
            rows = _rows(ends[link.id])
            path = None
            own = grid.states([(start, EAST)])
            if tree:
                count, states = coded.get((g, net), (0, Starts()))
                if count < len(tree):
                    states.add(grid.states(itertools.islice(tree, count, None)))
                    coded[(g, net)] = (len(tree), states)
            for lazy in (states, None) if tree else (None,):
                found = grid.search(own, goal, net, lazy)
                if found is None:
                    continue
                cells, heading = found
                skip, new, fresh = 0, cells, 1
                if (cells[0], heading) != (start, EAST):
                    trunk, k, trunk_points, trunk_corners = tree[(cells[0], heading)]
                    cells = trunk[: k + 1] + cells[1:]
                    fresh = k + 1  # the points and headings up to the trunk's k-th are in the tree already
                    corners = _corners(cells, trunk_corners, k)
                    points = _points(cells, corners, rows)
                    skip = _same_segments(trunk_points, points)
                else:
                    corners = _corners(cells)
                    points = _points(cells, corners, rows)
                if not _crossing(link, points, ends[link.id], boxes, skip):
                    path = cells
                    break
            if path is None:
                continue
            grid.claim(new, net)
            chains[link.id] = points
            runs.update(dict.fromkeys((net, a, b) for a, b in _runs(path, corners)))
            for k in range(fresh, len(path)):
                step = (path[k][0] - path[k - 1][0], path[k][1] - path[k - 1][1])
                tree.setdefault((path[k], STEPS.index(step)), (path, k, points, corners))
            break
        else:
            unresolved.append(link.id)
    reroutes, extensions = _trees(links, chains)
    canvas.set_reroutes(reroutes, extensions)
    return RouteResult(
        rerouted=tuple(sorted(chains)),
        dummy=dummy,
        unresolved=tuple(sorted(unresolved)),
        channels=_channels(runs, grids[0], result),
        shared_edges=grids[0].shared_edges(),
    )


def _net(link: Link) -> tuple[str, int]:
    return (str(link.origin_id), link.origin_slot)


def _net_order(link: Link) -> tuple[Any, ...]:
    return (id_order(str(link.origin_id)), link.origin_slot, link.id)


def _ends(link: Link, nodes: dict, facts: dict[str, BoxFacts], anchors: dict[Renderer, Anchors]) -> Ends | None:
    out: Ends = {}
    for renderer in Renderer:
        curve = link_spline(link, nodes, facts, renderer, anchors[renderer])
        if curve is None:
            return None
        out[renderer] = (curve.p0, curve.p3)
    return out


class _Index:
    """Node boxes by ``BUCKET`` px square, shrunk by ``EPSILON`` as G2 does."""

    def __init__(self, boxes: list[tuple[str, Rect]]) -> None:
        self.buckets: dict[tuple[int, int], list[tuple[str, float, float, float, float, Rect]]] = {}
        for key, box in boxes:
            inner = box.inflate(-EPSILON, -EPSILON, -EPSILON, -EPSILON)
            entry = (key, inner.x, inner.y, inner.right, inner.bottom, inner)
            for bx in range(math.floor(inner.x / BUCKET), math.floor(inner.right / BUCKET) + 1):
                for by in range(math.floor(inner.y / BUCKET), math.floor(inner.bottom / BUCKET) + 1):
                    self.buckets.setdefault((bx, by), []).append(entry)

    def hit(self, p0: Point, c1: Point, c2: Point, p3: Point, own: tuple[str, str]) -> bool:
        """Whether the curve ``p0, c1, c2, p3`` sampled as G2 samples it meets a box here that is
        not one of ``own``, as :func:`cwc.canvas.links.crosses` judges it.

        The curve is sampled only when a box meets the bounds of its control points, which hold
        every sample (a sample is a convex combination of them; the margin covers rounding)."""
        cx, cy = (p0[0], c1[0], c2[0], p3[0]), (p0[1], c1[1], c2[1], p3[1])
        lo_x, hi_x, lo_y, hi_y = min(cx), max(cx), min(cy), max(cy)
        margin = 1e-6 * (1.0 + max(abs(lo_x), abs(hi_x), abs(lo_y), abs(hi_y)))
        lo_x, hi_x, lo_y, hi_y = lo_x - margin, hi_x + margin, lo_y - margin, hi_y + margin
        near = []
        for bx in range(math.floor(lo_x / BUCKET), math.floor(hi_x / BUCKET) + 1):
            for by in range(math.floor(lo_y / BUCKET), math.floor(hi_y / BUCKET) + 1):
                for entry in self.buckets.get((bx, by), ()):
                    key, x, y, right, bottom, _ = entry
                    if key in own or x >= hi_x or lo_x >= right or y >= hi_y or lo_y >= bottom:
                        continue
                    near.append(entry)
        if not near:
            return False
        part = _sample(p0, c1, c2, p3)
        xs, ys = [p[0] for p in part], [p[1] for p in part]
        lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
        for key, x, y, right, bottom, box in near:
            if x >= hi_x or lo_x >= right or y >= hi_y or lo_y >= bottom:
                continue
            for k in range(len(part) - 1):
                (ax, ay), (bx, by) = part[k], part[k + 1]
                # a piece wholly on or beyond one side of the box misses its interior: the clip
                # in crosses() rejects it too, its ratios being monotone in the rounded inputs
                if (ax <= x and bx <= x) or (ax >= right and bx >= right) or (ay <= y and by <= y) or (ay >= bottom and by >= bottom):
                    continue
                if crosses(part[k : k + 2], box):
                    return True
        return False


def _same_segments(trunk: list[Point], branch: list[Point]) -> int:
    """How many leading segments a branch draws exactly as the trunk it leaves: those ending at a
    point both share with the same heading to the next point."""
    n = 0
    while n + 1 < min(len(trunk), len(branch)) and trunk[n] == branch[n] and _heading(trunk[n], trunk[n + 1]) == _heading(branch[n], branch[n + 1]):
        n += 1
    return n


def _crossing(link: Link, points: Iterable[Point], ends: Ends, boxes: dict[Renderer, _Index], skip: int = 0) -> bool:
    """Whether ``link`` drawn through reroutes at ``points`` crosses a node box that is not one of
    its ends, under either renderer, at the default and at an unlimited reroute spline cap. The
    first ``skip`` segments are known to be clear.

    The link is drawn as :func:`cwc.canvas.reroutes.paths_between` draws it with no other link
    through its reroutes, from the same helpers, so the curves are the same floats. A segment
    whose control points are the same at both caps is checked once."""
    chain = list(points)
    own = (str(link.origin_id), str(link.target_id))
    for renderer in Renderer:
        p0, p3 = ends[renderer]
        way = [p0, *chain, p3]
        first = max(skip, 1)
        directions = own_directions(way, first)
        index = boxes[renderer]
        last = len(way) - 2
        for i in range(skip, last + 1):
            a, b = way[i], way[i + 1]
            leaving = directions[i - first] if i else None
            entering = None if i == last else directions[i + 1 - first]
            c1, c2 = segment_controls(a, b, leaving, entering, REROUTE_CAP)
            if index.hit(a, c1, c2, b, own):
                return True
            if chain:
                d1, d2 = segment_controls(a, b, leaving, entering, math.inf)
                if (d1 != c1 or d2 != c2) and index.hit(a, d1, d2, b, own):
                    return True
    return False


# Bernstein weights at t = i / SEGMENTS, computed as Spline.point computes them
_WEIGHTS = tuple(
    (u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t) for t, u in ((i / SEGMENTS, 1 - i / SEGMENTS) for i in range(SEGMENTS + 1))
)


def _sample(p0: Point, c1: Point, c2: Point, p3: Point) -> list[Point]:
    """``Spline(p0, c1, c2, p3).sample(SEGMENTS)``, the same floats, without building the spline."""
    x0, y0 = p0
    x1, y1 = c1
    x2, y2 = c2
    x3, y3 = p3
    return [(a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3) for a, b, c, d in _WEIGHTS]


def _rows(ends: Ends) -> tuple[int, int]:
    """The grid lines nearest the output anchors and nearest the input anchors (their mean under
    the two renderers)."""
    out_y = sum(e[0][1] for e in ends.values()) / len(ends)
    in_y = sum(e[1][1] for e in ends.values()) / len(ends)
    return round(out_y / GRID), round(in_y / GRID)


def _start(ends: Ends, grid: Grid) -> Cell:
    """The first grid column right of both output anchors that the source box leaves free, on
    the grid line nearest the anchors when that point is free. The rightmost anchor lies on the
    right edge of the source's envelope, which :meth:`Grid.build` grows by the clearance and
    blocks up to the last column short of ``x + clearance``: a nearer column is blocked by the
    source itself on every line beside the anchors."""
    x = max(e[0][0] for e in ends.values())
    return _clear(math.ceil((x + grid.clearance) / GRID), _rows(ends)[0], grid)


def _goal(ends: Ends, grid: Grid) -> Cell:
    """As :func:`_start` on the other side: the last grid column left of the input anchors that
    the target box, grown by the clearance, leaves free. A path reaches its goal heading right,
    so the point left of the goal must be free as well."""
    x = min(e[1][0] for e in ends.values())
    return _clear(math.floor((x - grid.clearance) / GRID), _rows(ends)[1], grid, -1)


def _clear(i: int, j: int, grid: Grid, approach: int = 0) -> Cell:
    """The free grid point in column ``i`` nearest line ``j`` whose neighbour ``approach``
    columns along is free too (``0``: no neighbour asked); ``(i, j)`` if none is within two
    lines (the search then fails)."""
    for dj in (0, -1, 1, -2, 2):
        if grid.free((i, j + dj)) and grid.free((i + approach, j + dj)):
            return (i, j + dj)
    return (i, j)


def _corners(path: list[Cell], trunk: Sequence[int] = (), shared: int = 0) -> list[int]:
    """Indices of the path where the heading changes, the start included when the path does not
    leave it heading right. ``trunk``: the corners of a path whose first ``shared + 1`` points
    this path shares; a corner before ``shared`` depends on those points only."""
    out = [k for k in trunk if k < shared]
    if shared:
        heading = (path[shared][0] - path[shared - 1][0], path[shared][1] - path[shared - 1][1])
    else:
        heading = (1, 0)
    for k in range(shared, len(path) - 1):
        step = (path[k + 1][0] - path[k][0], path[k + 1][1] - path[k][1])
        if step != heading:
            out.append(k)
            heading = step
    return out


def _points(path: list[Cell], corners: list[int], rows: tuple[int, int]) -> list[Point]:
    """The reroutes of a grid path with the given ``corners``: each turn, a flank a grid step
    from a turn along each run at least ``FLANK_RUN`` long, and the end points unless a flank
    makes them redundant. ``rows``: the grid lines nearest the output and the input anchors
    (:func:`_rows`); an end point off its line is never redundant, since without it the segment
    from the anchor would run slanted all the way to the flank."""
    if not corners:
        cells = [path[0], path[-1]] if len(path) > 1 else [path[0]]
        return [(float(i * GRID), float(j * GRID)) for i, j in cells]
    verts = [0, *corners, len(path) - 1]
    cells: list[Cell] = []
    first_flank = last_flank = False
    for n in range(1, len(verts) - 1):
        k, before, after = verts[n], verts[n - 1], verts[n + 1]
        if k - before >= FLANK_RUN:
            cells.append(path[k - 1])
            first_flank = first_flank or n == 1
        cells.append(path[k])
        if after - k >= FLANK_RUN:
            cells.append(path[k + 1])
            last_flank = n == len(verts) - 2
    if not (first_flank and corners[0] != 0 and path[0][1] == rows[0]):
        cells.insert(0, path[0])
    if not (last_flank and path[-1][1] == rows[1]):
        cells.append(path[-1])
    out: list[Cell] = []
    for cell in cells:
        if not out or out[-1] != cell:
            out.append(cell)
    return [(float(i * GRID), float(j * GRID)) for i, j in out]


def _runs(path: list[Cell], corners: list[int]) -> list[tuple[Cell, Cell]]:
    """The two ends of each straight run of ``path``, whose corners are ``corners``."""
    verts = [0, *(k for k in corners if k), len(path) - 1]
    return [(path[a], path[b]) for a, b in zip(verts, verts[1:]) if b > a]


def _channels(runs: Iterable[tuple[object, Cell, Cell]], grid: Grid, result: LayoutResult) -> tuple[Channel, ...]:
    """The channels the runs lie in. A channel needs as many tracks as the most outputs whose
    runs overlap at one place along it."""
    spans: dict[str, list[tuple[int, int, object]]] = {}
    free: dict[str, int] = {}
    for net, a, b in runs:
        horizontal = a[1] == b[1]
        mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        x, y = mid[0] * GRID, mid[1] * GRID
        corridor = next((c for c in result.corridors if horizontal and c.rect.contains_point(x, y)), None)
        if corridor is not None:
            key, room = corridor.id, corridor.tracks
        else:
            above, below, room = grid.across(mid, horizontal)
            key = f"{'h' if horizontal else 'v'}:{above}|{below}"
        axis = 0 if horizontal else 1
        spans.setdefault(key, []).append((min(a[axis], b[axis]), max(a[axis], b[axis]), net))
        free[key] = min(free.get(key, room), room)
    return tuple(Channel(key, _most_overlapping(spans[key]), free[key]) for key in sorted(spans))


def _most_overlapping(spans: list[tuple[int, int, object]]) -> int:
    """The most distinct outputs whose spans meet one grid point."""
    points = sorted({p for lo, hi, _ in spans for p in (lo, hi)})
    most = 0
    for p in points:
        most = max(most, len({net for lo, hi, net in spans if lo <= p <= hi}))
    return most


def _trees(links: list[Link], chains: dict[int, list[Point]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The reroute entries and link extensions: per output a tree in which a link shares a
    reroute while its points up to it and the direction to its next point equal another's."""
    nodes: dict[tuple[Any, ...], int] = {}  # (net, parent id, pos, onward) -> reroute id
    pos: dict[int, Point] = {}
    parent: dict[int, int | None] = {}
    through: dict[int, list[int]] = {}
    extensions = []
    for link in sorted((link for link in links if link.id in chains), key=_net_order):
        points = chains[link.id]
        net = _net(link)
        up: int | None = None
        for k, point in enumerate(points):
            onward = _heading(point, points[k + 1]) if k + 1 < len(points) else ("input", link.id)
            key = (net, up, point, onward)
            rid = nodes.get(key)
            if rid is None:
                rid = len(nodes) + 1
                nodes[key] = rid
                pos[rid] = point
                parent[rid] = up
            through.setdefault(rid, []).append(link.id)
            up = rid
        extensions.append({"id": link.id, "parentId": up})
    reroutes = []
    for rid in sorted(pos):
        entry: dict[str, Any] = {"id": rid, "pos": [_num(pos[rid][0]), _num(pos[rid][1])], "linkIds": sorted(through[rid])}
        if parent[rid] is not None:
            entry["parentId"] = parent[rid]
        reroutes.append(entry)
    extensions.sort(key=lambda e: e["id"])
    return reroutes, extensions


def _heading(a: Point, b: Point) -> tuple[int, int]:
    dx, dy = round(b[0] - a[0]), round(b[1] - a[1])
    g = math.gcd(dx, dy) or 1
    return (dx // g, dy // g)


def _num(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value
