"""Groups cut into segments, ranked again over the placed groups (cwc.layout).

A group whose members are joined through nodes outside it is cut into segments, and layout
ranks the placed groups, segments included, by annotate's ``group_ranks``. So:

1. groups whose segments interleave (A#0, B#0, A#1, B#1), and a cut group feeding two linked
   groups that feed its next segment, compile with no error in every preset;
2. each segment sits right of every group that feeds it, and every group a segment feeds
   sits right of it (links into the cockpit run right to left by design);
3. with no group cut, the placed groups are the plan's top-level groups, in plan order, with
   the stages annotate gave them.

The graphs use core nodes only.
"""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img
from tests.layout_cases import OTHERS, SIMPLE
from tests.layout_generator import random_graph, reduced_graph

import cwc.layout as layout_module
from cwc.annotate import annotate, apply_notes
from cwc.layout.presets import get_preset
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")
UP = {"upscale_method": "nearest-exact", "scale_by": 1.0}


def _upscale(source: str, group: str) -> dict:
    return node("LatentUpscaleBy", {"samples": [source, 0], **UP}, ui_group=group)


def _blend(first: str, second: str, group: str) -> dict:
    return node("LatentBlend", {"samples1": [first, 0], "samples2": [second, 0], "blend_factor": 0.5}, ui_group=group)


def two_cut_groups() -> dict:
    """grp_a and grp_b each cut in two, interleaved: A#0 -> B#0 -> A#1 -> B#1."""
    doc = txt2img()
    doc["a0_b_init"] = _upscale("latent", "grp_b")
    doc["m1_a0"] = _upscale("latent", "grp_a")
    doc["m2_b0"] = _blend("a0_b_init", "m1_a0", "grp_b")
    doc["m3_a1"] = _upscale("m2_b0", "grp_a")
    doc["m4_b1"] = _upscale("m3_a1", "grp_b")
    doc["sampler"]["inputs"]["latent_image"] = ["m4_b1", 0]
    return doc


def three_cut_groups() -> dict:
    """grp_a, grp_b and grp_c each cut in two, interleaved: A#0 -> B#0 -> C#0 -> A#1 -> B#1 ->
    C#1. C and B each have an early member read from the latent, run before A's first, so
    ordering the plan groups' loop by earliest member would put C and B ahead of A."""
    doc = txt2img()
    doc["a0_c_init"] = _upscale("latent", "grp_c")
    doc["a1_b_init"] = _upscale("latent", "grp_b")
    doc["m0_a0"] = _upscale("latent", "grp_a")
    doc["m1_b0"] = _blend("a1_b_init", "m0_a0", "grp_b")
    doc["m2_c0"] = _blend("a0_c_init", "m1_b0", "grp_c")
    doc["m3_a1"] = _upscale("m2_c0", "grp_a")
    doc["m4_b1"] = _upscale("m3_a1", "grp_b")
    doc["m5_c1"] = _upscale("m4_b1", "grp_c")
    doc["sampler"]["inputs"]["latent_image"] = ["m5_c1", 0]
    return doc


def cut_feeds_chain() -> dict:
    """The model group is cut by an optional LoRA and again by two linked groups that its first
    LoRA feeds and that feed its second: model#0 -> optional -> model#1 -> g1 -> g2 -> model#2.
    model#1 and model#2 are also joined directly (CLIP), which does not keep them together."""
    doc = txt2img()
    doc["opt1"] = node("LoraLoader", {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "1.safetensors"}, ui_optional=True)
    doc["m1"] = node("LoraLoader", {"model": ["opt1", "MODEL"], "clip": ["opt1", "CLIP"], "lora_name": "2.safetensors"})
    doc["g1_n"] = node("ModelSamplingDiscrete", {"model": ["m1", "MODEL"], "sampling": "eps", "zsnr": False}, ui_group="g1")
    doc["g2_n"] = node("ModelSamplingDiscrete", {"model": ["g1_n", 0], "sampling": "eps", "zsnr": False}, ui_group="g2")
    doc["m2"] = node("LoraLoader", {"model": ["g2_n", 0], "clip": ["m1", "CLIP"], "lora_name": "3.safetensors"})
    doc["sampler"]["inputs"]["model"] = ["m2", "MODEL"]
    return doc


