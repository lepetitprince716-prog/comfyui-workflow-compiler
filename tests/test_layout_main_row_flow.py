"""The main row follows the data flow: a main-row group goes no earlier than the slot of any
main-row group strictly upstream of it (``cwc.layout.place._slots``).

The main row is laid out left to right in slot order. A group whose zone's slot comes before
that of a group feeding it would sit left of its producer: a prompt-zone combine fed through
the first pass's sampler, or the prompt-zone node fed by a segment of an author group that
went to the sampling slot. Such a group moves to the latest slot among the main-row groups
strictly upstream of it (they reach it along the links between top-level groups, through
groups of any band, not through a link into a cockpit frame, and it does not reach them back).
Cockpit frames, lower-band groups and the two ends of a cycle do not move.

Five parts: whole graphs (the compile and the position of the moved group), a random graph of
the ``fed`` entry, ``place()`` with hand-sized parts (one test per condition of the rule), the
order property over random graphs of three entries, and the random-graph seeds on which the
rule moves a group.
"""

from __future__ import annotations

import copy
import unittest
from functools import lru_cache
from typing import Any

from tests import support  # noqa: F401
from tests.graphs import DEFS, node, txt2img
from tests.layout_generator_wide import wide_graph_fed, wide_graph_outputs, wide_graph_scatter
from tests.test_layout_wide import placed_groups, stack_faults

from cwc.annotate import annotate
from cwc.annotate.types import GroupPlan, Zone
from cwc.canvas.geometry import Rect
from cwc.instantiate import instantiate
from cwc.layout.inner import Part
from cwc.layout.place import place
from cwc.layout.presets import get_preset
from cwc.layout.section import Placed
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")
# The presets whose prompt slot comes before a repeated sampling slot: there the rule moves groups.
SPLIT = ("bento", "stepped", "tower-preview")
EPS = 1e-6


# ---- whole graphs ----


def _ks(latent: list[Any], denoise: float, pos: list[Any], neg: list[Any]) -> dict[str, Any]:
    return node(
        "KSampler",
        {"model": ["ckpt", "MODEL"], "positive": pos, "negative": neg, "latent_image": latent, "seed": 1,
         "steps": 20, "cfg": 5.0, "sampler_name": "euler", "scheduler": "normal", "denoise": denoise},
    )


def _prompt(text: str) -> dict[str, Any]:
    return node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": text})


def _apply(pos: list[Any], neg: list[Any], loader: str, image: list[Any]) -> dict[str, Any]:
    return node(
        "ControlNetApplyAdvanced",
        {"positive": pos, "negative": neg, "control_net": [loader, 0], "image": image,
         "strength": 0.8, "start_percent": 0.0, "end_percent": 1.0},
    )


