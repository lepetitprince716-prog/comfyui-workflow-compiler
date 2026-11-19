"""Rule 4: a latent source reaching a sampler through latent-only steps (an upscale, a blend)
goes to the cockpit's size slot, as one feeding the sampler directly does; a path through
pixels (a decode, then an encode) does not count."""

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, backward_group_links, compiled, node, txt2img

from cwc.annotate import annotate, apply_notes
from cwc.annotate.types import CockpitSlot, Zone
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")
UP = {"upscale_method": "nearest-exact", "scale_by": 1.5}


def through_steps(steps):
    """The empty latent reaches the sampler through ``steps`` latent upscales."""
    doc = txt2img()
    prev = "latent"
    for i in range(steps):
        doc[f"up{i}"] = node("LatentUpscaleBy", {"samples": [prev, 0], **UP})
        prev = f"up{i}"
    doc["sampler"]["inputs"]["latent_image"] = [prev, 0]
    return doc


def through_pixels():
    """The empty latent is decoded, scaled as pixels and encoded again before the sampler."""
    doc = txt2img()
    doc["dec0"] = node("VAEDecode", {"samples": ["latent", 0], "vae": ["ckpt", "VAE"]})
    doc["enc0"] = node("VAEEncode", {"pixels": ["dec0", 0], "vae": ["ckpt", "VAE"]})
    doc["sampler"]["inputs"]["latent_image"] = ["enc0", 0]
    return doc


def two_blended():
    """Two empty latents blended into the sampler's latent."""
    doc = txt2img()
    doc["latent2"] = node("EmptyLatentImage", {"width": 1024, "height": 1024, "batch_size": 1})
    doc["mix"] = node("LatentBlend", {"samples1": ["latent", 0], "samples2": ["latent2", 0], "blend_factor": 0.5})
    doc["sampler"]["inputs"]["latent_image"] = ["mix", 0]
    return doc


class LatentSourceTest(unittest.TestCase):
    def build(self, doc):
        self.canvas, self.authoring = compiled(copy.deepcopy(doc))
        self.plan = annotate(self.canvas, DEFS, self.authoring)
        self.assertEqual(backward_group_links(self.canvas, self.plan), [])
        return self.plan

    def note(self, author_id):
        return self.plan.nodes[self.authoring.ids[author_id]]

    def group(self, author_id):
        key = self.authoring.ids[author_id]
        return next(group for group in self.plan.groups if key in group.members)

    def assert_size(self, author_id):
        self.assertIs(self.group(author_id).zone, Zone.COCKPIT, author_id)
        self.assertIs(self.note(author_id).cockpit, CockpitSlot.SIZE, author_id)

    def assert_compiles(self, doc):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
                self.assertEqual(errors, [])

    def test_direct_feed_unchanged(self):
        self.build(txt2img())
        self.assert_size("latent")

    def test_through_one_latent_step(self):
        doc = through_steps(1)
        self.build(doc)
        self.assert_size("latent")
        self.assertIsNot(self.group("up0").zone, Zone.COCKPIT)
        self.assert_compiles(doc)

    def test_through_two_latent_steps(self):
        doc = through_steps(2)
        self.build(doc)
        self.assert_size("latent")
        for author_id in ("up0", "up1"):
            self.assertIsNot(self.group(author_id).zone, Zone.COCKPIT, author_id)
        self.assert_compiles(doc)

    def test_through_decode_and_encode_is_not_lifted(self):
        self.build(through_pixels())
        self.assertIsNot(self.group("latent").zone, Zone.COCKPIT)
        self.assertIsNone(self.note("latent").cockpit)

    def test_two_blended_sources_both_lifted_with_distinct_titles(self):
        doc = two_blended()
        self.build(doc)
        self.assert_size("latent")
        self.assert_size("latent2")
        apply_notes(self.canvas, self.plan)
        nodes = self.canvas.node_map()
        first = nodes[self.authoring.ids["latent"]].title
        second = nodes[self.authoring.ids["latent2"]].title
        self.assertTrue(first and second)
        self.assertNotEqual(first, second)
        self.assert_compiles(doc)


if __name__ == "__main__":
    unittest.main()
