"""Cockpit titles: every node in a cockpit-zone group is drawn under a title that is not its
type name and that no other cockpit node shares, without the author writing one."""

from __future__ import annotations

import copy
import unittest

from cwc.annotate import annotate, apply_notes
from cwc.annotate.notes import readable_name, type_words
from cwc.annotate.types import Annotations, Zone
from cwc.pipeline import compile_document
from cwc.report import Code

from tests.graphs import DEFS, compiled, node, txt2img


def titles(doc: dict) -> tuple[Annotations, dict[str, str | None]]:
    """The plan for ``doc`` and each authoring id's drawn title after ``apply_notes``."""
    canvas, authoring = compiled(doc)
    plan = annotate(canvas, DEFS, authoring)
    apply_notes(canvas, plan)
    nodes = canvas.node_map()
    return plan, {author: nodes[key].title for author, key in authoring.ids.items()}


def c4(doc: dict) -> list:
    out = compile_document(copy.deepcopy(doc), DEFS, preset="bento")
    return [f for f in out.report.findings if f.code is Code.C4]


def cockpit_zone(plan: Annotations, canvas_key: str) -> bool:
    return any(canvas_key in group.members and group.zone is Zone.COCKPIT for group in plan.groups)


class TypeWordsTest(unittest.TestCase):
    def test_splits_at_case_and_digit_boundaries(self):
        cases = {
            "EmptySD3LatentImage": "Empty SD3 Latent Image",
            "ModelMergeSD35_Large": "Model Merge SD35 Large",
            "Flux2Scheduler": "Flux2 Scheduler",
            "SamplerDPMPP_2M_SDE": "Sampler DPMPP 2M SDE",
            "LTXVImgToVideo": "LTXV Img To Video",
            "lowerStart": "lower Start",
        }
        for name, words in cases.items():
            with self.subTest(name=name):
                self.assertEqual(type_words(name), words)

    def test_a_name_without_a_boundary_gets_a_word(self):
        self.assertEqual(type_words("Painter"), "Painter node")
        self.assertEqual(type_words("Two Words"), "Two Words node")

    def test_every_bundled_definition_has_a_readable_name(self):
        for type_name in DEFS:
            with self.subTest(type_name=type_name):
                name = readable_name(type_name, DEFS.get(type_name))
                self.assertTrue(name.strip())
                self.assertNotEqual(name, type_name)


