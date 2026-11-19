"""The cockpit-split preset: the stages along x as in swimlanes, the cockpit in two tiers, and a
spine corridor ``BAND_GAP`` wide between the cockpit column and the stages, as high as the section.
A wrap never cuts at the spine, so the first section holds both."""

import unittest

from tests import support  # noqa: F401
from tests.layout_cases import controlnet, laid_out, long_chain, routed, two_stage
from tests.test_layout_tiers import with_advanced

from cwc.annotate.types import Zone
from cwc.canvas.geometry import Rect
from cwc.layout.presets import get_preset
from cwc.layout.section import BAND_GAP
from cwc.layout.types import CorridorKind

COCKPIT_SPLIT = get_preset("cockpit-split")


def spines(result):
    return [c for c in result.corridors if c.kind is CorridorKind.SPINE]


def overlaps(rect: Rect, bounding) -> bool:
    x, y, w, h = bounding
    return rect.x < x + w and x < rect.right and rect.y < y + h and y < rect.bottom


class SpineTest(unittest.TestCase):
    def test_the_spine_stands_between_the_cockpit_and_the_stages(self):
        canvas, plan, result, report, _ = routed(two_stage(), COCKPIT_SPLIT)[:5]
        (spine,) = spines(result)
        self.assertEqual(spine.id, "spine:0")
        (cockpit,) = [c for c in result.columns if c.slot == 0]
        stages = sorted((c for c in result.columns if c.slot == 1), key=lambda c: c.rect.x)
        (section,) = result.bands
        self.assertEqual(spine.rect, Rect(cockpit.rect.right, section.y, BAND_GAP, section.h))
        self.assertEqual(stages[0].rect.x, spine.rect.right)
        self.assertEqual(spine.tracks, BAND_GAP // 20)
        for group in canvas.raw["groups"]:
            self.assertFalse(overlaps(spine.rect, group["bounding"]), group["title"])
        self.assertEqual([f for f in report.findings if f.severity.value == "error"], [])

    def test_the_stages_run_along_x(self):
        result = laid_out(two_stage(), COCKPIT_SPLIT)[2]
        stages = [c for c in result.columns if c.slot == 1]
        self.assertGreater(len(stages), 2)
        self.assertEqual([c.stage for c in stages], sorted(c.stage for c in stages))
        self.assertEqual({c.rect.y for c in stages}, {0.0})
        for a, b in zip(stages, stages[1:]):
            self.assertLess(a.rect.right, b.rect.x)

    def test_a_wrap_never_cuts_at_the_spine(self):
        # Without the rule the only cut controlnet accepts falls right after the cockpit, and in
        # long_chain the first cut does: the cockpit then stands alone in the first section and
        # no section holds both sides of the spine.
        for name, doc, wraps in (("controlnet", controlnet(), False), ("long_chain", long_chain(12), True)):
            with self.subTest(case=name):
                result = laid_out(doc, COCKPIT_SPLIT)[2]
                self.assertEqual(len(result.bands) > 1, wraps)
                first = [c.slot for c in result.columns if c.band == 0]
                self.assertEqual(first[0], 0)
                self.assertIn(1, first)
                self.assertEqual([c.id for c in spines(result)], ["spine:0"])

    def test_the_spine_reaches_past_the_lower_tier(self):
        canvas, plan, result, report, _ = routed(with_advanced(), COCKPIT_SPLIT)[:5]
        boxes = [canvas.raw["groups"][i]["bounding"] for i in result.cockpit]
        self.assertEqual(len(boxes), 2)
        upper, lower = boxes
        self.assertEqual(lower[0], upper[0])
        self.assertGreater(lower[1], upper[1] + upper[3])
        (spine,) = spines(result)
        self.assertEqual(spine.rect.x, max(b[0] + b[2] for b in boxes))
        self.assertGreaterEqual(spine.rect.bottom, lower[1] + lower[3])
        self.assertEqual([f for f in report.findings if f.severity.value == "error"], [])

    def test_other_presets_have_no_spine(self):
        for name in ("bento", "swimlanes", "stepped"):
            with self.subTest(preset=name):
                self.assertEqual(spines(laid_out(two_stage(), get_preset(name))[2]), [])

    def test_the_cockpit_is_one_box_without_advanced_values(self):
        _, plan, result, _, _ = laid_out(two_stage(), COCKPIT_SPLIT)
        (cockpit,) = [g.key for g in plan.groups if g.zone is Zone.COCKPIT]
        self.assertEqual(result.cockpit, (result.group_index[cockpit],))


if __name__ == "__main__":
    unittest.main()