CUT = {"two_cut_groups": (two_cut_groups, 2), "three_cut_groups": (three_cut_groups, 3), "cut_feeds_chain": (cut_feeds_chain, 1)}


def _laid(doc: dict, preset: str):
    canvas, authoring = compiled(copy.deepcopy(doc))
    plan = annotate(canvas, DEFS, authoring)
    apply_notes(canvas, plan)
    result = layout_module.layout(canvas, plan, get_preset(preset), DEFS)
    return canvas, plan, result


def _placed(doc: dict):
    """The plan and the groups layout hands to ``place``."""
    seen: list = []
    original = layout_module.place

    def spy(preset, groups, *args, **kwargs):
        seen.append(list(groups))
        return original(preset, groups, *args, **kwargs)

    canvas, authoring = compiled(copy.deepcopy(doc))
    plan = annotate(canvas, DEFS, authoring)
    apply_notes(canvas, plan)
    with mock.patch.object(layout_module, "place", spy):
        layout_module.layout(canvas, plan, get_preset("bento"), DEFS)
    return plan, seen[0]


class CutGraphTest(unittest.TestCase):
    def test_the_graphs_cut_the_groups_they_name(self):
        for name, (build, cut) in CUT.items():
            with self.subTest(graph=name):
                plan, placed = _placed(build())
                plans = {g.key for g in plan.children(None)}
                segmented = {g.name for g in placed if g.key not in plans}
                self.assertEqual(len(segmented), cut, [g.key for g in placed])

    def test_no_error_in_any_preset(self):
        for name, (build, _) in CUT.items():
            for preset in PRESETS:
                with self.subTest(graph=name, preset=preset):
                    out = compile_document(build(), DEFS, preset=preset)
                    found = [f"{f.code.value}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
                    self.assertEqual(found, [])

    def test_each_segment_sits_right_of_what_feeds_it(self):
        for name, (build, _) in CUT.items():
            for preset in PRESETS:
                with self.subTest(graph=name, preset=preset):
                    canvas, plan, result = _laid(build(), preset)
                    plans = {g.key for g in plan.groups}
                    key_of = {i: k for k, i in result.group_index.items()}
                    checked = 0
                    for link in canvas.links:
                        a, b = result.membership.get(str(link.origin_id)), result.membership.get(str(link.target_id))
                        if a is None or b is None or a == b or b in result.cockpit:
                            continue
                        if key_of[a] in plans and key_of[b] in plans:
                            continue
                        origin, target = canvas.groups[a].bounding, canvas.groups[b].bounding
                        self.assertGreaterEqual(target.x, origin.x + origin.w, f"{key_of[a]} -> {key_of[b]}")
                        checked += 1
                    self.assertGreater(checked, 0)


class UncutRankTest(unittest.TestCase):
    def test_with_no_group_cut_the_plan_reaches_place_unchanged(self):
        docs = {**{f"cases/{k}": v for k, v in {**SIMPLE, **OTHERS}.items()}}
        for seed in range(0, 400, 25):
            docs[f"full/{seed}"] = lambda s=seed: random_graph(s)
            docs[f"reduced/{seed}"] = lambda s=seed: reduced_graph(s)
        uncut = 0
        for name, build in docs.items():
            plan, placed = _placed(build())
            tops = list(plan.children(None))
            if {g.key for g in placed} != {g.key for g in tops}:
                continue  # a group is cut
            uncut += 1
            with self.subTest(graph=name):
                self.assertEqual([(g.key, g.stage) for g in placed], [(g.key, g.stage) for g in tops])
        self.assertGreater(uncut, 10)


if __name__ == "__main__":
    unittest.main()
