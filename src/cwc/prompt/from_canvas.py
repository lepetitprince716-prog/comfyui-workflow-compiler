"""Canvas to API prompt: a port of frontend 1.59.1 ``graphToPrompt`` (``src/utils/executionUtil.ts``).

Per node, in order:

1. Virtual nodes (``NodeDef.virtual``), muted (mode 2) and bypassed (mode 4) nodes are
   not emitted.
2. Every widget with a name whose ``to_prompt`` is true writes its value; list values are
   wrapped as ``{"__value__": [...]}``. Values come from ``widgets_values`` by position.
   ``widgets_values_named`` is used only with ``named_restore=True`` (see below).
3. Every input socket is resolved through bypassed, muted and virtual nodes
   (:mod:`cwc.prompt.bypass`); a resolved link overwrites the widget value, an unresolved
   one leaves the input out (or leaves the widget value in place).
4. Link inputs whose origin is not emitted are removed.

Before that, every primitive builtin writes its first widget value into the widgets its
output links reach (``applyToGraph``), whatever the modes involved.

A string value of a widget whose input has ``dynamicPrompts: true`` is exported as the
frontend's ``serializeValue`` returns it, :func:`cwc.prompt.dynamic.process`: comments are
stripped and escapes and choice blocks resolved. This holds for a value a primitive
pushed as well (the primitive sets the widget value, then ``serializeValue`` runs). A
random choice block ``{a|b}`` exports its first option here; the frontend picks one at
random each time, so a caller that has to know learns it from ``process(value).random``.

Subgraph instances are not expanded; a canvas that uses them raises ``ValueError``.
"""

from __future__ import annotations

from typing import Any, Callable

from cwc.canvas.model import Canvas, Link, Node, NodeMode
from cwc.nodedefs.types import Behavior, NodeDef, WidgetSpec
from cwc.prompt.bypass import Graph, ResolveNode, resolve_input
from cwc.prompt.dynamic import dynamic_widgets, process
from cwc.prompt.model import Prompt, drop_dangling_links, sort_prompt, wrap_widget_value
from cwc.prompt.slots import (
    DefLookup,
    InputSlot,
    defined_input_slots,
    merge_saved_inputs,
    output_types,
    saved_input_slot,
)

# Injection point for DynamicCombo-aware layouts: ``cwc.nodedefs.derive.widget_layout``
# has this shape. ``values`` is ``widgets_values_named`` when the node has it, otherwise
# ``widgets_values``.
WidgetLayout = Callable[[NodeDef, Any], tuple[WidgetSpec, ...]]


def static_layout(nodedef: NodeDef, values: Any) -> tuple[WidgetSpec, ...]:
    """The layout with no DynamicCombo sub-widgets selected."""
    return nodedef.widgets


def canvas_to_prompt(
    canvas: Canvas,
    defs: DefLookup,
    *,
    widget_layout: WidgetLayout = static_layout,
    named_restore: bool = False,
) -> Prompt:
    """The API prompt the frontend would queue for ``canvas``, ordered by numeric node id.

    ``named_restore`` states that the frontend setting ``Comfy.Workflow.NamedValuesRestore``
    is on. It is off by default in frontend 1.59.1, and a real frontend loaded with the
    default restores ``widgets_values`` by position and ignores ``widgets_values_named``
    (checked on a real 1.59.1 frontend: the oracle fixture ``widgets_named_vs_positional``).
    With the setting on, a node that has ``widgets_values_named`` is restored by name, and
    a widget missing from the table keeps its default (``LGraphNode.configure``,
    ``widgetValueStore``).
    """
    _reject_subgraphs(canvas)
    nodes = canvas.nodes
    links = canvas.link_map()
    nodedefs = {node.key: defs.get(node.type) for node in nodes}
    graph: Graph = {node.key: _resolve_node(node, nodedefs[node.key], links, named_restore) for node in nodes}
    pushed = _primitive_values(nodes, nodedefs, graph, links)

    out: Prompt = {}
    for node in nodes:
        nodedef = nodedefs[node.key]
        if (nodedef is not None and nodedef.virtual) or node.mode in (NodeMode.NEVER, NodeMode.BYPASS):
            continue
        inputs = _widget_inputs(node, nodedef, widget_layout, pushed.get(node.key, {}), named_restore)
        for index, socket in enumerate(graph[node.key].inputs):
            resolved = resolve_input(graph, node.key, index)
            if resolved is not None:
                inputs[socket.name] = [resolved[0], resolved[1]]
        out[node.key] = {"inputs": inputs, "class_type": node.type, "_meta": {"title": _title(node, nodedef)}}
    return sort_prompt(drop_dangling_links(out))


