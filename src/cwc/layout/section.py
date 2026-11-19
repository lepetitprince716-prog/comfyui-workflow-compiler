"""One section's geometry: the main row, the lower bands docked under it, and the parts.

A *section* is one main row (the first preset band's items) with the lower bands under it.

Zero-hole is judged by column: inside a section the main row is packed left to right ``GAP``
apart, and it opens a wider gap only for a *bridge*, a lower-band item fed by a main-row item
and feeding a later one. The gap is the least that keeps the bridge's links left to right:
the bridge sits between its producer's right edge and its consumer's left edge, ``GAP`` from
each. Bridges that feed each other sit side by side; the others share the gap.

Lower-band items dock under their own column: a bridge or an item fed from the main row
starts ``GAP`` right of its producers, an item that only feeds the main row ends at its
consumer's left edge, an item that only feeds lower items ends ``GAP`` left of them. An item
fed from the row and feeding it that has as many backward links either way ends at its
consumer when it would otherwise start right of the consumer (C3 reads it in order there).
When a dock would start left of the section's start, the main row moves right until it fits,
so the item fills the columns under the moved start. When two items of a lower band collide,
the later one (at one dock, the shorter) goes to the free start nearest its dock at the same
level, left or right, then one level down under the items in its own span; it keeps each
link on the side its dock gave it and stays inside the section's span.

A preset with a skybridge corridor leaves a strip ``BAND_GAP`` high above each section's main
row, as wide as the section, and the section moves down by that much. The section's rectangle
(the wrap's ratio) holds the groups only, so the skybridge changes no wrap decision.

A preset with a spine corridor after band-0 slot ``s`` widens the main row's gap between the last
item of slot ``s`` and the first item of a later slot from ``GAP`` to ``BAND_GAP``, in each section
that has both; the spine is that gap, as high as the section. The wider gap counts in the section's
width, so the wrap sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from cwc.canvas.geometry import Rect, snap_down, snap_up
from cwc.layout.inner import Part
from cwc.layout.items import GAP, Item

BAND_GAP = 80  # base corridor between bands
SCREEN_W = 1600  # the first screen's width (docs/m2-frontend-facts.md), G8
TITLE_REACH = 600  # the part of the first group's title band G8 wants on the first screen

Links = Mapping[tuple[str, str], int]  # (upstream top group, downstream top group) -> link count, in key order


@dataclass
class Placed:
    """The result of placement: every part's top-left corner, the bands' rectangles top to
    bottom, the items (for ``LayoutResult.columns``), the gaps between bands, the
    sections' rectangles, the skybridge strip above each section (empty without one) and
    the spines, each with the index of its section (empty without one)."""

    parts: list[tuple[Part, float, float]]  # reading order
    bands: list[Rect]
    items: list[Item]
    band_of: list[int]  # final band index of each item
    gaps: list[Rect] = field(default_factory=list)
    sections: list[Rect] = field(default_factory=list)
    skybridges: list[Rect] = field(default_factory=list)
    spines: list[tuple[int, Rect]] = field(default_factory=list)


@dataclass
class _Wiring:
    """How the lower items of one section connect: to main-row items (by index in the row)
    and to each other (``order`` is a topological order with cycle-closing links dropped)."""

    order: list[Item]
    fed: dict[Item, list[int]]  # main-row producers
    feeds: dict[Item, list[int]]  # main-row consumers (the exempt groups left out)
    preds: dict[Item, list[Item]]  # lower producers earlier in ``order``
    succs: dict[Item, list[Item]]  # lower consumers later in ``order``


def _wiring(row: list[Item], lower: list[Item], links: Links, exempt: frozenset[str]) -> _Wiring:
    main = {key: j for j, item in enumerate(row) for key in item.groups}
    low = {key: item for item in lower for key in item.groups}
    fed: dict[Item, list[int]] = {item: [] for item in lower}
    feeds: dict[Item, list[int]] = {item: [] for item in lower}
    out: dict[Item, list[Item]] = {item: [] for item in lower}
    for a, b in links:
        if b in low and a in main:
            fed[low[b]].append(main[a])
        elif a in low and b in main and b not in exempt:
            feeds[low[a]].append(main[b])
        elif a in low and b in low and low[a] is not low[b]:
            out[low[a]].append(low[b])
    position = {item: i for i, item in enumerate(lower)}
    state: dict[Item, int] = {}
    finished: list[Item] = []
    for root in lower:
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(sorted(out[root], key=position.__getitem__)))]
        while stack:
            item, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[item] = 2
                finished.append(item)
                stack.pop()
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(sorted(out[nxt], key=position.__getitem__))))
    order = finished[::-1]
    rank = {item: i for i, item in enumerate(order)}
    preds: dict[Item, list[Item]] = {item: [] for item in lower}
    succs: dict[Item, list[Item]] = {item: [] for item in lower}
    for a in lower:
        for b in out[a]:
            if rank[a] < rank[b] and b not in succs[a]:
                succs[a].append(b)
                preds[b].append(a)
    return _Wiring(order, {k: sorted(v) for k, v in fed.items()}, {k: sorted(v) for k, v in feeds.items()}, preds, succs)


def _split(row: list[Item], lower: list[Item], links: Links, exempt: frozenset[str]) -> list[Item]:
    """The main row with each item whose sub-columns a lower item bridges cut into one item per
    sub-column, so the row can open a bridge's gap inside it as between items: a lower item fed
    from one sub-column of the item, directly, and feeding a later sub-column of it, directly or
    through lower items. The pieces keep the item's slot, instance and stage; unbridged, they
    land where the sub-columns of the whole item would, ``GAP`` apart and top-aligned.
    ``exempt``: the cockpit frames' keys (empty: no frame)."""
    if not any(item.columns for item in row):
        return row
    pieces = [
        [Item(item.band, item.slot, item.instance, item.stage, groups, box) for groups, box in zip(item.columns, item.content.boxes)]  # type: ignore[arg-type]
        if item.columns
        else [item]
        for item in row
    ]
    flat = [piece for item in pieces for piece in item]
    wiring = _wiring(flat, lower, links, exempt)
    reach: dict[Item, set[int]] = {}
    for low in reversed(wiring.order):
        reach[low] = set(wiring.feeds[low]).union(*(reach[s] for s in wiring.succs[low]))
    out: list[Item] = []
    first = 0  # index in ``flat`` of the item's first piece
    for item, own in zip(row, pieces):
        span = range(first, first + len(own))
        bridged = len(own) > 1 and any(a < c for low in lower for a in wiring.fed[low] if a in span for c in reach[low] if c in span)
        out.extend(own if bridged else [item])
        first += len(own)
    return out


