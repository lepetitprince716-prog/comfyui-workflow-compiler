"""The frontend geometry rules in docs/m2-frontend-facts.md, checked against recorded oracle runs.

The fixtures in ``tests/fixtures/layout/`` were written by ``tools/oracle/geometry.py record``
from frontend 1.59.1. These tests restate each rule as plain arithmetic and check that the
frontend's own numbers agree, so layout code can rely on the rules offline.
"""

from __future__ import annotations

import json
import math
import unittest
from typing import Any

from tests.support import FIXTURES

LAYOUT = FIXTURES / "layout"
TITLE = 30
SLOT = 20


def _run(canvas: str, run: str) -> dict[str, Any]:
    return json.loads((LAYOUT / f"{canvas}.{run}.json").read_text(encoding="utf-8"))


def _canvas(canvas: str) -> dict[str, Any]:
    return json.loads((LAYOUT / f"{canvas}.canvas.json").read_text(encoding="utf-8"))


def _centre_in(rect: list[float], box: list[float]) -> bool:
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return rect[0] <= cx < rect[0] + rect[2] and rect[1] <= cy < rect[1] + rect[3]


def _contains(a: list[float], b: list[float]) -> bool:
    if a == b:
        return False
    return a[0] <= b[0] and a[1] <= b[1] and a[0] + a[2] >= b[0] + b[2] and a[1] + a[3] >= b[1] + b[3]


class GroupMembership(unittest.TestCase):
    def test_members_are_nodes_whose_box_centre_is_inside(self) -> None:
        for run in ("classic", "vue", "classic_snap"):
            doc = _run("boxes", run)
            for group in doc["groups"]:
                expected = sorted(n["id"] for n in doc["nodes"] if _centre_in(group["bounding"], n["bounding"]))
                self.assertEqual(group["member_nodes"], expected, (run, group["title"]))

    def test_child_groups_are_wholly_contained(self) -> None:
        doc = _run("boxes", "classic")
        for group in doc["groups"]:
            expected = sorted(g["id"] for g in doc["groups"] if g is not group and _contains(group["bounding"], g["bounding"]))
            self.assertEqual(group["child_groups"], expected, group["title"])

    def test_edge_cases_recorded(self) -> None:
        groups = {g["title"]: g for g in _run("boxes", "classic")["groups"]}
        self.assertIn("8", groups["Outer"]["member_nodes"])  # box sticks out below, centre inside
        self.assertNotIn("2", groups["Outer"]["member_nodes"])  # collapsed: only the title band counts
        self.assertNotIn("7", groups["Overlap"]["member_nodes"])  # centre on the right edge: outside
        self.assertIn("1", groups["Inner"]["member_nodes"])  # a node belongs to every containing group
        self.assertIn("1", groups["Outer"]["member_nodes"])


class GroupBounding(unittest.TestCase):
    def test_load_keeps_bounding_without_snap(self) -> None:
        saved = {g["id"]: g["bounding"] for g in _canvas("boxes")["groups"]}
        for run in ("classic", "vue"):
            for group in _run("boxes", run)["serialized"]["groups"]:
                self.assertEqual(group["bounding"], saved[group["id"]], run)  # the 100 x 50 group too

    def test_snap_rounds_group_position_only(self) -> None:
        saved = {g["id"]: g["bounding"] for g in _canvas("boxes")["groups"]}
        for group in _run("boxes", "classic_snap")["serialized"]["groups"]:
            x, y, w, h = saved[group["id"]]
            self.assertEqual(group["bounding"], [10 * round(x / 10), 10 * round(y / 10), w, h])


