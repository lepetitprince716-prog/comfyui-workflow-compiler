"""Link references in authoring inputs, and cycle detection over the links.

A link is a two-element list ``[source id, output]``: the source is an authoring id
string, the output an index or an output name (matched by name, then by type).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cwc.nodedefs.types import NodeDef
from cwc.normalize.diagnostics import Findings, listing
from cwc.report import Code


@dataclass(frozen=True)
class Ref:
    source: str
    output: int | str


@dataclass(frozen=True)
class Resolved:
    source: str  # author id
    numeric: str  # numeric prompt id of the source
    index: int
    type: str | None  # None when the source type is unknown (allow_unknown)


def as_ref(value: Any) -> Ref | None:
    """The link this value spells, or None when it is not link-shaped."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
        output = value[1]
        if isinstance(output, str) or (isinstance(output, int) and not isinstance(output, bool)):
            return Ref(value[0], output)
    return None


@dataclass(frozen=True)
class Sources:
    """What link resolution needs: author id -> numeric id, and -> definition (None: unknown)."""

    ids: Mapping[str, str]
    defs: Mapping[str, NodeDef | None]


def resolve(ref: Ref, sources: Sources, consumer: str, input_name: str, out: Findings) -> Resolved | None:
    where = f"node {consumer!r} input {input_name!r}"
    if ref.source not in sources.defs:
        out.error(
            Code.E6,
            f"{where} links to node {ref.source!r}, which does not exist",
            [consumer],
            hint=f"existing nodes: {listing(sorted(sources.defs))}",
        )
        return None
    numeric = sources.ids[ref.source]
    nodedef = sources.defs[ref.source]
    if nodedef is None:
        if isinstance(ref.output, str):
            out.error(
                Code.E6,
                f"{where} names output {ref.output!r} of node {ref.source!r}, whose type is unknown",
                [consumer, ref.source],
                hint="outputs of a node without a definition can only be referenced by index",
            )
            return None
        if ref.output < 0:
            out.error(Code.E6, f"{where} uses output index {ref.output}", [consumer], hint="output indexes start at 0")
            return None
        return Resolved(ref.source, numeric, ref.output, None)
    outputs = [f"{i} {o.name} ({o.type})" for i, o in enumerate(nodedef.outputs)]
    available = {"outputs": [{"index": i, "name": o.name, "type": o.type} for i, o in enumerate(nodedef.outputs)]}
    hint = f"outputs of {nodedef.type}: {', '.join(outputs) or 'none'}"
    if isinstance(ref.output, int):
        if not 0 <= ref.output < len(nodedef.outputs):
            out.error(Code.E6, f"{where} uses output {ref.output} of node {ref.source!r}, which has no such output", [consumer, ref.source], hint, available)
            return None
        index = ref.output
    else:
        matches = nodedef.output_index(ref.output)
        if not matches:
            out.error(Code.E6, f"{where} names output {ref.output!r} of node {ref.source!r}, which has no such output", [consumer, ref.source], hint, available)
            return None
        if len(matches) > 1:
            out.error(
                Code.E6,
                f"{where}: output name {ref.output!r} of node {ref.source!r} is ambiguous (indexes {', '.join(map(str, matches))})",
                [consumer, ref.source],
                f"write the output index instead; {hint}",
                available,
            )
            return None
        index = matches[0]
    return Resolved(ref.source, numeric, index, nodedef.outputs[index].type)


def find_cycles(edges: Mapping[str, set[str]]) -> list[list[str]]:
    """Directed cycles (consumer -> source), each as the node chain; deterministic order.

    One cycle is reported per back edge met in a depth-first walk over sorted ids.
    """
    state: dict[str, int] = {}  # 1 on the stack, 2 done
    cycles: list[list[str]] = []
    for root in sorted(edges):
        if root in state:
            continue
        stack: list[tuple[str, list[str]]] = [(root, sorted(edges.get(root, ())))]
        path = [root]
        state[root] = 1
        while stack:
            node, pending = stack[-1]
            if not pending:
                stack.pop()
                path.pop()
                state[node] = 2
                continue
            nxt = pending.pop(0)
            mark = state.get(nxt)
            if mark == 1:
                cycles.append(path[path.index(nxt) :] + [nxt])
            elif mark is None:
                state[nxt] = 1
                path.append(nxt)
                stack.append((nxt, sorted(edges.get(nxt, ()))))
    return cycles
