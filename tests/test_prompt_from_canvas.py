import copy
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.canvas.model import NodeMode
from cwc.prompt.bypass import ResolveError, factory_prompt
from cwc.prompt.from_canvas import canvas_to_prompt
from cwc.prompt.model import drop_dangling_links, sort_prompt
from tests.fixtures.prompt.build import CanvasBuilder, load_defs

BYPASS, MUTED = int(NodeMode.BYPASS), int(NodeMode.NEVER)


def _entry(class_type, title, inputs):
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


# Example canvases. Each takes ``modes`` (node id -> mode) so one layout gives the full
# graph and, with the optional nodes set to 4, the frontend's view of the factory state.


def txt2img(defs, modes=None):
    """Loader, optional LoRA, two encoders, latent, sampler, decode, two optional upscalers
    in a chain, save, and an optional INT source linked into the sampler's ``steps``."""
    modes = modes or {}
    b = CanvasBuilder(defs)
    b.add(1, "TestLoader", widgets=["m.safetensors"], mode=modes.get(1, 0))
    b.add(2, "TestLora", widgets=["l.safetensors", 0.8], mode=modes.get(2, 0))
    b.add(3, "TestEncode", widgets=["a cat"], mode=modes.get(3, 0))
    b.add(4, "TestEncode", widgets=["blurry"], mode=modes.get(4, 0))
    b.add(5, "TestLatent", widgets=[512, 768], mode=modes.get(5, 0))
    b.add(6, "TestSampler", widgets=[123, "fixed", 20], mode=modes.get(6, 0))
    b.add(7, "TestDecode", mode=modes.get(7, 0))
    b.add(8, "TestUpscale", widgets=[1.5], mode=modes.get(8, 0))
    b.add(9, "TestUpscale", widgets=[2.0], mode=modes.get(9, 0))
    b.add(10, "TestSave", widgets=["out"], mode=modes.get(10, 0))
    b.add(11, "TestIntGen", mode=modes.get(11, 0))
    b.link(1, 0, 2, "model")
    b.link(1, 1, 2, "clip")
    b.link(2, 1, 3, "clip")
    b.link(2, 1, 4, "clip")
    b.link(2, 0, 6, "model")
    b.link(3, 0, 6, "positive")
    b.link(4, 0, 6, "negative")
    b.link(5, 0, 6, "latent")
    b.link(11, 0, 6, "steps")
    b.link(6, 0, 7, "samples")
    b.link(1, 2, 7, "vae")
    b.link(7, 0, 8, "image")
    b.link(8, 0, 9, "image")
    b.link(9, 0, 10, "images")
    return b


TXT2IMG_OPTIONAL = ["2", "8", "9", "11"]

TXT2IMG_FULL = {
    "1": _entry("TestLoader", "Test Loader", {"ckpt_name": "m.safetensors"}),
    "2": _entry("TestLora", "TestLora", {"lora_name": "l.safetensors", "strength": 0.8, "model": ["1", 0], "clip": ["1", 1]}),
    "3": _entry("TestEncode", "Test Encode", {"text": "a cat", "clip": ["2", 1]}),
    "4": _entry("TestEncode", "Test Encode", {"text": "blurry", "clip": ["2", 1]}),
    "5": _entry("TestLatent", "TestLatent", {"width": 512, "height": 768}),
    "6": _entry(
        "TestSampler",
        "Test Sampler",
        {"seed": 123, "steps": ["11", 0], "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent": ["5", 0]},
    ),
    "7": _entry("TestDecode", "Test Decode", {"samples": ["6", 0], "vae": ["1", 2]}),
    "8": _entry("TestUpscale", "TestUpscale", {"scale": 1.5, "image": ["7", 0]}),
    "9": _entry("TestUpscale", "TestUpscale", {"scale": 2.0, "image": ["8", 0]}),
    "10": _entry("TestSave", "TestSave", {"filename_prefix": "out", "images": ["9", 0]}),
    "11": _entry("TestIntGen", "TestIntGen", {}),
}

