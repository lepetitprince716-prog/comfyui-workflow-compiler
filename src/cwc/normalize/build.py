"""``normalize``: an authoring document to two strict prompts, ids, intents and widget values."""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from cwc.nodedefs.types import NodeDef
from cwc.normalize.diagnostics import Findings, listing, no_effect_notice
from cwc.normalize.ids import RawNode, assign_ids, split_document
from cwc.normalize.inputs import NodeOutput, check_node, check_unknown_node
from cwc.normalize.intents import check_group_purity, parse_intent
from cwc.normalize.refs import Sources, find_cycles
from cwc.normalize.types import Authoring, NodeIntent
from cwc.normalize.workflow_info import parse_workflow
from cwc.prompt.bypass import ResolveError, factory_prompt
from cwc.prompt.model import Prompt, sort_prompt
from cwc.report import Code, CwcError, ExitCode, Finding, Severity, sort_findings

# How to give cwc a definition it lacks. ``--nodedefs`` entries are added to the bundled core
# definitions (``NodeDefs.load``), so a file holding only the missing types is enough.
NODEDEFS_HINT = (
    "pass --nodedefs with the URL of a ComfyUI server that has the node pack installed, or with a JSON "
    "file saved from <server>/api/object_info (the entries for the missing types are enough)"
)
UNKNOWN_HINT = (
    "if the type name is right, either " + NODEDEFS_HINT[0].lower() + NODEDEFS_HINT[1:]
    + ", or pass --allow-unknown to keep its inputs unchecked (link to its outputs by index then)"
)
OPTIONAL_UNKNOWN_HINT = "--allow-unknown does not cover this; " + NODEDEFS_HINT


class NodeDefSource(Protocol):
    """The two registry methods normalize uses."""

    def get(self, type: str) -> NodeDef | None: ...

    def closest(self, type: str, n: int = 3) -> Sequence[str]: ...


def normalize(
    doc: dict[str, Any],
    defs: NodeDefSource,
    *,
    allow_unknown: bool = False,
    portable: bool = False,
) -> tuple[Authoring, list[Finding]]:
    """Check an authoring document (E1 to E8) and build its strict prompts.

    Raises ``CwcError(INPUT_ERROR, findings)`` when any finding is an error; otherwise
    returns the result with the warnings, sorted. The E8 info on keys without effect rides on
    ``Authoring.notices`` and is left out of a failed run.
    """
    out = Findings()
    raw_workflow, nodes = split_document(doc, out)
    workflow = parse_workflow(raw_workflow, out)
    ids = assign_ids([node.author_id for node in nodes])
    intents = {node.author_id: parse_intent(node.author_id, node.meta, out) for node in nodes}
    resolved = {node.author_id: _definition(node, defs, allow_unknown, out) for node in nodes}

    # E1 errors: no definition and no --allow-unknown, or a frontend-only (virtual) type
    rejected = frozenset(
        node.author_id
        for node in nodes
        if resolved[node.author_id] is None and (not allow_unknown or defs.get(node.class_type) is not None)
    )
    sources = Sources(ids, resolved, rejected)
    outputs: dict[str, NodeOutput] = {}
    for node in nodes:
        nodedef = resolved[node.author_id]
        if nodedef is None:
            outputs[node.author_id] = check_unknown_node(node, sources, out)
        else:
            outputs[node.author_id] = check_node(node, nodedef, intents[node.author_id], sources, out, portable=portable)

    for cycle in find_cycles({aid: result.sources for aid, result in outputs.items()}):
        out.error(
            Code.E7,
            f"links form a cycle: {' <- '.join(cycle)}",
            sorted(set(cycle)),
            hint="each arrow points from a node to the node it takes input from; break one of these links",
            data={"cycle": cycle},
        )
    check_group_purity(intents, out)
    _check_unknown_bypass(nodes, resolved, intents, outputs, out)
    notices = no_effect_notice(out.unused)
    if out.has_errors:
        raise CwcError(ExitCode.INPUT_ERROR, sort_findings(out.items))

    full: Prompt = sort_prompt(
        {
            ids[node.author_id]: {
                "inputs": outputs[node.author_id].inputs,
                "class_type": node.class_type,
                "_meta": {"title": _title(node, resolved[node.author_id], intents[node.author_id])},
            }
            for node in nodes
        }
    )
    optional_ids = sorted((ids[aid] for aid, intent in intents.items() if intent.ui_optional), key=int)
    try:
        factory = factory_prompt(full, defs, optional_ids)
    except (ResolveError, ValueError) as exc:
        raise CwcError(
            ExitCode.INPUT_ERROR,
            sort_findings([*out.items, Finding(Code.E7, Severity.ERROR, f"factory state cannot be computed: {exc}", hint="check the links around the ui_optional nodes")]),
        ) from exc

    by_numeric = sorted(nodes, key=lambda node: int(ids[node.author_id]))
    authoring = Authoring(
        full=full,
        factory=factory,
        ids={aid: ids[aid] for aid in sorted(ids, key=lambda a: int(ids[a]))},
        defs={ids[n.author_id]: d for n in by_numeric if (d := resolved[n.author_id]) is not None},
        intents={ids[n.author_id]: intents[n.author_id] for n in by_numeric},
        workflow=workflow,
        widget_values={
            ids[n.author_id]: values for n in by_numeric if (values := outputs[n.author_id].widget_values) is not None
        },
        notices=notices,
    )
    return authoring, sort_findings(out.items)


