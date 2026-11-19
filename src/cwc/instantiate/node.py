"""One canvas node from one prompt entry, in the shape frontend 1.59.1 ``serialize()`` writes.

Key order and fields follow ``tests/fixtures/instantiate/frontend_serialize.json`` (a graph
built by hand in a real frontend) and the load probes in
``tests/fixtures/instantiate/load_probes.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cwc.canvas.model import NodeMode
from cwc.nodedefs.derive import widget_layout
from cwc.nodedefs.sizing import estimate_size, node_size
from cwc.nodedefs.types import NodeDef, OutputSpec, SlotSpec, WidgetKind, WidgetSpec
from cwc.prompt.dynamic import dynamic_widgets, escape
from cwc.prompt.slots import prompt_slots
from cwc.prompt.model import is_link, unwrap_prompt_value

OPTIONAL_SHAPE = 7  # RenderShape.HOLLOW, drawn for optional inputs
UPLOAD_LABEL = "choose file to upload"  # localized_name the frontend gives an upload button slot
SEARCH_NAME = "Node name for S&R"
WILDCARD = "*"


@dataclass(frozen=True)
class Slot:
    """An input slot of the node being built, with its link source."""

    spec: SlotSpec
    source: tuple[str, int] | None  # (origin key, origin output slot)


def input_slots(nodedef: NodeDef | None, inputs: Mapping[str, Any]) -> list[Slot]:
    """The node's input slots in frontend order. A node without a definition gets one
    wildcard slot per linked input, in prompt order."""
    if nodedef is None:
        return [Slot(SlotSpec(name, WILDCARD), (v[0], v[1])) for name, v in inputs.items() if is_link(v)]
    return [Slot(spec, _source(inputs.get(spec.name))) for spec in prompt_slots(nodedef, inputs)]


def _source(value: Any) -> tuple[str, int] | None:
    return (value[0], value[1]) if is_link(value) else None


def widget_values(
    nodedef: NodeDef | None, inputs: Mapping[str, Any], values: Mapping[str, Any] | None
) -> tuple[list[Any], dict[str, Any] | None]:
    """``(widgets_values, widgets_values_named)``; named is None when names are incomplete.

    ``values`` are normalize's widget values by name (defaults filled in). A node without
    a definition keeps its literal inputs by name, the only form the exporter can read.
    A string for a dynamic-prompt widget is written as :func:`cwc.prompt.dynamic.escape`
    spells it, so the frontend's export gives the text back; ``ValueError`` when no
    spelling exists (normalize reports that as E5 first).
    """
    if nodedef is None:
        named = {name: unwrap_prompt_value(v) for name, v in inputs.items() if not is_link(v)}
        return list(named.values()), named
    values = values or {}
    layout = widget_layout(nodedef, values)
    dynamic = dynamic_widgets(nodedef, layout)
    positional = []
    for w in layout:
        value = values.get(w.name, w.default) if w.name is not None else w.default
        positional.append(_escaped(nodedef, w, value) if w.name in dynamic else value)
    if not nodedef.names_complete:
        return positional, None
    return positional, {w.name: value for w, value in zip(layout, positional) if w.name is not None}


def _escaped(nodedef: NodeDef, widget: WidgetSpec, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    spelled = escape(value)
    if spelled is None:
        raise ValueError(f"{nodedef.type}.{widget.name}: no dynamic-prompt spelling exports {value!r}")
    return spelled


def output_types(nodedef: NodeDef | None, slots: list[Slot], link_types: Mapping[tuple[str, int], str], count: int) -> list[str]:
    """Output types as the frontend shows them once linked.

    A match-type output takes the type that reaches the first linked input of its
    template, as the frontend resolves it when links are connected; unlinked, it stays
    a wildcard.
    """
    if nodedef is None:
        return [WILDCARD] * count
    return [_matched_type(out, slots, link_types) for out in nodedef.outputs]


def _matched_type(out: OutputSpec, slots: list[Slot], link_types: Mapping[tuple[str, int], str]) -> str:
    if out.match is None:
        return out.type
    return _linked_type(slots, out.match, None, link_types) or out.type


def input_types(slots: list[Slot], link_types: Mapping[tuple[str, int], str]) -> list[str]:
    """Input socket types as a real frontend writes them after loading the canvas.

    A match-type slot takes the type that reaches the first *other* linked slot of its
    template; its own link does not count, so a template with one linked slot keeps the
    declared type on that slot (a wildcard, or a union such as ``IMAGE,MASK``) and every
    unlinked slot of the template takes the linked type. Measured on frontend 1.59.1, see
    ``tests/fixtures/instantiate/load_probes.md``.
    """
    return [
        slot.spec.type if slot.spec.match is None else _linked_type(slots, slot.spec.match, index, link_types) or slot.spec.type
        for index, slot in enumerate(slots)
    ]


def _linked_type(slots: list[Slot], match: str, skip: int | None, link_types: Mapping[tuple[str, int], str]) -> str | None:
    """The type reaching the first linked slot of template ``match`` other than slot ``skip``."""
    for index, slot in enumerate(slots):
        if index != skip and slot.spec.match == match and slot.source is not None and slot.source in link_types:
            return link_types[slot.source]
    return None


def size(nodedef: NodeDef | None, values: Mapping[str, Any] | None, slots: list[Slot], outputs: int, widgets: int) -> tuple[float, float]:
    if nodedef is None:
        return estimate_size(len(slots), outputs, [False] * widgets)
    linked = {slot.spec.name for slot in slots if slot.source is not None}
    return node_size(nodedef, values, slots=[slot.spec for slot in slots], linked=linked)


def input_entry(spec: SlotSpec, type_: str, widget: WidgetSpec | None, link: int | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"localized_name": _label(spec, widget), "name": spec.name}
    if spec.optional:
        entry["shape"] = OPTIONAL_SHAPE
    entry["type"] = type_
    if spec.widget is not None:
        entry["widget"] = {"name": spec.widget}
    entry["link"] = link
    return entry


def _label(spec: SlotSpec, widget: WidgetSpec | None) -> str:
    if widget is not None and widget.kind is WidgetKind.UPLOAD:
        return UPLOAD_LABEL
    return spec.name.rsplit(".", 1)[-1]


def output_entry(name: str, type_: str, links: list[int]) -> dict[str, Any]:
    return {"localized_name": name, "name": name, "type": type_, "links": links or None}


def properties(type_: str, nodedef: NodeDef | None) -> dict[str, Any]:
    props: dict[str, Any] = {SEARCH_NAME: type_}
    pack = nodedef.pack if nodedef is not None else None
    if pack is not None and pack.cnr_id is not None:
        props["cnr_id"] = pack.cnr_id
        if pack.ver is not None:
            props["ver"] = pack.ver
    return props


def mode(optional: bool) -> int:
    return int(NodeMode.BYPASS if optional else NodeMode.ALWAYS)
