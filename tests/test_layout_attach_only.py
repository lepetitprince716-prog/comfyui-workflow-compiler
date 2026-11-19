"""A repeated slot whose only groups are in an attach zone.

In swimlanes and cockpit-split the control zone attaches to the instances the other zones of
the repeated slot open. When every other node sits in the cockpit (``ui_role``), or an
explicit group whose majority is control holds the rest, the slot has control groups and no
opening group: there is no instance to join, so the attached groups open one together.

1. the graphs compile without errors under every bundled preset and ``auto``;
2. at the level of ``_slot_items``: attach groups alone make one instance holding all of them,
   in plan order, at their lowest stage; with an opening group the attach rule is unchanged.
"""

from __future__ import annotations

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, node
from tests.layout_cases import controlnet
from tests.test_layout_items import _edges, _groups

from cwc.annotate.types import Zone
from cwc.layout.items import _slot_items
from cwc.layout.types import Axis, Slot
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")
LANES = Slot((Zone.MODEL, Zone.CONTROL, Zone.SAMPLING), repeat=Axis.X, attach=(Zone.CONTROL,))


def rest_in_cockpit() -> dict:
    """A ControlNet graph with every node off the control branch moved into the cockpit."""
    doc = controlnet()
    for key in ("ckpt", "pos", "neg", "latent", "sampler", "decode", "save"):
        doc[key].setdefault("_meta", {})["ui_role"] = "cockpit"
    return doc


def one_control_group() -> dict:
    """Every node in one explicit group; the control branch's image steps outnumber the rest,
    so the group's zone is control."""
    doc = controlnet()
    previous = "image"
    for i in range(4):
        doc[f"s{i}"] = node("ImageScaleBy", {"image": [previous, 0], "upscale_method": "lanczos", "scale_by": 1.0})
        previous = f"s{i}"
    doc["edges"]["inputs"]["image"] = [previous, 0]
    for entry in doc.values():
        entry.setdefault("_meta", {})["ui_group"] = "all"
    return doc


class CompileTest(unittest.TestCase):
    def test_compiles_under_every_preset(self) -> None:
        for name, build in (("rest_in_cockpit", rest_in_cockpit), ("one_control_group", one_control_group)):
            doc = build()
            for preset in PRESETS:
                with self.subTest(graph=name, preset=preset):
                    out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                    errors = [f.code for f in out.report.findings if f.severity.value == "error"]
                    self.assertEqual(errors, [])
                    self.assertTrue(out.canvas.groups)


class SlotItemsTest(unittest.TestCase):
    def test_attach_groups_alone_open_one_instance(self) -> None:
        groups, units = _groups(("c2", Zone.CONTROL, 2), ("c1", Zone.CONTROL, 1), ("c3", Zone.CONTROL, 3))
        items = _slot_items(0, 1, LANES, groups, units, _edges(("c1", "c2")))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].groups, ["c2", "c1", "c3"])
        self.assertEqual(items[0].stage, 1)
        self.assertEqual(items[0].instance, 0)

    def test_one_attach_group_alone(self) -> None:
        groups, units = _groups(("c", Zone.CONTROL, 4))
        items = _slot_items(0, 1, LANES, groups, units, _edges())
        self.assertEqual([(item.groups, item.stage) for item in items], [(["c"], 4)])

    def test_with_an_opening_group_the_attach_rule_holds(self) -> None:
        groups, units = _groups(("c", Zone.CONTROL, 0), ("s0", Zone.SAMPLING, 1), ("s1", Zone.SAMPLING, 2))
        items = _slot_items(0, 1, LANES, groups, units, _edges(("c", "s1")))
        self.assertEqual([(item.groups, item.stage) for item in items], [(["s0"], 1), (["c", "s1"], 2)])


if __name__ == "__main__":
    unittest.main()
