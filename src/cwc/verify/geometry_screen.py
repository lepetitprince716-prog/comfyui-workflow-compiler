"""Reading checks about single nodes and the first screen: G7 (cut text), G8 (first screen), C5 (guide height).

A guide node is a node whose ``properties.cwc_role`` is ``"guide"``. M2 writes none, so the
guide parts of G8 and C5 apply only to canvases that carry one. The first screen is the
rectangle ``[0, 0, 1600, 900]`` (``docs/m2-frontend-facts.md``).
"""

from __future__ import annotations

import re
from typing import Any

from cwc.canvas.envelope import readable_width
from cwc.canvas.geometry import Rect
from cwc.canvas.model import Group, id_order
from cwc.layout.types import Numbering, Preset
from cwc.report import Code, Finding, Severity
from cwc.verify.geometry_groups import ancestor_sets
from cwc.verify.geometry_view import GeometryView

FIRST_SCREEN = Rect(0, 0, 1600, 900)
TITLE_BAND = 30  # px; the first row of a group's bounding
TITLE_REACH = 600  # px of the title band, from the group's left edge, that must be on screen
GUIDE_MAX_HEIGHT = 860
WIDTH_TOLERANCE = 1e-6
NUMBER_PREFIX = re.compile(r"01[a-z]? ")  # "01 Load", or "01a Load" for the first part of a split group
STEP_PREFIX = re.compile(r"Step 1(?!\d)")

GUIDE_ROLE = "guide"


def guide_nodes(view: GeometryView) -> list[str]:
    """Keys of the guide nodes, in id order."""
    return [node.key for node in view.nodes if node.properties.get("cwc_role") == GUIDE_ROLE]


def _fmt(rect: Rect) -> str:
    return f"[{rect.x:g}, {rect.y:g}, {rect.w:g}, {rect.h:g}]"


def check_readable_width(view: GeometryView) -> tuple[list[Finding], dict[str, Any]]:
    """G7: expanded nodes narrower than :func:`cwc.canvas.envelope.readable_width`."""
    findings = []
    expanded = 0
    for node in view.nodes:
        if node.collapsed:
            continue
        expanded += 1
        need = readable_width(view.facts[node.key])
        width = node.size[0]
        if width < need - WIDTH_TOLERANCE:
            findings.append(
                Finding(
                    Code.G7,
                    Severity.ERROR,
                    f"node {node.key} is {width:g} px wide; its title or widget labels need {need:.1f} px",
                    nodes=(node.key,),
                    data={"width": width, "needed": round(need, 3)},
                )
            )
    return findings, {"expanded": expanded, "narrow": len(findings)}


def first_group(groups: list[Group], preset: Preset | None) -> int | None:
    """Index of the first interactive group: the first whose title starts with ``"01 "`` or,
    for the first part of a split group, ``"01a "`` (numbering ``number``) or ``"Step 1"`` not followed by a digit (numbering ``step``);
    without a preset either numbering counts."""
    for index, group in enumerate(groups):
        title = group.title
        by_number = NUMBER_PREFIX.match(title) is not None
        by_step = STEP_PREFIX.match(title) is not None
        if preset is None and (by_number or by_step):
            return index
        if preset is not None and (by_number if preset.numbering is Numbering.NUMBER else by_step):
            return index
    return None


def _first_title_forms(preset: Preset | None) -> str:
    number, step = '"01 " or "01a " at the start', '"Step 1" not followed by a digit at the start'
    if preset is None:
        return f"{number}, or {step}"
    return number if preset.numbering is Numbering.NUMBER else step


def _topmost(view: GeometryView, members: list[str]) -> str:
    """The member with the smallest ``pos`` y, then x, then id order."""
    nodes = {node.key: node for node in view.nodes}
    return min(members, key=lambda key: (nodes[key].pos[1], nodes[key].pos[0], id_order(key)))


def check_first_screen(
    view: GeometryView, preset: Preset | None, *, interactive: bool
) -> tuple[list[Finding], dict[str, Any]]:
    """G8. Part (a): a guide node's envelope lies in the first screen. Part (b), run when
    ``interactive``: the first interactive group's title band (its first 600 px) and the
    envelope of its top-most member lie in the first screen. A canvas without a guide has nothing
    to test for part (a). Part (b) finds no first interactive group when no title matches: a
    canvas with nodes then gets one error (a compiled or regrouped canvas always has the group),
    an empty canvas none."""
    findings = []
    guides = guide_nodes(view)
    for key in guides:
        box = view.envelopes[key]
        if not FIRST_SCREEN.contains_rect(box):
            findings.append(
                Finding(
                    Code.G8,
                    Severity.ERROR,
                    f"the guide node {key} {_fmt(box)} is not inside the first screen {_fmt(FIRST_SCREEN)}",
                    nodes=(key,),
                    data={"part": "guide", "box": box.as_list()},
                )
            )
    metrics: dict[str, Any] = {"guides": len(guides), "first_group": None, "group_part": interactive}
    if not interactive:
        return findings, metrics
    groups = view.canvas.groups
    index = first_group(groups, preset)
    if index is None:
        if view.nodes:
            wanted = _first_title_forms(preset)
            findings.append(
                Finding(
                    Code.G8,
                    Severity.ERROR,
                    f"no group title is the first interactive group's ({wanted}), so the first screen has nothing to test",
                    data={"part": "title", "group": None, "wanted": wanted},
                )
            )
        return findings, metrics
    metrics["first_group"] = index
    bounding = groups[index].bounding
    band = Rect(bounding.x, bounding.y, min(bounding.w, TITLE_REACH), TITLE_BAND)
    if not FIRST_SCREEN.contains_rect(band):
        findings.append(
            Finding(
                Code.G8,
                Severity.ERROR,
                f"the title band {_fmt(band)} of group {index} {groups[index].title!r} is not inside the first screen {_fmt(FIRST_SCREEN)}",
                data={"part": "title", "group": index, "box": band.as_list()},
            )
        )
    members = [key for key, held in ancestor_sets(view).items() if index in held]
    if members:
        top = _topmost(view, members)
        box = view.envelopes[top]
        metrics["top_member"] = top
        if not FIRST_SCREEN.contains_rect(box):
            findings.append(
                Finding(
                    Code.G8,
                    Severity.ERROR,
                    f"node {top}, the top-most member of group {index} {groups[index].title!r}, {_fmt(box)} is not inside the first screen {_fmt(FIRST_SCREEN)}",
                    nodes=(top,),
                    data={"part": "member", "group": index, "box": box.as_list()},
                )
            )
    return findings, metrics


def check_guide_height(view: GeometryView) -> tuple[list[Finding], dict[str, Any]]:
    """C5: a guide node whose envelope is taller than 860 px."""
    findings = []
    guides = guide_nodes(view)
    for key in guides:
        height = view.envelopes[key].h
        if round(height, 6) > GUIDE_MAX_HEIGHT:
            findings.append(
                Finding(
                    Code.C5,
                    Severity.ERROR,
                    f"the guide node {key} is {height:g} px tall; the limit is {GUIDE_MAX_HEIGHT}",
                    nodes=(key,),
                    data={"height": height},
                )
            )
    return findings, {"guides": len(guides), "too_tall": len(findings)}
