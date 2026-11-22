"""Between groups: slots, repeat instances, bands, sections, the wrap and the x positions.

The first preset band is the *main row*; the lower preset bands sit under it. A *section* is
one main row with the lower bands under it; a wrap starts a new section below. Items:
:mod:`cwc.layout.items`; one section's geometry: :mod:`cwc.layout.section`.

Slots: a group goes to the slot of its zone, except that a main-row group strictly downstream
of a main-row group whose zone's slot comes later goes to the latest such slot (``_slots``):
the main row runs left to right in slot order, so a group in an earlier slot than one feeding
it would sit left of its producer.

Wrap: when a section is wider than ``wrap_ratio`` times its height, its tail moves to a new
section below, decided once. The cut lies between two main-row items. A cut pays only when
the part it moves is at least ``WRAP_MIN_MOVED`` of the section's width: moving one short
item away (an output group alone) saves its width and costs a backward link across the
canvas. Among the cuts that pay, those that leave a head not too wide come first,
then the fewest links between the two sections, then the longest head. A cut is taken only
when ``fits`` accepts the placement (G5 within its limit); otherwise the section stays whole
and G9 reports its width. A lower-band item goes to the section of its latest producer, one
with no producer to the first section it links to (``_assign``).
"""

from __future__ import annotations

from typing import Callable, Mapping

from cwc.annotate.types import GroupPlan, Zone
from cwc.canvas.geometry import Rect
from cwc.layout.inner import Part
from cwc.layout.items import Item, _downstream, _slot_items
from cwc.layout.section import Links, Placed, _at_spine, _Done, _finish, _lay, _settle
from cwc.layout.types import Axis, CorridorKind, Preset

WRAP_MIN_MOVED = 0.25  # a cut must move at least this share of the section's width


def _assign(sections: list[list[Item]], lower: list[Item], links: Links) -> dict[Item, int]:
    """The section of each lower item. An item with producers (main-row groups or other lower
    items linking into it) goes to the section of its latest producer, so a wrap never leaves it
    in an earlier section than a group that feeds it: that section is laid out first, when the
    producer has no x yet. It is assigned once every lower producer is. An item with no producer
    takes the smallest section among its links to main-row groups and assigned lower items.

    Passes go over ``lower`` in list order (``place`` passes it in preset order) and never
    revisit an item. When a pass assigns nothing (lower items feeding each other in a cycle, or a
    producer-less item linked only to lower items still waiting on it), the first item in list
    order with a known producer section takes the latest of those, else the first with a known
    link takes the smallest. An item with no link to any section goes to the first section.

    Every section is a max or min of main-row sections, chosen by the links alone. So when the
    sections after ``s`` change but stay after ``s``, every item in a section before ``s`` keeps
    it, and every other item stays after ``s``: the wrap's trials rely on this."""
    where = {key: s for s, row in enumerate(sections) for item in row for key in item.groups}
    owner = {key: item for item in lower for key in item.groups}
    near: dict[Item, list[str]] = {item: [] for item in lower}
    fed: dict[Item, list[int]] = {item: [] for item in lower}  # main-row producers' sections
    preds: dict[Item, list[Item]] = {item: [] for item in lower}  # lower producers
    for a, b in links:
        if a in owner:
            near[owner[a]].append(b)
        if b in owner:
            near[owner[b]].append(a)
            if a in where:
                fed[owner[b]].append(where[a])
            elif a in owner and owner[a] is not owner[b]:
                preds[owner[b]].append(owner[a])
    out: dict[Item, int] = {}

    def latest(item: Item) -> list[int]:
        return fed[item] + [out[p] for p in preds[item] if p in out]

    def first(item: Item) -> list[int]:
        return [where[k] for k in near[item] if k in where] + [out[owner[k]] for k in near[item] if k in owner and owner[k] in out]

    while True:
        changed = False
        for item in lower:
            if item in out:
                continue
            if fed[item] or preds[item]:
                if all(p in out for p in preds[item]):
                    out[item] = max(latest(item))
                    changed = True
            elif found := first(item):
                out[item] = min(found)
                changed = True
        if changed:
            continue
        waiting = [item for item in lower if item not in out]
        pick = next(((item, max(latest(item))) for item in waiting if latest(item)), None)
        pick = pick or next(((item, min(first(item))) for item in waiting if first(item)), None)
        if pick is None:
            break
        out[pick[0]] = pick[1]
    return {item: out.get(item, 0) for item in lower}