class NodeBoxes(unittest.TestCase):
    def test_classic_box(self) -> None:
        for canvas in ("boxes", "links"):
            for node in _run(canvas, "classic")["nodes"]:
                x, y = node["pos"]
                if node["collapsed"]:
                    width = node["collapsed_width"]
                    self.assertLessEqual(width, node["size"][0])
                    self.assertEqual(node["bounding"], [x, y - TITLE, width, TITLE])
                else:
                    self.assertEqual(node["bounding"], [x, y - TITLE, node["size"][0], node["size"][1] + TITLE])

    def test_vue_box_is_the_dom_box(self) -> None:
        for canvas in ("boxes", "links"):
            for node in _run(canvas, "vue")["nodes"]:
                box, dom = node["bounding"], node["vue_box"]
                for got, want in zip(box[:3], dom[:3]):
                    self.assertAlmostEqual(got, want, places=3)
                self.assertGreaterEqual(box[2], 225 if not node["collapsed"] else 0)
                if node["collapsed"]:
                    self.assertEqual(box[3], TITLE)  # the DOM header is 28; the hit box keeps 30
                else:
                    self.assertAlmostEqual(box[3], dom[3], places=3)

    def test_snap_on_load(self) -> None:
        saved = {str(n["id"]): n for n in _canvas("boxes")["nodes"]}
        for node in _run("boxes", "classic_snap")["serialized"]["nodes"]:
            before = saved[str(node["id"])]
            self.assertEqual(node["pos"], [10 * round(v / 10) for v in before["pos"]])
            self.assertEqual(node["size"], [10 * math.ceil(v / 10) for v in before["size"]])


class SlotAnchors(unittest.TestCase):
    def test_classic_rows(self) -> None:
        for canvas in ("boxes", "links"):
            for node in _run(canvas, "classic")["nodes"]:
                x, y = node["pos"]
                if node["collapsed"]:
                    for slot in node["inputs"]:
                        self.assertEqual(slot["anchor"], [x, y - TITLE / 2])
                    for slot in node["outputs"]:
                        self.assertEqual(slot["anchor"], [x + node["collapsed_width"], y - TITLE / 2])
                    continue
                rows = [s for s in node["inputs"] if s["widget"] is None]
                for row, slot in enumerate(rows):
                    self.assertEqual(slot["anchor"], [x + SLOT / 2, y + (row + 0.7) * SLOT], node["title"])
                for row, slot in enumerate(node["outputs"]):
                    self.assertEqual(slot["anchor"], [x + node["size"][0] + 1 - SLOT / 2, y + (row + 0.7) * SLOT])

    def test_vue_rows_from_the_dom(self) -> None:
        for node in _run("boxes", "vue")["nodes"]:
            if node["collapsed"]:
                continue
            x, y = node["pos"]
            rows = [s for s in node["inputs"] if s["widget"] is None]
            for row, slot in enumerate(rows):
                self.assertEqual(slot["anchor"], [x, y + 12 + SLOT * row], node["title"])
            for row, slot in enumerate(node["outputs"]):
                self.assertEqual(slot["anchor"], [x + node["bounding"][2], y + 12 + SLOT * row], node["title"])


class Links(unittest.TestCase):
    def test_default_mode_is_spline(self) -> None:
        for canvas in ("boxes", "links"):
            for run in ("classic", "vue"):
                self.assertEqual(_run(canvas, run)["settings"]["links_render_mode"], 2)

    def test_control_points_and_drawn_path(self) -> None:
        for canvas in ("boxes", "links"):
            for run in ("classic", "vue"):
                for link in _run(canvas, run)["links"]:
                    p0, p3 = link["start"], link["end"]
                    d = 0.25 * math.dist(p0, p3)
                    self.assertAlmostEqual(link["c1"][0], p0[0] + d)
                    self.assertAlmostEqual(link["c2"][0], p3[0] - d)
                    # 21 samples of the predicted curve on the stroked path (3 px test stroke)
                    self.assertGreaterEqual(link["on_path"], link["samples"] - 1, (canvas, run, link["id"]))
                    if run == "classic":
                        self.assertEqual(link["on_path"], link["samples"], (canvas, link["id"]))


if __name__ == "__main__":
    unittest.main()
