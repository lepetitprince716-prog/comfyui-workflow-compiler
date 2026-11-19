"""The zone of an author's group, and of each segment of one (cwc.annotate, cwc.layout).

An author's group that is not a cockpit frame goes to the latest zone of its steering members,
not to their majority zone. A member of an earlier zone moves right with the group when every
link it has goes to a member of the latest zone or to one that moves too (an output node always
can); a member that cannot stays in its own zone, as a part of its own. So:

1. a group of the intermediate previews and the final save of a multi-stage graph is one box in
   the output slot, starting no further left than the right edge of the post-processing group
   that feeds the save;
2. a ControlNet apply grouped with the sampler it feeds goes to the sampling slot;
3. a group of a first-stage decode that also feeds a preview outside the group, a later
   stage's preview and the final save is cut in two: the decode in the sampling slot, the
   preview and the save in the output slot;
4. a segment of a cut group holding an early preview and a post-processing node (one each, a
   tie) goes to the post-processing slot;
5. a group whose majority ties with the cockpit is still a cockpit frame.

The graphs use core nodes only.
"""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img

import cwc.layout as layout_module
from cwc.annotate import annotate, apply_notes
from cwc.annotate.types import Zone
from cwc.layout.presets import get_preset
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")
# The presets whose main row has one slot per zone; the other two put every zone but the
# cockpit in one repeated slot, where the zone does not decide the place.
ZONED = ("bento", "stepped", "tower-preview")


def _sampler(latent: list, denoise: float) -> dict:
    return node(
        "KSampler",
        {
            "model": ["ckpt", "MODEL"],
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "latent_image": latent,
            "seed": 1,
            "steps": 20,
            "cfg": 5.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": denoise,
        },
    )


def two_pass() -> dict:
    """txt2img, a latent upscale and a second sampler before the final decode; the first pass
    decoded by ``d1`` into the preview ``p1``."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = _sampler(["up", 0], 0.45)
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["p1"] = node("PreviewImage", {"images": ["d1", 0]})
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return doc


def three_stage() -> dict:
    """As :func:`two_pass` with a third sampler; ``d2`` decodes the second pass into ``p2``."""
    doc = two_pass()
    doc["up2"] = node("LatentUpscaleBy", {"samples": ["s2", 0], "upscale_method": "nearest-exact", "scale_by": 1.25})
    doc["s3"] = _sampler(["up2", 0], 0.3)
    doc["d2"] = node("VAEDecode", {"samples": ["s2", 0], "vae": ["ckpt", "VAE"]})
    doc["p2"] = node("PreviewImage", {"images": ["d2", 0]})
    doc["decode"]["inputs"]["samples"] = ["s3", 0]
    return doc


def grouped(doc: dict, name: str, *keys: str) -> dict:
    for key in keys:
        doc[key].setdefault("_meta", {})["ui_group"] = name
    return doc


def outputs_of_three() -> dict:
    """Both intermediate previews and the final save in one group."""
    return grouped(three_stage(), "Outputs", "p1", "p2", "save")


def preview_and_final() -> dict:
    """The first-pass preview and the final save in one group."""
    return grouped(two_pass(), "Outs", "p1", "save")


def control_with_sampler() -> dict:
    """A ControlNet apply and the sampler it feeds in one group."""
    doc = txt2img()
    doc["cimg"] = node("LoadImage", {"image": "depth.png"})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "depth.safetensors"})
    doc["cn"] = node(
        "ControlNetApplyAdvanced",
        {
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "control_net": ["cnl", 0],
            "image": ["cimg", "IMAGE"],
            "strength": 0.7,
            "start_percent": 0.0,
            "end_percent": 0.8,
        },
    )
    doc["sampler"]["inputs"]["positive"] = ["cn", 0]
    doc["sampler"]["inputs"]["negative"] = ["cn", 1]
    return grouped(doc, "Steer", "cn", "sampler")


def decode_feeding_outside() -> dict:
    """The first-pass decode (which also feeds the free preview ``p1``), the second-pass
    preview and the final save in one group."""
    return grouped(three_stage(), "Mix", "d1", "p2", "save")


def early_preview_and_post() -> dict:
    """The first-pass preview, a scale after the final decode and the save in one group; a
    sharpen outside it lies between the scale and the save, so the group is cut, and its first
    segment holds the preview and the scale."""
    doc = two_pass()
    doc["sc"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 0.5})
    doc["sh"] = node("ImageSharpen", {"image": ["sc", 0], "sharpen_radius": 1, "sigma": 1.0, "alpha": 0.5})
    doc["save"]["inputs"]["images"] = ["sh", 0]
    return grouped(doc, "Mine", "p1", "sc", "save")


def latent_and_decode() -> dict:
    """The empty latent (a cockpit node) and the decode in one group: one each, a tie."""
    return grouped(txt2img(), "Pair", "latent", "decode")


class _Laid:
    """One graph compiled under one preset, by the stages the compiler runs."""

    def __init__(self, doc: dict, preset: str):
        canvas, authoring = compiled(copy.deepcopy(doc))
        self.plan = annotate(canvas, DEFS, authoring)
        apply_notes(canvas, self.plan)
        self.preset = get_preset(preset)
        seen: list = []
        original = layout_module.place

        def spy(preset_, groups, *args, **kwargs):
            seen.append(list(groups))
            return original(preset_, groups, *args, **kwargs)

        with mock.patch.object(layout_module, "place", spy):
            self.result = layout_module.layout(canvas, self.plan, self.preset, DEFS)
        self.placed = seen[0]
        self.canvas = canvas
        self.ids = authoring.ids

    def box(self, author_id: str) -> list[float]:
        """The written group box holding the node."""
        return self.canvas.raw["groups"][self.result.membership[self.ids[author_id]]]["bounding"]

    def title(self, author_id: str) -> str:
        return self.canvas.raw["groups"][self.result.membership[self.ids[author_id]]]["title"]

    def slot(self, author_id: str) -> int:
        """The slot of the column holding the node's group box."""
        x, y = self.box(author_id)[:2]
        (column,) = [c for c in self.result.columns if c.rect.x <= x < c.rect.right and c.rect.y <= y < c.rect.y + c.rect.h]
        return column.slot

    def home(self, zone: Zone) -> int:
        return self.preset.home(zone)[1]

    def boxes(self, word: str) -> list[list[float]]:
        return [g["bounding"] for g in self.canvas.raw["groups"] if word in g["title"]]


