"""The kept plan: tidy without ``--regroup`` keeps the canvas's own groups.

Membership is the frontend's (``docs/m2-frontend-facts.md`` section 1): a node belongs to
every group whose bounding holds the centre of its box, half-open; its innermost group is
the smallest of those. The classic box decides; the Vue box is read only to refuse
overlapping groups. A group is a child of another when the other wholly contains its
bounding (edges may touch; identical rectangles do not nest).
"""

from __future__ import annotations

from cwc.annotate.graph import GraphView, group_ranks
from cwc.annotate.infer import counts
from cwc.annotate.notes import make_notes, member_order, node_zones
from cwc.annotate.palette import zone_class
from cwc.annotate.rules import classify, majority_zone
from cwc.annotate.types import Annotations, GroupPlan, Source
from cwc.canvas.envelope import box_facts, classic_box, vue_box
from cwc.canvas.geometry import Rect
from cwc.canvas.model import Canvas, NodeMode
from cwc.report import Code, CwcError, ExitCode, Finding, Severity, sort_findings

Point = tuple[float, float]
REGROUP_HINT = "pass --regroup to rebuild clean groups; tidy without it never changes which group a node is in"


def holds(rect: Rect, point: Point) -> bool:
    """The frontend's half-open test: ``x <= px < x + w`` and ``y <= py < y + h``."""
    px, py = point
    return rect.x <= px < rect.right and rect.y <= py < rect.bottom


def encloses(outer: Rect, inner: Rect) -> bool:
    return outer != inner and outer.contains_rect(inner)


def _area(rect: Rect) -> float:
    return rect.w * rect.h


def kept(view: GraphView, canvas: Canvas) -> Annotations:
    rects = [group.bounding for group in canvas.groups]
    titles = [group.title for group in canvas.groups]
    centres: dict[str, tuple[Point, Point]] = {}
    for key in view.keys:
        node = view.nodes[key]
        facts = box_facts(node, view.defs[key])
        centres[key] = (classic_box(node, facts).center, vue_box(node, facts).center)
    _refuse_overlaps(view, rects, titles, centres)

    def smallest(indexes: list[int]) -> int | None:
        return min(indexes, key=lambda i: (_area(rects[i]), i)) if indexes else None

    innermost = {key: smallest([i for i, r in enumerate(rects) if holds(r, centres[key][0])]) for key in view.keys}
    parent = {i: smallest([j for j, r in enumerate(rects) if j != i and encloses(r, rects[i])]) for i in range(len(rects))}

    optional = frozenset(key for key in view.keys if view.nodes[key].mode == NodeMode.BYPASS)
    classified = classify(view, optional, {})
    zones = node_zones(classified)
    notes = make_notes(view, classified, zones, optional, {}, compile_mode=False)

    direct: dict[int, list[str]] = {i: [] for i in range(len(rects))}
    for key in view.keys:
        if innermost[key] is not None:
            direct[innermost[key]].append(key)

    def ancestors(i: int) -> list[int]:
        out, j = [], parent[i]
        while j is not None:
            out.append(j)
            j = parent[j]
        return out

    subtree: dict[str, list[str]] = {str(i): [] for i in range(len(rects))}
    for i in range(len(rects)):
        for j in [i, *ancestors(i)]:
            subtree[str(j)].extend(direct[i])
    exclude = [(str(i), str(j)) for i in range(len(rects)) for j in ancestors(i)]
    ranks = group_ranks(subtree, [(e.origin, e.target) for e in view.edges], view.order, exclude)

    plans: dict[int, GroupPlan] = {}
    for i in range(len(rects)):
        zone = majority_zone([zones[key] for key in direct[i]])
        plans[i] = GroupPlan(
            key=f"kept:{i}",
            zone=zone,
            name=titles[i],
            members=member_order(view, notes, direct[i]),
            parent=None if parent[i] is None else f"kept:{parent[i]}",
            optional=bool(direct[i]) and all(key in optional for key in direct[i]),
            colour=zone_class(zone),
            stage=ranks[str(i)],
            source_index=i,
        )
    ordered: list[GroupPlan] = []

    def visit(i: int) -> None:
        ordered.append(plans[i])
        for child in sorted((j for j in plans if parent[j] == i), key=lambda j: (plans[j].stage, j)):
            visit(child)

    for root in sorted((i for i in plans if parent[i] is None), key=lambda i: (plans[i].stage, i)):
        visit(root)
    groups = tuple(ordered)
    ungrouped = tuple(view.topo_sorted(key for key in view.keys if innermost[key] is None))
    return Annotations(
        source=Source.KEPT,
        groups=groups,
        nodes=notes,
        ungrouped=ungrouped,
        counts=counts(len(view.keys), groups, node_zones(classify(view, frozenset(), {})) if optional else zones),
    )


def _refuse_overlaps(view: GraphView, rects: list[Rect], titles: list[str], centres: dict[str, tuple[Point, Point]]) -> None:
    findings = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if encloses(rects[i], rects[j]) or encloses(rects[j], rects[i]):
                continue
            shared = tuple(
                key
                for key in view.keys
                if any(holds(rects[i], point) and holds(rects[j], point) for point in centres[key])
            )
            if shared:
                findings.append(
                    Finding(
                        Code.G4,
                        Severity.ERROR,
                        f"group {i} {titles[i]!r} and group {j} {titles[j]!r} overlap without nesting, "
                        f"and the centres of nodes {', '.join(shared)} lie in both; pass --regroup",
                        nodes=shared,
                        hint=REGROUP_HINT,
                        data={"groups": [i, j]},
                    )
                )
    if findings:
        raise CwcError(ExitCode.INPUT_ERROR, sort_findings(findings))
