"""check_geometry: G1-G6, G9, G10 on small written canvases, and G2 against the recorded frontend runs.

Hand-written canvases use one unknown node type ``T`` (no definition), whose boxes then follow
the saved size alone. A node at ``(x, y)`` with size ``(w, h)`` and w >= 225 has

* classic box ``[x, y - 30, w, h + 30]``, so its centre is ``(x + w / 2, y + h / 2 - 15)``;
* Vue box ``[x, y - 30, w, h + 24 + 30]`` (the unknown body is the height plus 24), centre y ``y + h / 2 - 3``;
* envelope ``[x, y - 30, w, h + 54]``: bottom edge ``y + h + 24``;
* anchors: classic output ``(x + w - 9, y + 14)``, input ``(x + 10, y + 14)``; Vue output
  ``(x + w, y + 12)``, input ``(x, y + 12)``.
"""

from __future__ import annotations

import json
import math
import unittest
from dataclasses import replace
from typing import Any

from tests.support import FIXTURES

from cwc.canvas.envelope import COLLAPSED_PAD, VUE_COLLAPSED_PAD, Renderer, box_facts
from cwc.canvas.geometry import Rect
from cwc.canvas.links import crosses, spline
from cwc.canvas.model import Canvas
from cwc.nodedefs.registry import NodeDefs
from cwc.report import Code, Mode, Report, Severity
from cwc.verify.geometry import check_geometry
from cwc.verify.geometry_links import link_crossings
from cwc.verify.geometry_view import GeometryView

LAYOUT = FIXTURES / "layout"
COMPILE, BEAUTIFY = Mode.COMPILE, Mode.BEAUTIFY


class NoDefs:
    def get(self, type: str) -> None:
        return None


def node(key: int, x: float, y: float, w: float = 300, h: float = 100, *, collapsed: bool = False, pinned: bool = False) -> dict:
    flags: dict[str, Any] = {}
    if collapsed:
        flags["collapsed"] = True
    if pinned:
        flags["pinned"] = True
    return {
        "id": key,
        "type": "T",
        "pos": [x, y],
        "size": [w, h],
        "flags": flags,
        "order": 0,
        "mode": 0,
        "inputs": [{"name": "a", "type": "X", "link": None}, {"name": "b", "type": "X", "link": None}],
        "outputs": [{"name": "o", "type": "X", "links": []}],
        "properties": {},
    }


def group(x: float, y: float, w: float, h: float, *, pinned: bool = False, title: str = "g") -> dict:
    return {"title": title, "bounding": [x, y, w, h], "flags": {"pinned": True} if pinned else {}}


def link(link_id: int, origin: int, target: int, target_slot: int = 0) -> list:
    return [link_id, origin, 0, target, target_slot, "X"]


def canvas(nodes: list[dict], links: list[list] | None = None, groups: list[dict] | None = None, extra: dict | None = None) -> Canvas:
    return Canvas(
        {
            "last_node_id": max((n["id"] for n in nodes), default=0),
            "last_link_id": max((entry[0] for entry in links or []), default=0),
            "nodes": nodes,
            "links": links or [],
            "groups": groups or [],
            "config": {},
            "extra": extra or {},
            "version": 0.4,
        }
    )


def run(cv: Canvas, mode: Mode = COMPILE, **kwargs: Any) -> Report:
    return check_geometry(cv, NoDefs(), mode=mode, **kwargs)


def found(report: Report, code: Code) -> list:
    return [f for f in report.findings if f.code is code]


def severities(report: Report, code: Code) -> list[Severity]:
    return [f.severity for f in found(report, code)]


class Overlap(unittest.TestCase):
    def test_touching_passes_and_one_pixel_fails(self) -> None:
        for dx, count in ((300, 0), (299, 1)):
            report = run(canvas([node(1, 0, 0), node(2, dx, 0)]))
            self.assertEqual(len(found(report, Code.G1)), count, dx)
        self.assertEqual(found(run(canvas([node(1, 0, 0), node(2, 299, 0)])), Code.G1)[0].nodes, ("1", "2"))

    def test_one_pixel_vertically(self) -> None:
        # the envelope bottom is y + 124; the next node's title band starts at its y - 30
        self.assertEqual(len(found(run(canvas([node(1, 0, 0), node(2, 0, 154)])), Code.G1)), 0)
        self.assertEqual(len(found(run(canvas([node(1, 0, 0), node(2, 0, 153)])), Code.G1)), 1)

    def test_the_envelope_counts_not_the_classic_box_alone(self) -> None:
        # classic bottom is y + 100 = 100; the second node's classic top is 110, its Vue box reaches 124
        report = run(canvas([node(1, 0, 0), node(2, 0, 140)]))
        self.assertEqual(len(found(report, Code.G1)), 1)

    def test_severity_by_mode_and_pinning(self) -> None:
        both = canvas([node(1, 0, 0, pinned=True), node(2, 299, 0, pinned=True)])
        one = canvas([node(1, 0, 0, pinned=True), node(2, 299, 0)])
        self.assertEqual(severities(run(both, COMPILE), Code.G1), [Severity.ERROR])
        self.assertEqual(severities(run(both, BEAUTIFY), Code.G1), [Severity.WARNING])
        self.assertEqual(severities(run(one, BEAUTIFY), Code.G1), [Severity.ERROR])
        self.assertEqual(severities(run(one, COMPILE), Code.G1), [Severity.ERROR])
        self.assertEqual(run(both, BEAUTIFY).metrics["G1"], {"pairs": 1, "pinned_pairs": 1})


