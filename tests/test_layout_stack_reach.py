"""The sub-column rule of a slot stacked along y follows reach, not only direct links.

Two groups of one y stack can be joined only through groups outside the stack: a required
post-processing step, an optional module, a second required step. Stacked in one sub-column,
the two ends share an x and the outside group between them has no x to sit at, so one of its
links runs right to left. A group an earlier group of the stack reaches (and does not reach
back) therefore goes one sub-column right of it, as a directly linked group does:

1. the graphs compile without errors under every bundled preset;
2. the two ends sit in different sub-columns, the earlier one ending at or before the later
   one starts;
3. each outside group on the path sits between them along x (in a lower band its x
   projection does);
4. the rule at the level of ``_slot_items``: a path through two outside groups moves the
   later group, a path that loops back moves nothing, and a path into the cockpit is not
   followed.
"""

from __future__ import annotations

import copy
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, node, txt2img
from tests.test_layout_items import STACK, W, _at, _edges, _groups

from cwc.annotate.types import Zone
from cwc.layout.items import GAP, _slot_items
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")


def _scale(image: str, by: float, **meta) -> dict:
    return node("ImageScaleBy", {"image": [image, 0], "upscale_method": "lanczos", "scale_by": by}, **meta)


def required_optional_required() -> dict:
    """Post-processing, an optional module, post-processing: the two required steps share the
    post-processing slot and only the optional group joins them."""
    doc = txt2img()
    doc["post0"] = _scale("decode", 1.2)
    doc["opt"] = _scale("post0", 1.0, ui_optional=True)
    doc["post1"] = _scale("opt", 1.0)
    doc["save"]["inputs"]["images"] = ["post1", 0]
    return doc


def two_hops() -> dict:
    """The same with a required group of its own between two optional modules: the first and
    last steps are joined only through two optional groups and the middle step, so neither
    links to the next step of the stack directly."""
    doc = txt2img()
    doc["post0"] = _scale("decode", 1.2)
    doc["opt"] = _scale("post0", 1.0, ui_optional=True)
    doc["mid"] = node("ImageInvert", {"image": ["opt", 0]}, ui_group="mid")
    doc["opt2"] = _scale("mid", 1.0, ui_optional=True)
    doc["post1"] = _scale("opt2", 1.0)
    doc["save"]["inputs"]["images"] = ["post1", 0]
    return doc


def _boxes(doc: dict, preset: str) -> tuple[list[str], dict[str, tuple[float, float, float, float]]]:
    """The error codes, and the written group box of each authoring id's group."""
    out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
    errors = [f"{f.code}: {f.message}" for f in out.report.findings if f.severity.value == "error"]
    groups = out.canvas.raw["groups"]
    boxes = {}
    for author, numeric in out.authoring.ids.items():
        index = out.layout.membership.get(numeric)
        if index is not None:
            boxes[author] = tuple(groups[index]["bounding"])
    return errors, boxes


class StackReachTest(unittest.TestCase):
    def check_chain(self, doc: dict, chain: list[str]) -> None:
        """``chain``: authoring ids along the path, alternating stack ends and outside groups;
        every second one (from the first) is in the stack."""
        for preset in PRESETS:
            with self.subTest(preset=preset):
                errors, boxes = _boxes(doc, preset)
                self.assertEqual(errors, [])
                for a, between, b in zip(chain[0::2], chain[1::2], chain[2::2]):
                    first, outside, last = boxes[a], boxes[between], boxes[b]
                    self.assertNotEqual(first[0], last[0], (a, b))
                    self.assertLessEqual(first[0] + first[2], last[0], (a, b))
                    self.assertGreaterEqual(outside[0], first[0] + first[2], (a, between))
                    self.assertLessEqual(outside[0] + outside[2], last[0], (between, b))

    def test_two_required_steps_joined_through_an_optional_module(self) -> None:
        self.check_chain(required_optional_required(), ["post0", "opt", "post1"])

    def test_steps_joined_through_two_optional_modules_and_a_step(self) -> None:
        self.check_chain(two_hops(), ["post0", "opt", "mid", "opt2", "post1"])


class SlotReachTest(unittest.TestCase):
    def test_a_path_through_two_outside_groups_moves_the_later_group(self) -> None:
        groups, units = _groups(("a", Zone.MODEL, 0), ("b", Zone.PROMPT, 1))
        (item,) = _slot_items(0, 0, STACK, groups, units, _edges(("a", "x"), ("x", "y"), ("y", "b")))
        at = _at(item, units)
        self.assertEqual(at["a"], (0.0, 0.0))
        self.assertEqual(at["b"], (W + GAP, 0.0))
        self.assertEqual(item.columns, [["a"], ["b"]])

    def test_a_path_that_loops_back_moves_nothing(self) -> None:
        # a reaches b and b reaches a: neither order keeps every link left to right.
        groups, units = _groups(("a", Zone.MODEL, 0), ("b", Zone.PROMPT, 1))
        (item,) = _slot_items(0, 0, STACK, groups, units, _edges(("a", "x"), ("x", "b"), ("b", "y"), ("y", "a")))
        self.assertEqual(item.columns, [])

    def test_a_path_through_the_cockpit_is_not_followed(self) -> None:
        groups, units = _groups(("a", Zone.MODEL, 0), ("b", Zone.PROMPT, 1))
        (item,) = _slot_items(0, 0, STACK, groups, units, _edges(("a", "k"), ("k", "b")), "k")
        self.assertEqual(item.columns, [])


if __name__ == "__main__":
    unittest.main()
