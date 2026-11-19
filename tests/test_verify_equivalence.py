import copy
import json
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.normalize import normalize
from cwc.report import Code, Mode, Severity
from cwc.verify import verify_canvas, verify_compile
from tests.fixtures.normalize.registry import load_doc, load_registry
from tests.fixtures.prompt.build import CanvasBuilder
from tests.fixtures.verify.canvases import BYPASS, DEFS, add_group, authoring, node, txt2img


def verify(canvas, auth=None):
    return verify_compile(canvas, auth or authoring(), DEFS)


def found(report, code):
    return [f for f in report.findings if f.code is code]


class EquivalenceTest(unittest.TestCase):
    def test_equal_prompts(self):
        report = verify(txt2img())
        self.assertEqual(found(report, Code.Q1) + found(report, Code.Q2), [])

    def test_optional_widget_changes_only_the_full_prompt(self):
        canvas = txt2img()
        node(canvas, 2)["widgets_values"] = ["l.safetensors", 0.5]
        report = verify(canvas)
        [finding] = found(report, Code.Q1)
        self.assertIs(finding.severity, Severity.ERROR)
        self.assertEqual(finding.data, {"diff": ["node 2 input 'strength': 0.8 != 0.5"]})
        self.assertEqual(found(report, Code.Q2), [])

    def test_enabled_optional_node_changes_only_the_factory_prompt(self):
        report = verify(txt2img({8: 0}))
        self.assertEqual(found(report, Code.Q1), [])
        [finding] = found(report, Code.Q2)
        self.assertEqual(
            finding.data["diff"],
            ["node 8 unexpected", "node 9 input 'images': ['7', 0] != ['8', 0]"],
        )

    def test_shared_change_shows_in_both(self):
        canvas = txt2img()
        node(canvas, 3)["widgets_values"] = ["a dog"]
        report = verify(canvas)
        self.assertEqual(len(found(report, Code.Q1)), 1)
        self.assertEqual(len(found(report, Code.Q2)), 1)

    def test_export_failure_is_reported_and_closure_skipped(self):
        canvas = txt2img()
        canvas.raw["definitions"] = {"subgraphs": [{"id": "TestDecode"}]}
        report = verify(canvas)
        for code in (Code.Q1, Code.Q2):
            [finding] = found(report, code)
            self.assertIn("cannot be exported", finding.message)
            self.assertIn("subgraph", finding.message)
        self.assertEqual(found(report, Code.B1) + found(report, Code.B2), [])


