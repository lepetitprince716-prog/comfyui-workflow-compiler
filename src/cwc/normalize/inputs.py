"""One node's inputs: names (E2), omissions and defaults (E3), link types (E4), literal
values (E5) and link targets (E6); produces the strict prompt inputs and widget values.

Strict prompt inputs follow the frontend's export: every persisted widget that enters
the prompt carries a value (an omitted one gets its widget default), linked inputs are
``[numeric id, output index]``, unlinked optional sockets are absent. Key order is the
export's: widgets in widget order, then links in slot order.
"""

from __future__ import annotations

import copy
import difflib
from dataclasses import dataclass, field
from typing import Any

from cwc.nodedefs.derive import slot_layout
from cwc.nodedefs.types import NodeDef, WidgetKind
from cwc.normalize.diagnostics import Findings, listing
from cwc.normalize.ids import RawNode
from cwc.normalize.intents import LOCK_ALL
from cwc.normalize.literals import INVALID, check_dynamic_default, check_literal
from cwc.normalize.refs import Sources, as_ref, resolve
from cwc.normalize.surface import Kind, Surface, Target, surface
from cwc.normalize.types import NodeIntent
from cwc.prompt.bypass import is_valid_connection
from cwc.prompt.dynamic import dynamic_widgets
from cwc.prompt.model import VALUE_WRAPPER, wrap_widget_value
from cwc.report import Code

LIST_HINT = 'a link is ["node_id", output]; a list value is written as {"__value__": [...]}'


@dataclass
class NodeOutput:
    inputs: dict[str, Any]  # strict prompt inputs
    widget_values: dict[str, Any] | None  # None for a node without a definition
    sources: set[str] = field(default_factory=set)  # author ids this node links from


@dataclass
class _Split:
    """The author's inputs sorted by shape (malformed values are already reported)."""

    links: dict[str, Any] = field(default_factory=dict)  # name -> Ref
    literals: dict[str, Any] = field(default_factory=dict)  # name -> unwrapped value


def _split(node: RawNode, out: Findings) -> _Split:
    split = _Split()
    for name, raw in node.inputs.items():
        ref = as_ref(raw)
        if ref is not None:
            split.links[name] = ref
        elif isinstance(raw, list):
            out.error(Code.E5, f"node {node.author_id!r} input {name!r} is a list that is not a link", [node.author_id], LIST_HINT)
        elif isinstance(raw, dict) and VALUE_WRAPPER in raw:
            if set(raw) != {VALUE_WRAPPER} or not isinstance(raw[VALUE_WRAPPER], list):
                out.error(Code.E5, f"node {node.author_id!r} input {name!r} has a malformed {VALUE_WRAPPER} wrapper", [node.author_id], LIST_HINT)
            else:
                split.literals[name] = raw[VALUE_WRAPPER]
        else:
            split.literals[name] = raw
    return split


def check_node(
    node: RawNode,
    nodedef: NodeDef,
    intent: NodeIntent,
    sources: Sources,
    out: Findings,
    *,
    portable: bool = False,
) -> NodeOutput:
    aid = node.author_id
    split = _split(node, out)
    surf = surface(nodedef, split.literals)
    linked: dict[str, list[Any]] = {}
    accepted: dict[str, Any] = {}
    opaque: dict[str, Any] = {}
    result = NodeOutput({}, None)
    meant: set[str] = set()  # omitted inputs a misspelt name stands for: no second error for them

    for name, raw in node.inputs.items():
        target = surf.targets.get(name)
        if target is None:
            if surf.open_group_key(name):
                opaque[name] = _opaque(name, raw, split, sources, aid, result, out)
            else:
                guess = _unknown_name(name, node, nodedef, surf, aid, out)
                if guess is not None:
                    meant.add(guess)
            continue
        if target.kind is Kind.FRONTEND:
            _frontend_only(target, nodedef, aid, out)
            continue
        if name in split.links:
            link = _link(target, split.links[name], sources, aid, out)
            if link is not None:
                linked[name] = link
                result.sources.add(split.links[name].source)
        elif name in split.literals:
            value = _literal(target, split.literals[name], aid, out, portable=portable)
            if value is not INVALID:
                accepted[name] = value

    _check_omitted(node, nodedef, surf, aid, out, meant)
    _check_dynamic_defaults(nodedef, surf, split, aid, out)
    _check_intent_names(intent, surf, aid, out)

    result.inputs = _prompt_inputs(nodedef, surf, split, linked, accepted, opaque)
    result.widget_values = _widget_values(surf, accepted, intent)
    return result


