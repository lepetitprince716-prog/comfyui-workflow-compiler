import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.nodedefs.derive import from_object_info, slot_layout
from cwc.nodedefs.types import MATCHTYPE, InputSpec, NodeDef, OutputSpec, SlotSpec, Tier
from cwc.nodedefs.registry import NodeDefs
from cwc.prompt.slots import (
    InputSlot,
    defined_input_slots,
    merge_saved_inputs,
    output_types,
    prompt_literals,
    prompt_slots,
    saved_input_slot,
)
from tests.fixtures.prompt.build import load_defs


def _def(inputs, outputs=()):
    return NodeDef("X", Tier.TRUTH, tuple(inputs), tuple(OutputSpec(n, t) for n, t in outputs), ())


class DefinedSlotsTest(unittest.TestCase):
    def test_link_only_inputs_come_before_widget_inputs(self):
        defs = load_defs()
        slots = defined_input_slots(defs.get("TestEncode"))
        self.assertEqual([(s.name, s.widget) for s in slots], [("clip", None), ("text", "text")])
        slots = defined_input_slots(defs.get("TestSampler"))
        self.assertEqual([s.name for s in slots], ["model", "positive", "negative", "latent", "seed", "steps"])

    def test_each_group_keeps_definition_order(self):
        spec = [
            InputSpec("w1", "INT", True, True),
            InputSpec("a", "MODEL", True, False),
            InputSpec("w2", "INT", True, True),
            InputSpec("b", "CLIP", True, False),
        ]
        self.assertEqual([s.name for s in defined_input_slots(_def(spec))], ["a", "b", "w1", "w2"])

    def test_socketless_widgets_have_no_socket(self):
        spec = [InputSpec("w1", "INT", True, True, socketless=True), InputSpec("w2", "INT", True, True)]
        self.assertEqual([s.name for s in defined_input_slots(_def(spec))], ["w2"])

    def test_fresh_slots_are_unlinked(self):
        slots = defined_input_slots(load_defs().get("TestSave"))
        self.assertTrue(all(s.source is None for s in slots))

    def test_match_type_outputs_are_wildcards(self):
        info = {"input": {"required": {}}, "output": [MATCHTYPE, "IMAGE"], "output_name": ["a", "b"]}
        self.assertEqual(output_types(from_object_info("X", info)), ("*", "IMAGE"))

    def test_order_comes_from_slot_layout(self):
        # Autogrow groups and DynamicCombo sub-inputs follow nodedefs.derive.slot_layout.
        info = {
            "input": {
                "required": {
                    "mode": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                        {"key": "a", "inputs": {"required": {"img": ["IMAGE", {}]}}},
                        {"key": "b", "inputs": {"required": {}}},
                    ]}],
                    "items": ["COMFY_AUTOGROW_V3", {"template": {"input": {"required": {"x": ["LATENT", {}]}}, "prefix": "x", "min": 1}}],
                }
            },
            "output": [],
        }
        nodedef = from_object_info("X", info)
        for values in (None, ["a"], {"mode": "b"}):
            with self.subTest(values=values):
                expected = [(s.name, s.type, s.widget) for s in slot_layout(nodedef, values)]
                self.assertEqual([(s.name, s.type, s.widget) for s in defined_input_slots(nodedef, values)], expected)
        self.assertEqual([s.name for s in defined_input_slots(nodedef, ["a"])], ["items.x0", "items.x1", "mode", "mode.img"])

    def test_recorded_slots_win(self):
        recorded = (SlotSpec("b", "B"), SlotSpec("a", "A", "a"))
        nodedef = NodeDef("X", Tier.TRUTH, (InputSpec("a", "INT", True, True),), (), (), slots=recorded)
        self.assertEqual(defined_input_slots(nodedef), (InputSlot("b", "B"), InputSlot("a", "A", "a")))


