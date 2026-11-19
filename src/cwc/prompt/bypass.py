"""Link resolution through bypassed, muted and virtual nodes, and the factory-state prompt.

This is a port of frontend 1.59.1 ``ExecutableNodeDTO.resolveInput`` /
``resolveOutput`` / ``_getBypassSlotIndex`` (``src/lib/litegraph/src/subgraph/
ExecutableNodeDTO.ts``) and ``LiteGraphGlobal.isValidConnection``
(``src/lib/litegraph/src/LiteGraphGlobal.ts``). It is the single implementation used by
``canvas_to_prompt`` (canvas modes) and ``factory_prompt`` (optional nodes treated as
bypassed), so the two exports cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from cwc.canvas.model import NodeMode
from cwc.nodedefs.derive import widget_layout
from cwc.prompt.model import Prompt, drop_dangling_links, is_link, sort_prompt, wrap_widget_value
from cwc.prompt.slots import WILDCARD, DefLookup, InputSlot, SlotType, output_types, prompt_literals, prompt_slots

_JS_EVENT = -1  # LiteGraph.EVENT and LiteGraph.ACTION are both -1


class ResolveError(ValueError):
    """Resolution failed where the frontend throws (cycle, missing slot), so no prompt exists."""


# JavaScript value semantics for slot types -----------------------------------------


def _js_string(value: SlotType) -> str:
    if value is None:
        return ""  # only reached inside arrays: [null].toString() == ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ",".join(_js_string(item) for item in value)
    return str(value)


def _js_truthy(value: SlotType) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value != ""
    return True  # arrays and objects


def _js_loose_equal(a: SlotType, b: SlotType) -> bool:
    """``a == b`` for the value kinds a slot type can hold."""
    if a is None or b is None:
        return a is None and b is None
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers):
        return float(a) == float(b)
    if isinstance(a, list) and isinstance(b, list):
        return a is b
    if isinstance(a, numbers) or isinstance(b, numbers):
        number, other = (a, b) if isinstance(a, numbers) else (b, a)
        text = _js_string(other).strip()
        try:
            return float(text or "0") == float(number)
        except ValueError:
            return False
    return _js_string(a) == _js_string(b)


def _js_strict_equal(a: SlotType, b: SlotType) -> bool:
    """``a === b``: same kind and value; arrays only by identity."""
    numbers = (int, float)
    if isinstance(a, numbers) and not isinstance(a, bool) and isinstance(b, numbers) and not isinstance(b, bool):
        return float(a) == float(b)
    if isinstance(a, list) or isinstance(b, list):
        return a is b
    return type(a) is type(b) and a == b


def is_valid_connection(type_a: SlotType, type_b: SlotType) -> bool:
    """``LiteGraph.isValidConnection``: wildcards, case-insensitive names, comma lists."""
    if _js_loose_equal(type_a, "") or type_a == WILDCARD:
        type_a = 0
    if _js_loose_equal(type_b, "") or type_b == WILDCARD:
        type_b = 0
    if (
        not _js_truthy(type_a)
        or not _js_truthy(type_b)
        or _js_loose_equal(type_a, type_b)
        or (_js_loose_equal(type_a, _JS_EVENT) and _js_loose_equal(type_b, _JS_EVENT))
    ):
        return True
    a = _js_string(type_a).lower()
    b = _js_string(type_b).lower()
    if "," not in a and "," not in b:
        return a == b
    return any(is_valid_connection(x, y) for x in a.split(",") for y in b.split(","))


def bypass_slot_index(
    input_types: Sequence[SlotType], slot: int, output_type: SlotType, target_type: SlotType
) -> int:
    """``_getBypassSlotIndex``: the input of a bypassed node that stands in for output ``slot``.

    ``target_type`` is the type of the input that asked for the value. Returns -1 when no
    input fits.
    """
    if _js_strict_equal(target_type, WILDCARD) or _js_strict_equal(target_type, ""):
        return slot if len(input_types) > slot else 0
    if slot < len(input_types):
        opposite = input_types[slot]
        if is_valid_connection(opposite, output_type) and is_valid_connection(opposite, target_type):
            return slot
    for index, input_type in enumerate(input_types):
        if _js_strict_equal(input_type, target_type):
            return index
    for index, input_type in enumerate(input_types):
        if is_valid_connection(input_type, output_type) and is_valid_connection(input_type, target_type):
            return index
    return -1


# Resolution -----------------------------------------------------------------------


@dataclass(frozen=True)
class ResolveNode:
    """What resolution needs to know about one node."""

    key: str
    mode: int
    virtual: bool
    inputs: tuple[InputSlot, ...]
    outputs: tuple[SlotType, ...]


Graph = dict[str, ResolveNode]


def resolve_input(
    graph: Graph, key: str, slot: int, target_type: SlotType = None
) -> tuple[str, int] | None:
    """``resolveInput``: the real ``(origin key, output slot)`` feeding input ``slot`` of
    node ``key``, or None when nothing reaches it (the prompt then omits the input).

    ``target_type`` None means "the type of this input", as at the top-level call.
    """
    return _resolve(graph, ("I", key, slot, target_type))


def resolve_output(graph: Graph, key: str, slot: int, target_type: SlotType) -> tuple[str, int] | None:
    """``resolveOutput`` for a link from output ``slot`` of ``key`` into an input of ``target_type``."""
    return _resolve(graph, ("O", key, slot, target_type))


def _resolve(graph: Graph, step: tuple[str, str, int, SlotType]) -> tuple[str, int] | None:
    """Iterative form of the frontend's mutual recursion, so long chains cannot overflow."""
    visited: set[tuple[str, str, int]] = set()
    while True:
        kind, key, slot, target_type = step
        _visit(visited, kind, key, slot)
        node = graph.get(key)
        if node is None:
            return None  # dangling link; the structure checks report it
        if kind == "I":
            if not 0 <= slot < len(node.inputs):
                raise ResolveError(f"node {key} has no input slot {slot}")
            socket = node.inputs[slot]
            if socket.source is None:
                return None
            origin, origin_slot = socket.source
            step = ("O", origin, origin_slot, socket.type if target_type is None else target_type)
            continue
        if node.mode == NodeMode.NEVER:
            return None
        if node.mode == NodeMode.BYPASS:
            if not 0 <= slot < len(node.outputs):
                raise ResolveError(f"bypassed node {key} has no output slot {slot}")
            index = bypass_slot_index([s.type for s in node.inputs], slot, node.outputs[slot], target_type)
            if index == -1:
                return None
            step = ("I", key, index, None)  # the frontend drops the requested type here
            continue
        if node.virtual:
            # Pass through the virtual node's own input with the same index.
            if slot >= len(node.inputs) or node.inputs[slot].source is None:
                return None
            step = ("I", key, slot, target_type)
            continue
        return (key, slot)


