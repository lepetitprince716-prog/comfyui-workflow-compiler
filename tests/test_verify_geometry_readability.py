"""check_geometry: G7, G8 and C1-C6 on small written canvases.

Same hand-written canvases as ``test_verify_geometry`` (one unknown node type ``T``, so a node's
drawn title is its saved title, else ``T``). A node at ``(x, y)`` with size ``(w, h)`` has the
classic box ``[x, y - 30, w, h + 30]`` and an envelope ``[x, y - 30, w, h + 54]`` (w >= 225).
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from tests.test_verify_geometry import BEAUTIFY, COMPILE, canvas, found, group, link, node, run, severities

from cwc.canvas.model import Canvas
from cwc.layout.types import Axis, Numbering, Preset
from cwc.report import Code, Mode, Severity

ERROR, WARNING, INFO = Severity.ERROR, Severity.WARNING, Severity.INFO


def preset(axis: Axis = Axis.X, numbering: Numbering = Numbering.NUMBER) -> Preset:
    return Preset(
        name="test",
        axis=axis,
        bands=(),
        corridors=(),
        wrap_ratio=2.0,
        guide_width=400,
        numbering=numbering,
        palette="test",
    )


def titled(key: int, x: float, y: float, title: str, w: float = 300, h: float = 100, **flags: Any) -> dict:
    entry = node(key, x, y, w, h, **flags)
    entry["title"] = title
    return entry


def guide(key: int, x: float, y: float, w: float = 300, h: float = 100) -> dict:
    entry = node(key, x, y, w, h)
    entry["properties"] = {"cwc_role": "guide"}
    return entry


def bypassed(entry: dict) -> dict:
    entry["mode"] = 4
    return entry


def guide_part(report: Any) -> list:
    return [f for f in found(report, Code.G8) if f.data["part"] == "guide"]


def skipped(report: Any) -> dict[str, str]:
    return {item["code"]: item["reason"] for item in report.metrics["skipped"]}


class ReadableWidth(unittest.TestCase):
    """The title "x" * 28 needs its width at the title font (the measured table (7.643 px an "x" at 14 px),
    times the table's safety factor) plus 60 px: 278.0915 px; 29 characters need 285.8805."""

    TITLE = "x" * 28
    NEEDED = 278.0915

    def test_exactly_the_needed_width_passes_and_a_little_less_fails(self) -> None:
        self.assertEqual(found(run(canvas([titled(1, 0, 0, self.TITLE, w=self.NEEDED + 0.001)])), Code.G7), [])
        (finding,) = found(run(canvas([titled(1, 0, 0, self.TITLE, w=self.NEEDED - 0.1)])), Code.G7)
        self.assertEqual((finding.nodes, finding.data["width"]), (("1",), self.NEEDED - 0.1))
        self.assertAlmostEqual(finding.data["needed"], self.NEEDED, places=3)

    def test_a_longer_title_needs_more(self) -> None:
        self.assertEqual(len(found(run(canvas([titled(1, 0, 0, "x" * 29, w=280)])), Code.G7)), 1)
        self.assertEqual(found(run(canvas([titled(1, 0, 0, "x" * 28, w=280)])), Code.G7), [])

    def test_collapsed_nodes_are_not_counted(self) -> None:
        cv = canvas([titled(1, 0, 0, "x" * 50, w=100, collapsed=True)])
        self.assertEqual(found(run(cv), Code.G7), [])
        self.assertEqual(run(cv).metrics["G7"], {"expanded": 0, "narrow": 0})

    def test_error_in_both_modes(self) -> None:
        cv = canvas([titled(1, 0, 0, self.TITLE, w=self.NEEDED - 0.1)])
        self.assertEqual(severities(run(cv, COMPILE), Code.G7), [ERROR])
        self.assertEqual(severities(run(cv, BEAUTIFY), Code.G7), [ERROR])
        self.assertEqual(run(cv).metrics["G7"], {"expanded": 1, "narrow": 1})


class FirstScreen(unittest.TestCase):
    """A group at ``x`` of width 1000 has its title band ``[x, 0, 600, 30]`` clipped; node 1 is inside it."""

    def first(self, x: float, title: str = "01 Load", node_x: float | None = None) -> Canvas:
        member = titled(1, x + 100 if node_x is None else node_x, 100, "m")
        return canvas([member], groups=[group(x, 0, 1000, 500, title=title)])

    def test_the_title_band_reaches_1600_and_one_pixel_more_fails(self) -> None:
        self.assertEqual(found(run(self.first(1000)), Code.G8), [])
        (finding,) = found(run(self.first(1001, node_x=1100)), Code.G8)
        self.assertEqual((finding.severity, finding.data["part"], finding.data["box"]), (ERROR, "title", [1001, 0, 600, 30]))

    def test_a_narrow_group_is_clipped_to_its_own_width(self) -> None:
        cv = canvas([titled(1, 1300, 100, "m")], groups=[group(1300, 0, 300, 500, title="01 Load")])
        self.assertEqual(found(run(cv), Code.G8), [])
        cv = canvas([titled(1, 1300, 100, "m")], groups=[group(1301, 0, 300, 500, title="01 Load")])
        self.assertEqual(len(found(run(cv), Code.G8)), 1)

    def test_the_title_band_must_be_within_the_height_and_not_above_the_screen(self) -> None:
        cv = canvas([titled(1, 100, 900, "m")], groups=[group(0, 870, 1000, 500, title="01 Load")])
        self.assertEqual([f.data["part"] for f in found(run(cv), Code.G8)], ["member"])  # band bottom 900 is inside
        cv = canvas([titled(1, 100, 900, "m")], groups=[group(0, 871, 1000, 500, title="01 Load")])
        self.assertEqual([f.data["part"] for f in found(run(cv), Code.G8)], ["title", "member"])
        cv = canvas([titled(1, 100, 100, "m")], groups=[group(0, -1, 1000, 500, title="01 Load")])
        self.assertEqual([f.data["part"] for f in found(run(cv), Code.G8)], ["title"])

    def test_the_top_most_member_envelope_must_fit(self) -> None:
        # a 300 wide node at x 1300 ends at 1600; at 1301 it sticks out (its centre stays in the group)
        self.assertEqual(found(run(self.first(1000, node_x=1300)), Code.G8), [])
        (finding,) = found(run(self.first(1000, node_x=1301)), Code.G8)
        self.assertEqual((finding.nodes, finding.data["part"]), (("1",), "member"))
        # its top edge: y - 30 must not be above the screen
        cv = canvas([titled(1, 100, 29, "m")], groups=[group(0, 0, 1000, 500, title="01 Load")])
        self.assertEqual(len(found(run(cv), Code.G8)), 1)
        cv = canvas([titled(1, 100, 30, "m")], groups=[group(0, 0, 1000, 500, title="01 Load")])
        self.assertEqual(found(run(cv), Code.G8), [])

    def test_the_top_most_member_is_by_y_then_x_then_id(self) -> None:
        group_ = [group(0, 0, 1600, 800, title="01 Load")]
        cv = canvas([titled(1, 1300, 300, "a"), titled(2, 100, 200, "b"), titled(3, 50, 200, "c")], groups=group_)
        report = run(cv)
        self.assertEqual((found(report, Code.G8), report.metrics["G8"]["top_member"]), ([], "3"))
        cv = canvas([titled(1, 1301, 200, "a"), titled(2, 1301, 200, "b")], groups=group_)
        report = run(cv)
        self.assertEqual((len(found(report, Code.G8)), report.metrics["G8"]["top_member"]), (1, "1"))

    def test_a_member_of_a_nested_group_counts(self) -> None:
        cv = canvas(
            [titled(1, 1301, 100, "m")],
            groups=[group(0, 0, 2000, 500, title="01 Load"), group(1000, 50, 800, 300, title="inner")],
        )
        self.assertEqual([f.nodes for f in found(run(cv), Code.G8)], [("1",)])

    def test_which_title_is_the_first_group(self) -> None:
        # the group sits at x 1001, so a title that is the first group's reports its band off the first screen
        # (group 0); any other title leaves the canvas without a first group (group None)
        for title, hit in (("01 Load", True), ("01a Load", True), ("01ab Load", False), ("01", False), ("010 Load", False), ("Step 1", True), ("Step 1 Load", True), ("Step 10", False), ("02 Load", False)):
            with self.subTest(title=title):
                cv = canvas([titled(1, 100, 100, "m")], groups=[group(1001, 0, 1000, 500, title=title)])
                report = run(cv)
                self.assertEqual(report.metrics["G8"]["first_group"] == 0, hit)
                self.assertEqual([f.data["part"] for f in found(report, Code.G8)], ["title"])  # off screen, or no such title
                self.assertEqual(found(report, Code.G8)[0].data["group"], 0 if hit else None)

    def test_the_numbering_of_the_preset_picks_the_title(self) -> None:
        by_number = canvas([titled(1, 100, 100, "m")], groups=[group(1001, 0, 1000, 500, title="01 Load")])
        by_step = canvas([titled(1, 100, 100, "m")], groups=[group(1001, 0, 1000, 500, title="Step 1 Load")])
        for cv, numbering, hit in (
            (by_number, Numbering.NUMBER, True),
            (by_number, Numbering.STEP, False),
            (by_step, Numbering.STEP, True),
            (by_step, Numbering.NUMBER, False),
            (by_number, None, True),
            (by_step, None, True),
        ):
            with self.subTest(numbering=numbering, canvas=cv.groups[0].title):
                given = None if numbering is None else preset(numbering=numbering)
                self.assertEqual(run(cv, preset=given).metrics["G8"]["first_group"] == 0, hit)

    def test_the_first_matching_group_wins(self) -> None:
        cv = canvas([], groups=[group(0, 0, 500, 500, title="01 A"), group(1001, 0, 500, 500, title="01 B")])
        self.assertEqual(run(cv).metrics["G8"]["first_group"], 0)
        self.assertEqual(found(run(cv), Code.G8), [])

    def test_no_first_group_is_an_error_where_the_groups_are_written(self) -> None:
        cv = canvas([titled(1, 100, 100, "m")], groups=[group(0, 0, 500, 500, title="A")])
        for mode, regroup in ((COMPILE, False), (BEAUTIFY, True)):
            with self.subTest(mode=mode, regroup=regroup):
                report = run(cv, mode, regroup=regroup)
                (finding,) = found(report, Code.G8)
                self.assertEqual((finding.severity, finding.nodes, finding.data["part"], finding.data["group"]), (ERROR, (), "title", None))
                self.assertIn('"01 "', finding.message)
                self.assertIn('"Step 1"', finding.message)
                self.assertEqual(report.metrics["G8"]["first_group"], None)
        # the message names the title the preset's numbering looks for
        (finding,) = found(run(cv, preset=preset(numbering=Numbering.STEP)), Code.G8)
        self.assertNotIn('"01 "', finding.message)
        self.assertIn('"Step 1"', finding.message)
        (finding,) = found(run(cv, preset=preset(numbering=Numbering.NUMBER)), Code.G8)
        self.assertIn('"01 "', finding.message)
        self.assertNotIn('"Step 1"', finding.message)

    def test_no_first_group_is_not_reported_where_the_author_titles_stay(self) -> None:
        cv = canvas([titled(1, 5000, 5000, "m")], groups=[group(5000, 5000, 500, 500, title="A")])
        for mode in (BEAUTIFY, Mode.CHECK):
            with self.subTest(mode=mode):
                report = run(cv, mode)
                self.assertEqual((found(report, Code.G8), report.metrics["G8"]["first_group"]), ([], None))

    def test_no_first_group_on_a_canvas_without_nodes_has_nothing_to_test(self) -> None:
        for groups in ([], [group(0, 0, 500, 500, title="A")]):
            with self.subTest(groups=len(groups)):
                self.assertEqual(found(run(canvas([], groups=groups)), Code.G8), [])

    def test_the_group_part_runs_in_compile_and_beautify_with_regroup_only(self) -> None:
        cv = self.first(1001, node_x=1100)
        self.assertEqual(severities(run(cv, COMPILE), Code.G8), [ERROR])
        self.assertEqual(severities(run(cv, BEAUTIFY, regroup=True), Code.G8), [ERROR])
        report = run(cv, BEAUTIFY)
        self.assertEqual((found(report, Code.G8), report.metrics["G8"]["group_part"]), ([], False))
        self.assertEqual(found(run(cv, Mode.CHECK), Code.G8), [])

    def test_a_guide_node_must_be_inside_the_screen_in_every_mode(self) -> None:
        # an envelope of 300 x 154 at x 1300 ends at 1600; these canvases have no groups, so only the guide part counts
        for mode, regroup in ((COMPILE, False), (BEAUTIFY, False), (BEAUTIFY, True)):
            with self.subTest(mode=mode, regroup=regroup):
                self.assertEqual(guide_part(run(canvas([guide(1, 1300, 100)]), mode, regroup=regroup)), [])
                (finding,) = guide_part(run(canvas([guide(1, 1301, 100)]), mode, regroup=regroup))
                self.assertEqual((finding.severity, finding.nodes, finding.data["part"]), (ERROR, ("1",), "guide"))
        self.assertEqual(len(guide_part(run(canvas([guide(1, 100, 29)])))), 1)
        self.assertEqual(guide_part(run(canvas([guide(1, 100, 30)]))), [])
        # bottom edge: y + 100 + 24 <= 900
        self.assertEqual(guide_part(run(canvas([guide(1, 100, 776)]))), [])
        self.assertEqual(len(guide_part(run(canvas([guide(1, 100, 777)])))), 1)

    def test_without_a_guide_the_guide_part_does_not_apply(self) -> None:
        report = run(canvas([node(1, 5000, 5000)]), BEAUTIFY)
        self.assertEqual((found(report, Code.G8), report.metrics["G8"]["guides"]), ([], 0))
        self.assertNotIn("G8", skipped(report))


class Crowded(unittest.TestCase):
    def crowd(self, expanded: int, collapsed: int = 0) -> Canvas:
        nodes = [titled(k, 320 * k, 0, f"n{k}") for k in range(expanded)]
        nodes += [titled(100 + k, 320 * (expanded + k), 0, f"c{k}", collapsed=True) for k in range(collapsed)]
        return canvas(nodes, groups=[group(-10, -100, 320 * (expanded + collapsed) + 20, 400)])

    def test_twelve_pass_and_thirteen_fail(self) -> None:
        self.assertEqual(found(run(self.crowd(12)), Code.C1), [])
        (finding,) = found(run(self.crowd(13)), Code.C1)
        self.assertEqual((finding.data, len(finding.nodes)), ({"group": 0, "expanded": 13}, 13))

    def test_collapsed_nodes_do_not_count(self) -> None:
        self.assertEqual(found(run(self.crowd(12, collapsed=5)), Code.C1), [])
        self.assertEqual(run(self.crowd(12, collapsed=5)).metrics["C1"], {"limit": 12, "most_expanded": 12, "crowded": 0})

    def test_severity_by_mode(self) -> None:
        self.assertEqual(severities(run(self.crowd(13), COMPILE), Code.C1), [ERROR])
        self.assertEqual(severities(run(self.crowd(13), BEAUTIFY), Code.C1), [WARNING])

    def test_only_the_innermost_group_counts_its_nodes(self) -> None:
        nodes = [titled(k, 320 * k, 0, f"n{k}") for k in range(14)]
        # the inner group holds 7 of them, the outer one holds all 14 but 7 of them are inner members
        groups = [group(-10, -100, 320 * 14 + 20, 400), group(-10, -100, 320 * 7, 400)]
        self.assertEqual(found(run(canvas(nodes, groups=groups)), Code.C1), [])
        groups = [group(-10, -100, 320 * 14 + 20, 400), group(-10, -100, 320 * 13, 400)]
        self.assertEqual([f.data["group"] for f in found(run(canvas(nodes, groups=groups)), Code.C1)], [1])

    def test_ungrouped_nodes_are_not_counted(self) -> None:
        nodes = [titled(k, 320 * k, 0, f"n{k}") for k in range(20)]
        self.assertEqual(found(run(canvas(nodes)), Code.C1), [])


class TopLevelCount(unittest.TestCase):
    def spread(self, count: int) -> list[dict]:
        return [group(200 * k, 0, 100, 100) for k in range(count)]

    def test_twelve_pass_and_thirteen_fail(self) -> None:
        self.assertEqual(found(run(canvas([], groups=self.spread(12))), Code.C2), [])
        (finding,) = found(run(canvas([], groups=self.spread(13))), Code.C2)
        self.assertEqual(finding.data["count"], 13)

    def test_nested_groups_do_not_count(self) -> None:
        nested = [group(200 * k + 10, 10, 50, 50) for k in range(12)]
        report = run(canvas([], groups=self.spread(12) + nested))
        self.assertEqual((found(report, Code.C2), report.metrics["C2"]), ([], {"limit": 12, "top_level": 12}))
        extra = [group(5000, 0, 100, 100)]
        self.assertEqual(len(found(run(canvas([], groups=self.spread(12) + nested + extra)), Code.C2)), 1)

    def test_severity_by_mode(self) -> None:
        cv = canvas([], groups=self.spread(13))
        self.assertEqual(severities(run(cv, COMPILE), Code.C2), [WARNING])
        self.assertEqual(severities(run(cv, BEAUTIFY), Code.C2), [INFO])


class Axis_(unittest.TestCase):
    """Groups A and B, a link from node 1 (in A) to node 2 (in B)."""

    def pair(self, b_x: float, b_y: float = 0, links: list[list] | None = None, mode_b: int = 0) -> Canvas:
        nodes = [titled(1, 0, 0, "a"), bypassed(titled(2, b_x + 100, b_y + 100, "b")) if mode_b else titled(2, b_x + 100, b_y + 100, "b")]
        groups = [group(-10, -100, 500, 400, title="A"), group(b_x, b_y, 500, 400, title="B")]
        return canvas(nodes, links if links is not None else [link(1, 1, 2)], groups=groups)

    def test_downstream_at_or_after_the_upstream_start_passes(self) -> None:
        for b_x in (-10, 0, 1000):
            self.assertEqual(found(run(self.pair(b_x, 600), preset=preset(Axis.X)), Code.C3), [], b_x)

    def test_downstream_one_pixel_before_the_start_fails(self) -> None:
        # A starts at x -10; B at -11 starts before it, beside A across the axis (not a later band)
        (finding,) = found(run(self.pair(-11, 200), preset=preset(Axis.X)), Code.C3)
        self.assertEqual(
            (finding.severity, finding.data),
            (ERROR, {"upstream": 0, "downstream": 1, "axis": "x", "links": [1]}),
        )

    def test_a_group_wholly_in_a_later_band_advances(self) -> None:
        # A spans y -100 to 300. B starting before A on x passes once it lies wholly below A.
        self.assertEqual(found(run(self.pair(-11, 300), preset=preset(Axis.X)), Code.C3), [])
        self.assertEqual(len(found(run(self.pair(-11, 299), preset=preset(Axis.X)), Code.C3)), 1)
        # along y the later band is to the right: A ends at x 490
        self.assertEqual(found(run(self.pair(490, -101), preset=preset(Axis.Y)), Code.C3), [])
        self.assertEqual(len(found(run(self.pair(489, -101), preset=preset(Axis.Y)), Code.C3)), 1)

    def test_the_y_axis_compares_the_top_edges(self) -> None:
        # A starts at y -100: B at y -100 passes, at -101 fails (B overlaps A's x range: not a later band)
        self.assertEqual(found(run(self.pair(400, -100), preset=preset(Axis.Y)), Code.C3), [])
        self.assertEqual(len(found(run(self.pair(400, -101), preset=preset(Axis.Y)), Code.C3)), 1)
        # along x the same canvas is fine
        self.assertEqual(found(run(self.pair(400, -101), preset=preset(Axis.X)), Code.C3), [])

    def test_one_finding_per_group_pair(self) -> None:
        nodes = [titled(1, 0, 0, "a"), titled(3, 0, 200, "c"), titled(2, 100, 700, "b")]
        groups = [group(-10, -100, 500, 600, title="A"), group(-50, 400, 500, 400, title="B")]
        cv = canvas(nodes, [link(1, 1, 2), link(2, 3, 2, 1)], groups=groups)
        (finding,) = found(run(cv, preset=preset()), Code.C3)
        self.assertEqual(finding.data["links"], [1, 2])

    def test_a_feedback_edge_is_ignored(self) -> None:
        forward_and_back = [link(1, 1, 2), link(2, 2, 1, 1)]
        # B is right of A: the forward link is fine and the feedback link (B to A, A starts earlier) is not read
        self.assertEqual(found(run(self.pair(1000, 0, forward_and_back), preset=preset()), Code.C3), [])
        # without the forward link the same link is no feedback edge any more and fails
        self.assertEqual(len(found(run(self.pair(1000, 0, [link(2, 2, 1, 1)]), preset=preset()), Code.C3)), 1)

    def test_a_bypassed_group_is_left_out(self) -> None:
        self.assertEqual(found(run(self.pair(-11, 200, mode_b=4), preset=preset()), Code.C3), [])
        self.assertEqual(len(found(run(self.pair(-11, 200), preset=preset()), Code.C3)), 1)

    def test_one_live_node_is_enough(self) -> None:
        nodes = [titled(1, 0, 0, "a"), bypassed(titled(2, 89, 300, "b")), titled(3, 0, 450, "c")]
        groups = [group(-10, -100, 500, 400, title="A"), group(-11, 200, 500, 400, title="B")]
        cv = canvas(nodes, [link(1, 1, 2)], groups=groups)
        self.assertEqual(len(found(run(cv, preset=preset()), Code.C3)), 1)

    def test_nodes_in_one_top_level_group_and_ungrouped_nodes_are_left_out(self) -> None:
        nodes = [titled(1, 0, 0, "a"), titled(2, 100, 600, "b"), titled(3, 5000, 5000, "c")]
        groups = [group(-10, -100, 500, 1000, title="A"), group(80, 500, 300, 300, title="inner")]
        cv = canvas(nodes, [link(1, 1, 2), link(2, 3, 1, 1), link(3, 1, 3)], groups=groups)
        report = run(cv, preset=preset())
        self.assertEqual((found(report, Code.C3), report.metrics["C3"]["links"]), ([], 0))

    def test_links_into_the_cockpit_are_left_out(self) -> None:
        # B (index 1) starts before A but is the cockpit: its inbound links run backward by design
        self.assertEqual(found(run(self.pair(-11, 200), preset=preset(), cockpit={1}), Code.C3), [])
        self.assertEqual(len(found(run(self.pair(-11, 200), preset=preset(), cockpit={0}), Code.C3)), 1)

    def test_severity_by_mode_and_regroup(self) -> None:
        cv = self.pair(-11, 200)
        self.assertEqual(severities(run(cv, COMPILE, preset=preset()), Code.C3), [ERROR])
        self.assertEqual(severities(run(cv, BEAUTIFY, regroup=True, preset=preset()), Code.C3), [ERROR])
        self.assertEqual(severities(run(cv, BEAUTIFY, preset=preset()), Code.C3), [INFO])

    def test_skipped_without_a_preset(self) -> None:
        report = run(self.pair(-11, 200))
        self.assertEqual((found(report, Code.C3), skipped(report)["C3"]), ([], "no preset given"))
        self.assertNotIn("C3", skipped(run(self.pair(-11, 200), preset=preset())))


class CockpitTitles(unittest.TestCase):
    def cockpit(self, titles: list[str | None]) -> Canvas:
        nodes = [titled(k + 1, 320 * k, 0, t) if t is not None else node(k + 1, 320 * k, 0) for k, t in enumerate(titles)]
        nodes.append(node(99, 320 * len(titles) + 1000, 0))  # outside every group, drawn as its type
        return canvas(nodes, groups=[group(-10, -100, 320 * len(titles) + 20, 400)])

    def test_distinct_titles_pass(self) -> None:
        report = run(self.cockpit(["Prompt", "Seed"]), cockpit={0})
        self.assertEqual((found(report, Code.C4), report.metrics["C4"]), ([], {"groups": [0], "members": 2, "findings": 0}))

    def test_a_node_drawn_under_its_type_fails(self) -> None:
        (finding,) = found(run(self.cockpit(["Prompt", None]), cockpit={0}), Code.C4)
        self.assertEqual((finding.nodes, finding.data["reason"], finding.data["title"]), (("2",), "type", "T"))
        (finding,) = found(run(self.cockpit(["Prompt", "T"]), cockpit={0}), Code.C4)
        self.assertEqual(finding.nodes, ("2",))

    def test_a_repeated_title_fails_once_for_the_pair(self) -> None:
        (finding,) = found(run(self.cockpit(["Seed", "Prompt", "Seed"]), cockpit={0}), Code.C4)
        self.assertEqual((finding.nodes, finding.data["reason"]), (("1", "3"), "repeated"))
        self.assertEqual(len(found(run(self.cockpit(["Seed", "Seed", "Seed"]), cockpit={0}), Code.C4)), 1)

    def test_a_node_outside_the_group_is_not_read(self) -> None:
        self.assertEqual(found(run(self.cockpit(["Prompt"]), cockpit={0}), Code.C4), [])

    def test_only_innermost_members_count(self) -> None:
        nodes = [titled(1, 0, 0, "Seed"), titled(2, 320, 0, "Seed")]
        groups = [group(-10, -100, 700, 400), group(300, -100, 340, 400)]  # node 2 is in the inner group
        self.assertEqual(found(run(canvas(nodes, groups=groups), cockpit={0}), Code.C4), [])
        self.assertEqual(len(found(run(canvas(nodes, groups=[groups[0]]), cockpit={0}), Code.C4)), 1)

    def test_the_parts_of_a_split_cockpit_are_read_as_one_panel(self) -> None:
        nodes = [titled(1, 0, 0, "Seed"), titled(2, 0, 600, "Seed"), titled(3, 320, 600, "Prompt")]
        groups = [group(-10, -100, 340, 400), group(-10, 500, 660, 400)]
        self.assertEqual(found(run(canvas(nodes, groups=groups), cockpit={1}), Code.C4), [])
        report = run(canvas(nodes, groups=groups), cockpit={0, 1})
        (finding,) = found(report, Code.C4)
        self.assertEqual((finding.nodes, finding.data["groups"]), (("1", "2"), [0, 1]))
        self.assertEqual(report.metrics["C4"], {"groups": [0, 1], "members": 3, "findings": 1})

    def test_severity_by_mode(self) -> None:
        cv = self.cockpit(["Seed", "Seed"])
        self.assertEqual(severities(run(cv, COMPILE, cockpit={0}), Code.C4), [ERROR])
        self.assertEqual(severities(run(cv, BEAUTIFY, cockpit={0}), Code.C4), [INFO])

    def test_skipped_without_a_cockpit_and_a_bad_index_is_refused(self) -> None:
        report = run(self.cockpit(["Seed", "Seed"]))
        self.assertEqual((found(report, Code.C4), skipped(report)["C4"]), ([], "no cockpit group given"))
        with self.assertRaises(ValueError):
            run(self.cockpit(["Seed"]), cockpit={1})


class GuideHeight(unittest.TestCase):
    """A guide node's envelope is h + 54 high: 806 gives 860."""

    def test_860_passes_and_one_pixel_more_fails(self) -> None:
        report = run(canvas([guide(1, 0, 30, h=806)]))
        self.assertEqual((found(report, Code.C5), report.metrics["C5"]), ([], {"guides": 1, "too_tall": 0}))
        (finding,) = found(run(canvas([guide(1, 0, 30, h=807)])), Code.C5)
        self.assertEqual((finding.severity, finding.nodes, finding.data), (ERROR, ("1",), {"height": 861}))

    def test_error_in_both_modes(self) -> None:
        cv = canvas([guide(1, 0, 30, h=807)])
        self.assertEqual(severities(run(cv, COMPILE), Code.C5), [ERROR])
        self.assertEqual(severities(run(cv, BEAUTIFY), Code.C5), [ERROR])

    def test_a_node_that_is_no_guide_is_not_read(self) -> None:
        report = run(canvas([node(1, 0, 30, h=2000)]))
        self.assertEqual((found(report, Code.C5), skipped(report)["C5"]), ([], "no guide node"))
        self.assertNotIn("C5", report.metrics)


class Colours(unittest.TestCase):
    def coloured(self, colours: list[str | None]) -> Canvas:
        groups = []
        for k, colour in enumerate(colours):
            entry = group(200 * k, 0, 100, 100)
            if colour is not None:
                entry["color"] = colour
            groups.append(entry)
        return canvas([], groups=groups)

    SEVEN = ["#111111", "#222222", "#333333", "#444444", "#555555", "#666666", "#777777"]

    def test_seven_pass_and_eight_fail(self) -> None:
        self.assertEqual(found(run(self.coloured(self.SEVEN)), Code.C6), [])
        (finding,) = found(run(self.coloured(self.SEVEN + ["#888888"])), Code.C6)
        self.assertEqual((finding.severity, len(finding.data["colours"])), (ERROR, 8))

    def test_case_does_not_make_a_colour(self) -> None:
        self.assertEqual(found(run(self.coloured(self.SEVEN[:6] + ["#AAAAAA", "#aaaaaa"])), Code.C6), [])
        self.assertEqual(len(found(run(self.coloured(self.SEVEN + ["#AAAAAA", "#aaaaaa"])), Code.C6)), 1)

    def test_no_colour_counts_as_the_frontend_default(self) -> None:
        self.assertEqual(found(run(self.coloured(self.SEVEN[:6] + [None])), Code.C6), [])  # six and the default
        self.assertEqual(len(found(run(self.coloured(self.SEVEN + [None])), Code.C6)), 1)
        # the default written out, in any case, is the same colour as none
        self.assertEqual(found(run(self.coloured(self.SEVEN[:6] + [None, "#3F789E", "#3f789e"])), Code.C6), [])

    def test_compile_always_and_beautify_with_regroup(self) -> None:
        cv = self.coloured(self.SEVEN + ["#888888"])
        self.assertEqual(severities(run(cv, COMPILE), Code.C6), [ERROR])
        self.assertEqual(severities(run(cv, BEAUTIFY, regroup=True), Code.C6), [ERROR])

    def test_beautify_without_regroup_keeps_the_colours(self) -> None:
        report = run(self.coloured(self.SEVEN + ["#888888"]), BEAUTIFY)
        self.assertEqual(found(report, Code.C6), [])
        self.assertIn("C6", skipped(report))
        self.assertNotIn("C6", report.metrics)
        self.assertNotIn("C6", skipped(run(self.coloured(self.SEVEN), BEAUTIFY, regroup=True)))


class Whole(unittest.TestCase):
    def messy(self) -> Canvas:
        nodes = [titled(1, 1301, 100, "x" * 40, w=300), guide(2, 1301, 30, h=900), node(3, 0, 600), titled(4, 100, 700, "Seed"), titled(5, 100, 700, "Seed")]
        groups = [group(1001, 0, 1000, 500, title="01 Load"), group(0, 500, 500, 500, title="02 Next")]
        return canvas(nodes, [link(1, 1, 4), link(2, 4, 1, 1)], groups=groups)

    def report_json(self) -> str:
        report = run(self.messy(), BEAUTIFY, regroup=True, preset=preset(), cockpit={1})
        return json.dumps(report.to_json(), sort_keys=True)

    def test_deterministic(self) -> None:
        first, second = self.report_json(), self.report_json()
        self.assertEqual(first, second)
        for code in ("G7", "G8", "C3", "C4", "C5"):
            self.assertIn(f'"{code}"', first)

    def test_a_clean_canvas_with_everything_given_is_ok(self) -> None:
        nodes = [titled(1, 100, 100, "Load"), titled(2, 700, 100, "Seed")]
        groups = [group(0, 0, 500, 400, title="01 Load"), group(600, 0, 500, 400, title="02 Next")]
        report = run(canvas(nodes, [link(1, 1, 2)], groups=groups), preset=preset(), cockpit={1})
        self.assertEqual([f.to_json() for f in report.findings], [])
        self.assertEqual(report.metrics["skipped"], [{"code": "G3", "reason": "no membership or before canvas given"}, {"code": "C5", "reason": "no guide node"}])


if __name__ == "__main__":
    unittest.main()
