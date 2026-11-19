"""The node box model (cwc.canvas.envelope) against the recorded frontend runs and the m0 #6 table."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tests.support import FIXTURES

from cwc.canvas.envelope import (
    COLLAPSED_FONT,
    COLLAPSED_PAD,
    TITLE_FONT,
    TITLE_ROOM,
    VUE_COLLAPSED_PAD,
    BoxFacts,
    box_facts,
    classic_box,
    envelope,
    expanded_centre,
    readable_width,
    title_text,
    title_width,
    vue_box,
)
from cwc.canvas.geometry import Rect
from cwc.canvas.model import Canvas, Node
from cwc.nodedefs.registry import NodeDefs

LAYOUT = FIXTURES / "layout"
DEFS = NodeDefs.load()


def _load(name: str) -> dict:
    return json.loads((LAYOUT / f"{name}.json").read_text(encoding="utf-8"))


def _runs():
    """(recorded node, canvas node, facts from the definitions, run name) for every recorded node."""
    for canvas_name in ("boxes", "links"):
        nodes = Canvas(_load(f"{canvas_name}.canvas")).node_map()
        for run in ("classic", "vue"):
            for recorded in _load(f"{canvas_name}.{run}")["nodes"]:
                node = nodes[recorded["id"]]
                yield recorded, node, box_facts(node, DEFS.get(node.type)), run


def _close(test: unittest.TestCase, got: Rect, want: list[float]) -> None:
    for a, b in zip(got.as_list(), want):
        test.assertAlmostEqual(a, b, places=3)


class ReadableWidth(unittest.TestCase):
    def test_title_only(self) -> None:
        # the title term is the drawn (14 px) width, never the collapsed (12 px) one rescaled
        self.assertAlmostEqual(readable_width(BoxFacts(title_width=120.0, drawn_title_width=150.0)), 150.0 + 60)

    def test_the_longest_widget_label_can_decide(self) -> None:
        facts = BoxFacts(title_width=50.0, drawn_title_width=60.0, classic_widgets={"seed": 30.0, "control_after_generate": 54.0})
        self.assertAlmostEqual(readable_width(facts), title_width("control_after_generate", 12) + 100)

    def test_every_recorded_expanded_node_is_estimated_at_least_as_wide_as_its_title(self) -> None:
        for _recorded, node, facts, _run in _runs():
            self.assertGreaterEqual(readable_width(facts), facts.title_width + COLLAPSED_PAD)


    def test_title_term_bounds_every_measured_14px_title(self) -> None:
        # unicode.title: whole strings measured at 14 px in the frontend. Emoji are 15 px at
        # 12 px but 18 px at 14 px, so a 14/12 rescale of the collapsed width falls short.
        measured = _measured_titles()
        self.assertEqual(measured["\U0001F680" * 8], 144)
        for text, width in measured.items():
            node = Node({"id": 1, "type": "NoSuchNode", "title": text, "pos": [0, 0], "size": [200, 100], "flags": {}})
            facts = box_facts(node, None)
            self.assertEqual(facts.drawn_title_width, title_width(text, TITLE_FONT), text)
            self.assertGreaterEqual(readable_width(facts) - TITLE_ROOM, width, text)

    def test_eight_rockets_need_at_least_the_measured_width(self) -> None:
        node = Node({"id": 1, "type": "NoSuchNode", "title": "\U0001F680" * 8, "pos": [0, 0], "size": [200, 100], "flags": {}})
        self.assertGreaterEqual(readable_width(box_facts(node, None)), 144 + TITLE_ROOM)


def _measured_titles() -> dict[str, float]:
    data = json.loads((LAYOUT / "char_widths.measured.json").read_text(encoding="utf-8"))
    return data["unicode"]["title"]


class Envelope(unittest.TestCase):
    def test_envelope_holds_every_recorded_box(self) -> None:
        count = 0
        for recorded, node, facts, run in _runs():
            self.assertTrue(envelope(node, facts).contains_rect(Rect(*recorded["bounding"])), (run, recorded["id"]))
            count += 1
        self.assertEqual(count, 24)

    def test_classic_box_exact_with_measured_title(self) -> None:
        for recorded, node, facts, run in _runs():
            if run != "classic":
                continue
            if node.collapsed:
                facts = replace(facts, title_width=recorded["collapsed_width"] - COLLAPSED_PAD)
            _close(self, classic_box(node, facts), recorded["bounding"])

    def test_vue_box_exact_where_the_saved_size_wins(self) -> None:
        """Collapsed boxes with the measured title, and expanded nodes saved taller than their
        natural body, are exact; the natural body is an upper bound (next test)."""
        exact = 0
        for recorded, node, facts, run in _runs():
            if run != "vue":
                continue
            box = recorded["bounding"]
            if node.collapsed:
                facts = replace(facts, title_width=max(0.0, box[2] - VUE_COLLAPSED_PAD))
                _close(self, vue_box(node, facts), box)
                exact += 1
            elif node.size[1] >= facts.vue_body:
                _close(self, vue_box(node, facts), box)
                exact += 1
            else:
                model = vue_box(node, facts)
                self.assertEqual(model.as_list()[:3], box[:3])
                self.assertGreaterEqual(model.h, box[3])
        self.assertGreaterEqual(exact, 6)

    def test_title_width_is_an_upper_bound(self) -> None:
        for recorded, node, _, run in _runs():
            if run == "classic" and node.collapsed:
                self.assertGreaterEqual(title_width(title_text(node, DEFS.get(node.type))), recorded["collapsed_width"] - COLLAPSED_PAD)

    def test_untitled_node_uses_the_display_name(self) -> None:
        nodes = Canvas(_load("links.canvas")).node_map()
        node = nodes["4"]  # collapsed, no saved title; drawn as the display name
        facts = box_facts(node, DEFS.get(node.type))
        self.assertEqual(facts.title_width, title_width(DEFS.get(node.type).display_name, COLLAPSED_FONT))

    def test_unknown_type_still_has_a_box(self) -> None:
        node = Node({"id": 9, "type": "NoSuchNode", "pos": [0, 0], "size": [200, 100], "flags": {}})
        facts = box_facts(node, None)
        self.assertEqual(envelope(node, facts), Rect(0, -30, 225, 154))


class M0VueSizes(unittest.TestCase):
    """docs/m0-findings.md #6: classic height after load and Vue natural body, saved 100 x 40."""

    TABLE = {
        "CheckpointLoaderSimple": (98, 126),
        "CLIPTextEncode": (88, 102),
        "KSampler": (262, 262),
        "EmptyLatentImage": (106, 118),
        "VAEDecode": (46, 54),
        "SaveImage": (58, 302),
        "LoadImage": (314, 346),
        "PreviewImage": (40, 266),
        "Note": (88, 88),
    }
    NO_PREVIEW_IN_FORMULA = {"LoadImage", "PreviewImage"}  # classic preview not in computeSize

    def _node(self, type_name: str) -> tuple[Node, BoxFacts]:
        nodedef = DEFS.get(type_name)
        inputs = [{"name": s.name, "type": s.type, "link": None, **({"widget": {"name": s.widget}} if s.widget else {})} for s in (nodedef.slots or ())]
        outputs = [{"name": o.name, "type": o.type, "links": []} for o in nodedef.outputs]
        raw = {"id": 1, "type": type_name, "pos": [0, 0], "size": [100, 40], "flags": {}, "inputs": inputs, "outputs": outputs}
        node = Node(raw)
        return node, box_facts(node, nodedef)

    def test_classic_minimum_height(self) -> None:
        for type_name, (classic, _) in self.TABLE.items():
            if type_name in self.NO_PREVIEW_IN_FORMULA:
                continue
            _, facts = self._node(type_name)
            self.assertEqual(facts.min_height, classic, type_name)

    def test_vue_box_holds_the_natural_body(self) -> None:
        for type_name, (classic, natural) in self.TABLE.items():
            node, facts = self._node(type_name)
            box = vue_box(node, facts)
            self.assertGreaterEqual(box.w, 225, type_name)
            self.assertGreaterEqual(box.h - 30, natural, type_name)
            self.assertLessEqual(box.h - 30 - natural, 80, type_name)  # bounded waste


class ExpandedCentre(unittest.TestCase):
    def test_collapsed_node_centre_of_expanded_envelope(self) -> None:
        node = Canvas(_load("boxes.canvas")).node_map()["2"]  # collapsed, saved 400 x 200
        facts = box_facts(node, DEFS.get(node.type))
        x, y = node.pos
        cx, cy = expanded_centre(node, facts)
        self.assertEqual(cx, x + 200)
        self.assertGreater(cy, y)  # below the collapsed band: a group must reach it


if __name__ == "__main__":
    unittest.main()