def check_unknown_node(node: RawNode, sources: Sources, out: Findings) -> NodeOutput:
    """A node without a definition (``allow_unknown``): inputs pass as written, links resolved."""
    result = NodeOutput({}, None)
    split = _split(node, out)
    for name, raw in node.inputs.items():
        if name in split.links:
            resolved = resolve(split.links[name], sources, node.author_id, name, out)
            if resolved is not None:
                result.inputs[name] = [resolved.numeric, resolved.index]
                result.sources.add(resolved.source)
        elif name in split.literals:
            result.inputs[name] = copy.deepcopy(raw)
    return result


def _opaque(name: str, raw: Any, split: _Split, sources: Sources, aid: str, result: NodeOutput, out: Findings) -> Any:
    if name in split.links:
        resolved = resolve(split.links[name], sources, aid, name, out)
        if resolved is None:
            return None
        result.sources.add(resolved.source)
        return [resolved.numeric, resolved.index]
    return copy.deepcopy(raw)


def _unknown_name(name: str, node: RawNode, nodedef: NodeDef, surf: Surface, aid: str, out: Findings) -> str | None:
    """E2 for a name the type does not have. Returns the omitted input it most likely stands
    for, if any: the author wrote that input, only misspelt, so it is not reported missing too."""
    omitted = [legal for legal in surf.legal if legal not in node.inputs]
    guesses = difflib.get_close_matches(name, omitted, n=1, cutoff=0.75)
    guess = guesses[0] if guesses else None
    out.error(
        Code.E2,
        f"node {aid!r}: {nodedef.type} has no input {name!r}" + (f"; did you mean {guess!r}?" if guess else ""),
        [aid],
        hint=f"valid inputs: {listing(surf.legal, limit=40)}",
        data={"valid": list(surf.legal), **({"did_you_mean": guess} if guess else {})},
    )
    return guess


def _frontend_only(target: Target, nodedef: NodeDef, aid: str, out: Findings) -> None:
    widget = target.widget
    if widget is not None and widget.kind is WidgetKind.CONTROL:
        hint = 'set it with _meta.ui_seed ("fixed" or "randomize")'
    else:
        hint = "the frontend keeps this widget out of the API prompt; remove it"
    out.error(Code.E2, f"node {aid!r}: {target.name!r} is a frontend widget of {nodedef.type}, not an input", [aid], hint)


def _link(target: Target, ref: Any, sources: Sources, aid: str, out: Findings) -> list[Any] | None:
    if not target.linkable:
        out.error(Code.E4, f"node {aid!r} input {target.name!r} has no input socket and cannot take a link", [aid], "write a literal value instead")
        return None
    resolved = resolve(ref, sources, aid, target.name, out)
    if resolved is None:
        return None
    if resolved.type is not None and not is_valid_connection(resolved.type, target.type):
        out.error(
            Code.E4,
            f"node {aid!r} input {target.name!r} ({target.type}) cannot take output {resolved.index} "
            f"of node {resolved.source!r} ({resolved.type})",
            [aid, resolved.source],
            hint=f"source output type {resolved.type}, target input type {target.type}",
            data={"source_type": resolved.type, "target_type": target.type},
        )
        return None
    return [resolved.numeric, resolved.index]


def _literal(target: Target, value: Any, aid: str, out: Findings, *, portable: bool = False) -> Any:
    if target.kind is Kind.SOCKET:
        out.error(
            Code.E5,
            f"node {aid!r} input {target.name!r} takes a link from a {target.type} output, not a literal value",
            [aid],
            hint='write ["node_id", output]',
        )
        return INVALID
    if target.kind is Kind.UNPERSISTED:
        out.warning(
            Code.E5,
            f"node {aid!r} input {target.name!r}: the frontend does not keep this widget's value; it is dropped",
            [aid],
        )
        return INVALID
    if target.kind is Kind.COMPANION or target.spec is None:
        return copy.deepcopy(value)
    return copy.deepcopy(check_literal(target.spec, value, aid, out, portable=portable))


