"""Property tests over ``tests.layout_generator_wide``, under the five presets.

For seeds 0 to ``SEEDS``, ``wide_graph(seed)`` (explicit groups on required and cockpit nodes,
optional modules between required ones, cockpit nodes without a usable display name, early
decodes feeding the output, custom sampling shared between stages) is compiled with each
preset. The properties: the compile report holds no error finding; and in a slot stacked
along y (a ``stack y`` slot, or an instance of a repeated slot), a group that an earlier group
of the stack reaches, one way, along the links between top-level groups (links into a cockpit
group aside: they run right to left by design), starts no further left than that earlier
group's right edge. The reach counts paths through groups outside the stack. Segments of one
plan group in one stack follow the same rule, each after the one before it; the parts of a
split cockpit do not.

The same properties hold for ``wide_graph_across(seed)``, which adds one author group holding a
node from each end of the data flow: layout cuts it, and each segment goes to the slot of its
own members' zone.

They hold too for ``wide_graph_outputs(seed)``, whose author group holds output nodes from
several stages (and may hold other members), and for ``wide_graph_scatter(seed)``, whose author
group holds members drawn from anywhere in the graph: such a group goes to the latest zone of
its members, and a member of an earlier zone that cannot move there stays in its own.

There is no list of known failures: a seed that fails is a layout bug to fix.
"""

import copy
import unittest
from functools import lru_cache

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled
from tests.layout_generator_wide import (
    wide_across_features,
    wide_features,
    wide_graph,
    wide_graph_across,
    wide_graph_outputs,
    wide_graph_scatter,
    wide_outputs_features,
    wide_scatter_features,
)

from cwc.annotate import annotate
from cwc.annotate.types import Zone
from cwc.instantiate import instantiate
from cwc.layout.inner import Graph, segment_zone, segments
from cwc.layout.presets import get_preset
from cwc.layout.types import Axis
from cwc.pipeline import compile_document

SEEDS = range(25)  # the whole file runs in about 15 s
PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")
EPS = 1e-6


@lru_cache(maxsize=None)
def compiled_wide(seed: int, preset: str):
    return compile_document(wide_graph(seed), DEFS, preset=preset)


@lru_cache(maxsize=None)
def plan_wide(seed: int):
    """The annotate plan of the seed's graph: what layout grouped, before layout moved anything."""
    canvas, authoring = compiled(copy.deepcopy(wide_graph(seed)))
    return annotate(canvas, DEFS, authoring)


@lru_cache(maxsize=None)
def compiled_across(seed: int, preset: str):
    return compile_document(wide_graph_across(seed), DEFS, preset=preset)


@lru_cache(maxsize=None)
def plan_across(seed: int):
    canvas, authoring = compiled(copy.deepcopy(wide_graph_across(seed)))
    return annotate(canvas, DEFS, authoring)


@lru_cache(maxsize=None)
def compiled_outputs(seed: int, preset: str):
    return compile_document(wide_graph_outputs(seed), DEFS, preset=preset)


@lru_cache(maxsize=None)
def plan_outputs(seed: int):
    canvas, authoring = compiled(copy.deepcopy(wide_graph_outputs(seed)))
    return annotate(canvas, DEFS, authoring)


@lru_cache(maxsize=None)
def compiled_scatter(seed: int, preset: str):
    return compile_document(wide_graph_scatter(seed), DEFS, preset=preset)


@lru_cache(maxsize=None)
def plan_scatter(seed: int):
    canvas, authoring = compiled(copy.deepcopy(wide_graph_scatter(seed)))
    return annotate(canvas, DEFS, authoring)


def placed_groups(out, plan) -> tuple[dict[str, str], dict[str, Zone]]:
    """The top-level groups as placement saw them: node key -> its placed group, and each placed
    group's zone. A placed group is a top-level plan group, or, for a group cut into segments
    (:func:`cwc.layout.inner.segments`), one segment keyed ``<key>#<segment>`` with its own
    members' zone (:func:`cwc.layout.inner.segment_zone`). Cut on the canvas before layout, as
    layout cuts."""
    graph = Graph.of(instantiate(out.authoring), plan.nodes, DEFS)
    parent = {g.key: g.parent for g in plan.groups}
    placed: dict[str, str] = {}
    for group in plan.groups:
        top = group.key
        while parent[top] is not None:
            top = parent[top]
        placed.update(dict.fromkeys(group.members, top))
    zone: dict[str, Zone] = {}
    for group in plan.children(None):
        zone[group.key] = group.zone
        if group.zone is Zone.COCKPIT or not group.members or plan.children(group.key):
            continue
        cut = segments(graph, list(group.members))
        for s, run in enumerate(cut if len(cut) > 1 else ()):
            key = f"{group.key}#{s}"
            zone[key] = segment_zone(graph, run)
            placed.update(dict.fromkeys(run, key))
    return placed, zone


