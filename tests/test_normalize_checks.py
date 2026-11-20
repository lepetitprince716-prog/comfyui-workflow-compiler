"""E1 to E7: each check with a document that fails it and one that passes."""

import unittest
from unittest import mock

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.nodedefs.derive import from_object_info
from cwc.nodedefs.types import NodeDef, Tier
from cwc.normalize import normalize
from cwc.report import Code, CwcError, ExitCode, Severity
from tests.fixtures.normalize.registry import load_doc, load_registry

REGISTRY = load_registry()


def txt2img():
    return load_doc("txt2img.json")


# Synthetic types for shapes the bundled samples do not have; none is a real node type.
SYNTHETIC = {
    "TestSocketless": {"input": {"required": {"names": ["COMFY_BRANCH_INPUT_NAMES", {}]}}, "output": ["INT"]},
    "TestMulti": {"input": {"required": {"pick": ["COMBO", {"options": ["a", "b"], "multi_select": True}]}}, "output": ["INT"]},
}


def synthetic_registry():
    registry = REGISTRY
    for name, info in SYNTHETIC.items():
        registry = registry.with_def(from_object_info(name, info))
    return registry


class CheckCase(unittest.TestCase):
    registry = REGISTRY

    def passes(self, doc, **kwargs):
        authoring, findings = normalize(doc, self.registry, **kwargs)
        return authoring, findings

    def errors(self, doc, **kwargs):
        with self.assertRaises(CwcError) as caught:
            normalize(doc, self.registry, **kwargs)
        self.assertEqual(caught.exception.exit_code, ExitCode.INPUT_ERROR)
        return [f for f in caught.exception.findings if f.severity is Severity.ERROR]

    def only(self, doc, code, **kwargs):
        """The single error the document raises, which must have ``code``."""
        found = self.errors(doc, **kwargs)
        self.assertEqual([f.code for f in found], [code], [f.message for f in found])
        return found[0]


class E1UnknownTypeTest(CheckCase):
    def test_unknown_type_lists_close_names_and_the_ways_through(self):
        doc = txt2img()
        doc["sampler"]["class_type"] = "KSamplr"
        finding = self.errors(doc)[0]
        self.assertEqual(finding.code, Code.E1)
        self.assertEqual(finding.nodes, ("sampler",))
        self.assertEqual(finding.data["closest"][0], "KSampler")
        self.assertEqual(len(finding.data["closest"]), 3)
        self.assertIn("--nodedefs", finding.hint)
        self.assertIn("--allow-unknown", finding.hint)

    def test_known_types_pass(self):
        self.passes(txt2img())

    def test_frontend_only_node_is_rejected_even_with_allow_unknown(self):
        note = NodeDef(type="TestOnlyNote", tier=Tier.BUILTIN, inputs=(), outputs=(), widgets=(), virtual=True)
        self.registry = REGISTRY.with_def(note)
        doc = txt2img()
        doc["note"] = {"class_type": "TestOnlyNote", "inputs": {}}
        finding = self.only(doc, Code.E1, allow_unknown=True)
        self.assertIn("frontend-only", finding.message)


    def optional_feeds_unknown(self):
        doc = txt2img()
        doc["lora"] = {
            "class_type": "LoraLoader",
            "inputs": {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "style.safetensors"},
            "_meta": {"ui_optional": True},
        }
        doc["unknown"] = {"class_type": "TestUnknownType", "inputs": {"m": ["lora", 0]}}
        return doc

    def test_unknown_type_cannot_take_links_from_optional_nodes(self):
        finding = self.only(self.optional_feeds_unknown(), Code.E1, allow_unknown=True)
        self.assertEqual(finding.nodes, ("unknown", "lora"))
        self.assertIn("optional", finding.message)

    def test_factory_state_failure_is_an_e7_not_a_crash(self):
        # The E1 check above normally stops this document first; with it off, the factory state cannot resolve the link.
        with mock.patch("cwc.normalize.build._check_unknown_bypass"):
            finding = self.only(self.optional_feeds_unknown(), Code.E7, allow_unknown=True)
        self.assertIn("factory state cannot be computed", finding.message)


