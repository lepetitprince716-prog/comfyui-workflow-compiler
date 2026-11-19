"""Node boxes in square buckets, to ask which boxes a sampled path crosses without testing every box.

The answer is the one :func:`cwc.canvas.links.crosses` gives box by box (open rectangle, touching an
edge does not count); the buckets and the per-segment bounds only skip boxes that cannot be hit.
A box covering more than ``MAX_BUCKETS`` buckets is kept in a list of its own, checked on every
query, so building the index costs time in the number of boxes, not in the area they cover.
"""

from __future__ import annotations

import math
from typing import Sequence

from cwc.canvas.envelope import Point
from cwc.canvas.geometry import Rect
from cwc.canvas.links import _segment_hits

BUCKET = 256.0  # px; about one node box across, so a segment looks at a handful of buckets
CHUNK = 21  # segments per window: one curve sampled at 20 steps and the join to the next
MAX_BUCKETS = 4096  # a box over more buckets than this (a square about 16000 px across) is not bucketed


class BoxIndex:
    """``boxes`` (key, box) in a fixed order; :meth:`crossed` answers in that order."""

    def __init__(self, boxes: Sequence[tuple[str, Rect]]) -> None:
        self.keys = [key for key, _ in boxes]
        self.rects = [box for _, box in boxes]
        self.edges = [(box.x, box.y, box.right, box.bottom) for box in self.rects]
        self.buckets: dict[tuple[int, int], list[int]] = {}
        self.large: list[int] = []  # boxes too large to bucket, in index order; in no bucket
        for i, (x, y, right, bottom) in enumerate(self.edges):
            bx0, bx1 = math.floor(x / BUCKET), math.floor(right / BUCKET)
            by0, by1 = math.floor(y / BUCKET), math.floor(bottom / BUCKET)
            if (bx1 - bx0 + 1) * (by1 - by0 + 1) > MAX_BUCKETS:
                self.large.append(i)
                continue
            for bx in range(bx0, bx1 + 1):
                for by in range(by0, by1 + 1):
                    self.buckets.setdefault((bx, by), []).append(i)

    def crossed(self, path: Sequence[Point], exclude: frozenset[str] | set[str] = frozenset()) -> tuple[str, ...]:
        """The keys, in index order, of the boxes whose interior some segment of the polyline
        ``path`` meets, keys in ``exclude`` left out."""
        edges, rects, keys = self.edges, self.rects, self.keys
        if len(path) == 2 and path[0] == path[1]:
            # one segment of no length (the join of two drawn segments): the clip passes it exactly
            # when the point is strictly inside the box, and only the point's bucket (or a large box) holds such boxes
            px, py = path[0]
            inside = [
                i
                for i in (*self.buckets.get((math.floor(px / BUCKET), math.floor(py / BUCKET)), ()), *self.large)
                if edges[i][0] < px < edges[i][2] and edges[i][1] < py < edges[i][3] and keys[i] not in exclude
            ]
            return tuple(keys[i] for i in sorted(inside))
        hit: set[int] = set()
        xs = [p[0] for p in path]
        ys = [p[1] for p in path]
        last = len(path) - 1
        # windows of CHUNK segments sharing their end points: a box clear of a window's bounds is
        # clear of every segment in it, so most windows end at the bucket lookup
        for start in range(0, last, CHUNK):
            stop = min(start + CHUNK, last)
            wx, wy = xs[start : stop + 1], ys[start : stop + 1]
            lo_x, hi_x, lo_y, hi_y = min(wx), max(wx), min(wy), max(wy)
            near = [
                i
                for i in self._near(lo_x, lo_y, hi_x, hi_y)
                if i not in hit and keys[i] not in exclude and _overlaps(edges[i], lo_x, lo_y, hi_x, hi_y)
            ]
            if not near:
                continue
            for k in range(start, stop):
                ax, ay, bx, by = xs[k], ys[k], xs[k + 1], ys[k + 1]
                lo_x, hi_x = (ax, bx) if ax <= bx else (bx, ax)
                lo_y, hi_y = (ay, by) if ay <= by else (by, ay)
                for i in near:
                    if i not in hit and _overlaps(edges[i], lo_x, lo_y, hi_x, hi_y) and _segment_hits(path[k], path[k + 1], rects[i]):
                        hit.add(i)
        return tuple(keys[i] for i in sorted(hit))

    def _near(self, lo_x: float, lo_y: float, hi_x: float, hi_y: float) -> Sequence[int] | set[int]:
        """Every box in the buckets the bounds touch, and the large ones (every box, when that is
        fewer buckets' worth)."""
        floor = math.floor
        cx0, cx1 = floor(lo_x / BUCKET), floor(hi_x / BUCKET)
        cy0, cy1 = floor(lo_y / BUCKET), floor(hi_y / BUCKET)
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > len(self.edges):
            return range(len(self.edges))
        out: set[int] = set(self.large)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                out.update(self.buckets.get((cx, cy), ()))
        return out


def _overlaps(edge: tuple[float, float, float, float], lo_x: float, lo_y: float, hi_x: float, hi_y: float) -> bool:
    """False when bounds lie wholly on or beyond one side of the box. A segment within such bounds
    misses the open interior, and the clip rejects it too, its ratios being monotone in the rounded
    inputs; so this only skips boxes the clip would not report."""
    x, y, right, bottom = edge
    return not (hi_x <= x or lo_x >= right or hi_y <= y or lo_y >= bottom)
