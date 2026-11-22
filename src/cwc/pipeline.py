"""The compile pipeline: an authoring document to a verified canvas and its prompts.

Stages: normalize -> instantiate -> annotate -> apply_notes -> layout -> route -> verify
(``verify_compile`` and ``check_geometry`` in compile mode, merged into one report). Every
stage reads the previous stage's output only. Notes and the guide (M3) are not written yet.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from cwc.annotate import annotate, apply_notes
from cwc.annotate.types import PlanCounts
from cwc.canvas.model import Canvas, id_order
from cwc.effects import authoring_effects, overridden_keys
from cwc.instantiate import instantiate
from cwc.layout import layout
from cwc.canvas.geometry import snap_up
from cwc.layout.presets import DEFAULT, load_presets
from cwc.layout.section import BAND_GAP, SCREEN_W
from cwc.layout.types import LayoutResult
from cwc.nodedefs.registry import NodeDefs
from cwc.normalize import normalize
from cwc.normalize.types import Authoring
from cwc.notes import place_guide
from cwc.report import Code, CwcError, ExitCode, Finding, Mode, Report, Severity
from cwc.route import RouteResult, route
from cwc.verify import verify_compile
from cwc.verify.geometry import check_geometry

AUTO = "auto"  # choose the preset from the plan's counts (:func:`choose_auto`)
AUTHORED = "_workflow.layout.preset"

# The automatic choice's thresholds (small graphs, many parallel control branches, large graphs;
# docs/ARCHITECTURE.md, row ``pipeline``). Code, not data: the rules' order and comparisons
# are code anyway, and the presets
# document holds only the presets themselves.
AUTO_SMALL_NODES = 16  # at most this many nodes, and no optional module: tower-preview
AUTO_PARALLEL_BRANCHES = 3  # at least this many control or mask branches: swimlanes
AUTO_LARGE_NODES = 60  # at least this many nodes: cockpit-split


def preset_guide() -> dict[str, tuple[str, str]]:
    """`cwc presets`: what each preset looks like and when to pick it, by name, ``auto`` last.

    Only what layout does today: the corridors the presets name are reserved space, and the
    router does not use them yet (docs/ARCHITECTURE.md), so no description promises them for
    links. Numbers come from the presets document and the layout constants. A test keeps the
    names here and in presets.json the same, and measures the geometry described."""
    presets = load_presets()
    bento, tower = presets["bento"], presets["tower-preview"]
    preview = tower.preview
    assert preview is not None  # the presets document gives tower-preview its preview
    preview_w = snap_up(round(preview.share * SCREEN_W, 6))
    wrap = f"The main row wraps onto a new row below when it gets wider than {bento.wrap_ratio:g} times its height."
    columns = (
        "every other group in one row of columns in data-flow order, a control branch in the column of "
        "the stage it steers, no second row"
    )
    return {
        "bento": (
            "Controls group first, then models and prompts side by side, sampling, post-processing and output "
            f"in one main row; control branches and optional modules in a second row below it. {wrap}",
            "most graphs; the default",
        ),
        "swimlanes": (
            f"Controls group first, then {columns}; an empty strip {BAND_GAP} px high above each row.",
            f"several parallel control or mask branches (auto picks it at {AUTO_PARALLEL_BRANCHES} or more)",
        ),
        "cockpit-split": (
            "Controls group first, split in two boxes when it holds both kinds: prompts, reference image, size "
            f"and seed in the top box, the other controls in a box under it. Then a gap of {BAND_GAP} px and "
            f"{columns}.",
            f"large graphs (auto picks it at {AUTO_LARGE_NODES} nodes or more)",
        ),
        "tower-preview": (
            "The bento arrangement. When the output group holds a node that shows an image preview, the group "
            f"is at least {preview_w} px wide and {preview.min_height} px high and the preview node grows to "
            "fill it.",
            f"small graphs whose result you look at while tuning (auto picks it at {AUTO_SMALL_NODES} nodes or "
            "fewer with no optional module)",
        ),
        "stepped": (
            "Groups titled Step 1, Step 2 and so on; the models and Controls groups one above "
            "the other in the first column, then prompts, sampling, post-processing and output; control "
            "branches and optional modules in a second row.",
            "a graph meant to be followed in order, like a tutorial",
        ),
        "gallery": (
            f"Controls stays paired with the top pipeline row (separated by {BAND_GAP} px; tall "
            "cockpits of 5+ controls split side by side) with the final output flush on the right; "
            "intermediate step previews, control branches and optional comparisons dock below the "
            f"stages that feed them with enlarged preview viewports (at least "
            f"{snap_up(round(presets['gallery'].preview.share * SCREEN_W, 6))} px wide and "
            f"{presets['gallery'].preview.min_height} px high).",
            "multi-step workflows with intermediate step previews or side-by-side comparisons",
        ),
        "square": (
            f"Compact square / 4:3 dashboard layout: folds wide multi-stage pipelines into balanced "
            f"stacked rows (wrapping past {presets['square'].wrap_ratio:g}x height via clean cross-row "
            "carriage returns) and docks step previews and optional comparisons inside each row's width "
            f"with enlarged viewports (at least "
            f"{snap_up(round(presets['square'].preview.share * SCREEN_W, 6))} px wide and "
            f"{presets['square'].preview.min_height} px high).",
            "laptops, 4:3/square showcase screenshots, or multi-stage graphs without wide horizontal scrolling",
        ),
        AUTO: (
            f"Picks a preset from the graph's counts, the first rule that holds: at most {AUTO_SMALL_NODES} "
            f"nodes and no optional module: tower-preview; {AUTO_PARALLEL_BRANCHES} or more control or mask "
            f"branches: swimlanes; {AUTO_LARGE_NODES} nodes or more: cockpit-split; otherwise: bento. The "
            "compile summary and the report say which rule held.",
            "you have no preference",
        ),
    }


@dataclass(frozen=True)
class AutoChoice:
    preset: str
    rule: str  # the rule that matched, for the report and error messages


@dataclass(frozen=True)
class Compiled:
    authoring: Authoring
    canvas: Canvas
    report: Report  # verification and geometry findings plus the authoring warnings
    layout: LayoutResult
    routes: RouteResult
    counts: PlanCounts  # what auto read; the command line's summary reports the optional modules


def compile_document(
    doc: dict[str, Any],
    defs: NodeDefs,
    *,
    allow_unknown: bool = False,
    preset: str | None = None,
    portable: bool = False,
) -> Compiled:
    """Compile a parsed authoring document.

    The layout preset is ``preset`` when given, else the document's ``_workflow.layout.preset``,
    else ``bento``; ``auto`` (from either place) picks one after annotate by :func:`choose_auto`,
    and the report's ``metrics["preset_auto"]`` then records the rule and the counts. Raises ``CwcError(INPUT_ERROR)`` for authoring errors (E1-E7), for an
    unknown preset name, and for a preset layout does not implement yet (E7), also when
    ``auto`` chose it: it never falls back to another preset. Verification
    and geometry failures do not raise: they are errors in the returned report, and
    ``report.ok`` is false.
    """
    authoring, warnings = normalize(doc, defs, allow_unknown=allow_unknown, portable=portable)
    name = _choose(preset, authoring.workflow.preset)
    canvas = instantiate(authoring)
    plan = annotate(canvas, defs, authoring)
    apply_notes(canvas, plan)
    auto = choose_auto(plan.counts) if name == AUTO else None
    chosen = load_presets()[auto.preset if auto is not None else name]
    try:
        laid = layout(canvas, plan, chosen, defs)
    except NotImplementedError as exc:
        if auto is None:
            message = f"preset {chosen.name!r} is not available yet: {exc}"
        else:
            message = (
                f"preset {AUTO!r} is not available yet for this graph: it chose {chosen.name!r} "
                f"({auto.rule}; {_counts_text(plan.counts)}), and {exc}"
            )
        raise CwcError(ExitCode.INPUT_ERROR, [_preset_error(message)]) from None
    _, laid = place_guide(canvas, authoring, plan, laid, chosen, defs)
    routes = route(canvas, laid, defs)
    report = verify_compile(canvas, authoring, defs)
    geometry = check_geometry(
        canvas, defs, mode=Mode.COMPILE, membership=laid.membership, preset=chosen, cockpit=laid.cockpit
    )
    report.extend(_with_unresolved(geometry.findings, canvas, routes))
    report.metrics["preset"] = chosen.name
    if auto is not None:
        report.metrics["preset_auto"] = {"rule": auto.rule, **_counts_json(plan.counts)}
    report.metrics["geometry"] = geometry.metrics
    report.metrics["route"] = _route_metrics(routes)
    effects = authoring_effects(authoring, plan, canvas)
    report.metrics["authoring_keys"] = effects
    report.extend(warnings)
    report.extend(overridden_keys(authoring, effects))
    report.extend(authoring.notices)
    return Compiled(authoring, canvas, report, laid, routes, plan.counts)


def choose_auto(counts: PlanCounts) -> AutoChoice:
    """The preset ``auto`` picks: the first rule that holds, in the order of
    docs/ARCHITECTURE.md (a small graph with no optional module beats many control branches,
    which beat a large graph)."""
    if counts.nodes <= AUTO_SMALL_NODES and counts.optional_modules == 0:
        return AutoChoice("tower-preview", f"at most {AUTO_SMALL_NODES} nodes and no optional module")
    if counts.control_branches >= AUTO_PARALLEL_BRANCHES:
        return AutoChoice("swimlanes", f"at least {AUTO_PARALLEL_BRANCHES} control or mask branches")
    if counts.nodes >= AUTO_LARGE_NODES:
        return AutoChoice("cockpit-split", f"at least {AUTO_LARGE_NODES} nodes")
    return AutoChoice(DEFAULT, "no other rule holds")


def _counts_json(counts: PlanCounts) -> dict[str, int]:
    return {"nodes": counts.nodes, "optional_modules": counts.optional_modules, "control_branches": counts.control_branches}


def _counts_text(counts: PlanCounts) -> str:
    return ", ".join(f"{key} {value}" for key, value in _counts_json(counts).items())


def _choose(given: str | None, authored: str | None) -> str:
    """The preset name by priority: ``given`` (the command line), the authored one, the default;
    ``auto`` is resolved later, from the plan. An unknown authored name is an error even when
    ``given`` overrides it: a misspelt name would otherwise go unnoticed until the override is
    dropped."""
    presets = load_presets()
    unknown = [
        _preset_error(f"unknown preset {name!r}{where}")
        for where, name in (("", given), (f" in {AUTHORED}", authored))
        if name is not None and name != AUTO and name not in presets
    ]
    if unknown:
        raise CwcError(ExitCode.INPUT_ERROR, unknown)
    return given if given is not None else authored if authored is not None else DEFAULT


def _preset_error(message: str) -> Finding:
    names = ", ".join(sorted([*load_presets(), AUTO]))
    return Finding(Code.E7, Severity.ERROR, message, hint=f"presets: {names}")


def _with_unresolved(findings: list[Finding], canvas: Canvas, routes: RouteResult) -> list[Finding]:
    """``findings`` with one G2 error per link routing could not keep out of node boxes (it keeps
    its plain spline): the geometry check's own G2 for that link, marked ``unresolved``, or one of
    the router's when the check did not report it."""
    stuck = frozenset(routes.unresolved)
    reported: list[int] = []
    out = []
    for finding in findings:
        link_id = finding.data.get("link") if finding.code is Code.G2 else None
        if link_id in stuck:
            reported.append(link_id)
            finding = replace(finding, data={**finding.data, "unresolved": True})
        out.append(finding)
    links = {link.id: link for link in canvas.links}
    for link_id in routes.unresolved:
        if link_id in reported:
            continue
        link = links[link_id]
        ends = (str(link.origin_id), str(link.target_id))
        out.append(
            Finding(
                Code.G2,
                Severity.ERROR,
                f"routing found no path for link {link_id} from node {ends[0]} to node {ends[1]} clear of node boxes",
                nodes=tuple(sorted(set(ends), key=id_order)),
                data={"link": link_id, "unresolved": True},
            )
        )
    return out


def _route_metrics(routes: RouteResult) -> dict[str, Any]:
    return {
        "rerouted": len(routes.rerouted),
        "dummy": routes.dummy,
        "unresolved": list(routes.unresolved),
        "short_channels": len(routes.shortfalls),
        "shared_edges": routes.shared_edges,
    }