def _slots(
    preset: Preset,
    groups: list[GroupPlan],
    edges: Mapping[str, set[str]],
    exempt: frozenset[str],
    preview_groups: frozenset[str] = frozenset(),
) -> dict[str, tuple[int, int]]:
    """The ``(band, slot)`` each of ``groups`` is placed in.

    A group goes to its zone's slot (``Preset.home``), with one exception. A main-row group
    ``g`` (band 0) goes to the latest of the home slots of the main-row groups strictly upstream
    of it, when that slot comes after its own. ``p`` is strictly upstream of ``g`` when the links
    between top-level groups (``edges``) lead from ``p`` to ``g``, through groups of any band,
    and not back from ``g`` to ``p``; a link into a cockpit frame (``exempt``) is not followed.

    Why: the main row is laid out left to right in slot order, so a group whose zone's slot
    comes before that of a group feeding it would sit left of its producer, and its links would
    run back. A prompt-zone node fed through a first pass's sampler (its decode into a tile
    ControlNet, say) is such a group. With every group no earlier than each main-row group
    strictly upstream of it, the order inside one slot (ranks, the stack rule) puts it right of
    its producers, as long as the ranks agree with strict upstream. They can disagree in one
    case only: a cycle of top-level groups that closes through a link into a cockpit frame
    alone. Strict upstream does not follow that link, but the ranks (``group_ranks``) count it,
    so the cycle is ordered by the ranks' cycle rule, which may run against strict upstream.

    The rest stays as it is: a cockpit frame (its inbound links run right to left by design; as
    they are not followed, nothing is upstream of it), a lower-band group (it stays in its band)
    and a group that reaches its producer back (a cycle: neither of the two may claim the later
    slot). Upstream groups count with their home
    slots, not with the slots they move to: strict upstream is transitive, so a moved group's
    new slot is the home slot of a group upstream of it, which is upstream of what it feeds
    too. The result is that of moving groups until nothing moves, in any order, in one pass.

    In a repeated slot a moved group opens its own instance by its rank (``GroupPlan.stage``):
    its zone is not one of the slot's, so not one of its ``attach`` zones. In any other slot it
    goes by that slot's stack rule (:func:`cwc.layout.items._slot_items`)."""
    home = {g.key: preset.home(g.zone) for g in groups}
    forward = {a: {b for b in targets if b not in exempt} for a, targets in edges.items()}
    reach = {g.key: _downstream(g.key, forward) for g in groups}
    out = dict(home)
    for g in groups:
        band, slot = home[g.key]
        if band != 0:
            continue
        upstream = [home[p.key][1] for p in groups if home[p.key][0] == 0 and g.key in reach[p.key] and p.key not in reach[g.key]]
        latest = max(upstream, default=slot)
        if latest > slot:
            out[g.key] = (0, latest)
    if preset.preview is not None and preset.preview.dock_steps and len(preset.bands) >= 2:
        candidates = [
            g
            for g in groups
            if out[g.key][0] == 0
            and g.key in preview_groups
            and g.zone is not Zone.COCKPIT
            and not edges.get(g.key)
        ]
        if len(candidates) >= 2:
            cand_keys = {c.key for c in candidates}
            stage_of = {
                g.key: max(
                    (
                        p.stage
                        for p in groups
                        if out[p.key][0] == 0 and p.key not in cand_keys and g.key in reach[p.key]
                    ),
                    default=-1,
                )
                for g in candidates
            }
            max_stage = max(stage_of.values(), default=-1)
            for g in candidates:
                if 0 <= stage_of[g.key] < max_stage:
                    out[g.key] = (1, 0)
    return out


def _mine(lower: list[list[Item]], assign: Mapping[Item, int], s: int) -> list[list[Item]]:
    """The lower items of each band that ``assign`` gives to section ``s``."""
    return [[item for item in band if assign[item] == s] for band in lower]


def _too_wide(rect: Rect, ratio: float) -> bool:
    return rect.w > ratio * rect.h


def _crossing(sections: list[list[Item]], lower: list[Item], assign: Mapping[Item, int], links: Links, exempt: frozenset[str]) -> int:
    """The links between groups of different sections (into the exempt groups left out);
    ``assign``: the section of each lower item."""
    where = {key: s for s, row in enumerate(sections) for item in row for key in item.groups}
    for item in lower:
        for key in item.groups:
            where[key] = assign[item]
    return sum(n for (a, b), n in links.items() if b not in exempt and a in where and b in where and where[a] != where[b])