def _starts(wiring: _Wiring, right: Mapping[int, float]) -> dict[Item, float | None]:
    """Where each lower item starts when it is fed from the main row (``right``: the right
    edges of the main-row items placed so far), directly or through lower items; ``None``
    when it is not, or when one of its producers, or of theirs, is not placed yet (it is then
    no bridge into the item being placed)."""
    start: dict[Item, float | None] = {}
    waiting: list[Item] = []
    for item in wiring.order:
        if any(j not in right for j in wiring.fed[item]) or any(p in waiting for p in wiring.preds[item]):
            waiting.append(item)
            start[item] = None
            continue
        reach = [right[j] for j in wiring.fed[item]]
        reach += [s + p.w for p in wiring.preds[item] if (s := start[p]) is not None]
        start[item] = float(snap_up(max(reach) + GAP)) if reach else None
    return start


def _at_spine(before: Item, after: Item, spine: int | None) -> bool:
    """Whether the main row's gap between ``before`` and ``after`` is the spine that follows
    band-0 slot ``spine``."""
    return spine is not None and before.band == after.band == 0 and before.slot == spine < after.slot


def _pack_main(row: list[Item], lower: list[Item], wiring: _Wiring, x0: float, spine: int | None = None) -> None:
    """Pack the main row left to right; an item moves right of ``GAP`` (``BAND_GAP`` at the
    spine) only to leave room for the bridges that feed it."""
    right: dict[int, float] = {}
    for j, item in enumerate(row):
        x = x0 if j == 0 else float(snap_up(right[j - 1] + (BAND_GAP if _at_spine(row[j - 1], item, spine) else GAP)))
        start = _starts(wiring, right)
        need = [s + b.w + GAP for b in lower if j in wiring.feeds[b] and (s := start[b]) is not None]
        if need:
            x = max(x, float(snap_up(max(need))))
        item.x = x
        right[j] = x + item.w


