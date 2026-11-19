"""A member of an author's group whose own zone is earlier than the zone of a member feeding it.

Rule 7 puts a first-pass decode whose image feeds a second pass's tile ControlNet in the control
zone, though the sampler feeds it; a conditioning combine is a prompt node, though a ControlNet
apply feeds it. When the second cut of a group (:func:`cwc.layout.inner.segments`) parted the
members by their own zone level, such a member went to a part ahead of the member feeding it,
or into one part with a member of the same level on the other side of the sampler, and two parts
fed each other: the placed groups formed a loop and C3 failed.

The members are parted by their cut level instead: a steering member's own zone level raised to
the cut levels of the members feeding it within the segment, passed on through members that do
not steer. The invariant: within an author's group, every link between two members stays in
one part or runs from an earlier part to a later one. It is checked on the hand-written graphs
and on the author's groups of three generator entries."""

import copy
import random
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img
from tests.layout_generator_wide import wide_graph_across, wide_graph_fed, wide_graph_outputs, wide_graph_scatter
from tests.test_layout_wide import stack_faults

from cwc.annotate import annotate
from cwc.annotate.rules import flow_level
from cwc.annotate.types import NodeNote, Zone
from cwc.canvas.geometry import Rect
from cwc.instantiate import instantiate
from cwc.layout.inner import Graph, _along_flow, _by_zone, segment_zone, segments
from cwc.normalize.types import Role
from cwc.pipeline import compile_document

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview", "auto")
SEEDS = range(50)


def ksampler(latent: list, denoise: float, positive: list, negative: list) -> dict:
    return node(
        "KSampler",
        {
            "model": ["ckpt", "MODEL"],
            "positive": positive,
            "negative": negative,
            "latent_image": latent,
            "seed": 1,
            "steps": 20,
            "cfg": 5.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": denoise,
        },
    )


def control_apply(positive: list, negative: list, loader: str, image: list, strength: float) -> dict:
    return node(
        "ControlNetApplyAdvanced",
        {
            "positive": positive,
            "negative": negative,
            "control_net": [loader, 0],
            "image": image,
            "strength": strength,
            "start_percent": 0.0,
            "end_percent": 1.0,
        },
    )


def group(doc: dict, name: str, *keys: str) -> dict:
    for key in keys:
        doc[key].setdefault("_meta", {})["ui_group"] = name
    return doc


def tile_upscale() -> dict:
    """Two passes in pixel space: the first decode ``d1`` is scaled (``sc``) and encoded again
    (``e2``) for the second sampler ``s2``, whose conditioning goes through a tile ControlNet
    ``cna`` on the scaled image."""
    doc = txt2img()
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["sc"] = node("ImageScaleBy", {"image": ["d1", 0], "upscale_method": "lanczos", "scale_by": 1.5})
    doc["e2"] = node("VAEEncode", {"pixels": ["sc", 0], "vae": ["ckpt", "VAE"]})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "tile.safetensors"})
    doc["cna"] = control_apply(["pos", 0], ["neg", 0], "cnl", ["sc", 0], 0.6)
    doc["s2"] = ksampler(["e2", 0], 0.4, ["cna", 0], ["cna", 1])
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return doc


def two_controlnets() -> dict:
    """:func:`tile_upscale` with a first-pass ControlNet ``cn1`` on a pose image: it conditions
    the first sampler and, through ``cna``, the second. The group ``Base`` holds ``cn1``, the
    sampler and ``d1``: ``cn1`` and ``d1`` are both of the control zone and both feed nodes
    outside the group, on either side of the sampler."""
    doc = tile_upscale()
    doc["img1"] = node("LoadImage", {"image": "pose.png"})
    doc["cn1"] = control_apply(["pos", 0], ["neg", 0], "cnl", ["img1", "IMAGE"], 0.8)
    doc["sampler"]["inputs"]["positive"] = ["cn1", 0]
    doc["sampler"]["inputs"]["negative"] = ["cn1", 1]
    doc["cna"]["inputs"]["positive"] = ["cn1", 0]
    doc["cna"]["inputs"]["negative"] = ["cn1", 1]
    return group(doc, "Base", "cn1", "sampler", "d1")