def place(
    preset: Preset,
    groups: list[GroupPlan],
    units: Mapping[str, list[Part]],
    links: Links,
    *,
    x0: float,
    exempt: frozenset[str],
    fits: Callable[[Placed], bool],
    guide: float = 0.0,
    preview_groups: frozenset[str] = frozenset(),
) -> Placed:
    """Place the top-level ``groups`` (plan order), each a row of parts (``units``), by
    ``preset``, each in the slot :func:`_slots` gives it. ``links``: link counts between top-level groups. ``x0``: where every section
    starts. ``exempt``: the cockpit frames' keys, whose inbound links set no position and run
    right to left by design (empty: no frame). ``fits``: whether a placement keeps G5 within its limit (the wrap asks). ``guide``: the
    bottom of the reserved ``00`` guide rectangle in column 0 (0 without one)."""
    links = dict(sorted(links.items()))  # every pass below reads the links in key order
    edges: dict[str, set[str]] = {}
    for a, b in links:
        edges.setdefault(a, set()).add(b)
    slots = _slots(preset, groups, edges, exempt, preview_groups)
    by_slot: dict[tuple[int, int], list[GroupPlan]] = {}
    for group in groups:
        by_slot.setdefault(slots[group.key], []).append(group)
    tier_axis = preset.cockpit_split.axis if preset.cockpit_split is not None else Axis.Y
    preset_bands: list[list[Item]] = []
    for b, band in enumerate(preset.bands):
        items = [
            item
            for s, slot in enumerate(band.slots)
            for item in _slot_items(b, s, slot, by_slot.get((b, s), []), units, edges, exempt, tier_axis)
        ]
        if items:
            preset_bands.append(items)
    if not preset_bands:
        return Placed(parts=[], bands=[], items=[], band_of=[])
    main, lower = preset_bands[0], preset_bands[1:]
    flat = [item for band in lower for item in band]
    # The wrap works on the open last section ``rest``; the sections before it (``closed``) are
    # laid out once, in ``done``. Splitting ``rest`` leaves them and their lower items as they
    # were: ``_assign`` builds each lower item's section from main-row sections by max and min
    # alone, the order fixed by the links, and ``rest`` and its halves come after every closed
    # section. So a lower item in a closed section stays there and no other item enters one, and
    # a trial lays out its two sections only.
    closed: list[list[Item]] = []
    done = _Done(
        skybridge=any(rule.kind is CorridorKind.SKYBRIDGE for rule in preset.corridors),
        spine=next((rule.after_slot for rule in preset.corridors if rule.kind is CorridorKind.SPINE), None),
    )
    rest = main
    while True:
        s = len(closed)
        current = _lay(done, rest, _mine(lower, _assign(closed + [rest], flat, links), s), links, exempt, x0, guide)
        if len(rest) <= 1 or not _too_wide(current.sections[-1], preset.wrap_ratio):
            return _finish(current)
        before = current.sections[-1].w
        candidates = []
        for k in range(1, len(rest)):
            if _at_spine(rest[k - 1], rest[k], done.spine):
                continue  # the cockpit and the stages it steers stay in one section
            trial = closed + [rest[:k], rest[k:]]
            assign = _assign(trial, flat, links)
            head = _lay(done, rest[:k], _mine(lower, assign, s), links, exempt, x0, guide)
            arranged = _lay(head, rest[k:], _mine(lower, assign, s + 1), links, exempt, x0, guide)
            if arranged.sections[-1].w < WRAP_MIN_MOVED * before:
                continue
            candidates.append((_too_wide(arranged.sections[-2], preset.wrap_ratio), _crossing(trial, flat, assign, links, exempt), -k, k, arranged))
        candidates.sort(key=lambda c: c[:3])
        chosen = next((k for *_, k, arranged in candidates if fits(_finish(arranged))), None)
        if chosen is None:
            # The trials moved the items ``rest`` shares with them: lay it out again.
            return _finish(_lay(done, rest, _mine(lower, _assign(closed + [rest], flat, links), s), links, exempt, x0, guide))
        assign = _assign(closed + [rest[:chosen], rest[chosen:]], flat, links)
        done = _settle(_lay(done, rest[:chosen], _mine(lower, assign, s), links, exempt, x0, guide))
        closed, rest = closed + [rest[:chosen]], rest[chosen:]
