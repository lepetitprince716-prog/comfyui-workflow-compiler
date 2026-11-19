"""The link model (cwc.canvas.links, slot anchors in cwc.canvas.envelope) against the recorded runs.

The oracle recorded, per link, the anchors the frontend used and whether 21 points of the
predicted spline lie on the stroked path (``isPointInStroke``, 3 px test stroke). Here the
model's own curve is compared with the recorded curve: a link agrees when every one of its
21 sampled points is within 1.5 px (half the test stroke) of the recorded curve's point.
"""

from __future__ import annotations

import json
import math
import random
import unittest
from dataclasses import replace

from tests.support import FIXTURES

from cwc.canvas.envelope import COLLAPSED_PAD, VUE_COLLAPSED_PAD, Renderer, box_facts, slot_anchors
from cwc.canvas.geometry import Rect
from cwc.canvas.links import Spline, canvas_splines, crosses, spline
from cwc.canvas.model import Canvas
from cwc.nodedefs.registry import NodeDefs

LAYOUT = FIXTURES / "layout"
DEFS = NodeDefs.load()
TOLERANCE = 1.5


def _load(name: str) -> dict:
    return json.loads((LAYOUT / f"{name}.json").read_text(encoding="utf-8"))


def _facts(canvas: Canvas, recorded: dict, renderer: Renderer, measured_titles: bool) -> dict:
    nodes = canvas.node_map()
    out = {}
    for entry in recorded["nodes"]:
        node = nodes[entry["id"]]
        facts = box_facts(node, DEFS.get(node.type))
        if measured_titles and node.collapsed:
            pad = COLLAPSED_PAD if renderer is Renderer.CLASSIC else VUE_COLLAPSED_PAD
            facts = replace(facts, title_width=entry["bounding"][2] - pad)
        out[node.key] = facts
    return out


def agreement(run: str, measured_titles: bool) -> tuple[int, int, list]:
    renderer = Renderer(run)
    agree, total, misses = 0, 0, []
    for name in ("boxes", "links"):
        canvas = Canvas(_load(f"{name}.canvas"))
        recorded = _load(f"{name}.{run}")
        curves = canvas_splines(canvas, _facts(canvas, recorded, renderer, measured_titles), renderer)
        for link in recorded["links"]:
            want = spline(tuple(link["start"]), tuple(link["end"])).sample()
            got = curves[link["id"]].sample()
            worst = max(math.dist(a, b) for a, b in zip(got, want))
            total += 1
            if worst <= TOLERANCE:
                agree += 1
            else:
                misses.append((name, link["id"], round(worst, 2)))
    return agree, total, misses


class Agreement(unittest.TestCase):
    def test_classic_every_link(self) -> None:
        for measured in (False, True):
            agree, total, misses = agreement("classic", measured)
            self.assertEqual((agree, total), (13, 13), misses)

    def test_vue_all_but_the_collapsed_offset(self) -> None:
        # The one miss: collapsed VAE Decode draws its second input at x + 13.4 under Vue
        # (docs/m2-frontend-facts.md section 5, "measured oddity"); the model puts it at x.
        for measured in (False, True):
            agree, total, misses = agreement("vue", measured)
            self.assertEqual((agree, total), (12, 13))
            self.assertEqual([m[:2] for m in misses], [("links", 3)])

    def test_recorded_control_points(self) -> None:
        for name in ("boxes", "links"):
            for run in ("classic", "vue"):
                for link in _load(f"{name}.{run}")["links"]:
                    curve = spline(tuple(link["start"]), tuple(link["end"]))
                    for got, want in ((curve.c1, link["c1"]), (curve.c2, link["c2"])):
                        self.assertAlmostEqual(got[0], want[0], places=6)
                        self.assertAlmostEqual(got[1], want[1], places=6)


