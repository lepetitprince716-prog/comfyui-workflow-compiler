"""``node_size`` and ``estimate_size``: classic node sizes for nodes cwc creates."""

import dataclasses
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.nodedefs.derive import slot_layout
from cwc.nodedefs.sizing import estimate_size, node_size
from tests.fixtures.instantiate import graphs

DEFS = graphs.DEFS


def linked_names(node):
    return [slot["name"] for slot in node["inputs"] if slot["link"] is not None]


class FrontendSizesTest(unittest.TestCase):
    """The sizes of the nodes in ``frontend_serialize.json``, made by a real frontend."""

    def test_node_size_gives_the_measured_size(self):
        for node in graphs.serialize_fixture()["nodes"]:
            if node["type"] == "ResizeImageMaskNode":
                continue  # next test
            nodedef = DEFS.get(node["type"])
            values = node.get("widgets_values_named")
            slots = slot_layout(nodedef, values, linked_names(node))
            with self.subTest(node=node["id"], type=node["type"]):
                self.assertEqual(list(node_size(nodedef, values, slots=slots)), node["size"])

    def test_a_dynamic_combo_node_keeps_its_larger_fresh_size(self):
        # The frontend shrank this node when the option was chosen; a loaded node keeps
        # a larger written size, and the fresh size is never cut.
        node = next(n for n in graphs.serialize_fixture()["nodes"] if n["type"] == "ResizeImageMaskNode")
        nodedef = DEFS.get(node["type"])
        width, height = node_size(nodedef, node["widgets_values_named"])
        self.assertEqual((width, height), nodedef.size)
        self.assertGreaterEqual(height, node["size"][1])

    def test_a_fresh_layout_takes_the_recorded_size(self):
        for name in ("KSampler", "CheckpointLoaderSimple", "LoraLoader", "LoadImage"):
            nodedef = DEFS.get(name)
            with self.subTest(name):
                self.assertEqual(node_size(nodedef), nodedef.size)


class GrownLayoutTest(unittest.TestCase):
    def test_autogrow_height_follows_the_number_of_slots(self):
        nodedef = DEFS.get("BatchImagesNode")
        heights = {}
        for count in range(2, 6):
            linked = [f"images.image{n}" for n in range(count - 1)]
            slots = slot_layout(nodedef, None, linked)
            self.assertEqual(len(slots), count)
            heights[count] = node_size(nodedef, None, slots=slots)
        self.assertEqual(heights[2], nodedef.size)
        self.assertEqual([heights[n][1] for n in range(2, 6)], [46, 66, 86, 106])
        self.assertTrue(all(size[0] == 140 for size in heights.values()))

    def test_grown_autogrow_is_taller_than_a_fresh_node_when_it_leaves_the_minimum(self):
        nodedef = DEFS.get("TextEncodeMOSSImageEdit")
        fresh = node_size(nodedef)
        self.assertEqual(fresh, nodedef.size)
        names = ["images.image_0", "images.image_1", "images.image_2", "images.image_3"]
        slots = slot_layout(nodedef, None, names)
        grown = node_size(nodedef, None, slots=slots)
        self.assertGreater(grown[1], fresh[1])
        self.assertEqual(grown[0], fresh[0])  # the text width is never cut

    def test_dynamic_combo_option_with_more_widgets_is_taller_than_a_fresh_node(self):
        nodedef = DEFS.get("DecimateMesh")
        fresh = node_size(nodedef)
        chosen = node_size(nodedef, {"placement_mode": "qem"})
        self.assertEqual(fresh, nodedef.size)
        self.assertGreater(chosen[1], fresh[1])
        self.assertGreaterEqual(chosen[0], fresh[0])

    def test_a_recorded_size_wider_than_the_estimate_keeps_its_width(self):
        nodedef = dataclasses.replace(DEFS.get("DecimateMesh"), size=(500.0, 10.0))
        width, height = node_size(nodedef, {"placement_mode": "qem"})
        self.assertEqual(width, 500.0)
        self.assertGreater(height, 10.0)

    def test_without_a_recorded_size_the_estimate_is_used(self):
        nodedef = dataclasses.replace(DEFS.get("DecimateMesh"), size=None)
        self.assertEqual(node_size(nodedef, {"placement_mode": "qem"}), (270.0, 178.0))

    def test_slots_default_to_the_values_layout(self):
        nodedef = DEFS.get("DecimateMesh")
        values = {"placement_mode": "qem"}
        self.assertEqual(node_size(nodedef, values), node_size(nodedef, values, slots=slot_layout(nodedef, values)))


