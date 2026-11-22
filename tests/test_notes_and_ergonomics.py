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


class GalleryAndCollapsedTest(unittest.TestCase):
    def test_gallery_docks_intermediate_step_preview_and_keeps_final_in_band0(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["_workflow"] = {
            "format": 1,
            "title": "2-Stage Preview Demo",
            "layout": {"preset": "gallery"},
            "groups": {
                "stage1": {"title": "Stage 1"},
                "s1_prev": {"title": "Layout Preview"},
                "stage2": {"title": "Stage 2"},
                "final_out": {"title": "Final Output"},
            },
        }
        doc["sampler"]["_meta"] = {"ui_group": "stage1"}
        doc["decode"]["_meta"] = {"ui_group": "stage1", "ui_collapsed": True}
        doc["preview_s1"] = {
            "class_type": "PreviewImage",
            "inputs": {"images": ["decode", 0]},
            "_meta": {"title": "Layout Preview", "ui_group": "s1_prev"},
        }
        doc["sampler2"] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["ckpt", "MODEL"],
                "positive": ["pos", 0],
                "negative": ["neg", 0],
                "latent_image": ["sampler", 0],
                "seed": 43,
                "steps": 10,
                "cfg": 2.5,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.4,
            },
            "_meta": {"ui_group": "stage2"},
        }
        doc["decode2"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sampler2", 0], "vae": ["ckpt", "VAE"]},
            "_meta": {"ui_group": "stage2", "ui_collapsed": True},
        }
        doc["save"]["inputs"]["images"] = ["decode2", 0]
        doc["save"]["_meta"] = {"title": "Final Save", "ui_group": "final_out"}

        defs = NodeDefs.load()
        compiled = compile_document(doc, defs)
        self.assertTrue(compiled.report.ok, compiled.report.findings)
        # ui_collapsed collapses named-group boilerplate nodes without removing them from their group
        decodes = [n for n in compiled.canvas.nodes if n.type == "VAEDecode"]
        self.assertTrue(all(n.collapsed for n in decodes))
        # Intermediate Layout Preview docks in Band 1 below Band 0, while Final Output stays at y = 0
        by_title = {g["title"]: g["bounding"] for g in compiled.canvas.raw["groups"]}
        step1_box = next(b for t, b in by_title.items() if "Layout Preview" in t)
        final_box = next(b for t, b in by_title.items() if "Final Output" in t)
        self.assertEqual(final_box[1], 0)
        self.assertGreater(step1_box[1], final_box[1])
        # Both previews get enlarged classic heights and render IMAGE PREVIEW viewports under --values
        for n in compiled.canvas.nodes:
            if n.type in ("PreviewImage", "SaveImage"):
                self.assertGreaterEqual(n.size[1], 300)
        svg_vals = render_svg(compiled.canvas, defs, show_values=True)
        self.assertEqual(svg_vals.count("IMAGE PREVIEW"), 2)

    def test_gallery_splits_tall_cockpit_horizontally_and_tucks_lower_band(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["_workflow"] = {"format": 1, "layout": {"preset": "gallery"}}
        # Add 3 extra cockpit knobs (total 6 >= min_nodes 5) plus an optional module
        for idx, name in enumerate(("k1", "k2", "k3"), start=1):
            doc[name] = {
                "class_type": "PrimitiveInt",
                "inputs": {"value": 10 + idx},
                "_meta": {"title": f"Knob {idx}", "ui_role": "cockpit", "ui_order": idx},
            }
        doc["sampler"]["inputs"]["steps"] = ["k1", 0]
        doc["latent"]["inputs"]["batch_size"] = ["k2", 0]
        doc["sampler"]["inputs"]["seed"] = ["k3", 0]
        doc["opt_lora"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["ckpt", "MODEL"],
                "clip": ["ckpt", "CLIP"],
                "lora_name": "style.safetensors",
                "strength_model": 0.5,
                "strength_clip": 0.5,
            },
            "_meta": {"title": "Opt LoRA", "ui_optional": True},
        }
        doc["sampler"]["inputs"]["model"] = ["opt_lora", "MODEL"]
        compiled = compile_document(doc, NodeDefs.load(), portable=True)
        self.assertTrue(compiled.report.ok, compiled.report.findings)
        ctrl_boxes = [g["bounding"] for g in compiled.canvas.raw["groups"] if "Controls" in g["title"]]
        self.assertEqual(len(ctrl_boxes), 2)
        # Side-by-side cockpit split: same y=0, distinct x
        self.assertEqual(ctrl_boxes[0][1], ctrl_boxes[1][1])
        self.assertNotEqual(ctrl_boxes[0][0], ctrl_boxes[1][0])

    def test_stacked_previews_in_fill_group_all_get_uniform_classic_height(self) -> None:
        doc = copy.deepcopy(PLAIN)
        doc["_workflow"] = {"format": 1, "layout": {"preset": "gallery"}}
        doc["peek"] = {"class_type": "PreviewImage", "inputs": {"images": ["decode", 0]}}
        compiled = compile_document(doc, NodeDefs.load())
        self.assertTrue(compiled.report.ok, compiled.report.findings)
        heights = [n.size[1] for n in compiled.canvas.nodes if n.type in ("PreviewImage", "SaveImage")]
        self.assertEqual(len(heights), 2)
        self.assertTrue(all(h >= 300 for h in heights), heights)


if __name__ == "__main__":
    unittest.main()
