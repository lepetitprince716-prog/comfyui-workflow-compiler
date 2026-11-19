"""Vue anchors of widget-backed inputs (cwc.canvas.envelope) against every recorded Vue run.

``tests/fixtures/layout/<name>.vue.json`` holds, per node, the anchor of every input the frontend
had after the load, widget-backed inputs included (the saved canvases list only the linked ones).
``multiline`` (``tools/oracle/geometry.py record --canvas multiline``) holds text boxes at several
node heights, a box above a plain widget, two boxes, plain and advanced widgets above two boxes,
collapsed copies, and widget inputs fed by a link (text boxes, an advanced number, three numbers
in a row, a seed with its value-control combo). Every widget-backed input must be within 1 px
of the model, apart from the known misses below.
"""

from __future__ import annotations

import json
import unittest

from tests.support import FIXTURES

from cwc.canvas.envelope import VUE_COLLAPSED_Y, Renderer, box_facts, slot_anchors
from cwc.canvas.model import Canvas
from cwc.nodedefs.registry import NodeDefs

LAYOUT = FIXTURES / "layout"
DEFS = NodeDefs.load()
CANVASES = ("boxes", "links", "multiline")
TOLERANCE = 1.0
# An advanced widget not fed by a link is hidden under Vue and has no slot element; the frontend
# then falls back to the widget's classic ``y`` (slotCalculations.ts, getWidgetSlotY), which the
# model does not track. Such a slot has no link by definition (a link shows the widget), so no
# link ends there.
KNOWN = {
    ("multiline", "15", "crop_w"), ("multiline", "15", "crop_h"),
    ("multiline", "16", "crop_w"), ("multiline", "16", "crop_h"),
    ("multiline", "22", "crop_h"),
}


def _load(name: str) -> dict:
    return json.loads((LAYOUT / f"{name}.json").read_text(encoding="utf-8"))


def widget_anchors() -> list[tuple[tuple[str, str, str], tuple[float, float], tuple[float, float]]]:
    """``(key, model, recorded)`` for every widget-backed input of every recorded Vue node."""
    out = []
    for name in CANVASES:
        nodes = Canvas(_load(f"{name}.canvas")).node_map()
        for entry in _load(f"{name}.vue")["nodes"]:
            node = nodes[entry["id"]]
            facts = box_facts(node, DEFS.get(node.type))
            x, y = node.pos
            for slot in entry["inputs"]:
                widget = slot["widget"]
                if widget is None:
                    continue
                model = (x, y - VUE_COLLAPSED_Y) if node.collapsed else (x, y + facts.vue_widgets[widget])
                out.append(((name, entry["id"], widget), model, tuple(slot["anchor"])))
    return out


class VueWidgetAnchors(unittest.TestCase):
    def test_every_widget_input_within_a_pixel(self) -> None:
        rows = widget_anchors()
        misses = sorted(key for key, (mx, my), (rx, ry) in rows if max(abs(mx - rx), abs(my - ry)) > TOLERANCE)
        self.assertEqual(misses, sorted(KNOWN))
        self.assertEqual(len(rows), 95)

    def test_known_misses_are_unlinked_advanced_widgets(self) -> None:
        for name, node_id, widget in KNOWN:
            node = Canvas(_load(f"{name}.canvas")).node_map()[node_id]
            spec = next(s for s in DEFS.get(node.type).inputs if s.name == widget)
            self.assertTrue(spec.options.get("advanced"))
            self.assertFalse(any(s.get("widget") and s.get("link") is not None and s["name"] == widget for s in node.inputs))

    def test_text_box_follows_the_node_height(self) -> None:
        # One text box below one slot row: the anchor is at 7 + body / 2 for every body at or
        # above the natural 102 (heights 60 and 102 load at the natural body).
        nodes = Canvas(_load("multiline.canvas")).node_map()
        for entry in _load("multiline.vue")["nodes"]:
            if entry["type"] != "CLIPTextEncode" or entry["collapsed"] or entry["inputs"][1]["link"] is not None:
                continue
            node = nodes[entry["id"]]
            body = entry["vue_box"][3] - 30
            self.assertAlmostEqual(entry["inputs"][1]["anchor"][1] - node.pos[1], 7 + body / 2)
            self.assertAlmostEqual(box_facts(node, DEFS.get(node.type)).vue_widgets["text"], 7 + body / 2)

    def test_linked_widget_inputs_through_slot_anchors(self) -> None:
        # Linked widget inputs are in the saved canvas, so slot_anchors places them itself.
        checked = 0
        for name in CANVASES:
            nodes = Canvas(_load(f"{name}.canvas")).node_map()
            for entry in _load(f"{name}.vue")["nodes"]:
                node = nodes[entry["id"]]
                inputs, _ = slot_anchors(node, box_facts(node, DEFS.get(node.type)), Renderer.VUE)
                recorded = {s["name"]: s["anchor"] for s in entry["inputs"]}
                for slot, got in zip(node.inputs, inputs):
                    if isinstance(slot.get("widget"), dict) and slot.get("link") is not None:
                        checked += 1
                        self.assertAlmostEqual(got[0], recorded[slot["name"]][0], places=3)
                        self.assertAlmostEqual(got[1], recorded[slot["name"]][1], places=3)
        self.assertEqual(checked, 7)


if __name__ == "__main__":
    unittest.main()