class Crossing(unittest.TestCase):
    """A at x 0 and C at x 1200 on the same row: the link is the straight line y = 14 (classic), 12 (Vue)."""

    def triple(self, b_y: float, **flags: Any) -> Canvas:
        return canvas([node(1, 0, 0), node(2, 600, b_y, **flags), node(3, 1200, 0)], [link(1, 1, 3)])

    def test_clear_of_the_node_passes(self) -> None:
        self.assertEqual(found(run(self.triple(300)), Code.G2), [])

    def test_touching_the_line_passes_and_one_pixel_in_fails(self) -> None:
        # node 2's top edge is b_y - 30; the classic line is y = 14, the Vue line y = 12
        self.assertEqual(found(run(self.triple(44)), Code.G2), [])  # top edge on the line
        self.assertEqual(found(run(self.triple(45)), Code.G2), [])
        report = run(self.triple(43))
        (finding,) = found(report, Code.G2)
        self.assertEqual(finding.severity, Severity.ERROR)
        self.assertEqual(finding.nodes, ("1", "2", "3"))
        self.assertEqual(finding.data["link"], 1)
        self.assertEqual(report.metrics["G2"]["count"], 1)

    def test_a_link_counts_when_either_renderer_crosses(self) -> None:
        # node 2 at y -90: classic box bottom 10 (line y 14 passes below), Vue bottom 34 (line y 12 inside)
        report = run(self.triple(-90))
        (finding,) = found(report, Code.G2)
        self.assertEqual((finding.data["classic"], finding.data["vue"]), ([], ["2"]))
        self.assertEqual((report.metrics["G2"]["crossing_classic"], report.metrics["G2"]["crossing_vue"]), (0, 1))

    def test_ends_are_not_obstacles(self) -> None:
        cv = canvas([node(1, 0, 0), node(2, 100, 0)], [link(1, 1, 2)])  # the ends overlap each other
        self.assertEqual(found(run(cv), Code.G2), [])

    def test_beautify_is_a_warning_and_pinned_obstacles_do_not_count(self) -> None:
        self.assertEqual(severities(run(self.triple(0), BEAUTIFY), Code.G2), [Severity.WARNING])
        self.assertEqual(severities(run(self.triple(0, pinned=True), COMPILE), Code.G2), [Severity.ERROR])
        report = run(self.triple(0, pinned=True), BEAUTIFY)
        self.assertEqual(found(report, Code.G2), [])
        self.assertEqual(report.metrics["G2"]["count"], 0)

    def test_a_pinned_and_an_unpinned_obstacle_still_count(self) -> None:
        cv = canvas(
            [node(1, 0, 0), node(2, 500, 0, pinned=True), node(4, 800, 0), node(3, 1400, 0)], [link(1, 1, 3)]
        )
        (finding,) = found(run(cv, BEAUTIFY), Code.G2)
        self.assertEqual(finding.data["classic"], ["2", "4"])

    def rerouted(self, cv: Canvas, *points: tuple[float, float]) -> Canvas:
        """Link 1 of ``cv`` through reroutes at ``points``, output side first."""
        cv.set_reroutes(
            [{"id": i, "pos": list(p), "linkIds": [1], **({"parentId": i - 1} if i > 1 else {})} for i, p in enumerate(points, 1)],
            [{"id": 1, "parentId": len(points)}],
        )
        return cv

    def test_a_reroute_on_the_line_leaves_the_crossing_in_place(self) -> None:
        report = run(self.rerouted(self.triple(0), (1000, 14)))
        (finding,) = found(report, Code.G2)
        self.assertEqual((finding.data["link"], finding.data["classic"], finding.data["vue"]), (1, ["2"], ["2"]))
        self.assertEqual((report.metrics["G2"]["links_checked"], report.metrics["G2"]["count"]), (1, 1))

    def test_a_middle_segment_that_crosses_a_third_node_counts(self) -> None:
        # the first segment stays left of node 2 and the last right of it; only the one between crosses
        report = run(self.rerouted(self.triple(0), (450, -300), (1050, 300)))
        (finding,) = found(report, Code.G2)
        self.assertEqual((finding.data["link"], finding.data["classic"], finding.data["vue"]), (1, ["2"], ["2"]))

    def test_reroutes_that_take_the_link_around_the_node_pass(self) -> None:
        self.assertEqual(len(found(run(self.triple(0)), Code.G2)), 1)  # without them the link crosses
        report = run(self.rerouted(self.triple(0), (400, -200), (1100, -200)))
        self.assertEqual(found(report, Code.G2), [])
        self.assertEqual((report.metrics["G2"]["links_checked"], report.metrics["G2"]["count"]), (1, 0))

    def test_a_link_through_reroutes_is_checked_under_both_renderers(self) -> None:
        # node 2 at y -90: only the Vue box (bottom 34) holds the line y 12; the reroute keeps the line there
        report = run(self.rerouted(self.triple(-90), (1000, 12)))
        (finding,) = found(report, Code.G2)
        self.assertEqual((finding.data["classic"], finding.data["vue"]), ([], ["2"]))

    def test_a_link_to_a_missing_slot_is_not_drawable(self) -> None:
        cv = canvas([node(1, 0, 0), node(3, 1200, 0)], [link(1, 1, 3, target_slot=5)])
        report = run(cv)
        self.assertEqual(report.metrics["G2"]["links_unresolved"], 1)

    def big(self, total: int, crossings: int) -> Canvas:
        """``crossings`` triples (A, B, C with B on A's link to C), the rest a chain of fillers far away."""
        nodes, links = [], []
        key = 1
        for k in range(crossings):
            base = 400 * k
            nodes += [node(key, 0, base), node(key + 1, 600, base), node(key + 2, 1200, base)]
            links.append(link(len(links) + 1, key, key + 2))
            key += 3
        previous = None
        for i in range(total - len(nodes)):
            nodes.append(node(key, 400 * i, 100000))
            if previous is not None:
                links.append(link(len(links) + 1, previous, key))
            previous = key
            key += 1
        return canvas(nodes, links)

    def test_limit_is_zero_up_to_100_nodes_and_two_percent_above(self) -> None:
        at_100 = run(self.big(100, 1))
        self.assertEqual((len(found(at_100, Code.G2)), at_100.metrics["G2"]["limit"]), (1, 0))
        at_101 = run(self.big(101, 1))
        self.assertEqual((found(at_101, Code.G2), at_101.metrics["G2"]["limit"], at_101.metrics["G2"]["links"]), ([], 1, 98))
        self.assertEqual(at_101.metrics["G2"]["count"], 1)
        # two crossing links against a limit of one
        at_101_two = run(self.big(101, 2))
        self.assertEqual((len(found(at_101_two, Code.G2)), at_101_two.metrics["G2"]["limit"]), (2, 1))
        self.assertEqual(severities(run(self.big(101, 2), BEAUTIFY), Code.G2), [Severity.WARNING] * 2)

    def test_the_limit_is_two_percent_of_every_link_rerouted_ones_included(self) -> None:
        # 101 nodes, 98 links of which 50 go through a reroute far from every node: all 98 are checked.
        cv = self.big(101, 1)
        reroutes = [{"id": i, "pos": [0, 50000 + i]} for i in range(1, 51)]
        extensions = [{"id": i, "parentId": i - 48} for i in range(49, 99)]
        cv.set_reroutes(reroutes, extensions)
        metrics = run(cv).metrics["G2"]
        self.assertEqual((metrics["links"], metrics["links_checked"], metrics["limit"]), (98, 98, 1))
        self.assertNotIn("links_with_reroutes_skipped", metrics)

    def test_a_collapsed_end_is_counted(self) -> None:
        cv = canvas(
            [node(1, 0, 0, collapsed=True), node(2, 600, 0), node(3, 1200, 0)],
            [link(1, 1, 3)],
        )
        report = run(cv)
        self.assertEqual(len(found(report, Code.G2)), 1)
        self.assertEqual(report.metrics["G2"]["collapsed_end"], 1)


