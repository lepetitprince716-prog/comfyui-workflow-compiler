"""Verification: the checks that decide whether an artefact may be written.

* :func:`verify_compile` holds a compiled canvas to every compile rule: structure
  S1-S6, necessity N1-N3, bypass closure and optional-group purity B1-B3, and
  equivalence with the strict prompts Q1-Q2.
* :func:`verify_canvas` runs structure and necessity N1-N6 on any canvas, with the
  severities of the given mode (compile blocks; beautify and check mostly report).

Every check reads the canvas through the prompt package's export rules where a prompt
is involved, so verification and export cannot disagree.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from cwc.canvas.model import Canvas
from cwc.normalize.types import Authoring
from cwc.prompt.slots import DefLookup
from cwc.report import Code, Finding, Mode, Report, Severity
from cwc.verify.bypass import check_closure, check_purity
from cwc.verify.equivalence import check_equivalence, export_states
from cwc.verify.graph import CanvasFacts
from cwc.verify.necessity import check_necessity, check_tidiness
from cwc.verify.policy import Policy
from cwc.verify.structure import BLOCKING_SHAPE, check_structure

__all__ = ["verify_compile", "verify_canvas"]

UNKNOWN_TIER = "unknown"
PROMPT_CHECKS = (Code.B1, Code.B2, Code.Q1, Code.Q2)


def verify_compile(canvas: Canvas, authoring: Authoring, defs: DefLookup) -> Report:
    """Every compile rule for ``canvas``, built from ``authoring``.

    The prompt-based checks are skipped when ids or links are broken (S3, S4 errors).
    """
    facts = CanvasFacts(canvas, defs)
    policy = Policy.for_mode(Mode.COMPILE)
    report = _report(Mode.COMPILE, facts)
    report.extend(check_structure(facts, policy))
    report.extend(check_necessity(facts, policy))
    report.extend(check_purity(facts, authoring))
    if _shape_broken(report.findings):
        report.metrics["skipped"] = [code.value for code in PROMPT_CHECKS]
    else:
        exports = export_states(facts)
        report.extend(check_closure(facts, exports))
        report.extend(check_equivalence(exports, authoring))
    report.findings = _name_authors(report.findings, authoring)
    return report


def _name_authors(findings: list[Finding], authoring: Authoring) -> list[Finding]:
    """Findings name canvas ids; the author wrote other ids. Say both, in the message and in
    ``data["author_ids"]`` (canvas id -> author id), for every node the author renamed."""
    author = {numeric: name for name, numeric in authoring.ids.items() if name != numeric}
    out = []
    for finding in findings:
        named = {key: author[key] for key in finding.nodes if key in author}
        if named:
            label = ", ".join(f"{key} is {name!r}" for key, name in named.items())
            finding = replace(finding, message=f"{finding.message} (author id: {label})", data={**finding.data, "author_ids": named})
        out.append(finding)
    return out


def verify_canvas(canvas: Canvas, defs: DefLookup, *, mode: Mode) -> Report:
    """Structure S1-S6 and necessity N1-N6 under the severities of ``mode``."""
    facts = CanvasFacts(canvas, defs)
    policy = Policy.for_mode(mode)
    report = _report(mode, facts)
    report.extend(check_structure(facts, policy))
    report.extend(check_necessity(facts, policy))
    report.extend(check_tidiness(facts))
    return report


def _shape_broken(findings: list[Finding]) -> bool:
    return any(f.code in BLOCKING_SHAPE and f.severity is Severity.ERROR for f in findings)


def _report(mode: Mode, facts: CanvasFacts) -> Report:
    """An empty report with the node counts and the definition tier of every node."""
    nodes = facts.nodes
    tiers = {}
    for node in nodes:
        nodedef = facts.nodedef(node)
        tiers[node.key] = nodedef.tier.value if nodedef is not None else UNKNOWN_TIER
    counts = Counter(tiers.values())
    metrics = {
        "nodes": len(nodes),
        "links": len(facts.canvas.links),
        "groups": len(facts.canvas.groups),
        "tiers": {tier: counts[tier] for tier in sorted(counts)},
    }
    return Report(mode, metrics=metrics, nodedef_tiers=tiers)
