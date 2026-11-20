"""The nine first-match rules: each node's zone and group, from slot types and link directions.

A rule claims only nodes no earlier rule claimed. Rules 5 to 8 run before rule 4 is
decided, because rule 4 (a cockpit shared source) asks which groups a source feeds; their
claims are committed only for the nodes rule 4 leaves. Node class names never appear:
every test reads socket types, input names, link directions, ``output_node``, a frontend
node's builtin behaviour, and the intents.

Rules, in order:

1. optional nodes, one group per connected optional sub-chain naming one ``ui_group``;
2. explicit ``ui_group`` and ``ui_role`` intents (cockpit, and plumbing, which is then
   resolved with rule 9);
3. output nodes at the end of the graph (a preview beside a sampler that a later sampler
   follows stays with that sampler; one the author put in a group stays in that group, and
   the nodes between the sampler and it still join the sampler);
4. cockpit shared sources: no input link, widgets, and no model-asset output, feeding two
   or more groups directly or through plumbing; and a latent source feeding a sampler,
   directly or through latent-only steps (the canvas size and batch);
5. sampler anchors (a ``LATENT`` output and a ``MODEL``, ``GUIDER``, ``SAMPLER`` or
   ``SIGMAS`` input) with the guider, scheduler, sampler-select and noise nodes feeding
   them directly, one group per anchor;
6. the model stack: up the ``MODEL`` inputs of the sampling groups through ``MODEL`` to
   ``MODEL`` nodes, plus nodes that only turn model assets into model assets (a value
   output beside the assets, such as a text one, does not count against it);
7. control branches: a node turning an image, video, mask or control-model input into
   conditioning that reaches a sampler, with what feeds those inputs; a source that also
   feeds the base latent goes to the cockpit;
8. prompts (conditioning without a control input; polarity from the input names
   downstream; the main positive and negative go to the cockpit when no control branch
   exists; a node downstream of a decode whose conditioning reaches no sampler or guider
   is left to post-processing, since its input comes from there) and post-processing
   (from a decode to the outputs, feeding no sampler);
9. plumbing: small value-conversion and pass-through nodes, collapsed, in the group of
   their earliest non-optional consumer.

What no rule claims becomes a generic stage group by longest-path depth.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Mapping

from cwc.annotate.graph import ANY, COMBO, GraphView
from cwc.annotate.types import AUTHOR_GROUP_PREFIX, COCKPIT_KEY, CockpitSlot, Zone
from cwc.canvas.model import id_order
from cwc.nodedefs.types import Behavior
from cwc.normalize.types import NodeIntent, Role

VALUE_TYPES = frozenset({"INT", "FLOAT", "BOOLEAN", "STRING", "NUMBER", COMBO, ANY})
ASSET_TYPES = frozenset({"MODEL", "CLIP", "VAE"})
CONTROL_TYPES = frozenset(
    {"IMAGE", "VIDEO", "MASK", "CONTROL_NET", "CLIP_VISION", "CLIP_VISION_OUTPUT", "STYLE_MODEL", "GLIGEN"}
)
ANCHOR_INPUTS = frozenset({"MODEL", "GUIDER", "SAMPLER", "SIGMAS"})
ADJACENT_TYPES = frozenset({"GUIDER", "SAMPLER", "SIGMAS", "NOISE"})
LATENT = "LATENT"
SAMPLES = "SAMPLES"
MODEL = "MODEL"
GUIDER = "GUIDER"
CONDITIONING = "CONDITIONING"
PIXELS = frozenset({"IMAGE", "VIDEO"})
POSITIVE = "positive"
NEGATIVE = "negative"
SEED_WORD = "seed"
SIZE_WORDS = frozenset({"width", "height", "batch_size", "batch", "length"})
IMAGE = "IMAGE"

OUTPUT_KEY = "output"
MODEL_KEY = "model"
PROMPT_KEY = "prompt"


MODEL_ASSETS = ASSET_TYPES | frozenset({"CONTROL_NET", "CLIP_VISION", "STYLE_MODEL", "GLIGEN"})
MODEL_SUFFIX = "_MODEL"


def model_asset(types: frozenset[str]) -> bool:
    """A loaded model of any kind: a core asset, a control model or any ``*_MODEL`` socket."""
    return any(t in MODEL_ASSETS or t.endswith(MODEL_SUFFIX) for t in types)


@dataclass(frozen=True)
class Claim:
    rule: int
    group: str
    zone: Zone | None  # None: the group's zone (explicit groups and plumbing)
    cockpit: CockpitSlot | None = None


@dataclass
class Classified:
    claims: dict[str, Claim] = field(default_factory=dict)
    plumbing: set[str] = field(default_factory=set)
    polarity: dict[str, str] = field(default_factory=dict)
    anchors: tuple[str, ...] = ()
    # Output nodes an author put in a group that rule 3 did not keep beside a sampler: their own
    # zone is the output zone, whatever the classification without intents says.
    outputs: frozenset[str] = frozenset()


class _Rules:
    def __init__(self, view: GraphView, optional: frozenset[str], intents: Mapping[str, NodeIntent]):
        self.view = view
        self.optional = optional
        self.intents = intents
        self.claims: dict[str, Claim] = {}
        self.reserved: set[str] = set()  # explicit plumbing, resolved with rule 9
        self.previews: set[str] = set()  # intermediate previews, kept beside their sampler
        self.outputs: set[str] = set()  # author-grouped outputs left to the output zone
        self.no_lift = {key for key, intent in intents.items() if intent.ui_role is Role.PIPELINE}
        self.anchors = tuple(view.topo_sorted(key for key in view.keys if self._anchor(key)))
        self.guiders = frozenset(key for key in view.keys if GUIDER in view.out_types(key))
        self.polarity = polarities(view, set(self.anchors) | self.guiders)

    # Shape tests.

    def _anchor(self, key: str) -> bool:
        view = self.view
        latent_out = LATENT in view.out_types(key) or SAMPLES in view.out_names(key)
        return latent_out and bool(view.in_types(key) & ANCHOR_INPUTS)

    def plumbing_shape(self, key: str) -> bool:
        view = self.view
        if view.behavior(key) is Behavior.PASSTHROUGH:
            return True
        outs = view.out_types(key)
        return bool(outs) and outs <= VALUE_TYPES and view.in_types(key) <= VALUE_TYPES

    def free(self, key: str) -> bool:
        return key not in self.claims and key not in self.reserved

    def claim(self, key: str, rule: int, group: str, zone: Zone | None, slot: CockpitSlot | None = None) -> None:
        if self.free(key):
            self.claims[key] = Claim(rule, group, zone, slot)

    # The waterfall.

    def run(self) -> Classified:
        self.rule1_optional()
        self.rule2_explicit()
        self.rule3_outputs()
        provisional = copy.copy(self)
        provisional.claims = dict(self.claims)
        provisional.reserved = set(self.reserved)
        provisional.rules5to8()
        self.rule4_cockpit_sources(provisional.claims)
        self.rules5to8()
        plumbing = self.rule9_plumbing()
        for key in self.view.keys:
            self.claim(key, 10, f"stage:{self.view.depth[key]}", Zone.STAGE)
        return Classified(self.claims, plumbing, self.polarity, self.anchors, frozenset(self.outputs))

    def rules5to8(self) -> None:
        self.rule5_sampling()
        self.rule6_model()
        self.rule7_control()
        self.rule8_prompts()
        self.rule8_postprocess()

    def rule1_optional(self) -> None:
        view = self.view

        def author_group(key: str) -> str | None:
            intent = self.intents.get(key)
            return intent.ui_group if intent is not None else None

        # Two linked optional nodes share a module only when they name the same ``ui_group``
        # (none counts as the same): each named group stays its own module and keeps its title.
        for component in components(self.optional, view, same=lambda a, b: author_group(a) == author_group(b)):
            for key in component:
                self.claim(key, 1, f"optional:{component[0]}", Zone.OPTIONAL)

    def rule2_explicit(self) -> None:
        for key in self.view.keys:
            intent = self.intents.get(key)
            if intent is None or not self.free(key):
                continue
            if intent.ui_role is Role.COCKPIT:
                self.claim(key, 2, COCKPIT_KEY, Zone.COCKPIT, self.cockpit_slot(key))
            elif intent.ui_group is not None:
                self.claim(key, 2, f"{AUTHOR_GROUP_PREFIX}{intent.ui_group}", None)
            elif intent.ui_role is Role.PLUMBING:
                self.reserved.add(key)

    def rule3_outputs(self) -> None:
        """Final outputs to the output zone; an intermediate preview (one a later sampler
        follows) to its sampler's group, together with the free chain from that sampler to
        the preview (the decode), also when that chain feeds a final output too. Keeping
        the chain with the preview keeps the group graph acyclic: a chain node left to
        post-processing would sit downstream of the sampling group and feed the preview in
        it, so the two groups would loop. A chain node that feeds a later sampler stays
        out: the later stages are downstream of it, and the free nodes between the two
        samplers take their own groups.

        A side branch feeding such a group (a free, non-plumbing node with a path into the
        preview or its chain that passes no sampler, not itself downstream of the preview's
        sampler) joins its own nearest upstream sampler's group. Left to post-processing it
        could share a group with the final outputs, which sit downstream of the preview's
        group, and the two groups would loop. Its nearest upstream sampler is earlier than
        the preview's (a chain node has no later sampler upstream), so its links all run
        forward.

        An intermediate preview the author put in a group (rule 2) stays in that group,
        while its chain and the side branches feeding it are handled as for a free preview:
        left to post-processing, the decode would sit to the right of the sampling slot the
        author's group takes and feed the preview from there. Free previews go first, so a
        chain shared with an author-grouped preview ends as it would without that preview.
        An author-grouped final output is left as it is."""
        view = self.view
        anchors = set(self.anchors)
        beside: list[tuple[str, str, str, Zone | None]] = []  # preview, anchor, group, zone
        grouped: list[tuple[str, str, str, Zone | None]] = []  # the same, previews in an author group
        for key in view.keys:
            if not view.output_node(key) or view.outgoing[key]:
                continue
            if self.free(key):
                owner = self._beside(key, anchors)
                if owner is None:
                    self.claim(key, 3, OUTPUT_KEY, Zone.OUTPUT)
                else:
                    beside.append((key, *owner))
            elif self._author_grouped(key):
                owner = self._beside(key, anchors)
                if owner is None:
                    self.outputs.add(key)
                else:
                    grouped.append((key, *owner))
        side: set[str] = set()
        for key, anchor, group, zone in beside + grouped:
            self.claim(key, 3, group, zone)  # a no-op for an author-grouped preview
            self.previews.add(key)
            chain = (view.downstream([anchor]) & view.upstream([key])) - {anchor, key}
            kept = {key}
            for node in view.topo_sorted(chain):
                if self.free(node) and not self.plumbing_shape(node) and not (view.downstream([node]) & anchors):
                    self.claim(node, 3, group, zone)
                    kept.add(node)
            side |= self._feeding(kept, anchors) - view.downstream([anchor])
        for node in view.topo_sorted(side):
            if not self.free(node) or self.plumbing_shape(node):
                continue
            nearest = self._nearest(node, anchors)
            owner = self._sampler_group(nearest) if nearest is not None else None
            if owner is not None:
                self.claim(node, 3, *owner)

    def _author_grouped(self, key: str) -> bool:
        """Claimed by rule 2 into an author's named group (not the cockpit)."""
        owner = self.claims.get(key)
        return owner is not None and owner.rule == 2 and owner.group != COCKPIT_KEY

    def _feeding(self, keys: set[str], anchors: set[str]) -> set[str]:
        """Every node with a path into ``keys`` that passes no sampler (``keys`` excluded)."""
        view = self.view
        seen: set[str] = set()
        stack = list(keys)
        while stack:
            node = stack.pop()
            for edge in view.incoming[node]:
                origin = edge.origin
                if origin not in seen and origin not in keys and origin not in anchors:
                    seen.add(origin)
                    stack.append(origin)
        return seen

    def _nearest(self, key: str, anchors: set[str]) -> str | None:
        """The latest sampler upstream of ``key`` in execution order, or None."""
        view = self.view
        upstream = view.upstream([key]) & anchors
        if not upstream:
            return None
        return max(upstream, key=lambda a: (view.order[a], id_order(a)))

    def _sampler_group(self, anchor: str) -> tuple[str, Zone | None] | None:
        """A sampler's (group, zone) when known by now: its own sampling group, or the
        explicit group it sits in; else None."""
        owner = self.claims.get(anchor)
        if owner is None:
            return f"sampling:{anchor}", Zone.SAMPLING
        if owner.rule == 2:
            return owner.group, owner.zone
        return None

    def _beside(self, key: str, anchors: set[str]) -> tuple[str, str, Zone | None] | None:
        """The nearest upstream sampler's (anchor, group, zone) when a later sampler follows
        it and its group is known by now (its own, or an explicit one); else None."""
        view = self.view
        nearest = self._nearest(key, anchors)
        if nearest is None or not (view.downstream([nearest]) & anchors) - {nearest}:
            return None
        owner = self._sampler_group(nearest)
        return None if owner is None else (nearest, *owner)

    def rule4_cockpit_sources(self, provisional: Mapping[str, Claim]) -> None:
        view = self.view
        anchors = set(self.anchors)
        for key in view.keys:
            if not self.free(key) or key in self.no_lift or not view.is_source(key):
                continue
            outs = view.out_types(key)
            if LATENT in outs and self._latent_reaches_sampler(key, anchors):
                self.claim(key, 4, COCKPIT_KEY, Zone.COCKPIT, CockpitSlot.SIZE)
                continue
            if not view.has_widgets(key) or model_asset(outs):
                continue
            if len(self._fed_groups(key, provisional, set())) >= 2:
                self.claim(key, 4, COCKPIT_KEY, Zone.COCKPIT, self.cockpit_slot(key))

    def _latent_reaches_sampler(self, key: str, anchors: set[str]) -> bool:
        """``key`` feeds a sampler directly, or through links carrying only a latent and
        latent steps only: the canvas size stays a cockpit control when a latent upscale or
        blend sits between it and the sampler."""
        view = self.view
        stack, seen = [key], {key}
        while stack:
            node = stack.pop()
            for edge in view.outgoing[node]:
                target = edge.target
                if target in anchors and (node == key or edge.types == {LATENT}):
                    return True
                if target not in seen and edge.types == {LATENT} and self._latent_step(target):
                    seen.add(target)
                    stack.append(target)
        return False

    def _latent_step(self, key: str) -> bool:
        """A node that only processes a latent: every output a latent, every declared input a
        latent or a value (a parameter), at least one latent input, and not a sampler."""
        view = self.view
        if key in self.anchors:
            return False
        return view.out_types(key) == {LATENT} and view.in_types(key) - VALUE_TYPES == {LATENT}

    def _fed_groups(self, key: str, provisional: Mapping[str, Claim], seen: set[str]) -> set[str]:
        groups: set[str] = set()
        for edge in self.view.outgoing[key]:
            target = edge.target
            if target in seen:
                continue
            seen.add(target)
            claim = provisional.get(target)
            if claim is None and (target in self.reserved or self.plumbing_shape(target)):
                groups |= self._fed_groups(target, provisional, seen)
            elif claim is None:
                groups.add(f"stage:{self.view.depth[target]}")
            else:
                groups.add(claim.group)
        return groups

    def rule5_sampling(self) -> None:
        view = self.view
        for anchor in self.anchors:
            group = f"sampling:{anchor}"
            self.claim(anchor, 5, group, Zone.SAMPLING)
            for edge in view.incoming[anchor]:
                if edge.types & ADJACENT_TYPES:
                    self.claim(edge.origin, 5, group, Zone.SAMPLING)

    def rule6_model(self) -> None:
        view = self.view
        heads = set(self.anchors) | self.guiders | {k for k, c in self.claims.items() if c.rule == 5}
        stack = sorted({e.origin for head in heads for e in view.incoming[head] if MODEL in e.types}, key=id_order)
        seen: set[str] = set()
        while stack:
            key = stack.pop()
            if key in seen or MODEL not in view.out_types(key):
                continue
            seen.add(key)
            self.claim(key, 6, MODEL_KEY, Zone.MODEL)
            stack.extend(e.origin for e in view.incoming[key] if MODEL in e.types)
        for key in view.keys:
            # A value output beside the assets (a text one, say) does not make the node
            # anything but a model-asset step; its asset outputs still decide.
            assets = view.out_types(key) - VALUE_TYPES
            if assets and assets <= ASSET_TYPES and view.linked_in_types(key) <= ASSET_TYPES | VALUE_TYPES:
                self.claim(key, 6, MODEL_KEY, Zone.MODEL)

    def rule7_control(self) -> None:
        view = self.view
        sinks = set(self.anchors) | self.guiders
        branch: set[str] = set()
        for key in view.keys:
            if CONDITIONING not in view.out_types(key):
                continue
            control_in = [e for e in view.incoming[key] if e.types & CONTROL_TYPES]
            if not control_in or not (view.downstream([key]) & sinks):
                continue
            branch.add(key)
            stack = [e.origin for e in control_in]
            while stack:
                node = stack.pop()
                if node in branch:
                    continue
                branch.add(node)
                stack.extend(e.origin for e in view.incoming[node] if e.types & CONTROL_TYPES)
        members = {key for key in branch if self.free(key) and not self.plumbing_shape(key)}
        anchors = set(self.anchors)
        for key in view.topo_sorted(members):
            if not view.is_source(key) or key in self.no_lift:
                continue
            outside = [e.target for e in view.outgoing[key] if e.target not in branch]
            if any(view.downstream([target]) & anchors for target in outside):
                self.claim(key, 7, COCKPIT_KEY, Zone.COCKPIT, CockpitSlot.REFERENCE)
                members.discard(key)
        for component in components(members, view):
            for key in component:
                self.claim(key, 7, f"control:{component[0]}", Zone.CONTROL)

    def rule8_prompts(self) -> None:
        view = self.view
        sinks = view.topo_sorted(set(self.anchors) | self.guiders)
        first_anchor = self.anchors[0] if self.anchors else None
        lift = not any(c.zone is Zone.CONTROL for c in self.claims.values())
        decoded = view.downstream(self.decodes())
        for key in view.keys:
            if not self.free(key) or CONDITIONING not in view.out_types(key):
                continue
            if view.linked_in_types(key) & CONTROL_TYPES:
                continue
            reached = view.downstream([key])
            if key in decoded and not any(sink in reached for sink in sinks):
                # Fed from after a decode and conditioning no sampler or guider (a pipe
                # unpacked between detailers, say): post-processing, where its input is.
                continue
            polarity = self.polarity.get(key)
            encoder = not any(CONDITIONING in e.types for e in view.incoming[key])
            if lift and encoder and polarity is not None and key not in self.no_lift and first_anchor in reached:
                slot = CockpitSlot.POSITIVE if polarity == POSITIVE else CockpitSlot.NEGATIVE
                self.claim(key, 8, COCKPIT_KEY, Zone.COCKPIT, slot)
                continue
            target = next((sink for sink in sinks if sink in reached), None)
            self.claim(key, 8, f"{PROMPT_KEY}:{target}" if target is not None else PROMPT_KEY, Zone.PROMPT)

    def decodes(self) -> list[str]:
        """Nodes turning a linked latent into pixels: where post-processing starts."""
        view = self.view
        return [key for key in view.keys if LATENT in view.linked_in_types(key) and view.out_types(key) & PIXELS]

    def rule8_postprocess(self) -> None:
        view = self.view
        decodes = self.decodes()
        # Only the final outputs: an intermediate preview and the decode feeding it sit in
        # their sampler's group (rule 3).
        outputs = [key for key in view.keys if view.output_node(key) and key not in self.previews]
        if not decodes or not outputs:
            return
        anchors = set(self.anchors)
        before_outputs = view.upstream(outputs) - set(outputs)
        members = {
            key
            for key in view.downstream(decodes) & before_outputs
            if self.free(key) and not self.plumbing_shape(key) and not (view.downstream([key]) & anchors)
        }
        for component in components(members, view):
            for key in component:
                self.claim(key, 8, f"post:{component[0]}", Zone.POSTPROCESS)

    def rule9_plumbing(self) -> set[str]:
        view = self.view
        plumbing = {key for key in view.keys if key in self.reserved or (self.free(key) and self.plumbing_shape(key))}
        self.reserved -= plumbing
        for key in reversed(view.topo_sorted(plumbing)):
            consumers = view.topo_sorted(e.target for e in view.outgoing[key])
            for consumer in consumers:
                owner = self.claims.get(consumer)
                if owner is not None and consumer not in self.optional:
                    self.claims[key] = Claim(9, owner.group, None)
                    break
            else:
                # Every consumer optional: annotate cannot change a node's mode, so the node
                # stays non-optional and goes to its nearest non-optional producer's group,
                # else (no such producer) to a stage group. In compile, verify's B2 already
                # rejects such a node unless its author marked it optional, so this case
                # arises only in tidy with ``--regroup``.
                if consumers and all(consumer in self.optional for consumer in consumers):
                    group = self._producer_group(key, plumbing)
                    if group is not None:
                        self.claims[key] = Claim(9, group, None)
        return plumbing

    def _producer_group(self, key: str, plumbing: set[str]) -> str | None:
        """The group of the nearest non-optional node upstream of ``key`` (breadth first,
        each layer in topological order), passing through optional nodes and unresolved
        plumbing; a node no rule claimed yet counts as its stage group."""
        view = self.view
        layer, seen = [key], {key}
        while layer:
            parents = view.topo_sorted(e.origin for node in layer for e in view.incoming[node] if e.origin not in seen)
            seen.update(parents)
            for parent in parents:
                if parent in self.optional:
                    continue
                claim = self.claims.get(parent)
                if claim is not None:
                    return claim.group
                if parent not in plumbing:
                    return f"stage:{view.depth[parent]}"
            layer = parents  # all optional or unresolved plumbing: look further up
        return None

    def cockpit_slot(self, key: str) -> CockpitSlot:
        view = self.view
        polarity = self.polarity.get(key)
        if polarity is not None:
            return CockpitSlot.POSITIVE if polarity == POSITIVE else CockpitSlot.NEGATIVE
        if LATENT in view.out_types(key):
            return CockpitSlot.SIZE
        names = {e.input_name.lower() for e in view.outgoing[key]}
        if any(SEED_WORD in name for name in names):
            return CockpitSlot.SEED
        if names & SIZE_WORDS:
            return CockpitSlot.SIZE
        if IMAGE in view.out_types(key) and view.is_source(key):
            return CockpitSlot.REFERENCE
        return CockpitSlot.OTHER