class WidgetAnchors(unittest.TestCase):
    def _check(self, run: str) -> tuple[int, list]:
        renderer = Renderer(run)
        exact, misses = 0, []
        for name in ("boxes", "links"):
            canvas = Canvas(_load(f"{name}.canvas"))
            nodes = canvas.node_map()
            for entry in _load(f"{name}.{run}")["nodes"]:
                node = nodes[entry["id"]]
                if node.collapsed:
                    continue
                facts = box_facts(node, DEFS.get(node.type))
                rows = facts.classic_widgets if renderer is Renderer.CLASSIC else facts.vue_widgets
                for slot in entry["inputs"]:
                    if slot["widget"] is None:
                        continue
                    got = node.pos[1] + rows[slot["widget"]]
                    if abs(got - slot["anchor"][1]) < 1e-6:
                        exact += 1
                    else:
                        misses.append((entry["type"], slot["widget"], round(slot["anchor"][1] - got, 2)))
        return exact, misses

    def test_classic_widget_rows_exact(self) -> None:
        exact, misses = self._check("classic")
        self.assertEqual(misses, [])
        self.assertEqual(exact, 14)

    def test_vue_widget_rows_exact(self) -> None:
        # Text boxes and single-line text rows: tests/test_envelope_vue_widgets.py.
        exact, misses = self._check("vue")
        self.assertEqual(misses, [])
        self.assertEqual(exact, 14)

    def test_plain_rows_match_the_recorded_anchors(self) -> None:
        for name in ("boxes", "links"):
            canvas = Canvas(_load(f"{name}.canvas"))
            nodes = canvas.node_map()
            for run in ("classic", "vue"):
                renderer = Renderer(run)
                recorded = _load(f"{name}.{run}")
                facts = _facts(canvas, recorded, renderer, measured_titles=True)
                for entry in recorded["nodes"]:
                    node = nodes[entry["id"]]
                    inputs, outputs = slot_anchors(node, facts[node.key], renderer)
                    by_name = {s["name"]: s["anchor"] for s in entry["inputs"]}
                    for slot, got in zip(node.inputs, inputs):
                        if (name, run, entry["id"], slot["name"]) == ("links", "vue", "4", "vae"):
                            continue  # the collapsed offset above
                        self.assertAlmostEqual(got[0], by_name[slot["name"]][0], places=3)
                        self.assertAlmostEqual(got[1], by_name[slot["name"]][1], delta=0.02)
                    for slot, got in zip(entry["outputs"], outputs):
                        self.assertAlmostEqual(got[0], slot["anchor"][0], delta=0.3)  # Vue collapsed: DOM 285 vs box 284.72
                        self.assertAlmostEqual(got[1], slot["anchor"][1], delta=0.02)


class Crosses(unittest.TestCase):
    BOX = Rect(100, 100, 100, 50)

    def test_through_the_interior(self) -> None:
        self.assertTrue(crosses([(0, 120), (300, 130)], self.BOX))

    def test_inside_only(self) -> None:
        self.assertTrue(crosses([(120, 110), (130, 120)], self.BOX))

    def test_along_an_edge_or_touching_a_corner_does_not_count(self) -> None:
        self.assertFalse(crosses([(0, 100), (300, 100)], self.BOX))
        self.assertFalse(crosses([(0, 50), (100, 100), (0, 150)], self.BOX))

    def test_miss(self) -> None:
        self.assertFalse(crosses([(0, 0), (300, 90)], self.BOX))

    def test_recorded_backward_link_crosses_the_box_between(self) -> None:
        """links.canvas link 1 runs right to left from the loader at x 1200 to node 2 at x 100;
        its spline bulges past both ends, so it crosses a box placed on its way."""
        link = _load("links.classic")["links"][0]
        path = spline(tuple(link["start"]), tuple(link["end"])).sample()
        self.assertTrue(crosses(path, Rect(700, 100, 100, 40)))
        self.assertFalse(crosses(path, Rect(700, 300, 100, 40)))


class Sample(unittest.TestCase):
    def test_sample_is_point_bit_for_bit(self) -> None:
        rng = random.Random(11)
        for _ in range(500):
            curve = Spline(*((rng.uniform(-5e4, 5e4), rng.uniform(-5e4, 5e4)) for _ in range(4)))
            for segments in (1, 3, 20, 37):
                self.assertEqual(curve.sample(segments), [curve.point(i / segments) for i in range(segments + 1)])


if __name__ == "__main__":
    unittest.main()