def _check_omitted(node: RawNode, nodedef: NodeDef, surf: Surface, aid: str, out: Findings, meant: set[str]) -> None:
    for target in surf.targets.values():
        if target.name in node.inputs or target.name in meant:
            continue
        if target.kind is Kind.SOCKET and target.required:
            out.error(
                Code.E3,
                f"node {aid!r}: required input {target.name!r} ({target.type}) is not connected",
                [aid],
                hint=f'link a {target.type} output: "{target.name}": ["node_id", output]',
                data={"type": target.type},
            )
        elif target.kind is Kind.WIDGET:
            spec = target.spec
            assert spec is not None
            if spec.required and not spec.has_default:
                options = list(spec.combo_values or ())
                enum = f"; options: {listing(options)}" if options else ""
                out.error(
                    Code.E3,
                    f"node {aid!r}: required input {target.name!r} has no declared default and must be written",
                    [aid],
                    hint=f"type {spec.type}{enum}",
                    data={"type": spec.type, "options": options} if options else {"type": spec.type},
                )
                continue
        if target.kind in (Kind.WIDGET, Kind.COMPANION) and not nodedef.layout_complete:
            out.error(
                Code.E3,
                f"node {aid!r}: {target.name!r} is omitted, but the widget layout of {nodedef.type} is not "
                "reliable enough to fill its default",
                [aid],
                hint="write every widget input of this node",
            )


def _check_dynamic_defaults(nodedef: NodeDef, surf: Surface, split: _Split, aid: str, out: Findings) -> None:
    """Every dynamic-prompt widget the author gave no literal gets its default written, linked or not."""
    dynamic = dynamic_widgets(nodedef, surf.layout)
    for widget in surf.layout:
        if widget.name in dynamic and widget.name not in split.literals:
            check_dynamic_default(widget.name, widget.default, aid, out, linked=widget.name in split.links)


def _check_intent_names(intent: NodeIntent, surf: Surface, aid: str, out: Findings) -> None:
    known = [w.name for w in surf.layout if w.name is not None]
    known += [name for name in surf.legal if name not in known]
    for key, names in (("ui_locked", intent.ui_locked), ("ui_expose", intent.ui_expose)):
        for name in names:
            if name == LOCK_ALL and key == "ui_locked":
                continue
            if name not in surf.targets and name not in known:
                out.error(Code.E2, f"node {aid!r}: _meta.{key} names {name!r}, which is not a widget of this node", [aid], hint=f"widgets: {listing(known, limit=40)}")
    if intent.ui_seed is not None and not any(w.kind is WidgetKind.CONTROL for w in surf.layout):
        out.error(Code.E7, f"node {aid!r}: _meta.ui_seed is set, but the node has no control_after_generate widget", [aid], hint="remove ui_seed from this node")


def _prompt_inputs(
    nodedef: NodeDef, surf: Surface, split: _Split, linked: dict[str, Any], accepted: dict[str, Any], opaque: dict[str, Any]
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for widget in surf.layout:
        if widget.name is None or not widget.to_prompt:
            continue
        if widget.name in linked:
            inputs[widget.name] = linked[widget.name]
        elif widget.name in accepted:
            inputs[widget.name] = wrap_widget_value(accepted[widget.name])
        else:
            inputs[widget.name] = wrap_widget_value(copy.deepcopy(widget.default))
    order = [slot.name for slot in slot_layout(nodedef, split.literals)] + list(surf.targets)
    for name in order:
        if name in linked and name not in inputs:
            inputs[name] = linked[name]
    for name in sorted(opaque):
        if opaque[name] is not None:
            inputs[name] = opaque[name]
    return inputs


def _widget_values(surf: Surface, accepted: dict[str, Any], intent: NodeIntent) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for widget in surf.layout:
        if widget.name is None:
            continue
        if widget.name in accepted:
            values[widget.name] = copy.deepcopy(accepted[widget.name])
        elif widget.kind is WidgetKind.CONTROL and intent.ui_seed is not None:
            values[widget.name] = intent.ui_seed.value
        else:
            values[widget.name] = copy.deepcopy(widget.default)
    return values