def classify(view: GraphView, optional: frozenset[str], intents: Mapping[str, NodeIntent]) -> Classified:
    return _Rules(view, optional, intents).run()


def polarities(view: GraphView, sinks: set[str]) -> dict[str, str]:
    """``positive`` or ``negative`` for each conditioning node whose conditioning reaches
    sampler or guider inputs of one polarity only: the first input downstream whose name
    says positive or negative decides; a sampler or guider input that says neither counts
    as positive (its only conditioning)."""
    out: dict[str, str] = {}
    for key in view.keys:
        if CONDITIONING not in view.out_types(key):
            continue
        found: set[str] = set()
        stack, seen = [key], {key}
        while stack:
            node = stack.pop()
            for edge in view.outgoing[node]:
                if CONDITIONING not in edge.types:
                    continue
                name = edge.input_name.lower()
                if NEGATIVE in name:
                    found.add(NEGATIVE)
                elif POSITIVE in name:
                    found.add(POSITIVE)
                elif edge.target in sinks:
                    found.add(POSITIVE)
                elif edge.target not in seen and CONDITIONING in view.out_types(edge.target):
                    seen.add(edge.target)
                    stack.append(edge.target)
        if len(found) == 1:
            out[key] = found.pop()
    return out


def components(
    keys: set[str] | frozenset[str], view: GraphView, same: Callable[[str, str], bool] | None = None
) -> list[tuple[str, ...]]:
    """Connected parts of ``keys`` along links between them, each in id order, the parts
    ordered by their first key. With ``same``, a link joins its two ends only when
    ``same(origin, target)`` holds."""
    keys = set(keys)
    neighbours: dict[str, set[str]] = {key: set() for key in keys}
    for edge in view.edges:
        if edge.origin in keys and edge.target in keys and (same is None or same(edge.origin, edge.target)):
            neighbours[edge.origin].add(edge.target)
            neighbours[edge.target].add(edge.origin)
    out: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for start in sorted(keys, key=id_order):
        if start in seen:
            continue
        part, stack = [], [start]
        seen.add(start)
        while stack:
            node = stack.pop()
            part.append(node)
            for other in neighbours[node]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        out.append(tuple(sorted(part, key=id_order)))
    return out


