"""Readability checks on groups: C1 (crowded group), C2 (too many top-level groups), C3 (groups
against the preset axis), C4 (cockpit titles) and C6 (colour categories).

A node's group is the frontend's rule (:func:`cwc.verify.geometry_groups.innermost`, classic box
centre). A top-level group is one that no other group wholly holds. A node belongs to the
top-level group that holds its classic box centre (the lowest index when overlapping top-level
groups both do).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cwc.canvas.direction import feedback_links
from cwc.canvas.envelope import Renderer, title_text
from cwc.canvas.model import NodeMode, id_order
from cwc.layout.types import Axis, Preset
from cwc.prompt.slots import DefLookup
from cwc.report import Code, Finding, Severity
from cwc.verify.geometry_groups import ancestor_sets, containment, innermost
from cwc.verify.geometry_view import GeometryView
from cwc.verify.policy import Policy

CROWDED = 12  # expanded members of one innermost group
TOP_LEVEL_MAX = 12
COLOUR_MAX = 7
DEFAULT_COLOUR = "#3f789e"  # the frontend's group colour when none is saved


def innermost_members(view: GeometryView) -> dict[int, list[str]]:
    """Group index to the keys (id order) of the nodes whose innermost group it is."""
    boundings = [group.bounding for group in view.canvas.groups]
    out: dict[int, list[str]] = defaultdict(list)
    for node in view.nodes:
        index = innermost(boundings, view.boxes[Renderer.CLASSIC][node.key].center)
        if index is not None:
            out[index].append(node.key)
    return out


def top_level(view: GeometryView) -> list[int]:
    """Indices of the groups no other group nests, ascending."""
    inner = {j for _, j in containment(view.canvas)}
    return [index for index in range(len(view.canvas.groups)) if index not in inner]


def _label(view: GeometryView, index: int) -> str:
    return f"group {index} {view.canvas.groups[index].title!r}"


def check_crowded(view: GeometryView, policy: Policy) -> tuple[list[Finding], dict[str, Any]]:
    """C1: an innermost group with more than 12 expanded members."""
    nodes = {node.key: node for node in view.nodes}
    findings = []
    worst = 0
    for index, members in sorted(innermost_members(view).items()):
        expanded = [key for key in members if not nodes[key].collapsed]
        worst = max(worst, len(expanded))
        if len(expanded) > CROWDED:
            findings.append(
                Finding(
                    Code.C1,
                    Severity.ERROR if policy.strict else Severity.WARNING,
                    f"{_label(view, index)} holds {len(expanded)} expanded nodes; the limit is {CROWDED}",
                    nodes=tuple(expanded),
                    data={"group": index, "expanded": len(expanded)},
                )
            )
    return findings, {"limit": CROWDED, "most_expanded": worst, "crowded": len(findings)}


def check_top_level_count(view: GeometryView, policy: Policy) -> tuple[list[Finding], dict[str, Any]]:
    """C2: more than 12 top-level groups."""
    tops = top_level(view)
    findings = []
    if len(tops) > TOP_LEVEL_MAX:
        findings.append(
            Finding(
                Code.C2,
                Severity.WARNING if policy.strict else Severity.INFO,
                f"{len(tops)} top-level groups; the limit is {TOP_LEVEL_MAX}",
                data={"count": len(tops), "groups": tops},
            )
        )
    return findings, {"limit": TOP_LEVEL_MAX, "top_level": len(tops)}


def check_axis(
    view: GeometryView, policy: Policy, preset: Preset, regroup: bool, exempt: frozenset[int] = frozenset()
) -> tuple[list[Finding], dict[str, Any]]:
    """C3: for each link between nodes of two different top-level groups, both holding a node
    that is not bypassed, feedback links (G5's finder) and ``exempt`` links (those into the
    cockpit) left out, the downstream group must not come before the upstream group in reading
    order: it starts at or after the upstream group along ``preset.axis``, or it lies wholly
    beyond the upstream group across the axis (below it for ``x``, right of it for ``y``), which
    is where a wrapped band or a lower band puts it. One finding per offending pair."""
    tops = set(top_level(view))
    ancestors = ancestor_sets(view)
    home = {key: min(held & tops) for key, held in ancestors.items() if held & tops}
    live = {home[node.key] for node in view.nodes if node.mode != NodeMode.BYPASS and node.key in home}
    feedback = feedback_links(view.canvas)
    boundings = [group.bounding for group in view.canvas.groups]

    def start(index: int) -> float:
        return boundings[index].x if preset.axis is Axis.X else boundings[index].y

    def later_band(up: int, down: int) -> bool:
        if preset.axis is Axis.X:
            return boundings[down].y >= boundings[up].bottom
        return boundings[down].x >= boundings[up].right

    pairs: dict[tuple[int, int], list[int]] = defaultdict(list)
    considered = 0
    for link in view.canvas.links:
        origin, target = str(link.origin_id), str(link.target_id)
        if link.id in feedback or link.id in exempt or origin not in home or target not in home:
            continue
        up, down = home[origin], home[target]
        if up == down or up not in live or down not in live:
            continue
        considered += 1
        if start(down) < start(up) and not later_band(up, down):
            pairs[(up, down)].append(link.id)
    severity = Severity.ERROR if policy.strict or regroup else Severity.INFO
    findings = [
        Finding(
            Code.C3,
            severity,
            f"{_label(view, down)} is downstream of {_label(view, up)} but starts at {start(down):g}, before {start(up):g} on the {preset.axis.value} axis",
            data={"upstream": up, "downstream": down, "axis": preset.axis.value, "links": sorted(links)},
        )
        for (up, down), links in sorted(pairs.items())
    ]
    return findings, {"axis": preset.axis.value, "links": considered, "offending_pairs": len(findings)}


def check_cockpit_titles(
    view: GeometryView, defs: DefLookup, policy: Policy, cockpit: frozenset[int]
) -> tuple[list[Finding], dict[str, Any]]:
    """C4: in the cockpit groups (innermost members of every part of a cockpit C1 split), a node
    drawn under its type name, and members drawn under the same title. The parts are one panel,
    so a title repeated across two parts counts as repeated."""
    severity = Severity.ERROR if policy.strict else Severity.INFO
    by_group = innermost_members(view)
    members = sorted((key for index in cockpit for key in by_group.get(index, [])), key=id_order)
    groups = sorted(cockpit)
    nodes = {node.key: node for node in view.nodes}
    drawn = {key: title_text(nodes[key], defs.get(nodes[key].type)) for key in members}
    findings = []
    for key in members:
        if drawn[key] == nodes[key].type:
            findings.append(
                Finding(
                    Code.C4,
                    severity,
                    f"cockpit node {key} is drawn as {drawn[key]!r}, its type name",
                    nodes=(key,),
                    data={"groups": groups, "title": drawn[key], "reason": "type"},
                )
            )
    by_title: dict[str, list[str]] = defaultdict(list)
    for key in members:
        by_title[drawn[key]].append(key)
    for title, keys in sorted(by_title.items(), key=lambda item: id_order(item[1][0])):
        if len(keys) > 1:
            findings.append(
                Finding(
                    Code.C4,
                    severity,
                    f"cockpit nodes {', '.join(keys)} are all drawn as {title!r}",
                    nodes=tuple(keys),
                    data={"groups": groups, "title": title, "reason": "repeated"},
                )
            )
    return findings, {"groups": groups, "members": len(members), "findings": len(findings)}


def check_colours(view: GeometryView) -> tuple[list[Finding], dict[str, Any]]:
    """C6: more than 7 distinct group colours, compared case-insensitively; a group without a
    colour counts as the frontend default."""
    colours = sorted({(group.color or DEFAULT_COLOUR).lower() for group in view.canvas.groups})
    findings = []
    if len(colours) > COLOUR_MAX:
        findings.append(
            Finding(
                Code.C6,
                Severity.ERROR,
                f"{len(colours)} group colours; the limit is {COLOUR_MAX}",
                data={"colours": colours},
            )
        )
    return findings, {"limit": COLOUR_MAX, "colours": len(colours)}
