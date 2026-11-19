import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.canvas.model import NodeMode
from cwc.prompt.bypass import (
    ResolveError,
    ResolveNode,
    bypass_slot_index,
    factory_prompt,
    is_valid_connection,
    resolve_input,
    resolve_output,
)
from cwc.nodedefs.derive import slot_layout
from cwc.nodedefs.registry import NodeDefs
from cwc.prompt.slots import InputSlot
from tests.fixtures.prompt.build import load_defs

ALWAYS, MUTED, BYPASS = NodeMode.ALWAYS, NodeMode.NEVER, NodeMode.BYPASS


def _node(key, mode=ALWAYS, inputs=(), outputs=(), virtual=False):
    """``inputs``: (type, source) pairs; ``source`` is (origin key, output slot) or None."""
    slots = tuple(InputSlot(f"in{i}", kind, None, source) for i, (kind, source) in enumerate(inputs))
    return ResolveNode(key, mode, virtual, slots, tuple(outputs))


def _graph(*nodes):
    return {node.key: node for node in nodes}


class IsValidConnectionTest(unittest.TestCase):
    def test_wildcards_and_empty_types_match_everything(self):
        for a, b in [("*", "IMAGE"), ("IMAGE", "*"), ("", "IMAGE"), ("IMAGE", ""), ("*", ""), (None, "IMAGE"), (0, "IMAGE")]:
            with self.subTest(a=a, b=b):
                self.assertTrue(is_valid_connection(a, b))

    def test_names_compare_without_case(self):
        self.assertTrue(is_valid_connection("IMAGE", "image"))
        self.assertTrue(is_valid_connection("Image", "IMAGE"))
        self.assertFalse(is_valid_connection("IMAGE", "LATENT"))

    def test_comma_lists_match_when_any_pair_matches(self):
        self.assertTrue(is_valid_connection("INT,FLOAT", "FLOAT"))
        self.assertTrue(is_valid_connection("INT", "int,float"))
        self.assertTrue(is_valid_connection("INT,FLOAT", "float,string"))
        self.assertFalse(is_valid_connection("INT,FLOAT", "STRING,MODEL"))
        self.assertFalse(is_valid_connection("INT,FLOAT", "STRING"))

    def test_an_empty_list_member_is_a_wildcard(self):
        self.assertTrue(is_valid_connection("INT,", "STRING"))

    def test_string_list_types_are_joined_with_commas(self):
        self.assertTrue(is_valid_connection(["INT", "FLOAT"], "float"))
        self.assertFalse(is_valid_connection(["INT", "FLOAT"], "string"))

    def test_event_numbers_match_only_each_other(self):
        self.assertTrue(is_valid_connection(-1, -1))
        self.assertFalse(is_valid_connection(-1, "INT"))


class BypassSlotIndexTest(unittest.TestCase):
    def test_any_target_takes_the_same_slot_or_falls_back_to_the_first(self):
        self.assertEqual(bypass_slot_index(["A", "B"], 1, "B", "*"), 1)
        self.assertEqual(bypass_slot_index(["A", "B"], 1, "B", ""), 1)
        self.assertEqual(bypass_slot_index(["A", "B"], 3, "B", "*"), 0)

    def test_same_slot_wins_when_it_fits_both_sides(self):
        # the exact-match rule would give 0 here, the first input of the type: the second of two
        # CONDITIONING outputs of a bypassed ControlNet must come from its second input
        self.assertEqual(
            bypass_slot_index(["CONDITIONING", "CONDITIONING"], 1, "CONDITIONING", "CONDITIONING"), 1
        )

    def test_same_slot_is_skipped_when_it_does_not_fit(self):
        self.assertEqual(bypass_slot_index(["IMAGE", "MODEL"], 1, "IMAGE", "IMAGE"), 0)

    def test_exact_type_match_is_next(self):
        self.assertEqual(bypass_slot_index(["MODEL", "IMAGE"], 0, "IMAGE", "IMAGE"), 1)

    def test_exact_match_is_case_sensitive(self):
        # "image" is compatible, but only the exact "IMAGE" at index 1 is the exact match.
        self.assertEqual(bypass_slot_index(["image", "IMAGE", "x"], 2, "IMAGE", "IMAGE"), 1)

    def test_first_compatible_input_is_the_last_resort(self):
        self.assertEqual(bypass_slot_index(["MODEL", "INT,FLOAT"], 0, "FLOAT", "FLOAT"), 1)

    def test_no_fitting_input_gives_minus_one(self):
        self.assertEqual(bypass_slot_index(["MODEL"], 0, "IMAGE", "IMAGE"), -1)
        self.assertEqual(bypass_slot_index([], 0, "IMAGE", "IMAGE"), -1)