def _load(name: str) -> dict:
    return json.loads((LAYOUT / f"{name}.json").read_text(encoding="utf-8"))


class FrontendAgreement(unittest.TestCase):
    """G2 against the recorded runs: each recorded link's curve and each recorded node box give the
    links that pass through a node; check_geometry's crossings are compared with them.

    The recorded links all stayed on the predicted stroke (``on_path`` 21 of 21, bar one Vue sample of
    boxes link 4); the question here is which boxes they cross. In ``links.canvas`` none do, and cwc
    says none. In ``boxes.canvas`` link 5 passes through node 5's box (the recorded one too) and cwc finds it.
    With the old estimate (0.6 em an ASCII character) cwc put boxes link 8 through node 3, the collapsed
    node whose title is 46 characters: it estimated the title width as 386 against the measured 249, so
    the collapsed box was 400 (classic) and 422 (Vue) wide where the frontend drew 309 and 285. The
    measured per-character table estimates 253.7 (a box of 314 and 290), and the estimated and the
    measured title widths agree on every crossing.
    """

    DEFS = NodeDefs.load()

    def view(self, name: str, run_name: str, measured: bool) -> tuple[GeometryView, dict]:
        cv = Canvas(_load(f"{name}.canvas"))
        recorded = _load(f"{name}.{run_name}")
        nodes = cv.node_map()
        facts = {}
        for entry in recorded["nodes"]:
            item = nodes[entry["id"]]
            facts[item.key] = box_facts(item, self.DEFS.get(item.type))
            if measured and item.collapsed:
                pad = COLLAPSED_PAD if run_name == "classic" else VUE_COLLAPSED_PAD
                facts[item.key] = replace(facts[item.key], title_width=entry["bounding"][2] - pad)
        return GeometryView.from_facts(cv, facts), recorded

    @staticmethod
    def recorded_crossings(recorded: dict) -> dict[int, tuple[str, ...]]:
        boxes = {entry["id"]: Rect(*entry["bounding"]) for entry in recorded["nodes"]}
        out = {}
        for item in recorded["links"]:
            ends = {item["origin"][0], item["target"][0]}
            path = spline(tuple(item["start"]), tuple(item["end"])).sample()
            hit = tuple(key for key in boxes if key not in ends and crosses(path, boxes[key]))
            if hit:
                out[item["id"]] = hit
        return out

    def test_measured_title_widths_agree_exactly(self) -> None:
        for name in ("boxes", "links"):
            for run_name in ("classic", "vue"):
                view, recorded = self.view(name, run_name, measured=True)
                got = link_crossings(view, Renderer(run_name), view.canvas.links)
                self.assertEqual(got, self.recorded_crossings(recorded), (name, run_name))

    def test_estimated_title_widths_agree_too(self) -> None:
        for name in ("boxes", "links"):
            for run_name in ("classic", "vue"):
                view, recorded = self.view(name, run_name, measured=False)
                got = link_crossings(view, Renderer(run_name), view.canvas.links)
                self.assertEqual(got, self.recorded_crossings(recorded), (name, run_name))

    def test_the_known_links(self) -> None:
        views = {name: self.view(name, "classic", measured=False)[0] for name in ("boxes", "links")}
        links_report = check_geometry(views["links"].canvas, self.DEFS, mode=BEAUTIFY)
        self.assertEqual(links_report.metrics["G2"]["crossing_links"], [])
        boxes_report = check_geometry(views["boxes"].canvas, self.DEFS, mode=BEAUTIFY)
        self.assertEqual(boxes_report.metrics["G2"]["crossing_links"], [5])