def _two_pass() -> dict[str, Any]:
    """txt2img, then a latent upscale and a second sampler ``s2`` into the decode; ``d1`` decodes
    the first pass."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = _ks(["up", 0], 0.45, ["pos", 0], ["neg", 0])
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return doc


def p2_graph() -> dict[str, Any]:
    """A tile ControlNet ``cn2`` on the prompts takes the first pass's decode ``d1``; ``comb``
    (prompt zone) combines ``cn2`` with a second positive ``pos2`` into the second sampler. So
    ``comb`` is downstream of the first sampler."""
    doc = _two_pass()
    doc["tcnl"] = node("ControlNetLoader", {"control_net_name": "tile.safetensors"})
    doc["cn2"] = _apply(["pos", 0], ["neg", 0], "tcnl", ["d1", 0])
    doc["pos2"] = _prompt("detailed")
    doc["comb"] = node("ConditioningCombine", {"conditioning_1": ["cn2", 0], "conditioning_2": ["pos2", 0]})
    doc["s2"]["inputs"]["positive"] = ["comb", 0]
    doc["s2"]["inputs"]["negative"] = ["cn2", 1]
    return doc


def p1_graph() -> dict[str, Any]:
    """``cn1`` (its own image) on the prompts; ``comb`` combines ``cn1`` and ``pos2`` into ``cn2``,
    whose image is the first pass's decode; ``cn2`` steers the second sampler. ``comb`` does not
    depend on the first sampler node by node: one control group holds ``cn1`` and ``d1``."""
    doc = _two_pass()
    doc["img"] = node("LoadImage", {"image": "pose.png"})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "cn.safetensors"})
    doc["cn1"] = _apply(["pos", 0], ["neg", 0], "cnl", ["img", "IMAGE"])
    doc["pos2"] = _prompt("detailed")
    doc["comb"] = node("ConditioningCombine", {"conditioning_1": ["cn1", 0], "conditioning_2": ["pos2", 0]})
    doc["tcnl"] = node("ControlNetLoader", {"control_net_name": "tile.safetensors"})
    doc["cn2"] = _apply(["comb", 0], ["cn1", 1], "tcnl", ["d1", 0])
    doc["s2"]["inputs"]["positive"] = ["cn2", 0]
    doc["s2"]["inputs"]["negative"] = ["cn2", 1]
    return doc


def q4_graph() -> dict[str, Any]:
    """Custom sampling with two positives combined (``both``) into the guider; an author group
    holds the scheduler, the second positive and the sampler. Layout cuts it, because
    ``pos2 -> both -> guider -> sampler`` leaves it and comes back, and its first segment
    goes to the sampling slot while ``both`` is of the prompt zone."""
    doc = txt2img()
    doc["noise"] = node("RandomNoise", {"noise_seed": 3})
    doc["guider"] = node("CFGGuider", {"model": ["ckpt", "MODEL"], "positive": ["pos", 0], "negative": ["neg", 0], "cfg": 4.0})
    doc["pick"] = node("KSamplerSelect", {"sampler_name": "euler"})
    doc["sched"] = node("BasicScheduler", {"model": ["ckpt", "MODEL"], "scheduler": "normal", "steps": 20, "denoise": 1.0})
    doc["sampler"] = node(
        "SamplerCustomAdvanced",
        {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["pick", 0], "sigmas": ["sched", 0], "latent_image": ["latent", 0]},
    )
    doc["pos2"] = _prompt("detailed")
    doc["both"] = node("ConditioningCombine", {"conditioning_1": ["pos", 0], "conditioning_2": ["pos2", 0]})
    doc["guider"]["inputs"]["positive"] = ["both", 0]
    for key in ("sched", "pos2", "sampler"):
        doc[key]["_meta"] = {"ui_group": "Scatter"}
    return doc


GRAPHS = {"p1": p1_graph, "p2": p2_graph, "q4": q4_graph}


@lru_cache(maxsize=None)
def _compiled(name: str, preset: str):
    return compile_document(copy.deepcopy(GRAPHS[name]()), DEFS, preset=preset)


def _errors(out) -> list[str]:
    return [f"{f.code.value}: {f.message}" for f in out.report.findings if f.severity.value == "error"]


def _plan(out):
    return annotate(instantiate(out.authoring), DEFS, out.authoring)


class _Written:
    """Where the written groups of one compile are: the frame and the column of the group that
    holds an authoring node."""

    def __init__(self, out) -> None:
        self.out = out
        self.key = {i: key for key, i in out.layout.group_index.items()}

    def frame(self, author_id: str) -> tuple[float, float, float, float]:
        """``(x, y, w, h)`` of the written group holding ``author_id``."""
        index = self.out.layout.membership[str(self.out.authoring.ids[author_id])]
        return tuple(self.out.canvas.raw["groups"][index]["bounding"])

    def column(self, author_id: str):
        """The column (``LayoutResult.columns``) the written group holding ``author_id`` is in."""
        index = self.out.layout.membership[str(self.out.authoring.ids[author_id])]
        written = self.key[index]
        x, y, _, _ = self.frame(author_id)
        found = [
            c for c in self.out.layout.columns
            if any(written == g or written.startswith(g + "/") for g in c.groups)
            and c.rect.x - EPS <= x < c.rect.right - EPS and c.rect.y - EPS <= y < c.rect.y + c.rect.h - EPS
        ]
        assert len(found) == 1, (author_id, written, found)
        return found[0]


class WholeGraphTest(unittest.TestCase):
    def test_no_error_and_no_stack_fault(self) -> None:
        for name in GRAPHS:
            for preset in PRESETS:
                with self.subTest(graph=name, preset=preset):
                    out = _compiled(name, preset)
                    self.assertEqual(_errors(out), [])
                    self.assertEqual(stack_faults(out, _plan(out)), [])

    def test_p2_second_prompts_sit_in_the_repeated_slot_between_the_passes(self) -> None:
        for preset in SPLIT:
            with self.subTest(preset=preset):
                out = _compiled("p2", preset)
                at = _Written(out)
                slots = get_preset(preset).bands[0].slots
                column = at.column("comb")
                self.assertIsNotNone(slots[column.slot].repeat, "the combine's column is an instance of a repeated slot")
                comb, first, control, second = (at.frame(k) for k in ("comb", "sampler", "cn2", "s2"))
                self.assertGreaterEqual(comb[0], first[0] + first[2], "right of the first sampling group")
                self.assertGreaterEqual(comb[0], control[0] + control[2], "right of the control group")
                self.assertLessEqual(comb[0] + comb[2], second[0], "left of the second sampling group")
                self.assertEqual(at.column("pos").slot, get_preset(preset).home(Zone.PROMPT)[1], "the first prompts stay in the prompt slot")

    def test_q4_combine_sits_between_the_two_segments(self) -> None:
        for preset in SPLIT:
            with self.subTest(preset=preset):
                at = _Written(_compiled("q4", preset))
                head, both, tail = at.frame("sched"), at.frame("both"), at.frame("sampler")
                self.assertNotEqual(head, tail, "the author group is cut")
                self.assertGreaterEqual(both[0], head[0] + head[2])
                self.assertLessEqual(both[0] + both[2], tail[0])


class FedEntryTest(unittest.TestCase):
    def test_seed_315(self) -> None:
        doc = wide_graph_fed(315)
        for preset in SPLIT:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                self.assertEqual(_errors(out), [])


# ---- place() with hand-sized parts, under bento ----
# Bento's band 0: cockpit (0), model and prompt (1), sampling and stage (2, repeated along x),
# post-processing (3), output (4); band 1: control and optional.

W, H = 300, 100


def _place(specs: list[tuple[str, Zone, int]], links: list[tuple[str, str]], exempt: frozenset[str] = frozenset()) -> Placed:
    """``(key, zone, stage)`` per top-level group in plan order; one link per pair."""
    groups, units = [], {}
    for key, zone, stage in specs:
        plan = GroupPlan(key=key, zone=zone, name=key, members=(), stage=stage)
        groups.append(plan)
        units[key] = [Part(key=key, plan=plan, letter="", box=Rect(0, 0, W, H), nodes={})]
    return place(get_preset("bento"), groups, units, dict.fromkeys(links, 1), x0=0.0, exempt=exempt, fits=lambda placed: True)


def _where(placed: Placed, key: str) -> tuple[int, int]:
    """``(preset band, slot)`` of the item holding ``key``."""
    (item,) = [item for item in placed.items if key in item.groups]
    return item.band, item.slot


S, P, M, PP, K, CTL = Zone.SAMPLING, Zone.PROMPT, Zone.MODEL, Zone.POSTPROCESS, Zone.COCKPIT, Zone.CONTROL


class SlotRuleTest(unittest.TestCase):
    def test_moves_when_a_strict_producer_has_a_later_slot(self) -> None:
        placed = _place([("pos", P, 0), ("s1", S, 1), ("comb", P, 2), ("s2", S, 3)], [("pos", "s1"), ("s1", "comb"), ("comb", "s2")])
        self.assertEqual(_where(placed, "pos"), (0, 1))
        self.assertEqual(_where(placed, "comb"), (0, 2))
        (item,) = [item for item in placed.items if "comb" in item.groups]
        self.assertEqual((item.stage, item.groups), (2, ["comb"]), "an instance of its own, at its rank")

    def test_no_move_when_the_producer_is_not_later(self) -> None:
        placed = _place([("m", M, 0), ("pos", P, 1), ("s", S, 2), ("pp", PP, 3)], [("m", "pos"), ("pos", "s"), ("s", "pp")])
        self.assertEqual([_where(placed, k) for k in ("m", "pos", "s", "pp")], [(0, 1), (0, 1), (0, 2), (0, 3)])

    def test_no_move_when_the_two_reach_each_other(self) -> None:
        placed = _place([("s", S, 0), ("comb", P, 0)], [("s", "comb"), ("comb", "s")])
        self.assertEqual(_where(placed, "comb"), (0, 1))
        self.assertEqual(_where(placed, "s"), (0, 2))

    def test_no_move_when_reached_only_through_a_link_into_a_cockpit_frame(self) -> None:
        placed = _place([("k", K, 0), ("s", S, 1), ("comb", P, 2)], [("s", "k"), ("k", "comb")], exempt=frozenset({"k"}))
        self.assertEqual(_where(placed, "comb"), (0, 1))

    def test_a_cockpit_frame_does_not_move(self) -> None:
        placed = _place([("s", S, 0), ("k", K, 1)], [("s", "k")], exempt=frozenset({"k"}))
        self.assertEqual(_where(placed, "k"), (0, 0))

    def test_a_lower_band_group_passes_reach_on_and_keeps_its_band(self) -> None:
        placed = _place([("s", S, 0), ("ctl", CTL, 1), ("comb", P, 2)], [("s", "ctl"), ("ctl", "comb")])
        self.assertEqual(_where(placed, "ctl"), (1, 0), "the lower group stays in its band")
        self.assertEqual(_where(placed, "comb"), (0, 2), "reach goes through the lower group")

    def test_the_latest_of_several_producer_slots(self) -> None:
        placed = _place([("m", M, 0), ("s", S, 1), ("pp", PP, 2), ("comb", P, 3)], [("m", "comb"), ("s", "comb"), ("pp", "comb")])
        self.assertEqual(_where(placed, "comb"), (0, 3))

    def test_what_a_moved_group_feeds_follows_it(self) -> None:
        placed = _place([("s", S, 0), ("comb", P, 1), ("m", M, 2)], [("s", "comb"), ("comb", "m")])
        self.assertEqual(_where(placed, "comb"), (0, 2))
        self.assertEqual(_where(placed, "m"), (0, 2))

    def test_in_a_slot_that_is_not_repeated_the_slot_stack_rule_applies(self) -> None:
        placed = _place([("pp", PP, 0), ("m", M, 1)], [("pp", "m")])
        (item,) = [item for item in placed.items if "m" in item.groups]
        self.assertEqual((item.band, item.slot), (0, 3))
        self.assertEqual(item.columns, [["pp"], ["m"]], "one sub-column right of the group that reaches it")


# ---- the property, over random graphs ----


def _written_frames(out, plan) -> tuple[dict[str, str], dict[str, Zone], dict[str, list[list[float]]]]:
    """The placed groups as :func:`tests.test_layout_wide.placed_groups` reads them, and the written
    frames (``[x, y, w, h]``) of each: a placed group's frames are the written groups that hold its
    members (a group's parts, a segment of a cut group)."""
    placed, zone = placed_groups(out, plan)
    top = {g.key for g in plan.groups if g.parent is None}
    boxes = out.canvas.raw["groups"]
    members: dict[int, list[str]] = {}
    for node_key, index in out.layout.membership.items():
        if index is not None:
            members.setdefault(index, []).append(node_key)
    found: dict[str, list[list[float]]] = {}
    for key, index in out.layout.group_index.items():
        plan_key = next((k for k in top if key == k or key.startswith(k + "/")), None)
        if plan_key is None:
            continue
        own = {placed[n] for n in members.get(index, ()) if placed.get(n, plan_key) != plan_key}
        assert len(own) <= 1, (key, own)
        found.setdefault(own.pop() if own else plan_key, []).append(list(boxes[index]["bounding"]))
    return placed, zone, found


def flow_faults(out, plan, preset_name: str) -> tuple[int, list[tuple[str, str, float, float]]]:
    """``(pairs checked, faults)``. For each pair of placed main-row groups ``p``, ``g`` where ``p``
    is strictly upstream of ``g`` (the links between placed groups lead from ``p`` to ``g``, not
    through a link into a cockpit frame, and do not lead back from ``g`` to ``p``), a fault is a
    written frame of ``g`` that starts left of a written frame of ``p`` and does not lie wholly
    below it. A fault is ``(p, g, p's x, g's x)``."""
    preset = get_preset(preset_name)
    placed, zone, found = _written_frames(out, plan)
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

    main = sorted(k for k in found if k in zone and preset.home(zone[k])[0] == 0)
    reaches = {k: reach(k) for k in main}
    pairs, faults = 0, []
    for p in main:
        for g in main:
            if p == g or g not in reaches[p] or p in reaches[g]:
                continue
            pairs += 1
            for pb in found[p]:
                for gb in found[g]:
                    if gb[0] < pb[0] - EPS and not gb[1] >= pb[1] + pb[3] - EPS:
                        faults.append((p, g, pb[0], gb[0]))
    return pairs, faults


ENTRIES = {"fed": wide_graph_fed, "scatter": wide_graph_scatter, "outputs": wide_graph_outputs}


class FlowPropertyTest(unittest.TestCase):
    """The property the slot rule keeps, on the random graphs of the wide entries under bento:
    every written frame of a main-row group starts at or right of every written frame of a
    main-row group strictly upstream of it, or lies wholly below it. Strictly upstream: the links
    between top-level groups lead from the one to the other, not through a link into a cockpit
    frame, and do not lead back. The seeds are the whole range 0 to 59 of each entry, none
    picked."""

    def _check(self, entry: str, seed: int) -> int:
        out = compile_document(ENTRIES[entry](seed), DEFS, preset="bento")
        pairs, faults = flow_faults(out, _plan(out), "bento")
        self.assertEqual(faults, [], f"{entry} seed {seed}")
        return pairs

    def test_seeds_0_to_59_of_every_entry(self) -> None:
        for entry in ENTRIES:
            checked = 0
            for seed in range(60):
                with self.subTest(entry=entry, seed=seed):
                    checked += self._check(entry, seed)
            self.assertGreater(checked, 0, f"{entry}: no pair of groups was checked, the test would be empty")

    def test_fed_seed_315_where_the_rule_moves_a_group(self) -> None:
        """A prompt-zone group fed through a first-pass sampler: without the rule it sits left of
        its producers."""
        self.assertGreater(self._check("fed", 315), 0)


# Seeds of the random-graph entries where the slot rule moves a group: with the rule switched off,
# each of them fails to compile under every preset of ``SPLIT`` (a prompt-zone group left of its
# producers). Found by sweeping seeds beyond the range 0 to 59 that ``FlowPropertyTest`` covers.
MOVED_FED_SEEDS = (315, 681, 876, 900, 1011, 1254, 1507)
MOVED_SCATTER_SEEDS = (433,)
MOVED_SEEDS = tuple(("fed", s) for s in MOVED_FED_SEEDS) + tuple(("scatter", s) for s in MOVED_SCATTER_SEEDS)


class MovedSeedsTest(unittest.TestCase):
    """The random graphs on which the slot rule moves a group, pinned: each compiles without an
    error and without a stack fault under every preset of ``SPLIT``, and under bento no main-row
    group sits left of a group strictly upstream of it (with at least one pair checked)."""

    def test_compile_and_stack_under_every_split_preset(self) -> None:
        for entry, seed in MOVED_SEEDS:
            for preset in SPLIT:
                with self.subTest(entry=entry, seed=seed, preset=preset):
                    out = compile_document(ENTRIES[entry](seed), DEFS, preset=preset)
                    self.assertEqual(_errors(out), [])
                    self.assertEqual(stack_faults(out, _plan(out)), [])

    def test_no_group_left_of_its_producer_under_bento(self) -> None:
        for entry, seed in MOVED_SEEDS:
            with self.subTest(entry=entry, seed=seed):
                out = compile_document(ENTRIES[entry](seed), DEFS, preset="bento")
                pairs, faults = flow_faults(out, _plan(out), "bento")
                self.assertEqual(faults, [])
                self.assertGreater(pairs, 0, "no pair of groups was checked, the test would be empty")


if __name__ == "__main__":
    unittest.main()
