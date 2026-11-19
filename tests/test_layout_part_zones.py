"""The segments of a cut author group, each in the slot of its own members' zone (cwc.layout).

An author group holding nodes from both ends of the data flow (a checkpoint and the decode) is
cut into segments, because a path leaves the group between them. Each segment takes the zone of
its own members, by annotate's rule for an explicit group, and goes to that zone's slot; the
title, number and colour stay the group's. So:

1. the graphs below compile with no error in every preset, and each segment sits right of every
   group that feeds it;
2. each segment is handed to placement with its own members' zone;
3. a segment whose members' zone is the cockpit is a cockpit frame;
4. an author group that is not cut keeps its plan zone, the latest zone of its members, and
   that zone's slot.

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


def ckpt_and_decode() -> dict:
    """The checkpoint and the decode in one author group: the start and the end of the chain."""
    doc = txt2img()
    doc["ckpt"]["_meta"] = {"ui_group": "Ends"}
    doc["decode"]["_meta"] = {"ui_group": "Ends"}
    return doc


def ckpt_and_save() -> dict:
    doc = txt2img()
    doc["ckpt"]["_meta"] = {"ui_group": "Misc"}
    doc["save"]["_meta"] = {"ui_group": "Misc"}
    return doc


def sampler_and_save() -> dict:
    """The sampler and the save in one author group; the decode between them is outside."""
    doc = txt2img()
    doc["sampler"]["_meta"] = {"ui_group": "Run"}
    doc["save"]["_meta"] = {"ui_group": "Run"}
    return doc


def lora_and_save() -> dict:
    doc = txt2img()
    doc["lora"] = node("LoraLoader", {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "a.safetensors"}, ui_group="Mine")
    doc["pos"]["inputs"]["clip"] = ["lora", "CLIP"]
    doc["neg"]["inputs"]["clip"] = ["lora", "CLIP"]
    doc["sampler"]["inputs"]["model"] = ["lora", "MODEL"]
    doc["save"]["_meta"] = {"ui_group": "Mine"}
    return doc


def upstream_and_save() -> dict:
    """A steps primitive and the save in one author group."""
    doc = txt2img()
    doc["steps"] = node("PrimitiveInt", {"value": 28}, ui_group="Misc")
    doc["sampler"]["inputs"]["steps"] = ["steps", 0]
    doc["save"]["_meta"] = {"ui_group": "Misc"}
    return doc


def latent_save_preview() -> dict:
    """The empty latent with the save and a preview of the decode: the outputs are the group's
    majority, so it is cut, and its first segment holds the latent alone, a cockpit zone."""
    doc = txt2img()
    doc["latent"]["_meta"] = {"ui_group": "Frame"}
    doc["save"]["_meta"] = {"ui_group": "Frame"}
    doc["preview"] = node("PreviewImage", {"images": ["decode", 0]}, ui_group="Frame")
    return doc


def seed_with_sampler() -> dict:
    """A seed primitive with two samplers in one author group, an optional upscale between the
    samplers outside it: the group is cut, and its first segment holds the seed primitive (a
    cockpit node) and the first sampler."""
    doc = txt2img()
    doc["seed"] = node("PrimitiveInt", {"value": 7}, ui_group="Sampling")
    doc["sampler"]["inputs"]["seed"] = ["seed", 0]
    doc["sampler"]["_meta"] = {"ui_group": "Sampling"}
    doc["up"] = node(
        "LatentUpscaleBy",
        {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5},
        ui_group="Upscale",
        ui_optional=True,
    )
    second = copy.deepcopy(doc["sampler"])
    second["inputs"].update({"latent_image": ["up", 0], "denoise": 0.45})
    doc["s2"] = second
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return doc


def decode_and_save() -> dict:
    """The decode and the save in one author group, linked directly: two zones, not cut. The
    decode's one link goes to the save, so it moves right with it: the group is in the output
    zone."""
    doc = txt2img()
    doc["decode"]["_meta"] = {"ui_group": "Finish"}
    doc["save"]["_meta"] = {"ui_group": "Finish"}
    return doc


# graph -> (builder, the zone of each segment of its one cut group, in order)
CUT = {
    "ckpt+decode": (ckpt_and_decode, (Zone.MODEL, Zone.POSTPROCESS)),
    "ckpt+save": (ckpt_and_save, (Zone.MODEL, Zone.OUTPUT)),
    "sampler+save": (sampler_and_save, (Zone.SAMPLING, Zone.OUTPUT)),
    "lora+save": (lora_and_save, (Zone.MODEL, Zone.OUTPUT)),
    "upstream+save": (upstream_and_save, (Zone.SAMPLING, Zone.OUTPUT)),
    "latent+save+preview": (latent_save_preview, (Zone.COCKPIT, Zone.OUTPUT)),
}


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


def _errors(doc: dict, preset: str) -> list[str]:
    out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
    return [f"{f.code.value}: {f.message}" for f in out.report.findings if f.severity.value == "error"]


class CutAuthorGroupTest(unittest.TestCase):
    def test_no_error_in_any_preset(self):
        for name, (build, _) in CUT.items():
            for preset in PRESETS:
                with self.subTest(graph=name, preset=preset):
                    self.assertEqual(_errors(build(), preset), [])

    def test_each_segment_takes_its_own_members_zone(self):
        for name, (build, zones) in CUT.items():
            with self.subTest(graph=name):
                plan, placed = _placed(build())
                plans = {g.key: g for g in plan.children(None)}
                segments = [g for g in placed if g.key not in plans]
                self.assertEqual(tuple(g.zone for g in segments), zones, [g.key for g in segments])
                for g in segments:
                    group = plans[g.key.rsplit("#", 1)[0]]
                    self.assertEqual((g.name, g.colour), (group.name, group.colour))

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
                        if a is None or b is None or a == b or b in result.cockpit or key_of[b] in plans:
                            continue
                        origin, target = canvas.groups[a].bounding, canvas.groups[b].bounding
                        self.assertGreaterEqual(target.x, origin.x + origin.w, f"{key_of[a]} -> {key_of[b]}")
                        checked += 1
                    self.assertGreater(checked, 0)

    def test_the_segments_keep_the_group_number(self):
        for name, (build, _) in CUT.items():
            with self.subTest(graph=name):
                _, _, result = _laid(build(), "bento")
                cut = [label for key, label in result.labels.items() if "/" in key]
                self.assertEqual(len({label[:-1] for label in cut}), 1, cut)


class CockpitSegmentTest(unittest.TestCase):
    def test_a_cockpit_segment_is_a_cockpit_frame(self):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                canvas, _, result = _laid(latent_save_preview(), preset)
                first = next(i for key, i in result.group_index.items() if key.endswith("/a"))
                last = next(i for key, i in result.group_index.items() if key.endswith("/b"))
                self.assertIn(first, result.cockpit)
                self.assertNotIn(last, result.cockpit)
                self.assertEqual(list(result.cockpit), sorted(result.cockpit))

    def test_cockpit_members_do_not_steer_a_mixed_segment(self):
        plan, placed = _placed(seed_with_sampler())
        plans = {g.key for g in plan.children(None)}
        self.assertEqual(tuple(g.zone for g in placed if g.key not in plans), (Zone.SAMPLING, Zone.SAMPLING))
        for preset in PRESETS:
            with self.subTest(preset=preset):
                self.assertEqual(_errors(seed_with_sampler(), preset), [])
                canvas, _, result = _laid(seed_with_sampler(), preset)
                group_of = {}
                for n in canvas.raw["nodes"]:
                    group_of.setdefault(n["type"], []).append(result.membership[str(n["id"])])
                (mixed,) = group_of["PrimitiveInt"]
                self.assertIn(mixed, group_of["KSampler"])
                first = [i for key, i in result.group_index.items() if key.endswith("/a")]
                self.assertEqual(first, [mixed])
                self.assertNotIn(mixed, result.cockpit)


class UncutAuthorGroupTest(unittest.TestCase):
    def test_an_uncut_group_keeps_its_plan_zone(self):
        plan, placed = _placed(decode_and_save())
        finish = next(g for g in plan.children(None) if g.name == "Finish")
        self.assertIs(finish.zone, Zone.OUTPUT)
        self.assertIn(finish, placed)

    def test_an_uncut_group_stays_in_its_zone_slot(self):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                _, plan, result = _laid(decode_and_save(), preset)
                finish = next(g for g in plan.children(None) if g.name == "Finish")
                (column,) = [c for c in result.columns if finish.key in c.groups]
                self.assertEqual(column.slot, get_preset(preset).home(finish.zone)[1])
                self.assertEqual(_errors(decode_and_save(), preset), [])


if __name__ == "__main__":
    unittest.main()
