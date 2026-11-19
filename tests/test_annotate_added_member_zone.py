"""A node rule 3 adds to an author's group (an intermediate preview kept beside its sampler, and
the decode feeding it) takes its own zone as the author's members do: the zone the rules give it
without intents. It does not take its group's zone. An author's group around the first sampler
and the final save goes to the output zone (the latest zone of its members), and a preview and
decode read as members of that zone would be cut away from their sampler and placed after the
post-processing they feed."""

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img
from tests.test_layout_wide import placed_groups, stack_faults

from cwc.annotate import annotate
from cwc.annotate.types import Zone
from cwc.instantiate import instantiate
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")


def two_stage(*grouped: str) -> dict:
    """Two samplers through a latent upscale. The first is decoded (``d1``) into a preview
    (``p1``), which rule 3 keeps beside it; ``d1`` also feeds a blend with the final decode
    before the save. ``grouped`` names the nodes put in the author group ``Scatter``."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = copy.deepcopy(doc["sampler"])
    doc["s2"]["inputs"]["latent_image"] = ["up", 0]
    doc["s2"]["inputs"]["denoise"] = 0.45
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["p1"] = node("PreviewImage", {"images": ["d1", 0]})
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    doc["blend"] = node(
        "ImageBlend", {"image1": ["decode", 0], "image2": ["d1", 0], "blend_factor": 0.2, "blend_mode": "normal"}
    )
    doc["save"]["inputs"]["images"] = ["blend", 0]
    for key in grouped:
        doc[key].setdefault("_meta", {})["ui_group"] = "Scatter"
    return doc


def errors(out) -> list[str]:
    return [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]


class AddedMemberZoneTest(unittest.TestCase):
    def setUp(self):
        self.doc = two_stage("sampler", "save")

    def test_added_members_keep_their_own_zone(self):
        canvas, authoring = compiled(copy.deepcopy(self.doc))
        plan = annotate(canvas, DEFS, authoring)
        key = authoring.ids
        group = next(g for g in plan.groups if key["sampler"] in g.members)
        self.assertEqual(group.key, "group:Scatter")
        self.assertIn(key["d1"], group.members)
        self.assertIn(key["p1"], group.members)
        self.assertIs(group.zone, Zone.OUTPUT)
        self.assertIs(plan.nodes[key["sampler"]].zone, Zone.SAMPLING)
        self.assertIs(plan.nodes[key["d1"]].zone, Zone.SAMPLING)
        self.assertIs(plan.nodes[key["p1"]].zone, Zone.SAMPLING)
        self.assertIs(plan.nodes[key["save"]].zone, Zone.OUTPUT)

    def test_bento_keeps_them_beside_their_sampler(self):
        out = compile_document(copy.deepcopy(self.doc), DEFS, preset="bento")
        self.assertEqual(errors(out), [])
        plan = annotate(instantiate(out.authoring), DEFS, out.authoring)
        self.assertEqual(stack_faults(out, plan), [])
        placed, zone = placed_groups(out, plan)
        key = out.authoring.ids
        self.assertEqual(placed[key["d1"]], placed[key["sampler"]])
        self.assertEqual(placed[key["p1"]], placed[key["sampler"]])
        self.assertIs(zone[placed[key["sampler"]]], Zone.SAMPLING)
        self.assertNotEqual(placed[key["save"]], placed[key["sampler"]])
        self.assertIs(zone[placed[key["save"]]], Zone.OUTPUT)

    def test_every_preset_compiles(self):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(self.doc), DEFS, preset=preset)
                self.assertEqual(errors(out), [])
                plan = annotate(instantiate(out.authoring), DEFS, out.authoring)
                self.assertEqual(stack_faults(out, plan), [])


if __name__ == "__main__":
    unittest.main()
