"""Group checks: G3 (membership against a target) and G4 (groups that overlap without nesting).

Membership is the frontend's rule (``docs/m2-frontend-facts.md`` section 1): a node is in a
group when the centre of its box lies in the group's bounding, half-open. A group is a child
of another when the other wholly contains its bounding; identical rectangles are not
nested.
"""

from __future__ import annotations

from typing import Any, Mapping

from cwc.canvas.envelope import Renderer, expanded_centre
from cwc.canvas.geometry import Rect
from cwc.canvas.model import Canvas, Group, Node
from cwc.report import Code, Finding, Severity
from cwc.verify.geometry_view import GeometryView
from cwc.verify.policy import Policy

Point = tuple[float, float]


def holds(rect: Rect, point: Point) -> bool:
    """The frontend's ``isInRect``: left and top edges belong to the rect, right and bottom do not."""
    return rect.x <= point[0] < rect.right and rect.y <= point[1] < rect.bottom


def nested(outer: Rect, inner: Rect) -> bool:
    """``outer`` wholly contains ``inner`` (touching edges allowed) and is not the same rectangle."""
    return outer.contains_rect(inner) and outer != inner


def innermost(boundings: list[Rect], point: Point) -> int | None:
    """Index of the smallest group holding ``point`` (the lower index on equal areas), or ``None``."""
    best: tuple[float, int] | None = None
    for index, rect in enumerate(boundings):
        if holds(rect, point):
            key = (rect.w * rect.h, index)
            if best is None or key < best:
                best = key
    return None if best is None else best[1]


def _boundings(canvas: Canvas) -> list[Rect]:
    return [group.bounding for group in canvas.groups]


def links_into(view: GeometryView, groups: frozenset[int]) -> frozenset[int]:
    """Ids of the links whose target node's innermost group (classic centre) is one of
    ``groups`` or a group nested in one of them.

    G5 and C3 leave out the links into the cockpit: the cockpit is the control panel left of the
    model stack, and the prompt encoders lifted into it are wired from that stack, so their
    inbound links run right to left by design. A cockpit split by C1 is several groups, and a
    group nested in the cockpit is part of the panel too, so every one of them is covered."""
    if not groups:
        return frozenset()
    boundings = _boundings(view.canvas)
    covered = set(groups) | {inner for outer, inner in containment(view.canvas) if outer in groups}
    inside = {
        node.key for node in view.nodes if innermost(boundings, view.boxes[Renderer.CLASSIC][node.key].center) in covered
    }
    return frozenset(link.id for link in view.canvas.links if str(link.target_id) in inside)


def box_centres(view: GeometryView, node: Node, *, expanded: bool) -> dict[str, Point]:
    """The points the frontend may take a node's group from: the classic box centre, the Vue
    box centre and, when ``expanded`` is asked for (a collapsed node), the centre of the
    envelope the node would have if expanded. G3 holds only when the group is right at all of them."""
    points = {
        "classic": view.boxes[Renderer.CLASSIC][node.key].center,
        "vue": view.boxes[Renderer.VUE][node.key].center,
    }
    if expanded:
        points["expanded"] = expanded_centre(node, view.facts[node.key])
    return points


def _label(index: int, groups: list[Group]) -> str:
    return f"group {index} {groups[index].title!r}"


def check_membership(view: GeometryView, membership: Mapping[str, int | None]) -> tuple[list[Finding], dict[str, Any]]:
    """G3 against a target: every node's innermost group, seen from the classic box centre, the
    Vue box centre and (collapsed nodes) the expanded centre, must be its target."""
    groups = view.canvas.groups
    boundings = [group.bounding for group in groups]
    findings = []
    for node in view.nodes:
        if node.key not in membership:
            findings.append(
                Finding(Code.G3, Severity.ERROR, f"node {node.key} has no target group in the membership", nodes=(node.key,))
            )
            continue
        target = membership[node.key]
        if target is not None and not 0 <= target < len(groups):
            findings.append(
                Finding(
                    Code.G3,
                    Severity.ERROR,
                    f"node {node.key} targets group {target}, but the canvas has {len(groups)} groups",
                    nodes=(node.key,),
                    data={"target": target},
                )
            )
            continue
        points = box_centres(view, node, expanded=node.collapsed)
        found = {name: innermost(boundings, point) for name, point in points.items()}
        if any(index != target for index in found.values()):
            wrong = ", ".join(f"{name} {_describe(index, groups)}" for name, index in found.items() if index != target)
            findings.append(
                Finding(
                    Code.G3,
                    Severity.ERROR,
                    f"node {node.key} should be in {_describe(target, groups)}; innermost group by box centre: {wrong}",
                    nodes=(node.key,),
                    data={"target": target, "found": found},
                )
            )
    return findings, {"nodes_checked": len(view.nodes), "mismatches": len(findings)}


