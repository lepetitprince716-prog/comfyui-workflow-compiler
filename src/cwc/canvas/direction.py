"""Links drawn right to left: the one definition that G5 checks and layout budgets for.

A link runs right to left when its output anchor lies right of its input anchor (classic
curves, :func:`cwc.canvas.links.canvas_splines`). Feedback links (those a depth-first search
closes a cycle with) and exempt links (those into the cockpit) are not counted. The share of
the counted links that run right to left may be at most ``G5_SHARE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from cwc.canvas.links import Spline
from cwc.canvas.model import Canvas, id_order

G5_SHARE = 0.05


def feedback_links(canvas: Canvas) -> set[int]:
    """Ids of the links a depth-first search closes a cycle with (an edge into a node still on
    the stack). Roots and neighbours are taken in id order, so the answer is deterministic."""
    keys = sorted({node.key for node in canvas.nodes}, key=id_order)
    out_edges: dict[str, list[tuple[tuple[int, int | str], int, str]]] = {key: [] for key in keys}
    for link in canvas.links:
        origin, target = str(link.origin_id), str(link.target_id)
        if origin in out_edges and target in out_edges:
            out_edges[origin].append((id_order(target), link.id, target))
    for edges in out_edges.values():
        edges.sort()
    state: dict[str, int] = {}  # 1 while on the stack, 2 when finished
    back: set[int] = set()
    for root in keys:
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(out_edges[root]))]
        while stack:
            key, edges = stack[-1]
            step = next(edges, None)
            if step is None:
                state[key] = 2
                stack.pop()
                continue
            _, link_id, target = step
            if state.get(target) == 1:
                back.add(link_id)
            elif target not in state:
                state[target] = 1
                stack.append((target, iter(out_edges[target])))
    return back


@dataclass(frozen=True)
class Direction:
    """``considered``: the counted link ids; ``backward``: those drawn right to left (sorted);
    ``feedback``: the feedback links among the drawable ones."""

    considered: tuple[int, ...]
    backward: tuple[int, ...]
    feedback: frozenset[int]

    @property
    def share(self) -> float:
        return len(self.backward) / len(self.considered) if self.considered else 0.0

    @property
    def over(self) -> bool:
        """True when the share is over ``G5_SHARE`` (G5 fails)."""
        return self.share > G5_SHARE


def direction(canvas: Canvas, curves: Mapping[int, Spline], exempt: frozenset[int] = frozenset()) -> Direction:
    """Count the drawable links (``curves``: classic curves by link id) of ``canvas``."""
    feedback = feedback_links(canvas)
    considered = tuple(link.id for link in canvas.links if link.id in curves and link.id not in feedback and link.id not in exempt)
    backward = tuple(sorted(i for i in considered if curves[i].p0[0] > curves[i].p3[0]))
    return Direction(considered=considered, backward=backward, feedback=frozenset(feedback & set(curves)))
