"""The tiered cockpit of a ``cockpit_split`` preset (M5): the members whose cockpit slot is in
``core`` form the upper tier, the rest the lower; each tier is then split by C1 on its own, the
parts lettered in order, the upper tier's first. No empty box: when either tier would be empty
the cockpit stays one tier. Placement stacks the tiers: the lower box ``GAP`` under the upper,
left-aligned, each tier a row of its parts.

The presets here are bento plus ``cockpit_split`` only, so the tiers are seen apart from the rest
of the ``cockpit-split`` preset."""

import copy
import json
import unittest
from dataclasses import replace

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img
from tests.layout_cases import laid_out, routed

from cwc.annotate import annotate, apply_notes
from cwc.annotate.types import CockpitSlot, Zone
from cwc.canvas.envelope import box_facts
from cwc.canvas.geometry import snap_up
from cwc.layout.inner import Graph, group_parts, tiers
from cwc.layout.items import GAP
from cwc.layout.presets import parse_presets
from cwc.layout.types import CockpitSplit
from cwc.resources import read_json

CORE = ["positive", "negative", "reference", "size", "seed"]


def tiered(core: list[str] = CORE):
    """Bento with a ``cockpit_split`` of ``core`` and nothing else changed."""
    doc = copy.deepcopy(read_json("presets.json"))
    doc["presets"]["bento"]["cockpit_split"] = {"core": core}
    return parse_presets(doc)["bento"]


def with_advanced(doc=None, encoders: int = 0):
    """Text to image with the sampler's steps and cfg exposed as cockpit value nodes (slot other,
    the lower tier), and ``encoders`` extra positive encoders lifted into the cockpit."""
    doc = doc or txt2img()
    doc["steps"] = node("PrimitiveInt", {"value": 20}, ui_role="cockpit")
    doc["cfg"] = node("PrimitiveFloat", {"value": 7.0}, ui_role="cockpit")
    doc["sampler"]["inputs"]["steps"] = ["steps", 0]
    doc["sampler"]["inputs"]["cfg"] = ["cfg", 0]
    prev = "pos"
    for i in range(encoders):
        doc[f"t{i}"] = node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": f"x{i}"}, ui_role="cockpit")
        doc[f"c{i}"] = node("ConditioningCombine", {"conditioning_1": [prev, 0], "conditioning_2": [f"t{i}", 0]})
        prev = f"c{i}"
    doc["sampler"]["inputs"]["positive"] = [prev, 0]
    return doc


def run(doc, preset):
    """``routed`` without the route result: the checks see the routed canvas, as the template
    sweeps do."""
    return routed(doc, preset)[:5]


def cockpit_parts(doc, core):
    """The cockpit's parts straight from ``group_parts``, with the plan."""
    canvas, authoring = compiled(doc)
    plan = annotate(canvas, DEFS, authoring)
    apply_notes(canvas, plan)
    nodes = canvas.node_map()
    graph = Graph(
        nodes=nodes,
        facts={key: box_facts(n, DEFS.get(n.type)) for key, n in nodes.items()},
        notes=plan.nodes,
        edges=tuple((link.id, str(link.origin_id), str(link.target_id)) for link in canvas.links if link.origin_id != link.target_id),
    )
    (cockpit,) = [g for g in plan.groups if g.zone is Zone.COCKPIT]
    return plan, graph, cockpit, group_parts(graph, cockpit, [], core)


def slot_of(plan, keys):
    return {plan.nodes[key].cockpit for key in keys}


def bounding(canvas, index):
    return canvas.to_json()["groups"][index]["bounding"]


class Tiers(unittest.TestCase):
    def setUp(self) -> None:
        self.plan, self.graph, self.cockpit, _ = cockpit_parts(with_advanced(), None)
        self.members = list(self.cockpit.members)
        self.core = tuple(CockpitSlot(c) for c in CORE)

    def test_core_above_the_rest_below_in_member_order(self) -> None:
        upper, lower = tiers(self.graph, self.members, self.core)
        self.assertEqual(upper + lower, [k for k in self.members if k in upper] + [k for k in self.members if k in lower])
        self.assertTrue(slot_of(self.plan, upper) <= set(self.core))
        self.assertEqual(slot_of(self.plan, lower), {CockpitSlot.OTHER})

    def test_no_split_without_cockpit_split(self) -> None:
        self.assertEqual(tiers(self.graph, self.members, None), [self.members])

    def test_empty_core_is_one_tier(self) -> None:
        # The presets file refuses an empty core; the rule still writes no empty box.
        self.assertEqual(tiers(self.graph, self.members, ()), [self.members])

    def test_no_core_member_is_one_tier(self) -> None:
        self.assertEqual(tiers(self.graph, self.members, (CockpitSlot.REFERENCE,)), [self.members])

    def test_every_member_in_core_is_one_tier(self) -> None:
        self.assertEqual(tiers(self.graph, self.members, tuple(CockpitSlot)), [self.members])


