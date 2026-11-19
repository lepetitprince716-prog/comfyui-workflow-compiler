"""``order``: the execution order the frontend assigns on load (``LGraph.computeExecutionOrder``).

Kahn's algorithm over the links: start with every node that has no linked input, in
node-list order; take nodes from the front of the queue; when a node is taken, each of its
output links (by output slot, then link order) is removed, and a target left with no
linked input joins the back of the queue. Nodes left over (a cycle) follow in list order.
"""

from __future__ import annotations

from collections import deque
from typing import Sequence


def execution_order(nodes: Sequence[str], links: Sequence[tuple[str, str]]) -> dict[str, int]:
    """``nodes``: node keys in list order. ``links``: ``(origin, target)`` pairs in the order
    the origin's outputs list them. Returns key -> order."""
    pending = {key: 0 for key in nodes}
    outgoing: dict[str, list[str]] = {key: [] for key in nodes}
    for origin, target in links:
        pending[target] += 1
        outgoing[origin].append(target)
    queue = deque(key for key in nodes if pending[key] == 0)
    ordered: list[str] = []
    while queue:
        key = queue.popleft()
        ordered.append(key)
        for target in outgoing[key]:
            pending[target] -= 1
            if pending[target] == 0:
                queue.append(target)
    placed = frozenset(ordered)
    ordered += [key for key in nodes if key not in placed]
    return {key: index for index, key in enumerate(ordered)}