class E2InputNameTest(CheckCase):
    def test_unknown_input_lists_the_valid_names(self):
        doc = txt2img()
        doc["sampler"]["inputs"]["stepz"] = 20
        finding = self.only(doc, Code.E2)
        self.assertIn("steps", finding.data["valid"])
        self.assertIn("latent_image", finding.hint)

    def test_value_control_widget_is_not_an_input(self):
        doc = txt2img()
        doc["sampler"]["inputs"]["control_after_generate"] = "fixed"
        self.assertIn("ui_seed", self.only(doc, Code.E2).hint)

    def test_upload_button_is_not_an_input(self):
        self.only({"img": {"class_type": "LoadImage", "inputs": {"image": "a.png", "upload": "image"}}}, Code.E2)

    def resize(self, mode, **extra):
        return {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "resize": {
                "class_type": "ResizeImageMaskNode",
                "inputs": {"input": ["img", "IMAGE"], "resize_type": mode, **extra},
            },
        }

    def test_dynamic_combo_sub_inputs_follow_the_selected_option(self):
        authoring, _ = self.passes(self.resize("scale dimensions", **{"resize_type.width": 640}))
        inputs = authoring.full[authoring.ids["resize"]]["inputs"]
        self.assertEqual(inputs["resize_type.width"], 640)
        self.assertEqual(inputs["resize_type.height"], 512)  # default of the selected option
        self.assertEqual(inputs["resize_type.crop"], "center")
        authoring, _ = self.passes(self.resize("scale by multiplier", **{"resize_type.multiplier": 2.0}))
        inputs = authoring.full[authoring.ids["resize"]]["inputs"]
        self.assertEqual(inputs["resize_type.multiplier"], 2.0)
        self.assertNotIn("resize_type.width", inputs)

    def test_sub_input_of_another_option_is_rejected(self):
        finding = self.only(self.resize("scale dimensions", **{"resize_type.multiplier": 2.0}), Code.E2)
        self.assertIn("resize_type.width", finding.data["valid"])

    def batch(self, *slots):
        doc = {"img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}
        doc["batch"] = {"class_type": "BatchImagesNode", "inputs": {slot: ["img", 0] for slot in slots}}
        return doc

    def test_autogrow_slots_are_valid_names(self):
        authoring, _ = self.passes(self.batch("images.image0", "images.image1", "images.image49"))
        inputs = authoring.full[authoring.ids["batch"]]["inputs"]
        self.assertEqual(list(inputs), ["images.image0", "images.image1", "images.image49"])

    def test_autogrow_slot_past_the_maximum_is_rejected(self):
        finding = self.only(self.batch("images.image0", "images.image50"), Code.E2)
        self.assertIn("images.image0 ... images.image49", finding.data["valid"])

    def test_autogrow_group_name_itself_is_not_an_input(self):
        self.only(self.batch("images.image0", "images"), Code.E2)

    def test_ui_locked_must_name_widgets(self):
        doc = txt2img()
        doc["sampler"]["_meta"]["ui_locked"] = ["cfgg"]
        self.only(doc, Code.E2)
        doc["sampler"]["_meta"]["ui_locked"] = ["cfg", "control_after_generate"]
        self.passes(doc)


class E3MissingInputTest(CheckCase):
    def test_required_combo_without_default_lists_its_options(self):
        doc = txt2img()
        del doc["sampler"]["inputs"]["sampler_name"]
        finding = self.only(doc, Code.E3)
        self.assertIn("euler", finding.data["options"])
        self.assertEqual(finding.data["type"], "COMBO")

    def test_required_string_without_default(self):
        doc = txt2img()
        del doc["pos"]["inputs"]["text"]
        self.assertEqual(self.only(doc, Code.E3).nodes, ("pos",))

    def test_required_socket(self):
        doc = txt2img()
        del doc["sampler"]["inputs"]["model"]
        self.assertEqual(self.only(doc, Code.E3).data, {"type": "MODEL"})

    def test_inputs_with_a_declared_default_may_be_omitted(self):
        doc = txt2img()
        del doc["latent"]["inputs"]["batch_size"]
        authoring, _ = self.passes(doc)
        self.assertEqual(authoring.full[authoring.ids["latent"]]["inputs"]["batch_size"], 1)

    def test_required_autogrow_slots(self):
        doc = {"img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}
        doc["batch"] = {"class_type": "BatchImagesNode", "inputs": {}}
        self.assertIn("images.image0", self.only(doc, Code.E3).message)
        doc["fmt"] = {"class_type": "StringFormat", "inputs": {}}  # min 0: no slot is required
        doc["batch"]["inputs"]["images.image0"] = ["img", 0]
        authoring, _ = self.passes(doc)
        self.assertEqual(authoring.full[authoring.ids["fmt"]]["inputs"], {"f_string": "{a}"})


class E4LinkTypeTest(CheckCase):
    def test_incompatible_link_names_both_types(self):
        doc = txt2img()
        doc["sampler"]["inputs"]["model"] = ["ckpt", "CLIP"]
        finding = self.only(doc, Code.E4)
        self.assertEqual(finding.data, {"source_type": "CLIP", "target_type": "MODEL"})
        self.assertEqual(finding.nodes, ("sampler", "ckpt"))

    def test_widget_input_linked_from_the_wrong_type(self):
        doc = txt2img()
        doc["seed"] = {"class_type": "PrimitiveInt", "inputs": {"value": 1}}
        doc["sampler"]["inputs"]["cfg"] = ["seed", 0]
        self.only(doc, Code.E4)

    def test_widget_without_an_input_socket_cannot_take_a_link(self):
        self.registry = synthetic_registry()
        doc = {
            "seed": {"class_type": "PrimitiveInt", "inputs": {"value": 1}},
            "node": {"class_type": "TestSocketless", "inputs": {"names": ["seed", 0]}},
        }
        finding = self.only(doc, Code.E4)
        self.assertIn("no input socket", finding.message)

    def test_wildcard_and_type_lists_connect(self):
        doc = txt2img()
        doc["resize"] = {
            "class_type": "ResizeImageMaskNode",
            "inputs": {"input": ["decode", "IMAGE"], "resize_type": "scale by multiplier"},
        }
        doc["save"]["inputs"]["images"] = ["resize", 0]  # output type "*"
        self.passes(doc)

    def test_custom_widget_linked_from_the_wrong_type(self):
        doc = {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "crop": {"class_type": "ImageCropV2", "inputs": {"image": ["img", 0], "crop_region": ["img", 0]}},
        }
        self.assertEqual(self.only(doc, Code.E4).data["target_type"], "BOUNDING_BOX")


class E5LiteralTest(CheckCase):
    def sampler_with(self, **values):
        doc = txt2img()
        doc["sampler"]["inputs"].update(values)
        return doc

    def test_basic_types(self):
        for name, value in [("steps", 28.5), ("steps", "28"), ("steps", True), ("cfg", "4"), ("denoise", None)]:
            with self.subTest(name=name, value=value):
                self.only(self.sampler_with(**{name: value}), Code.E5)
        doc = txt2img()
        doc["pos"]["inputs"]["text"] = 5
        self.only(doc, Code.E5)

    def test_integral_float_is_an_integer(self):
        authoring, _ = self.passes(self.sampler_with(steps=30.0))
        value = authoring.full[authoring.ids["sampler"]]["inputs"]["steps"]
        self.assertEqual((value, type(value)), (30, int))

    def test_range(self):
        finding = self.only(self.sampler_with(steps=0), Code.E5)
        self.assertEqual(finding.data, {"min": 1, "max": 10000})
        self.only(self.sampler_with(denoise=1.5), Code.E5)
        self.passes(self.sampler_with(steps=1, denoise=0.0))

    def test_combo_value_outside_the_list_is_a_warning(self):
        authoring, findings = self.passes(self.sampler_with(sampler_name="euler_new"))
        self.assertEqual([(f.code, f.severity) for f in findings], [(Code.E5, Severity.WARNING)])
        self.assertIn("euler", findings[0].data["options"])
        self.assertEqual(authoring.full[authoring.ids["sampler"]]["inputs"]["sampler_name"], "euler_new")

    def test_empty_option_list_is_not_checked(self):
        doc = txt2img()
        doc["ckpt"]["inputs"]["ckpt_name"] = "anything.safetensors"
        self.assertEqual(self.passes(doc)[1], [])

    def test_literal_on_a_link_only_input(self):
        self.only(self.sampler_with(model="model.safetensors"), Code.E5)

    def test_bare_list_and_bad_wrapper(self):
        self.only(self.sampler_with(steps=[1, 2, 3]), Code.E5)
        self.only(self.sampler_with(steps={"__value__": 3}), Code.E5)

    def test_bare_list_on_a_custom_widget_is_refused_before_the_type_check(self):
        # A custom widget type accepts any value shape, so only the shape check stops the list.
        doc = {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "crop": {"class_type": "ImageCropV2", "inputs": {"image": ["img", 0], "crop_region": [0, 0, 64, 64, 1]}},
        }
        finding = self.only(doc, Code.E5)
        self.assertIn("not a link", finding.message)

    def test_combo_takes_one_value_not_null_or_an_object(self):
        for value in (None, {"a": 1}, {"__value__": ["euler"]}):
            with self.subTest(value=value):
                finding = self.only(self.sampler_with(sampler_name=value), Code.E5)
                self.assertIn("one option value", finding.message)

    def test_multi_select_combo_takes_a_list(self):
        self.registry = synthetic_registry()
        finding = self.only({"m": {"class_type": "TestMulti", "inputs": {"pick": "a"}}}, Code.E5)
        self.assertIn("a list of options", finding.message)
        _, findings = self.passes({"m": {"class_type": "TestMulti", "inputs": {"pick": {"__value__": ["a"]}}}})
        self.assertEqual(findings, [])

    def test_value_of_an_unpersisted_widget_is_dropped_with_a_warning(self):
        doc = {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "compare": {"class_type": "ImageCompare", "inputs": {"image_a": ["img", 0], "compare_view": "state"}},
        }
        authoring, findings = self.passes(doc)
        self.assertEqual([(f.code, f.severity) for f in findings], [(Code.E5, Severity.WARNING)])
        self.assertNotIn("compare_view", authoring.full[authoring.ids["compare"]]["inputs"])

    def test_dynamic_combo_value_must_be_an_option(self):
        doc = {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "resize": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["img", 0], "resize_type": "stretch"}},
        }
        self.only(doc, Code.E5)