def majority_zone(zones: list[Zone]) -> Zone:
    """The zone most of ``zones`` have; ties go to the earlier zone in :class:`Zone` order;
    ``STAGE`` for none."""
    if not zones:
        return Zone.STAGE
    counts = Counter(zones)
    rank = {zone: index for index, zone in enumerate(Zone)}
    return min(counts, key=lambda zone: (-counts[zone], rank[zone]))


# The zones in their usual data-flow order. A single node's own zone can be earlier than the
# zone of a node feeding it: a first-pass decode whose image feeds a second pass's tile
# ControlNet is of the control zone (rule 7), fed by the sampler. Sampling and the generic stage
# share a level. The cockpit and the optional zone are not on the flow (knobs at its head,
# modules beside it) and come before every level.
_FLOW_LEVEL = {
    Zone.MODEL: 1,
    Zone.PROMPT: 2,
    Zone.CONTROL: 3,
    Zone.SAMPLING: 4,
    Zone.STAGE: 4,
    Zone.POSTPROCESS: 5,
    Zone.OUTPUT: 6,
}


def flow_level(zone: Zone) -> int:
    """The zone's place in the data flow (:data:`_FLOW_LEVEL`), 0 off the flow."""
    return _FLOW_LEVEL.get(zone, 0)


def latest_zone(zones: list[Zone]) -> Zone:
    """The latest of ``zones`` in the data flow (:func:`flow_level`); between zones of one level
    (sampling and the generic stage) the majority, as :func:`majority_zone`; ``STAGE`` for
    none."""
    if not zones:
        return Zone.STAGE
    top = max(flow_level(zone) for zone in zones)
    return majority_zone([zone for zone in zones if flow_level(zone) == top])
