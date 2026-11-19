"""The swimlanes preset: the prompt zone in the stage lanes and a skybridge above each section."""

import unittest

from tests import support  # noqa: F401
from tests.layout_cases import controlnet, laid_out, two_stage
from tests.layout_generator import random_graph

from cwc.canvas.geometry import Rect
from cwc.layout.presets import get_preset
from cwc.layout.section import BAND_GAP
from cwc.layout.types import CorridorKind

SWIMLANES = get_preset("swimlanes")


def errors(report, code):
    return [f.message for f in report.findings if f.severity.value == "error" and f.code.value == code]


class SwimlanesTest(unittest.TestCase):
    def test_prompts_sit_in_the_lanes_and_the_cockpit_column_holds_the_cockpit_only(self):
        canvas, plan, result, report, _ = laid_out(controlnet(), SWIMLANES)
        zone = {g.key: g.zone.value for g in plan.groups}
        first = [c for c in result.columns if c.slot == 0]
        self.assertEqual([zone[k] for c in first for k in c.groups], ["cockpit"])
        lanes = {zone[k] for c in result.columns if c.slot == 1 for k in c.groups}
        self.assertIn("prompt", lanes)

    def test_one_skybridge_above_each_section(self):
        # seed 3 wraps into three sections under swimlanes; two_stage stays in one
        for doc in (two_stage(), controlnet(), random_graph(3)):
            canvas, plan, result, report, _ = laid_out(doc, SWIMLANES)
            sky = [c for c in result.corridors if c.kind is CorridorKind.SKYBRIDGE]
            self.assertEqual([c.id for c in sky], [f"skybridge:{i}" for i in range(len(result.bands))])
            self.assertEqual([c.id for c in result.corridors], [c.id for c in sky])  # no bus in swimlanes
            for corridor, band in zip(sky, result.bands):
                self.assertEqual(corridor.rect, Rect(band.x, band.y - BAND_GAP, band.w, BAND_GAP))
                self.assertEqual(corridor.tracks, BAND_GAP // 20)
                for group in canvas.raw["groups"]:
                    x, y, w, h = group["bounding"]
                    self.assertFalse(corridor.rect.x < x + w and x < corridor.rect.right and corridor.rect.y < y + h and y < corridor.rect.bottom)
            self.assertEqual(result.bands[0].y, BAND_GAP)
            for above, below in zip(result.bands, result.bands[1:]):
                self.assertEqual(below.y - above.bottom, 2 * BAND_GAP)
            self.assertEqual(errors(report, "G8"), [])

    def test_bento_has_no_skybridge(self):
        result = laid_out(controlnet())[2]
        self.assertEqual({c.kind for c in result.corridors}, {CorridorKind.BUS})
        self.assertEqual(result.bands[0].y, 0)


if __name__ == "__main__":
    unittest.main()