TXT2IMG_FACTORY = {
    "1": TXT2IMG_FULL["1"],
    "3": _entry("TestEncode", "Test Encode", {"text": "a cat", "clip": ["1", 1]}),
    "4": _entry("TestEncode", "Test Encode", {"text": "blurry", "clip": ["1", 1]}),
    "5": TXT2IMG_FULL["5"],
    "6": _entry(
        "TestSampler",
        "Test Sampler",
        {"seed": 123, "steps": 20, "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent": ["5", 0]},
    ),
    "7": TXT2IMG_FULL["7"],
    "10": _entry("TestSave", "TestSave", {"filename_prefix": "out", "images": ["7", 0]}),
}


def wildcard_slots(defs, modes=None):
    """A bypassed node with a widget declared first, feeding a ``*`` input: the slot order
    decides which input stands in for the output."""
    modes = modes or {}
    b = CanvasBuilder(defs)
    b.add(1, "TestImage")
    b.add(2, "TestWidgetFirst", widgets=[1.5], mode=modes.get(2, 0))
    b.add(3, "TestAny", mode=modes.get(3, 0))
    b.add(4, "TestSave", widgets=["o"])
    b.link(1, 0, 2, "image")
    b.link(2, 0, 3, "anything")
    b.link(3, 0, 4, "images")
    return b


def two_modules(defs, modes=None):
    """Two chained optional LoRAs feeding two encoders and a sampler, a list widget linked
    to an optional INT source, and an optional extra save."""
    modes = modes or {}
    b = CanvasBuilder(defs)
    b.add(1, "TestLoader", widgets=["m.safetensors"])
    b.add(2, "TestLora", widgets=["a.safetensors", 1.0], mode=modes.get(2, 0))
    b.add(3, "TestLora", widgets=["b.safetensors", 0.5], mode=modes.get(3, 0))
    b.add(4, "TestEncode", widgets=["p"])
    b.add(5, "TestEncode", widgets=["n"])
    b.add(6, "TestSampler", widgets=[7, "fixed", 30])
    b.add(7, "TestLatent", widgets=[64, 64])
    b.add(8, "TestList", widgets=[[]])
    b.add(9, "TestIntGen", mode=modes.get(9, 0))
    b.add(11, "TestDecode")
    b.add(12, "TestSave", widgets=["x"], mode=modes.get(12, 0))
    b.add(13, "TestSave", widgets=["y"])
    b.link(1, 0, 2, "model")
    b.link(1, 1, 2, "clip")
    b.link(2, 0, 3, "model")
    b.link(2, 1, 3, "clip")
    b.link(3, 1, 4, "clip")
    b.link(3, 1, 5, "clip")
    b.link(3, 0, 6, "model")
    b.link(4, 0, 6, "positive")
    b.link(5, 0, 6, "negative")
    b.link(7, 0, 6, "latent")
    b.link(9, 0, 8, "values")
    b.link(6, 0, 11, "samples")
    b.link(1, 2, 11, "vae")
    b.link(11, 0, 12, "images")
    b.link(11, 0, 13, "images")
    return b


class ExamplePromptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defs = load_defs()

    def prompt(self, builder, **kwargs):
        return canvas_to_prompt(builder.canvas(), self.defs, **kwargs)

    def test_all_enabled_prompt(self):
        out = self.prompt(txt2img(self.defs))
        self.assertEqual(out, TXT2IMG_FULL)
        self.assertEqual(list(out), [str(i) for i in range(1, 12)])

    def test_bypassed_optional_nodes(self):
        modes = {int(i): BYPASS for i in TXT2IMG_OPTIONAL}
        self.assertEqual(self.prompt(txt2img(self.defs, modes)), TXT2IMG_FACTORY)

    def test_the_canvas_is_not_changed(self):
        builder = txt2img(self.defs, {2: BYPASS})
        before = copy.deepcopy(builder.canvas().raw)
        self.prompt(builder)
        self.assertEqual(builder.canvas().raw, before)

    def test_muted_node_is_omitted_and_its_consumers_lose_the_input(self):
        out = self.prompt(txt2img(self.defs, {2: MUTED}))
        self.assertNotIn("2", out)
        self.assertNotIn("model", out["6"]["inputs"])
        self.assertNotIn("clip", out["3"]["inputs"])
        self.assertEqual(out["6"]["inputs"]["positive"], ["3", 0])

    def test_a_muted_node_feeding_a_linked_widget_leaves_the_stored_value(self):
        out = self.prompt(txt2img(self.defs, {11: MUTED}))
        self.assertNotIn("11", out)
        self.assertEqual(out["6"]["inputs"]["steps"], 20)

    def test_modes_other_than_mute_and_bypass_stay_in_the_prompt(self):
        for mode in (1, 3):
            with self.subTest(mode=mode):
                self.assertIn("2", self.prompt(txt2img(self.defs, {2: mode})))

    def test_a_cycle_through_bypassed_nodes_raises(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestAny", mode=BYPASS)
        b.add(2, "TestAny", mode=BYPASS)
        b.add(3, "TestSave", widgets=["o"])
        b.link(1, 0, 2, "anything")
        b.link(2, 0, 1, "anything")
        b.link(1, 0, 3, "images")
        with self.assertRaises(ResolveError):
            self.prompt(b)


class FactoryEquivalenceTest(unittest.TestCase):
    """``factory_prompt(full prompt)`` must equal the prompt of the same canvas with the
    optional nodes set to mode 4, which is what the real frontend exports."""

    @classmethod
    def setUpClass(cls):
        cls.defs = load_defs()

    def check(self, build, optional):
        full = canvas_to_prompt(build(self.defs).canvas(), self.defs)
        bypassed = canvas_to_prompt(
            build(self.defs, {int(i): BYPASS for i in optional}).canvas(), self.defs
        )
        got = factory_prompt(full, self.defs, optional)
        self.assertEqual(got, bypassed)
        self.assertEqual(list(got), list(bypassed))
        return got

    def test_text_to_image_with_a_chained_upscaler_and_a_linked_widget(self):
        self.assertEqual(self.check(txt2img, TXT2IMG_OPTIONAL), TXT2IMG_FACTORY)

    def test_each_optional_node_alone(self):
        for node_id in TXT2IMG_OPTIONAL:
            with self.subTest(optional=node_id):
                self.check(txt2img, [node_id])

    def test_a_widget_before_the_link_input_uses_the_frontend_socket_order(self):
        out = self.check(wildcard_slots, ["2"])
        self.assertEqual(out["3"]["inputs"], {"anything": ["1", 0]})

    def test_a_chain_through_a_wildcard_node(self):
        out = self.check(wildcard_slots, ["2", "3"])
        self.assertEqual(out["4"]["inputs"]["images"], ["1", 0])

    def test_two_chained_modules_a_list_widget_and_an_optional_leaf(self):
        out = self.check(two_modules, ["2", "3", "9", "12"])
        self.assertEqual(out["8"]["inputs"], {"values": {"__value__": []}})
        self.assertEqual(out["4"]["inputs"]["clip"], ["1", 1])
        self.assertEqual(out["6"]["inputs"]["model"], ["1", 0])
        self.assertNotIn("12", out)

    def test_the_second_module_alone(self):
        out = self.check(two_modules, ["3"])
        self.assertEqual(out["4"]["inputs"]["clip"], ["2", 1])


class VirtualNodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defs = load_defs()

    def prompt(self, builder, **kwargs):
        return canvas_to_prompt(builder.canvas(), self.defs, **kwargs)

    def sampler(self, **kwargs):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestLoader", widgets=["m.safetensors"])
        b.add(2, "TestSampler", widgets=[5, "fixed", 10], **kwargs)
        return b

    def reroute(self, b, node_id, **kwargs):
        return b.add(node_id, "TestReroute", ins=[("", "*")], outs=["*"], **kwargs)

    def test_reroutes_are_transparent_and_not_emitted(self):
        b = self.sampler()
        self.reroute(b, 3)
        self.reroute(b, 4)
        b.link(1, 0, 3, 0)
        b.link(3, 0, 4, 0)
        b.link(4, 0, 2, "model")
        out = self.prompt(b)
        self.assertEqual(list(out), ["1", "2"])
        self.assertEqual(out["2"]["inputs"]["model"], ["1", 0])

    def test_a_reroute_without_input_gives_no_link(self):
        b = self.sampler()
        self.reroute(b, 3)
        b.link(3, 0, 2, "model")
        self.assertNotIn("model", self.prompt(b)["2"]["inputs"])

    def test_a_reroute_behind_a_muted_node_gives_no_link(self):
        b = self.sampler(mode=0)
        b.add(5, "TestLora", widgets=["l", 1.0], mode=MUTED)
        self.reroute(b, 3)
        b.link(1, 0, 5, "model")
        b.link(5, 0, 3, 0)
        b.link(3, 0, 2, "model")
        out = self.prompt(b)
        self.assertNotIn("5", out)
        self.assertNotIn("model", out["2"]["inputs"])

    def test_a_bypassed_reroute_passes_its_input_on(self):
        b = self.sampler()
        self.reroute(b, 3, mode=BYPASS)
        b.link(1, 0, 3, 0)
        b.link(3, 0, 2, "model")
        self.assertEqual(self.prompt(b)["2"]["inputs"]["model"], ["1", 0])

    def test_notes_are_not_emitted(self):
        b = self.sampler()
        b.add(9, "TestNote", ins=[], outs=[], widgets=["remember"])
        self.assertEqual(list(self.prompt(b)), ["1", "2"])

    def primitive(self, b, widgets, **kwargs):
        return b.add(20, "TestPrimitive", ins=[], outs=["INT"], widgets=widgets, **kwargs)

    def test_a_primitive_writes_its_value_into_the_linked_widget(self):
        b = self.sampler()
        self.primitive(b, [42, "fixed"])
        b.link(20, 0, 2, "steps")
        out = self.prompt(b)
        self.assertEqual(list(out), ["1", "2"])
        self.assertEqual(out["2"]["inputs"]["steps"], 42)
        self.assertEqual(out["2"]["inputs"]["seed"], 5)

    def test_a_primitive_reaches_every_linked_widget(self):
        b = self.sampler()
        b.add(3, "TestLatent", widgets=[64, 64])
        self.primitive(b, [256])
        b.link(20, 0, 2, "steps")
        b.link(20, 0, 3, "width")
        b.link(20, 0, 3, "height")
        out = self.prompt(b)
        self.assertEqual(out["2"]["inputs"]["steps"], 256)
        self.assertEqual(out["3"]["inputs"], {"width": 256, "height": 256})

    def test_a_primitive_without_a_value_changes_nothing(self):
        b = self.sampler()
        self.primitive(b, [])
        b.link(20, 0, 2, "steps")
        self.assertEqual(self.prompt(b)["2"]["inputs"]["steps"], 10)

    def test_a_primitive_into_a_link_only_socket_gives_nothing(self):
        b = self.sampler()
        self.primitive(b, [42])
        b.link(20, 0, 2, "latent")
        out = self.prompt(b)
        self.assertNotIn("latent", out["2"]["inputs"])
        self.assertEqual(out["2"]["inputs"]["steps"], 10)

    def test_a_bypassed_primitive_still_writes_its_value(self):
        b = self.sampler()
        self.primitive(b, [42], mode=BYPASS)
        b.link(20, 0, 2, "steps")
        self.assertEqual(self.prompt(b)["2"]["inputs"]["steps"], 42)

    def test_a_primitive_list_value_is_wrapped(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestList", widgets=[["old"]])
        b.add(20, "TestPrimitive", ins=[], outs=["STRING"], widgets=[["a", "b"]])
        b.link(20, 0, 1, "values")
        self.assertEqual(self.prompt(b)["1"]["inputs"]["values"], {"__value__": ["a", "b"]})

    def test_a_primitive_writes_even_when_its_target_is_muted(self):
        b = self.sampler(mode=MUTED)
        self.primitive(b, [42])
        b.link(20, 0, 2, "steps")
        self.assertEqual(list(self.prompt(b)), ["1"])

    def test_the_link_decides_the_widget_even_if_the_saved_input_order_is_stale(self):
        b = self.sampler()
        self.primitive(b, [42])
        b.link(20, 0, 2, "steps")
        inputs = b.nodes[1]["inputs"]
        inputs.reverse()  # steps is no longer at its defined position, the link id still names it
        self.assertEqual(self.prompt(b)["2"]["inputs"]["steps"], 42)


class WidgetValueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defs = load_defs()

    def sampler_inputs(self, widgets=None, named=None, **kwargs):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestSampler", widgets=widgets, named=named)
        return canvas_to_prompt(b.canvas(), self.defs, **kwargs)["1"]["inputs"]

    def test_values_are_read_by_position_and_the_control_widget_stays_out(self):
        self.assertEqual(self.sampler_inputs([1, "fixed", 10]), {"seed": 1, "steps": 10})

    def test_missing_values_take_the_widget_defaults(self):
        self.assertEqual(self.sampler_inputs(), {"seed": 0, "steps": 20})
        self.assertEqual(self.sampler_inputs([5]), {"seed": 5, "steps": 20})

    def test_named_values_are_ignored_by_default(self):
        named = {"seed": 2, "control_after_generate": "x", "steps": 30}
        self.assertEqual(self.sampler_inputs([1, "fixed", 10], named), {"seed": 1, "steps": 10})
        self.assertEqual(self.sampler_inputs(None, named), {"seed": 0, "steps": 20})

    def test_named_values_win_when_named_restore_is_on(self):
        named = {"seed": 2, "control_after_generate": "x", "steps": 30}
        self.assertEqual(self.sampler_inputs([1, "fixed", 10], named, named_restore=True), {"seed": 2, "steps": 30})

    def test_a_name_missing_from_the_table_takes_the_default_when_named_restore_is_on(self):
        self.assertEqual(self.sampler_inputs([1, "fixed", 10], {"seed": 2}, named_restore=True), {"seed": 2, "steps": 20})

    def test_named_restore_without_a_table_reads_by_position(self):
        self.assertEqual(self.sampler_inputs([1, "fixed", 10], named_restore=True), {"seed": 1, "steps": 10})

    def test_the_layout_callback_gets_the_values_the_restore_uses(self):
        calls = []

        def layout(nodedef, values):
            calls.append(values)
            return nodedef.widgets

        named = {"seed": 2}
        b = CanvasBuilder(self.defs)
        b.add(1, "TestSampler", widgets=[1, "fixed", 10], named=named)
        canvas_to_prompt(b.canvas(), self.defs, widget_layout=layout)
        canvas_to_prompt(b.canvas(), self.defs, widget_layout=layout, named_restore=True)
        self.assertEqual(calls, [[1, "fixed", 10], named])

    def test_list_values_are_wrapped(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestList", widgets=[["a", "b"]])
        b.add(2, "TestList")
        out = canvas_to_prompt(b.canvas(), self.defs)
        self.assertEqual(out["1"]["inputs"]["values"], {"__value__": ["a", "b"]})
        self.assertEqual(out["2"]["inputs"]["values"], {"__value__": []})

    def test_a_linked_widget_input_takes_the_link_over_the_stored_value(self):
        out = canvas_to_prompt(txt2img(self.defs).canvas(), self.defs)
        self.assertEqual(out["6"]["inputs"]["steps"], ["11", 0])


class NodeEntryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.defs = load_defs()

    def test_title_comes_from_the_canvas_then_the_definition_then_the_type(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestLoader", widgets=["m"], title="My Loader")
        b.add(2, "TestLoader", widgets=["m"])
        b.add(3, "TestLatent")
        b.add(4, "Mystery", ins=[], outs=[])
        titles = {k: v["_meta"]["title"] for k, v in canvas_to_prompt(b.canvas(), self.defs).items()}
        self.assertEqual(titles, {"1": "My Loader", "2": "Test Loader", "3": "TestLatent", "4": "Mystery"})

    def test_unknown_types_keep_named_values_and_their_links(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestImage")
        b.add(2, "Mystery", ins=[("x", "IMAGE")], outs=["IMAGE"], named={"a": 1, "b": [1, 2]}, widgets=[9, 9])
        b.add(3, "TestSave", widgets=["o"])
        b.link(1, 0, 2, 0)
        b.link(2, 0, 3, "images")
        out = canvas_to_prompt(b.canvas(), self.defs)
        self.assertEqual(out["2"]["inputs"], {"a": 1, "b": {"__value__": [1, 2]}, "x": ["1", 0]})
        self.assertEqual(out["2"]["class_type"], "Mystery")
        self.assertEqual(out["3"]["inputs"]["images"], ["2", 0])

    def test_an_unknown_type_with_only_positional_values_has_no_widget_inputs(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "Mystery", ins=[], outs=[], widgets=[1, 2])
        self.assertEqual(canvas_to_prompt(b.canvas(), self.defs)["1"]["inputs"], {})

    def test_node_ids_come_out_in_numeric_order(self):
        b = CanvasBuilder(self.defs)
        for node_id in (10, 9, 2):
            b.add(node_id, "TestImage")
        self.assertEqual(list(canvas_to_prompt(b.canvas(), self.defs)), ["2", "9", "10"])

    def test_a_link_from_a_node_that_is_not_in_the_canvas_is_left_out(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestSave", widgets=["o"])
        canvas = b.canvas()
        canvas.raw["nodes"][0]["inputs"][0]["link"] = 99
        canvas.raw["links"].append([99, 77, 0, 1, 0, "IMAGE"])
        self.assertEqual(canvas_to_prompt(canvas, self.defs)["1"]["inputs"], {"filename_prefix": "o"})

    def test_subgraph_instances_are_refused(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestImage")
        b.add(2, "1a2b3c4d-0000-4000-8000-000000000000", ins=[], outs=[])
        canvas = b.canvas()
        canvas.raw["definitions"] = {"subgraphs": [{"id": "1a2b3c4d-0000-4000-8000-000000000000", "nodes": []}]}
        with self.assertRaisesRegex(ValueError, "subgraph instances.*2"):
            canvas_to_prompt(canvas, self.defs)

    def test_definitions_without_instances_are_fine(self):
        b = CanvasBuilder(self.defs)
        b.add(1, "TestImage")
        canvas = b.canvas()
        canvas.raw["definitions"] = {"subgraphs": [{"id": "unused", "nodes": []}]}
        self.assertEqual(list(canvas_to_prompt(canvas, self.defs)), ["1"])

    def test_saved_socket_order_does_not_change_the_result(self):
        # An old canvas that lists the widget socket first: the frontend rebuilds the
        # definition order (links first) and takes the saved links by name.
        b = wildcard_slots(self.defs, {2: BYPASS})
        node = b.nodes[1]
        node["inputs"].reverse()
        self.assertEqual(node["inputs"][0]["name"], "scale")
        out = canvas_to_prompt(b.canvas(), self.defs)
        self.assertEqual(out["3"]["inputs"], {"anything": ["1", 0]})

    def test_no_virtual_node_needs_the_builtins_file(self):
        out = canvas_to_prompt(txt2img(self.defs).canvas(), self.defs)
        self.assertEqual(out, TXT2IMG_FULL)


class PromptHelpersTest(unittest.TestCase):
    def test_dangling_links_are_removed(self):
        prompt = {
            "1": {"inputs": {"a": ["9", 0], "b": ["2", 0], "c": {"__value__": ["9", 0]}, "d": 5, "e": ["x", "y", "z"]}},
            "2": {"inputs": {}},
        }
        drop_dangling_links(prompt)
        self.assertEqual(prompt["1"]["inputs"], {"b": ["2", 0], "c": {"__value__": ["9", 0]}, "d": 5, "e": ["x", "y", "z"]})

    def test_a_two_item_list_is_taken_for_a_link_as_the_frontend_does(self):
        prompt = {"1": {"inputs": {"v": ["a", "b"]}}}
        self.assertEqual(drop_dangling_links(prompt)["1"]["inputs"], {})

    def test_sort_prompt_orders_numeric_ids_then_text(self):
        prompt = {"b": {}, "10": {}, "2": {}, "a": {}}
        self.assertEqual(list(sort_prompt(prompt)), ["2", "10", "a", "b"])


if __name__ == "__main__":
    unittest.main()
