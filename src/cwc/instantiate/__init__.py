"""``instantiate``: an :class:`~cwc.normalize.types.Authoring` to a native canvas without positions.

Every node of the all-enabled prompt becomes one canvas node, in id order, in the shape a
real frontend writes on ``serialize()``; ``ui_optional`` nodes are bypassed (mode 4), so
the canvas exports the factory prompt. Rules that make the output deterministic:

* nodes in :func:`~cwc.canvas.model.id_order` of their prompt ids, written as ints;
* links numbered from 1 by target node (id order), then target input slot;
* each output lists its link ids in ascending order, ``null`` when unlinked;
* ``order`` is the frontend's execution order (:mod:`cwc.instantiate.order`);
* ``pos`` is ``[0, 0]``; ``size`` comes from :func:`cwc.nodedefs.sizing.node_size`.
"""

from __future__ import annotations

from typing import Any

from cwc.canvas.model import Canvas, Node, id_order
from cwc.instantiate import node as build
from cwc.instantiate.order import execution_order
from cwc.nodedefs.derive import widget_layout
from cwc.normalize.types import Authoring

__all__ = ["instantiate"]


def instantiate(authoring: Authoring) -> Canvas:
    """The canvas for ``authoring``: ports, links, widget values, titles and modes."""
    full = authoring.full
    keys = sorted(full, key=id_order)
    slots = {key: build.input_slots(authoring.defs.get(key), full[key].get("inputs", {})) for key in keys}

    # Links, numbered by target node then input slot.
    links: list[list[Any]] = []
    link_of: dict[tuple[str, int], int] = {}  # (target key, input index) -> link id
    outgoing: dict[tuple[str, int], list[int]] = {}  # (origin key, output slot) -> link ids
    out_counts = {key: len(nodedef.outputs) for key in keys if (nodedef := authoring.defs.get(key)) is not None}
    for key in keys:
        for index, slot in enumerate(slots[key]):
            if slot.source is not None:
                origin, origin_slot = slot.source
                out_counts[origin] = max(out_counts.get(origin, 0), origin_slot + 1)
    out_types = _output_types(authoring, keys, slots, out_counts)
    for key in keys:
        for index, slot in enumerate(slots[key]):
            if slot.source is None:
                continue
            link_id = len(links) + 1
            origin, origin_slot = slot.source
            links.append([link_id, int(origin), origin_slot, int(key), index, out_types[origin][origin_slot]])
            link_of[(key, index)] = link_id
            outgoing.setdefault(slot.source, []).append(link_id)

    source_types = {(key, slot): type_ for key, types in out_types.items() for slot, type_ in enumerate(types)}
    order = execution_order(keys, [(str(link[1]), str(link[3])) for link in sorted(links, key=_by_origin)])
    canvas = Canvas.empty()
    canvas.raw["last_node_id"] = max((int(key) for key in keys), default=0)
    canvas.raw["last_link_id"] = len(links)
    in_types = {key: build.input_types(slots[key], source_types) for key in keys}
    canvas.raw["nodes"] = [
        _node(authoring, key, slots[key], in_types[key], out_types[key], link_of, outgoing, order[key]) for key in keys
    ]
    canvas.raw["links"] = links
    if authoring.workflow.models:
        canvas.raw["models"] = [
            {"name": m.name, "url": m.url, "directory": m.directory} for m in authoring.workflow.models
        ]
    return canvas


def _by_origin(link: list[Any]) -> tuple[int, int, int]:
    """Links in the order the origin outputs list them: origin output slot, then link id."""
    return (link[1], link[2], link[0])


def _output_types(
    authoring: Authoring, keys: list[str], slots: dict[str, list[build.Slot]], counts: dict[str, int]
) -> dict[str, list[str]]:
    """Output types per node, resolved in id order so a match-type source is known first
    whenever ids follow the links (they do after normalize, which numbers in author order)."""
    types: dict[tuple[str, int], str] = {}
    out: dict[str, list[str]] = {}
    for key in _topological(keys, slots):
        resolved = build.output_types(authoring.defs.get(key), slots[key], types, counts.get(key, 0))
        out[key] = resolved
        for slot, type_ in enumerate(resolved):
            types[(key, slot)] = type_
    return {key: out[key] for key in keys}


def _topological(keys: list[str], slots: dict[str, list[build.Slot]]) -> list[str]:
    """Keys with every link source before its target (the links are acyclic after normalize)."""
    edges = [(slot.source[0], key) for key in keys for slot in slots[key] if slot.source is not None]
    order = execution_order(keys, edges)
    return sorted(keys, key=lambda key: order[key])


def _node(
    authoring: Authoring,
    key: str,
    slots: list[build.Slot],
    in_types: list[str],
    out_types: list[str],
    link_of: dict[tuple[str, int], int],
    outgoing: dict[tuple[str, int], list[int]],
    order: int,
) -> dict[str, Any]:
    entry = authoring.full[key]
    nodedef = authoring.defs.get(key)
    type_ = entry["class_type"]
    values = authoring.widget_values.get(key)
    positional, named = build.widget_values(nodedef, entry.get("inputs", {}), values)
    widgets = {w.name: w for w in widget_layout(nodedef, values)} if nodedef is not None else {}
    raw: dict[str, Any] = {"id": int(key), "type": type_, "pos": [0, 0], "size": [0, 0]}
    raw["flags"] = {}
    raw["order"] = order
    raw["mode"] = build.mode(authoring.intents[key].ui_optional)
    raw["inputs"] = [
        build.input_entry(slot.spec, in_types[index], widgets.get(slot.spec.widget), link_of.get((key, index)))
        for index, slot in enumerate(slots)
    ]
    raw["outputs"] = [
        build.output_entry(_output_name(nodedef, slot), type_out, sorted(outgoing.get((key, slot), [])))
        for slot, type_out in enumerate(out_types)
    ]
    title = entry.get("_meta", {}).get("title")
    shown = (nodedef.display_name or nodedef.type) if nodedef is not None else type_
    if title is not None and title != shown:
        raw["title"] = title
    raw["properties"] = build.properties(type_, nodedef)
    if positional:
        raw["widgets_values"] = positional
        if named is not None:
            raw["widgets_values_named"] = named
    Node(raw).set_size(*build.size(nodedef, values, slots, len(out_types), len(positional)))
    return raw


def _output_name(nodedef: Any, slot: int) -> str:
    return nodedef.outputs[slot].name if nodedef is not None else f"output_{slot}"
