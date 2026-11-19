"""Node notes from the rules' claims, shared by both plan sources."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import Collection, Mapping, Sequence

from cwc.annotate.graph import GraphView
from cwc.annotate.rules import POSITIVE, Classified, latest_zone, majority_zone
from cwc.annotate.types import COCKPIT_KEY, CockpitSlot, NodeNote, Zone
from cwc.canvas.envelope import title_text
from cwc.nodedefs.types import NodeDef
from cwc.normalize.types import NodeIntent, Role

PROMPT_TITLES = {CockpitSlot.POSITIVE: "Positive prompt", CockpitSlot.NEGATIVE: "Negative prompt"}


def own_zones(classified: Classified, domain: Mapping[str, Zone]) -> dict[str, Zone]:
    """The zone each node has on its own, as the group rule reads it: ``domain`` (the zones the
    rules give without intents), except that an output node an author grouped (``outputs``)
    is in the output zone."""
    return {**domain, **dict.fromkeys(classified.outputs, Zone.OUTPUT)}


def group_zones(classified: Classified, domain: Mapping[str, Zone]) -> dict[str, Zone]:
    """The zone of every group the claims name. A group whose claims carry no zone (an
    explicit ``ui_group``) takes :func:`members_zone` over its members' own zones
    (:func:`own_zones` of ``domain``)."""
    domain = own_zones(classified, domain)
    fixed: dict[str, Zone] = {}
    open_members: dict[str, list[str]] = {}
    for key, claim in classified.claims.items():
        if claim.zone is not None:
            fixed.setdefault(claim.group, claim.zone)
        else:
            open_members.setdefault(claim.group, []).append(key)
    for group, keys in open_members.items():
        if group in fixed:
            continue
        fixed[group] = members_zone(keys, classified.plumbing, domain)
    return fixed


def members_zone(keys: Sequence[str], plumbing: Collection[str], domain: Mapping[str, Zone]) -> Zone:
    """The zone of an explicit group with these members, from their zones on their own
    (``domain``). The group is a cockpit frame when the majority zone of its members, plumbing
    left out unless it is all they are, is the cockpit (a tie with the cockpit counts). Any
    other group takes the latest zone of its steering members (:func:`steering_members`,
    :func:`cwc.annotate.rules.latest_zone`): a group drawn around outputs from several stages
    goes where its last member goes, not where most of its members would go on their own.
    Layout gives each segment of a cut group its zone by the second rule, over the segment's
    members."""
    pipeline = [key for key in keys if key not in plumbing] or list(keys)
    if majority_zone([domain[key] for key in pipeline]) is Zone.COCKPIT:
        return Zone.COCKPIT
    return latest_zone([domain[key] for key in steering_members(keys, plumbing, domain)])


def steering_members(keys: Sequence[str], plumbing: Collection[str], domain: Mapping[str, Zone]) -> list[str]:
    """The members that set the zone of a group or a segment: those whose zone on their own
    (``domain``) is not the cockpit, unless that is all of them; of these, those that are not
    plumbing, unless that is all of them. Cockpit members are knobs at the head of the data
    flow and plumbing follows its consumer, so neither tells where the work of a group lies."""
    steered = [key for key in keys if domain[key] is not Zone.COCKPIT] or list(keys)
    return [key for key in steered if key not in plumbing] or steered


def node_zones(classified: Classified, domain: Mapping[str, Zone] | None = None) -> dict[str, Zone]:
    """Each node's own first-match zone. A node in an explicit group has no zone of its own
    from its claim, so it takes the zone the rules give it without intents (``domain``), or the
    output zone for an author-grouped output (:func:`own_zones`). This holds for the members
    the author wrote in (rule 2) and the ones rule 3 adds to the group alike (an intermediate
    preview, its decode, a side branch): the group's zone is derived from these own zones, so
    a member never takes its own zone from the group, which would make a node of an earlier
    stage read as a member of the group's latest zone."""
    zones: dict[str, Zone] = {}
    pending: list[str] = []
    own = own_zones(classified, domain or {})
    for key, claim in classified.claims.items():
        if claim.zone is not None:
            zones[key] = claim.zone
        elif key in classified.outputs or domain is not None:
            zones[key] = own[key]
        else:
            pending.append(key)
    if pending:
        by_group = group_zones(classified, {**zones, **(domain or {})})
        for key in pending:
            zones[key] = by_group[classified.claims[key].group]
    return zones