def first_pass() -> dict:
    """:func:`tile_upscale` with the group ``Pass 1`` around the first sampler, its decode and
    the scale, the last two of the control zone."""
    return group(tile_upscale(), "Pass 1", "sampler", "d1", "sc")


def latent_tile_preview() -> dict:
    """Two passes in latent space; the first decode ``d1`` feeds only a tile ControlNet ``cn2``
    on the second pass and a preview ``p1``. The group ``Hi`` holds the sampler and ``d1``;
    rule 3 adds ``p1``."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["d1"] = node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]})
    doc["p1"] = node("PreviewImage", {"images": ["d1", 0]})
    doc["cnl2"] = node("ControlNetLoader", {"control_net_name": "tile.safetensors"})
    doc["cn2"] = control_apply(["pos", 0], ["neg", 0], "cnl2", ["d1", 0], 0.5)
    doc["s2"] = ksampler(["up", 0], 0.45, ["cn2", 0], ["cn2", 1])
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return group(doc, "Hi", "sampler", "d1")


def conditioning_mix() -> dict:
    """A ControlNet ``cn`` on the prompts; ``comb`` combines its positive with a second prompt;
    a second ControlNet ``cn2`` on ``comb`` conditions the first sampler, and ``comb`` conditions
    the second sampler directly. The group ``Ctl`` holds ``cn``, ``comb`` (a prompt node) and
    ``cn2``."""
    doc = txt2img()
    doc["img"] = node("LoadImage", {"image": "pose.png"})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "cn.safetensors"})
    doc["cn"] = control_apply(["pos", 0], ["neg", 0], "cnl", ["img", "IMAGE"], 0.8)
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = ksampler(["up", 0], 0.45, ["comb", 0], ["neg", 0])
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    doc["pos2"] = node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": "a red fox"})
    doc["comb"] = node("ConditioningCombine", {"conditioning_1": ["cn", 0], "conditioning_2": ["pos2", 0]})
    doc["cnl2"] = node("ControlNetLoader", {"control_net_name": "depth.safetensors"})
    doc["img2"] = node("LoadImage", {"image": "depth.png"})
    doc["cn2"] = control_apply(["comb", 0], ["cn", 1], "cnl2", ["img2", "IMAGE"], 0.6)
    doc["sampler"]["inputs"]["positive"] = ["cn2", 0]
    doc["sampler"]["inputs"]["negative"] = ["cn2", 1]
    return group(doc, "Ctl", "cn", "comb", "cn2")


def image_two_parts() -> dict:
    """An image-to-image pass with a ControlNet on the same image: the loaded image ``img``
    (a cockpit reference, so it does not steer) feeds ``cn`` (control; it also conditions the
    second sampler ``s2`` outside the group) and ``enc`` (encoded into the sampler). The
    group ``G`` holds ``img``, ``cn``, ``enc``, the sampler and ``up``. ``img`` reaches both
    parts, the control one and the later one, and goes with the earlier."""
    doc = txt2img()
    doc["img"] = node("LoadImage", {"image": "photo.png"})
    doc["enc"] = node("VAEEncode", {"pixels": ["img", "IMAGE"], "vae": ["ckpt", "VAE"]})
    doc["sampler"]["inputs"]["latent_image"] = ["enc", 0]
    doc["sampler"]["inputs"]["denoise"] = 0.6
    del doc["latent"]
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "cn.safetensors"})
    doc["cn"] = control_apply(["pos", 0], ["neg", 0], "cnl", ["img", "IMAGE"], 0.8)
    doc["sampler"]["inputs"]["positive"] = ["cn", 0]
    doc["sampler"]["inputs"]["negative"] = ["cn", 1]
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = ksampler(["up", 0], 0.45, ["cn", 0], ["cn", 1])
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return group(doc, "G", "img", "cn", "enc", "sampler", "up")


def plumbing_upscale() -> dict:
    """The first sampler's latent goes through a plumbing node ``up`` (a latent upscale) into
    a decode ``d1`` whose image feeds a tile ControlNet of the second pass; the second sampler
    samples from ``up``. The group ``G`` holds the sampler and ``d1``; rule 9 adds ``up``.
    ``up`` links out of the group, so it does not move, but it passes the sampler's level on
    to ``d1``, which is of the control zone."""
    doc = txt2img()
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5}, ui_role="plumbing")
    doc["d1"] = node("VAEDecode", {"samples": ["up", 0], "vae": ["ckpt", "VAE"]})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "tile.safetensors"})
    doc["cna"] = control_apply(["pos", 0], ["neg", 0], "cnl", ["d1", 0], 0.6)
    doc["s2"] = ksampler(["up", 0], 0.4, ["cna", 0], ["cna", 1])
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return group(doc, "G", "sampler", "d1")


def plumbing_cond_fork() -> dict:
    """A ControlNet ``cn`` whose only link goes to ``comb`` (a plumbing conditioning combine);
    ``comb`` conditions the sampler, in the group, and the second sampler ``s2``, outside. The
    group ``G`` holds ``cn`` and the sampler; rule 9 adds ``comb``. ``comb`` is in the last
    part but cannot move, because a link of its leaves the group: ``cn`` stays ahead."""
    doc = txt2img()
    doc["img"] = node("LoadImage", {"image": "pose.png"})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "cn.safetensors"})
    doc["cn"] = control_apply(["pos", 0], ["neg", 0], "cnl", ["img", "IMAGE"], 0.8)
    doc["pos2"] = node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": "a red fox"})
    doc["comb"] = node("ConditioningCombine", {"conditioning_1": ["cn", 0], "conditioning_2": ["pos2", 0]}, ui_role="plumbing")
    doc["sampler"]["inputs"]["positive"] = ["comb", 0]
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    doc["s2"] = ksampler(["up", 0], 0.45, ["comb", 0], ["neg", 0])
    doc["decode"]["inputs"]["samples"] = ["s2", 0]
    return group(doc, "G", "cn", "sampler")


def plan_of(doc: dict):
    """The canvas graph, the annotate plan and canvas key -> authoring id."""
    canvas, authoring = compiled(copy.deepcopy(doc))
    plan = annotate(canvas, DEFS, authoring)
    names = {canvas_key: author_id for author_id, canvas_key in authoring.ids.items()}
    return Graph.of(canvas, plan.nodes, DEFS), plan, names


def author_groups(plan) -> list:
    """The author's groups layout may cut: keyed ``group:``, not a cockpit frame."""
    return [g for g in plan.groups if g.key.startswith("group:") and g.zone is not Zone.COCKPIT]


