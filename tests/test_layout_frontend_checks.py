"""The check functions of ``tests/test_layout_frontend.py`` without the oracle.

That test runs only against a real frontend, so its checks are driven here by recorded frontend
data: ``tests/fixtures/layout/boxes.<run>.json`` (groups as the frontend computed them, Outer id 1
holding Inner id 2) and ``reroutes.<run>.json`` (``serialize()`` after loading
``reroutes.canvases.json``). The canvases the oracle test lays out are checked here too, for the
nesting and the reroutes they are meant to carry.
"""

from __future__ import annotations

import json
import re
import unittest
from types import SimpleNamespace

from tests import support
from tests.test_layout_frontend import (
    KNOWN_FAILURES,
    ROUTED,
    Spec,
    cases,
    check_membership,
    check_serialize,
    frontend_innermost,
    lay_out,
    reroute_lines,
)

LAYOUT = support.FIXTURES / "layout"
BOX_RUNS = ("classic", "vue", "classic_snap")
REROUTE_RUNS = ("classic", "vue", "classic_snap", "classic_offset80")


def load(name: str):
    return json.loads((LAYOUT / name).read_text(encoding="utf-8"))


def live_groups(rows) -> SimpleNamespace:
    return SimpleNamespace(groups={row["id"]: row for row in rows})


def group(gid: int, members: list[str], children: list[int] = ()) -> dict:
    return {"id": gid, "member_nodes": members, "child_groups": list(children)}


def membership_case(want: dict[str, int | None], group_ids: list[int]) -> SimpleNamespace:
    """What ``check_membership`` reads of a ``Case``: the nodes, the layout's membership (an index
    into the written groups) and the written groups."""
    return SimpleNamespace(
        canvas=SimpleNamespace(nodes=[SimpleNamespace(key=key, type="T") for key in want]),
        result=SimpleNamespace(membership={k: None if g is None else group_ids.index(g) for k, g in want.items()}),
        written={"groups": [{"id": gid} for gid in group_ids]},
    )


class InnermostTest(unittest.TestCase):
    def test_recorded_nesting_gives_the_inner_group(self):
        for run in BOX_RUNS:
            live = live_groups(load(f"boxes.{run}.json")["groups"])
            with self.subTest(run=run):
                self.assertEqual(frontend_innermost(live, "1"), ([1, 2], 2))
                self.assertEqual(frontend_innermost(live, "4"), ([1, 2], 2))
                self.assertEqual(frontend_innermost(live, "3"), ([1], 1))
                self.assertEqual(frontend_innermost(live, "6"), ([3], 3))
                self.assertEqual(frontend_innermost(live, "2"), ([], None))

    def test_order_of_the_groups_does_not_matter(self):
        rows = list(reversed(load("boxes.classic.json")["groups"]))
        self.assertEqual(frontend_innermost(live_groups(rows), "1")[1], 2)

    def test_overlapping_siblings_have_no_innermost(self):
        live = live_groups([group(10, ["7"]), group(11, ["7"])])
        self.assertEqual(frontend_innermost(live, "7"), ([10, 11], None))


class MembershipCheckTest(unittest.TestCase):
    def setUp(self):
        self.recorded = live_groups(load("boxes.classic.json")["groups"])

    def test_inner_group_passes_and_outer_fails_for_a_nested_node(self):
        ids = [1, 2, 3]
        self.assertEqual(check_membership(membership_case({"1": 2, "3": 1, "6": 3, "2": None}, ids), "classic", self.recorded, None), [])
        lines = check_membership(membership_case({"1": 1}, ids), "classic", self.recorded, None)
        self.assertEqual(len(lines), 1)
        self.assertIn("innermost group in the frontend 2, layout says 1", lines[0])

    def test_an_ungrouped_node_held_by_two_overlapping_groups_fails(self):
        live = live_groups([group(10, ["7"]), group(11, ["7"])])
        lines = check_membership(membership_case({"7": None}, [10, 11]), "classic", live, None)
        self.assertEqual(len(lines), 1)
        self.assertIn("groups holding it [10, 11]", lines[0])

    def test_an_ungrouped_node_held_by_one_group_fails(self):
        live = live_groups([group(10, ["7"])])
        self.assertEqual(len(check_membership(membership_case({"7": None}, [10]), "classic", live, None)), 1)


