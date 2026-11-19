import copy
import dataclasses
import json
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.normalize import normalize
from cwc.normalize.types import NodeIntent, Role, SeedMode
from cwc.report import Code, CwcError, Severity
from tests.fixtures.normalize.registry import load_doc, load_registry

REGISTRY = load_registry()


def run(doc, **kwargs):
    return normalize(doc, REGISTRY, **kwargs)


def txt2img():
    return load_doc("txt2img.json")


def with_optional_modules():
    """txt2img plus a LoRA between checkpoint and sampler and an upscale before saving, both optional."""
    doc = txt2img()
    doc["lora"] = {
        "class_type": "LoraLoader",
        "inputs": {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "style.safetensors"},
        "_meta": {"ui_optional": True, "ui_group": "extras"},
    }
    doc["upscale"] = {
        "class_type": "ImageScaleBy",
        "inputs": {"image": ["decode", "IMAGE"], "upscale_method": "lanczos", "scale_by": 1.5},
        "_meta": {"ui_optional": True, "ui_group": "extras"},
    }
    doc["sampler"]["inputs"]["model"] = ["lora", "MODEL"]
    doc["pos"]["inputs"]["clip"] = ["lora", "CLIP"]
    doc["neg"]["inputs"]["clip"] = ["lora", 1]
    doc["save"]["inputs"]["images"] = ["upscale", 0]
    return doc