class ResolveTest(unittest.TestCase):
    def test_plain_node_resolves_to_itself(self):
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("b", inputs=[("IMAGE", ("a", 0))]),
        )
        self.assertEqual(resolve_input(graph, "b", 0), ("a", 0))

    def test_unlinked_input_and_missing_origin_resolve_to_nothing(self):
        graph = _graph(_node("b", inputs=[("IMAGE", None), ("IMAGE", ("gone", 0))]))
        self.assertIsNone(resolve_input(graph, "b", 0))
        self.assertIsNone(resolve_input(graph, "b", 1))

    def test_bypass_passes_the_matching_input_through(self):
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("mid", BYPASS, inputs=[("IMAGE", ("a", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("mid", 0))]),
        )
        self.assertEqual(resolve_input(graph, "c", 0), ("a", 0))

    def test_chained_bypass_passes_through_every_hop(self):
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("m1", BYPASS, inputs=[("IMAGE", ("a", 0))], outputs=["IMAGE"]),
            _node("m2", BYPASS, inputs=[("IMAGE", ("m1", 0))], outputs=["IMAGE"]),
            _node("m3", BYPASS, inputs=[("IMAGE", ("m2", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("m3", 0))]),
        )
        self.assertEqual(resolve_input(graph, "c", 0), ("a", 0))

    def test_a_very_long_chain_does_not_overflow_the_stack(self):
        nodes = [_node("n0", outputs=["IMAGE"])]
        nodes += [_node(f"n{i}", BYPASS, inputs=[("IMAGE", (f"n{i - 1}", 0))], outputs=["IMAGE"]) for i in range(1, 3000)]
        nodes.append(_node("end", inputs=[("IMAGE", ("n2999", 0))]))
        self.assertEqual(resolve_input(_graph(*nodes), "end", 0), ("n0", 0))

    def test_bypass_with_no_fitting_input_resolves_to_nothing(self):
        graph = _graph(
            _node("a", outputs=["MODEL"]),
            _node("mid", BYPASS, inputs=[("MODEL", ("a", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("mid", 0))]),
        )
        self.assertIsNone(resolve_input(graph, "c", 0))

    def test_muted_node_produces_nothing(self):
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("mid", MUTED, inputs=[("IMAGE", ("a", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("mid", 0))]),
        )
        self.assertIsNone(resolve_input(graph, "c", 0))

    def test_muted_node_behind_a_bypass_still_produces_nothing(self):
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("muted", MUTED, inputs=[("IMAGE", ("a", 0))], outputs=["IMAGE"]),
            _node("mid", BYPASS, inputs=[("IMAGE", ("muted", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("mid", 0))]),
        )
        self.assertIsNone(resolve_input(graph, "c", 0))

    def test_virtual_node_passes_its_input_with_the_same_index(self):
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("r", inputs=[("*", ("a", 0))], outputs=["*"], virtual=True),
            _node("c", inputs=[("IMAGE", ("r", 0))]),
        )
        self.assertEqual(resolve_input(graph, "c", 0), ("a", 0))

    def test_virtual_node_without_a_linked_input_is_discarded(self):
        graph = _graph(
            _node("r", inputs=[("*", None)], outputs=["*"], virtual=True),
            _node("note", virtual=True),
            _node("c", inputs=[("IMAGE", ("r", 0)), ("IMAGE", ("note", 0))]),
        )
        self.assertIsNone(resolve_input(graph, "c", 0))
        self.assertIsNone(resolve_input(graph, "c", 1))

    def test_the_requested_type_is_dropped_after_a_bypass(self):
        # c asks for MASK. The bypassed mid has output MASK and input type "IMAGE,MASK"; it
        # asks its own origin, the bypassed up (output IMAGE), for "IMAGE,MASK", not for MASK.
        # With "IMAGE,MASK" up picks its IMAGE input (index 1); with MASK it would pick index 0.
        graph = _graph(
            _node("p0", outputs=["MASK"]),
            _node("p1", outputs=["IMAGE"]),
            _node("up", BYPASS, inputs=[("MASK", ("p0", 0)), ("IMAGE", ("p1", 0))], outputs=["IMAGE"]),
            _node("mid", BYPASS, inputs=[("IMAGE,MASK", ("up", 0))], outputs=["MASK"]),
            _node("c", inputs=[("MASK", ("mid", 0))]),
        )
        self.assertEqual(resolve_input(graph, "c", 0), ("p1", 0))

    def test_cycle_raises(self):
        graph = _graph(
            _node("x", BYPASS, inputs=[("IMAGE", ("y", 0))], outputs=["IMAGE"]),
            _node("y", BYPASS, inputs=[("IMAGE", ("x", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("x", 0))]),
        )
        with self.assertRaisesRegex(ResolveError, "circular"):
            resolve_input(graph, "c", 0)

    def test_a_bypassed_node_feeding_itself_raises(self):
        graph = _graph(
            _node("x", BYPASS, inputs=[("IMAGE", ("x", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("x", 0))]),
        )
        with self.assertRaises(ResolveError):
            resolve_input(graph, "c", 0)

    def test_a_cycle_through_virtual_nodes_raises(self):
        graph = _graph(
            _node("r1", inputs=[("*", ("r2", 0))], outputs=["*"], virtual=True),
            _node("r2", inputs=[("*", ("r1", 0))], outputs=["*"], virtual=True),
            _node("c", inputs=[("IMAGE", ("r1", 0))]),
        )
        with self.assertRaises(ResolveError):
            resolve_input(graph, "c", 0)

    def test_the_same_node_reached_twice_without_a_loop_is_fine(self):
        # Two inputs of one consumer share an origin: each top-level call starts clean.
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("mid", BYPASS, inputs=[("IMAGE", ("a", 0))], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("mid", 0)), ("IMAGE", ("mid", 0))]),
        )
        self.assertEqual(resolve_input(graph, "c", 0), ("a", 0))
        self.assertEqual(resolve_input(graph, "c", 1), ("a", 0))

    def test_missing_slots_raise(self):
        graph = _graph(
            _node("mid", BYPASS, inputs=[("IMAGE", None)], outputs=["IMAGE"]),
            _node("c", inputs=[("IMAGE", ("mid", 3))]),
        )
        with self.assertRaises(ResolveError):
            resolve_input(graph, "c", 5)
        with self.assertRaises(ResolveError):
            resolve_input(graph, "c", 0)

    def test_resolve_output_takes_the_consumer_type(self):
        graph = _graph(
            _node("a", outputs=["IMAGE"]),
            _node("mid", BYPASS, inputs=[("MODEL", None), ("IMAGE", ("a", 0))], outputs=["IMAGE"]),
        )
        self.assertEqual(resolve_output(graph, "mid", 0, "IMAGE"), ("a", 0))
        self.assertIsNone(resolve_output(graph, "mid", 0, "LATENT"))


