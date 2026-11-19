"""The items of one slot (cwc.layout.items): the sub-column rule of a slot stacked along y.

A frontend link leaves a node's right edge and enters the next node's left edge, so two
linked groups stacked one above the other always draw a backward link. Inside a y stack:

1. a group an earlier group of the stack links to goes one sub-column right of it;
2. groups with no link between them still stack;
3. a group's segments never share a sub-column: segment s+1 goes right of segment s;
4. the rule holds inside every instance of a repeated slot, so an attached control branch
   becomes a sub-column left of the sampling it feeds.

These drive ``_slot_items`` with hand-sized parts and read where each part lands.
"""

from __future__ import annotations

import unittest

from cwc.annotate.graph import group_ranks
from cwc.annotate.types import GroupPlan, Zone
from cwc.canvas.geometry import Rect
from cwc.layout.inner import Part
from cwc.layout.items import GAP, Item, _slot_items
from cwc.layout.types import Axis, Slot

W, H = 300, 100


def _groups(*specs: tuple[str, Zone, int]) -> tuple[list[GroupPlan], dict[str, list[Part]]]:
    """``(key, zone, stage)`` per placed group, in plan order. A key ``g#s`` is segment ``s``
    of plan group ``g``, as layout keys the segments of a cut group."""
    groups, units = [], {}
    plans: dict[str, GroupPlan] = {}
    for key, zone, stage in specs:
        base, _, segment = key.partition("#")
        plan = plans.setdefault(base, GroupPlan(key=base, zone=zone, name=base, members=(), stage=stage))
        placed = GroupPlan(key=key, zone=zone, name=base, members=(), stage=stage)
        groups.append(placed)
        units[key] = [Part(key=base, plan=plan, letter="", box=Rect(0, 0, W, H), nodes={}, segment=int(segment or 0))]
    return groups, units


