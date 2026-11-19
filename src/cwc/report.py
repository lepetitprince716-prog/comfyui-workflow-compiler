"""Findings, check codes, reports and process exit codes.

Every check in cwc reports through :class:`Finding`. A check decides the severity itself,
because the same code can block in ``compile`` mode and only warn in ``beautify`` mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterable

from cwc import FORMAT_VERSION, TOOL_NAME
from cwc.canvas.model import id_order


class ExitCode(IntEnum):
    OK = 0
    CHECK_FAILED = 1  # verification ran and at least one blocking finding was raised
    USAGE = 2  # bad command line (argparse uses 2 as well)
    INPUT_ERROR = 3  # the input file is unreadable, malformed or fails authoring checks
    NODEDEFS_MISSING = 4  # node definitions needed for the input are not available
    WRITE_FAILED = 5  # an output could not be written; the run's artefacts were removed
    INTERNAL = 70  # a bug in cwc: an exception no check anticipated (EX_SOFTWARE)


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Mode(str, Enum):
    COMPILE = "compile"
    BEAUTIFY = "beautify"
    CHECK = "check"


class Code(str, Enum):
    """Every finding code. Titles live in :data:`TITLES`."""

    # Ingest: the input cannot be read as a supported document.
    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"
    # Node definitions.
    D1 = "D1"
    # Authoring checks (compile only).
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    E5 = "E5"
    E6 = "E6"
    E7 = "E7"
    # Structure.
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"
    S6 = "S6"
    # Geometry.
    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"
    G6 = "G6"
    G7 = "G7"
    G8 = "G8"
    G9 = "G9"
    G10 = "G10"
    # Readability.
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    C5 = "C5"
    C6 = "C6"
    # Necessity diagnostics.
    N1 = "N1"
    N2 = "N2"
    N3 = "N3"
    N4 = "N4"
    N5 = "N5"
    N6 = "N6"
    # Bypass closure and optional-group purity.
    B1 = "B1"
    B2 = "B2"
    B3 = "B3"
    # Equivalence.
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    # Pre-publication privacy.
    P1 = "P1"
    # Live frontend check.
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


TITLES: dict[Code, str] = {
    Code.I1: "input is not valid JSON or PNG, or is not a workflow",
    Code.I2: "unsupported workflow version",
    Code.I3: "input exceeds size or nesting limits",
    Code.I4: "PNG carries no workflow or prompt text chunk",
    Code.D1: "node definitions cannot be read",
    Code.E1: "unknown node type",
    Code.E2: "input name not defined for the node type",
    Code.E3: "required input without a declared default is missing",
    Code.E4: "link connects incompatible types",
    Code.E5: "literal value has the wrong type, is out of range, or is not a known option",
    Code.E6: "link points at a missing node or output, or names an ambiguous output",
    Code.E7: "graph structure is invalid (cycle, bad id, bad _workflow, mixed optional group)",
    Code.S1: "root version is not 0.4",
    Code.S2: "node is missing a required field",
    Code.S3: "node or link id is not unique",
    Code.S4: "link endpoint does not exist or link tables disagree",
    Code.S5: "last_node_id or last_link_id is not the maximum id",
    Code.S6: "widgets_values_named disagrees with widgets_values",
    Code.G1: "node frames overlap",
    Code.G2: "link passes through a node that is not its endpoint",
    Code.G3: "node group membership differs from the target",
    Code.G4: "groups overlap without nesting",
    Code.G5: "share of right-to-left links is too high",
    Code.G6: "node position is not on the 20 px grid",
    Code.G7: "title or widget label would be truncated",
    Code.G8: "guide or first interactive group is outside the first screen",
    Code.G9: "canvas aspect ratio is outside 1:1 to 2.8:1",
    Code.G10: "average link length",
    Code.C1: "group has more than 12 expanded nodes",
    Code.C2: "more than 12 top-level groups",
    Code.C3: "main-line groups do not advance along the preset axis in topological order",
    Code.C4: "cockpit node uses a class name as title, or titles repeat within a group",
    Code.C5: "guide note is taller than 860 px",
    Code.C6: "more than 7 group colour categories",
    Code.N1: "node does not reach any output node",
    Code.N2: "graph has no output node",
    Code.N3: "duplicate node (same type and inputs)",
    Code.N4: "legacy Reroute node or redundant pass-through node",
    Code.N5: "bypassed or muted node outside an optional module",
    Code.N6: "empty group or stray note",
    Code.B1: "factory state leaves a required input of an active node unconnected",
    Code.B2: "upstream node used only by an optional module is not marked optional",
    Code.B3: "optional group mixes bypassed and active nodes, or sits inside a non-optional group",
    Code.Q1: "all-enabled prompt differs from the strict prompt",
    Code.Q2: "factory-state prompt differs from the strict factory prompt",
    Code.Q3: "beautify changed a field outside the geometry allow-list",
    Code.Q4: "beautify changed group ancestry or group nesting",
    Code.P1: "possible private data (local path, user name, internal address, secret)",
    Code.L1: "frontend reported an error while loading the workflow",
    Code.L2: "prompt exported by the real frontend differs from the strict prompt",
    Code.L3: "geometry measured in the real frontend fails a geometry check",
}


@dataclass(frozen=True)
class Finding:
    code: Code
    severity: Severity
    message: str
    nodes: tuple[str, ...] = ()
    hint: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code.value,
            "severity": self.severity.value,
            "title": TITLES[self.code],
            "message": self.message,
        }
        if self.nodes:
            out["nodes"] = list(self.nodes)
        if self.hint:
            out["hint"] = self.hint
        if self.data:
            out["data"] = self.data
        return out


_CODE_ORDER = {code: index for index, code in enumerate(Code)}


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Deterministic order: by code declaration order, then node ids, then message."""
    return sorted(findings, key=lambda f: (_CODE_ORDER[f.code], f.nodes, f.message))


class VerificationTier(str, Enum):
    SELF = "self"  # built-in checks only; the artefact is a draft
    LIVE_FRONTEND = "live_frontend"  # also loaded and exported by a real frontend


@dataclass
class Report:
    mode: Mode
    findings: list[Finding] = field(default_factory=list)
    verification_tier: VerificationTier = VerificationTier.SELF
    metrics: dict[str, Any] = field(default_factory=dict)
    nodedef_tiers: dict[str, str] = field(default_factory=dict)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: Iterable[Finding]) -> None:
        self.findings.extend(findings)

    @property
    def ok(self) -> bool:
        return not any(f.severity is Severity.ERROR for f in self.findings)

    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    def to_json(self) -> dict[str, Any]:
        return {
            "tool": TOOL_NAME,
            "format": FORMAT_VERSION,
            "mode": self.mode.value,
            "ok": self.ok,
            "verification_tier": self.verification_tier.value,
            "findings": [f.to_json() for f in sort_findings(self.findings)],
            "metrics": {key: self.metrics[key] for key in sorted(self.metrics)},
            "nodedef_tiers": {key: self.nodedef_tiers[key] for key in sorted(self.nodedef_tiers, key=id_order)},
        }


class CwcError(Exception):
    """An error that ends a command with a specific exit code and findings to report."""

    def __init__(self, exit_code: ExitCode, findings: list[Finding]):
        super().__init__("; ".join(f"{f.code.value}: {f.message}" for f in findings))
        self.exit_code = exit_code
        self.findings = findings
