"""The cockpit as layout hands it to the checks: ``LayoutResult.cockpit`` names every part of a
cockpit C1 split, and G5 then counts what layout's own wrap search counts."""

import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, node, txt2img
from tests.layout_cases import laid_out

from cwc.annotate.types import Zone
from cwc.canvas.direction import direction
from cwc.canvas.envelope import Renderer, box_facts
from cwc.canvas.links import canvas_splines


def crowded_cockpit(encoders: int = 13):
    """Text to image with ``encoders`` extra prompt encoders lifted into the cockpit, chained into
    the positive conditioning: more than C1's 12 expanded nodes, so the cockpit is split."""
    doc = txt2img()
    prev = "pos"
    for i in range(encoders):
        doc[f"t{i}"] = node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": f"x{i}"}, ui_role="cockpit")
        doc[f"c{i}"] = node("ConditioningCombine", {"conditioning_1": [prev, 0], "conditioning_2": [f"t{i}", 0]})
        prev = f"c{i}"
    doc["sampler"]["inputs"]["positive"] = [prev, 0]
    return doc


class SplitCockpit(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas, self.plan, self.result, self.report, _ = laid_out(crowded_cockpit())

    def test_every_part_is_listed(self) -> None:
        (cockpit,) = [g.key for g in self.plan.groups if g.zone is Zone.COCKPIT]
        parts = sorted(index for key, index in self.result.group_index.items() if key.startswith(f"{cockpit}/"))
        self.assertGreaterEqual(len(parts), 2)
        self.assertNotIn(cockpit, self.result.group_index)
        self.assertEqual(self.result.cockpit, tuple(parts))

    def test_g5_counts_as_layout_counts(self) -> None:
        # Layout's wrap search leaves out the links into every cockpit member (cwc.layout._fits).
        (cockpit,) = [g for g in self.plan.groups if g.zone is Zone.COCKPIT]
        nodes = self.canvas.node_map()
        facts = {key: box_facts(n, DEFS.get(n.type)) for key, n in nodes.items()}
        curves = canvas_splines(self.canvas, facts, Renderer.CLASSIC)
        exempt = frozenset(link.id for link in self.canvas.links if str(link.target_id) in cockpit.members)
        own = direction(self.canvas, curves, exempt)
        g5 = self.report.metrics["G5"]
        self.assertEqual((g5["right_to_left"], g5["links"]), (len(own.backward), len(own.considered)))
        self.assertFalse(own.over)
        self.assertEqual([f for f in self.report.findings if f.code.value == "G5"], [])


class WholeCockpit(unittest.TestCase):
    def test_an_unsplit_cockpit_is_one_index(self) -> None:
        _, plan, result, _, _ = laid_out(crowded_cockpit(encoders=2))
        (cockpit,) = [g.key for g in plan.groups if g.zone is Zone.COCKPIT]
        self.assertEqual(result.cockpit, (result.group_index[cockpit],))


if __name__ == "__main__":
    unittest.main()