class SavedSlotsTest(unittest.TestCase):
    def test_saved_slot_reads_name_type_and_widget(self):
        slot = saved_input_slot({"name": "steps", "type": "INT", "widget": {"name": "steps"}, "link": 3}, ("7", 1))
        self.assertEqual(slot, InputSlot("steps", "INT", "steps", ("7", 1)))
        self.assertEqual(saved_input_slot({}, None), InputSlot("", None, None, None))

    def test_merge_keeps_defined_order_and_takes_links_by_name(self):
        defined = (InputSlot("a", "A"), InputSlot("b", "B", widget="b"))
        saved = [
            InputSlot("b", "OLD", None, ("1", 0)),
            InputSlot("extra", "E", None, ("2", 0)),
            InputSlot("a", "OLD", None, ("3", 1)),
        ]
        self.assertEqual(
            merge_saved_inputs(defined, saved),
            (
                InputSlot("a", "A", None, ("3", 1)),
                InputSlot("b", "B", "b", ("1", 0)),
                InputSlot("extra", "E", None, ("2", 0)),
            ),
        )

    def test_a_defined_slot_missing_from_the_save_is_unlinked(self):
        merged = merge_saved_inputs((InputSlot("a", "A"),), [])
        self.assertEqual(merged, (InputSlot("a", "A"),))

    def test_the_last_saved_slot_with_a_name_wins(self):
        saved = [InputSlot("a", "A", None, ("1", 0)), InputSlot("a", "A", None, ("2", 0))]
        self.assertEqual(merge_saved_inputs((InputSlot("a", "A"),), saved)[0].source, ("2", 0))


class PromptSlotsTest(unittest.TestCase):
    """The ``inputs`` array a loaded node gets for a prompt entry's inputs."""

    @classmethod
    def setUpClass(cls):
        cls.defs = NodeDefs.load()

    def test_literals_are_the_inputs_that_are_not_links(self):
        inputs = {"a": ["1", 0], "b": 3, "c": "x", "d": {"__value__": [1, 2]}}
        self.assertEqual(prompt_literals(inputs), {"b": 3, "c": "x", "d": {"__value__": [1, 2]}})

    def test_autogrow_slots_grow_for_the_linked_inputs(self):
        nodedef = self.defs.get("BatchImagesNode")
        inputs = {"images.image0": ["1", 0], "images.image1": ["1", 0]}
        self.assertEqual([s.name for s in prompt_slots(nodedef, inputs)], ["images.image0", "images.image1", "images.image2"])

    def test_a_literal_does_not_grow_a_group(self):
        nodedef = self.defs.get("BatchImagesNode")
        self.assertEqual([s.name for s in prompt_slots(nodedef, {"images.image0": ["1", 0]})], ["images.image0", "images.image1"])

    def test_dynamic_combo_sub_inputs_follow_the_chosen_option(self):
        nodedef = self.defs.get("ResizeImageMaskNode")
        inputs = {"input": ["1", 0], "resize_type": "scale by multiplier", "resize_type.multiplier": 0.5}
        names = [s.name for s in prompt_slots(nodedef, inputs)]
        self.assertEqual(names, ["input", "resize_type", "resize_type.multiplier", "scale_method"])

    def test_a_dynamic_combo_sub_input_linked_stays_a_slot(self):
        nodedef = self.defs.get("ResizeImageMaskNode")
        inputs = {"input": ["1", 0], "resize_type": "scale by multiplier", "resize_type.multiplier": ["2", 0]}
        slots = {s.name: s for s in prompt_slots(nodedef, inputs)}
        self.assertEqual(slots["resize_type.multiplier"].widget, "resize_type.multiplier")

    def test_match_type_ids_reach_the_slots(self):
        nodedef = self.defs.get("ComfySwitchNode")
        slots = {s.name: s.match for s in prompt_slots(nodedef, {"switch": True})}
        self.assertEqual(slots, {"on_false": "switch", "on_true": "switch", "switch": None})
        self.assertEqual([out.match for out in nodedef.outputs], ["switch"])


if __name__ == "__main__":
    unittest.main()
