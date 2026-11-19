"""Rule 3: an intermediate preview the author put in a group stays in that group, while the
chain from its sampler to it (the decode) joins the sampler's group as for a free preview.
An author-grouped final output is left as it is."""

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img

from cwc.annotate import annotate
from cwc.annotate.types import Zone
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")


def ksampler(latent: str, denoise: float) -> dict:
    return node(
        "KSampler",
        {
            "model": ["ckpt", "MODEL"],
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "latent_image": [latent, 0],
            "seed": 1,
            "steps": 20,
            "cfg": 5.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": denoise,
        },
    )


def two_pass(*grouped: str, name: str = "Base out", save1: bool = True) -> dict:
    """Two-pass through a latent upscale; the first pass is decoded once into a save and a
    preview. ``grouped`` names the nodes put in the author group ``name``."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = ksampler("up", 0.45)
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    if save1:
        doc["save1"] = node("SaveImage", {"images": ["d1", 0], "filename_prefix": "base"})
    doc["p1"] = node("PreviewImage", {"images": ["d1", 0]})
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    for key in grouped:
        doc[key].setdefault("_meta", {})["ui_group"] = name
    return doc


class GroupedPreviewTest(unittest.TestCase):
    def build(self, doc):
        self.canvas, self.authoring = compiled(copy.deepcopy(doc))
        self.plan = annotate(self.canvas, DEFS, self.authoring)
        return self.plan

    def group(self, author_id):
        key = self.authoring.ids[author_id]
        return next(group for group in self.plan.groups if key in group.members)

    def assert_compiles(self, doc):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
                self.assertEqual(errors, [])

    def assert_decode_with_sampler(self, doc, author_group="Base out"):
        self.build(doc)
        self.assertEqual(self.group("d1").key, self.group("sampler").key)
        self.assertEqual(self.group("p1").key, f"group:{author_group}")
        self.assertIsNot(self.group("d1").zone, Zone.POSTPROCESS)
        self.assert_compiles(doc)

    def test_preview_alone_in_an_author_group(self):
        doc = two_pass("p1", save1=False)
        self.assert_decode_with_sampler(doc)
        self.assertNotEqual(self.group("p1").key, self.group("sampler").key)
        self.assertIs(self.group("sampler").zone, Zone.SAMPLING)

    def test_save_and_preview_in_an_author_group(self):
        doc = two_pass("save1", "p1")
        self.assert_decode_with_sampler(doc)
        self.assertEqual(self.group("save1").key, self.group("p1").key)
        self.assertNotEqual(self.group("p1").key, self.group("sampler").key)

    def test_samplers_and_first_outputs_in_one_author_group(self):
        doc = two_pass("sampler", "save1", "p1", "s2", name="Run")
        self.assert_decode_with_sampler(doc, author_group="Run")
        self.assertEqual(self.group("p1").key, self.group("sampler").key)

    def test_free_preview_beside_a_grouped_one_on_the_same_chain(self):
        # The same result as with the free preview alone: the decode joins the sampler.
        doc = two_pass("p1", save1=False)
        doc["p2"] = node("PreviewImage", {"images": ["d1", 0]})
        self.assert_decode_with_sampler(doc)
        self.assertEqual(self.group("p2").key, self.group("sampler").key)

    def test_free_preview_control_is_unchanged(self):
        doc = two_pass(save1=False)
        self.build(doc)
        sampling = self.group("sampler")
        self.assertIs(sampling.zone, Zone.SAMPLING)
        for author_id in ("d1", "p1"):
            self.assertEqual(self.group(author_id).key, sampling.key, author_id)
        self.assert_compiles(doc)

    def test_grouped_final_output_is_unchanged(self):
        # The final save has no later sampler: the author's group keeps it, and its decode
        # stays in post-processing.
        doc = two_pass("save", save1=False)
        self.build(doc)
        self.assertEqual(self.group("save").key, "group:Base out")
        self.assertIs(self.group("decode").zone, Zone.POSTPROCESS)
        self.assertNotEqual(self.group("decode").key, self.group("s2").key)
        self.assertEqual(self.group("d1").key, self.group("sampler").key)
        self.assert_compiles(doc)


if __name__ == "__main__":
    unittest.main()