def _definition(node: RawNode, defs: NodeDefSource, allow_unknown: bool, out: Findings) -> NodeDef | None:
    nodedef = defs.get(node.class_type)
    aid = node.author_id
    if nodedef is not None and nodedef.virtual:
        out.error(
            Code.E1,
            f"node {aid!r}: {node.class_type} is a frontend-only node and cannot appear in an API prompt",
            [aid],
            hint="notes and the guide are added by the compiler; remove this node",
        )
        return None
    if nodedef is not None:
        return nodedef
    closest = list(defs.closest(node.class_type, 3))
    hint = (f"closest known types: {listing(closest)}; " if closest else "") + UNKNOWN_HINT
    data = {"closest": closest}
    if allow_unknown:
        out.warning(
            Code.E1,
            f"node {aid!r}: unknown type {node.class_type!r} passes unchecked (allow_unknown); "
            "its widget values are written in authoring order and a frontend that knows the type "
            "reads them by position, so its exported values are not verified and may be shifted",
            [aid],
            hint="write every widget input of this node, in the order the node shows its widgets in ComfyUI, and "
            "link to its outputs by index, as [\"node_id\", 0]; to have it checked instead, " + NODEDEFS_HINT,
            data=data,
        )
    else:
        out.error(Code.E1, f"node {aid!r}: unknown type {node.class_type!r}", [aid], hint, data)
    return None


def _check_unknown_bypass(
    nodes: list[RawNode],
    resolved: dict[str, NodeDef | None],
    intents: dict[str, NodeIntent],
    outputs: dict[str, NodeOutput],
    out: Findings,
) -> None:
    """The factory state re-resolves links through optional nodes, which needs both ends' sockets."""
    for node in nodes:
        aid = node.author_id
        if resolved[aid] is not None:
            continue
        if intents[aid].ui_optional:
            out.error(Code.E1, f"node {aid!r}: a node of unknown type {node.class_type!r} cannot be ui_optional", [aid], OPTIONAL_UNKNOWN_HINT)
        feeding = sorted(src for src in outputs[aid].sources if intents[src].ui_optional)
        if feeding:
            out.error(
                Code.E1,
                f"node {aid!r}: a node of unknown type {node.class_type!r} cannot take links from optional nodes ({', '.join(feeding)})",
                [aid, *feeding],
                OPTIONAL_UNKNOWN_HINT,
            )


def _title(node: RawNode, nodedef: NodeDef | None, intent: NodeIntent) -> str:
    if intent.title is not None:
        return intent.title
    if nodedef is not None:
        return nodedef.display_name or nodedef.type
    return node.class_type
