"""What annotate hands to layout: a plan, not geometry.

The plan says which groups exist, how they nest, which zone of a preset each one goes to,
which nodes each one holds and in what order, and how each node should look. It carries
no coordinates. Layout turns it into ``pos``, ``size`` and ``groups``; group numbers
("01", "02a", "Step 3") are assigned by layout from the final order, so the plan names
groups without numbers and numbering stays continuous when modules come and go.

Two sources produce a plan:

* Compile, or tidy with regrouping: annotate infers the groups from the graph (the
  nine first-match rules of ``cwc.annotate.rules``) and the authoring intents.
* Tidy without regrouping: the groups are the canvas's own. Each node is assigned to the
  smallest group whose bounding holds the centre of its box (the frontend's membership
  rule, ``docs/m2-frontend-facts.md`` section 1). Every group keeps its title, colour and
  every other key; layout changes only its ``bounding``.

The invariants listed on :class:`Annotations` are checked when it is built, so code
holding one may rely on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from cwc.normalize.types import Role


class Zone(str, Enum):
    """The semantic area a group belongs to; presets map zones to bands and slots.

    The guide (``00``) is not a zone: it is a reserved rectangle, see
    :attr:`Annotations.reserve_guide`.
    """

    COCKPIT = "cockpit"  # the controls a user changes on every run
    MODEL = "model"  # loaders and the MODEL -> MODEL chain (LoRA, shifts)
    PROMPT = "prompt"  # text encoding not lifted into the cockpit
    CONTROL = "control"  # image, video, mask and control-model branches into conditioning
    SAMPLING = "sampling"  # a sampling stage and its guider, scheduler, sampler pickers
    POSTPROCESS = "postprocess"  # after the first decode, before the outputs
    OUTPUT = "output"  # final outputs and the main preview
    OPTIONAL = "optional"  # an optional module (bypassed in the factory state)
    STAGE = "stage"  # a generic topological stage, for nodes no domain rule claims


class CockpitSlot(str, Enum):
    """Order of controls inside the cockpit: prompts, reference image, size and batch, seed, the rest."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    REFERENCE = "reference"  # the main reference image
    SIZE = "size"  # canvas size and batch
    SEED = "seed"
    OTHER = "other"  # other exposed controls


class Source(str, Enum):
    """Where the plan's groups come from."""

    INFERRED = "inferred"  # compile, or tidy with regrouping: layout writes new groups
    KEPT = "kept"  # tidy without regrouping: the canvas's own groups, only boundings change


COCKPIT_KEY = "cockpit"  # the key of the group annotate makes of the exposed controls


@dataclass(frozen=True)
class GroupPlan:
    """One group of the plan.

    ``key``: stable identifier, unique in the plan, independent of order and numbering (an
    authoring ``ui_group`` id, or a key annotate derives from the members). ``name``: the
    human title without a number; for a kept group, its current title. ``members``: the
    nodes whose innermost group this is, in display order (layout keeps this order within a
    layer). ``parent``: key of the enclosing group, or ``None`` at top level. ``stage``: the
    group's rank in one topological order over all groups of the plan, starting at 0 (a
    group whose members all lie upstream of another group's has the lower rank; equal ranks
    are allowed). C3 does not read it: it checks the written frames and links. Layout ranks
    the placed groups again by the same rule once cut groups are split into segments (with no
    group cut the ranks are these), and a repeat slot opens one instance per distinct rank
    among its groups, in ascending order. ``colour``: a group
    colour class (:func:`cwc.annotate.palette.zone_class` of the zone; layout maps it to a
    colour through the preset's palette); at most seven distinct classes per plan (C6). ``source_index``: for a kept group, its index in the canvas's
    ``groups`` array; ``None`` for an inferred group.
    """

    key: str
    zone: Zone
    name: str
    members: tuple[str, ...]
    parent: str | None = None
    optional: bool = False
    colour: str | None = None
    stage: int = 0
    source_index: int | None = None


@dataclass(frozen=True)
class NodeNote:
    """How one node should look; applied to the canvas before layout in compile mode only
    (tidy never changes these fields).

    ``zone``: the zone the first-match rules give the node on its own, in both sources. A
    ``KEPT`` group takes the zone most of its members have (ties: the earlier zone in
    :class:`Zone` order), and layout places an ungrouped node of a ``KEPT`` plan, without a
    group box, in the slot of this zone. ``collapsed``: the wanted ``flags.collapsed``.
    ``colour``: a node colour class (``positive`` or ``negative``, see
    :mod:`cwc.annotate.palette`), ``None`` for the default. ``title``:
    the cockpit display title, ``None`` to keep the node's own. ``cockpit``: its slot in the
    cockpit order, ``None`` outside the cockpit; ``order`` breaks ties (``ui_order``, then
    node id). ``optional``: the node is bypassed in the factory state (in tidy: its mode is
    bypass).
    """

    zone: Zone = Zone.STAGE
    role: Role = Role.PIPELINE
    collapsed: bool = False
    colour: str | None = None
    title: str | None = None
    cockpit: CockpitSlot | None = None
    order: int | None = None
    optional: bool = False