def _visit(visited: set[tuple[str, str, int]], kind: str, key: str, slot: int) -> None:
    marker = (kind, key, slot)
    if marker in visited:
        side = "input" if kind == "I" else "output"
        raise ResolveError(f"circular reference while resolving {side} {slot} of node {key}")
    visited.add(marker)


# Factory-state prompt -------------------------------------------------------------


def factory_prompt(full: Prompt, defs: DefLookup, optional_ids: Iterable[str]) -> Prompt:
    """The prompt the frontend exports once the ``optional_ids`` nodes are bypassed (mode 4).

    ``full`` is the all-enabled strict prompt. Links into optional nodes are re-resolved
    with the bypass rule (chains recurse, cycles raise :class:`ResolveError`); an input
    that resolves to nothing is dropped, except a widget input, which falls back to its
    widget default: the canvas keeps a linked widget's stored value, and instantiate
    stores the default there. Optional nodes are removed.
    """
    optional = set(optional_ids)
    graph = {key: _prompt_node(key, entry, defs, key in optional) for key, entry in full.items()}
    out: Prompt = {}
    for key, entry in full.items():
        if key in optional:
            continue
        inputs = dict(entry.get("inputs", {}))
        for name, value in entry.get("inputs", {}).items():
            if not (is_link(value) and value[0] in optional):
                continue
            slot_type, default = _consumer_input(key, entry, name, defs)
            resolved = resolve_output(graph, value[0], value[1], slot_type)
            if resolved is not None:
                inputs[name] = [resolved[0], resolved[1]]
            elif default is not _NO_WIDGET:
                inputs[name] = default
            else:
                del inputs[name]
        out[key] = {**entry, "inputs": inputs}
    return sort_prompt(drop_dangling_links(out))


_NO_WIDGET = object()


def _prompt_node(key: str, entry: dict, defs: DefLookup, bypassed: bool) -> ResolveNode:
    nodedef = defs.get(entry["class_type"])
    if nodedef is None:
        if bypassed:
            raise ValueError(f"optional node {key} has unknown type {entry['class_type']!r}")
        return ResolveNode(key, NodeMode.ALWAYS, False, (), ())
    inputs = entry.get("inputs", {})
    slots = tuple(
        InputSlot(slot.name, slot.type, slot.widget, _link_source(inputs.get(slot.name)))
        for slot in prompt_slots(nodedef, inputs)
    )
    mode = NodeMode.BYPASS if bypassed else NodeMode.ALWAYS
    return ResolveNode(key, mode, nodedef.virtual, slots, output_types(nodedef))


def _link_source(value: object) -> tuple[str, int] | None:
    return (value[0], value[1]) if is_link(value) else None  # type: ignore[index]


def _consumer_input(key: str, entry: dict, name: str, defs: DefLookup) -> tuple[SlotType, object]:
    """Type of the consuming socket, and the widget default it falls back to."""
    nodedef = defs.get(entry["class_type"])
    if nodedef is None:
        raise ValueError(f"node {key} has unknown type {entry['class_type']!r} and links to an optional node")
    inputs = entry.get("inputs", {})
    for slot in prompt_slots(nodedef, inputs):
        if slot.name == name:
            break
    else:
        raise ValueError(f"node {key} input {name!r} is not a socket of {entry['class_type']!r}")
    if slot.widget is None:
        return slot.type, _NO_WIDGET
    # The layout for the chosen values: DynamicCombo sub-widgets fall back to their own default.
    for widget in widget_layout(nodedef, prompt_literals(inputs)):
        if widget.name == slot.widget and widget.to_prompt:
            return slot.type, wrap_widget_value(widget.default)
    return slot.type, _NO_WIDGET
