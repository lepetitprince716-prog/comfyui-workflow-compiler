"""A lower-band group with no producer docks left of every group it feeds. When it feeds the main
row and a lower-band group, it ends left of both: at the main-row consumer's left edge and
``GAP`` left of the lower-band consumer's dock, whichever is further left. An author's group
around a sampler and the ControlNet loader that feeds it is cut where a path leaves the group
(the loader reaches the sampler only through the apply node and the guider outside it) into a
control part (the loader and the sampler's selector) and the rest; the second cut, by zone,
parts neither further. The control part has no producer and feeds the
main row (the selector into the sampler) and the Control group (the loader into the apply
node), and used to dock beside the main row, right of the Control group it feeds."""

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, node, txt2img
from tests.test_layout_wide import stack_faults

from cwc.annotate import annotate
from cwc.canvas.geometry import Rect
from cwc.instantiate import instantiate
from cwc.layout.items import GAP
from cwc.pipeline import compile_document

PRESETS = ("bento", "stepped", "tower-preview")


def with_control(doc: dict) -> None:
    """A ControlNet applied to the prompts, in ``doc``."""
    doc["img"] = node("LoadImage", {"image": "pose.png"})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "cn.safetensors"})
    doc["cn"] = node(
        "ControlNetApplyAdvanced",
        {
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "control_net": ["cnl", 0],
            "image": ["img", "IMAGE"],
            "strength": 0.8,
            "start_percent": 0.0,
            "end_percent": 1.0,
        },
    )


def two_stage_custom() -> dict:
    """Custom sampling over two stages with a ControlNet. The author group ``Scatter`` holds the
    first sampler, the loader, the sampler selector and the first decode's preview (rule 3 adds
    the decode). The loader reaches the sampler only along a path through nodes outside the
    group (the apply node and the guider), so the first cut, where a path leaves the group,
    puts the sampler in a later segment than the loader; the selector, which feeds the sampler
    directly, stays in the first. Two segments: ``[cnl, pick]`` (control; the selector is a
    cockpit member and does not steer) and the sampler with its decode and preview
    (sampling)."""
    doc = txt2img()
    with_control(doc)
    doc["noise"] = node("RandomNoise", {"noise_seed": 3})
    doc["guider"] = node("CFGGuider", {"model": ["ckpt", "MODEL"], "positive": ["cn", 0], "negative": ["cn", 1], "cfg": 4.0})
    doc["pick"] = node("KSamplerSelect", {"sampler_name": "euler"})
    doc["sched"] = node("BasicScheduler", {"model": ["ckpt", "MODEL"], "scheduler": "normal", "steps": 20, "denoise": 1.0})
    custom = {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["pick", 0], "sigmas": ["sched", 0]}
    doc["sampler"] = node("SamplerCustomAdvanced", dict(custom, latent_image=["latent", 0]))
    doc["decode_s0"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["preview0"] = node("PreviewImage", {"images": ["decode_s0", 0]})
    doc["up0"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["sampler1"] = node("SamplerCustomAdvanced", dict(custom, latent_image=["up0", 0]))
    doc["decode"]["inputs"]["samples"] = ["sampler1", 0]
    for key in ("sampler", "cnl", "pick", "preview0"):
        doc[key]["_meta"] = {"ui_group": "scatter"}
    return doc


def loader_only() -> dict:
    """The ControlNet loader alone in an author group: no producer, and it feeds only the
    Control group in the lower band, not the main row."""
    doc = txt2img()
    with_control(doc)
    doc["sampler"]["inputs"]["positive"] = ["cn", 0]
    doc["sampler"]["inputs"]["negative"] = ["cn", 1]
    doc["cnl"]["_meta"] = {"ui_group": "Loader"}
    return doc


def errors(out) -> list[str]:
    return [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]


def frame_of(out, key: str) -> Rect:
    """The group frame around the node ``key`` (an authoring id): the smallest that holds its
    position."""
    node_ = next(n for n in out.canvas.nodes if n.key == str(out.authoring.ids[key]))
    x, y = node_.pos
    frames = [g.bounding for g in out.canvas.groups if g.bounding.contains_point(x, y)]
    return min(frames, key=lambda r: r.w * r.h)


class SourcelessDockTest(unittest.TestCase):
    def compile(self, doc: dict, preset: str):
        out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
        plan = annotate(instantiate(out.authoring), DEFS, out.authoring)
        return out, plan

    def test_control_part_docks_left_of_the_group_it_feeds(self):
        doc = two_stage_custom()
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out, plan = self.compile(doc, preset)
                self.assertEqual(errors(out), [])
                self.assertEqual(stack_faults(out, plan), [])
                part, rest, control = frame_of(out, "cnl"), frame_of(out, "sampler"), frame_of(out, "cn")
                self.assertNotEqual(part, rest)
                self.assertNotEqual(part, control)
                self.assertLessEqual(part.right + GAP, control.x)
                self.assertLessEqual(part.x, rest.x)

    def test_pick_stays_with_the_control_part(self):
        out, _ = self.compile(two_stage_custom(), "bento")
        self.assertEqual(frame_of(out, "pick"), frame_of(out, "cnl"))

    def test_part_feeding_only_a_lower_group_keeps_its_dock(self):
        doc = loader_only()
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out, plan = self.compile(doc, preset)
                self.assertEqual(errors(out), [])
                self.assertEqual(stack_faults(out, plan), [])
                loader, control = frame_of(out, "cnl"), frame_of(out, "cn")
                self.assertNotEqual(loader, control)
                self.assertEqual(loader.right + GAP, control.x)


if __name__ == "__main__":
    unittest.main()
