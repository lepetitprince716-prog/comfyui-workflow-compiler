"""Optional modules follow ``ui_group``: two linked optional nodes share a module only when
they name the same group (none counts as the same); a named group keeps its own title."""

from __future__ import annotations

import copy
import unittest

from cwc.annotate import annotate
from cwc.annotate.types import Annotations
from cwc.pipeline import compile_document
from cwc.report import Severity

from tests.graphs import DEFS, compiled, node, txt2img

STYLE = "style_lora"
CHAR = "char_lora"
PACK = "hires_pack"
INFO = {
    "format": 1,
    "groups": {STYLE: {"title": "Style LoRA"}, CHAR: {"title": "Character LoRA"}, PACK: {"title": "Hires pack"}},
}
PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")


def lora(model: list, clip: list, name: str, group: str | None) -> dict:
    meta: dict = {"ui_optional": True}
    if group is not None:
        meta["ui_group"] = group
    return node(
        "LoraLoader",
        {"model": model, "clip": clip, "lora_name": name, "strength_model": 0.8, "strength_clip": 0.8},
        **meta,
    )


def lora_chain(first: str | None, second: str | None) -> dict:
    """Two linked optional LoRAs between the checkpoint and the sampler and prompts."""
    doc = txt2img()
    doc["lora_a"] = lora(["ckpt", "MODEL"], ["ckpt", "CLIP"], "a.safetensors", first)
    doc["lora_b"] = lora(["lora_a", "MODEL"], ["lora_a", "CLIP"], "b.safetensors", second)
    doc["sampler"]["inputs"]["model"] = ["lora_b", "MODEL"]
    doc["pos"]["inputs"]["clip"] = ["lora_b", "CLIP"]
    doc["neg"]["inputs"]["clip"] = ["lora_b", "CLIP"]
    doc["_workflow"] = copy.deepcopy(INFO)
    return doc


def plan_for(doc: dict) -> tuple[Annotations, dict[str, str]]:
    canvas, authoring = compiled(doc)
    return annotate(canvas, DEFS, authoring), dict(authoring.ids)


def optional_groups(doc: dict) -> list[tuple[str, tuple[str, ...]]]:
    """(name, authoring ids of the members) of each optional group, in plan order."""
    plan, ids = plan_for(doc)
    author = {canvas_key: author_id for author_id, canvas_key in ids.items()}
    return [(g.name, tuple(sorted(author[k] for k in g.members))) for g in plan.groups if g.optional]


class OptionalGroupsTest(unittest.TestCase):
    def test_linked_nodes_with_different_groups_are_two_modules_with_their_own_titles(self):
        self.assertEqual(
            optional_groups(lora_chain(STYLE, CHAR)),
            [("Style LoRA", ("lora_a",)), ("Character LoRA", ("lora_b",))],
        )

    def test_a_group_without_a_title_is_named_from_its_id(self):
        doc = lora_chain(STYLE, CHAR)
        del doc["_workflow"]
        self.assertEqual([name for name, _ in optional_groups(doc)], ["Style lora", "Char lora"])

    def test_linked_nodes_without_ui_group_stay_one_module(self):
        groups = optional_groups(lora_chain(None, None))
        self.assertEqual(groups, [("Optional models", ("lora_a", "lora_b"))])

    def test_linked_nodes_with_the_same_group_stay_one_module(self):
        groups = optional_groups(lora_chain(STYLE, STYLE))
        self.assertEqual(groups, [("Style LoRA", ("lora_a", "lora_b"))])

    def test_one_named_and_one_unnamed_node_are_two_modules(self):
        groups = optional_groups(lora_chain(STYLE, None))
        self.assertEqual(groups, [("Style LoRA", ("lora_a",)), ("Optional models", ("lora_b",))])

    def test_unlinked_nodes_with_the_same_group_are_still_two_modules_with_one_title(self):
        doc = txt2img()
        doc["lora"] = lora(["ckpt", "MODEL"], ["ckpt", "CLIP"], "a.safetensors", PACK)
        doc["sampler"]["inputs"]["model"] = ["lora", "MODEL"]
        doc["pos"]["inputs"]["clip"] = ["lora", "CLIP"]
        doc["neg"]["inputs"]["clip"] = ["lora", "CLIP"]
        doc["upscale"] = node(
            "ImageScaleBy",
            {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5},
            ui_optional=True,
            ui_group=PACK,
        )
        doc["save"]["inputs"]["images"] = ["upscale", 0]
        doc["_workflow"] = copy.deepcopy(INFO)
        plan, _ = plan_for(doc)
        groups = [g for g in plan.groups if g.optional]
        self.assertEqual([g.name for g in groups], ["Hires pack", "Hires pack"])
        self.assertEqual(len({g.key for g in groups}), 2)

    def test_split_modules_compile_clean_on_every_preset(self):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(lora_chain(STYLE, CHAR), DEFS, preset=preset)
                errors = [f for f in out.report.findings if f.severity is Severity.ERROR]
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