class MinimalExampleTest(unittest.TestCase):
    def setUp(self):
        self.authoring, self.warnings = run(txt2img())
        self.ids = self.authoring.ids

    def node(self, author_id, prompt=None):
        return (prompt or self.authoring.full)[self.ids[author_id]]

    def test_passes_without_findings(self):
        self.assertEqual(self.warnings, [])
        self.assertEqual(len(self.authoring.full), 7)

    def test_symbolic_ids_follow_sorted_order(self):
        expected = ["ckpt", "decode", "latent", "neg", "pos", "sampler", "save"]
        self.assertEqual(self.ids, {key: str(i + 1) for i, key in enumerate(expected)})
        self.assertEqual(list(self.authoring.full), [str(i) for i in range(1, 8)])

    def test_defaults_are_filled(self):
        inputs = self.node("sampler")["inputs"]
        self.assertEqual(inputs["seed"], 0)
        self.assertEqual(inputs["denoise"], 1.0)
        self.assertEqual(inputs["steps"], 28)

    def test_links_use_numeric_ids_and_output_indexes(self):
        sampler = self.node("sampler")["inputs"]
        self.assertEqual(sampler["model"], [self.ids["ckpt"], 0])
        self.assertEqual(sampler["positive"], [self.ids["pos"], 0])
        self.assertEqual(self.node("decode")["inputs"]["vae"], [self.ids["ckpt"], 2])
        self.assertEqual(self.node("pos")["inputs"]["clip"], [self.ids["ckpt"], 1])

    def test_prompt_nodes_carry_only_title_meta(self):
        for entry in self.authoring.full.values():
            self.assertEqual(list(entry), ["inputs", "class_type", "_meta"])
            self.assertEqual(list(entry["_meta"]), ["title"])
        self.assertEqual(self.node("sampler")["_meta"]["title"], "Main sampler")
        self.assertEqual(self.node("decode")["_meta"]["title"], "VAE Decode")  # display name
        self.assertNotIn("_workflow", self.authoring.full)

    def test_widget_inputs_come_first_in_widget_order(self):
        keys = list(self.node("sampler")["inputs"])
        self.assertEqual(keys[:6], ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"])
        self.assertEqual(keys[6:], ["model", "positive", "negative", "latent_image"])

    def test_widget_values_hold_every_persisted_widget(self):
        values = self.authoring.widget_values[self.ids["sampler"]]
        self.assertEqual(
            values,
            {"seed": 0, "control_after_generate": "randomize", "steps": 28, "cfg": 4.0,
             "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0},
        )
        self.assertEqual(self.authoring.widget_values[self.ids["decode"]], {})

    def test_intents_and_workflow(self):
        intent = self.authoring.intents[self.ids["sampler"]]
        self.assertEqual(intent.ui_locked, ("cfg",))
        self.assertEqual(intent.title, "Main sampler")
        self.assertIs(self.authoring.intents[self.ids["pos"]].ui_role, Role.COCKPIT)
        self.assertEqual(self.authoring.intents[self.ids["decode"]], NodeIntent())
        workflow = self.authoring.workflow
        self.assertEqual((workflow.title, workflow.locale, workflow.preset), ("Minimal text to image", "en", "bento"))

    def test_defs_by_numeric_id(self):
        self.assertEqual(self.authoring.defs[self.ids["sampler"]].type, "KSampler")
        self.assertEqual(set(self.authoring.defs), set(self.authoring.full))

    def test_factory_equals_full_without_optional_nodes(self):
        self.assertEqual(self.authoring.factory, self.authoring.full)

    def test_strict_prompt_is_a_fixed_point(self):
        again, findings = run(copy.deepcopy(self.authoring.full))
        self.assertEqual(findings, [])
        self.assertEqual(again.full, self.authoring.full)
        self.assertEqual(again.ids, {key: key for key in self.authoring.full})

    def test_output_is_deterministic_and_input_untouched(self):
        doc = txt2img()
        before = copy.deepcopy(doc)
        first, _ = run(doc)
        self.assertEqual(doc, before)
        reordered = dict(reversed(list(txt2img().items())))
        second, _ = run(reordered)
        self.assertEqual(json.dumps(first.full), json.dumps(second.full))
        self.assertEqual(first.ids, second.ids)


class IdAssignmentTest(unittest.TestCase):
    def doc(self, *keys):
        return {key: {"class_type": "PreviewImage", "inputs": {"images": ["img", 0]}} for key in keys} | {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
        }

    def test_numeric_ids_are_kept_and_symbolic_ones_follow_the_maximum(self):
        authoring, _ = run(self.doc("12", "b", "a", "3"))
        self.assertEqual(authoring.ids, {"3": "3", "12": "12", "a": "13", "b": "14", "img": "15"})
        self.assertEqual(authoring.full["12"]["inputs"]["images"], ["15", 0])

    def test_without_numeric_ids_numbering_starts_at_one(self):
        authoring, _ = run(self.doc("x"))
        self.assertEqual(authoring.ids, {"img": "1", "x": "2"})

    def test_assignment_ignores_document_order(self):
        a, _ = run(self.doc("zeta", "alpha", "7"))
        b, _ = run(dict(reversed(list(self.doc("zeta", "alpha", "7").items()))))
        self.assertEqual(a.ids, b.ids)
        self.assertEqual(a.full, b.full)


class OutputByNameTest(unittest.TestCase):
    def test_names_and_indexes_resolve_to_the_same_link(self):
        doc = txt2img()
        doc["sampler"]["inputs"]["model"] = ["ckpt", 0]
        doc["decode"]["inputs"]["vae"] = ["ckpt", "VAE"]
        by_index, _ = run(doc)
        by_name, _ = run(txt2img())
        self.assertEqual(by_index.full, by_name.full)

    def test_output_named_unlike_its_type_resolves_by_name(self):
        doc = txt2img()
        doc["ref"] = {"class_type": "LoadImage", "inputs": {"image": "ref.png"}}
        doc["size"] = {"class_type": "GetImageSize", "inputs": {"image": ["ref", "IMAGE"]}}
        doc["latent"]["inputs"]["height"] = ["size", "height"]
        authoring, _ = run(doc)
        self.assertEqual(authoring.full[authoring.ids["latent"]]["inputs"]["height"], [authoring.ids["size"], 1])


class LinkedWidgetTest(unittest.TestCase):
    def test_linked_widget_takes_the_link_and_keeps_its_default_value(self):
        doc = txt2img()
        doc["seed"] = {"class_type": "PrimitiveInt", "inputs": {"value": 42}}
        doc["sampler"]["inputs"]["seed"] = ["seed", "INT"]
        authoring, _ = run(doc)
        sampler = authoring.ids["sampler"]
        self.assertEqual(authoring.full[sampler]["inputs"]["seed"], [authoring.ids["seed"], 0])
        self.assertEqual(authoring.widget_values[sampler]["seed"], 0)
        self.assertEqual(list(authoring.full[sampler]["inputs"])[0], "seed")


class SeedIntentTest(unittest.TestCase):
    def test_ui_seed_sets_the_control_widget_only(self):
        doc = txt2img()
        doc["sampler"]["_meta"]["ui_seed"] = "fixed"
        authoring, _ = run(doc)
        sampler = authoring.ids["sampler"]
        self.assertEqual(authoring.widget_values[sampler]["control_after_generate"], "fixed")
        self.assertIs(authoring.intents[sampler].ui_seed, SeedMode.FIXED)
        self.assertNotIn("control_after_generate", authoring.full[sampler]["inputs"])
        self.assertEqual(authoring.full[sampler]["_meta"], {"title": "Main sampler"})

    def test_ui_locked_true_locks_everything(self):
        doc = txt2img()
        doc["sampler"]["_meta"]["ui_locked"] = True
        authoring, _ = run(doc)
        self.assertEqual(authoring.intents[authoring.ids["sampler"]].ui_locked, ("*",))


class ValueWrapperTest(unittest.TestCase):
    def test_list_values_are_wrapped_in_the_prompt_and_bare_in_widget_values(self):
        doc = {
            "img": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "crop": {"class_type": "ImageCropV2", "inputs": {"image": ["img", 0], "crop_region": {"__value__": [0, 0, 64, 64]}}},
        }
        authoring, _ = run(doc)
        crop = authoring.ids["crop"]
        self.assertEqual(authoring.full[crop]["inputs"]["crop_region"], {"__value__": [0, 0, 64, 64]})
        self.assertEqual(authoring.widget_values[crop]["crop_region"], [0, 0, 64, 64])

    def test_unknown_node_keeps_the_wrapper_as_written(self):
        doc = {"x": {"class_type": "SomePackNode", "inputs": {"items": {"__value__": ["a", "b"]}, "count": 3}}}
        authoring, _ = run(doc, allow_unknown=True)
        self.assertEqual(authoring.full["1"]["inputs"], {"items": {"__value__": ["a", "b"]}, "count": 3})


class CompanionWidgetTest(unittest.TestCase):
    def test_companion_widget_that_enters_the_prompt_is_filled_and_accepted(self):
        authoring, _ = run({"img": {"class_type": "LoadImageOutput", "inputs": {"image": "a.png"}}})
        self.assertEqual(authoring.full["1"]["inputs"], {"image": "a.png", "refresh": "refresh"})
        self.assertEqual(authoring.widget_values["1"]["upload"], "image")
        again, findings = run(copy.deepcopy(authoring.full))
        self.assertEqual((again.full, findings), (authoring.full, []))


class OptionalModuleTest(unittest.TestCase):
    def setUp(self):
        self.authoring, self.findings = run(with_optional_modules())
        self.ids = self.authoring.ids

    def test_full_keeps_the_optional_nodes(self):
        full = self.authoring.full
        self.assertIn(self.ids["lora"], full)
        self.assertEqual(full[self.ids["sampler"]]["inputs"]["model"], [self.ids["lora"], 0])
        self.assertEqual(full[self.ids["save"]]["inputs"]["images"], [self.ids["upscale"], 0])

    def test_factory_bypasses_them(self):
        factory = self.authoring.factory
        ids = self.ids
        self.assertNotIn(ids["lora"], factory)
        self.assertNotIn(ids["upscale"], factory)
        self.assertEqual(factory[ids["sampler"]]["inputs"]["model"], [ids["ckpt"], 0])
        self.assertEqual(factory[ids["pos"]]["inputs"]["clip"], [ids["ckpt"], 1])
        self.assertEqual(factory[ids["neg"]]["inputs"]["clip"], [ids["ckpt"], 1])
        self.assertEqual(factory[ids["save"]]["inputs"]["images"], [ids["decode"], 0])
        self.assertEqual(len(factory), len(self.authoring.full) - 2)

    def test_intents_mark_them_optional(self):
        self.assertTrue(self.authoring.intents[self.ids["lora"]].ui_optional)
        self.assertEqual(self.findings, [])


class AllowUnknownTest(unittest.TestCase):
    def doc(self):
        doc = txt2img()
        doc["post"] = {"class_type": "PackImageFilter", "inputs": {"image": ["decode", "IMAGE"], "amount": 0.5}}
        doc["save"]["inputs"]["images"] = ["post", 0]
        return doc

    def test_without_the_flag_the_type_is_rejected(self):
        with self.assertRaises(CwcError):
            run(self.doc())

    def test_with_the_flag_the_node_passes_as_written(self):
        authoring, findings = run(self.doc(), allow_unknown=True)
        post = authoring.ids["post"]
        self.assertEqual(authoring.full[post]["inputs"], {"image": [authoring.ids["decode"], 0], "amount": 0.5})
        self.assertEqual(authoring.full[post]["_meta"], {"title": "PackImageFilter"})
        self.assertNotIn(post, authoring.defs)
        self.assertNotIn(post, authoring.widget_values)
        self.assertEqual([(f.code, f.severity) for f in findings], [(Code.E1, Severity.WARNING)])
        self.assertIn("exported values are not verified", findings[0].message)
        self.assertEqual(authoring.full[authoring.ids["save"]]["inputs"]["images"], [post, 0])

    def test_outputs_of_unknown_nodes_need_an_index(self):
        doc = self.doc()
        doc["save"]["inputs"]["images"] = ["post", "IMAGE"]
        with self.assertRaises(CwcError) as caught:
            run(doc, allow_unknown=True)
        self.assertEqual([f.code for f in caught.exception.findings if f.severity is Severity.ERROR], [Code.E6])

    def test_unknown_nodes_cannot_be_optional(self):
        doc = self.doc()
        doc["post"]["_meta"] = {"ui_optional": True}
        with self.assertRaises(CwcError) as caught:
            run(doc, allow_unknown=True)
        self.assertIn(Code.E1, [f.code for f in caught.exception.findings if f.severity is Severity.ERROR])


class IncompleteLayoutTest(unittest.TestCase):
    def registry(self):
        sampler = dataclasses.replace(REGISTRY.get("KSampler"), layout_complete=False)
        return REGISTRY.with_def(sampler)

    def test_omitted_default_on_unreliable_layout_is_an_error(self):
        with self.assertRaises(CwcError) as caught:
            normalize(txt2img(), self.registry())
        findings = caught.exception.findings
        self.assertEqual({f.code for f in findings}, {Code.E3})
        self.assertEqual(sorted(f.message.split("'")[3] for f in findings), ["denoise", "seed"])
        self.assertIn("cwc snapshot", findings[0].hint)

    def test_writing_every_widget_input_passes(self):
        doc = txt2img()
        doc["sampler"]["inputs"].update({"seed": 1, "denoise": 0.5})
        authoring, _ = normalize(doc, self.registry())
        self.assertEqual(authoring.full[authoring.ids["sampler"]]["inputs"]["denoise"], 0.5)


if __name__ == "__main__":
    unittest.main()