class FactoryPromptTest(unittest.TestCase):
    """``factory_prompt`` against hand-written full prompts. Canvas equivalence is in
    ``test_prompt_from_canvas``."""

    @classmethod
    def setUpClass(cls):
        cls.defs = load_defs()

    def _full(self):
        return {
            "1": {"class_type": "TestLoader", "inputs": {"ckpt_name": "m.safetensors"}, "_meta": {"title": "Loader"}},
            "2": {
                "class_type": "TestLora",
                "inputs": {"model": ["1", 0], "clip": ["1", 1], "lora_name": "l.safetensors", "strength": 0.8},
                "_meta": {"title": "Lora"},
            },
            "3": {
                "class_type": "TestLora",
                "inputs": {"model": ["2", 0], "clip": ["2", 1], "lora_name": "l2.safetensors", "strength": 0.5},
                "_meta": {"title": "Lora 2"},
            },
            "4": {"class_type": "TestEncode", "inputs": {"text": "a cat", "clip": ["3", 1]}, "_meta": {"title": "Encode"}},
        }

    def test_links_are_resolved_through_chained_optional_nodes(self):
        out = factory_prompt(self._full(), self.defs, ["2", "3"])
        self.assertEqual(list(out), ["1", "4"])
        self.assertEqual(out["4"]["inputs"], {"text": "a cat", "clip": ["1", 1]})

    def test_a_single_optional_node_keeps_the_rest_of_the_chain(self):
        out = factory_prompt(self._full(), self.defs, ["3"])
        self.assertEqual(list(out), ["1", "2", "4"])
        self.assertEqual(out["4"]["inputs"]["clip"], ["2", 1])

    def test_no_optional_nodes_gives_the_same_prompt(self):
        full = self._full()
        self.assertEqual(factory_prompt(full, self.defs, []), full)

    def test_the_input_prompt_is_not_changed(self):
        full = self._full()
        before = {key: {**entry, "inputs": dict(entry["inputs"])} for key, entry in full.items()}
        factory_prompt(full, self.defs, ["2", "3"])
        self.assertEqual(full, before)

    def test_an_unresolvable_link_input_is_dropped(self):
        full = {
            "1": {"class_type": "TestIntGen", "inputs": {}, "_meta": {"title": "x"}},
            "2": {"class_type": "TestDecode", "inputs": {"samples": ["1", 0], "vae": ["1", 0]}, "_meta": {"title": "d"}},
        }
        out = factory_prompt(full, self.defs, ["1"])
        self.assertEqual(out["2"]["inputs"], {})

    def test_an_unresolvable_widget_input_falls_back_to_the_widget_default(self):
        full = {
            "1": {"class_type": "TestIntGen", "inputs": {}, "_meta": {"title": "x"}},
            "2": {"class_type": "TestLatent", "inputs": {"width": ["1", 0], "height": 64}, "_meta": {"title": "l"}},
            "3": {"class_type": "TestList", "inputs": {"values": ["1", 0]}, "_meta": {"title": "t"}},
        }
        out = factory_prompt(full, self.defs, ["1"])
        self.assertEqual(out["2"]["inputs"], {"width": 512, "height": 64})
        self.assertEqual(out["3"]["inputs"], {"values": {"__value__": []}})

    def test_the_fallback_default_belongs_to_the_widget_not_its_position(self):
        # A control widget sits between seed and steps in the layout; defaults still match by name.
        full = {
            "1": {"class_type": "TestIntGen", "inputs": {}, "_meta": {"title": "x"}},
            "2": {"class_type": "TestSampler", "inputs": {"seed": ["1", 0], "steps": ["1", 0]}, "_meta": {"title": "s"}},
        }
        out = factory_prompt(full, self.defs, ["1"])
        self.assertEqual(out["2"]["inputs"], {"seed": 0, "steps": 20})

    def test_node_ids_come_out_in_numeric_order(self):
        full = {
            "10": {"class_type": "TestImage", "inputs": {}, "_meta": {"title": "a"}},
            "9": {"class_type": "TestImage", "inputs": {}, "_meta": {"title": "b"}},
            "2": {"class_type": "TestImage", "inputs": {}, "_meta": {"title": "c"}},
        }
        self.assertEqual(list(factory_prompt(full, self.defs, [])), ["2", "9", "10"])

    def test_a_cycle_between_optional_nodes_raises(self):
        full = {
            "1": {"class_type": "TestAny", "inputs": {"anything": ["2", 0]}, "_meta": {"title": "a"}},
            "2": {"class_type": "TestAny", "inputs": {"anything": ["1", 0]}, "_meta": {"title": "b"}},
            "3": {"class_type": "TestSave", "inputs": {"images": ["1", 0], "filename_prefix": "x"}, "_meta": {"title": "s"}},
        }
        with self.assertRaises(ResolveError):
            factory_prompt(full, self.defs, ["1", "2"])

    def test_unknown_types_are_refused_where_the_bypass_needs_them(self):
        full = {
            "1": {"class_type": "Nope", "inputs": {}, "_meta": {"title": "a"}},
            "2": {"class_type": "TestSave", "inputs": {"images": ["1", 0], "filename_prefix": "x"}, "_meta": {"title": "s"}},
        }
        with self.assertRaises(ValueError):
            factory_prompt(full, self.defs, ["1"])
        unknown_consumer = {
            "1": {"class_type": "TestImage", "inputs": {}, "_meta": {"title": "a"}},
            "2": {"class_type": "Nope", "inputs": {"x": ["1", 0]}, "_meta": {"title": "s"}},
        }
        with self.assertRaises(ValueError):
            factory_prompt(unknown_consumer, self.defs, ["1"])

    def test_a_consumer_input_that_is_not_a_socket_is_refused(self):
        full = {
            "1": {"class_type": "TestImage", "inputs": {}, "_meta": {"title": "a"}},
            "2": {"class_type": "TestSave", "inputs": {"nope": ["1", 0]}, "_meta": {"title": "s"}},
        }
        with self.assertRaises(ValueError):
            factory_prompt(full, self.defs, ["1"])