def _docks(row: list[Item], wiring: _Wiring, x0: float, floor: float | None = None, *, clamp: bool = True) -> dict[Item, float]:
    """Where each lower item of a section wants to start, with the main row placed: at ``x0``
    when nothing places it, and never left of ``floor`` (default ``x0``) unless ``clamp`` is
    off (to see how far left the docks reach)."""
    low = x0 if floor is None else floor
    left = {j: item.x for j, item in enumerate(row)}
    right = {j: item.x + item.w for j, item in enumerate(row)}
    want: dict[Item, float] = {}
    for item in wiring.order:
        reach = [right[j] for j in wiring.fed[item]] + [want[p] + p.w for p in wiring.preds[item] if p in want]
        if not reach:
            continue
        x = float(snap_up(max(reach) + GAP))
        if wiring.feeds[item]:
            before = float(snap_down(min(left[j] for j in wiring.feeds[item]) - item.w))

            def backward(at: float, item: Item = item) -> int:
                return sum(1 for j in wiring.fed[item] if right[j] > at) + sum(1 for j in wiring.feeds[item] if left[j] < at + item.w)

            # On a tie, end at the consumer when starting after the producer would put the
            # item right of its consumer's start: C3 reads a lower item feeding the row above
            # as in order only when it starts at or before its consumer.
            if backward(before) < backward(x) or (backward(before) == backward(x) and x > min(left[j] for j in wiring.feeds[item])):
                x = before
        want[item] = max(low, x) if clamp else x
    for item in reversed(wiring.order):
        if item in want:
            continue
        # An item with no producer ends left of each consumer: at a main-row consumer's left
        # edge, ``GAP`` left of a lower consumer's dock; the one further left wins.
        ends = [float(snap_down(left[j] - item.w)) for j in wiring.feeds[item]]
        ends += [float(snap_down(want[s] - GAP - item.w)) for s in wiring.succs[item] if s in want]
        x = min(ends) if ends else x0
        want[item] = max(low, x) if clamp else x
    return want


def _bounds(item: Item, row: list[Item], wiring: _Wiring, want: Mapping[Item, float], at: Mapping[Item, float]) -> tuple[float, float]:
    """The interval the start of a lower item may move in and keep each link as its dock made
    it: right of a main-row or lower producer it starts right of, left of a consumer it ends
    left of. ``at``: where the other lower items are now (placed, or still at their dock)."""
    lo, hi = float("-inf"), float("inf")
    x, w = want[item], item.w
    for j in wiring.fed[item]:
        if x >= row[j].x + row[j].w + GAP:
            lo = max(lo, row[j].x + row[j].w + GAP)
    for j in wiring.feeds[item]:
        if x + w <= row[j].x:
            hi = min(hi, row[j].x - w)
    for p in wiring.preds[item]:
        if x >= want[p] + p.w + GAP:
            lo = max(lo, at[p] + p.w + GAP)
    for q in wiring.succs[item]:
        if x + w + GAP <= want[q]:
            hi = min(hi, at[q] - GAP - w)
    return lo, hi


