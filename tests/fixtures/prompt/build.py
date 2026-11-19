"""Test-only builders: a mini node definition loader and a canvas builder.

``defs.json`` lists node definitions in a compact format so prompt tests do not depend on
the real node definition registry. A widget input without a ``widgets`` list gets one
``WidgetSpec`` of the same name; ``socketless`` marks a widget without an input socket.

``CanvasBuilder`` writes canvases the way the frontend serialises a freshly created
node: link-only sockets first, then widget sockets, each in definition order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cwc.canvas.model import Canvas
from cwc.nodedefs.types import Behavior, InputSpec, NodeDef, OutputSpec, Tier, WidgetKind, WidgetSpec

DEFS_FILE = Path(__file__).with_name("defs.json")


class Defs:
    """Dict-like registry of the fixture definitions; ``get`` is all the prompt package uses."""

    def __init__(self, raw: dict[str, dict[str, Any]]):
        self._defs = {name: _nodedef(name, entry) for name, entry in raw.items()}

    def get(self, type: str) -> NodeDef | None:
        return self._defs.get(type)


def load_defs(path: Path = DEFS_FILE) -> Defs:
    return Defs(json.loads(path.read_text(encoding="utf-8")))


def _nodedef(name: str, entry: dict[str, Any]) -> NodeDef:
    inputs = tuple(
        InputSpec(
            item["name"],
            item["type"],
            True,
            bool(item.get("widget")),
            has_default="default" in item,
            default=item.get("default"),
            socketless=bool(item.get("socketless")),
        )
        for item in entry["inputs"]
    )
    if "widgets" in entry:
        widgets = tuple(
            WidgetSpec(
                item["name"],
                WidgetKind(item.get("kind", "value")),
                item.get("input"),
                item.get("default"),
                item.get("to_prompt", True),
            )
            for item in entry["widgets"]
        )
    else:
        widgets = tuple(WidgetSpec(i.name, WidgetKind.VALUE, i.name, i.default) for i in inputs if i.widget)
    return NodeDef(
        name,
        Tier.BUILTIN if entry.get("virtual") else Tier.TRUTH,
        inputs,
        tuple(OutputSpec(out_name, out_type) for out_name, out_type in entry["outputs"]),
        widgets,
        display_name=entry.get("display_name", ""),
        output_node=entry.get("output_node", False),
        virtual=entry.get("virtual", False),
        behavior=Behavior(entry["behavior"]) if "behavior" in entry else None,
    )


class CanvasBuilder:
    """Builds a 0.4 canvas. Node ids are integers; ``link`` returns the new link id."""

    def __init__(self, defs: Defs):
        self.defs = defs
        self.nodes: list[dict[str, Any]] = []
        self.links: list[list[Any]] = []

    def add(
        self,
        node_id: int,
        type: str,
        *,
        mode: int = 0,
        widgets: list[Any] | None = None,
        named: dict[str, Any] | None = None,
        title: str | None = None,
        ins: list[tuple[str, Any]] | None = None,
        outs: list[Any] | None = None,
    ) -> int:
        """Add a node. ``ins`` (name, type) and ``outs`` (type) give explicit sockets for
        virtual and unknown types; defined types get theirs from the definition."""
        nodedef = self.defs.get(type)
        if ins is None and outs is None and nodedef is not None:
            input_entries = self._defined_inputs(nodedef)
            out_entries = [{"name": out.name, "type": out.type, "links": []} for out in nodedef.outputs]
        else:
            input_entries = [_input_entry(name, kind) for name, kind in (ins or [])]
            out_entries = [{"name": str(kind), "type": kind, "links": []} for kind in (outs or [])]
        node: dict[str, Any] = {
            "id": node_id,
            "type": type,
            "pos": [0, 0],
            "size": [200, 100],
            "flags": {},
            "order": len(self.nodes),
            "mode": mode,
            "inputs": input_entries,
            "outputs": out_entries,
            "properties": {},
        }
        if title is not None:
            node["title"] = title
        if widgets is not None:
            node["widgets_values"] = widgets
        if named is not None:
            node["widgets_values_named"] = named
        self.nodes.append(node)
        return node_id

    @staticmethod
    def _defined_inputs(nodedef: NodeDef) -> list[dict[str, Any]]:
        link_only = [_input_entry(i.name, i.type) for i in nodedef.inputs if not i.widget]
        widget_inputs = [_input_entry(i.name, i.type, widget=i.name) for i in nodedef.inputs if i.widget and not i.socketless]
        return link_only + widget_inputs

    def link(self, origin: int, origin_slot: int, target: int, target_input: str | int) -> int:
        """Link output ``origin_slot`` of ``origin`` to input ``target_input`` (name or index)."""
        source = self._node(origin)
        sink = self._node(target)
        names = [entry["name"] for entry in sink["inputs"]]
        index = target_input if isinstance(target_input, int) else names.index(target_input)
        link_id = len(self.links) + 1
        kind = source["outputs"][origin_slot]["type"]
        sink["inputs"][index]["link"] = link_id
        source["outputs"][origin_slot]["links"].append(link_id)
        self.links.append([link_id, origin, origin_slot, target, index, kind])
        return link_id

    def canvas(self) -> Canvas:
        return Canvas(
            {
                "last_node_id": max((n["id"] for n in self.nodes), default=0),
                "last_link_id": len(self.links),
                "nodes": self.nodes,
                "links": self.links,
                "groups": [],
                "config": {},
                "extra": {},
                "version": 0.4,
            }
        )

    def _node(self, node_id: int) -> dict[str, Any]:
        return next(node for node in self.nodes if node["id"] == node_id)


def _input_entry(name: str, kind: Any, widget: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "type": kind, "link": None}
    if widget is not None:
        entry["widget"] = {"name": widget}
    return entry
