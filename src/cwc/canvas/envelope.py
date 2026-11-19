"""The node box model shared by layout and every geometry check.

A node is drawn by one of two renderers, and a user may switch between them, so cwc
reserves the union of both boxes: :func:`envelope`. Rules and their sources are in
``docs/m2-frontend-facts.md`` (section 3) and ``docs/m0-findings.md`` (#6):

* Classic, expanded: ``[x, y - 30, w, h + 30]`` with the live size
  ``max(saved, computeSize())``. Collapsed: ``[x, y - 30, min(w, title + 60), 30]``.
* Vue, expanded: ``[x, y - 30, max(w, 225), max(live h, natural body h) + 30]``.
  Collapsed: ``[x, y - 30, max(225, title + VUE_COLLAPSED_PAD), 30]``.

What the canvas does not say (the title's text width, the classic minimum height, the
natural Vue body height, widget rows) is in :class:`BoxFacts`; :func:`box_facts` derives
it from the node definition. Text widths come from the measured per-character table
(:mod:`cwc.canvas.textwidth`). Every derived number is an upper bound where it is not
exact, so the envelope holds the box a real frontend draws (checked against
``tests/fixtures/layout/`` and the m0 #6 table in ``tests/test_canvas_envelope.py``).
Known gaps: a node with an image preview loaded below its classic minimum height (the
preview is not in ``computeSize`` here), and widths below the classic minimum width
(not modelled; cwc writes sizes at or above it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from cwc.canvas.geometry import TITLE_HEIGHT, Rect
from cwc.canvas.model import Node
from cwc.canvas.textwidth import text_width
from cwc.nodedefs.derive import widget_layout
from cwc.nodedefs.sizing import (
    MULTILINE_HEIGHT,
    SLOT,
    WIDGET_GAP,
    WIDGET_HEIGHT,
    _input_specs,
    compute_height,
    multiline_flags,
)
from cwc.nodedefs.types import NodeDef, WidgetKind, WidgetSpec

Point = tuple[float, float]


class Renderer(str, Enum):
    CLASSIC = "classic"
    VUE = "vue"


COLLAPSED_PAD = 60  # classic collapsed width: title text + 60 (measured)
VUE_MIN_WIDTH = 225
# Vue collapsed width: max(225, title text + 36), the text measured at COLLAPSED_FONT. Fitted on
# five titles of 36 to 94 characters (frontend 1.59.1): the box minus the whole-string
# ``measureText`` width was 36.000 to 36.014 px for all five, rounded up to a hundredth
# (``collapsed_fit`` in tests/fixtures/layout/char_widths.measured.json).
VUE_COLLAPSED_PAD = 36.02
TITLE_FONT = 14  # LiteGraph NODE_TEXT_SIZE: the font a node title is drawn in
WIDGET_FONT = 12  # LiteGraph NODE_SUBTEXT_SIZE: slot and widget labels
# LGraphNode.measure measures a collapsed node's title with the sub-text font, not the title
# font (LGraphNode.ts:2532), and the Vue header text is 12 px as well.
COLLAPSED_FONT = WIDGET_FONT
# Readable width (G7, see readable_width). Estimates, not measurements: the title row holds the
# collapse box and padding as the collapsed width does; a widget row holds two 15 px margins, two
# 20 px arrows and about 30 px of value text beside its label.
TITLE_ROOM = 60
WIDGET_VALUE_ROOM = 100

# Vue body: VUE_TOP + 20 per slot row + 28 per widget row (a value-control combo shares its
# number's row) + VUE_MULTILINE per text box + VUE_PREVIEW for an image preview + VUE_SLACK.
# Fitted on the nine m0 #6 nodes; VUE_SLACK covers the largest miss (the checkpoint loader's
# file combo, 24 px taller than one row). An upper bound, not a fit for every node.
VUE_TOP = 14
VUE_SLOT = 20
VUE_WIDGET = 28
VUE_MULTILINE = 74
# 244, not the 240 fitted in m0 #6: an enabled image loader whose image the server does not have
# (a 404) is 4 px taller in Vue (tests/test_layout_frontend.py, frontend 1.59.1). A loaded image
# of another aspect ratio is not measured.
VUE_PREVIEW = 244
VUE_SLACK = 24
# Classic widget rows start 16 below the slot rows.
CLASSIC_WIDGET_TOP = 16
VUE_SLOT_TOP = 12
# Vue widget rows (docs/m2-frontend-facts.md section 5.1, frontend 1.59.1, measured in
# tests/fixtures/layout/*.vue.json): the rows start VUE_WIDGETS_TOP below the slot rows
# (20 per row, at least one). Each row's height and the anchor's offset within it:
#   plain widget 28, anchor 14; single-line text 36, anchor 18; a widget input fed by a link
#   (the widget is replaced by its slot) 24, anchor 12; multi-line text box 68, anchor 34;
#   an advanced widget not fed by a link is hidden and takes no row.
# The natural body ends VUE_WIDGETS_BOTTOM below the last row, plus VUE_ADVANCED_FOOTER when
# a widget is hidden (the advanced-inputs button). A body taller than natural (the node's
# height) gives the extra height to the multi-line boxes in equal shares; a box's anchor
# stays at its middle, so it moves down by half its own share plus the shares above it.
VUE_WIDGETS_TOP = 4
VUE_TEXT_ROW = 36
VUE_LINKED_ROW = 24
VUE_MULTILINE_ROW = 68
VUE_WIDGETS_BOTTOM = 10
VUE_ADVANCED_FOOTER = 24
_TEXT_TYPE = "STRING"
# Slot anchors (section 5 of the facts): classic inputs 10 px in, rows at 0.7 of 20 px;
# Vue anchors on the box edge, rows 12 + 20 r; collapsed anchors at the band's middle.
CLASSIC_SLOT_INSET = 10
CLASSIC_ROW_CENTRE = 0.7
CLASSIC_COLLAPSED_Y = TITLE_HEIGHT / 2
VUE_COLLAPSED_Y = 16

_ROW_KINDS = (WidgetKind.VALUE, WidgetKind.COMPANION, WidgetKind.EXTRA, WidgetKind.FILTER)


@dataclass(frozen=True)
class BoxFacts:
    """What a node's box needs beyond the canvas entry.

    ``title_width``: width of the title text measured as a collapsed node measures it
    (``COLLAPSED_FONT``). ``drawn_title_width``: width of the same text at ``TITLE_FONT``, as an
    expanded node draws it; measured in its own table, not scaled from ``title_width``, because
    the two fonts are not proportional (an emoji is 15 px at 12 px and 18 px at 14 px). Both are
    upper bounds where they are estimates. ``min_height``: classic ``computeSize`` height, the
    floor a load puts under the saved height (0 when unknown). ``vue_body``: natural Vue body height, title
    excluded (``None`` when unknown: the Vue box then takes the classic height plus
    ``VUE_SLACK``). ``classic_widgets`` and ``vue_widgets``: widget name to the y offset
    from ``pos`` of its row, where a widget-backed input slot is drawn. ``vue_widgets``
    holds for the node's height and links at the time ``box_facts`` ran: a multi-line box
    grows with the node, and a widget fed by a link takes a shorter row.
    """

    title_width: float
    drawn_title_width: float
    min_height: float = 0.0
    vue_body: float | None = None
    classic_widgets: Mapping[str, float] = field(default_factory=dict)
    vue_widgets: Mapping[str, float] = field(default_factory=dict)


def title_text(node: Node, nodedef: NodeDef | None) -> str:
    """The title the frontend draws: the saved title, else the type's display name, else the type."""
    if node.title is not None:
        return node.title
    if nodedef is not None and nodedef.display_name:
        return nodedef.display_name
    return node.type


