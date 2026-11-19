"""The graph as annotate reads it: typed ports, typed edges, one topological order, depths.

Everything here comes from the canvas (slot names and types, links) and the node
definition's ``output_node`` flag; a node whose type has no definition is read from the
slots the canvas gives it, with ``output_node`` false. Node order never depends on the
order of the canvas ``nodes`` array: keys are sorted by id first.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from cwc.canvas.model import Canvas, Node, id_order
from cwc.instantiate.order import execution_order
from cwc.nodedefs.types import Behavior, NodeDef
from cwc.prompt.slots import DefLookup
from cwc.verify.graph import reaching

ANY = "*"
COMBO = "COMBO"


def slot_types(value: Any) -> frozenset[str]:
    """The socket types of one slot: ``"IMAGE,MASK"`` names two, a list is a combo."""
    if isinstance(value, str):
        return frozenset(part.strip().upper() for part in value.split(",") if part.strip()) or frozenset({ANY})
    if isinstance(value, (list, tuple)):
        return frozenset({COMBO})
    return frozenset({ANY})


@dataclass(frozen=True)
class Edge:
    origin: str
    origin_slot: int
    target: str
    target_slot: int
    input_name: str
    types: frozenset[str]


class GraphView:
    """Read-only facts about one canvas for the first-match rules."""

    def __init__(self, canvas: Canvas, defs: DefLookup):
        nodes = sorted(canvas.nodes, key=lambda node: id_order(node.key))
        self.keys: tuple[str, ...] = tuple(node.key for node in nodes)
        self.nodes: dict[str, Node] = {node.key: node for node in nodes}
        self.defs: dict[str, NodeDef | None] = {node.key: defs.get(node.type) for node in nodes}
        edges: list[Edge] = []
        for link in canvas.links:
            origin, target = str(link.origin_id), str(link.target_id)
            if origin not in self.nodes or target not in self.nodes:
                continue
            slots = self.nodes[target].inputs
            slot = slots[link.target_slot] if 0 <= link.target_slot < len(slots) else {}
            types = slot_types(link.type)
            if types == {ANY}:
                types = slot_types(slot.get("type"))
            edges.append(Edge(origin, link.origin_slot, target, link.target_slot, str(slot.get("name", "")), types))
        edges.sort(key=lambda e: (id_order(e.origin), e.origin_slot, id_order(e.target), e.target_slot))
        self.edges: tuple[Edge, ...] = tuple(edges)
        self.incoming: dict[str, list[Edge]] = defaultdict(list)
        self.outgoing: dict[str, list[Edge]] = defaultdict(list)
        for edge in edges:
            self.incoming[edge.target].append(edge)
            self.outgoing[edge.origin].append(edge)
        pairs = [(e.origin, e.target) for e in edges]
        self.order: dict[str, int] = execution_order(self.keys, pairs)
        self.depth: dict[str, int] = longest_path_depth(self.keys, pairs, self.order)
        self._pairs = pairs
        self._reversed = [(target, origin) for origin, target in pairs]

    # Slots.

    def in_types(self, key: str) -> frozenset[str]:
        """Types of every input slot, linked or not."""
        out: set[str] = set()
        for slot in self.nodes[key].inputs:
            out |= slot_types(slot.get("type"))
        return frozenset(out)

    def out_types(self, key: str) -> frozenset[str]:
        out: set[str] = set()
        for slot in self.nodes[key].outputs:
            out |= slot_types(slot.get("type"))
        return frozenset(out)

    def out_names(self, key: str) -> frozenset[str]:
        return frozenset(str(slot.get("name", "")).upper() for slot in self.nodes[key].outputs)

    def linked_in_types(self, key: str) -> frozenset[str]:
        out: set[str] = set()
        for edge in self.incoming[key]:
            out |= edge.types
        return frozenset(out)

    def is_source(self, key: str) -> bool:
        return not self.incoming[key]

    def has_widgets(self, key: str) -> bool:
        node = self.nodes[key]
        return bool(node.widgets_values) or bool(node.widgets_values_named)

    def output_node(self, key: str) -> bool:
        nodedef = self.defs[key]
        return nodedef is not None and nodedef.output_node

    def behavior(self, key: str) -> Behavior | None:
        nodedef = self.defs[key]
        return nodedef.behavior if nodedef is not None else None

    # Reachability (the helpers in cwc.verify.graph).

    def upstream(self, keys: Iterable[str]) -> set[str]:
        """``keys`` and every node with a path into one of them."""
        return reaching(self._pairs, keys)

    def downstream(self, keys: Iterable[str]) -> set[str]:
        """``keys`` and every node one of them has a path into."""
        return reaching(self._reversed, keys)

    def topo_sorted(self, keys: Iterable[str]) -> list[str]:
        return sorted(set(keys), key=lambda key: (self.order[key], id_order(key)))


def longest_path_depth(keys: Iterable[str], pairs: Iterable[tuple[str, str]], order: Mapping[str, int]) -> dict[str, int]:
    """Longest-path layer of each key along ``pairs``, over the edges that go forward in
    ``order`` (an edge closing a cycle is ignored)."""
    keys = sorted(keys, key=lambda key: order[key])
    forward: dict[str, list[str]] = defaultdict(list)
    for origin, target in pairs:
        if order[origin] < order[target]:
            forward[origin].append(target)
    depth = {key: 0 for key in keys}
    for key in keys:
        for target in forward[key]:
            depth[target] = max(depth[target], depth[key] + 1)
    return depth


def group_ranks(
    members: Mapping[str, Iterable[str]],
    pairs: Iterable[tuple[str, str]],
    order: Mapping[str, int],
    exclude: Iterable[tuple[str, str]] = (),
) -> dict[str, int]:
    """Rank of each group along the data flow: a node edge from one group's nodes into
    another's makes a group edge, and ranks are longest-path layers of that group graph.
    ``members``: group key to its nodes; ``order``: the node execution order; ``exclude``:
    group pairs that never make an edge (a group and its own ancestors).

    A group reached from another and not reaching it back ranks higher. Groups that reach each
    other (one loop: a strongly connected set) are ranked as one unit from outside: the loop
    starts one above the highest rank of any loop with an edge into it, and every group a loop
    reaches ranks above all of the loop's groups, because each of them reaches it. Inside a loop
    the groups are ordered by their earliest member in ``order``, and ranked by the longest path
    along the loop's own edges that follow that order from the loop's start; an edge running
    against it closes the loop and sets no rank. The loops are walked in a topological order of
    the loop graph, earliest member first; a group key only breaks a tie between equal earliest
    members."""
    owner: dict[str, list[str]] = defaultdict(list)
    for group, nodes in members.items():
        for node in nodes:
            owner[node].append(group)
    skip = set(exclude) | {(b, a) for a, b in exclude}
    links: set[tuple[str, str]] = set()
    for origin, target in pairs:
        for a in owner.get(origin, ()):
            for b in owner.get(target, ()):
                if a != b and (a, b) not in skip:
                    links.add((a, b))
    groups = sorted(members)
    ordered_links = sorted(links)
    first = {g: (min((order[n] for n in members[g] if n in order), default=len(order)), g) for g in groups}
    ranked = sorted(groups, key=first.__getitem__)
    loop = _loops(ranked, ordered_links)
    heads = list(dict.fromkeys(loop[g] for g in ranked))
    pending = {head: 0 for head in heads}
    after: dict[str, list[str]] = {head: [] for head in heads}
    for a, b in ordered_links:
        if loop[a] != loop[b] and loop[b] not in after[loop[a]]:
            after[loop[a]].append(loop[b])
            pending[loop[b]] += 1
    ready = [(first[head], head) for head in heads if pending[head] == 0]
    heapq.heapify(ready)
    walk: list[str] = []
    while ready:
        _, head = heapq.heappop(ready)
        walk += [g for g in ranked if loop[g] == head]
        for nxt in after[head]:
            pending[nxt] -= 1
            if pending[nxt] == 0:
                heapq.heappush(ready, (first[nxt], nxt))
    position = {g: i for i, g in enumerate(walk)}
    inside = longest_path_depth(groups, [(a, b) for a, b in ordered_links if loop[a] == loop[b]], position)
    start = {head: 0 for head in heads}
    ranks: dict[str, int] = {}
    for head in dict.fromkeys(loop[g] for g in walk):
        unit = [g for g in walk if loop[g] == head]
        for g in unit:
            ranks[g] = start[head] + inside[g]
        top = max(ranks[g] for g in unit)
        for nxt in after[head]:
            start[nxt] = max(start[nxt], top + 1)
    return ranks


def _loops(keys: list[str], pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Each key's strongly connected set along ``pairs``, named by its first key in ``keys``."""
    edges: dict[str, list[str]] = defaultdict(list)
    for a, b in pairs:
        edges[a].append(b)
    down: dict[str, set[str]] = {}
    for key in keys:
        seen: set[str] = {key}
        todo = [key]
        while todo:
            for nxt in edges[todo.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        down[key] = seen
    out: dict[str, str] = {}
    for key in keys:
        if key not in out:
            for other in keys:
                if other not in out and other in down[key] and key in down[other]:
                    out[other] = key
    return out