class Grid(unittest.TestCase):
    def test_on_grid_passes_and_one_pixel_off_fails(self) -> None:
        self.assertEqual(found(run(canvas([node(1, 20, -40)])), Code.G6), [])
        (finding,) = found(run(canvas([node(1, 21, 0)])), Code.G6)
        self.assertEqual((finding.severity, finding.nodes), (Severity.ERROR, ("1",)))
        self.assertEqual(len(found(run(canvas([node(1, 20, -39)])), Code.G6)), 1)

    def test_rounding_to_a_millionth(self) -> None:
        self.assertEqual(found(run(canvas([node(1, 40.0000001, 0)])), Code.G6), [])
        self.assertEqual(len(found(run(canvas([node(1, 40.00001, 0)])), Code.G6)), 1)

    def test_pinned_nodes_are_exempt_in_beautify_only(self) -> None:
        cv = canvas([node(1, 21, 0, pinned=True)])
        self.assertEqual(found(run(cv, BEAUTIFY), Code.G6), [])
        self.assertEqual(run(cv, BEAUTIFY).metrics["G6"], {"off_grid": 0, "pinned_exempt": 1})
        self.assertEqual(severities(run(cv, COMPILE), Code.G6), [Severity.ERROR])

    def test_off_grid_is_an_error_in_beautify_too(self) -> None:
        self.assertEqual(severities(run(canvas([node(1, 21, 0)]), BEAUTIFY), Code.G6), [Severity.ERROR])