def title_width(text: str, size: float = TITLE_FONT) -> float:
    """Upper-bound width of ``text`` at font ``size`` (the title font by default): the sum of
    measured per-character advances (``src/cwc/data/char_widths.json``), exact in the two measured
    sizes (``TITLE_FONT``, ``WIDGET_FONT``), scaled linearly in any other. At 12 px the
    frontend measures 7.7 for "P", 34.6 for "Below", 70.4 for "VAE Decode" and 248.7 for a
    46-character title; this gives 7.8, 35.4, 72.6 and 253.7."""
    return text_width(text, size)


def readable_width(facts: BoxFacts) -> float:
    """Narrowest expanded width at which, by the text-width estimate, neither the title nor
    a widget label is cut: the title needs its text at the title font (``drawn_title_width``)
    plus ``TITLE_ROOM``, a widget row its label plus ``WIDGET_VALUE_ROOM``. Layout widens an
    expanded node to at least this (and to ``VUE_MIN_WIDTH``); G7 counts the expanded nodes
    narrower than it. Both read only this function, so they cannot disagree."""
    need = facts.drawn_title_width + TITLE_ROOM
    for name in facts.classic_widgets:
        need = max(need, title_width(name, WIDGET_FONT) + WIDGET_VALUE_ROOM)
    return need


