"""Geometry and readability checks G1-G10 and C1-C6 on a written canvas.

They read the canvas only (node boxes through :func:`cwc.canvas.envelope.envelope`, links
through :mod:`cwc.canvas.links`), never layout internals. Severities follow the mode
(``docs/ARCHITECTURE.md``, M2 contract).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from cwc.canvas.geometry import GRID, Rect, bounding
from cwc.canvas.model import Canvas
from cwc.layout.types import Axis, Preset
from cwc.prompt.slots import DefLookup
from cwc.report import Code, Finding, Mode, Report, Severity
from cwc.verify.geometry_clarity import (
    check_axis,
    check_cockpit_titles,
    check_colours,
    check_crowded,
    check_top_level_count,
    top_level,
)
from cwc.verify.geometry_groups import ancestor_sets, check_ancestry, check_group_overlap, check_membership, links_into
from cwc.verify.geometry_links import check_crossings, check_direction, mean_length
from cwc.verify.geometry_screen import check_first_screen, check_guide_height, check_readable_width, guide_nodes
from cwc.verify.geometry_view import GeometryView
from cwc.verify.policy import Policy

ASPECT_MIN = 1.0
ASPECT_MAX = 2.8

__all__ = ["check_geometry"]


def check_geometry(
    canvas: Canvas,
    defs: DefLookup,
    *,
    mode: Mode,
    membership: Mapping[str, int | None] | None = None,
    before: Canvas | None = None,
    regroup: bool = False,
    preset: Preset | None = None,
    cockpit: Iterable[int] = (),
) -> Report:
    """G1-G10 and C1-C6 for ``canvas`` under ``mode``.

    Boxes: G1 tests the envelope (union of both renderers' boxes). G2 runs once per renderer,
    each link's curve from that renderer's anchors against that renderer's boxes of the
    nodes that are not its ends; a link counts when it crosses under either renderer. G3
    takes a node's group from the frontend rule (smallest group whose bounding holds the
    centre, half-open) and requires the target under the classic box centre and the Vue box
    centre alike, plus the expanded centre for a collapsed node.

    G3 needs a target. ``membership`` (compile, or beautify with ``regroup``): node key to the
    index of its intended innermost group in ``canvas.groups`` (``LayoutResult.membership``).
    ``before`` (beautify without ``regroup``): the input canvas, whose ancestor sets and group
    containment the output must keep. Without either, G3 is not checked and the report says so
    in ``metrics["skipped"]``. ``regroup`` also sets the severities of G8, C3 and C6.

    ``preset`` (``cwc.layout.types.Preset``): C3 reads its axis and G8 its numbering. Without it C3
    is skipped and G8 accepts either numbering ("01 " or "Step 1"). ``cockpit``: the indices in
    ``canvas.groups`` of the cockpit's groups (``LayoutResult.cockpit``: one index, or one per part
    when C1 split the cockpit); empty when there is no cockpit, and then C4 is skipped. G5 and C3
    leave out the links into those groups and into any group nested in them: the prompt encoders
    lifted into the cockpit are wired from the model stack to its right, so those links run
    backward by design. C4 reads the innermost members of all the given groups together.

    G7 and C5 follow the node boxes alone. A guide node is one whose ``properties.cwc_role`` is
    ``"guide"``; without one C5 is skipped and G8 tests only the first interactive group. G8's group
    part runs in compile mode and in beautify mode with ``regroup``. C6 is skipped in beautify mode
    without ``regroup``: kept colours are the user's.
    """
    if isinstance(cockpit, int):
        raise TypeError("cockpit takes a collection of group indices, not one index")
    cockpit_groups = frozenset(cockpit)
    for index in sorted(cockpit_groups):
        if not 0 <= index < len(canvas.groups):
            raise ValueError(f"cockpit index {index} is outside the {len(canvas.groups)} groups")
    policy = Policy.for_mode(mode)
    view = GeometryView.build(canvas, defs)
    report = Report(mode)
    skipped = []

    def record(code: Code, found: tuple[list[Finding], dict[str, Any]]) -> None:
        report.extend(found[0])
        report.metrics[code.value] = found[1]

    record(Code.G1, check_overlap(view, policy))
    record(Code.G2, check_crossings(view, policy))
    ran_target = False
    if membership is not None:
        record(Code.G3, check_membership(view, membership))
        ran_target = True
    if before is not None and not regroup:
        found, metrics = check_ancestry(view, GeometryView.build(before, defs))
        if ran_target:
            report.metrics[Code.G3.value]["ancestry"] = metrics
        else:
            report.metrics[Code.G3.value] = metrics
        report.extend(found)
        ran_target = True
    if not ran_target:
        skipped.append({"code": Code.G3.value, "reason": "no membership or before canvas given"})
    record(Code.G4, check_group_overlap(canvas, policy))
    into_cockpit = links_into(view, cockpit_groups)
    row_wraps = _row_wrap_links(view, preset) - into_cockpit if preset is not None and preset.wrap_rows else frozenset()
    record(Code.G5, check_direction(view, policy, into_cockpit, row_wraps=row_wraps))
    record(Code.G6, check_grid(view, policy))
    record(Code.G9, check_aspect(view))
    report.metrics[Code.G10.value] = _link_length(view, defs, policy, before)
    regrouped = policy.strict or regroup
    record(Code.G7, check_readable_width(view))
    record(Code.G8, check_first_screen(view, preset, interactive=regrouped))
    record(Code.C1, check_crowded(view, policy))
    record(Code.C2, check_top_level_count(view, policy))
    if preset is None:
        skipped.append({"code": Code.C3.value, "reason": "no preset given"})
    else:
        record(Code.C3, check_axis(view, policy, preset, regroup, into_cockpit))
    if not cockpit_groups:
        skipped.append({"code": Code.C4.value, "reason": "no cockpit group given"})
    else:
        record(Code.C4, check_cockpit_titles(view, defs, policy, cockpit_groups))
    if guide_nodes(view):
        record(Code.C5, check_guide_height(view))
    else:
        skipped.append({"code": Code.C5.value, "reason": "no guide node"})
    if regrouped:
        record(Code.C6, check_colours(view))
    else:
        skipped.append({"code": Code.C6.value, "reason": "colours are kept in beautify without regroup"})
    report.metrics["skipped"] = skipped
    return report


def _row_wrap_links(view: GeometryView, preset: Preset) -> frozenset[int]:
    """Links descending from one top-level group strictly into a lower band (``down.y >= up.bottom``
    for ``Axis.X``): carriage-return links in a ``wrap_rows`` preset, exempt from G5's intra-row
    left-to-right check just as C3 treats them as forward reading order."""
    tops = set(top_level(view))
    ancestors = ancestor_sets(view)
    home = {key: min(held & tops) for key, held in ancestors.items() if held & tops}
    boundings = [group.bounding for group in view.canvas.groups]
    out: set[int] = set()
    for link in view.canvas.links:
        origin, target = str(link.origin_id), str(link.target_id)
        if origin not in home or target not in home:
            continue
        up, down = home[origin], home[target]
        if up == down:
            continue
        if preset.axis is Axis.X:
            if boundings[down].y >= boundings[up].bottom:
                out.add(link.id)
        elif boundings[down].x >= boundings[up].right:
            out.add(link.id)
    return frozenset(out)


def _link_length(view: GeometryView, defs: DefLookup, policy: Policy, before: Canvas | None) -> dict[str, Any]:
    after = mean_length(view)
    if before is None or policy.strict:
        return after
    return {"before": mean_length(GeometryView.build(before, defs)), "after": after}


def check_overlap(view: GeometryView, policy: Policy) -> tuple[list[Finding], dict[str, Any]]:
    """G1: node pairs whose envelopes overlap. Beautify: a pair of two pinned nodes is a warning."""
    findings = []
    pinned_pairs = 0
    nodes = view.nodes
    for i, a in enumerate(nodes):
        box_a = view.envelopes[a.key]
        for b in nodes[i + 1 :]:
            box_b = view.envelopes[b.key]
            if not box_a.overlaps(box_b):
                continue
            both_pinned = a.pinned and b.pinned
            pinned_pairs += both_pinned
            findings.append(
                Finding(
                    Code.G1,
                    Severity.WARNING if both_pinned and not policy.strict else Severity.ERROR,
                    f"nodes {a.key} and {b.key} overlap",
                    nodes=(a.key, b.key),
                    data={"pinned": both_pinned},
                )
            )
    return findings, {"pairs": len(findings), "pinned_pairs": pinned_pairs}


def on_grid(value: float) -> bool:
    """True when ``value`` is a multiple of the grid, compared after rounding to 1e-6."""
    return round(value - GRID * round(value / GRID), 6) == 0


def check_grid(view: GeometryView, policy: Policy) -> tuple[list[Finding], dict[str, Any]]:
    """G6: nodes whose ``pos`` is off the 20 px grid; beautify leaves pinned nodes alone."""
    findings = []
    exempt = 0
    for node in view.nodes:
        x, y = node.pos
        if on_grid(x) and on_grid(y):
            continue
        if node.pinned and not policy.strict:
            exempt += 1
            continue
        findings.append(
            Finding(
                Code.G6,
                Severity.ERROR,
                f"node {node.key} is at ({x:g}, {y:g}), off the {GRID} px grid",
                nodes=(node.key,),
                data={"pos": [x, y]},
            )
        )
    return findings, {"off_grid": len(findings), "pinned_exempt": exempt}


def check_aspect(view: GeometryView) -> tuple[list[Finding], dict[str, Any]]:
    """G9: width over height of the union of node envelopes and group boundings must lie in 1 to 2.8."""
    rects: list[Rect] = list(view.envelopes.values()) + [group.bounding for group in view.canvas.groups]
    if not rects:
        return [], {"extent": None, "ratio": None}
    extent = bounding(rects)
    ratio = extent.w / extent.h if extent.h > 0 else None
    metrics = {"extent": extent.as_list(), "ratio": None if ratio is None else round(ratio, 4)}
    if ratio is not None and ASPECT_MIN <= round(ratio, 6) <= ASPECT_MAX:
        return [], metrics
    shown = "unbounded" if ratio is None else f"{ratio:.2f}"
    finding = Finding(
        Code.G9,
        Severity.WARNING,
        f"the canvas extent is {extent.w:g} x {extent.h:g}, aspect ratio {shown}; it should be {ASPECT_MIN:g} to {ASPECT_MAX:g}",
        data={"extent": extent.as_list()},
    )
    return [finding], metrics