def _reject_subgraphs(canvas: Canvas) -> None:
    definitions = canvas.raw.get("definitions") or {}
    ids = {sub.get("id") for sub in definitions.get("subgraphs") or [] if isinstance(sub, dict)}
    used = sorted(node.key for node in canvas.nodes if node.type in ids)
    if used:
        raise ValueError(f"subgraph instances are not supported: nodes {', '.join(used)}")


def _layout_values(node: Node, named_restore: bool) -> Any:
    """The widget values the frontend restores: by name with ``named_restore`` when the node
    has ``widgets_values_named``, otherwise ``widgets_values`` by position."""
    named = node.widgets_values_named if named_restore else None
    return named if named is not None else node.widgets_values


def _resolve_node(node: Node, nodedef: NodeDef | None, links: dict[int, Link], named_restore: bool) -> ResolveNode:
    saved = [saved_input_slot(entry, _source(entry, links)) for entry in node.inputs]
    saved_outputs = tuple(entry.get("type") for entry in node.outputs)
    if nodedef is None or nodedef.virtual:
        # Frontend-only and unknown nodes keep the sockets the canvas saved.
        return ResolveNode(node.key, node.mode, nodedef is not None, tuple(saved), saved_outputs)
    defined_outputs = output_types(nodedef)
    return ResolveNode(
        node.key,
        node.mode,
        False,
        merge_saved_inputs(defined_input_slots(nodedef, _layout_values(node, named_restore)), saved),
        defined_outputs + saved_outputs[len(defined_outputs) :],
    )


def _source(entry: dict[str, Any], links: dict[int, Link]) -> tuple[str, int] | None:
    link_id = entry.get("link")
    link = links.get(link_id) if isinstance(link_id, int) else None
    return (str(link.origin_id), link.origin_slot) if link is not None else None


def _primitive_values(
    nodes: list[Node],
    nodedefs: dict[str, NodeDef | None],
    graph: Graph,
    links: dict[int, Link],
) -> dict[str, dict[str, Any]]:
    """Widget values pushed by primitives: target node key -> widget name -> value."""
    by_key = {node.key: node for node in nodes}
    pushed: dict[str, dict[str, Any]] = {}
    for node in nodes:
        nodedef = nodedefs[node.key]
        if nodedef is None or nodedef.behavior is not Behavior.PRIMITIVE:
            continue
        values = node.widgets_values
        if not isinstance(values, list) or not values:
            continue  # no source widget: nothing is applied
        for link in sorted(links.values(), key=lambda item: item.id):
            if str(link.origin_id) != node.key or link.origin_slot != 0:
                continue
            target_key = str(link.target_id)
            socket = _target_socket(by_key.get(target_key), graph.get(target_key), link)
            if socket is not None and socket.widget is not None:
                pushed.setdefault(target_key, {})[socket.widget] = values[0]
    return pushed


def _target_socket(target: Node | None, resolved: ResolveNode | None, link: Link) -> InputSlot | None:
    """The live socket a link ends on: the one whose saved entry names the link
    (``realignInputLinkSlots``), else the link's own target slot index."""
    if target is None or resolved is None:
        return None
    for entry in target.inputs:
        if entry.get("link") == link.id:
            return next((s for s in resolved.inputs if s.name == entry.get("name")), None)
    if 0 <= link.target_slot < len(resolved.inputs):
        return resolved.inputs[link.target_slot]
    return None


def _widget_inputs(
    node: Node, nodedef: NodeDef | None, widget_layout: WidgetLayout, pushed: dict[str, Any], named_restore: bool
) -> dict[str, Any]:
    if nodedef is None:
        # Unknown type: no widget definitions, so only named values can be placed.
        named = node.widgets_values_named or {}
        values = {**named, **{k: v for k, v in pushed.items() if k in named}}
        return {name: wrap_widget_value(value) for name, value in values.items()}
    named = node.widgets_values_named if named_restore else None
    positional = node.widgets_values if isinstance(node.widgets_values, list) else []
    layout = widget_layout(nodedef, _layout_values(node, named_restore))
    dynamic = dynamic_widgets(nodedef, layout)
    inputs: dict[str, Any] = {}
    for index, spec in enumerate(layout):
        if spec.name is None or not spec.to_prompt:
            continue
        if spec.name in pushed:
            value = pushed[spec.name]
        elif named is not None:
            value = named[spec.name] if spec.name in named else spec.default
        else:
            value = positional[index] if index < len(positional) else spec.default
        if spec.name in dynamic and isinstance(value, str):
            value = process(value).text
        inputs[spec.name] = wrap_widget_value(value)
    return inputs


def _title(node: Node, nodedef: NodeDef | None) -> str:
    if node.title is not None:
        return node.title
    if nodedef is not None:
        return nodedef.display_name or nodedef.type
    return node.type