# Core Autogrow nodes, each built live in frontend 1.59.1 with a link into the first slot of its
# group (tests/oracle_runner.py capture), then loaded and serialized again (batch), twice.
# (type, linked slot, height as built, width and height after loading); the second load
# changed nothing. See tests/fixtures/instantiate/load_probes.md.
LOADED = (
    ("BatchCameras", "cameras.camera_info0", 46, 191.8359375, 66),
    ("BatchImagesNode", "images.image0", 46, 140, 66),
    ("BatchLatentsNode", "latents.latent0", 46, 140, 66),
    ("BatchMasksNode", "masks.mask0", 46, 140, 66),
    ("BerniniConditioning", "reference_images.reference_image_0", 274, 270, 294),
    ("BranchNode", "autogrow.branch0", 78, 270, 78),
    ("ComfyAndNode", "values.value0", 46, 140.513671875, 66),
    ("ComfyOrNode", "values.value0", 46, 140.513671875, 66),
    ("ConcatenateVideo", "videos.video0", 98, 270, 118),
    ("CreateList", "inputs.input0", 46, 140, 66),
    ("EndLoop", "terminations.termination0", 118, 270, 138),
    ("GLSLShader", "images.image0", 212, 400, 232),
    ("HiDreamO1ReferenceImages", "images.image_1", 86, 243.52890625, 106),
    ("MiniMaxH3ImageConditioning", "images.image_0", 200, 400, 216),
    ("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_0", 324, 400, 344),
    ("PH3ImageConditioning", "images.image_1", 200, 400, 216),
    ("ReferenceMultiLatent", "images.image0", 138, 274.694921875, 158),
    ("StringFormat", "values.a", 200, 400, 200),
    ("TextEncodeBooguEdit", "images.image_1", 202, 400, 222),
    ("TextEncodeFibo2", "images.image_1", 274, 400, 294),
    ("TextEncodeJoyImageEdit", "images.image0", 200, 400, 200),
    ("TextEncodeMOSSImageEdit", "images.image_0", 200, 400, 216),
    ("TextEncodeMageFlowEdit", "images.image_1", 274, 400, 294),
    ("TextEncodeMingImageEdit", "images.image_1", 200, 400, 200),
    ("TextEncodeQwenImage21", "images.image_1", 226, 400, 246),
    ("TextGenerateV2", "images.image1", 476, 400, 496),
)


class LoadedSizeTest(unittest.TestCase):
    """A loaded node with a linked Autogrow slot is one row taller than one built live."""

    def test_a_loaded_node_has_the_measured_size(self):
        for type_, slot, _, width, height in LOADED:
            with self.subTest(type_):
                self.assertEqual(node_size(DEFS.get(type_), None, linked=[slot]), (width, height))

    def test_a_node_built_live_has_the_size_before_loading(self):
        for type_, slot, built, width, _ in LOADED:
            nodedef = DEFS.get(type_)
            with self.subTest(type_):
                self.assertEqual(node_size(nodedef, None, slots=slot_layout(nodedef, None, [slot])), (width, built))

    def test_the_extra_row_comes_before_the_multiline_minimum(self):
        nodedef = DEFS.get("TextEncodeJoyImageEdit")
        self.assertEqual(node_size(nodedef, None, linked=["images.image0"]), nodedef.size)

    def test_the_extra_row_is_paid_once(self):
        nodedef = DEFS.get("BatchImagesNode")
        height = {count: node_size(nodedef, None, linked=[f"images.image{n}" for n in range(count)])[1] for count in (1, 2, 3)}
        self.assertEqual(height, {1: 66, 2: 86, 3: 106})

    def test_a_link_outside_the_group_adds_nothing(self):
        nodedef = DEFS.get("TextEncodeMOSSImageEdit")
        self.assertEqual(node_size(nodedef, None, linked=["clip", "vae"]), nodedef.size)

    def test_a_match_type_group_adds_nothing(self):
        nodedef = DEFS.get("BranchNode")
        slots = slot_layout(nodedef, None, ["autogrow.branch0", "autogrow.branch1"])
        self.assertEqual(node_size(nodedef, None, linked=["autogrow.branch0", "autogrow.branch1"]), node_size(nodedef, None, slots=slots))
        self.assertEqual(node_size(nodedef, None, linked=["autogrow.branch0", "autogrow.branch1"]), (270.0, 122.0))


class EstimateTest(unittest.TestCase):
    def test_a_bare_node_has_one_slot_row(self):
        self.assertEqual(estimate_size(0, 0, []), (140.0, 26.0))
        self.assertEqual(estimate_size(1, 1, []), (140.0, 26.0))

    def test_rows_follow_the_larger_of_inputs_and_outputs(self):
        self.assertEqual(estimate_size(3, 1, []), (140.0, 66.0))
        self.assertEqual(estimate_size(1, 4, []), (140.0, 86.0))

    def test_widgets_widen_the_node_and_add_their_height(self):
        self.assertEqual(estimate_size(1, 1, [False]), (270.0, 58.0))
        self.assertEqual(estimate_size(0, 1, [False, False, False]), (270.0, 20 + 3 * 24 + 8 + 6))

    def test_a_node_with_a_widget_but_no_slots_still_has_one_row(self):
        self.assertEqual(estimate_size(0, 0, [False]), (270.0, 58.0))

    def test_a_multiline_text_widget_opens_wide_and_at_least_200_tall(self):
        self.assertEqual(estimate_size(1, 1, [True]), (400.0, 200.0))
        self.assertEqual(estimate_size(2, 1, [True, False]), (400.0, 200.0))

    def test_a_tall_multiline_node_is_not_cut_to_200(self):
        self.assertEqual(estimate_size(2, 1, [True, True, True, True]), (400.0, 40 + 4 * 54 + 8 + 6))


if __name__ == "__main__":
    unittest.main()
