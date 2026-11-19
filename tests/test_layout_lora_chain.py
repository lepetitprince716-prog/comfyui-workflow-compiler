"""A LoRA chain whose optional and required loaders alternate, and the preset features layout
refuses.

Alternating loaders cut the model group into segments with an optional group between each
two. The optional groups and the model group form a loop, so they share a stage; each optional
group still goes right of the segment that feeds it, in the presets whose model slot repeats
(``swimlanes``, ``cockpit-split``) as in the others.
"""

import unittest
from dataclasses import replace

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, txt2img
from tests.layout_cases import lora_chain, routed

from cwc.annotate import annotate, apply_notes
from cwc.layout import layout
from cwc.layout.presets import get_preset

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")
REPEATED = ("swimlanes", "cockpit-split")  # the model slot repeats along x
PATTERNS = ("OROR", "ROROR", "OOROR", "OROOR", "ORORO", "ORORR", "ORROR")


def column_of(result, node):
    """The layout column holding ``node``'s top-left corner."""
    x, y = node.pos
    return next(c for c in result.columns if c.rect.x <= x < c.rect.right and c.rect.y <= y < c.rect.y + c.rect.h)


class LoraChainTest(unittest.TestCase):
    def test_alternating_chains_have_no_error(self):
        for pattern in PATTERNS:
            for name in PRESETS:
                with self.subTest(pattern=pattern, preset=name):
                    report = routed(lora_chain(pattern), get_preset(name))[3]
                    found = [f"{f.code.value}: {f.message}" for f in report.findings if f.severity.value == "error"]
                    self.assertEqual(found, [])

    def test_each_optional_group_sits_right_of_the_segment_feeding_it(self):
        for pattern in ("OROR", "ROROR"):
            for name in REPEATED:
                with self.subTest(pattern=pattern, preset=name):
                    canvas, plan, result = routed(lora_chain(pattern), get_preset(name))[:3]
                    nodes = canvas.node_map()
                    group = {key: g for g in plan.groups for key in g.members}
                    fed = 0
                    for link in canvas.links:
                        origin, target = str(link.origin_id), str(link.target_id)
                        if origin not in group or target not in group or group[origin].zone.value != "model" or not group[target].optional:
                            continue
                        segment, optional = column_of(result, nodes[origin]), column_of(result, nodes[target])
                        self.assertEqual(optional.groups, (group[target].key,))
                        self.assertGreaterEqual(optional.rect.x, segment.rect.right, f"{origin} -> {target}")
                        fed += 1
                    self.assertEqual(fed, 2 * pattern.count("O"))  # the model link and the clip link of each


class UnimplementedPresetTest(unittest.TestCase):
    def test_layout_refuses_a_slot_under_the_guide(self):
        bento = get_preset("bento")
        first = bento.bands[0]
        slots = (replace(first.slots[0], under_guide=True), *first.slots[1:])
        preset = replace(bento, bands=(replace(first, slots=slots), *bento.bands[1:]))
        canvas, authoring = compiled(txt2img())
        plan = annotate(canvas, DEFS, authoring)
        apply_notes(canvas, plan)
        with self.assertRaisesRegex(NotImplementedError, "uses under_guide, which"):
            layout(canvas, plan, preset, DEFS)


if __name__ == "__main__":
    unittest.main()