class CockpitTitlesTest(unittest.TestCase):
    def test_type_named_node_gets_its_words(self):
        doc = txt2img()
        doc["latent"] = node("EmptySD3LatentImage", {"width": 1024, "height": 1024, "batch_size": 1})
        _, drawn = titles(doc)
        self.assertEqual(drawn["latent"], "Empty SD3 Latent Image")
        self.assertEqual(c4(doc), [])

    def test_two_positive_prompts_are_numbered(self):
        doc = txt2img()
        doc["pos2"] = node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": "a dog"})
        doc["both"] = node("ConditioningCombine", {"conditioning_1": ["pos", 0], "conditioning_2": ["pos2", 0]})
        doc["sampler"]["inputs"]["positive"] = ["both", 0]
        _, drawn = titles(doc)
        self.assertEqual((drawn["pos"], drawn["pos2"]), ("Positive prompt 1", "Positive prompt 2"))
        self.assertEqual(drawn["neg"], "Negative prompt")
        self.assertEqual(c4(doc), [])

    def test_two_shared_sources_take_the_input_they_feed(self):
        doc = txt2img()
        doc["w"] = node("PrimitiveInt", {"value": 1024})
        doc["h"] = node("PrimitiveInt", {"value": 1024})
        doc["latent"]["inputs"]["width"] = ["w", 0]
        doc["latent"]["inputs"]["height"] = ["h", 0]
        doc["scale"] = node(
            "ImageScale",
            {"image": ["decode", 0], "upscale_method": "lanczos", "width": ["w", 0], "height": ["h", 0], "crop": "disabled"},
        )
        doc["save"]["inputs"]["images"] = ["scale", 0]
        _, drawn = titles(doc)
        self.assertEqual((drawn["w"], drawn["h"]), ("Int (width)", "Int (height)"))
        self.assertEqual(c4(doc), [])

    def test_shared_sources_feeding_the_same_inputs_are_numbered(self):
        doc = txt2img()
        doc["w"] = node("PrimitiveInt", {"value": 1024})
        doc["h"] = node("PrimitiveInt", {"value": 1024})
        doc["latent"]["inputs"]["width"] = ["w", 0]
        doc["latent"]["inputs"]["height"] = ["h", 0]
        doc["scale"] = node(
            "ImageScale",
            {"image": ["decode", 0], "upscale_method": "lanczos", "width": ["h", 0], "height": ["w", 0], "crop": "disabled"},
        )
        doc["save"]["inputs"]["images"] = ["scale", 0]
        plan, drawn = titles(doc)
        # Both feed a width and a height: the input names do not tell them apart, so they are
        # numbered in display order (the cockpit group's member order).
        canvas, authoring = compiled(doc)
        (group,) = [g for g in plan.groups if authoring.ids["w"] in g.members]
        first, second = sorted(("w", "h"), key=lambda a: group.members.index(authoring.ids[a]))
        self.assertEqual((drawn[first], drawn[second]), ("Int (height, width) 1", "Int (height, width) 2"))
        self.assertEqual(c4(doc), [])

    def test_clear_display_name_is_not_written(self):
        canvas, authoring = compiled(txt2img())
        plan = annotate(canvas, DEFS, authoring)
        latent = authoring.ids["latent"]
        self.assertTrue(cockpit_zone(plan, latent))
        self.assertIsNone(plan.nodes[latent].title)

    def test_explicit_group_in_the_cockpit_zone_is_titled(self):
        doc = txt2img()
        doc["pos"]["_meta"] = {"ui_group": "prompts"}
        doc["neg"]["_meta"] = {"ui_group": "prompts"}
        canvas, authoring = compiled(doc)
        plan = annotate(canvas, DEFS, authoring)
        pos, neg = authoring.ids["pos"], authoring.ids["neg"]
        self.assertTrue(cockpit_zone(plan, pos))
        self.assertEqual((plan.nodes[pos].title, plan.nodes[neg].title), ("Positive prompt", "Negative prompt"))

    def test_authored_title_is_kept_and_generated_one_gives_way(self):
        doc = txt2img()
        doc["pos2"] = node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": "a dog"})
        doc["both"] = node("ConditioningCombine", {"conditioning_1": ["pos", 0], "conditioning_2": ["pos2", 0]})
        doc["sampler"]["inputs"]["positive"] = ["both", 0]
        doc["pos2"]["_meta"] = {"title": "Positive prompt"}
        doc["neg"]["_meta"] = {"title": "Avoid"}
        canvas, authoring = compiled(doc)
        plan = annotate(canvas, DEFS, authoring)
        self.assertIsNone(plan.nodes[authoring.ids["pos2"]].title)
        self.assertIsNone(plan.nodes[authoring.ids["neg"]].title)
        _, drawn = titles(doc)
        self.assertEqual((drawn["pos"], drawn["pos2"], drawn["neg"]), ("Positive prompt 1", "Positive prompt", "Avoid"))
        self.assertEqual(c4(doc), [])

    def test_two_authored_titles_alike_still_report_c4(self):
        doc = txt2img()
        doc["pos"]["_meta"] = {"title": "Prompt"}
        doc["neg"]["_meta"] = {"title": "Prompt"}
        _, drawn = titles(doc)
        self.assertEqual((drawn["pos"], drawn["neg"]), ("Prompt", "Prompt"))
        (finding,) = c4(doc)
        self.assertEqual((finding.data["title"], finding.data["reason"]), ("Prompt", "repeated"))

    def test_tidy_writes_no_title(self):
        doc = txt2img()
        doc["latent"] = node("EmptySD3LatentImage", {"width": 1024, "height": 1024, "batch_size": 1})
        canvas, _ = compiled(doc)
        plan = annotate(canvas, DEFS, None, regroup=True)
        self.assertTrue(all(note.title is None for note in plan.nodes.values()))


if __name__ == "__main__":
    unittest.main()
