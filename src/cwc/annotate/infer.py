"""The inferred plan: compile, and tidy with ``--regroup``."""

from __future__ import annotations

import re
from typing import Mapping

from cwc.annotate.graph import GraphView, group_ranks
from cwc.annotate.notes import group_zones, make_notes, member_order, node_zones
from cwc.annotate.palette import zone_class
from cwc.annotate.rules import Classified, classify, majority_zone
from cwc.annotate.types import Annotations, GroupPlan, NodeNote, PlanCounts, Source, Zone
from cwc.canvas.model import NodeMode
from cwc.normalize.types import Authoring, NodeIntent, WorkflowInfo

ZONE_NAMES = {
    Zone.COCKPIT: "Controls",
    Zone.MODEL: "Models",
    Zone.PROMPT: "Prompts",
    Zone.CONTROL: "Control",
    Zone.SAMPLING: "Sampling",
    Zone.POSTPROCESS: "Post-processing",
    Zone.OUTPUT: "Output",
    Zone.OPTIONAL: "Optional",
    Zone.STAGE: "Stage",
}
_NUMBER_PREFIX = re.compile(r"^\s*(?:step\s*)?\d+[a-z]?(?=[\s.:)_-]|$)[\s.:)_-]*", re.IGNORECASE)
_AUTHOR_KEY = "group:"
_SEPARATORS = re.compile(r"[\s_-]+")
_NUMBER_WORD = re.compile(r"[0-9]+")


def inferred(view: GraphView, authoring: Authoring | None) -> Annotations:
    compile_mode = authoring is not None
    if authoring is not None:
        intents: Mapping[str, NodeIntent] = {key: authoring.intents[key] for key in view.keys if key in authoring.intents}
        optional = frozenset(key for key, intent in intents.items() if intent.ui_optional)
        workflow = authoring.workflow
    else:
        intents = {}
        optional = frozenset(key for key in view.keys if view.nodes[key].mode == NodeMode.BYPASS)
        workflow = WorkflowInfo()
    final = classify(view, optional, intents)
    plain = classify(view, frozenset(), {}) if (intents or optional) else final
    domain = node_zones(plain)
    zones = node_zones(final, domain)
    notes = make_notes(
        view, final, zones, optional, intents, compile_mode=compile_mode, group_zone=group_zones(final, domain)
    )
    groups = _groups(view, final, domain, notes, intents, workflow)
    return Annotations(
        source=Source.INFERRED,
        groups=groups,
        nodes=notes,
        reserve_guide=compile_mode,
        counts=counts(len(view.keys), groups, domain),
    )


def _groups(
    view: GraphView,
    final: Classified,
    domain: Mapping[str, Zone],
    notes: Mapping[str, NodeNote],
    intents: Mapping[str, NodeIntent],
    workflow: WorkflowInfo,
) -> tuple[GroupPlan, ...]:
    members: dict[str, list[str]] = {}
    for key in view.keys:
        members.setdefault(final.claims[key].group, []).append(key)
    zones = group_zones(final, domain)
    ranks = group_ranks(members, [(e.origin, e.target) for e in view.edges], view.order)
    zone_rank = {zone: index for index, zone in enumerate(Zone)}
    plans = []
    for key, keys in members.items():
        zone = zones[key]
        optional = zone is Zone.OPTIONAL
        plans.append(
            GroupPlan(
                key=key,
                zone=zone,
                name=_name(key, zone, keys, domain, intents, workflow),
                members=member_order(view, notes, keys),
                optional=optional,
                colour=zone_class(zone),
                stage=ranks[key],
            )
        )
    first = {plan.key: min(view.order[k] for k in plan.members) for plan in plans}
    plans.sort(key=lambda plan: (plan.stage, zone_rank[plan.zone], first[plan.key], plan.key))
    return tuple(plans)


def _author_group(key: str, zone: Zone, keys: list[str], intents: Mapping[str, NodeIntent]) -> str | None:
    """The author's group id when the author alone names this group, else None.

    A group keyed ``group:<id>`` was built from the author's ``ui_group``; nodes the compiler
    added to it later carry no ``ui_group``, so the key, not the members, says whose name it is.
    An optional module takes the author's name only when all its members name the same group."""
    if key.startswith(_AUTHOR_KEY):
        return key[len(_AUTHOR_KEY) :]
    if zone is Zone.OPTIONAL:
        authored = {intents[k].ui_group for k in keys if k in intents}
        if len(authored) == 1 and None not in authored:
            (group_id,) = authored
            return group_id
    return None


def _id_words(group_id: str) -> str:
    """An author's group id as words: split at whitespace, underscores and hyphens; leading words that
    are only ASCII digits are dropped (the layout numbers the groups), every other word keeps its digits."""
    words = [word for word in _SEPARATORS.split(group_id) if word]
    start = 0
    while start < len(words) and _NUMBER_WORD.fullmatch(words[start]):
        start += 1
    return " ".join(words[start:])


def _name(
    key: str,
    zone: Zone,
    keys: list[str],
    domain: Mapping[str, Zone],
    intents: Mapping[str, NodeIntent],
    workflow: WorkflowInfo,
) -> str:
    """A human name without a number; layout adds the numbers."""
    group_id = _author_group(key, zone, keys, intents)
    if group_id is not None:
        info = workflow.groups.get(group_id)
        if info is not None and info.title:
            cleaned = _NUMBER_PREFIX.sub("", info.title).strip()
            if cleaned:
                return cleaned
        words = _id_words(group_id)
        if words:
            return words[0].upper() + words[1:]
    if zone is Zone.OPTIONAL:
        inner = majority_zone([domain[k] for k in keys])
        if inner is not Zone.OPTIONAL:
            return f"{ZONE_NAMES[Zone.OPTIONAL]} {ZONE_NAMES[inner].lower()}"
    return ZONE_NAMES[zone]


def counts(nodes: int, groups: tuple[GroupPlan, ...], plain: Mapping[str, Zone]) -> PlanCounts:
    """The plan's counts (fields in :class:`PlanCounts`). ``plain``: each node's zone with no
    node optional; an optional group's own zone is the optional zone, so whether it is a
    control branch is read from its members' plain zones."""
    top = [g for g in groups if g.parent is None]
    return PlanCounts(
        nodes=nodes,
        optional_modules=sum(1 for g in top if g.optional),
        control_branches=sum(
            1
            for g in top
            if (majority_zone([plain[k] for k in g.members]) if g.optional else g.zone) is Zone.CONTROL
        ),
    )
