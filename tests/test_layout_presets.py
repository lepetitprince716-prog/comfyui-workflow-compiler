import copy
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.annotate.types import CockpitSlot, Zone
from cwc.layout.presets import DEFAULT, get_preset, load_presets, parse_presets
from cwc.layout.types import Axis, CorridorKind, Numbering
from cwc.resources import read_json

Z = Zone


def band0(name):
    return [slot.zones for slot in get_preset(name).bands[0].slots]


class BundledPresetsTest(unittest.TestCase):
    def test_all_seven_load(self):
        self.assertEqual(set(load_presets()), {"bento", "swimlanes", "cockpit-split", "tower-preview", "stepped", "gallery", "square"})
        self.assertEqual(DEFAULT, "bento")

    def test_every_zone_has_one_home(self):
        for name, preset in load_presets().items():
            for zone in Zone:
                with self.subTest(preset=name, zone=zone):
                    preset.home(zone)

    def test_bento(self):
        self.assertEqual(
            band0("bento"),
            [(Z.COCKPIT,), (Z.MODEL, Z.PROMPT), (Z.SAMPLING, Z.STAGE), (Z.POSTPROCESS,), (Z.OUTPUT,)],
        )
        preset = get_preset("bento")
        self.assertEqual(preset.home(Z.OPTIONAL), (1, 0))
        self.assertEqual([c.kind for c in preset.corridors], [CorridorKind.BUS])

    def test_swimlanes(self):
        self.assertEqual(
            band0("swimlanes"),
            [(Z.COCKPIT,), (Z.MODEL, Z.PROMPT, Z.CONTROL, Z.SAMPLING, Z.STAGE, Z.POSTPROCESS, Z.OPTIONAL, Z.OUTPUT)],
        )
        preset = get_preset("swimlanes")
        lanes = preset.bands[0].slots[1]
        self.assertEqual((lanes.repeat, lanes.attach), (Axis.X, (Z.CONTROL,)))
        self.assertEqual([c.kind for c in preset.corridors], [CorridorKind.SKYBRIDGE])

    def test_cockpit_split(self):
        self.assertEqual(
            band0("cockpit-split"),
            [(Z.COCKPIT,), (Z.MODEL, Z.PROMPT, Z.CONTROL, Z.SAMPLING, Z.STAGE, Z.POSTPROCESS, Z.OPTIONAL, Z.OUTPUT)],
        )
        preset = get_preset("cockpit-split")
        # The stages run along x: stacked along y, every link between them would run backward.
        stages = preset.bands[0].slots[1]
        self.assertEqual((preset.axis, stages.repeat, stages.stack, stages.attach), (Axis.X, Axis.X, Axis.Y, (Z.CONTROL,)))
        self.assertEqual([(c.kind, c.after_slot) for c in preset.corridors], [(CorridorKind.SPINE, 0)])
        self.assertNotIn(CockpitSlot.OTHER, preset.cockpit_split.core)

    def test_tower_preview(self):
        # the bento skeleton plus a preview window in the output slot
        tower, bento = get_preset("tower-preview"), get_preset("bento")
        self.assertEqual((tower.bands, tower.corridors), (bento.bands, bento.corridors))
        self.assertEqual((tower.preview.zone, tower.preview.share, tower.preview.min_height), (Z.OUTPUT, 0.55, 680))
        self.assertIsNone(tower.tray)
        self.assertFalse(any(slot.under_guide for band in tower.bands for slot in band.slots))
        self.assertEqual((tower.numbering, tower.palette, tower.guide_width), (Numbering.NUMBER, "semantic", 460))

    def test_stepped(self):
        self.assertEqual(
            band0("stepped"),
            [(Z.MODEL, Z.COCKPIT), (Z.PROMPT,), (Z.SAMPLING, Z.STAGE), (Z.POSTPROCESS,), (Z.OUTPUT,)],
        )
        preset = get_preset("stepped")
        self.assertEqual((preset.home(Z.CONTROL), preset.home(Z.OPTIONAL)), ((1, 0), (1, 0)))
        self.assertEqual((preset.numbering, preset.palette, preset.guide_width), (Numbering.STEP, "official", 480))

    def test_gallery(self):
        preset = get_preset("gallery")
        self.assertEqual(
            band0("gallery"),
            [(Z.COCKPIT,), (Z.MODEL, Z.PROMPT), (Z.SAMPLING, Z.STAGE), (Z.POSTPROCESS,), (Z.OUTPUT,)],
        )
        self.assertEqual([(c.kind, c.after_slot) for c in preset.corridors], [(CorridorKind.BUS, None), (CorridorKind.SPINE, 0)])
        self.assertIsNotNone(preset.preview)
        self.assertTrue(preset.preview.dock_steps)
        self.assertIn(Z.OPTIONAL, preset.preview.extra_zones)
        self.assertIsNotNone(preset.cockpit_split)
        self.assertEqual((preset.cockpit_split.axis, preset.cockpit_split.min_nodes), (Axis.X, 5))

    def test_square(self):
        preset = get_preset("square")
        self.assertTrue(preset.wrap_rows)
        self.assertLessEqual(preset.wrap_ratio, 2.0)
        self.assertIsNotNone(preset.preview)
        self.assertTrue(preset.preview.dock_steps)
        self.assertEqual([(c.kind, c.after_slot) for c in preset.corridors], [(CorridorKind.BUS, None), (CorridorKind.SPINE, 0)])


