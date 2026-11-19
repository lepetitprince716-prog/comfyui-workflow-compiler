"""Facts about one canvas that several check families read: definitions per node,
link edges, reachability and group membership.

Group membership follows the frontend's rule for ``Ctrl+B`` on a group: a node belongs
to every group whose box contains the centre of the node's frame, so nested groups
count for their ancestors too.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from cwc.canvas.model import Canvas, Node, NodeMode, id_order
from cwc.nodedefs.types import Behavior, NodeDef
from cwc.prompt.model import Prompt, is_link
from cwc.prompt.slots import DefLookup

OFF_MODES = (int(NodeMode.NEVER), int(NodeMode.BYPASS))  # muted, bypassed

Edge = tuple[str, str]  # (producer key, consumer key)


def sorted_keys(keys: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(keys), key=id_order))


@dataclass(frozen=True)
class CanvasFacts:
    canvas: Canvas
    defs: DefLookup

    @property
    def nodes(self) -> list[Node]:
        return sorted(self.canvas.nodes, key=lambda node: id_order(node.key))

    def nodedef(self, node: Node) -> NodeDef | None:
        return self.defs.get(node.type)

    def virtual(self, node: Node) -> bool:
        nodedef = self.nodedef(node)
        return nodedef is not None and nodedef.virtual

    def behavior(self, node: Node) -> Behavior | None:
        """The builtin behaviour of a frontend-only node; None for every other node."""
        nodedef = self.nodedef(node)
        return nodedef.behavior if nodedef is not None else None

    def real_nodes(self) -> list[Node]:
        """Nodes that can enter a prompt (not frontend-only), in id order."""
        return [node for node in self.nodes if not self.virtual(node)]

    def canvas_edges(self) -> list[Edge]:
        """Links whose two endpoints exist, as (origin, target) keys; modes are ignored."""
        keys = {node.key for node in self.canvas.nodes}
        return [
            (str(link.origin_id), str(link.target_id))
            for link in self.canvas.links
            if str(link.origin_id) in keys and str(link.target_id) in keys
        ]

    def groups(self) -> list[GroupMembers]:
        nodes = self.nodes
        centres = [(node, node.frame().center) for node in nodes]
        out = []
        for index, group in enumerate(self.canvas.groups):
            box = group.bounding
            members = tuple(node for node, (x, y) in centres if box.contains_point(x, y))
            out.append(GroupMembers(index, group.title, members))
        return out


@dataclass(frozen=True)
class GroupMembers:
    index: int  # position in the canvas ``groups`` array
    title: str
    members: tuple[Node, ...]  # every node whose frame centre is inside the box, id order

    def label(self) -> str:
        return f"group {self.index} {self.title!r}"


def prompt_edges(prompt: Prompt) -> list[Edge]:
    return [
        (value[0], key)
        for key, entry in prompt.items()
        for value in entry.get("inputs", {}).values()
        if is_link(value) and value[0] in prompt
    ]


def reaching(edges: Iterable[Edge], sinks: Iterable[str]) -> set[str]:
    """The sinks and every node with a path of edges into one."""
    producers: dict[str, set[str]] = defaultdict(set)
    for producer, consumer in edges:
        producers[consumer].add(producer)
    seen = set(sinks)
    stack = sorted(seen)
    while stack:
        for producer in producers.get(stack.pop(), ()):
            if producer not in seen:
                seen.add(producer)
                stack.append(producer)
    return seen


def prompt_outputs(prompt: Prompt, defs: DefLookup) -> set[str]:
    """Entries whose type is an output node (or unknown, so possibly one)."""
    out = set()
    for key, entry in prompt.items():
        nodedef = defs.get(entry["class_type"])
        if nodedef is None or nodedef.output_node:
            out.add(key)
    return out