def _spot(placed: list[Item], w: float, h: float, want: float, lo: float, hi: float) -> tuple[float, float] | None:
    """Where an item of size ``w`` x ``h`` goes among the ``placed`` items of its band (``y``
    relative to the band's top): the highest level with a free start in ``[lo, hi]``, ``GAP``
    from every item, and there the free start nearest ``want``. The levels are the band's top
    and ``GAP`` under each placed item, so an item goes down only as far as the items in its
    own span reach, not under the tallest item of the band."""
    if lo > hi:
        return None
    for y in sorted({0.0} | {float(snap_up(i.y + i.h + GAP)) for i in placed}):
        near = [i for i in placed if i.y < y + h + GAP and y < i.y + i.h + GAP]
        free, edge = [], lo
        for a, b in sorted((i.x - GAP - w, i.x + i.w + GAP) for i in near):  # starts that collide, open
            if a > edge:
                free.append((edge, min(a, hi)))
            edge = max(edge, b)
        free.append((edge, hi))
        best = [min(max(want, a), b) for a, b in free if a <= b]
        if best:
            return min(best, key=lambda x: (abs(x - want), x)), y
    return None


def _pack(bands: list[list[Item]], row: list[Item], wiring: _Wiring, want: Mapping[Item, float]) -> list[list[Item]]:
    """Each lower band's items with ``x`` set and ``y`` relative to the band's top, in reading
    order. Each item goes to its dock; when that collides, to the free start nearest its dock
    at the same level, left or right, then one level down (``_spot``), inside the section's
    span and inside ``_bounds``. When no start fits, it goes under the whole band at its dock."""
    span = [min(i.x for i in row), max(i.x + i.w for i in row)]
    for item in want:
        span = [min(span[0], want[item]), max(span[1], want[item] + item.w)]
    at = dict(want)
    out: list[list[Item]] = []
    for band in bands:
        placed: list[Item] = []
        for item in sorted(band, key=lambda item: (want[item], -item.h, band.index(item))):  # at one dock, the tallest first
            lo, hi = _bounds(item, row, wiring, want, at)
            lo, hi = max(lo, span[0]), min(hi, span[1] - item.w)
            found = _spot(placed, item.w, item.h, want[item], float(snap_up(lo)), float(snap_down(hi)))
            if found is None:
                found = (want[item], float(snap_up(max(i.y + i.h + GAP for i in placed))))
            item.x, item.y = found
            at[item] = item.x
            placed.append(item)
        out.append(sorted(placed, key=lambda item: (item.y, item.x)))
    return out


def _union(rects: list[Rect]) -> Rect:
    left, top = min(r.x for r in rects), min(r.y for r in rects)
    return Rect(left, top, max(r.right for r in rects) - left, max(r.bottom for r in rects) - top)


def _hull(items: list[Item]) -> Rect:
    left, top = min(i.x for i in items), min(i.y for i in items)
    return Rect(left, top, max(i.x + i.w for i in items) - left, max(i.y + i.h for i in items) - top)


def _first_width(item: Item) -> float:
    out: list[tuple[Part, float, float]] = []
    item.content.place(0.0, 0.0, out)
    return out[0][0].box.w


@dataclass
class _Done:
    """The sections laid out so far, as ``Placed`` will hold them. ``at`` is each item's corner
    as it was laid out, in reading order: laying out more sections moves the items they share,
    never these. ``skybridge``: whether each section leaves a skybridge strip above its main
    row (``skybridges``, one per section). ``spine``: the band-0 slot a spine follows, ``None``
    without one (``spines``: each spine with its section's index)."""

    bands: list[Rect] = field(default_factory=list)
    band_rows: list[list[Item]] = field(default_factory=list)  # each band's items in reading order
    sections: list[Rect] = field(default_factory=list)
    at: list[tuple[Item, float, float]] = field(default_factory=list)
    parts: list[tuple[Part, float, float]] = field(default_factory=list)  # the parts of ``at[:laid]``
    laid: int = 0
    skybridge: bool = False
    skybridges: list[Rect] = field(default_factory=list)
    spine: int | None = None
    spines: list[tuple[int, Rect]] = field(default_factory=list)


