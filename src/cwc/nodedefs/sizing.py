"""Classic-renderer node size, for nodes cwc creates.

A real frontend records each type's size right after creation (``NodeDef.size``). A node
whose layout matches that fresh node takes the recorded size. A node whose layout differs
(Autogrow groups grown by links, DynamicCombo options that add or drop sub-widgets) or a
type without a recorded size gets the classic ``computeSize`` estimate below; with a
recorded size the larger of the two wins in each dimension, so text measured by the
frontend (long labels, wide combos) is never cut.

Estimate (checked against the frontend 1.59.1 truth snapshot, see
``tests/test_nodedefs_sizing.py``)::

    height = SLOT * max(link-only inputs, outputs, 1)
             + sum(widget height + WIDGET_GAP) + (WIDGETS_PAD if any widget) + BOTTOM
    width  = MULTILINE_WIDTH with a multiline text widget, else WIDGET_WIDTH with widgets,
             else BARE_WIDTH

Widgets are the persisted ones (``widget_layout``); a multiline text widget is at least
``MULTILINE_HEIGHT`` tall, every other widget ``WIDGET_HEIGHT``.

Loading adds one input row (measured, frontend 1.59.1, see
``tests/fixtures/instantiate/load_probes.md``): a node that is loaded with a linked slot in
an Autogrow group has the height of its own slots plus one more ``SLOT`` row, as if the
group's next slot were already there. The extra row is added before the minimum height
of a multiline node is applied, and it is paid once: the loaded node writes the larger
size, and loading that again changes nothing. Autogrow groups whose slots are match-type
(``BranchNode``, the one such group in the core nodes) do not grow this way; that is one
sample, not a rule about match types. The core Autogrow nodes measured (see
``tests/test_nodedefs_sizing.py``) cannot tell an extra input row from a flat ``SLOT``;
the code adds an input row.
"""

from __future__ import annotations

from typing import Collection, Iterable, Sequence

from cwc.nodedefs.derive import WidgetValues, slot_layout, widget_layout
from cwc.nodedefs.types import Behavior, InputSpec, NodeDef, SlotSpec, WidgetSpec

SLOT = 20
WIDGET_HEIGHT = 20
MULTILINE_HEIGHT = 50
WIDGET_GAP = 4
WIDGETS_PAD = 8
BOTTOM = 6
BARE_WIDTH = 140
WIDGET_WIDTH = 270
MULTILINE_WIDTH = 400
MULTILINE_MIN_HEIGHT = 200  # a node with a multiline text widget opens at least this tall

_TEXT = "STRING"


def node_size(
    nodedef: NodeDef,
    values: WidgetValues | None = None,
    *,
    slots: Sequence[SlotSpec] | None = None,
    linked: Collection[str] = (),
) -> tuple[float, float]:
    """``(width, height)`` of a node of this type holding ``values``, as a real frontend
    writes it after loading the canvas.

    ``slots`` is the node's actual ``inputs`` layout when it differs from
    ``slot_layout(nodedef, values, linked)`` (Autogrow slots added for links); by default
    that layout is used. ``linked`` names the slots that have a link: a linked slot of an
    Autogrow group adds the row described in the module docstring. A node built live in a
    frontend, before any load, is that many pixels shorter.
    """
    widgets = widget_layout(nodedef, values)
    inputs = tuple(slots) if slots is not None else slot_layout(nodedef, values, linked)
    grown = _group_slot_linked(nodedef, inputs, linked)
    recorded = nodedef.size
    if recorded is not None and not grown and _same_layout(nodedef, widgets, inputs):
        return recorded
    specs = _input_specs(nodedef)
    estimate = estimate_size(
        sum(1 for slot in inputs if slot.widget is None) + grown,
        len(nodedef.outputs),
        [_multiline(specs.get(w.input_name or "")) for w in widgets],
    )
    if recorded is None:
        return estimate
    return (max(recorded[0], estimate[0]), max(recorded[1], estimate[1]))


def estimate_size(link_inputs: int, outputs: int, widgets: Sequence[bool]) -> tuple[float, float]:
    """The classic ``computeSize`` estimate. ``widgets``: one flag per persisted widget,
    true for a multiline text widget."""
    height = compute_height(link_inputs, outputs, widgets)
    if any(widgets):
        return (float(MULTILINE_WIDTH), float(max(height, MULTILINE_MIN_HEIGHT)))
    return (float(WIDGET_WIDTH if widgets else BARE_WIDTH), float(height))


def compute_height(link_inputs: int, outputs: int, widgets: Sequence[bool]) -> float:
    """Classic ``computeSize`` height, before the opening minimum of a multiline node: the
    floor a load applies to a saved height (``max(saved, computeSize())``). Matches the
    frontend after load for nodes without an image preview (``docs/m0-findings.md`` #6);
    a node that shows a preview is taller than this."""
    height = SLOT * max(link_inputs, outputs, 1)
    height += sum((MULTILINE_HEIGHT if multiline else WIDGET_HEIGHT) + WIDGET_GAP for multiline in widgets)
    if widgets:
        height += WIDGETS_PAD
    return float(height + BOTTOM)


def multiline_flags(nodedef: NodeDef, widgets: Sequence[WidgetSpec]) -> list[bool]:
    """One flag per widget of ``widgets``: true for a multiline text box. A note's text
    widget is one (m0 #6: the note's classic height after load is the multiline formula);
    :func:`node_size` does not count it, because cwc creates no plain notes."""
    specs = _input_specs(nodedef)
    note = nodedef.behavior is Behavior.NOTE
    return [note or _multiline(specs.get(w.input_name or "")) for w in widgets]


def _group_slot_linked(nodedef: NodeDef, slots: Sequence[SlotSpec], linked: Collection[str]) -> int:
    """1 when a linked slot belongs to an Autogrow group of plain (not match-type) slots, else 0."""
    groups = tuple(spec.name + "." for spec in _input_specs(nodedef).values() if spec.autogrow is not None)
    return int(any(slot.name in linked and slot.match is None and slot.name.startswith(groups) for slot in slots))


def _same_layout(nodedef: NodeDef, widgets: Sequence[WidgetSpec], slots: Sequence[SlotSpec]) -> bool:
    """Same widgets and the same set of slots as a fresh node (slot order does not change the size)."""
    fresh_slots = slot_layout(nodedef)
    return tuple(w.name for w in widgets) == tuple(w.name for w in widget_layout(nodedef)) and sorted(
        s.name for s in slots
    ) == sorted(s.name for s in fresh_slots)


def _input_specs(nodedef: NodeDef) -> dict[str, InputSpec]:
    """Every declared input by name, DynamicCombo sub-inputs included (``combo.sub``)."""
    out: dict[str, InputSpec] = {}

    def visit(specs: Iterable[InputSpec]) -> None:
        for spec in specs:
            out.setdefault(spec.name, spec)
            for _, children in spec.dynamic_options:
                visit(children)

    visit(nodedef.inputs)
    return out


def _multiline(spec: InputSpec | None) -> bool:
    return spec is not None and spec.type == _TEXT and bool(spec.options.get("multiline"))
