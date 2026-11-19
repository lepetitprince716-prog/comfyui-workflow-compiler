"""Rule 3: a side branch off an earlier stage's decode that feeds a later stage's preview
chain joins its own nearest upstream sampler's group, also when it feeds the final
post-processing too; left to post-processing, that group and the preview's would loop."""

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, backward_group_links, compiled, node
from tests.layout_cases import long_chain
from tests.test_annotate_preview_chain import on_cycle, top_group_edges

from cwc.annotate import annotate
from cwc.annotate.types import Zone
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")
BLEND = {"blend_factor": 0.5, "blend_mode": "normal"}
SCALE = {"upscale_method": "lanczos", "scale_by": 1.0}


def side_post(stages=3, source=0, into=1, steps=1):
    """``steps`` scale nodes off stage ``source``'s decode, feeding a blend on stage ``into``'s
    preview chain and a blend before the final save."""
    doc = long_chain(stages)
    prev = f"d{source}"
    for i in range(steps):
        doc[f"sc{i}"] = node("ImageScaleBy", {"image": [prev, 0], **SCALE})
        prev = f"sc{i}"
    doc["mix"] = node("ImageBlend", {"image1": [f"d{into}", 0], "image2": [prev, 0], **BLEND})
    doc[f"p{into}"]["inputs"]["images"] = ["mix", 0]
    doc["blend"] = node("ImageBlend", {"image1": ["decode", 0], "image2": [prev, 0], **BLEND})
    doc["save"]["inputs"]["images"] = ["blend", 0]
    return doc


class SideBranchTest(unittest.TestCase):
    def build(self, doc):
        self.canvas, self.authoring = compiled(copy.deepcopy(doc))
        self.plan = annotate(self.canvas, DEFS, self.authoring)
        return self.plan

    def group(self, author_id):
        key = self.authoring.ids[author_id]
        return next(group for group in self.plan.groups if key in group.members)

    def assert_side_kept(self, doc, side, source, into):
        self.build(doc)
        own = self.group(f"s{source}")
        self.assertIs(own.zone, Zone.SAMPLING)
        for author_id in (f"d{source}", *side):
            self.assertEqual(self.group(author_id).key, own.key, author_id)
        preview = self.group(f"p{into}")
        self.assertEqual(preview.key, self.group(f"s{into}").key)
        self.assertEqual(self.group("mix").key, preview.key)
        self.assertIs(self.group("blend").zone, Zone.POSTPROCESS)
        edges = top_group_edges(self.canvas, self.plan)
        for author_id in [f"p{i}" for i in range(source, into + 1)]:
            self.assertFalse(on_cycle(edges, self.group(author_id).key), (author_id, edges))
        self.assertEqual(backward_group_links(self.canvas, self.plan), [])

    def assert_compiles(self, doc):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
                self.assertEqual(errors, [])

    def test_side_branch_into_the_next_stage_preview_stays_with_its_sampler(self):
        doc = side_post()
        self.assert_side_kept(doc, ("sc0",), source=0, into=1)
        self.assert_compiles(doc)

    def test_two_node_side_branch_comes_along_whole(self):
        doc = side_post(steps=2)
        self.assert_side_kept(doc, ("sc0", "sc1"), source=0, into=1)
        self.assert_compiles(doc)

    def test_side_branch_skipping_a_stage(self):
        doc = side_post(stages=4, source=0, into=2)
        self.assert_side_kept(doc, ("sc0",), source=0, into=2)
        self.assert_compiles(doc)


if __name__ == "__main__":
    unittest.main()
