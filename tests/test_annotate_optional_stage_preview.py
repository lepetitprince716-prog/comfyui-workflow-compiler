"""Rule 3: an output node the author put in a group, left by rule 3 as a final output (the
sampler before it is optional, so no sampler group is known for it), has the output zone as its
own, and its group takes its zone from its members' own zones by the group rule (the latest
zone, so an output makes the group an output group). One kept beside a sampler keeps the zone the
rules give it without intents, and a final save keeps the output zone."""

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


def three_stage(*grouped: str, optional_middle: bool, name: str = "Peek") -> dict:
    """Three samplers through latent upscales, each of the first two decoded into a preview;
    ``grouped`` names the nodes put in the author group ``name``."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = ksampler("up", 0.45)
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["p1"] = node("PreviewImage", {"images": ["d1", 0]})
    doc["up2"] = node("LatentUpscaleBy", {"samples": ["s2", 0], "upscale_method": "nearest-exact", "scale_by": 1.25})
    doc["s3"] = ksampler("up2", 0.3)
    doc["d2"] = node("VAEDecode", {"samples": ["s2", 0], "vae": ["ckpt", "VAE"]})
    doc["p2"] = node("PreviewImage", {"images": ["d2", 0]})
    doc["decode"]["inputs"]["samples"] = ["s3", 0]
    if optional_middle:
        doc["s2"].setdefault("_meta", {})["ui_optional"] = True
    for key in grouped:
        doc[key].setdefault("_meta", {})["ui_group"] = name
    return doc


class OptionalStagePreviewTest(unittest.TestCase):
    def build(self, doc):
        self.canvas, self.authoring = compiled(copy.deepcopy(doc))
        self.plan = annotate(self.canvas, DEFS, self.authoring)

    def group(self, author_id):
        key = self.authoring.ids[author_id]
        return next(group for group in self.plan.groups if key in group.members)

    def zone(self, author_id):
        return self.plan.nodes[self.authoring.ids[author_id]].zone

    def test_preview_after_an_optional_sampler_is_an_output(self):
        self.build(three_stage("p2", optional_middle=True))
        self.assertIs(self.zone("p2"), Zone.OUTPUT)
        self.assertEqual(self.group("p2").key, "group:Peek")
        self.assertIs(self.group("p2").zone, Zone.OUTPUT)
        self.assertIs(self.group("d2").zone, Zone.POSTPROCESS)

    def test_the_group_follows_the_post_processing_group(self):
        doc = three_stage("p2", optional_middle=True)
        out = compile_document(copy.deepcopy(doc), DEFS, preset="bento")
        errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
        self.assertEqual(errors, [])
        raw = out.canvas.raw
        start = {group["title"].split(" ", 1)[1]: group["bounding"][0] for group in raw["groups"]}
        self.assertGreaterEqual(start["Peek"], start["Post-processing"])

    def test_every_preset_compiles(self):
        doc = three_stage("p2", optional_middle=True)
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
                self.assertEqual(errors, [])

    def test_a_free_preview_takes_the_same_zone(self):
        self.build(three_stage(optional_middle=True))
        self.assertIs(self.zone("p2"), Zone.OUTPUT)

    def test_a_group_with_other_members_takes_the_latest_zone(self):
        self.build(three_stage("p2", "s3", optional_middle=True))
        self.assertIs(self.zone("p2"), Zone.OUTPUT)
        self.assertIs(self.zone("s3"), Zone.SAMPLING)
        self.assertIs(self.group("p2").zone, Zone.OUTPUT)

    def test_a_group_of_two_previews_is_an_output_group(self):
        self.build(three_stage("p1", "p2", optional_middle=True))
        self.assertIs(self.zone("p1"), Zone.SAMPLING)
        self.assertIs(self.zone("p2"), Zone.OUTPUT)
        self.assertIs(self.group("p1").zone, Zone.OUTPUT)
        self.assertEqual(self.group("p1").key, self.group("p2").key)

    def test_a_group_of_two_previews_follows_the_post_processing_group(self):
        doc = three_stage("p1", "p2", optional_middle=True)
        for preset in ("bento", "swimlanes"):
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
                self.assertEqual(errors, [])
        out = compile_document(copy.deepcopy(doc), DEFS, preset="bento")
        start = {group["title"].split(" ", 1)[1]: group["bounding"][0] for group in out.canvas.raw["groups"]}
        self.assertGreaterEqual(start["Peek"], start["Post-processing"])

    def test_a_preview_after_a_required_sampler_stays_beside_it(self):
        self.build(three_stage("p2", optional_middle=False))
        self.assertIs(self.zone("p2"), Zone.SAMPLING)
        self.assertIs(self.group("p2").zone, Zone.SAMPLING)

    def test_a_final_save_in_a_group_stays_an_output(self):
        for optional in (False, True):
            with self.subTest(optional=optional):
                self.build(three_stage("save", optional_middle=optional, name="Final"))
                self.assertIs(self.zone("save"), Zone.OUTPUT)
                self.assertIs(self.group("save").zone, Zone.OUTPUT)


if __name__ == "__main__":
    unittest.main()