def _entry(class_type, inputs):
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": "x"}}


class DynamicSlotsTest(unittest.TestCase):
    """Optional nodes feeding sockets that only exist for the chosen values or links."""

    @classmethod
    def setUpClass(cls):
        cls.defs = NodeDefs.load()

    def test_autogrow_slot_linked_to_an_optional_node_resolves_through_it(self):
        full = {
            "1": _entry("LoadImage", {"image": "a.png"}),
            "2": _entry("ImageInvert", {"image": ["1", 0]}),
            "3": _entry(
                "BatchImagesNode",
                {"images.image0": ["1", 0], "images.image1": ["1", 0], "images.image2": ["2", 0]},
            ),
        }
        factory = factory_prompt(full, self.defs, ["2"])
        self.assertEqual(list(factory), ["1", "3"])
        self.assertEqual(
            factory["3"]["inputs"],
            {"images.image0": ["1", 0], "images.image1": ["1", 0], "images.image2": ["1", 0]},
        )

    def test_autogrow_slot_past_the_fresh_slots_is_found_when_every_slot_is_optional(self):
        full = {
            "1": _entry("LoadImage", {"image": "a.png"}),
            "2": _entry("ImageInvert", {"image": ["1", 0]}),
            "3": _entry("ImageInvert", {"image": ["2", 0]}),
            "4": _entry("BatchImagesNode", {"images.image0": ["1", 0], "images.image3": ["3", 0]}),
        }
        factory = factory_prompt(full, self.defs, ["2", "3"])
        self.assertEqual(factory["4"]["inputs"], {"images.image0": ["1", 0], "images.image3": ["1", 0]})

    def test_dynamic_combo_input_linked_to_an_optional_node_falls_back_to_its_default(self):
        full = {
            "1": _entry("LoadImage", {"image": "a.png"}),
            "2": _entry("PrimitiveFloat", {"value": 0.5}),
            "3": _entry(
                "ResizeImageMaskNode",
                {
                    "input": ["1", 0],
                    "resize_type": "scale by multiplier",
                    "resize_type.multiplier": ["2", 0],
                    "scale_method": "area",
                },
            ),
        }
        factory = factory_prompt(full, self.defs, ["2"])
        self.assertEqual(list(factory), ["1", "3"])
        self.assertEqual(
            factory["3"]["inputs"],
            {"input": ["1", 0], "resize_type": "scale by multiplier", "resize_type.multiplier": 1.0, "scale_method": "area"},
        )

    def test_dynamic_combo_sub_input_that_is_a_socket_keeps_the_resolved_link(self):
        full = {
            "1": _entry("LoadImage", {"image": "a.png"}),
            "2": _entry("ImageInvert", {"image": ["1", 0]}),
            "3": _entry("ResizeImageMaskNode", {"input": ["2", 0], "resize_type": "match size", "resize_type.match": ["2", 0], "resize_type.crop": "center", "scale_method": "area"}),
        }
        factory = factory_prompt(full, self.defs, ["2"])
        self.assertEqual(factory["3"]["inputs"]["input"], ["1", 0])
        self.assertEqual(factory["3"]["inputs"]["resize_type.match"], ["1", 0])


