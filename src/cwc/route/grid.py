"""The free space between node boxes as a 20 px grid, and the track search over it.

A grid point ``(i, j)`` is the canvas point ``(i * GRID, j * GRID)``. It is blocked when it lies
within ``clearance`` of a node envelope (the union of the classic and Vue boxes), so a straight
run between free points keeps that distance from every box and a reroute's circle (radius 10)
never covers a node. Every other point is free: the gaps between layers inside a group, the gaps
between groups, the corridors between bands and the margin outside the groups are all free space
here; the grid does not know which is which (:mod:`cwc.route` names the channel afterwards).

The search is A* over ``(point, heading)``: a step costs 1, a turn ``TURN`` more, and a step
along a grid edge another output already uses ``SHARED`` more, so each output takes its own
track while there is room and shares one only when there is not. A path leaves its start heading
right and reaches its goal heading right, so the drawn link leaves its output and enters its
input horizontally.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Sequence

from cwc.canvas.geometry import GRID, Rect

Cell = tuple[int, int]
STEPS = ((1, 0), (0, 1), (-1, 0), (0, -1))  # east, south, west, north
EAST = 0
TURN = 3
SHARED = 40
WEIGHT = 3  # the search's greed: at most this many times the cheapest cost
SCAN = 40  # grid lines a channel's width is looked for on each side
MARGIN = 10  # grid points of free margin around the boxes


State = tuple[int, int, int]  # a search state as (code, pi, pj): see Grid.states
BLOCK = 8  # padded grid points: the side of the squares Starts groups its states by


class Starts:
    """Search states to start from, kept across searches over one grid (the points and headings
    of an output's routed tree): each state once, in ``zero`` (state to cost 0) and in the
    ``BLOCK``-point square of its padded place, which :meth:`Grid.search` queues as a whole."""

    def __init__(self) -> None:
        self.zero: dict[int, int] = {}
        self.squares: dict[tuple[int, int], list[State]] = {}

    def add(self, states: Iterable[State]) -> None:
        zero, squares = self.zero, self.squares
        for state in states:
            if state[0] not in zero:
                zero[state[0]] = 0
                squares.setdefault((state[1] // BLOCK, state[2] // BLOCK), []).append(state)

    def __len__(self) -> int:
        return len(self.zero)


@dataclass
class Grid:
    """Blocked points with the node that blocks each (the first in id order), the search
    bounds, and which outputs use each grid edge so far.

    The search works on codes: a point ``(i, j)`` inside the bounds is the place
    ``p = pi * stride + pj`` of a padded raster (``pi = i - lo_i + 1``, ``pj = j - lo_j + 1``)
    whose border and blocked points are set in ``blocked``; a state is ``4 p + heading`` and a
    grid edge ``2 p + axis`` for its lower end. Place codes grow with ``(i, j)`` in
    lexicographic order, so state codes order states as ``(i, j, heading)`` tuples do."""

    owner: dict[Cell, str]
    lo: Cell
    hi: Cell
    used: dict[int, set[object]] = field(default_factory=dict)  # by edge code
    clearance: float = 0.0  # px a free point keeps from every box: the first free column right of a box depends on it
    stride: int = field(init=False)
    blocked: bytearray = field(init=False)

    def __post_init__(self) -> None:
        (lo_i, lo_j), (hi_i, hi_j) = self.lo, self.hi
        self.stride = hi_j - lo_j + 3
        width = hi_i - lo_i + 3
        blocked = bytearray(b"\x01") * (width * self.stride)
        for pi in range(1, width - 1):
            row = pi * self.stride
            blocked[row + 1 : row + self.stride - 1] = bytes(self.stride - 2)
        for i, j in self.owner:
            if lo_i <= i <= hi_i and lo_j <= j <= hi_j:
                blocked[(i - lo_i + 1) * self.stride + (j - lo_j + 1)] = 1
        self.blocked = blocked

    @classmethod
    def build(cls, boxes: list[tuple[str, Rect]], clearance: float, used: dict[int, set[object]] | None = None) -> Grid:
        """``used``: a track table to share with another grid over the same ``boxes`` (the bounds,
        and so the edge codes, do not depend on ``clearance``)."""
        owner: dict[Cell, str] = {}
        for key, box in boxes:
            i0 = math.floor((box.x - clearance) / GRID) + 1
            i1 = math.ceil((box.right + clearance) / GRID) - 1
            j0 = math.floor((box.y - clearance) / GRID) + 1
            j1 = math.ceil((box.bottom + clearance) / GRID) - 1
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    owner.setdefault((i, j), key)
        if boxes:
            lo = (min(math.floor(b.x / GRID) for _, b in boxes) - MARGIN, min(math.floor(b.y / GRID) for _, b in boxes) - MARGIN)
            hi = (max(math.ceil(b.right / GRID) for _, b in boxes) + MARGIN, max(math.ceil(b.bottom / GRID) for _, b in boxes) + MARGIN)
        else:
            lo, hi = (0, 0), (0, 0)
        return cls(owner, lo, hi, clearance=clearance) if used is None else cls(owner, lo, hi, used, clearance)

    def free(self, cell: Cell) -> bool:
        return cell not in self.owner and self.lo[0] <= cell[0] <= self.hi[0] and self.lo[1] <= cell[1] <= self.hi[1]

    def states(self, starts: Iterable[tuple[Cell, int]]) -> list[State]:
        """The free ones of ``starts`` (a point and a heading) as search states, in order."""
        (lo_i, lo_j), (hi_i, hi_j) = self.lo, self.hi
        owner, stride = self.owner, self.stride
        out = []
        for (i, j), d in starts:
            if (i, j) not in owner and lo_i <= i <= hi_i and lo_j <= j <= hi_j:
                pi, pj = i - lo_i + 1, j - lo_j + 1
                out.append(((pi * stride + pj) * 4 + d, pi, pj))
        return out

    def search(self, starts: Sequence[State], goal: Cell, net: object, tree: Starts | None = None) -> tuple[list[Cell], int] | None:
        """A cheap path from one of ``starts`` (from :meth:`states`) or of ``tree`` to ``goal``,
        arriving heading right, or ``None`` when the goal cannot be reached. The search is
        weighted (``WEIGHT`` times the Manhattan distance left), which trades the shortest path
        for a search that stays near the straight line; ties break on the state, so the result is
        deterministic. Returns the path, which begins at the start it used, and that start's
        heading.

        The queue holds one integer per entry, ``(f * span_cost + cost) * span + state``: no cost
        reaches ``span_cost`` (a path through distinct states costs at most ``SHARED + TURN + 1``
        per state), so integers order as ``(f, cost, i, j, heading)`` tuples do.

        Every start costs 0 from the outset, so a start blocks any other path into its state
        whether or not it is ever popped. The states of ``tree`` enter the queue a square at a
        time: a square's marker ``f * span_cost * span`` (state 0 is a border place, never a
        state) has the least ``f`` any of its states can have and sorts before every entry of
        that ``f``, so its states are queued before any of them could be the least entry, and the
        pops come in the same order as with every start queued at once."""
        if not ((starts or tree) and self.free(goal)):
            return None
        stride, blocked, used = self.stride, self.blocked, self.used
        lo_i, lo_j = self.lo
        gi, gj = goal[0] - lo_i + 1, goal[1] - lo_j + 1
        target = (gi * stride + gj) * 4 + EAST
        span = len(blocked) * 4
        span_cost = (1 + TURN + SHARED) * span + 1
        unit = span_cost * span
        reach = WEIGHT * unit  # the queue entry of a start is its distance to the goal times this, plus its state
        best: dict[int, int] = tree.zero.copy() if tree else {}
        heap = []
        eager = []  # the states of ``starts``, queued now
        for code, pi, pj in starts:
            if code not in eager:
                eager.append(code)
                best[code] = 0
                heap.append((abs(pi - gi) + abs(pj - gj)) * reach + code)
        pending: dict[int, list[list[State]]] = {}  # marker -> the squares it queues
        if tree:
            for (si, sj), group in tree.squares.items():
                i0, j0 = si * BLOCK, sj * BLOCK
                di = i0 - gi if gi < i0 else max(0, gi - i0 - BLOCK + 1)
                dj = j0 - gj if gj < j0 else max(0, gj - j0 - BLOCK + 1)
                pending.setdefault((di + dj) * reach, []).append(group)
            heap.extend(pending)
        heapq.heapify(heap)
        back: dict[int, int] = {}  # a start has no entry
        moves = _moves(stride)
        pop, push = heapq.heappop, heapq.heappush
        while heap:
            item = pop(heap)
            rest, state = divmod(item, span)
            if not state:
                for group in pending.pop(item):
                    for code, pi, pj in group:
                        if code not in eager:
                            push(heap, (abs(pi - gi) + abs(pj - gj)) * reach + code)
                continue
            cost = rest % span_cost
            if cost > best[state]:
                continue
            if state == target:
                path = []
                at: int | None = state
                heading = EAST
                while at is not None:
                    pi, pj = divmod(at >> 2, stride)
                    path.append((pi + lo_i - 1, pj + lo_j - 1))
                    heading = at & 3
                    at = back.get(at)
                return path[::-1], heading
            p = state >> 2
            pi, pj = divmod(p, stride)
            for nd, dp, di, dj, step in moves[state & 3]:
                n = p + dp
                if blocked[n]:
                    continue
                total = cost + step
                if used:
                    others = used.get((p if dp > 0 else n) * 2 + (nd & 1))
                    if others and (len(others) > 1 or net not in others):
                        total += SHARED
                nxt = n * 4 + nd
                if total < best.get(nxt, total + 1):
                    best[nxt] = total
                    back[nxt] = state
                    push(heap, ((total + WEIGHT * (abs(pi + di - gi) + abs(pj + dj - gj))) * span_cost + total) * span + nxt)
        return None

    def claim(self, path: list[Cell], net: object) -> None:
        lo_i, lo_j = self.lo
        stride = self.stride
        for a, b in zip(path, path[1:]):
            low = (min(a[0], b[0]) - lo_i + 1) * stride + (min(a[1], b[1]) - lo_j + 1)
            self.used.setdefault(low * 2 + (0 if a[1] == b[1] else 1), set()).add(net)

    def shared_edges(self) -> int:
        """Grid edges more than one output runs along (a channel short of tracks)."""
        return sum(1 for nets in self.used.values() if len(nets) > 1)

    def across(self, cell: Cell, horizontal: bool) -> tuple[str, str, int]:
        """What bounds the free strip through ``cell`` across a run: the blocking node, ``"edge"``
        at the search bounds or ``"open"`` past ``SCAN`` grid lines, on each side, above and below for a horizontal run, left and right for a
        vertical one, and how many free grid lines the strip holds there."""
        di, dj = (0, 1) if horizontal else (1, 0)
        ends = []
        count = 1
        for sign in (-1, 1):
            i, j = cell
            for _ in range(SCAN):
                i, j = i + sign * di, j + sign * dj
                if (i, j) in self.owner:
                    ends.append(self.owner[(i, j)])
                    break
                if not (self.lo[0] <= i <= self.hi[0] and self.lo[1] <= j <= self.hi[1]):
                    ends.append("edge")
                    break
                count += 1
            else:
                ends.append("open")
        return ends[0], ends[1], count


@lru_cache(maxsize=8)
def _moves(stride: int) -> tuple[tuple[tuple[int, int, int, int, int], ...], ...]:
    """Per heading, the steps the search may take: new heading, place code change, the change
    of ``i`` and ``j``, and the step cost. Turning back is not a step."""
    out = []
    for d in range(4):
        row = []
        for nd, (di, dj) in enumerate(STEPS):
            if nd != (d + 2) % 4:
                row.append((nd, di * stride + dj, di, dj, 1 if nd == d else 1 + TURN))
        out.append(tuple(row))
    return tuple(out)