def backward_links(graph: Graph, keys: list[str]) -> list[tuple[str, str, int, int]]:
    """``(origin, target, origin's part, target's part)`` for each link between two of ``keys``
    that runs from a later part of :func:`segments` to an earlier one."""
    part = {key: s for s, run in enumerate(segments(graph, keys)) for key in run}
    return [(a, b, part[a], part[b]) for _, a, b in graph.edges if a in part and b in part and part[a] > part[b]]


def cut_of(doc: dict, name: str) -> list[list[str]]:
    """The segments of the author's group ``name``, each as sorted authoring ids."""
    graph, plan, names = plan_of(doc)
    members = next(g.members for g in plan.groups if g.key == f"group:{name}")
    return [sorted(names[key] for key in run) for run in segments(graph, list(members))]


def frame_of(out, key: str) -> Rect:
    """The group frame around the node ``key`` (an authoring id): the smallest that holds its
    position."""
    node_ = next(n for n in out.canvas.nodes if n.key == str(out.authoring.ids[key]))
    x, y = node_.pos
    frames = [g.bounding for g in out.canvas.groups if g.bounding.contains_point(x, y)]
    return min(frames, key=lambda r: r.w * r.h)


class FedMemberCompiles(unittest.TestCase):
    def check(self, doc: dict) -> None:
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                plan = annotate(instantiate(out.authoring), DEFS, out.authoring)
                self.assertEqual([f"{f.code.value}: {f.message}" for f in out.report.findings if f.severity.value == "error"], [])
                self.assertEqual(stack_faults(out, plan), [])

    def test_two_controlnets(self):
        self.check(two_controlnets())

    def test_latent_tile_preview(self):
        self.check(latent_tile_preview())

    def test_conditioning_mix(self):
        self.check(conditioning_mix())

    def test_first_pass(self):
        self.check(first_pass())

    def test_image_two_parts(self):
        self.check(image_two_parts())

    def test_plumbing_upscale(self):
        self.check(plumbing_upscale())

    def test_plumbing_cond_fork(self):
        self.check(plumbing_cond_fork())