class GrownSlotsTest(unittest.TestCase):
    """``slot_layout(..., linked=...)``: Autogrow groups grow with the links, as in the frontend."""

    @classmethod
    def setUpClass(cls):
        cls.defs = NodeDefs.load()

    def names(self, type_, linked, values=None):
        return [slot.name for slot in slot_layout(self.defs.get(type_), values, linked)]

    def test_nothing_linked_keeps_the_fresh_slots(self):
        self.assertEqual(self.names("BatchImagesNode", []), ["images.image0", "images.image1"])

    def test_a_group_has_min_plus_one_slots_at_least(self):
        self.assertEqual(self.names("BatchImagesNode", ["images.image0"]), ["images.image0", "images.image1"])

    def test_one_empty_slot_follows_the_last_linked_ordinal(self):
        self.assertEqual(
            self.names("BatchImagesNode", ["images.image0", "images.image1"]),
            ["images.image0", "images.image1", "images.image2"],
        )
        self.assertEqual(
            self.names("BatchImagesNode", ["images.image3"]),
            [f"images.image{n}" for n in range(5)],
        )

    def test_the_last_linked_ordinal_counts_not_the_number_of_links(self):
        self.assertEqual(
            self.names("BatchImagesNode", ["images.image0", "images.image2"]),
            [f"images.image{n}" for n in range(4)],
        )

    def test_new_slots_are_inserted_at_the_end_of_the_group_before_later_inputs(self):
        names = self.names("TextEncodeMOSSImageEdit", ["images.image_0", "images.image_1", "vae"])
        self.assertEqual(names[:6], ["clip", "images.image_0", "images.image_1", "images.image_2", "vae", "prompt"])

    def test_a_group_never_grows_past_its_maximum(self):
        # GLSLShader curves: min 0, max 4
        names = [n for n in self.names("GLSLShader", ["curves.u_curve3"]) if n.startswith("curves.")]
        self.assertEqual(names, [f"curves.u_curve{n}" for n in range(4)])

    def test_a_group_with_a_minimum_of_zero_starts_with_one_slot(self):
        grown = [n for n in self.names("GLSLShader", ["curves.u_curve0"]) if n.startswith("curves.")]
        self.assertEqual(grown, ["curves.u_curve0", "curves.u_curve1"])

    def test_groups_grow_independently(self):
        names = self.names("GLSLShader", ["images.image1", "curves.u_curve0"])
        self.assertEqual([n for n in names if n.startswith("images.")], ["images.image0", "images.image1", "images.image2"])
        self.assertEqual([n for n in names if n.startswith("curves.")], ["curves.u_curve0", "curves.u_curve1"])

    def test_grown_slots_after_the_first_are_optional(self):
        slots = {slot.name: slot for slot in slot_layout(self.defs.get("BatchImagesNode"), None, ["images.image1"])}
        self.assertFalse(slots["images.image0"].optional)
        self.assertTrue(slots["images.image1"].optional)
        self.assertTrue(slots["images.image2"].optional)

    def test_unrelated_links_do_not_grow_a_group(self):
        self.assertEqual(
            self.names("TextEncodeMOSSImageEdit", ["clip", "vae"])[:4],
            ["clip", "images.image_0", "images.image_1", "vae"],
        )

    def test_growth_in_a_node_with_a_dynamic_combo_keeps_the_group_order(self):
        nodedef = self.defs.get("TextGenerateV2")
        values = {"sampling_mode": nodedef.input("sampling_mode").combo_values[0]}
        names = [slot.name for slot in slot_layout(nodedef, values, ["images.image1"])]
        self.assertEqual(names[:4], ["clip", "images.image1", "images.image2", "videos.video1"])


if __name__ == "__main__":
    unittest.main()