def make_notes(
    view: GraphView,
    classified: Classified,
    zones: Mapping[str, Zone],
    optional: frozenset[str],
    intents: Mapping[str, NodeIntent],
    *,
    compile_mode: bool,
    group_zone: Mapping[str, Zone] | None = None,
) -> dict[str, NodeNote]:
    """One note per node. Compile writes collapsed plumbing, prompt colours and cockpit
    titles (:func:`cockpit_titles`; ``group_zone`` is each group's zone, as
    :func:`group_zones` gives it, and compile needs it); tidy reports the node as it is (its
    own collapsed flag, no colour or title), because a tidy run changes only geometry."""
    if compile_mode and group_zone is None:
        raise ValueError("compile needs the group zones for the cockpit titles")
    notes: dict[str, NodeNote] = {}
    for key in view.keys:
        claim = classified.claims[key]
        intent = intents.get(key, NodeIntent())
        cockpit = claim.group == COCKPIT_KEY
        plumbing = key in classified.plumbing and not cockpit
        role = Role.COCKPIT if cockpit else Role.PLUMBING if plumbing else Role.PIPELINE
        slot = (claim.cockpit or CockpitSlot.OTHER) if cockpit else None
        node = view.nodes[key]
        if compile_mode:
            collapsed, colour = plumbing, classified.polarity.get(key)
        else:
            collapsed, colour = node.collapsed, None
        notes[key] = NodeNote(
            zone=zones[key],
            role=role,
            collapsed=collapsed,
            colour=colour,
            cockpit=slot,
            order=intent.ui_order,
            optional=key in optional,
        )
    if compile_mode:
        assert group_zone is not None
        panel = [key for key in view.keys if group_zone[classified.claims[key].group] is Zone.COCKPIT]
        authored = {key for key in panel if view.nodes[key].title is not None or intents.get(key, NodeIntent()).title is not None}
        for key, title in cockpit_titles(view, classified, notes, panel, authored).items():
            notes[key] = replace(notes[key], title=title)
    return notes


# Word boundaries inside a type name: a lower-case letter before a capital (``yL``), the
# last capital of an acronym before a capitalised word (``SDTurbo`` splits before ``Tu``),
# and a digit before a capitalised word (``3L``). A digit after a letter stays with it
# (``SD3``, ``Flux2``), which keeps model names whole.
_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[0-9])(?=[A-Z][a-z])")
_SEPARATORS = re.compile(r"[\s_]+")


def type_words(type_name: str) -> str:
    """``type_name`` split into words at its case and digit boundaries and underscores
    (``EmptySD3LatentImage`` gives ``Empty SD3 Latent Image``). A name with no boundary
    would read as itself, the type name C4 forbids, so it gets the word ``node``."""
    words = " ".join(part for part in _SEPARATORS.split(_BOUNDARY.sub(" ", type_name)) if part)
    return words if words != type_name else f"{words} node"


def readable_name(type_name: str, nodedef: NodeDef | None) -> str:
    """The definition's display name, or (none, or one equal to the type) the type split
    into words: never the type name itself."""
    shown = nodedef.display_name if nodedef is not None else ""
    return shown if shown and shown != type_name else type_words(type_name)


def base_title(view: GraphView, classified: Classified, key: str) -> str:
    """The title a cockpit node gets before repeats are settled: the prompt title for a
    conditioning node of one polarity, else :func:`readable_name`."""
    polarity = classified.polarity.get(key)
    if polarity is not None:
        return PROMPT_TITLES[CockpitSlot.POSITIVE if polarity == POSITIVE else CockpitSlot.NEGATIVE]
    return readable_name(view.nodes[key].type, view.defs[key])


def cockpit_titles(
    view: GraphView, classified: Classified, notes: Mapping[str, NodeNote], panel: list[str], authored: set[str]
) -> dict[str, str]:
    """Titles for the cockpit panel's nodes (``panel``: every node whose group lies in the
    cockpit zone; C4 reads all cockpit groups as one panel; ``authored``: those of them
    whose title the author wrote, on the canvas or in the intent), so that none is drawn under its
    type name and no two are drawn alike. An authored title stays as it is, even when two
    collide (C4 reports them). Every other node starts from :func:`base_title`; a repeated
    one, or one equal to an authored title, gives way: a non-prompt node first adds the
    distinct input names it feeds, sorted (``Int (width)``, ``Int (height, width)``); what still repeats, and
    every repeated prompt, is numbered in display order (``Positive prompt 1``), skipping
    numbers taken by any other title. Only a title that differs from what the frontend would
    draw anyway (the display name) is returned, so a node already clear keeps its canvas."""
    fixed = {title_text(view.nodes[key], view.defs[key]) for key in authored}
    shown = [key for key in member_order(view, notes, panel) if key not in authored]
    names = {key: base_title(view, classified, key) for key in shown}

    def clashing() -> list[str]:
        counts = Counter(names.values())
        return [key for key in shown if counts[names[key]] > 1 or names[key] in fixed]

    for key in clashing():
        if key in classified.polarity:
            continue
        # Sorted, so two nodes feeding the same inputs read alike and are numbered.
        ports = sorted({edge.input_name for edge in view.outgoing[key] if edge.input_name})
        if ports:
            names[key] = f"{names[key]} ({', '.join(ports)})"
    numbered = clashing()
    taken = fixed | {names[key] for key in shown if key not in numbered}
    stems = {key: names[key] for key in numbered}
    for key in numbered:
        number = 1
        while f"{stems[key]} {number}" in taken:
            number += 1
        names[key] = f"{stems[key]} {number}"
        taken.add(names[key])
    out: dict[str, str] = {}
    for key in shown:
        if names[key] != title_text(view.nodes[key], view.defs[key]):
            out[key] = names[key]
    return out


def member_order(view: GraphView, notes: Mapping[str, NodeNote], keys: list[str]) -> tuple[str, ...]:
    """Display order: the cockpit's human-factors order (slot, ``ui_order``, id), and
    topological order with ids breaking ties everywhere else."""
    slots = list(CockpitSlot)

    def sort_key(key: str) -> tuple:
        note = notes[key]
        if note.cockpit is not None:
            order = note.order if note.order is not None else 1 << 30
            return (0, slots.index(note.cockpit), order, view.order[key])
        return (1, 0, 0, view.order[key])

    return tuple(sorted(keys, key=sort_key))

