"""Input sockets with their link sources, shared by both prompt exporters.

Bypass resolution picks an input *slot index*, so both exporters (canvas and prompt)
must agree on the order of a node's input sockets. That order has one source,
:func:`cwc.nodedefs.derive.slot_layout`. ``ComfyNode.configure`` then merges a saved
canvas's ``inputs`` into that list by name and appends saved inputs the definition does
not know (:func:`merge_saved_inputs`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Union

from cwc.nodedefs.derive import WidgetValues, slot_layout
from cwc.nodedefs.types import NodeDef, SlotSpec
from cwc.prompt.model import is_link

# A litegraph slot type: usually a string ("IMAGE", "*", "INT,FLOAT"); saved canvases
# may also hold numbers or string lists. Compared with JavaScript rules in ``bypass``.
SlotType = Union[str, int, float, list, None]

WILDCARD = "*"


class DefLookup(Protocol):
    """The one thing the prompt package needs from the node definition registry."""

    def get(self, type: str) -> NodeDef | None: ...


@dataclass(frozen=True)
class InputSlot:
    """One input socket: its name, type, the widget behind it (if any) and its link source."""

    name: str
    type: SlotType
    widget: str | None = None  # name of the widget this socket feeds
    source: tuple[str, int] | None = None  # (origin node key, origin output slot)


def defined_input_slots(nodedef: NodeDef, values: WidgetValues | None = None) -> tuple[InputSlot, ...]:
    """Input sockets of a node of this type, all unlinked: ``slot_layout`` as ``InputSlot``s.
    ``values`` are the node's widget values (they place DynamicCombo sub-inputs)."""
    return tuple(InputSlot(slot.name, slot.type, slot.widget) for slot in slot_layout(nodedef, values))


def prompt_literals(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The literal inputs of a prompt entry: its widget values by name."""
    return {name: value for name, value in inputs.items() if not is_link(value)}


def prompt_slots(nodedef: NodeDef, inputs: Mapping[str, Any]) -> tuple[SlotSpec, ...]:
    """The ``inputs`` array of a loaded node holding a prompt entry's ``inputs``: placed by
    its widget values, with Autogrow groups grown for its links."""
    linked = [name for name, value in inputs.items() if is_link(value)]
    return slot_layout(nodedef, prompt_literals(inputs), linked)


def output_types(nodedef: NodeDef) -> tuple[SlotType, ...]:
    """Output socket types (definitions already create match-type outputs as wildcards)."""
    return tuple(out.type for out in nodedef.outputs)


def saved_input_slot(entry: dict[str, Any], source: tuple[str, int] | None) -> InputSlot:
    """A socket exactly as a saved canvas lists it."""
    widget = entry.get("widget")
    return InputSlot(
        entry.get("name", ""),
        entry.get("type"),
        widget.get("name") if isinstance(widget, dict) else None,
        source,
    )


def merge_saved_inputs(defined: tuple[InputSlot, ...], saved: list[InputSlot]) -> tuple[InputSlot, ...]:
    """``ComfyNode.configure``: defined sockets keep name, type and widget and take the link
    of the saved socket with the same name (the last one wins, as in a ``Map``); saved
    sockets unknown to the definition follow in saved order."""
    by_name = {slot.name: slot for slot in saved}
    defined_names = {slot.name for slot in defined}
    merged = [
        InputSlot(slot.name, slot.type, slot.widget, by_name[slot.name].source if slot.name in by_name else None)
        for slot in defined
    ]
    merged += [slot for slot in saved if slot.name not in defined_names]
    return tuple(merged)
