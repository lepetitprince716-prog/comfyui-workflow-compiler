"""Structure checks S1-S6.

Always blocking: the root version (S1), the fields every node needs (S2), unique ids
(S3) and links whose endpoints exist (S4). Compile output must also keep the three link
records in step (S4), carry exact ``last_node_id``/``last_link_id`` (S5) and a
``widgets_values_named`` table that matches ``widgets_values`` (S6); elsewhere those are
reported without blocking, and object-form ``widgets_values`` are not checked.

S6 matters because a frontend loaded with default settings restores widgets by position
and ignores the named table, so a named table that disagrees would mislead a reader.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from cwc.canvas.model import ROOT_VERSION, Link, Node, is_ascii_decimal
from cwc.nodedefs.derive import widget_layout
from cwc.nodedefs.types import NodeDef
from cwc.prompt.model import values_equal
from cwc.report import Code, Finding, Severity
from cwc.verify.graph import CanvasFacts, sorted_keys
from cwc.verify.policy import Policy

# Keys the 0.4 schema requires on every node.
REQUIRED_NODE_FIELDS = ("id", "type", "pos", "size", "flags", "order", "mode", "properties")

BLOCKING_SHAPE = frozenset({Code.S3, Code.S4})  # later checks cannot trust the graph past these


def check_structure(facts: CanvasFacts, policy: Policy) -> list[Finding]:
    findings = _version(facts)
    findings += _fields(facts)
    findings += _unique_ids(facts)
    findings += _links(facts, policy)
    findings += _last_ids(facts, policy)
    findings += _named_widgets(facts, policy)
    return findings


def _version(facts: CanvasFacts) -> list[Finding]:
    version = facts.canvas.raw.get("version")
    if isinstance(version, (int, float)) and not isinstance(version, bool) and version == ROOT_VERSION:
        return []
    return [Finding(Code.S1, Severity.ERROR, f"root version is {version!r}, expected {ROOT_VERSION}")]


def _fields(facts: CanvasFacts) -> list[Finding]:
    out = []
    for node in facts.nodes:
        missing = [key for key in REQUIRED_NODE_FIELDS if key not in node.raw]
        if missing:
            out.append(
                Finding(
                    Code.S2,
                    Severity.ERROR,
                    f"node {node.key} lacks {', '.join(missing)}",
                    nodes=(node.key,),
                    data={"missing": missing},
                )
            )
    return out


def _unique_ids(facts: CanvasFacts) -> list[Finding]:
    out = []
    node_counts = Counter(node.key for node in facts.canvas.nodes)
    for key in sorted_keys(k for k, n in node_counts.items() if n > 1):
        out.append(Finding(Code.S3, Severity.ERROR, f"node id {key} is used {node_counts[key]} times", nodes=(key,)))
    link_counts = Counter(link.id for link in facts.canvas.links)
    for link_id in sorted(k for k, n in link_counts.items() if n > 1):
        out.append(
            Finding(Code.S3, Severity.ERROR, f"link id {link_id} is used {link_counts[link_id]} times", data={"link": link_id})
        )
    return out


def _links(facts: CanvasFacts, policy: Policy) -> list[Finding]:
    """S4: endpoints exist (always blocking); the links table, each target's
    ``inputs[].link`` and each source's ``outputs[].links`` agree (compile only)."""
    nodes = {node.key: node for node in facts.canvas.nodes}
    links = facts.canvas.link_map()
    out: list[Finding] = []
    for link in sorted(links.values(), key=lambda item: item.id):
        endpoint = _endpoint_problem(link, nodes)
        if endpoint:
            out.append(_s4(Severity.ERROR, f"link {link.id}: {endpoint}", link))
            continue
        origin, target = nodes[str(link.origin_id)], nodes[str(link.target_id)]
        if target.inputs[link.target_slot].get("link") != link.id:
            out.append(_s4(policy.compile_only, f"link {link.id}: input {link.target_slot} of node {target.key} does not name it", link))
        if link.id not in (origin.outputs[link.origin_slot].get("links") or []):
            out.append(_s4(policy.compile_only, f"link {link.id}: output {link.origin_slot} of node {origin.key} does not list it", link))
    for node in facts.nodes:
        for index, slot in enumerate(node.inputs):
            link_id = slot.get("link")
            if link_id is None:
                continue
            link = links.get(link_id)
            if link is None:
                out.append(_s4(policy.compile_only, f"node {node.key} input {index} names link {link_id}, which is not in the links table", None, node.key))
            elif (str(link.target_id), link.target_slot) != (node.key, index):
                out.append(_s4(policy.compile_only, f"node {node.key} input {index} names link {link_id}, which ends elsewhere", link, node.key))
        for index, slot in enumerate(node.outputs):
            for link_id in slot.get("links") or []:
                link = links.get(link_id)
                if link is None:
                    out.append(_s4(policy.compile_only, f"node {node.key} output {index} lists link {link_id}, which is not in the links table", None, node.key))
                elif (str(link.origin_id), link.origin_slot) != (node.key, index):
                    out.append(_s4(policy.compile_only, f"node {node.key} output {index} lists link {link_id}, which starts elsewhere", link, node.key))
    return out