class E6LinkTargetTest(CheckCase):
    def test_missing_source_node(self):
        doc = txt2img()
        doc["decode"]["inputs"]["vae"] = ["vae_loader", "VAE"]
        self.assertIn("ckpt", self.only(doc, Code.E6).hint)

    def test_missing_output_lists_the_outputs(self):
        doc = txt2img()
        doc["decode"]["inputs"]["vae"] = ["ckpt", 3]
        finding = self.only(doc, Code.E6)
        self.assertEqual([o["name"] for o in finding.data["outputs"]], ["MODEL", "CLIP", "VAE"])
        doc["decode"]["inputs"]["vae"] = ["ckpt", "VAE_"]
        self.only(doc, Code.E6)
        doc["decode"]["inputs"]["vae"] = ["ckpt", -1]
        self.only(doc, Code.E6)

    def test_negative_output_index_on_an_unknown_type(self):
        doc = txt2img()
        doc["unknown"] = {"class_type": "TestUnknownType", "inputs": {}}
        doc["decode"]["inputs"]["vae"] = ["unknown", -1]
        finding = self.only(doc, Code.E6, allow_unknown=True)
        self.assertIn("output index -1", finding.message)

    def ambiguous(self, output):
        doc = txt2img()
        doc["ref"] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}}
        doc["size"] = {"class_type": "GetImageSize", "inputs": {"image": ["ref", "IMAGE"]}}
        doc["latent"]["inputs"]["width"] = ["size", output]
        return doc

    def test_ambiguous_output_name_asks_for_the_index(self):
        finding = self.only(self.ambiguous("INT"), Code.E6)
        self.assertIn("ambiguous", finding.message)
        self.assertIn("index", finding.hint)

    def test_unambiguous_references_pass(self):
        self.passes(self.ambiguous("width"))
        self.passes(self.ambiguous(0))


