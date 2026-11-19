import copy
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.annotate.palette import (
    MAX_CLASSES,
    group_colour,
    node_colours,
    palette_names,
    parse_palettes,
    zone_class,
)
from cwc.annotate.types import Zone
from cwc.resources import read_json


class BundledPalettesTest(unittest.TestCase):
    def test_every_zone_has_a_class_with_a_colour_in_every_palette(self):
        self.assertEqual(palette_names(), frozenset({"semantic", "official"}))
        classes = {zone_class(zone) for zone in Zone}
        self.assertLessEqual(len(classes), MAX_CLASSES)
        for name in palette_names():
            for cls in classes:
                self.assertRegex(group_colour(name, cls), r"^#[0-9a-f]{6}$")
        self.assertEqual({group_colour("official", cls) for cls in classes}, {"#3f789e"})

    def test_prompt_and_control_share_a_class(self):
        self.assertEqual(zone_class(Zone.PROMPT), zone_class(Zone.CONTROL))
        self.assertEqual(zone_class(Zone.STAGE), zone_class(Zone.SAMPLING))

    def test_node_colours(self):
        self.assertEqual(node_colours("positive").bgcolor, "#234732")
        self.assertEqual(node_colours("negative").bgcolor, "#4d262f")


class RejectedPalettesTest(unittest.TestCase):
    def doc(self):
        return copy.deepcopy(read_json("palettes.json"))

    def assertRejected(self, doc, fragment):
        with self.assertRaises(ValueError) as caught:
            parse_palettes(doc)
        self.assertIn(fragment, str(caught.exception))

    def test_unknown_top_level_key(self):
        doc = self.doc()
        doc["extra"] = {}
        self.assertRejected(doc, "unknown keys ['extra']")

    def test_a_zone_without_a_class(self):
        doc = self.doc()
        del doc["zone_classes"]["stage"]
        self.assertRejected(doc, "missing keys ['stage']")

    def test_more_than_seven_classes(self):
        doc = self.doc()
        doc["zone_classes"]["stage"] = "stage"
        for palette in doc["palettes"].values():
            palette["stage"] = "#000000"
        self.assertRejected(doc, "more than 7 classes")

    def test_a_palette_missing_a_class(self):
        doc = self.doc()
        del doc["palettes"]["semantic"]["output"]
        self.assertRejected(doc, "missing keys ['output']")

    def test_bad_colours(self):
        for bad in ("#ABCDEF", "#abc", "red", 7):
            doc = self.doc()
            doc["palettes"]["semantic"]["model"] = bad
            self.assertRejected(doc, "is not a lower-case #rrggbb colour")

    def test_node_classes_are_exactly_positive_and_negative(self):
        doc = self.doc()
        doc["nodes"]["neutral"] = {"color": "#000000", "bgcolor": "#000000"}
        self.assertRejected(doc, "nodes must name exactly")


if __name__ == "__main__":
    unittest.main()