def _errors(doc: dict, preset: str) -> list[str]:
    out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
    return [f"{f.code.value}: {f.message}" for f in out.report.findings if f.severity.value == "error"]


class OutputsGroupTest(unittest.TestCase):
    def test_no_error_in_any_preset(self):
        for build in (outputs_of_three, preview_and_final):
            for preset in PRESETS + ("auto",):
                with self.subTest(graph=build.__name__, preset=preset):
                    self.assertEqual(_errors(build(), preset), [])

    def test_the_group_is_one_box_in_the_output_slot(self):
        for build, members in ((outputs_of_three, ("p1", "p2", "save")), (preview_and_final, ("p1", "save"))):
            for preset in ZONED:
                with self.subTest(graph=build.__name__, preset=preset):
                    laid = _Laid(build(), preset)
                    group = next(g for g in laid.plan.children(None) if g.key.startswith("group:"))
                    self.assertIs(group.zone, Zone.OUTPUT)
                    self.assertEqual({laid.title(m) for m in members}, {laid.title(members[0])})
                    self.assertEqual(len({tuple(laid.box(m)) for m in members}), 1)
                    self.assertEqual(laid.slot("save"), laid.home(Zone.OUTPUT))

    def test_the_group_starts_right_of_the_post_processing(self):
        for build in (outputs_of_three, preview_and_final):
            for preset in ZONED:
                with self.subTest(graph=build.__name__, preset=preset):
                    laid = _Laid(build(), preset)
                    (post,) = [b for b in laid.boxes("Post-processing")]
                    self.assertGreaterEqual(laid.box("save")[0], post[0] + post[2])
                    self.assertEqual(laid.box("decode"), post)


class ControlWithSamplerTest(unittest.TestCase):
    def test_the_group_goes_to_the_sampling_slot(self):
        for preset in ZONED:
            with self.subTest(preset=preset):
                laid = _Laid(control_with_sampler(), preset)
                (steer,) = [g for g in laid.placed if g.key == "group:Steer"]
                self.assertIs(steer.zone, Zone.SAMPLING)
                self.assertEqual(laid.slot("cn"), laid.home(Zone.SAMPLING))
                self.assertEqual(laid.box("cn"), laid.box("sampler"))
                self.assertEqual(_errors(control_with_sampler(), preset), [])


class MemberThatCannotMoveTest(unittest.TestCase):
    def test_the_group_is_cut_by_zone(self):
        laid = _Laid(decode_feeding_outside(), "bento")
        parts = [(g.key, g.zone) for g in laid.placed if g.key.startswith("group:Mix")]
        self.assertEqual(parts, [("group:Mix#0", Zone.SAMPLING), ("group:Mix#1", Zone.OUTPUT)])

    def test_the_decode_stays_in_sampling_and_the_outputs_go_right(self):
        for preset in ZONED:
            with self.subTest(preset=preset):
                laid = _Laid(decode_feeding_outside(), preset)
                self.assertEqual(laid.box("p2"), laid.box("save"))
                self.assertNotEqual(laid.box("d1"), laid.box("save"))
                self.assertEqual(laid.slot("d1"), laid.home(Zone.SAMPLING))
                self.assertEqual(laid.slot("save"), laid.home(Zone.OUTPUT))
                first, last = laid.title("d1"), laid.title("save")
                self.assertTrue(first.endswith("a Mix") and last.endswith("b Mix"), (first, last))
                self.assertEqual(first[: -len("a Mix")], last[: -len("b Mix")])  # one group number
                self.assertEqual(_errors(decode_feeding_outside(), preset), [])


class TiedSegmentTest(unittest.TestCase):
    def test_the_segment_goes_to_the_post_processing_slot(self):
        for preset in ZONED:
            with self.subTest(preset=preset):
                laid = _Laid(early_preview_and_post(), preset)
                parts = [(g.key, g.zone) for g in laid.placed if g.key.startswith("group:Mine")]
                self.assertEqual(parts, [("group:Mine#0", Zone.POSTPROCESS), ("group:Mine#1", Zone.OUTPUT)])
                self.assertEqual(laid.box("p1"), laid.box("sc"))
                self.assertEqual(laid.slot("sc"), laid.home(Zone.POSTPROCESS))
                self.assertEqual(_errors(early_preview_and_post(), preset), [])


class CockpitFrameTest(unittest.TestCase):
    def test_a_tie_with_the_cockpit_is_a_cockpit_frame(self):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                laid = _Laid(latent_and_decode(), preset)
                pair = next(g for g in laid.plan.children(None) if g.key == "group:Pair")
                self.assertIs(pair.zone, Zone.COCKPIT)
                self.assertIn(laid.result.membership[laid.ids["decode"]], laid.result.cockpit)


if __name__ == "__main__":
    unittest.main()
