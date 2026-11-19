"""Necessity diagnostics N1-N6. Static topology and mode checks; nothing is removed.

* N1, N2 use the all-enabled graph: every link counts whatever the node modes. A node of
  unknown type may be an output node, so it counts as one (no false dead-node reports).
  Frontend-only nodes never run and are not reported as dead; links pass through them.
* N3 compares nodes of one type by their widget values and the sources of their links.
* N4-N6 describe canvases cwc did not write; compile output cannot contain them.
  A muted or bypassed node is in a module when some group around it holds only real
  nodes of that same mode. Stray notes are reported only on canvases that have groups.
"""

from __future__ import annotations

from typing import Any

from cwc.canvas.model import Node, NodeMode
from cwc.prompt.model import values_equal
from cwc.report import Code, Finding, Severity
from cwc.nodedefs.types import Behavior
from cwc.verify.graph import OFF_MODES, CanvasFacts, GroupMembers, reaching
from cwc.verify.policy import Policy


def check_necessity(facts: CanvasFacts, policy: Policy) -> list[Finding]:
    """N1-N3: dead nodes, no output node, duplicate nodes."""
    return _dead_nodes(facts, policy) + _duplicates(facts)


def check_tidiness(facts: CanvasFacts) -> list[Finding]:
    """N4-N6, report-only: legacy reroutes, stray off nodes, empty groups and stray notes."""
    groups = facts.groups()
    return _legacy_reroutes(facts) + _stray_off_nodes(facts, groups) + _empty_and_stray(facts, groups)


def _dead_nodes(facts: CanvasFacts, policy: Policy) -> list[Finding]:
    real = facts.real_nodes()
    outputs = {node.key for node in real if (nodedef := facts.nodedef(node)) is None or nodedef.output_node}
    if not outputs:
        return [Finding(Code.N2, policy.compile_only, "the graph has no output node, so nothing would run")]
    alive = reaching(facts.canvas_edges(), outputs)
    return [
        Finding(Code.N1, policy.compile_only, f"node {node.key} ({node.type}) does not reach any output node", nodes=(node.key,))
        for node in real
        if node.key not in alive
    ]


def _duplicates(facts: CanvasFacts) -> list[Finding]:
    links = facts.canvas.link_map()
    clusters: list[tuple[Node, Any, list[Node]]] = []  # (first node, signature, all members)
    for node in facts.real_nodes():
        sources = {
            slot.get("name"): (str(links[slot["link"]].origin_id), links[slot["link"]].origin_slot)
            for slot in node.inputs
            if slot.get("link") in links
        }
        signature = (node.widgets_values, sorted(sources.items(), key=lambda item: str(item[0])))
        for first, other, members in clusters:
            if first.type == node.type and _same(signature, other):
                members.append(node)
                break
        else:
            clusters.append((node, signature, [node]))
    return [
        Finding(
            Code.N3,
            Severity.INFO,
            f"nodes {', '.join(m.key for m in members)} ({first.type}) have the same inputs",
            nodes=tuple(m.key for m in members),
        )
        for first, _, members in clusters
        if len(members) > 1
    ]


def _same(a: Any, b: Any) -> bool:
    (values_a, sources_a), (values_b, sources_b) = a, b
    return values_equal(values_a, values_b) and sources_a == sources_b


def _legacy_reroutes(facts: CanvasFacts) -> list[Finding]:
    out = []
    for node in facts.nodes:
        if facts.behavior(node) is not Behavior.PASSTHROUGH:
            continue
        consumers = sum(len(slot.get("links") or []) for slot in node.outputs)
        out.append(
            Finding(
                Code.N4,
                Severity.INFO,
                f"node {node.key} is a legacy reroute node with {consumers} consumer(s)",
                nodes=(node.key,),
                hint="native link reroutes do the same without a node",
                data={"consumers": consumers},
            )
        )
    return out


def _stray_off_nodes(facts: CanvasFacts, groups: list[GroupMembers]) -> list[Finding]:
    module_modes: dict[str, set[int]] = {}
    for group in groups:
        modes = {node.mode for node in group.members if not facts.virtual(node)}
        if len(modes) == 1 and next(iter(modes)) in OFF_MODES:
            for node in group.members:
                module_modes.setdefault(node.key, set()).update(modes)
    out = []
    for node in facts.real_nodes():
        if node.mode in OFF_MODES and node.mode not in module_modes.get(node.key, set()):
            state = "muted" if node.mode == NodeMode.NEVER else "bypassed"
            out.append(
                Finding(
                    Code.N5,
                    Severity.INFO,
                    f"node {node.key} is {state} but no group around it is a {state} module",
                    nodes=(node.key,),
                    hint="put it in a group of its own so the group toggles it",
                )
            )
    return out


def _empty_and_stray(facts: CanvasFacts, groups: list[GroupMembers]) -> list[Finding]:
    out = [
        Finding(Code.N6, Severity.INFO, f"{group.label()} holds no node", data={"group": group.index})
        for group in groups
        if not group.members
    ]
    if not groups:
        return out
    grouped = {node.key for group in groups for node in group.members}
    for node in facts.nodes:
        if facts.behavior(node) is Behavior.NOTE and node.key not in grouped:
            out.append(Finding(Code.N6, Severity.INFO, f"note {node.key} is outside every group", nodes=(node.key,)))
    return out