def _endpoint_problem(link: Link, nodes: dict[str, Node]) -> str:
    origin, target = nodes.get(str(link.origin_id)), nodes.get(str(link.target_id))
    problems = []
    if origin is None:
        problems.append(f"origin node {link.origin_id} does not exist")
    elif not 0 <= link.origin_slot < len(origin.outputs):
        problems.append(f"node {origin.key} has no output {link.origin_slot}")
    if target is None:
        problems.append(f"target node {link.target_id} does not exist")
    elif not 0 <= link.target_slot < len(target.inputs):
        problems.append(f"node {target.key} has no input {link.target_slot}")
    return "; ".join(problems)


def _s4(severity: Severity, message: str, link: Link | None, *extra: str) -> Finding:
    keys = list(extra)
    if link is not None:
        keys += [str(link.origin_id), str(link.target_id)]
    data = {"link": link.id} if link is not None else {}
    return Finding(Code.S4, severity, message, nodes=sorted_keys(keys), data=data)


def _last_ids(facts: CanvasFacts, policy: Policy) -> list[Finding]:
    raw = facts.canvas.raw
    expected = {
        "last_node_id": max((int(node.key) for node in facts.canvas.nodes if is_ascii_decimal(node.key)), default=0),
        "last_link_id": max((link.id for link in facts.canvas.links), default=0),
    }
    out = []
    for key, want in expected.items():
        have = raw.get(key)
        if not (isinstance(have, int) and not isinstance(have, bool) and have == want):
            out.append(
                Finding(Code.S5, policy.compile_only, f"{key} is {have!r}, the largest id is {want}", data={"field": key, "expected": want})
            )
    return out


def _named_widgets(facts: CanvasFacts, policy: Policy) -> list[Finding]:
    out = []
    for node in facts.nodes:
        named = node.widgets_values_named
        nodedef = facts.nodedef(node)
        if named is None or nodedef is None:
            continue  # without a definition there is no layout to zip against
        positional = node.widgets_values
        if not isinstance(positional, list):
            if policy.strict:
                out.append(
                    Finding(Code.S6, Severity.ERROR, f"node {node.key} has widgets_values_named but widgets_values is not an array", nodes=(node.key,))
                )
            continue
        problems = _zip_problems(node, named, positional, nodedef)
        if problems:
            out.append(
                Finding(
                    Code.S6,
                    policy.compile_only,
                    f"node {node.key}: widgets_values_named disagrees with widgets_values in {len(problems)} place(s)",
                    nodes=(node.key,),
                    data={"problems": problems},
                )
            )
    return out


def _zip_problems(node: Node, named: dict[str, Any], positional: list[Any], nodedef: NodeDef) -> list[str]:
    layout = widget_layout(nodedef, positional)
    problems = []
    layout_names = set()
    for index, spec in enumerate(layout):
        if spec.name is None:
            continue
        layout_names.add(spec.name)
        if index >= len(positional):
            problems.append(f"{spec.name!r}: widgets_values has no position {index}")
        elif spec.name not in named:
            problems.append(f"{spec.name!r}: missing from widgets_values_named")
        elif not values_equal(named[spec.name], positional[index]):
            problems.append(f"{spec.name!r}: named {named[spec.name]!r} != position {index} {positional[index]!r}")
    for name in sorted(set(named) - layout_names):
        problems.append(f"{name!r}: not a widget of {node.type}")
    if len(positional) > len(layout):
        problems.append(f"widgets_values has {len(positional)} entries, the layout {len(layout)}")
    return problems
