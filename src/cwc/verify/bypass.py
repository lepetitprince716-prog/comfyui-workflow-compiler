"""Bypass closure and optional-group purity, B1-B3 (compile only, always blocking).

* B1: in the factory state every active node has a source for each required socket.
  The factory prompt comes from the frontend's export rules, so a socket counts as fed
  exactly when bypass resolution finds a source for it.
* B2: a node that reaches an output node with everything enabled but not in the factory
  state serves only optional nodes; it would sit dangling in the factory state, so it
  must be optional too.
* B3: optional nodes are bypassed (mode 4), other real nodes are not bypassed or muted,
  and any group holding an optional node holds only optional nodes, nested groups
  included, so ``Ctrl+B`` on the group toggles exactly the optional module.
"""

from __future__ import annotations

from cwc.canvas.model import NodeMode
from cwc.nodedefs.derive import slot_layout
from cwc.normalize.types import Authoring
from cwc.prompt.model import Prompt, is_link
from cwc.report import Code, Finding, Severity
from cwc.verify.equivalence import Exports
from cwc.verify.graph import OFF_MODES, CanvasFacts, prompt_edges, prompt_outputs, reaching, sorted_keys


def check_closure(facts: CanvasFacts, exports: Exports) -> list[Finding]:
    """B1 and B2; nothing to check when an export failed (Q1/Q2 report why)."""
    factory = exports.factory.prompt
    if factory is None:
        return []
    out = _unfed_inputs(facts, factory)
    if exports.full.prompt is not None:
        out += _optional_only(facts, exports.full.prompt, factory)
    return out


def _unfed_inputs(facts: CanvasFacts, factory: Prompt) -> list[Finding]:
    nodes = facts.canvas.node_map()
    out = []
    for key, entry in factory.items():
        nodedef = facts.defs.get(entry["class_type"])
        if nodedef is None:
            continue  # sockets of an unknown type are not known
        inputs = entry.get("inputs", {})
        missing = [
            slot.name
            for slot in slot_layout(nodedef, nodes[key].widgets_values)
            if slot.widget is None and not slot.optional and not is_link(inputs.get(slot.name))
        ]
        if missing:
            out.append(
                Finding(
                    Code.B1,
                    Severity.ERROR,
                    f"node {key} ({entry['class_type']}) has no source for {', '.join(map(repr, missing))} in the factory state",
                    nodes=(key,),
                    hint="a bypassed node upstream has no input of the needed type; make the consumer optional too or reroute",
                    data={"inputs": missing},
                )
            )
    return out


def _optional_only(facts: CanvasFacts, full: Prompt, factory: Prompt) -> list[Finding]:
    alive_full = reaching(prompt_edges(full), prompt_outputs(full, facts.defs))
    alive_factory = reaching(prompt_edges(factory), prompt_outputs(factory, facts.defs))
    out = []
    for key in sorted_keys(factory):
        if key in alive_full and key not in alive_factory:
            consumers = sorted_keys(consumer for producer, consumer in prompt_edges(full) if producer == key)
            out.append(
                Finding(
                    Code.B2,
                    Severity.ERROR,
                    f"node {key} ({factory[key]['class_type']}) serves only optional nodes and is left dangling in the factory state",
                    nodes=(key,),
                    hint="mark it ui_optional together with the module it feeds",
                    data={"consumers": list(consumers)},
                )
            )
    return out


def check_purity(facts: CanvasFacts, authoring: Authoring) -> list[Finding]:
    """B3: node modes against ``ui_optional``, then group purity."""
    optional = {key for key, intent in authoring.intents.items() if intent.ui_optional}
    out = []
    for node in facts.real_nodes():
        if node.key in optional and node.mode != NodeMode.BYPASS:
            out.append(
                Finding(Code.B3, Severity.ERROR, f"optional node {node.key} has mode {node.mode}, not 4 (bypassed)", nodes=(node.key,))
            )
        elif node.key not in optional and node.mode in OFF_MODES:
            out.append(
                Finding(Code.B3, Severity.ERROR, f"node {node.key} is not optional but has mode {node.mode}", nodes=(node.key,))
            )
    for group in facts.groups():
        real = [node for node in group.members if not facts.virtual(node)]
        held = [node.key for node in real if node.key in optional]
        others = [node.key for node in real if node.key not in optional]
        if held and others:
            out.append(
                Finding(
                    Code.B3,
                    Severity.ERROR,
                    f"{group.label()} holds optional nodes {', '.join(held)} and non-optional nodes {', '.join(others)}",
                    nodes=sorted_keys(others),
                    hint="Ctrl+B on the group would toggle both; keep optional modules in groups of their own",
                    data={"group": group.index, "optional": held},
                )
            )
    return out