class TieredLayout(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas, self.plan, self.result, self.report, _ = run(with_advanced(), tiered())
        (self.cockpit,) = [g.key for g in self.plan.groups if g.zone is Zone.COCKPIT]

    def test_two_boxes_numbered_01a_01b(self) -> None:
        keys = [f"{self.cockpit}/a", f"{self.cockpit}/b"]
        self.assertEqual([self.result.labels[k] for k in keys], ["01a", "01b"])
        self.assertEqual(self.result.cockpit, tuple(self.result.group_index[k] for k in keys))

    def test_core_slots_in_the_upper_box(self) -> None:
        upper, lower = (self.result.group_index[f"{self.cockpit}/{x}"] for x in "ab")
        inside = {i: [k for k, g in self.result.membership.items() if g == i] for i in (upper, lower)}
        self.assertTrue(slot_of(self.plan, inside[upper]) <= {CockpitSlot(c) for c in CORE})
        self.assertEqual(slot_of(self.plan, inside[lower]), {CockpitSlot.OTHER})

    def test_lower_box_right_under_the_upper(self) -> None:
        upper, lower = (bounding(self.canvas, self.result.group_index[f"{self.cockpit}/{x}"]) for x in "ab")
        self.assertEqual(lower[0], upper[0])
        self.assertEqual(lower[1], snap_up(upper[1] + upper[3] + GAP))

    def test_checks_read_both_boxes_as_the_cockpit(self) -> None:
        errors = [f for f in self.report.findings if f.severity.value == "error"]
        self.assertEqual(errors, [])
        self.assertNotIn("C4", self.report.metrics.get("skipped", []))

    def test_bento_without_cockpit_split_keeps_one_box(self) -> None:
        _, _, result, _, _ = laid_out(with_advanced())
        self.assertEqual(result.cockpit, (result.group_index[self.cockpit],))


class TiersThenC1(unittest.TestCase):
    """Tiers first, then C1 inside each tier: a crowded upper tier splits into ``a`` and ``b``,
    the lower tier is ``c``; no part mixes tiers."""

    def setUp(self) -> None:
        self.doc = with_advanced(encoders=13)

    def test_upper_split_lower_whole_letters_in_order(self) -> None:
        plan, _, cockpit, parts = cockpit_parts(self.doc, tuple(CockpitSlot(c) for c in CORE))
        self.assertEqual([p.letter for p in parts], ["a", "b", "c"])
        self.assertEqual([p.tier for p in parts], [0, 0, 1])
        self.assertEqual([p.key for p in parts], [f"{cockpit.key}/{x}" for x in "abc"])
        for part in parts:
            slots = slot_of(plan, part.nodes)
            self.assertEqual(slots == {CockpitSlot.OTHER}, part.tier == 1)
        self.assertEqual(sorted(k for p in parts for k in p.nodes), sorted(cockpit.members))

    def test_layout_numbering_and_cockpit_indices(self) -> None:
        _, plan, result, report, _ = run(self.doc, tiered())
        (cockpit,) = [g.key for g in plan.groups if g.zone is Zone.COCKPIT]
        keys = [f"{cockpit}/{x}" for x in "abc"]
        self.assertEqual([result.labels[k] for k in keys], ["01a", "01b", "01c"])
        self.assertEqual(result.cockpit, tuple(result.group_index[k] for k in keys))
        # The 13 extra encoders each get a numbered title, so C4 has nothing to report in bento
        # either; the tiers add no error of their own.
        plain = routed(with_advanced(encoders=13))[3]
        codes = [(f.code.value, f.data.get("reason")) for r in (report, plain) for f in r.findings if f.severity.value == "error"]
        self.assertEqual(codes, [])

    def test_upper_tier_a_row_lower_tier_under_it(self) -> None:
        canvas, plan, result, _, _ = run(self.doc, tiered())
        (cockpit,) = [g.key for g in plan.groups if g.zone is Zone.COCKPIT]
        a, b, c = (bounding(canvas, result.group_index[f"{cockpit}/{x}"]) for x in "abc")
        self.assertEqual((b[0], b[1]), (snap_up(a[0] + a[2] + GAP), a[1]))
        self.assertEqual((c[0], c[1]), (a[0], snap_up(a[1] + max(a[3], b[3]) + GAP)))

    def test_deterministic(self) -> None:
        first = run(self.doc, tiered())[0].to_json()
        second = run(with_advanced(encoders=13), tiered())[0].to_json()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


class Boundaries(unittest.TestCase):
    """Each boundary lays the cockpit out as one box, exactly as without ``cockpit_split``."""

    def one_box(self, doc, preset) -> None:
        canvas, plan, result, _, _ = run(doc, preset)
        (cockpit,) = [g.key for g in plan.groups if g.zone is Zone.COCKPIT]
        self.assertEqual(result.cockpit, (result.group_index[cockpit],))
        plain = routed(copy.deepcopy(doc))[0]
        self.assertEqual(canvas.to_json(), plain.to_json())

    def test_empty_core(self) -> None:
        self.one_box(with_advanced(), replace(tiered(), cockpit_split=CockpitSplit(())))

    def test_no_member_in_core(self) -> None:
        self.one_box(with_advanced(), tiered(["reference"]))

    def test_every_member_in_core(self) -> None:
        self.one_box(txt2img(), tiered())


if __name__ == "__main__":
    unittest.main()