class FedMemberSegments(unittest.TestCase):
    def test_decode_stays_with_the_sampler_feeding_it(self):
        # d1 is of the control zone, fed by the sampler: its cut level is the sampler's. cn1
        # feeds the sampler and a node outside the group, so it stays in a control part ahead.
        self.assertEqual(cut_of(two_controlnets(), "Base"), [["cn1"], ["d1", "sampler"]])

    def test_preview_decode_moves_with_the_sampler(self):
        self.assertEqual(cut_of(latent_tile_preview(), "Hi"), [["d1", "p1", "sampler"]])

    def test_combine_stays_with_the_controlnet_feeding_it(self):
        self.assertEqual(cut_of(conditioning_mix(), "Ctl"), [["cn", "cn2", "comb"]])

    def test_first_pass_is_not_cut(self):
        # d1 and sc are fed by the sampler, so their cut level is the sampler's: one segment.
        self.assertEqual(cut_of(first_pass(), "Pass 1"), [["d1", "sampler", "sc"]])

    def test_cockpit_member_goes_with_the_earliest_part_it_reaches(self):
        # img reaches cn (control) and enc (a later level): it is no steering member and
        # goes with cn. Of the latest level, enc and the sampler stay in the last part.
        self.assertEqual(cut_of(image_two_parts(), "G"), [["cn", "img"], ["enc", "sampler", "up"]])

    def test_plumbing_passes_the_level_on(self):
        # up links out of the group, so it does not move; it carries the sampler's level on
        # to d1, which is then of the latest level and stays in the one segment.
        self.assertEqual(cut_of(plumbing_upscale(), "G"), [["d1", "sampler", "up"]])

    def test_plumbing_that_links_out_does_not_move_its_feeder(self):
        # comb is in the last part but a link of its leaves the group, so cn, whose only link
        # goes to comb, stays ahead.
        self.assertEqual(cut_of(plumbing_cond_fork(), "G"), [["cn"], ["comb", "sampler"]])

    def test_last_part_is_set_by_steering_members_of_the_latest_level_only(self):
        """A member of the latest cut level that does not steer is not counted with the steering
        members there. Here ``N`` (plumbing, a stage zone, a link out of the segment) cannot move,
        so ``A``, whose only link goes to it, stays in a part ahead; counting ``N`` as one of the
        latest members would put ``A`` in the last part and leave one segment. The graph is built
        by hand, with no link running back, so an invariant check cannot tell the two cuts apart."""
        notes = {
            "A": NodeNote(zone=Zone.CONTROL, role=Role.PIPELINE),
            "S": NodeNote(zone=Zone.SAMPLING, role=Role.PIPELINE),
            "N": NodeNote(zone=Zone.STAGE, role=Role.PLUMBING),
            "O": NodeNote(zone=Zone.OUTPUT, role=Role.PIPELINE),
        }
        graph = Graph(nodes={key: None for key in notes}, facts={}, notes=notes, edges=((1, "A", "N"), (2, "S", "N"), (3, "N", "O")))
        self.assertEqual(_by_zone(graph, ["A", "S", "N"]), [["A"], ["S", "N"]])
        # Within a part the members keep the order they were given; the parts and what is in them stay.
        reversed_cut = _by_zone(graph, ["N", "S", "A"])
        self.assertEqual([sorted(part) for part in reversed_cut], [["A"], ["N", "S"]])

    def test_first_pass_is_one_frame(self):
        doc = first_pass()
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                self.assertEqual(frame_of(out, "d1"), frame_of(out, "sampler"))
                self.assertEqual(frame_of(out, "sc"), frame_of(out, "sampler"))

    def test_parts_run_left_to_right_along_the_flow(self):
        # cn1 feeds the sampler: its part starts no further right than the sampler's.
        doc = two_controlnets()
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
                early, late = frame_of(out, "cn1"), frame_of(out, "sampler")
                self.assertNotEqual(early, late)
                self.assertEqual(frame_of(out, "d1"), late)
                self.assertLessEqual(early.x, late.x)