class GroupOverlap(unittest.TestCase):
    def test_touching_and_nesting_pass_one_pixel_fails(self) -> None:
        self.assertEqual(found(run(canvas([], groups=[group(0, 0, 100, 100), group(100, 0, 100, 100)])), Code.G4), [])
        self.assertEqual(found(run(canvas([], groups=[group(0, 0, 100, 100), group(10, 10, 50, 50)])), Code.G4), [])
        self.assertEqual(found(run(canvas([], groups=[group(0, 0, 100, 100), group(50, 10, 50, 50)])), Code.G4), [])  # edge touches
        report = run(canvas([], groups=[group(0, 0, 100, 100), group(99, 99, 100, 100)]))
        (finding,) = found(report, Code.G4)
        self.assertEqual((finding.severity, finding.data["groups"]), (Severity.ERROR, [0, 1]))

    def test_identical_rectangles_are_not_nested(self) -> None:
        self.assertEqual(len(found(run(canvas([], groups=[group(0, 0, 100, 100), group(0, 0, 100, 100)])), Code.G4)), 1)

    def test_pinned_pairs(self) -> None:
        both = canvas([], groups=[group(0, 0, 100, 100, pinned=True), group(99, 99, 100, 100, pinned=True)])
        one = canvas([], groups=[group(0, 0, 100, 100, pinned=True), group(99, 99, 100, 100)])
        self.assertEqual(severities(run(both, BEAUTIFY), Code.G4), [Severity.WARNING])
        self.assertEqual(severities(run(both, COMPILE), Code.G4), [Severity.ERROR])
        self.assertEqual(severities(run(one, BEAUTIFY), Code.G4), [Severity.ERROR])


class Membership(unittest.TestCase):
    """Node 1 at (0, 0), 300 x 100: classic centre (150, 35), Vue centre (150, 47)."""

    def check(self, groups: list[dict], target: int | None, nodes: list[dict] | None = None, mode: Mode = COMPILE) -> list:
        cv = canvas(nodes or [node(1, 0, 0)], groups=groups)
        keys = [n["id"] for n in cv.raw["nodes"]]
        report = run(cv, mode, membership={str(k): target for k in keys})
        return found(report, Code.G3)

    def test_inside_passes(self) -> None:
        self.assertEqual(self.check([group(-10, -100, 400, 300)], 0), [])

    def test_one_pixel_outside_fails(self) -> None:
        # bottom edge 47 excludes the Vue centre (half-open) although the classic centre (y 35) is inside
        (finding,) = self.check([group(-10, -100, 400, 147)], 0)
        self.assertEqual(finding.data["found"], {"classic": 0, "vue": None})
        self.assertEqual(self.check([group(-10, -100, 400, 148)], 0), [])

    def test_left_and_top_edges_belong_to_the_group_right_and_bottom_do_not(self) -> None:
        self.assertEqual(self.check([group(150, -100, 400, 300)], 0), [])  # centre x 150 on the left edge
        self.assertEqual(len(self.check([group(-250, -100, 400, 300)], 0)), 1)  # centre x 150 on the right edge

    def test_innermost_is_the_smallest_group(self) -> None:
        groups = [group(-100, -200, 800, 600), group(-10, -50, 400, 200)]
        self.assertEqual(self.check(groups, 1), [])
        (finding,) = self.check(groups, 0)
        self.assertEqual(finding.data, {"target": 0, "found": {"classic": 1, "vue": 1}})

    def test_ungrouped_target(self) -> None:
        self.assertEqual(self.check([group(500, 500, 100, 100)], None), [])
        self.assertEqual(len(self.check([group(-10, -100, 400, 300)], None)), 1)

    def test_collapsed_node_also_needs_its_expanded_centre(self) -> None:
        # collapsed band centres: y -15 (both renderers); expanded centre (150, 47)
        collapsed = [node(1, 0, 0, collapsed=True)]
        (finding,) = self.check([group(-10, -60, 400, 80)], 0, collapsed)
        self.assertEqual(finding.data["found"], {"classic": 0, "vue": 0, "expanded": None})
        self.assertEqual(self.check([group(-10, -60, 400, 108)], 0, collapsed), [])  # bottom 48 > 47
        self.assertEqual(len(self.check([group(-10, -60, 400, 107)], 0, collapsed)), 1)  # bottom 47: half-open

    def test_beautify_is_an_error_too(self) -> None:
        self.assertEqual(severities(run(canvas([node(1, 0, 0)], groups=[group(500, 500, 10, 10)]), BEAUTIFY, membership={"1": 0}), Code.G3), [Severity.ERROR])

    def test_a_node_without_a_target_and_a_bad_index(self) -> None:
        cv = canvas([node(1, 0, 0), node(2, 0, 400)], groups=[group(-10, -100, 400, 300)])
        report = run(cv, membership={"1": 0})
        (finding,) = found(report, Code.G3)
        self.assertEqual(finding.nodes, ("2",))
        (finding,) = found(run(cv, membership={"1": 0, "2": 3}), Code.G3)
        self.assertEqual(finding.data, {"target": 3})

    def test_an_empty_membership_checks_nothing(self) -> None:
        self.assertEqual(found(run(canvas([]), membership={}), Code.G3), [])