class NormalizedAuthoringTest(unittest.TestCase):
    """Authoring from the normalize fixture; the canvas written by hand to match it."""

    def setUp(self):
        self.registry = load_registry()
        doc = load_doc("txt2img.json")
        doc["lora"] = {
            "class_type": "LoraLoader",
            "inputs": {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "style.safetensors"},
            "_meta": {"ui_optional": True},
        }
        doc["upscale"] = {
            "class_type": "ImageScaleBy",
            "inputs": {"image": ["decode", "IMAGE"], "upscale_method": "lanczos", "scale_by": 1.5},
            "_meta": {"ui_optional": True},
        }
        doc["sampler"]["inputs"]["model"] = ["lora", "MODEL"]
        doc["pos"]["inputs"]["clip"] = ["lora", "CLIP"]
        doc["neg"]["inputs"]["clip"] = ["lora", 1]
        doc["save"]["inputs"]["images"] = ["upscale", 0]
        self.authoring, warnings = normalize(doc, self.registry)
        self.assertEqual(warnings, [])

    def canvas(self):
        i = {name: int(key) for name, key in self.authoring.ids.items()}
        b = CanvasBuilder(self.registry)
        b.add(i["ckpt"], "CheckpointLoaderSimple", widgets=["base.safetensors"])
        b.add(i["lora"], "LoraLoader", widgets=["style.safetensors", 1.0, 1.0], mode=BYPASS)
        b.add(i["pos"], "CLIPTextEncode", widgets=["a cat"])
        b.add(i["neg"], "CLIPTextEncode", widgets=["blurry, lowres"])
        b.add(i["latent"], "EmptyLatentImage", widgets=[1024, 1024, 1])
        b.add(i["sampler"], "KSampler", widgets=[0, "randomize", 28, 4.0, "euler", "normal", 1.0])
        b.add(i["decode"], "VAEDecode")
        b.add(i["upscale"], "ImageScaleBy", widgets=["lanczos", 1.5], mode=BYPASS)
        b.add(i["save"], "SaveImage", widgets=["cwc_demo"])
        b.link(i["ckpt"], 0, i["lora"], "model")
        b.link(i["ckpt"], 1, i["lora"], "clip")
        b.link(i["lora"], 1, i["pos"], "clip")
        b.link(i["lora"], 1, i["neg"], "clip")
        b.link(i["lora"], 0, i["sampler"], "model")
        b.link(i["pos"], 0, i["sampler"], "positive")
        b.link(i["neg"], 0, i["sampler"], "negative")
        b.link(i["latent"], 0, i["sampler"], "latent_image")
        b.link(i["sampler"], 0, i["decode"], "samples")
        b.link(i["ckpt"], 2, i["decode"], "vae")
        b.link(i["decode"], 0, i["upscale"], "image")
        b.link(i["upscale"], 0, i["save"], "images")
        return b.canvas()

    def test_matching_canvas_passes(self):
        report = verify_compile(self.canvas(), self.authoring, self.registry)
        self.assertEqual(report.findings, [])
        self.assertEqual(report.metrics["tiers"], {"derived": 9})

    def test_wrong_widget_value_is_caught(self):
        canvas = self.canvas()
        node(canvas, int(self.authoring.ids["sampler"]))["widgets_values"][2] = 30
        report = verify_compile(canvas, self.authoring, self.registry)
        self.assertEqual([f.code for f in report.findings], [Code.Q1, Code.Q2])


class ReportTest(unittest.TestCase):
    def messy(self):
        canvas = txt2img({5: BYPASS, 2: 0})
        node(canvas, 3)["widgets_values"] = ["a dog"]
        canvas.raw["last_link_id"] = 99
        add_group(canvas, "nothing", [5000, 5000, 100, 100])
        return canvas

    def test_metrics_and_tiers(self):
        b = CanvasBuilder(DEFS)
        b.add(1, "TestNote", ins=[], outs=[], widgets=["x"])
        b.add(2, "SomethingElse", ins=[], outs=[])
        report = verify_canvas(b.canvas(), DEFS, mode=Mode.CHECK)
        self.assertEqual(report.nodedef_tiers, {"1": "builtin", "2": "unknown"})
        self.assertEqual(report.metrics, {"nodes": 2, "links": 0, "groups": 0, "tiers": {"builtin": 1, "unknown": 1}})
        full = verify(txt2img())
        self.assertEqual(full.metrics, {"nodes": 9, "links": 12, "groups": 5, "tiers": {"truth": 9}})

    def test_report_json_is_deterministic(self):
        first = json.dumps(verify(self.messy()).to_json(), sort_keys=False)
        self.assertEqual(first, json.dumps(verify(self.messy()).to_json(), sort_keys=False))
        shuffled = self.messy()
        shuffled.raw["nodes"].reverse()
        shuffled.raw["links"].reverse()
        self.assertEqual(first, json.dumps(verify(shuffled).to_json(), sort_keys=False))
        codes = [f["code"] for f in json.loads(first)["findings"]]
        self.assertEqual(codes, ["S5", "B1", "B3", "B3", "Q1", "Q2"])

    def test_verify_does_not_change_the_canvas(self):
        canvas = self.messy()
        before = copy.deepcopy(canvas.raw)
        verify(canvas)
        verify_canvas(canvas, DEFS, mode=Mode.BEAUTIFY)
        self.assertEqual(canvas.raw, before)


if __name__ == "__main__":
    unittest.main()
