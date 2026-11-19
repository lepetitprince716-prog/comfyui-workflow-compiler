"""Links through native reroutes, drawn as frontend 1.59.1 draws them.

``docs/m2-frontend-facts.md`` section 6. A link without reroutes is the one spline of
:func:`cwc.canvas.links.spline`. A link through reroutes is a chain of cubic segments: output anchor,
reroute 1, ..., reroute k, input anchor. Control points:

- the first segment starts ``(0.25 d, 0)`` right of the output anchor and the last ends ``(0.25 d, 0)``
  left of the input anchor, ``d`` being that segment's own length, with no cap;
- every reroute has one direction ``(cos phi, sin phi)``: ``o`` is the angle from its upstream point to
  it, ``m`` the arithmetic mean of the angles from it to the next point of every link through it,
  ``diff = (o - m) / 2`` (plus pi when ``|diff| > pi / 2``) and ``phi = o - diff``;
- a segment ending at a reroute ``R`` has the control point ``R - e (cos phi, sin phi)`` with
  ``e = min(cap, 0.25 |R - U|)``, one leaving it ``R + f (cos phi, sin phi)`` with
  ``f = min(cap, 0.25 |R - N|)``; ``cap`` is the user setting ``LiteGraph.Reroute.SplineOffset``
  (default 20, :data:`REROUTE_CAP`).

What a load keeps (section 6.2) decides which reroutes a link passes through: the chain is read from
``linkExtensions`` (the reroute nearest the input) upstream through ``parentId``. An extension naming
a missing reroute leaves a plain spline; a ``parentId`` naming no reroute ends the chain; a parent
cycle is cut at the assignment that would close it, in entry order; two entries with one id merge,
the later ``pos`` and ``parentId`` winning; a missing ``pos`` is ``[0, 0]``.
A reroute that no existing link or floating link passes through is dropped (``read`` leaves it out, as
the frontend's load does).

A reroute segment is drawn once, keyed by the reroute it ends at (:class:`SegmentKey`); a link's own
last segment is keyed by the link. Every :class:`LinkPath` still lists all of its segments, so a
reader that measures a link sees it whole and a reader that draws skips keys already drawn.
Reroutes shared by links of different outputs are outside the canonical form: each link is modelled
from its own output, and the frontend would draw only the first link's leg. Floating links and link
modes other than spline are not modelled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, NamedTuple, Sequence

from cwc.canvas.envelope import BoxFacts, Point, Renderer
from cwc.canvas.links import SPLINE_FACTOR, Spline, link_spline
from cwc.canvas.model import Canvas, vec2

REROUTE_CAP = 20.0  # LiteGraph.Reroute.SplineOffset, default


class SegmentKey(NamedTuple):
    """What a drawn segment belongs to: ``"reroute"`` and the id of the reroute it ends at, or
    ``"link"`` and the id of the link whose last segment it is."""

    kind: str
    id: int


@dataclass(frozen=True)
class LinkPath:
    """One link's drawn path: ``segments[i]`` has the key ``keys[i]``; a link without reroutes has one
    segment, the plain spline."""

    link_id: int
    chain: tuple[int, ...]  # reroute ids from the output to the input
    keys: tuple[SegmentKey, ...]
    segments: tuple[Spline, ...]

    @property
    def rerouted(self) -> bool:
        return bool(self.chain)

    def points(self, per_segment: int) -> list[Point]:
        """The path as one polyline, ``per_segment`` steps on each segment."""
        out: list[Point] = []
        for segment in self.segments:
            out.extend(segment.sample(per_segment))
        return out


@dataclass(frozen=True)
class Reroutes:
    """The reroutes a load keeps (those some link or floating link passes through) and the chains it gives the links."""

    points: dict[int, Point]
    parents: dict[int, int]  # only parents that name a reroute
    extensions: dict[int, int]  # link id to the reroute nearest its input

    @classmethod
    def read(cls, canvas: Canvas) -> Reroutes:
        points: dict[int, Point] = {}
        assigned: dict[int, int] = {}
        named: dict[int, None] = {}  # every parent assigned so far: no walk reaches a reroute outside it
        for raw in canvas.reroutes:
            rid = int(raw["id"])
            pos = raw.get("pos")
            points[rid] = vec2(pos) if pos is not None else (0.0, 0.0)
            parent = raw.get("parentId")
            if parent is None:
                assigned.pop(rid, None)
                continue
            parent = int(parent)
            # the walk from the parent reaches rid only by starting there or along an assignment to it
            if parent != rid and (rid not in named or not _closes_cycle(rid, parent, points, assigned)):
                assigned[rid] = parent
                named[parent] = None
        parents = {rid: parent for rid, parent in assigned.items() if parent in points}
        full = cls(points, parents, canvas.link_extensions)
        # the load drops every reroute that no link or floating link passes through (section 6.2,
        # 6.5): the chain of a link that exists, or of a floating link, keeps its reroutes
        used: set[int] = set()
        for link in canvas.links:
            used.update(full.chain(link.id))
        for floating in canvas.floating_links:
            parent = floating.get("parentId") if isinstance(floating, Mapping) else None
            if isinstance(parent, int):
                used.update(full._walk(parent))
        links = {link.id for link in canvas.links}
        return cls(
            {rid: point for rid, point in points.items() if rid in used},
            {rid: parent for rid, parent in parents.items() if rid in used},
            {link_id: rid for link_id, rid in full.extensions.items() if link_id in links},
        )

    def chain(self, link_id: int) -> tuple[int, ...]:
        """The reroute ids a link passes through, output side first; empty for a plain spline."""
        return tuple(reversed(self._walk(self.extensions.get(link_id))))

    def _walk(self, current: int | None) -> dict[int, None]:
        """The reroutes from ``current`` upstream through the parents, input end first."""
        points, parents = self.points, self.parents
        seen: dict[int, None] = {}
        while current in points and current not in seen:
            seen[current] = None
            current = parents.get(current)
        return seen


def _closes_cycle(rid: int, parent: int, points: Mapping[int, Point], assigned: Mapping[int, int]) -> bool:
    """True when giving ``rid`` the parent ``parent`` makes ``rid`` its own ancestor, walking only
    through reroutes defined so far (the frontend refuses the assignment that closes the loop)."""
    current: int | None = parent
    while current is not None and current in points:
        if current == rid:
            return True
        current = assigned.get(current)
    return False


def link_paths(
    canvas: Canvas, facts: dict[str, BoxFacts], renderer: Renderer, cap: float = REROUTE_CAP
) -> dict[int, LinkPath]:
    """Every drawable link of ``canvas`` by link id, as the frontend draws it under ``renderer``."""
    ends: dict[int, tuple[Point, Point]] = {}
    nodes = canvas.node_map()
    for link in canvas.links:
        curve = link_spline(link, nodes, facts, renderer)
        if curve is not None:
            ends[link.id] = (curve.p0, curve.p3)
    return paths_between(canvas, ends, cap)


def paths_between(
    canvas: Canvas, ends: Mapping[int, tuple[Point, Point]], cap: float = REROUTE_CAP, table: Reroutes | None = None
) -> dict[int, LinkPath]:
    """The paths of the links in ``ends`` (link id to output anchor and input anchor), in that order.
    ``table``: ``Reroutes.read(canvas)`` when the caller has it already."""
    if table is None:
        table = Reroutes.read(canvas)
    chains = {link_id: table.chain(link_id) for link_id in ends}
    waypoints = {
        link_id: [ends[link_id][0], *(table.points[r] for r in chain), ends[link_id][1]] for link_id, chain in chains.items()
    }
    directions = _directions(chains, waypoints)
    out: dict[int, LinkPath] = {}
    # A segment ending at reroute r after a reroute starts at r's parent (a chain runs along
    # parents), so r alone fixes its ends and directions; after the output anchor, the anchor
    # does too. Links of a bus tree share these segments: each is built once.
    built: dict[tuple[int, Point | None], tuple[SegmentKey, Spline]] = {}
    for link_id, chain in chains.items():
        keys: list[SegmentKey] = []
        segments: list[Spline] = []
        points = waypoints[link_id]
        last = len(points) - 2
        for i in range(last):
            a = points[i]
            memo = (chain[i], None if i else a)
            got = built.get(memo)
            if got is None:
                b = points[i + 1]
                c1, c2 = segment_controls(a, b, directions[chain[i - 1]] if i else None, directions[chain[i]], cap)
                got = built[memo] = (SegmentKey("reroute", chain[i]), Spline(a, c1, c2, b))
            keys.append(got[0])
            segments.append(got[1])
        a, b = points[last], points[last + 1]
        c1, c2 = segment_controls(a, b, directions[chain[last - 1]] if last else None, None, cap)
        keys.append(SegmentKey("link", link_id))
        segments.append(Spline(a, c1, c2, b))
        out[link_id] = LinkPath(link_id, chain, tuple(keys), tuple(segments))
    return out


Direction = tuple[float, float]


def segment_controls(a: Point, b: Point, leaving: Direction | None, entering: Direction | None, cap: float) -> tuple[Point, Point]:
    """The control points of the segment from ``a`` to ``b``: ``leaving`` is the direction of the
    reroute at ``a`` and ``entering`` that of the reroute at ``b``, ``None`` for an anchor."""
    length = math.dist(a, b)
    if leaving is None:
        c1 = (a[0] + SPLINE_FACTOR * length, a[1])
    else:
        f = min(cap, SPLINE_FACTOR * length)
        c1 = (a[0] + f * leaving[0], a[1] + f * leaving[1])
    if entering is None:
        c2 = (b[0] - SPLINE_FACTOR * length, b[1])
    else:
        e = min(cap, SPLINE_FACTOR * length)
        c2 = (b[0] - e * entering[0], b[1] - e * entering[1])
    return c1, c2


def own_directions(points: Sequence[Point], first: int = 1) -> list[Direction]:
    """The direction of each reroute of one link drawn through ``points[1:-1]`` (anchors first and
    last) when no other link passes through them, as :func:`paths_between` gives it: item
    ``j - first`` is the direction at ``points[j]``, from ``j = first`` on."""
    out = []
    for j in range(first, len(points) - 1):
        here, after = points[j], points[j + 1]
        out.append(_direction(points[j - 1], here, [math.atan2(after[1] - here[1], after[0] - here[0])]))
    return out


def _directions(chains: Mapping[int, tuple[int, ...]], waypoints: Mapping[int, list[Point]]) -> dict[int, Direction]:
    """Each reroute's ``(cos phi, sin phi)``, from the first link (in table order) that reaches it
    for the upstream point and from every link through it for the mean onward angle."""
    upstream: dict[int, tuple[Point, Point]] = {}
    onward: dict[int, list[float]] = {}
    for link_id, chain in chains.items():
        points = waypoints[link_id]
        for j, rid in enumerate(chain):
            here, after = points[j + 1], points[j + 2]
            upstream.setdefault(rid, (points[j], here))
            onward.setdefault(rid, []).append(math.atan2(after[1] - here[1], after[0] - here[0]))
    return {rid: _direction(before, here, onward[rid]) for rid, (before, here) in upstream.items()}


def _direction(before: Point, here: Point, onward: list[float]) -> Direction:
    angle = math.atan2(here[1] - before[1], here[0] - before[0])
    mean = sum(onward) / len(onward)
    diff = (angle - mean) / 2
    if abs(diff) > math.pi / 2:
        diff += math.pi
    phi = angle - diff
    return (math.cos(phi), math.sin(phi))