class Ancestry(unittest.TestCase):
    def pair(self, after_groups: list[dict], after_x: float = 0) -> list:
        before = canvas([node(1, 0, 0)], groups=[group(-100, -200, 800, 600), group(-10, -50, 400, 200)])
        after = canvas([node(1, after_x, 0)], groups=after_groups)
        return found(run(after, BEAUTIFY, before=before), Code.G3)

    def test_unchanged_passes(self) -> None:
        self.assertEqual(self.pair([group(-100, -200, 800, 600), group(-10, -50, 400, 200)]), [])

    def test_moving_a_group_so_the_node_leaves_it_fails(self) -> None:
        # the inner group's bottom edge moves to y 35, the node's classic centre y (half-open: outside)
        findings = self.pair([group(-100, -200, 800, 600), group(-10, -50, 400, 85)])
        self.assertEqual([f.nodes for f in findings], [("1",)])
        # the Vue centre (y 47) is below that edge too: both centres name the change
        moves = {"before": [0, 1], "after": [0]}
        self.assertEqual(findings[0].data, {"centres": {"classic": moves, "vue": moves}})
        # bottom edge 36 keeps the classic centre but not the Vue centre (y 47); 48 keeps both
        (vue_only,) = self.pair([group(-100, -200, 800, 600), group(-10, -50, 400, 86)])
        self.assertEqual(list(vue_only.data["centres"]), ["vue"])
        self.assertEqual(self.pair([group(-100, -200, 800, 600), group(-10, -50, 400, 98)]), [])

    def test_the_containment_graph_must_not_change(self) -> None:
        # the node stays in both, but the inner group now sticks out of the outer one
        findings = self.pair([group(-100, -200, 800, 600), group(-10, -50, 800, 200)])
        self.assertEqual([f.data for f in findings], [{"outer": 0, "inner": 1, "before": True, "after": False}])

    def test_group_count_change(self) -> None:
        findings = self.pair([group(-100, -200, 800, 600)])
        self.assertEqual(len(findings), 3)  # the count, the node's ancestors, the containment edge

    def test_before_is_ignored_with_regroup_and_the_membership_wins(self) -> None:
        before = canvas([node(1, 0, 0)], groups=[group(-100, -200, 800, 600), group(-10, -50, 400, 200)])
        after = canvas([node(1, 0, 0)], groups=[group(500, 500, 10, 10)])
        report = run(after, BEAUTIFY, before=before, regroup=True)
        self.assertEqual(found(report, Code.G3), [])
        self.assertIn({"code": "G3", "reason": "no membership or before canvas given"}, report.metrics["skipped"])
        report = run(after, BEAUTIFY, before=before, membership={"1": None})
        self.assertEqual(len(found(report, Code.G3)), 3)
        self.assertIn("ancestry", report.metrics["G3"])