def box_facts(node: Node, nodedef: NodeDef | None) -> BoxFacts:
    """:class:`BoxFacts` for ``node`` from its definition (``None`` for an unknown type)."""
    text = title_text(node, nodedef)
    width, drawn = title_width(text, COLLAPSED_FONT), title_width(text, TITLE_FONT)
    if nodedef is None:
        return BoxFacts(title_width=width, drawn_title_width=drawn)
    rows = _slot_rows(node)
    widgets = widget_layout(nodedef, node.widgets_values_named or node.widgets_values)
    multiline = multiline_flags(nodedef, widgets)
    min_height = compute_height(_link_inputs(node), len(node.outputs), multiline)
    return BoxFacts(
        title_width=width,
        drawn_title_width=drawn,
        min_height=min_height,
        vue_body=_vue_body(nodedef, widgets, multiline, rows),
        classic_widgets=_classic_widget_rows(widgets, multiline, rows),
        vue_widgets=_vue_widget_rows(node, nodedef, widgets, multiline, rows, max(node.size[1], min_height)),
    )


def live_size(node: Node, facts: BoxFacts) -> tuple[float, float]:
    """Classic size after a load: the saved size, grown to the classic minimum height."""
    w, h = node.size
    return (w, max(h, facts.min_height))


def classic_box(node: Node, facts: BoxFacts) -> Rect:
    """The classic renderer's ``boundingRect`` in the node's current state."""
    x, y = node.pos
    w, h = live_size(node, facts)
    if node.collapsed:
        return Rect(x, y - TITLE_HEIGHT, min(w, facts.title_width + COLLAPSED_PAD), TITLE_HEIGHT)
    return Rect(x, y - TITLE_HEIGHT, w, h + TITLE_HEIGHT)


def vue_box(node: Node, facts: BoxFacts) -> Rect:
    """The Vue renderer's DOM box in the node's current state (the collapsed hit box is 30
    high although the DOM header measures 28)."""
    x, y = node.pos
    w, h = live_size(node, facts)
    if node.collapsed:
        return Rect(x, y - TITLE_HEIGHT, max(VUE_MIN_WIDTH, facts.title_width + VUE_COLLAPSED_PAD), TITLE_HEIGHT)
    body = facts.vue_body if facts.vue_body is not None else h + VUE_SLACK
    return Rect(x, y - TITLE_HEIGHT, max(w, VUE_MIN_WIDTH), max(h, body) + TITLE_HEIGHT)


def envelope(node: Node, facts: BoxFacts) -> Rect:
    """The box layout reserves and every check tests: the union of the classic and the Vue
    box in the node's current state, title band included."""
    return classic_box(node, facts).union(vue_box(node, facts))


def expanded_centre(node: Node, facts: BoxFacts) -> Point:
    """Centre of the envelope the node would have if expanded. A group holding a collapsed
    node must hold this point as well as the node's own envelope, so that expanding the
    node later keeps it in the group (membership is decided by the box centre)."""
    x, y = node.pos
    w, h = live_size(node, facts)
    body = facts.vue_body if facts.vue_body is not None else h + VUE_SLACK
    rect = Rect(x, y - TITLE_HEIGHT, max(w, VUE_MIN_WIDTH), max(h, body) + TITLE_HEIGHT)
    return rect.center


def slot_anchors(node: Node, facts: BoxFacts, renderer: Renderer) -> tuple[list[Point], list[Point]]:
    """``(inputs, outputs)``: where each input and output slot of ``node`` is drawn, in the
    order of the node's ``inputs`` and ``outputs`` arrays. A widget-backed input sits on its
    widget's row; one whose widget row is unknown takes the next plain row."""
    x, y = node.pos
    box = classic_box(node, facts) if renderer is Renderer.CLASSIC else vue_box(node, facts)
    n_in, n_out = len(node.inputs), len(node.outputs)
    if node.collapsed:
        dy = CLASSIC_COLLAPSED_Y if renderer is Renderer.CLASSIC else VUE_COLLAPSED_Y
        return [(x, y - dy)] * n_in, [(box.right, y - dy)] * n_out
    if renderer is Renderer.CLASSIC:
        def row(r: int) -> float:
            return y + (r + CLASSIC_ROW_CENTRE) * SLOT

        in_x, out_x, widgets = x + CLASSIC_SLOT_INSET, x + live_size(node, facts)[0] + 1 - CLASSIC_SLOT_INSET, facts.classic_widgets
    else:
        def row(r: int) -> float:
            return y + VUE_SLOT_TOP + VUE_SLOT * r

        in_x, out_x, widgets = x, box.right, facts.vue_widgets
    inputs: list[Point] = []
    plain = 0
    for slot in node.inputs:
        name = _slot_widget(slot)
        if name is not None and name in widgets:
            inputs.append((in_x, y + widgets[name]))
        else:
            inputs.append((in_x, row(plain)))
            plain += 1
    return inputs, [(out_x, row(r)) for r in range(n_out)]


