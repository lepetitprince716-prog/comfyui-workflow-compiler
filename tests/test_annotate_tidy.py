"""annotate in tidy mode without regrouping: the kept plan and the overlap refusal.

The private hook runs on a real canvas when ``CWC_TIDY_GRAPH`` names one (with node
definitions from ``CWC_TIDY_NODEDEFS``, an ``/object_info`` export, gzip allowed); it is
skipped when the variable is unset. No private path is written here.
"""

import json
import os
import unittest
from collections import Counter
from pathlib import Path

from tests import support  # noqa: F401
from tests.graphs import DEFS, backward_group_links, bare_canvas

from cwc.annotate import annotate
from cwc.annotate.types import Annotations, Source, Zone
from cwc.canvas.envelope import box_facts, classic_box, vue_box
from cwc.canvas.model import Canvas
from cwc.ingest.canvas import load_canvas
from cwc.nodedefs.registry import NodeDefs, read_source
from cwc.report import Code, CwcError, ExitCode

LAYOUT = support.FIXTURES / "layout"


def load_json(name: str):
    return json.loads((LAYOUT / name).read_text(encoding="utf-8"))


def by_title(plan: Annotations) -> dict[str, object]:
    return {group.name: group for group in plan.groups}


class BoxesFixtureTest(unittest.TestCase):
    """The kept plan of ``boxes.canvas.json`` against the frontend's ``member_nodes``."""

    def setUp(self):
        self.canvas = Canvas(load_json("boxes.canvas.json"))
        self.plan = annotate(self.canvas, DEFS, None)
        self.frontend = load_json("boxes.classic.json")["groups"]

    def test_innermost_groups_match_the_frontend(self):
        self.assertIs(self.plan.source, Source.KEPT)
        area = {g["title"]: g["bounding"][2] * g["bounding"][3] for g in self.frontend}
        holding: dict[str, list[str]] = {}
        for group in self.frontend:
            for key in group["member_nodes"]:
                holding.setdefault(key, []).append(group["title"])
        expected = {key: min(titles, key=lambda t: area[t]) for key, titles in holding.items()}
        actual = {key: group.name for group in self.plan.groups for key in group.members}
        self.assertEqual(actual, expected)
        every = {node.key for node in self.canvas.nodes}
        self.assertEqual(set(self.plan.ungrouped), every - set(expected))

    def test_nesting_and_source_order(self):
        groups = by_title(self.plan)
        ids = {g["id"]: g["title"] for g in self.frontend}
        for group in self.frontend:
            for child in group["child_groups"]:
                self.assertEqual(groups[ids[child]].parent, groups[group["title"]].key)
        titles = [group.title for group in self.canvas.groups]
        for title, group in groups.items():
            self.assertEqual(group.source_index, titles.index(title))
            if not group.members:
                self.assertIs(group.zone, Zone.STAGE, title)

    def test_tidy_reports_nodes_as_they_are(self):
        nodes = self.canvas.node_map()
        for key, note in self.plan.nodes.items():
            self.assertEqual(note.collapsed, nodes[key].collapsed, key)
            self.assertIsNone(note.colour)
            self.assertIsNone(note.title)

    def test_same_plan_twice_and_for_shuffled_nodes(self):
        raw = load_json("boxes.canvas.json")
        raw["nodes"].reverse()
        self.assertEqual(annotate(Canvas(raw), DEFS, None), self.plan)
        self.assertEqual(annotate(self.canvas, DEFS, None), self.plan)


def mystery(node_id: int = 1, pos=(0, 30), size=(50, 50)) -> dict:
    """An unknown, narrow node: its Vue box is wider than its classic box."""
    return {"id": node_id, "type": "TypeFromAnUninstalledPack", "pos": list(pos), "size": list(size)}


def centres(canvas: Canvas, key: str):
    node = canvas.node_map()[key]
    facts = box_facts(node, DEFS.get(node.type))
    return classic_box(node, facts).center, vue_box(node, facts).center