class RerouteSerializeTest(unittest.TestCase):
    """``reroute_lines`` on the recorded round trips (facts 6.1, 6.2, 6.6)."""

    def setUp(self):
        self.written = load("reroutes.canvases.json")

    def lines(self, run: str, name: str) -> list[str]:
        return reroute_lines(self.written[name], load(f"reroutes.{run}.json")["results"][name]["serialized"])

    def test_canonical_canvases_come_back_unchanged(self):
        for run in REROUTE_RUNS:
            for name in ("main", "short"):
                with self.subTest(run=run, canvas=name):
                    self.assertEqual(self.lines(run, name), [])

    def test_a_dropped_reroute_is_reported(self):
        lines = self.lines("classic", "orphan")
        self.assertTrue(any(line.startswith("extra.reroutes[") for line in lines), lines)

    def test_reroutes_without_extensions_are_reported_as_lost(self):
        lines = self.lines("classic", "linkids_only")
        self.assertIn("extra.reroutes: written [", lines[0])
        self.assertTrue(lines[0].endswith("serialized None"), lines[0])

    def test_no_reroutes_on_either_side_passes(self):
        self.assertEqual(reroute_lines({"extra": {}}, {"extra": {"ds": {}}}), [])
        self.assertEqual(reroute_lines({}, {}), [])

    def test_check_serialize_reports_reroutes(self):
        written = {"nodes": [], "groups": [], "links": [], "last_node_id": 0, "last_link_id": 0}
        written["extra"] = {"reroutes": [{"id": 1, "pos": [0, 0], "linkIds": [1]}], "linkExtensions": [{"id": 1, "parentId": 1}]}
        got = json.loads(json.dumps(written))
        got["extra"]["reroutes"][0]["pos"] = [20, 0]
        lines = check_serialize(SimpleNamespace(written=written), "classic", SimpleNamespace(serialized=got), None)
        self.assertEqual(lines, ["extra.reroutes[0]: written {'id': 1, 'pos': [0, 0], 'linkIds': [1]}, serialized {'id': 1, 'pos': [20, 0], 'linkIds': [1]}"])


class CanvasesTest(unittest.TestCase):
    """The canvases the oracle test loads carry what they are there for."""

    def test_the_nested_canvas_has_a_group_inside_another(self):
        spec = cases()["nested_two_stage"]
        self.assertIsInstance(spec, Spec)
        case = lay_out("bento", "nested_two_stage", spec)
        rects = [g["bounding"] for g in case.written["groups"]]

        def inside(a, b):
            return a != b and b[0] <= a[0] and b[1] <= a[1] and a[0] + a[2] <= b[0] + b[2] and a[1] + a[3] <= b[1] + b[3]

        pairs = [(i, j) for i, a in enumerate(rects) for j, b in enumerate(rects) if inside(a, b)]
        self.assertTrue(pairs, rects)
        inner, outer = pairs[0]
        members = case.result.membership
        self.assertIn(inner, members.values())
        self.assertIn(outer, members.values())  # the outer group holds a node of its own

    def test_the_routed_variant_writes_reroutes(self):
        case = lay_out("bento", "two_stage", cases()["two_stage"], routing=True)
        self.assertEqual(case.name, "two_stage" + ROUTED)
        self.assertTrue(case.written["extra"].get("reroutes"))
        self.assertTrue(case.written["extra"].get("linkExtensions"))
        plain = lay_out("bento", "two_stage", cases()["two_stage"])
        self.assertNotIn("reroutes", plain.written.get("extra") or {})

    def test_known_failures_name_existing_canvases(self):
        names = {name + suffix for name in cases() for suffix in ("", ROUTED)}
        for preset, canvas, renderer, check in KNOWN_FAILURES:
            self.assertIn(canvas, names)
            for pattern, _cause in KNOWN_FAILURES[(preset, canvas, renderer, check)]:
                re.compile(pattern)


if __name__ == "__main__":
    unittest.main()
