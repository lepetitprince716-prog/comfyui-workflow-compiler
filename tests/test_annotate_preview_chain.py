"""Rule 3: an intermediate preview kept in its sampler's group takes the chain from the
sampler to it (the decode) along, also when that chain feeds a final output; a chain node
feeding a later sampler stays out."""

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, backward_group_links, compiled, node, txt2img
from tests.layout_cases import long_chain

from cwc.annotate import annotate
from cwc.annotate.types import Annotations, Zone
from cwc.canvas.model import Canvas
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")
BLEND = {"blend_factor": 0.2, "blend_mode": "normal"}


def optional_blend():
    """Six refine stages; the first stage's decode also feeds an optional blend into the save."""
    doc = long_chain(6)
    doc["opt_blend"] = node(
        "ImageBlend", {"image1": ["decode", 0], "image2": ["d0", 0], **BLEND}, ui_optional=True, ui_group="watermark_opt"
    )
    doc["save"]["inputs"]["images"] = ["opt_blend", 0]
    return doc


def required_blend():
    """As above, with the blend required: the first decode reaches the final save directly."""
    doc = long_chain(6)
    doc["blend"] = node("ImageBlend", {"image1": ["decode", 0], "image2": ["d0", 0], **BLEND})
    doc["save"]["inputs"]["images"] = ["blend", 0]
    return doc


def direct_save():
    """The first stage's decode also feeds a save node of its own."""
    doc = long_chain(6)
    doc["save0"] = node("SaveImage", {"images": ["d0", 0], "filename_prefix": "stage0"})
    return doc


def pixel_chain():
    """The first stage's preview sits behind a pixel step that also feeds the final blend."""
    doc = long_chain(3)
    doc["scale"] = node("ImageScaleBy", {"image": ["d0", 0], "upscale_method": "lanczos", "scale_by": 1.0})
    doc["p0"]["inputs"]["images"] = ["scale", 0]
    doc["blend"] = node("ImageBlend", {"image1": ["decode", 0], "image2": ["scale", 0], **BLEND})
    doc["save"]["inputs"]["images"] = ["blend", 0]
    return doc


def pixel_hires():
    """A decode feeding its stage's preview and, through a pixel upscale, the next sampler."""
    doc = txt2img()
    doc["p0"] = node("PreviewImage", {"images": ["decode", 0]})
    doc["up"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5})
    doc["enc"] = node("VAEEncode", {"pixels": ["up", 0], "vae": ["ckpt", "VAE"]})
    second = copy.deepcopy(doc["sampler"])
    second["inputs"]["latent_image"] = ["enc", 0]
    second["inputs"]["denoise"] = 0.5
    doc["sampler2"] = second
    doc["decode2"] = node("VAEDecode", {"samples": ["sampler2", 0], "vae": ["ckpt", "VAE"]})
    doc["save"]["inputs"]["images"] = ["decode2", 0]
    return doc


def top_group_edges(canvas: Canvas, annotations: Annotations) -> dict[str, set[str]]:
    """Top-level group key to the top-level groups its nodes link into."""
    parent = {group.key: group.parent for group in annotations.groups}

    def top(key: str) -> str:
        while parent[key] is not None:
            key = parent[key]
        return key

    owner = {member: top(group.key) for group in annotations.groups for member in group.members}
    edges: dict[str, set[str]] = {key: set() for key in parent if parent[key] is None}
    for link in canvas.links:
        a, b = owner.get(str(link.origin_id)), owner.get(str(link.target_id))
        if a is not None and b is not None and a != b:
            edges[a].add(b)
    return edges


def on_cycle(edges: dict[str, set[str]], start: str) -> bool:
    """Whether a path of group edges leads from ``start`` back to it."""
    stack, seen = list(edges[start]), set()
    while stack:
        key = stack.pop()
        if key == start:
            return True
        if key not in seen:
            seen.add(key)
            stack.extend(edges[key])
    return False


class PreviewChainTest(unittest.TestCase):
    def build(self, doc):
        self.canvas, self.authoring = compiled(copy.deepcopy(doc))
        self.plan = annotate(self.canvas, DEFS, self.authoring)
        return self.plan

    def group(self, author_id):
        key = self.authoring.ids[author_id]
        return next(group for group in self.plan.groups if key in group.members)

    def assert_chain_kept(self, doc, chain=("d0",)):
        self.build(doc)
        preview = self.group("p0")
        self.assertIs(preview.zone, Zone.SAMPLING)
        self.assertEqual(preview.key, self.group("s0").key)
        for author_id in chain:
            self.assertEqual(self.group(author_id).key, preview.key, author_id)
        edges = top_group_edges(self.canvas, self.plan)
        self.assertFalse(on_cycle(edges, preview.key), edges)
        self.assertEqual(backward_group_links(self.canvas, self.plan), [])

    def assert_compiles(self, doc):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
                self.assertEqual(errors, [])

    def test_decode_feeding_an_optional_blend_into_the_save_stays_with_its_preview(self):
        self.assert_chain_kept(optional_blend())
        self.assert_compiles(optional_blend())

    def test_decode_feeding_a_required_blend_into_the_save_stays_with_its_preview(self):
        self.assert_chain_kept(required_blend())
        self.assertIs(self.group("blend").zone, Zone.POSTPROCESS)
        self.assert_compiles(required_blend())

    def test_decode_feeding_a_save_of_its_own_stays_with_its_preview(self):
        # A save node a later sampler follows is kept beside its sampler like a preview.
        self.assert_chain_kept(direct_save())
        self.assertEqual(self.group("save0").key, self.group("p0").key)
        self.assert_compiles(direct_save())

    def test_pixel_step_between_decode_and_preview_comes_along(self):
        self.assert_chain_kept(pixel_chain(), chain=("d0", "scale"))
        self.assert_compiles(pixel_chain())

    def test_decode_feeding_the_next_sampler_stays_out(self):
        self.build(pixel_hires())
        sampling = self.group("sampler")
        self.assertEqual(self.group("p0").key, sampling.key)
        for author_id, zone in (("decode", Zone.STAGE), ("up", Zone.STAGE), ("enc", Zone.STAGE)):
            self.assertNotEqual(self.group(author_id).key, sampling.key, author_id)
            self.assertIs(self.group(author_id).zone, zone, author_id)
        self.assertIs(self.group("sampler2").zone, Zone.SAMPLING)
        self.assertIs(self.group("decode2").zone, Zone.POSTPROCESS)
        self.assertIs(self.group("save").zone, Zone.OUTPUT)
        self.assert_compiles(pixel_hires())


if __name__ == "__main__":
    unittest.main()