def _lay(done: _Done, row: list[Item], mine: list[list[Item]], links: Links, exempt: frozenset[str], x0: float, guide: float) -> _Done:
    """``done`` with one more section: the main ``row`` and the lower items ``mine`` (a list
    per lower band) assigned to it, under the bands already laid out. A section reads only
    ``done``'s last band, so the sections before it are never laid out again."""
    mine_flat = [item for band in mine for item in band]
    row = _split(row, mine_flat, links, exempt)
    wiring = _wiring(row, mine_flat, links, exempt)
    bands = list(done.bands)
    sky = BAND_GAP if done.skybridge else 0
    # The guide reserve stands beside the main row only: lower bands that start below it
    # may use its column.
    top = float(snap_up(bands[-1].bottom + BAND_GAP)) + sky if bands else float(sky)
    floor = 0.0 if snap_up(top + max(i.h for i in row) + BAND_GAP) >= guide else x0
    _pack_main(row, mine_flat, wiring, x0, done.spine)
    reach = min(_docks(row, wiring, x0, floor, clamp=False).values(), default=x0)
    if reach < floor:  # a lower item ends left of its consumer only if the row starts further right
        start = float(snap_up(x0 + floor - reach))
        if not bands:  # the first group's title band stays on the first screen (G8)
            start = max(x0, min(start, float(snap_down(SCREEN_W - min(_first_width(row[0]), TITLE_REACH)))))
        _pack_main(row, mine_flat, wiring, start, done.spine)
    want = _docks(row, wiring, x0, floor)
    first = len(bands)
    rows = list(done.band_rows)
    for b, members in enumerate([row] + _pack([band for band in mine if band], row, wiring, want)):
        top = float(snap_up(bands[-1].bottom + BAND_GAP)) if bands else 0.0
        top += 0 if b else sky
        for item in members:
            item.y = top + (item.y if b else 0.0)
        bands.append(_hull(members))
        rows.append(members)
    at = done.at + [(item, item.x, item.y) for members in rows[len(done.band_rows) :] for item in members]
    section = _union(bands[first:])
    skybridges = done.skybridges + ([Rect(section.x, section.y - sky, section.w, sky)] if sky else [])
    spines = done.spines + [
        (len(done.sections), Rect(a.x + a.w, section.y, b.x - (a.x + a.w), section.h)) for a, b in zip(row, row[1:]) if _at_spine(a, b, done.spine)
    ]
    return replace(done, bands=bands, band_rows=rows, sections=done.sections + [section], at=at, skybridges=skybridges, spines=spines)


def _settle(done: _Done) -> _Done:
    """``done`` with the parts of every section made, so the trials that follow make only theirs."""
    parts = list(done.parts)
    for item, x, y in done.at[done.laid :]:
        item.content.place(x, y, parts)
    return replace(done, parts=parts, laid=len(done.at))


def _finish(done: _Done) -> Placed:
    parts = _settle(done).parts
    items = [item for members in done.band_rows for item in members]
    band_of = [b for b, members in enumerate(done.band_rows) for _ in members]
    gaps = []
    tops = {sky.bottom: sky.y for sky in done.skybridges}  # a gap ends where a skybridge starts
    for above, below in zip(done.bands, done.bands[1:]):
        left, right = min(above.x, below.x), max(above.right, below.right)
        gaps.append(Rect(left, above.bottom, right - left, tops.get(below.y, below.y) - above.bottom))
    return Placed(
        parts=parts,
        bands=done.bands,
        items=items,
        band_of=band_of,
        gaps=gaps,
        sections=done.sections,
        skybridges=done.skybridges,
        spines=done.spines,
    )
