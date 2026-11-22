"""Tests for M3 in-canvas guide notes, --portable file combo mode, cockpit ui_order, and render --values."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tests import support as _support  # noqa: F401

from cwc.cli import main
from cwc.nodedefs.registry import NodeDefs
from cwc.pipeline import compile_document
from cwc.render.svg import render_svg
from cwc.report import ExitCode

PLAIN = {
    "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
    "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ckpt", "CLIP"], "text": "cat"}},
    "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ckpt", "CLIP"], "text": "blur"}},
    "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "sampler": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["ckpt", "MODEL"],
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "latent_image": ["latent", 0],
            "seed": 42,
            "steps": 25,
            "cfg": 2.5,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 0.72,
        },
    },
    "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]}},
    "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "out"}},
}


class GuideNotesTest(unittest.TestCase):
    def test_title_and_notes_emit_c5_g8_verified_markdown_guide(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["_workflow"] = {
            "format": 1,
            "title": "Simple Demo",
            "groups": {"finish": {"title": "Finish", "note": "Saves the final PNG."}},
        }
        doc["decode"]["_meta"] = {"ui_group": "finish"}
        doc["sampler"]["_meta"] = {"ui_note": "Keep cfg near 2.5 for clean colors."}
        compiled = compile_document(doc, NodeDefs.load())
        self.assertTrue(compiled.report.ok, compiled.report.findings)
        guides = [n for n in compiled.canvas.nodes if n.properties.get("cwc_role") == "guide"]
        self.assertEqual(len(guides), 1)
        guide = guides[0]
        self.assertEqual(guide.type, "MarkdownNote")
        text = guide.widgets_values[0]
        self.assertIn("# Simple Demo", text)
        self.assertIn("Saves the final PNG.", text)
        self.assertIn("Keep cfg near 2.5 for clean colors.", text)
        geom = compiled.report.metrics["geometry"]
        self.assertEqual(geom["C5"]["guides"], 1)
        self.assertEqual(geom["C5"]["too_tall"], 0)
        self.assertEqual(geom["G8"]["guides"], 1)

    def test_plain_graph_without_title_or_notes_adds_no_guide(self) -> None:
        compiled = compile_document(copy.deepcopy(PLAIN), NodeDefs.load())
        self.assertTrue(compiled.report.ok)
        guides = [n for n in compiled.canvas.nodes if n.properties.get("cwc_role") == "guide"]
        self.assertEqual(guides, [])

    def test_long_notes_still_pass_c5_and_g8(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["_workflow"] = {"format": 1, "title": "A" * 120}
        doc["sampler"]["_meta"] = {"ui_note": " ".join(f"tip{i}" for i in range(200))}
        compiled = compile_document(doc, NodeDefs.load())
        self.assertTrue(compiled.report.ok, compiled.report.findings)
        self.assertEqual(compiled.report.metrics["geometry"]["C5"]["too_tall"], 0)


class CockpitOrderTest(unittest.TestCase):
    def test_explicit_ui_order_keeps_size_knob_after_x_and_y(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["x_in"] = {"class_type": "PrimitiveInt", "inputs": {"value": 25}, "_meta": {"title": "Crop X", "ui_role": "cockpit", "ui_order": 1}}
        doc["y_in"] = {"class_type": "PrimitiveFloat", "inputs": {"value": 2.5}, "_meta": {"title": "Crop Y", "ui_role": "cockpit", "ui_order": 2}}
        doc["sz_in"] = {"class_type": "PrimitiveInt", "inputs": {"value": 512}, "_meta": {"title": "Crop Size", "ui_role": "cockpit", "ui_order": 3}}
        doc["latent"]["inputs"]["width"] = ["sz_in", 0]
        doc["latent"]["inputs"]["height"] = ["sz_in", 0]
        doc["sampler"]["inputs"]["steps"] = ["x_in", 0]
        doc["sampler"]["inputs"]["cfg"] = ["y_in", 0]
        compiled = compile_document(doc, NodeDefs.load())
        self.assertTrue(compiled.report.ok, compiled.report.findings)
        by_title = {n.title: n.pos[1] for n in compiled.canvas.nodes if n.title in ("Crop X", "Crop Y", "Crop Size")}
        self.assertLess(by_title["Crop X"], by_title["Crop Y"])
        self.assertLess(by_title["Crop Y"], by_title["Crop Size"])


class PortableFlagTest(unittest.TestCase):
    def test_portable_suppresses_file_combo_warnings_but_keeps_enum_warnings(self) -> None:
        fake_obj = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["local_only_model.safetensors"]]}},
                "output": ["MODEL", "CLIP", "VAE"],
                "output_name": ["MODEL", "CLIP", "VAE"],
                "name": "CheckpointLoaderSimple",
                "display_name": "Load Checkpoint",
                "category": "loaders",
            }
        }
        defs = NodeDefs.load(object_info=fake_obj)
        c_strict = compile_document(copy.deepcopy(PLAIN), defs, portable=False)
        c_port = compile_document(copy.deepcopy(PLAIN), defs, portable=True)
        self.assertTrue(any(f.code.value == "E5" for f in c_strict.report.findings))
        self.assertFalse(any(f.code.value == "E5" for f in c_port.report.findings))

        bad_enum = copy.deepcopy(PLAIN)
        bad_enum["sampler"]["inputs"]["sampler_name"] = "nonexistent_sampler_xyz"
        c_bad = compile_document(bad_enum, defs, portable=True)
        self.assertTrue(any(f.code.value == "E5" for f in c_bad.report.findings))


class RenderValuesAndNoteTest(unittest.TestCase):
    def test_render_draws_guide_text_and_optional_widget_values(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["_workflow"] = {"format": 1, "title": "Render Value Check"}
        defs = NodeDefs.load()
        compiled = compile_document(doc, defs)
        svg_plain = render_svg(compiled.canvas, defs, show_values=False)
        svg_vals = render_svg(compiled.canvas, defs, show_values=True)
        self.assertIn("Render Value Check", svg_plain)
        self.assertIn("note-line", svg_plain)
        self.assertNotIn("denoise: 0.72", svg_plain)
        self.assertIn("denoise: 0.72", svg_vals)
        self.assertIn("steps: 25", svg_vals)
        ET.fromstring(svg_vals.encode("utf-8"))

    def test_cli_portable_and_render_values_flags(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["_workflow"] = {"format": 1, "title": "CLI Roundtrip"}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            g = p / "demo.json"
            g.write_text(json.dumps(doc), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            rc = main(["compile", str(g), "-o", str(p / "out"), "--portable"], out, err)
            self.assertEqual(rc, ExitCode.OK, err.getvalue())
            out2, err2 = io.StringIO(), io.StringIO()
            rc2 = main(["render", str(p / "out" / "demo.ui.json"), "-o", str(p / "demo.svg"), "--values"], out2, err2)
            self.assertEqual(rc2, ExitCode.OK, err2.getvalue())
            self.assertIn("denoise: 0.72", (p / "demo.svg").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