def _edges(*links: tuple[str, str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for a, b in links:
        out.setdefault(a, set()).add(b)
    return out


def _at(item: Item, units: dict[str, list[Part]]) -> dict[str, tuple[float, float]]:
    """Each placed group's corner inside its item."""
    out: list[tuple[Part, float, float]] = []
    item.content.place(0.0, 0.0, out)
    where = {id(part): (x, y) for part, x, y in out}
    return {key: where[id(parts[0])] for key, parts in units.items() if id(parts[0]) in where}


STACK = Slot((Zone.MODEL, Zone.PROMPT, Zone.SAMPLING))


class SubColumnTest(unittest.TestCase):
    def test_linked_group_goes_one_sub_column_right(self) -> None:
        groups, units = _groups(("a", Zone.MODEL, 0), ("b", Zone.PROMPT, 1), ("c", Zone.SAMPLING, 2))
        (item,) = _slot_items(0, 0, STACK, groups, units, _edges(("a", "b"), ("b", "c")))
        at = _at(item, units)
        self.assertEqual(at["a"], (0.0, 0.0))
        self.assertEqual(at["b"], (W + GAP, 0.0))
        self.assertEqual(at["c"], (2 * (W + GAP), 0.0))
        self.assertEqual((item.w, item.h), (3 * W + 2 * GAP, H))

    def test_unlinked_groups_stack(self) -> None:
        groups, units = _groups(("a", Zone.MODEL, 0), ("b", Zone.PROMPT, 0), ("c", Zone.SAMPLING, 1))
        (item,) = _slot_items(0, 0, STACK, groups, units, _edges(("a", "c")))
        at = _at(item, units)
        self.assertEqual(at["a"], (0.0, 0.0))
        self.assertEqual(at["b"], (0.0, H + GAP))  # no link with a: stacked under it
        self.assertEqual(at["c"], (W + GAP, 0.0))  # a links to it: right of a, at the top
        self.assertEqual(item.groups, ["a", "b", "c"])

    def test_link_to_an_earlier_group_or_into_the_cockpit_moves_nothing(self) -> None:
        groups, units = _groups(("a", Zone.MODEL, 0), ("b", Zone.PROMPT, 1), ("k", Zone.COCKPIT, 2))
        (item,) = _slot_items(0, 0, STACK, groups, units, _edges(("b", "a"), ("b", "k")), "k")
        at = _at(item, units)
        self.assertEqual([at["a"], at["b"], at["k"]], [(0.0, 0.0), (0.0, H + GAP), (0.0, 2 * (H + GAP))])

    def test_segments_never_share_a_sub_column(self) -> None:
        # No link joins the two segments directly: an outside group bridges them.
        groups, units = _groups(("m#0", Zone.MODEL, 0), ("m#1", Zone.MODEL, 0), ("p", Zone.PROMPT, 0))
        (item,) = _slot_items(0, 0, STACK, groups, units, _edges(("m#0", "x"), ("x", "m#1")))
        at = _at(item, units)
        self.assertEqual(at["m#0"], (0.0, 0.0))
        self.assertEqual(at["m#1"], (W + GAP, 0.0))
        self.assertEqual(at["p"], (0.0, H + GAP))

    def test_attached_control_is_a_sub_column_left_of_its_sampling(self) -> None:
        slot = Slot((Zone.CONTROL, Zone.SAMPLING), repeat=Axis.X, attach=(Zone.CONTROL,))
        groups, units = _groups(("c1", Zone.CONTROL, 0), ("c2", Zone.CONTROL, 0), ("s1", Zone.SAMPLING, 1), ("s2", Zone.SAMPLING, 2))
        items = _slot_items(0, 0, slot, groups, units, _edges(("c1", "s1"), ("c2", "s1"), ("s1", "s2")))
        self.assertEqual([(item.stage, item.groups) for item in items], [(1, ["c1", "c2", "s1"]), (2, ["s2"])])
        at = _at(items[0], units)
        self.assertEqual(at["c1"], (0.0, 0.0))
        self.assertEqual(at["c2"], (0.0, H + GAP))
        self.assertEqual(at["s1"], (W + GAP, 0.0))

    def test_a_stack_along_x_is_unchanged(self) -> None:
        slot = Slot((Zone.MODEL, Zone.PROMPT), stack=Axis.X)
        groups, units = _groups(("a", Zone.MODEL, 0), ("b", Zone.PROMPT, 1))
        items = _slot_items(0, 0, slot, groups, units, _edges(("a", "b")))
        self.assertEqual([item.groups for item in items], [["a"], ["b"]])


LANES = Slot((Zone.MODEL, Zone.OPTIONAL, Zone.SAMPLING), repeat=Axis.X)


def _ranked(specs: list[tuple[str, Zone]], links: list[tuple[str, str]]) -> list[tuple[str, Zone, int]]:
    """``(key, zone, stage)`` per placed group, the stage ranked by ``group_ranks`` over the
    placed groups, as layout ranks them once groups are cut: each key stands for one node,
    the keys in ``links`` outside ``specs`` (groups of other slots) too, run in list order."""
    keys = [key for key, _ in specs]
    keys += [k for link in links for k in link if k not in keys]
    rank = group_ranks({k: [k] for k in keys}, links, {k: i for i, k in enumerate(keys)})
    return [(key, zone, rank[key]) for key, zone in specs]


class SegmentInstanceTest(unittest.TestCase):
    """In a repeated slot, each placed group opens or joins the instance of its stage; a
    segment ranks on its own, so it follows its previous segment and the groups bridging the
    two, in the slot or outside it."""

    def test_segment_follows_the_bridge_in_the_slot(self) -> None:
        links = [("m#0", "x"), ("x", "m#1"), ("m#1", "s"), ("y", "s")]
        specs = _ranked([("m#0", Zone.MODEL), ("m#1", Zone.MODEL), ("x", Zone.OPTIONAL), ("y", Zone.OPTIONAL), ("s", Zone.SAMPLING)], links)
        groups, units = _groups(*specs)
        items = _slot_items(0, 0, LANES, groups, units, _edges(*links))
        self.assertEqual([item.groups for item in items], [["m#0", "y"], ["x"], ["m#1"], ["s"]])
        self.assertEqual([item.stage for item in items], [0, 1, 2, 3])
        self.assertEqual([item.instance for item in items], [0, 1, 2, 3])

    def test_segment_bridged_outside_the_slot_follows_its_previous_segment(self) -> None:
        links = [("m#0", "out"), ("out", "m#1"), ("m#1", "s")]
        specs = _ranked([("m#0", Zone.MODEL), ("m#1", Zone.MODEL), ("p", Zone.MODEL), ("s", Zone.SAMPLING)], links)
        groups, units = _groups(*specs)
        items = _slot_items(0, 0, LANES, groups, units, _edges(*links))
        self.assertEqual([item.groups for item in items], [["m#0", "p"], ["m#1"], ["s"]])
        self.assertEqual([item.stage for item in items], [0, 2, 3])

    def test_each_later_segment_follows_the_one_before(self) -> None:
        links = [("m#0", "x"), ("x", "m#1"), ("m#1", "out"), ("out", "m#2")]
        specs = _ranked([("m#0", Zone.MODEL), ("m#1", Zone.MODEL), ("m#2", Zone.MODEL), ("x", Zone.OPTIONAL)], links)
        groups, units = _groups(*specs)
        items = _slot_items(0, 0, LANES, groups, units, _edges(*links))
        self.assertEqual([item.groups for item in items], [["m#0"], ["x"], ["m#1"], ["m#2"]])


if __name__ == "__main__":
    unittest.main()