def _slot_widget(slot: Mapping[str, object]) -> str | None:
    widget = slot.get("widget")
    if isinstance(widget, Mapping):
        name = widget.get("name")
        return name if isinstance(name, str) else None
    return None


def _link_inputs(node: Node) -> int:
    return sum(1 for slot in node.inputs if _slot_widget(slot) is None)


def _slot_rows(node: Node) -> int:
    return max(_link_inputs(node), len(node.outputs))


def _vue_body(nodedef: NodeDef, widgets: tuple[WidgetSpec, ...], multiline: list[bool], rows: int) -> float:
    body = VUE_TOP + VUE_SLOT * rows + VUE_SLACK
    for widget, text_box in zip(widgets, multiline):
        if text_box:
            body += VUE_MULTILINE
        elif widget.kind in _ROW_KINDS:
            body += VUE_WIDGET
    if shows_preview(nodedef):
        body += VUE_PREVIEW
    return float(body)


def shows_preview(nodedef: NodeDef) -> bool:
    """An output node that takes images, or a node with an upload button, shows an image."""
    if any(w.kind is WidgetKind.UPLOAD for w in nodedef.widgets):
        return True
    return nodedef.output_node and any(spec.type == "IMAGE" for spec in nodedef.inputs)


def _classic_widget_rows(widgets: tuple[WidgetSpec, ...], multiline: list[bool], rows: int) -> dict[str, float]:
    out: dict[str, float] = {}
    offset = SLOT * max(rows, 1) + CLASSIC_WIDGET_TOP
    for widget, text_box in zip(widgets, multiline):
        if widget.name is not None:
            out.setdefault(widget.name, float(offset))
        offset += (MULTILINE_HEIGHT if text_box else WIDGET_HEIGHT) + WIDGET_GAP
    return out


def _vue_widget_rows(
    node: Node, nodedef: NodeDef, widgets: tuple[WidgetSpec, ...], multiline: list[bool], rows: int, height: float
) -> dict[str, float]:
    """Widget name to the y offset of its input anchor under Vue, for a body ``height`` high
    (the node's live height; the body is never shorter than its natural height)."""
    specs = _input_specs(nodedef)
    linked = {name for slot in node.inputs if slot.get("link") is not None and (name := _slot_widget(slot)) is not None}
    # (name, row height, anchor offset in the row, grows); a hidden widget is a row of height 0
    laid: list[tuple[str | None, float, float, bool]] = []
    hidden = False
    for widget, text_box in zip(widgets, multiline):
        if widget.kind not in _ROW_KINDS:
            continue
        # only a backend input's own widget can be hidden, a text box or a text row
        spec = specs.get(widget.input_name or "") if widget.kind is WidgetKind.VALUE else None
        if widget.name in linked:
            laid.append((widget.name, VUE_LINKED_ROW, VUE_LINKED_ROW / 2, False))
        elif spec is not None and spec.options.get("advanced"):
            hidden = True
            laid.append((widget.name, 0.0, 0.0, False))
        elif text_box:
            laid.append((widget.name, VUE_MULTILINE_ROW, VUE_MULTILINE_ROW / 2, True))
        elif spec is not None and spec.type == _TEXT_TYPE:
            laid.append((widget.name, VUE_TEXT_ROW, VUE_TEXT_ROW / 2, False))
        else:
            laid.append((widget.name, VUE_WIDGET, VUE_WIDGET / 2, False))
    top = VUE_SLOT * max(rows, 1) + VUE_WIDGETS_TOP
    natural = top + sum(row[1] for row in laid) + VUE_WIDGETS_BOTTOM + (VUE_ADVANCED_FOOTER if hidden else 0)
    growing = sum(1 for row in laid if row[3])
    share = max(0.0, height - natural) / growing if growing else 0.0
    out: dict[str, float] = {}
    offset = float(top)
    for name, row_height, anchor, grows in laid:
        extra = share if grows else 0.0
        if name is not None:
            out.setdefault(name, offset + anchor + extra / 2)
        offset += row_height + extra
    return out