class ClassicVueTest(unittest.TestCase):
    OUTER = [-100, -100, 600, 600]

    def test_classic_centre_decides_when_the_vue_centre_is_elsewhere(self):
        canvas = bare_canvas([mystery()], [("Outer", self.OUTER), ("Inner", [-10, -10, 80, 200])])
        (cx, _), (vx, _) = centres(canvas, "1")
        self.assertTrue(-10 <= cx < 70 <= vx, (cx, vx))  # classic inside Inner, Vue outside
        plan = annotate(canvas, DEFS, None)
        self.assertEqual(by_title(plan)["Inner"].members, ("1",))
        self.assertEqual(by_title(plan)["Outer"].members, ())

    def test_vue_centre_alone_does_not_make_a_member(self):
        canvas = bare_canvas([mystery()], [("Outer", self.OUTER), ("Right", [90, -10, 60, 200])])
        (cx, _), (vx, _) = centres(canvas, "1")
        self.assertTrue(cx < 90 <= vx < 150, (cx, vx))
        plan = annotate(canvas, DEFS, None)
        self.assertEqual(by_title(plan)["Outer"].members, ("1",))
        self.assertEqual(by_title(plan)["Right"].members, ())
        self.assertEqual(by_title(plan)["Right"].zone, Zone.STAGE)


class OverlapRefusalTest(unittest.TestCase):
    def test_overlapping_groups_sharing_a_centre_are_refused(self):
        nodes = [mystery(1, (100, 130)), mystery(2, (1000, 130))]
        groups = [("Left", [0, 0, 400, 300]), ("Middle", [120, 50, 400, 300]), ("Far", [900, 0, 300, 300])]
        canvas = bare_canvas(nodes, groups)
        with self.assertRaises(CwcError) as caught:
            annotate(canvas, DEFS, None)
        error = caught.exception
        self.assertEqual(error.exit_code, ExitCode.INPUT_ERROR)
        self.assertEqual([f.code for f in error.findings], [Code.G4])
        self.assertEqual(error.findings[0].data["groups"], [0, 1])
        self.assertEqual(error.findings[0].nodes, ("1",))
        self.assertIn("--regroup", error.findings[0].hint)
        plan = annotate(canvas, DEFS, None, regroup=True)
        self.assertIs(plan.source, Source.INFERRED)

    def test_overlap_without_a_shared_centre_is_kept(self):
        canvas = bare_canvas([mystery(1, (10, 130))], [("Left", [0, 0, 400, 300]), ("Middle", [300, 50, 400, 300])])
        plan = annotate(canvas, DEFS, None)
        self.assertEqual(by_title(plan)["Left"].members, ("1",))


@unittest.skipUnless(os.environ.get("CWC_TIDY_GRAPH"), "CWC_TIDY_GRAPH names no canvas")
class PrivateCanvasTest(unittest.TestCase):
    """A real canvas from outside the repository (see the module docstring)."""

    @classmethod
    def setUpClass(cls):
        cls.canvas = load_canvas(Path(os.environ["CWC_TIDY_GRAPH"]).read_bytes())
        source = os.environ.get("CWC_TIDY_NODEDEFS")
        cls.defs = NodeDefs.load(object_info=read_source(source)) if source else DEFS

    def test_kept_plan_refuses_the_overlaps(self):
        with self.assertRaises(CwcError) as caught:
            annotate(self.canvas, self.defs, None)
        error = caught.exception
        self.assertEqual(error.exit_code, ExitCode.INPUT_ERROR)
        self.assertTrue(error.findings)
        self.assertTrue(all(f.code is Code.G4 for f in error.findings))
        pairs = [(f.data["groups"], f.nodes) for f in error.findings]
        shared = sorted({key for _, nodes in pairs for key in nodes}, key=int)
        print(f"\nrefusal: {len(pairs)} pairs sharing {len(shared)} node centres: {pairs}")

    def test_regroup_returns_a_valid_plan(self):
        plan = annotate(self.canvas, self.defs, None, regroup=True)
        self.assertIs(plan.source, Source.INFERRED)
        members = [key for group in plan.groups for key in group.members]
        self.assertEqual(sorted(members), sorted(node.key for node in self.canvas.nodes))
        self.assertEqual(len(members), len(set(members)))
        # Not asserted: on a real canvas the zone groups may form a cycle; the count is reported.
        backward = backward_group_links(self.canvas, plan)
        zones = Counter(group.zone.value for group in plan.groups)
        optional = sum(1 for group in plan.groups if group.optional)
        print(f"\nregroup: {len(plan.groups)} groups, per zone {dict(sorted(zones.items()))}, "
              f"optional {optional}, ungrouped {len(plan.ungrouped)}, counts {plan.counts}, "
              f"group links not forward in stage {len(backward)}")


if __name__ == "__main__":
    unittest.main()