def _describe(index: int | None, groups: list[Group]) -> str:
    return "no group" if index is None else _label(index, groups)


def _holding(boundings: list[Rect], point: Point) -> frozenset[int]:
    return frozenset(index for index, rect in enumerate(boundings) if holds(rect, point))


def ancestor_sets(view: GeometryView) -> dict[str, frozenset[int]]:
    """Node key to the indices of every group holding the classic box centre."""
    boundings = _boundings(view.canvas)
    return {node.key: _holding(boundings, view.boxes[Renderer.CLASSIC][node.key].center) for node in view.nodes}


def containment(canvas: Canvas) -> frozenset[tuple[int, int]]:
    """Every ``(outer, inner)`` pair of group indices where the first group nests the second."""
    boundings = _boundings(canvas)
    return frozenset(
        (i, j) for i, outer in enumerate(boundings) for j, inner in enumerate(boundings) if i != j and nested(outer, inner)
    )


def check_ancestry(view: GeometryView, before: GeometryView) -> tuple[list[Finding], dict[str, Any]]:
    """G3 without a target: the groups are matched by index; each node's ancestor groups, taken at
    the classic box centre, the Vue box centre and (collapsed nodes) the expanded centre as
    :func:`check_membership` does, and the containment graph must equal the input's."""
    findings = []
    groups_before, groups_after = before.canvas.groups, view.canvas.groups
    if len(groups_before) != len(groups_after):
        findings.append(
            Finding(
                Code.G3,
                Severity.ERROR,
                f"the group count changed from {len(groups_before)} to {len(groups_after)}",
                data={"before": len(groups_before), "after": len(groups_after)},
            )
        )
    boundings_before, boundings_after = _boundings(before.canvas), _boundings(view.canvas)
    was_nodes = {node.key: node for node in before.nodes}
    compared = 0
    for node in view.nodes:
        if node.key not in was_nodes:
            continue
        compared += 1
        # the expanded centre is asked for by the node as written now; the input node is read at
        # the same centre whether or not it was collapsed
        after_points = box_centres(view, node, expanded=node.collapsed)
        before_points = box_centres(before, was_nodes[node.key], expanded=node.collapsed)
        changed = {}
        for name, point in after_points.items():
            was, now = _holding(boundings_before, before_points[name]), _holding(boundings_after, point)
            if was != now:
                changed[name] = {"before": sorted(was), "after": sorted(now)}
        if changed:
            detail = "; ".join(
                f"under the {name} box centre it was in groups {entry['before']} and is now in {entry['after']}"
                for name, entry in changed.items()
            )
            findings.append(Finding(Code.G3, Severity.ERROR, f"node {node.key} changed groups: {detail}", nodes=(node.key,), data={"centres": changed}))
    edges_before, edges_after = containment(before.canvas), containment(view.canvas)
    for outer, inner in sorted(edges_before ^ edges_after):
        kept = (outer, inner) in edges_before
        findings.append(
            Finding(
                Code.G3,
                Severity.ERROR,
                f"group {outer} {'no longer holds' if kept else 'now holds'} group {inner}",
                data={"outer": outer, "inner": inner, "before": kept, "after": not kept},
            )
        )
    return findings, {"nodes_checked": compared, "mismatches": len(findings)}


def check_group_overlap(canvas: Canvas, policy: Policy) -> tuple[list[Finding], dict[str, Any]]:
    """G4: group pairs whose boundings overlap with positive area and are not nested."""
    groups = canvas.groups
    boundings = [group.bounding for group in groups]
    findings = []
    pinned_pairs = 0
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            a, b = boundings[i], boundings[j]
            if not a.overlaps(b) or nested(a, b) or nested(b, a):
                continue
            both_pinned = groups[i].pinned and groups[j].pinned
            pinned_pairs += both_pinned
            severity = Severity.WARNING if both_pinned and not policy.strict else Severity.ERROR
            findings.append(
                Finding(
                    Code.G4,
                    severity,
                    f"{_label(i, groups)} and {_label(j, groups)} overlap without nesting",
                    data={"groups": [i, j], "pinned": both_pinned},
                )
            )
    return findings, {"pairs": len(findings), "pinned_pairs": pinned_pairs}