class NoLinkRunsBack(unittest.TestCase):
    def check(self, doc: dict) -> int:
        """Asserts the invariant for every author's group of ``doc``; the number of groups."""
        graph, plan, _ = plan_of(doc)
        groups = author_groups(plan)
        for g in groups:
            with self.subTest(group=g.key):
                self.assertEqual(backward_links(graph, list(g.members)), [])
        return len(groups)

    def test_hand_written(self):
        for doc in (two_controlnets(), latent_tile_preview(), conditioning_mix(), first_pass(), image_two_parts(), plumbing_upscale(), plumbing_cond_fork()):
            self.assertEqual(self.check(doc), 1)

    def test_generator_entries(self):
        for entry in (wide_graph_scatter, wide_graph_outputs, wide_graph_across, wide_graph_fed):
            checked = 0
            for seed in SEEDS:
                with self.subTest(entry=entry.__name__, seed=seed):
                    checked += self.check(entry(seed))
            self.assertGreater(checked, 0, entry.__name__)


ZONES = (Zone.MODEL, Zone.PROMPT, Zone.CONTROL, Zone.SAMPLING, Zone.STAGE, Zone.POSTPROCESS, Zone.OUTPUT, Zone.COCKPIT)
GRAPHS = 2000


def random_graph(rng: random.Random) -> tuple[Graph, list[str]]:
    """A random acyclic graph and the members of a group in it. Members and other nodes get
    any zone, the cockpit included, and a fifth of them are plumbing; a link runs from an
    earlier node to a later one of a random topological order, which is not the order of the
    members."""
    inside, outside = rng.randint(2, 8), rng.randint(0, 4)
    keys = [str(i) for i in range(1, inside + outside + 1)]
    rng.shuffle(keys)
    order = keys[:]
    rng.shuffle(order)
    rank = {key: i for i, key in enumerate(order)}
    pairs = [(a, b) for a in keys for b in keys if rank[a] < rank[b] and rng.random() < 0.3]
    notes = {key: NodeNote(zone=rng.choice(ZONES), role=Role.PLUMBING if rng.random() < 0.2 else Role.PIPELINE) for key in keys}
    graph = Graph(nodes={key: None for key in keys}, facts={}, notes=notes, edges=tuple((i, a, b) for i, (a, b) in enumerate(pairs, 1)))
    return graph, keys[:inside]


def describe(graph: Graph, members: list[str]) -> str:
    cells = ", ".join(f"{key}:{graph.notes[key].zone.value}{'/plumbing' if graph.notes[key].role is Role.PLUMBING else ''}" for key in members)
    return f"members [{cells}], links {' '.join(f'{a}->{b}' for _, a, b in graph.edges)}"


class SegmentsOfRandomGraphs(unittest.TestCase):
    """:func:`cwc.layout.inner.segments` on random acyclic graphs built straight as a
    :class:`Graph`, with members of every zone and with members that do not steer (plumbing,
    the cockpit), which the generator entries never put in an author's group."""

    def test_parts(self):
        rng = random.Random(11)
        for _ in range(GRAPHS):
            graph, members = random_graph(rng)
            where = describe(graph, members)
            parts = segments(graph, members)
            self.assertEqual(sorted(key for part in parts for key in part), sorted(members), where)
            self.assertTrue(all(parts), where)
            part_of = {key: i for i, part in enumerate(parts) for key in part}
            back = [(a, b) for _, a, b in graph.edges if a in part_of and b in part_of and part_of[a] > part_of[b]]
            self.assertEqual(back, [], f"{where}, parts {parts}")
            self.assertEqual(segments(graph, members), parts, where)

    def test_parts_of_one_run_never_fall_in_the_flow(self):
        rng = random.Random(11)
        for _ in range(GRAPHS):
            graph, members = random_graph(rng)
            for run in _along_flow(graph, members):
                parts = _by_zone(graph, run)
                levels = [flow_level(segment_zone(graph, part)) for part in parts]
                self.assertEqual(levels, sorted(levels), f"{describe(graph, members)}, run {run}, parts {parts}")
                self.assertEqual(_by_zone(graph, run), parts)


if __name__ == "__main__":
    unittest.main()