class Direction(unittest.TestCase):
    def forward(self, count: int) -> tuple[list[dict], list[list]]:
        nodes = [node(k, 1000 * k, 10000) for k in range(1, count + 2)]
        return nodes, [link(k, k, k + 1) for k in range(1, count + 1)]

    def with_backward(self, forward_links: int) -> Canvas:
        nodes, links = self.forward(forward_links)
        nodes += [node(900, 5000000, 0), node(901, 0, 0)]
        return canvas(nodes, links + [link(900, 900, 901)])

    def test_five_percent_passes_and_a_little_over_fails(self) -> None:
        ok = run(self.with_backward(19))
        self.assertEqual(found(ok, Code.G5), [])
        self.assertEqual((ok.metrics["G5"]["right_to_left"], ok.metrics["G5"]["links"]), (1, 20))
        over = run(self.with_backward(18))
        (finding,) = found(over, Code.G5)
        self.assertEqual((finding.severity, finding.data["links"]), (Severity.ERROR, [900]))

    def test_severity_in_beautify(self) -> None:
        self.assertEqual(severities(run(self.with_backward(18), BEAUTIFY), Code.G5), [Severity.WARNING])

    def test_links_into_the_cockpit_are_left_out(self) -> None:
        # The backward link ends at node 901 at (0, 0); a group around it is the cockpit.
        cv = self.with_backward(18)
        cv.set_groups([group(-20, -60, 360, 200, title="01 Cockpit")])
        self.assertEqual(len(found(run(cv), Code.G5)), 1)
        report = run(cv, cockpit={0})
        self.assertEqual(found(report, Code.G5), [])
        self.assertEqual((report.metrics["G5"]["cockpit_links"], report.metrics["G5"]["links"]), (1, 18))

    def test_a_cockpit_index_outside_the_groups_is_refused_before_any_check(self) -> None:
        cv = self.with_backward(18)
        cv.set_groups([group(-20, -60, 360, 200, title="01 Cockpit")])
        with self.assertRaisesRegex(ValueError, "cockpit index 5 is outside the 1 groups"):
            run(cv, cockpit={5})

    def test_a_cockpit_split_by_c1_is_left_out_in_every_part(self) -> None:
        # Part 0 ("01a") is empty ground; the backward link ends in part 1 ("01b").
        cv = self.with_backward(18)
        cv.set_groups([group(-20, 400, 360, 200, title="01a Cockpit"), group(-20, -60, 360, 200, title="01b Cockpit")])
        self.assertEqual(len(found(run(cv, cockpit={0}), Code.G5)), 1)
        report = run(cv, cockpit={0, 1})
        self.assertEqual(found(report, Code.G5), [])
        self.assertEqual(report.metrics["G5"]["cockpit_links"], 1)

    def test_a_group_nested_in_the_cockpit_is_left_out(self) -> None:
        # Node 901's innermost group is the inner one (index 1), nested in the cockpit (index 0).
        cv = self.with_backward(18)
        cv.set_groups([group(-20, -60, 360, 200, title="01 Cockpit"), group(-10, -50, 330, 180, title="Inner")])
        self.assertEqual(found(run(cv, cockpit={0}), Code.G5), [])
        self.assertEqual(len(found(run(cv, cockpit={1}), Code.G5)), 0)
        self.assertEqual(len(found(run(cv), Code.G5)), 1)

    def test_one_bare_cockpit_index_is_refused(self) -> None:
        cv = self.with_backward(18)
        cv.set_groups([group(-20, -60, 360, 200, title="01 Cockpit")])
        with self.assertRaisesRegex(TypeError, "collection of group indices"):
            run(cv, cockpit=0)  # type: ignore[arg-type]

    def test_equal_anchors_do_not_count_and_one_pixel_does(self) -> None:
        # output anchor x = x + w - 9 = 291; input anchor x = x2 + 10
        self.assertEqual(run(canvas([node(1, 0, 0), node(2, 281, 0)], [link(1, 1, 2)])).metrics["G5"]["right_to_left"], 0)
        self.assertEqual(run(canvas([node(1, 0, 0), node(2, 280, 0)], [link(1, 1, 2)])).metrics["G5"]["right_to_left"], 1)

    def test_feedback_edges_of_a_cycle_are_left_out(self) -> None:
        cycle = canvas([node(1, 0, 0), node(2, 600, 0)], [link(1, 1, 2), link(2, 2, 1, target_slot=1)])
        report = run(cycle)
        self.assertEqual(found(report, Code.G5), [])
        self.assertEqual((report.metrics["G5"]["links"], report.metrics["G5"]["feedback_links"]), (1, 1))
        alone = canvas([node(1, 0, 0), node(2, 600, 0)], [link(2, 2, 1)])
        self.assertEqual(len(found(run(alone), Code.G5)), 1)  # without the forward edge there is no cycle

    def test_a_self_link_is_a_feedback_edge(self) -> None:
        report = run(canvas([node(1, 0, 0)], [link(1, 1, 1)]))
        self.assertEqual(report.metrics["G5"]["feedback_links"], 1)


class Aspect(unittest.TestCase):
    def test_range_edges(self) -> None:
        # envelope height h + 54: h 46 gives 100, so 280 wide is exactly 2.8
        self.assertEqual(found(run(canvas([node(1, 0, 0, 280, 46)])), Code.G9), [])
        (finding,) = found(run(canvas([node(1, 0, 0, 281, 46)])), Code.G9)
        self.assertEqual(finding.severity, Severity.WARNING)
        # width 225 (the Vue minimum), height 225: exactly square
        self.assertEqual(found(run(canvas([node(1, 0, 0, 225, 171)])), Code.G9), [])
        self.assertEqual(len(found(run(canvas([node(1, 0, 0, 225, 172)])), Code.G9)), 1)

    def test_warning_in_both_modes_and_groups_widen_the_extent(self) -> None:
        wide = canvas([node(1, 0, 0, 900, 100)])
        self.assertEqual(severities(run(wide, BEAUTIFY), Code.G9), [Severity.WARNING])
        self.assertEqual(severities(run(wide, COMPILE), Code.G9), [Severity.WARNING])
        taller = canvas([node(1, 0, 0, 900, 100)], groups=[group(0, -30, 900, 700)])
        self.assertEqual(found(run(taller), Code.G9), [])

    def test_empty_canvas(self) -> None:
        self.assertEqual(run(canvas([])).metrics["G9"], {"extent": None, "ratio": None})