@dataclass(frozen=True)
class PlanCounts:
    """The numbers ``--preset auto`` reads (``cwc.pipeline.choose_auto``), for both plan sources.

    ``nodes``: every canvas node. ``optional_modules``: optional groups with no parent.
    ``control_branches``: parallel control or mask branches, among groups with no parent only:
    a non-optional group whose zone is the control zone, plus an optional group most of whose
    members have the control zone when no node is optional (the optional rule claims optional
    nodes first, so neither the group's zone nor its nodes' ``NodeNote.zone`` is ever the
    control zone). Chained branches form one group and count as one branch: they are not
    parallel."""

    nodes: int
    optional_modules: int
    control_branches: int


@dataclass(frozen=True)
class Annotations:
    """The plan for one canvas.

    ``groups``: every group, parents before children, siblings in plan order.
    ``nodes``: a note for every canvas node, by node key. ``ungrouped``: node keys that are
    deliberately in no group. ``reserve_guide``: layout reserves the ``00`` guide rectangle in
    column 0 (compile); no node is written for it until M3, so G8's guide part does not apply
    to a canvas without a guide node.

    Invariants, checked at construction:

    * group keys are unique, and a parent is listed before its children (so nesting is a
      forest);
    * every node key in ``nodes`` is in exactly one group's ``members`` or in ``ungrouped``,
      and nothing else is;
    * in an ``INFERRED`` plan, an optional group's parent is ``None`` or optional, children of
      an optional group are optional, and a group's members are optional exactly when the
      group is (the purity rule behind B3). A ``KEPT`` plan reports the canvas as it is;
      impure user groups are B3 findings, not plan errors;
    * every group of a ``KEPT`` plan has a ``source_index``, and an ``INFERRED`` plan has none.
    """

    source: Source
    groups: tuple[GroupPlan, ...]
    nodes: Mapping[str, NodeNote]
    ungrouped: tuple[str, ...] = ()
    reserve_guide: bool = False
    counts: PlanCounts = field(default_factory=lambda: PlanCounts(0, 0, 0))

    def __post_init__(self) -> None:
        seen: dict[str, GroupPlan] = {}
        placed: set[str] = set(self.ungrouped)
        if len(placed) != len(self.ungrouped):
            raise ValueError("a node is listed twice in ungrouped")
        for group in self.groups:
            if group.key in seen:
                raise ValueError(f"group key {group.key!r} is not unique")
            if group.parent is not None:
                parent = seen.get(group.parent)
                if parent is None:
                    raise ValueError(f"group {group.key!r}: parent {group.parent!r} is not listed before it")
                if self.source is Source.INFERRED and group.optional and not parent.optional:
                    raise ValueError(f"optional group {group.key!r} sits inside non-optional {parent.key!r}")
                if self.source is Source.INFERRED and parent.optional and not group.optional:
                    raise ValueError(f"group {group.key!r} inside optional {parent.key!r} must be optional")
            if (group.source_index is None) is (self.source is Source.KEPT):
                raise ValueError(f"group {group.key!r}: source_index does not match a {self.source.value} plan")
            for key in group.members:
                if key in placed:
                    raise ValueError(f"node {key!r} is placed twice")
                note = self.nodes.get(key)
                if note is None:
                    raise ValueError(f"group {group.key!r} holds node {key!r}, which has no note")
                if self.source is Source.INFERRED and note.optional is not group.optional:
                    raise ValueError(f"node {key!r} and group {group.key!r} disagree on optional")
                placed.add(key)
            seen[group.key] = group
        missing = sorted(set(self.nodes) - placed)
        extra = sorted(placed - set(self.nodes))
        if missing or extra:
            raise ValueError(f"nodes not placed: {missing}; placed without a note: {extra}")

    def group(self, key: str) -> GroupPlan:
        for group in self.groups:
            if group.key == key:
                return group
        raise KeyError(key)

    def children(self, key: str | None) -> tuple[GroupPlan, ...]:
        """Groups whose parent is ``key`` (top-level groups for ``None``), in plan order."""
        return tuple(group for group in self.groups if group.parent == key)
