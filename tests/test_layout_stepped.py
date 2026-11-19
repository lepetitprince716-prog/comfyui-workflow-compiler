"""The stepped preset: "Step N" titles, the official blue, the guide's width, and the main-row
items cut into sub-columns where a lower-band item bridges them (``section._split``)."""

import re
import unittest
from collections import Counter

from tests import support  # noqa: F401
from tests.layout_cases import controlnet, laid_out, optional_upscale, two_stage
from tests.layout_generator import random_graph

from cwc.canvas.geometry import Rect
from cwc.layout.items import Item, Seq
from cwc.layout.presets import get_preset
from cwc.layout.section import _split
from cwc.layout.types import Axis

STEPPED = get_preset("stepped")
OFFICIAL_BLUE = "#3f789e"
TITLE = re.compile(r"^Step (\d+)([a-z]?) \S")


def errors(report, code):
    return [f.message for f in report.findings if f.severity.value == "error" and f.code.value == code]


def graphs():
    return {
        "two_stage": two_stage(),
        "controlnet": controlnet(),
        "controlnet_upscale": optional_upscale(controlnet()),
        "seed_5": random_graph(5),
        "seed_33": random_graph(33),
    }


class SteppedTest(unittest.TestCase):
    def test_step_numbers_run_from_one_without_a_gap(self):
        for name, doc in graphs().items():
            with self.subTest(graph=name):
                canvas, plan, result, report, _ = laid_out(doc, STEPPED)
                titles = [g["title"] for g in canvas.raw["groups"]]
                matches = [TITLE.match(t) for t in titles]
                self.assertTrue(all(matches), titles)
                numbers = sorted({int(m.group(1)) for m in matches if m})
                self.assertEqual(numbers, list(range(1, len(numbers) + 1)), titles)
                # a group cut into segments keeps one number and letters a, b, ... in order
                letters: dict[int, list[str]] = {}
                for m in matches:
                    if m and m.group(2):
                        letters.setdefault(int(m.group(1)), []).append(m.group(2))
                for number, seen in letters.items():
                    self.assertEqual(sorted(seen), [chr(ord("a") + i) for i in range(len(seen))], (number, titles))

    def test_every_group_is_the_official_blue(self):
        for name, doc in graphs().items():
            with self.subTest(graph=name):
                canvas = laid_out(doc, STEPPED)[0]
                self.assertEqual({g["color"] for g in canvas.raw["groups"]}, {OFFICIAL_BLUE})

    def test_the_guide_reserves_480_and_the_main_row_starts_right_of_it(self):
        for name, doc in graphs().items():
            with self.subTest(graph=name):
                canvas, plan, result, report, _ = laid_out(doc, STEPPED)
                self.assertEqual(result.guide, Rect(0, 0, 480, 900))
                # the main row starts GUIDE_GAP right of the guide; a lower item docked left of
                # that start moves the row further right, never left
                self.assertGreaterEqual(result.bands[0].x, 520)
                left = min(g["bounding"][0] for g in canvas.raw["groups"])
                self.assertGreaterEqual(left, 520)
                if name == "two_stage":
                    self.assertEqual(result.bands[0].x, 520)

    def test_a_bridged_item_is_cut_into_its_sub_columns(self):
        # seed 5: a lower-band item is fed by the first sub-column of the first main-row item and
        # feeds its second; cut, the row opens the bridge's gap between them and G5 stays clean
        canvas, plan, result, report, _ = laid_out(random_graph(5), STEPPED)
        main = Counter((c.slot, c.stage) for c in result.columns if c.band == 0)
        self.assertEqual(main[(0, None)], 2)
        self.assertEqual(errors(report, "G5"), [])


def _box() -> Seq:
    return Seq(Axis.Y, [])


def _row_item(columns: list[list[str]]) -> Item:
    groups = [key for column in columns for key in column]
    return Item(0, 0, 0, None, groups, Seq(Axis.X, [_box() for _ in columns]), columns=columns)


def _lower(key: str) -> Item:
    return Item(1, 0, 0, None, [key], Seq(Axis.X, [_box()]))


class SplitTest(unittest.TestCase):
    def test_bridged_sub_columns_become_items(self):
        item = _row_item([["a"], ["b", "c"]])
        bridge = _lower("x")
        row = _split([item], [bridge], {("a", "x"): 1, ("x", "c"): 1}, frozenset())
        self.assertEqual([piece.groups for piece in row], [["a"], ["b", "c"]])
        self.assertEqual({(p.band, p.slot, p.instance, p.stage) for p in row}, {(0, 0, 0, None)})
        self.assertEqual([p.content for p in row], item.content.boxes)

    def test_a_bridge_through_lower_items_also_cuts(self):
        item = _row_item([["a"], ["c"]])
        first, second = _lower("x"), _lower("y")
        links = {("a", "x"): 1, ("x", "y"): 1, ("y", "c"): 1}
        self.assertEqual([p.groups for p in _split([item], [first, second], links, frozenset())], [["a"], ["c"]])

    def test_without_a_bridge_the_item_stays_whole(self):
        item = _row_item([["a"], ["c"]])
        lower = _lower("x")
        for links in ({("a", "x"): 1}, {("x", "c"): 1}, {("c", "x"): 1, ("x", "a"): 1}):
            with self.subTest(links=links):
                row = _split([item], [lower], links, frozenset())
                self.assertEqual(len(row), 1)
                self.assertIs(row[0], item)

    def test_a_row_without_sub_columns_is_returned_as_is(self):
        row = [_lower("a"), _lower("c")]
        self.assertIs(_split(row, [_lower("x")], {("a", "x"): 1, ("x", "c"): 1}, frozenset()), row)


if __name__ == "__main__":
    unittest.main()
