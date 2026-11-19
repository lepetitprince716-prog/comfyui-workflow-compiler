"""The link model shared by layout and every geometry check.

A link without reroutes is drawn as the frontend's default spline (``docs/m2-frontend-facts.md``
section 5): a cubic Bezier from the output anchor ``p0`` to the input anchor ``p3`` with
``d = 0.25 * |p3 - p0|``, ``c1 = p0 + (d, 0)`` and ``c2 = p3 - (d, 0)``. Both renderers use the
same curve; only the anchors differ (:func:`cwc.canvas.envelope.slot_anchors`). Links through
reroutes are modelled by :mod:`cwc.canvas.reroutes`, which builds on this module; the curves here
are the anchor-to-anchor splines, which is what a link's direction (G5) is judged by. Other link
render modes are not modelled.

G2 asks whether a link passes through a node box that is not one of its ends:
:func:`crosses` tests a sampled path against the box interior.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from typing import Sequence

from cwc.canvas.envelope import BoxFacts, Point, Renderer, slot_anchors
from cwc.canvas.geometry import Rect
from cwc.canvas.model import Canvas, Link, Node

SPLINE_FACTOR = 0.25
SEGMENTS = 20  # 21 points, as the oracle tested the drawn path


@dataclass(frozen=True)
class Spline:
    p0: Point
    c1: Point
    c2: Point
    p3: Point

    def point(self, t: float) -> Point:
        u = 1 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        return (
            a * self.p0[0] + b * self.c1[0] + c * self.c2[0] + d * self.p3[0],
            a * self.p0[1] + b * self.c1[1] + c * self.c2[1] + d * self.p3[1],
        )

    def sample(self, segments: int = SEGMENTS) -> list[Point]:
        """``segments + 1`` points at equal steps of the curve parameter; the same floats as
        :meth:`point` at ``i / segments``, the weights being computed by the same expressions."""
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = self.p0, self.c1, self.c2, self.p3
        return [(a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3) for a, b, c, d in _weights(segments)]


@functools.lru_cache(maxsize=8)
def _weights(segments: int) -> tuple[tuple[float, float, float, float], ...]:
    """The Bernstein weights :meth:`Spline.point` uses, at ``t = i / segments``."""
    out = []
    for i in range(segments + 1):
        t = i / segments
        u = 1 - t
        out.append((u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t))
    return tuple(out)


def spline(start: Point, end: Point) -> Spline:
    """The default spline from an output anchor to an input anchor."""
    d = SPLINE_FACTOR * math.dist(start, end)
    return Spline(start, (start[0] + d, start[1]), (end[0] - d, end[1]), end)


Anchors = dict[str, tuple[list[Point], list[Point]]]  # node key to its slot_anchors under one renderer


def link_spline(link: Link, nodes: dict[str, Node], facts: dict[str, BoxFacts], renderer: Renderer, anchors: Anchors | None = None) -> Spline | None:
    """The drawn curve of ``link`` under ``renderer``; ``None`` when an end or a slot is missing.
    ``anchors``: a cache of :func:`slot_anchors` for this renderer, filled as nodes come up."""
    origin, target = nodes.get(str(link.origin_id)), nodes.get(str(link.target_id))
    if origin is None or target is None:
        return None
    if anchors is None:
        outputs = slot_anchors(origin, facts[origin.key], renderer)[1]
        inputs = slot_anchors(target, facts[target.key], renderer)[0]
    else:
        outputs = _anchors(origin, facts, renderer, anchors)[1]
        inputs = _anchors(target, facts, renderer, anchors)[0]
    if not (0 <= link.origin_slot < len(outputs) and 0 <= link.target_slot < len(inputs)):
        return None
    return spline(outputs[link.origin_slot], inputs[link.target_slot])


def _anchors(node: Node, facts: dict[str, BoxFacts], renderer: Renderer, cache: Anchors) -> tuple[list[Point], list[Point]]:
    got = cache.get(node.key)
    if got is None:
        got = cache[node.key] = slot_anchors(node, facts[node.key], renderer)
    return got


def canvas_splines(canvas: Canvas, facts: dict[str, BoxFacts], renderer: Renderer) -> dict[int, Spline]:
    """Every drawable link of ``canvas`` by link id, as the one spline between its anchors (a link's
    reroutes are ignored: see :func:`cwc.canvas.reroutes.link_paths` for the drawn path)."""
    nodes = canvas.node_map()
    out: dict[int, Spline] = {}
    anchors: Anchors = {}
    for link in canvas.links:
        curve = link_spline(link, nodes, facts, renderer, anchors)
        if curve is not None:
            out[link.id] = curve
    return out


def crosses(path: Sequence[Point], rect: Rect) -> bool:
    """True when the polyline ``path`` meets the interior of ``rect``. Touching an edge or
    running along it does not count."""
    return any(_segment_hits(a, b, rect) for a, b in zip(path, path[1:]))


def _segment_hits(a: Point, b: Point, rect: Rect) -> bool:
    """Liang-Barsky clipping against the open rectangle."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - rect.x), (dx, rect.right - a[0]), (-dy, a[1] - rect.y), (dy, rect.bottom - a[1])):
        if p == 0:
            if q <= 0:
                return False
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 >= t1:
            return False
    return True