def stack_faults(out, plan) -> list[tuple[str, str, float, float]]:
    """``(earlier, later, earlier's right edge, later's x)`` for each pair of written groups in
    one column stacked along y where the earlier reaches the later one way (or is the later's
    previous part of one plan group) and the later starts left of the earlier's right edge.

    Reach is that of the placed groups (:func:`placed_groups`): a segment of a cut group reaches
    what its own members reach and goes by its own zone. Taken per plan group instead, a group
    cut across the data flow would join its two ends, and its later segment's reach would be
    read as the earlier one's. A column's stack rule is that of the slot it is an instance of
    (``Column.slot``) in the band of its groups' zones: placement may move a main-row group to
    a later slot than its zone's, never to another band."""
    preset = get_preset(out.report.metrics["preset"])
    placed, zone = placed_groups(out, plan)
    top = {g.key for g in plan.groups if g.parent is None}
    cockpits = {g.key for g in plan.groups if g.parent is None and g.zone is Zone.COCKPIT}
    edges: dict[str, set[str]] = {}
    for link in out.canvas.links:
        a, b = placed.get(str(link.origin_id)), placed.get(str(link.target_id))
        if a is not None and b is not None and a != b and zone[b] is not Zone.COCKPIT:
            edges.setdefault(a, set()).add(b)

    def reach(key: str) -> set[str]:
        seen, todo = {key}, [key]
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return seen - {key}

    boxes = out.canvas.raw["groups"]
    members: dict[int, list[str]] = {}  # written index -> its node keys
    for node_key, index in out.layout.membership.items():
        if index is not None:
            members.setdefault(index, []).append(node_key)
    # plan key -> (written key, placed key, bounding); a group cut into segments, or split by
    # C1, is written as "<plan key>/a", "<plan key>/b", ...
    written: dict[str, list[tuple[str, str, list[float]]]] = {}
    for key, index in out.layout.group_index.items():
        plan_key = next((k for k in top if key == k or key.startswith(k + "/")), None)
        if plan_key is not None:
            own = {placed[n] for n in members.get(index, ()) if placed.get(n, plan_key) != plan_key}
            assert len(own) <= 1, (key, own)  # a written part never spans two segments
            written.setdefault(plan_key, []).append((key, own.pop() if own else plan_key, boxes[index]["bounding"]))
    found = []
    for column in out.layout.columns:
        rect = column.rect
        stack = []  # (plan key, written key, placed key, bounding), in the stack's order
        for plan_key in dict.fromkeys(column.groups):
            for key, at, box in sorted(written.get(plan_key, ())):
                if rect.x - EPS <= box[0] < rect.right - EPS and rect.y - EPS <= box[1] < rect.y + rect.h - EPS:
                    stack.append((plan_key, key, at, box))
        if not stack:
            continue
        # ``column.band`` counts wrapped bands, so the preset band comes from the zone's home (a
        # group never changes band); ``column.slot`` is the slot the group was placed in, which
        # for a main-row group moved downstream of a later slot is not its zone's.
        band, slot = preset.home(zone[stack[0][2]])[0], column.slot
        if preset.bands[band].slots[slot].stack is not Axis.Y:
            continue
        for i, (p, p_key, p_at, p_box) in enumerate(stack):
            for g, g_key, g_at, g_box in stack[i + 1 :]:
                # Parts of one group: segments follow each other; the parts of a split cockpit
                # (``cockpit-split``'s core box over the rest) stack by design.
                ahead = g_at in reach(p_at) and p_at not in reach(g_at) if g != p else p not in cockpits
                if ahead and g_box[0] < p_box[0] + p_box[2] - EPS:
                    found.append((p_key, g_key, p_box[0] + p_box[2], g_box[0]))
    return found


class WideGraphTest(unittest.TestCase):
    def test_compile_has_no_error(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    report = compiled_wide(seed, preset).report
                    found = [f"{f.code.value}: {f.message}" for f in report.findings if f.severity.value == "error"]
                    self.assertEqual(found, [], f"seed {seed} {wide_features(seed)}, {preset}")

    def test_stacked_groups_follow_reach(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    found = stack_faults(compiled_wide(seed, preset), plan_wide(seed))
                    self.assertEqual(found, [], f"seed {seed} {wide_features(seed)}, {preset}: (earlier, later, right edge, x)")


class WideAcrossGraphTest(unittest.TestCase):
    def test_compile_has_no_error(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    report = compiled_across(seed, preset).report
                    found = [f"{f.code.value}: {f.message}" for f in report.findings if f.severity.value == "error"]
                    self.assertEqual(found, [], f"seed {seed} {wide_across_features(seed)}, {preset}")

    def test_stacked_groups_follow_reach(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    found = stack_faults(compiled_across(seed, preset), plan_across(seed))
                    self.assertEqual(found, [], f"seed {seed} {wide_across_features(seed)}, {preset}: (earlier, later, right edge, x)")



class WideOutputsGraphTest(unittest.TestCase):
    def test_compile_has_no_error(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    report = compiled_outputs(seed, preset).report
                    found = [f"{f.code.value}: {f.message}" for f in report.findings if f.severity.value == "error"]
                    self.assertEqual(found, [], f"seed {seed} {wide_outputs_features(seed)}, {preset}")

    def test_stacked_groups_follow_reach(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    found = stack_faults(compiled_outputs(seed, preset), plan_outputs(seed))
                    self.assertEqual(found, [], f"seed {seed} {wide_outputs_features(seed)}, {preset}: (earlier, later, right edge, x)")



class WideScatterGraphTest(unittest.TestCase):
    def test_compile_has_no_error(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    report = compiled_scatter(seed, preset).report
                    found = [f"{f.code.value}: {f.message}" for f in report.findings if f.severity.value == "error"]
                    self.assertEqual(found, [], f"seed {seed} {wide_scatter_features(seed)}, {preset}")

    def test_stacked_groups_follow_reach(self):
        for preset in PRESETS:
            for seed in SEEDS:
                with self.subTest(preset=preset, seed=seed):
                    found = stack_faults(compiled_scatter(seed, preset), plan_scatter(seed))
                    self.assertEqual(found, [], f"seed {seed} {wide_scatter_features(seed)}, {preset}: (earlier, later, right edge, x)")


if __name__ == "__main__":
    unittest.main()