class MeanLength(unittest.TestCase):
    def test_straight_link(self) -> None:
        # classic anchors (291, 14) and (510, 14)
        report = run(canvas([node(1, 0, 0), node(2, 500, 0)], [link(1, 1, 2)]))
        self.assertAlmostEqual(report.metrics["G10"]["mean"], 219.0, places=3)
        self.assertEqual(report.metrics["G10"]["links"], 1)

    def test_a_rerouted_link_is_the_sum_of_its_segments(self) -> None:
        # reroute on the line between the anchors (291, 14) and (510, 14): 109 + 110
        cv = canvas([node(1, 0, 0), node(2, 500, 0)], [link(1, 1, 2)])
        cv.set_reroutes([{"id": 1, "pos": [400, 14], "linkIds": [1]}], [{"id": 1, "parentId": 1}])
        self.assertAlmostEqual(run(cv).metrics["G10"]["mean"], 219.0, places=3)
        # off the line the route is longer than the straight distance
        cv.set_reroutes([{"id": 1, "pos": [400, 314], "linkIds": [1]}], [{"id": 1, "parentId": 1}])
        self.assertGreater(run(cv).metrics["G10"]["mean"], 219.0 + 300)

    def test_the_recorded_canvas_gives_the_sum_of_the_recorded_segments(self) -> None:
        recorded = json.loads((LAYOUT / "reroutes.classic.json").read_text(encoding="utf-8"))["results"]["main"]["draw"]
        cv = Canvas(json.loads((LAYOUT / "reroutes.canvases.json").read_text(encoding="utf-8"))["main"])

        def bezier(seg: dict, t: float) -> tuple[float, float]:
            u = 1 - t
            w = (u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t)
            pts = (seg["a"], seg["c1"], seg["c2"], seg["b"])
            return (sum(k * p[0] for k, p in zip(w, pts)), sum(k * p[1] for k, p in zip(w, pts)))

        lengths = []
        for row in recorded["predicted"]:
            total = 0.0
            for seg in row["segments"]:
                points = [bezier(seg, i / 20) for i in range(21)]
                total += sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            lengths.append(total)
        report = check_geometry(cv, NodeDefs.load(), mode=COMPILE)
        self.assertEqual(report.metrics["G10"]["links"], len(lengths))
        self.assertAlmostEqual(report.metrics["G10"]["mean"], sum(lengths) / len(lengths), places=2)

    def test_before_and_after_in_beautify_only(self) -> None:
        before = canvas([node(1, 0, 0), node(2, 1000, 0)], [link(1, 1, 2)])
        after = canvas([node(1, 0, 0), node(2, 500, 0)], [link(1, 1, 2)])
        metrics = run(after, BEAUTIFY, before=before).metrics["G10"]
        self.assertAlmostEqual(metrics["before"]["mean"], 719.0, places=3)
        self.assertAlmostEqual(metrics["after"]["mean"], 219.0, places=3)
        self.assertEqual(set(run(after, COMPILE, before=before).metrics["G10"]), {"mean", "links"})

    def test_no_links(self) -> None:
        self.assertEqual(run(canvas([node(1, 0, 0)])).metrics["G10"], {"mean": 0.0, "links": 0})


class Report_(unittest.TestCase):
    def test_skipped_and_metric_codes(self) -> None:
        metrics = run(canvas([node(1, 0, 0)])).metrics
        self.assertEqual(
            metrics["skipped"],
            [
                {"code": "G3", "reason": "no membership or before canvas given"},
                {"code": "C3", "reason": "no preset given"},
                {"code": "C4", "reason": "no cockpit group given"},
                {"code": "C5", "reason": "no guide node"},
            ],
        )
        self.assertEqual({"G1", "G2", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "C1", "C2", "C6"} - set(metrics), set())

    def test_a_clean_canvas_is_ok(self) -> None:
        # the group is the first interactive group (G8) and its title band is on the first screen
        cv = canvas([node(1, 20, 60), node(2, 520, 60)], [link(1, 1, 2)], groups=[group(0, 0, 900, 400, title="01 Load")])
        report = run(cv, membership={"1": 0, "2": 0})
        self.assertEqual([f.to_json() for f in report.findings], [])
        self.assertTrue(report.ok)

    def test_deterministic(self) -> None:
        nodes = [node(1, 21, 0), node(2, 299, 0), node(3, 600, 10), node(4, 900, 0)]
        cv = canvas(nodes, [link(1, 1, 4), link(2, 4, 2, 1)], groups=[group(0, 0, 100, 100), group(99, 99, 100, 100)])
        first = json.dumps(run(cv, BEAUTIFY, before=cv, membership={"1": 0}).to_json(), sort_keys=True)
        second = json.dumps(run(cv, BEAUTIFY, before=cv, membership={"1": 0}).to_json(), sort_keys=True)
        self.assertEqual(first, second)
        self.assertIn('"G1"', first)


if __name__ == "__main__":
    unittest.main()