class RejectedPresetsTest(unittest.TestCase):
    def doc(self):
        return copy.deepcopy(read_json("presets.json"))

    def assertRejected(self, doc, fragment):
        with self.assertRaises(ValueError) as caught:
            parse_presets(doc)
        self.assertIn(fragment, str(caught.exception))

    def bento(self, doc):
        return doc["presets"]["bento"]

    def tower(self, doc):
        return doc["presets"]["tower-preview"]

    def test_shape(self):
        doc = self.doc()
        doc["extra"] = 1
        self.assertRejected(doc, "unknown keys ['extra']")
        doc = self.doc()
        doc["format"] = 2
        self.assertRejected(doc, "unsupported format")
        doc = self.doc()
        doc["presets"] = {}
        self.assertRejected(doc, "non-empty object")
        doc = self.doc()
        del self.bento(doc)["axis"]
        self.assertRejected(doc, "missing keys ['axis']")
        doc = self.doc()
        self.bento(doc)["bands"][0]["slots"][0]["width"] = 3
        self.assertRejected(doc, "unknown keys ['width']")
        doc = self.doc()
        self.bento(doc)["axis"] = "z"
        self.assertRejected(doc, "is not one of")
        doc = self.doc()
        self.bento(doc)["bands"] = []
        self.assertRejected(doc, "no bands")
        doc = self.doc()
        self.bento(doc)["bands"][1]["slots"] = []
        self.assertRejected(doc, "no slots")
        doc = self.doc()
        self.bento(doc)["bands"][0]["slots"][0]["zones"] = []
        self.assertRejected(doc, "no zones")

    def test_every_zone_in_exactly_one_slot(self):
        doc = self.doc()
        self.bento(doc)["bands"][1]["slots"][0]["zones"] = ["control"]
        self.assertRejected(doc, "missing: ['optional']")
        doc = self.doc()
        self.bento(doc)["bands"][1]["slots"][0]["zones"].append("cockpit")
        self.assertRejected(doc, "twice: ['cockpit']")

    def test_bus_needs_two_bands(self):
        doc = self.doc()
        bento = self.bento(doc)
        bento["bands"][0]["slots"].append(bento["bands"].pop(1)["slots"][0])
        self.assertRejected(doc, "a bus needs two bands")

    def test_spine_after_slot(self):
        for after in (None, -1, 1, True):
            doc = self.doc()
            spine = doc["presets"]["cockpit-split"]["corridors"][0]
            spine.pop("after_slot")
            if after is not None:
                spine["after_slot"] = after
            with self.subTest(after=after):
                self.assertRejected(doc, "a spine needs after_slot")
        doc = self.doc()
        self.bento(doc)["corridors"][0]["after_slot"] = 0
        self.assertRejected(doc, "only for a spine")

    def test_guide_width(self):
        for width in (0, 470, 460.0, True):
            doc = self.doc()
            self.bento(doc)["guide_width"] = width
            with self.subTest(width=width):
                self.assertRejected(doc, "guide_width")

    def test_palette(self):
        doc = self.doc()
        self.bento(doc)["palette"] = "neon"
        self.assertRejected(doc, "palette must be one of")

    def test_ratios(self):
        for value in (1, 0.5, float("inf"), "2"):
            doc = self.doc()
            self.bento(doc)["wrap_ratio"] = value
            with self.subTest(value=value):
                self.assertRejected(doc, "wrap_ratio: must be a number above 1")

    def test_attach(self):
        doc = self.doc()
        self.bento(doc)["bands"][0]["slots"][0]["attach"] = ["cockpit"]
        self.assertRejected(doc, "attach needs repeat")
        for attach in (["cockpit"], ["control", "control"], ["model", "prompt", "control", "sampling", "stage", "postprocess", "optional", "output"]):
            doc = self.doc()
            doc["presets"]["swimlanes"]["bands"][0]["slots"][1]["attach"] = attach
            with self.subTest(attach=attach):
                self.assertRejected(doc, "attach must name zones of this slot")

    def test_under_guide(self):
        doc = self.doc()
        self.bento(doc)["bands"][0]["slots"][1]["under_guide"] = True
        self.assertRejected(doc, "only band 0 slot 0 may be under_guide")
        doc = self.doc()
        self.bento(doc)["bands"][1]["slots"][0]["under_guide"] = True
        self.assertRejected(doc, "only band 0 slot 0 may be under_guide")
        doc = self.doc()
        self.bento(doc)["bands"][0]["slots"][0]["under_guide"] = 1
        self.assertRejected(doc, "under_guide must be true or false")

    def test_cockpit_split(self):
        for core in ([], ["seed", "seed"], ["wheel"]):
            doc = self.doc()
            doc["presets"]["cockpit-split"]["cockpit_split"]["core"] = core
            with self.subTest(core=core):
                self.assertRejected(doc, "cockpit_split core")

    def test_preview(self):
        for share in (0, 1, 1.5, "0.55"):
            doc = self.doc()
            self.tower(doc)["preview"]["share"] = share
            with self.subTest(share=share):
                self.assertRejected(doc, "preview share")
        for height in (0, 680.0, True):
            doc = self.doc()
            self.tower(doc)["preview"]["min_height"] = height
            with self.subTest(height=height):
                self.assertRejected(doc, "preview min_height")
        doc = self.doc()
        self.bento(doc)["preview"] = {"zone": "optional", "share": 0.5, "min_height": 600}
        self.assertRejected(doc, "preview zone must sit in band 0")

    def with_tray(self, doc):
        # no bundled preset has a tray; build one on tower-preview for the parse rules
        self.tower(doc)["tray"] = {"ratio": 1.3, "zones": ["prompt", "postprocess"]}
        return doc

    def test_tray(self):
        parse_presets(self.with_tray(self.doc()))
        doc = self.with_tray(self.doc())
        self.tower(doc)["preview"] = None
        self.assertRejected(doc, "a tray needs a preview")
        doc = self.doc()
        self.bento(doc)["preview"] = {"zone": "output", "share": 0.5, "min_height": 600}
        self.bento(doc)["tray"] = {"ratio": 1.3, "zones": ["optional"]}
        self.assertRejected(doc, "sit in band 0")
        for zones in ([], ["prompt", "prompt"], ["optional"]):
            doc = self.with_tray(self.doc())
            self.tower(doc)["tray"]["zones"] = zones
            with self.subTest(zones=zones):
                self.assertRejected(doc, "tray zones must be non-empty")
        doc = self.with_tray(self.doc())
        self.tower(doc)["tray"]["zones"] = ["output"]
        self.assertRejected(doc, "preview's zone cannot go to the tray")
        doc = self.with_tray(self.doc())
        self.tower(doc)["tray"]["ratio"] = 1
        self.assertRejected(doc, "tray ratio")


if __name__ == "__main__":
    unittest.main()
