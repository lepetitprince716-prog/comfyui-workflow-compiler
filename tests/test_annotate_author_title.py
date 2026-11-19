"""A group the author keyed with ``ui_group`` is named by the author alone: nodes the compiler
adds to it later (rule 3's free preview and its decode) do not change its name. An optional
module still takes the author's name only when all its members name the same group."""

from __future__ import annotations

import copy
import unittest

from cwc.annotate import annotate
from cwc.annotate.types import GroupPlan

from tests.graphs import DEFS, compiled, node, txt2img
from tests.test_annotate_optional_groups import STYLE, lora_chain, optional_groups

AUTHOR = "Run"


def sampler(model: list, latent: list, denoise: float = 1.0) -> dict:
    return node(
        "KSampler",
        {
            "model": model,
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


def two_pass(group: str, title: str | None = None) -> dict:
    """A latent-upscale graph with a preview of the first pass; only the first sampler is
    grouped by the author, so rule 3 adds the free preview and its decode to that group."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = sampler(["ckpt", "MODEL"], ["up", 0], denoise=0.45)
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["p1"] = node("PreviewImage", {"images": ["d1", 0]})
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    doc["sampler"].setdefault("_meta", {})["ui_group"] = group
    if title is not None:
        doc["_workflow"] = {"format": 1, "groups": {group: {"title": title}}}
    return doc


def author_group(doc: dict, group: str = AUTHOR) -> tuple[GroupPlan, set[str]]:
    """The plan of the group keyed ``group:<group>`` and its members' authoring ids."""
    canvas, authoring = compiled(doc)
    plan = annotate(canvas, DEFS, authoring)
    by_canvas = {canvas_key: author_id for author_id, canvas_key in authoring.ids.items()}
    (found,) = [g for g in plan.groups if g.key == f"group:{group}"]
    return found, {by_canvas[k] for k in found.members}


class AuthorTitleTest(unittest.TestCase):
    def test_compiler_added_nodes_leave_the_authors_group_name(self):
        found, members = author_group(two_pass(AUTHOR))
        self.assertEqual(members, {"sampler", "d1", "p1"})
        self.assertEqual(found.name, AUTHOR)

    def test_an_authored_workflow_title_is_used_cleaned_when_nodes_were_added(self):
        found, members = author_group(two_pass(AUTHOR, title="02 First pass"))
        self.assertEqual(members, {"sampler", "d1", "p1"})
        self.assertEqual(found.name, "First pass")

    def test_an_id_without_words_falls_back_to_the_zone_name(self):
        found, members = author_group(two_pass("12"), group="12")
        self.assertEqual(members, {"sampler", "d1", "p1"})
        self.assertEqual(found.name, "Sampling")

    def test_a_group_without_added_nodes_keeps_its_name(self):
        doc = txt2img()
        doc["sampler"].setdefault("_meta", {})["ui_group"] = AUTHOR
        found, members = author_group(doc)
        self.assertEqual(members, {"sampler"})
        self.assertEqual(found.name, AUTHOR)

    def test_a_workflow_title_is_used_cleaned_when_nothing_was_added(self):
        doc = txt2img()
        doc["sampler"].setdefault("_meta", {})["ui_group"] = AUTHOR
        doc["_workflow"] = copy.deepcopy({"format": 1, "groups": {AUTHOR: {"title": "Step 3: Denoise"}}})
        found, _ = author_group(doc)
        self.assertEqual(found.name, "Denoise")


class OptionalModuleNameTest(unittest.TestCase):
    def test_a_named_and_an_unnamed_optional_node_keep_their_names(self):
        self.assertEqual(
            optional_groups(lora_chain(STYLE, None)),
            [("Style LoRA", ("lora_a",)), ("Optional models", ("lora_b",))],
        )

    def test_a_named_optional_module_keeps_its_title(self):
        self.assertEqual(optional_groups(lora_chain(STYLE, STYLE)), [("Style LoRA", ("lora_a", "lora_b"))])


if __name__ == "__main__":
    unittest.main()