class E7StructureTest(CheckCase):
    def test_cycle_reports_the_chain(self):
        doc = {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "a": {"class_type": "ImageScaleBy", "inputs": {"image": ["b", 0], "upscale_method": "area"}},
            "b": {"class_type": "ImageScaleBy", "inputs": {"image": ["a", 0], "upscale_method": "area"}},
        }
        finding = self.only(doc, Code.E7)
        self.assertEqual(finding.data["cycle"], ["a", "b", "a"])
        doc["b"]["inputs"]["image"] = ["img", 0]
        self.passes(doc)

    def test_self_link_is_a_cycle(self):
        doc = {"a": {"class_type": "ImageScaleBy", "inputs": {"image": ["a", 0], "upscale_method": "area"}}}
        self.assertEqual(self.only(doc, Code.E7).data["cycle"], ["a", "a"])

    def test_bad_ids(self):
        node = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
        for key in ["a:b", "", " ", "007", "0", "_private"]:
            with self.subTest(key=key):
                self.only({key: dict(node), "ok": dict(node)}, Code.E7)

    def test_bad_node_envelopes(self):
        for entry in [[], {"inputs": {}}, {"class_type": "LoadImage", "inputs": []}, {"class_type": "LoadImage", "inputs": {"image": "a"}, "_meta": "x"}]:
            with self.subTest(entry=entry):
                self.only({"n": entry}, Code.E7)

    def test_workflow_key(self):
        bad = [
            "x",
            {"format": 2},
            {"title": "t"},
            {"format": 1, "titel": "t"},
            {"format": 1, "layout": {"preset": 3}},
            {"format": 1, "groups": {"g": {"title": 1}}},
            {"format": 1, "models": [{"name": "m", "url": "u"}]},
        ]
        for workflow in bad:
            with self.subTest(workflow=workflow):
                doc = txt2img()
                doc["_workflow"] = workflow
                self.only(doc, Code.E7)

    def test_complete_workflow_key_parses(self):
        doc = txt2img()
        doc["_workflow"] = {
            "format": 1,
            "title": "T",
            "locale": "zh",
            "layout": {"preset": "stepped"},
            "groups": {"b": {"title": "B"}, "a": {"note": "n"}},
            "models": [{"name": "m.safetensors", "url": "https://example.com/m", "directory": "checkpoints"}],
        }
        workflow = self.passes(doc)[0].workflow
        self.assertEqual(list(workflow.groups), ["a", "b"])
        self.assertEqual(workflow.groups["a"].note, "n")
        self.assertEqual(workflow.models[0].directory, "checkpoints")
        self.assertEqual((workflow.locale, workflow.preset), ("zh", "stepped"))

    def test_mixed_optional_group(self):
        doc = txt2img()
        doc["upscale"] = {
            "class_type": "ImageScaleBy",
            "inputs": {"image": ["decode", 0], "upscale_method": "area"},
            "_meta": {"ui_optional": True, "ui_group": "post"},
        }
        doc["save"]["_meta"] = {"ui_group": "post"}
        finding = self.only(doc, Code.E7)
        self.assertEqual(finding.data["optional"], ["upscale"])
        self.assertEqual(finding.data["non_optional"], ["save"])
        doc["save"]["_meta"] = {"ui_group": "output"}
        self.passes(doc)

    def test_meta_values_are_typed(self):
        for meta in [{"ui_optional": "yes"}, {"ui_role": "boss"}, {"ui_order": 1.5}, {"ui_seed": "increment"}, {"ui_expose": "text"}, {"ui_group": ""}]:
            with self.subTest(meta=meta):
                doc = txt2img()
                doc["sampler"]["_meta"] = meta
                self.only(doc, Code.E7)

    def test_extra_node_keys_are_dropped_with_a_warning(self):
        doc = txt2img()
        doc["sampler"]["note"] = "kept by nobody"
        authoring, findings = self.passes(doc)
        self.assertEqual([(f.code, f.severity, f.nodes) for f in findings], [(Code.E7, Severity.WARNING, ("sampler",))])
        self.assertNotIn("note", authoring.full[authoring.ids["sampler"]])

    def test_unknown_ui_meta_key_is_an_error(self):
        doc = txt2img()
        doc["sampler"]["_meta"]["ui_grup"] = "x"
        self.assertIn("did you mean 'ui_group'", self.only(doc, Code.E7).message)

    def test_unknown_meta_key_outside_ui_is_a_warning(self):
        doc = txt2img()
        doc["sampler"]["_meta"]["other_tool"] = "x"
        _, findings = self.passes(doc)
        self.assertEqual([(f.code, f.severity) for f in findings], [(Code.E7, Severity.WARNING)])

    def test_ui_seed_needs_a_control_widget(self):
        doc = txt2img()
        doc["decode"]["_meta"] = {"ui_seed": "fixed"}
        self.only(doc, Code.E7)


class FindingShapeTest(CheckCase):
    def test_every_finding_names_author_ids_and_carries_a_hint(self):
        doc = txt2img()
        doc["sampler"]["inputs"]["stepz"] = 1
        doc["decode"]["inputs"]["vae"] = ["nowhere", 0]
        del doc["pos"]["inputs"]["text"]
        found = self.errors(doc)
        self.assertEqual([f.code for f in found], [Code.E2, Code.E3, Code.E6])
        for finding in found:
            self.assertTrue(finding.hint)
            self.assertTrue(set(finding.nodes) <= set(doc))


if __name__ == "__main__":
    unittest.main()
