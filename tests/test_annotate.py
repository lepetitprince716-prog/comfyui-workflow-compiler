"""annotate in compile mode and tidy with regrouping: the nine first-match rules."""

import copy
import random
import re
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, backward_group_links, compiled, node, txt2img

from cwc.annotate import annotate, apply_notes
from cwc.annotate.graph import group_ranks
from cwc.annotate.palette import node_colours, zone_class
from cwc.annotate.types import Annotations, CockpitSlot, Source, Zone
from cwc.canvas.model import Canvas, NodeMode
from cwc.normalize.types import Role


def plan(doc):
    canvas, authoring = compiled(doc)
    return canvas, authoring, annotate(canvas, DEFS, authoring)


def group_of(annotations: Annotations, key: str):
    for group in annotations.groups:
        if key in group.members:
            return group
    raise AssertionError(f"node {key} is in no group")


class Case(unittest.TestCase):
    def build(self, doc):
        self.canvas, self.authoring, self.plan = plan(doc)
        self.assertIs(self.plan.source, Source.INFERRED)
        for group in self.plan.groups:
            self.assertNotRegex(group.name, r"\d", group)
            self.assertEqual(group.colour, zone_class(group.zone))
        self.assertEqual(backward_group_links(self.canvas, self.plan), [])
        return self.plan

    def key(self, author_id):
        return self.authoring.ids[author_id]

    def group(self, author_id):
        return group_of(self.plan, self.key(author_id))

    def note(self, author_id):
        return self.plan.nodes[self.key(author_id)]


def two_stage():
    """Base sampler, a preview of its decode, a latent upscale and a second sampler."""
    doc = txt2img()
    doc["preview"] = node("PreviewImage", {"images": ["decode", 0]})
    doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
    second = copy.deepcopy(doc["sampler"])
    second["inputs"]["latent_image"] = ["up", 0]
    second["inputs"]["denoise"] = 0.5
    doc["sampler2"] = second
    doc["decode2"] = node("VAEDecode", {"samples": ["sampler2", 0], "vae": ["ckpt", "VAE"]})
    doc["save"]["inputs"]["images"] = ["decode2", 0]
    return doc


def custom_sampling():
    """A sampler driven by a guider, a scheduler, a sampler picker and a noise source."""
    doc = txt2img()
    doc["noise"] = node("RandomNoise", {"noise_seed": 3})
    doc["guider"] = node("CFGGuider", {"model": ["ckpt", "MODEL"], "positive": ["pos", 0], "negative": ["neg", 0], "cfg": 4.0})
    doc["pick"] = node("KSamplerSelect", {"sampler_name": "euler"})
    doc["sched"] = node("BasicScheduler", {"model": ["ckpt", "MODEL"], "scheduler": "normal", "steps": 20, "denoise": 1.0})
    doc["sampler"] = node(
        "SamplerCustomAdvanced",
        {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["pick", 0], "sigmas": ["sched", 0], "latent_image": ["latent", 0]},
    )
    return doc


def controlnet(shared_reference=False):
    doc = txt2img()
    doc["image"] = node("LoadImage", {"image": "pose.png"})
    doc["edges"] = node("Canny", {"image": ["image", "IMAGE"], "low_threshold": 0.4, "high_threshold": 0.8})
    doc["cnl"] = node("ControlNetLoader", {"control_net_name": "cn.safetensors"})
    doc["cn"] = node(
        "ControlNetApplyAdvanced",
        {
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "control_net": ["cnl", 0],
            "image": ["edges", 0],
            "strength": 0.8,
            "start_percent": 0.0,
            "end_percent": 1.0,
        },
    )
    doc["sampler"]["inputs"]["positive"] = ["cn", 0]
    doc["sampler"]["inputs"]["negative"] = ["cn", 1]
    if shared_reference:
        doc["encode"] = node("VAEEncode", {"pixels": ["image", "IMAGE"], "vae": ["ckpt", "VAE"]})
        doc["sampler"]["inputs"]["latent_image"] = ["encode", 0]
        del doc["latent"]
    return doc


class Rule1OptionalTest(Case):
    def test_one_group_per_connected_optional_chain(self):
        doc = txt2img()
        extras = {"ui_optional": True, "ui_group": "extras"}
        doc["lora_a"] = node("LoraLoader", {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "a.safetensors"}, **extras)
        doc["lora_b"] = node("LoraLoader", {"model": ["lora_a", "MODEL"], "clip": ["lora_a", "CLIP"], "lora_name": "b.safetensors"}, **extras)
        doc["upscale"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5}, ui_optional=True)
        doc["sampler"]["inputs"]["model"] = ["lora_b", "MODEL"]
        doc["pos"]["inputs"]["clip"] = ["lora_b", "CLIP"]
        doc["neg"]["inputs"]["clip"] = ["lora_b", "CLIP"]
        doc["save"]["inputs"]["images"] = ["upscale", 0]
        self.build(doc)
        loras = self.group("lora_a")
        self.assertEqual(loras.members, (self.key("lora_a"), self.key("lora_b")))
        self.assertTrue(loras.optional)
        self.assertIs(loras.zone, Zone.OPTIONAL)
        self.assertEqual(loras.name, "Extras")
        upscale = self.group("upscale")
        self.assertNotEqual(upscale.key, loras.key)
        self.assertEqual(upscale.name, "Optional post-processing")
        self.assertEqual(self.plan.counts.optional_modules, 2)
        self.assertTrue(self.note("lora_a").optional)
        self.assertIs(self.note("lora_a").zone, Zone.OPTIONAL)

    def test_tidy_regroup_reads_optional_from_bypass_mode(self):
        doc = txt2img()
        doc["upscale"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5})
        doc["save"]["inputs"]["images"] = ["upscale", 0]
        canvas, authoring = compiled(doc)
        upscale = authoring.ids["upscale"]
        canvas.node_map()[upscale].raw["mode"] = int(NodeMode.BYPASS)
        tidy = annotate(canvas, DEFS, None, regroup=True)
        self.assertIs(tidy.source, Source.INFERRED)
        self.assertTrue(group_of(tidy, upscale).optional)
        self.assertFalse(tidy.reserve_guide)


class Rule2ExplicitTest(Case):
    def test_ui_group_and_cockpit_role(self):
        doc = txt2img()
        doc["_workflow"] = {"format": 1, "groups": {"render": {"title": "03 Render it"}}}
        doc["sampler"]["_meta"] = {"ui_group": "render"}
        doc["decode"]["_meta"] = {"ui_group": "render"}
        doc["ckpt"]["_meta"] = {"ui_role": "cockpit"}
        self.build(doc)
        render = self.group("sampler")
        self.assertEqual(render.key, "group:render")
        self.assertEqual(render.members, (self.key("sampler"), self.key("decode")))
        self.assertEqual(render.name, "Render it")
        self.assertIs(render.zone, Zone.POSTPROCESS)  # the latest zone of its members
        self.assertIs(self.note("sampler").zone, Zone.SAMPLING)
        self.assertIs(self.note("decode").zone, Zone.POSTPROCESS)
        self.assertEqual(self.group("ckpt").key, "cockpit")
        self.assertIs(self.note("ckpt").role, Role.COCKPIT)


class Rule3OutputTest(Case):
    def test_final_output_and_preview_beside_a_sampler(self):
        self.build(two_stage())
        self.assertIs(self.group("save").zone, Zone.OUTPUT)
        self.assertEqual(self.group("preview").key, self.group("sampler").key)
        self.assertIs(self.note("preview").zone, Zone.SAMPLING)

    def test_preview_of_the_last_stage_is_an_output(self):
        doc = txt2img()
        doc["preview"] = node("PreviewImage", {"images": ["decode", 0]})
        self.build(doc)
        self.assertIs(self.group("preview").zone, Zone.OUTPUT)
        self.assertEqual(self.group("preview").key, self.group("save").key)


    def test_decode_feeding_only_a_preview_stays_with_its_sampler(self):
        self.build(two_stage())
        sampling = self.group("sampler")
        self.assertEqual(self.group("decode").key, sampling.key)
        self.assertIs(self.note("decode").zone, Zone.SAMPLING)
        self.assertIs(self.group("decode2").zone, Zone.POSTPROCESS)


class Rule4CockpitSourceTest(Case):
    def test_source_shared_by_two_groups_and_the_latent_source(self):
        doc = two_stage()
        doc["seed"] = node("PrimitiveInt", {"value": 7})
        doc["sampler"]["inputs"]["seed"] = ["seed", 0]
        doc["sampler2"]["inputs"]["seed"] = ["seed", 0]
        self.build(doc)
        self.assertEqual(self.group("seed").key, "cockpit")
        self.assertIs(self.note("seed").cockpit, CockpitSlot.SEED)
        self.assertIs(self.note("seed").role, Role.COCKPIT)
        self.assertFalse(self.note("seed").collapsed)  # rule 4 before rule 9
        self.assertIs(self.note("latent").cockpit, CockpitSlot.SIZE)

    def test_loader_feeding_many_groups_is_not_a_cockpit_source(self):
        self.build(txt2img())
        self.assertIs(self.group("ckpt").zone, Zone.MODEL)


class Rule5SamplingTest(Case):
    def test_anchor_takes_its_guider_scheduler_picker_and_noise(self):
        self.build(custom_sampling())
        group = self.group("sampler")
        self.assertIs(group.zone, Zone.SAMPLING)
        for author_id in ("guider", "sched", "pick", "noise"):
            self.assertEqual(self.group(author_id).key, group.key, author_id)

    def test_stages_ranked_in_topological_order(self):
        self.build(two_stage())
        first, second = self.group("sampler"), self.group("sampler2")
        self.assertNotEqual(first.key, second.key)
        self.assertEqual((first.name, second.name), ("Sampling", "Sampling"))
        self.assertLess(first.stage, second.stage)
        order = [group.key for group in self.plan.groups]
        self.assertLess(order.index(first.key), order.index(second.key))


class Rule6ModelTest(Case):
    def test_model_chain_and_asset_nodes(self):
        doc = txt2img()
        doc["shift"] = node("ModelSamplingSD3", {"model": ["ckpt", "MODEL"], "shift": 3.0})
        doc["lora"] = node("LoraLoaderModelOnly", {"model": ["shift", 0], "lora_name": "a.safetensors", "strength_model": 1.0})
        doc["skip"] = node("CLIPSetLastLayer", {"clip": ["ckpt", "CLIP"], "stop_at_clip_layer": -2})
        doc["sampler"]["inputs"]["model"] = ["lora", 0]
        doc["pos"]["inputs"]["clip"] = ["skip", 0]
        doc["neg"]["inputs"]["clip"] = ["skip", 0]
        self.build(doc)
        model = self.group("ckpt")
        self.assertIs(model.zone, Zone.MODEL)
        self.assertEqual(set(model.members), {self.key(a) for a in ("ckpt", "shift", "lora", "skip")})


class Rule7ControlTest(Case):
    def test_control_branch(self):
        self.build(controlnet())
        branch = self.group("cn")
        self.assertIs(branch.zone, Zone.CONTROL)
        self.assertEqual(set(branch.members), {self.key(a) for a in ("image", "edges", "cnl", "cn")})
        self.assertEqual(self.plan.counts.control_branches, 1)
        # with a control branch, the prompts stay in the prompt zone
        self.assertIs(self.group("pos").zone, Zone.PROMPT)
        self.assertEqual(self.note("pos").colour, "positive")

    def test_source_feeding_control_and_base_latent_goes_to_the_cockpit(self):
        self.build(controlnet(shared_reference=True))
        self.assertEqual(self.group("image").key, "cockpit")
        self.assertIs(self.note("image").cockpit, CockpitSlot.REFERENCE)
        self.assertIs(self.group("edges").zone, Zone.CONTROL)


class Rule8PromptAndPostTest(Case):
    def test_main_prompts_go_to_the_cockpit_with_colours_and_titles(self):
        self.build(txt2img())
        cockpit = self.group("pos")
        self.assertEqual(cockpit.key, "cockpit")
        self.assertEqual(cockpit.members, (self.key("pos"), self.key("neg"), self.key("latent")))
        self.assertEqual((self.note("pos").colour, self.note("neg").colour), ("positive", "negative"))
        self.assertEqual((self.note("pos").title, self.note("neg").title), ("Positive prompt", "Negative prompt"))
        self.assertIs(self.note("neg").cockpit, CockpitSlot.NEGATIVE)

    def test_conditioning_chain_stays_in_the_prompt_zone(self):
        doc = txt2img()
        doc["style"] = node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": "watercolour"})
        doc["mix"] = node("ConditioningCombine", {"conditioning_1": ["pos", 0], "conditioning_2": ["style", 0]})
        doc["sampler"]["inputs"]["positive"] = ["mix", 0]
        self.build(doc)
        self.assertIs(self.group("mix").zone, Zone.PROMPT)
        self.assertEqual(self.note("mix").colour, "positive")
        self.assertEqual(self.group("style").key, "cockpit")

    def test_post_processing_between_decode_and_output(self):
        doc = txt2img()
        doc["upscale"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5})
        doc["invert"] = node("ImageInvert", {"image": ["upscale", 0]})
        doc["save"]["inputs"]["images"] = ["invert", 0]
        self.build(doc)
        post = self.group("upscale")
        self.assertIs(post.zone, Zone.POSTPROCESS)
        self.assertEqual(post.members, (self.key("decode"), self.key("upscale"), self.key("invert")))
        self.assertEqual(post.name, "Post-processing")


class Rule9PlumbingTest(Case):
    def test_value_node_follows_its_consumer_and_collapses(self):
        doc = txt2img()
        doc["cfg"] = node("PrimitiveFloat", {"value": 4.5})
        doc["sampler"]["inputs"]["cfg"] = ["cfg", 0]
        self.build(doc)
        self.assertEqual(self.group("cfg").key, self.group("sampler").key)
        note = self.note("cfg")
        self.assertTrue(note.collapsed)
        self.assertIs(note.role, Role.PLUMBING)
        self.assertIs(note.zone, Zone.SAMPLING)

    def test_plumbing_never_joins_an_optional_group_it_is_not_part_of(self):
        doc = txt2img()
        doc["upscale"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5}, ui_optional=True)
        doc["scale"] = node("PrimitiveFloat", {"value": 1.5})
        doc["upscale"]["inputs"]["scale_by"] = ["scale", 0]
        doc["save"]["inputs"]["images"] = ["upscale", 0]
        self.build(doc)
        self.assertFalse(self.group("scale").optional)
        self.assertNotEqual(self.group("scale").key, self.group("upscale").key)
        self.assertIs(self.group("scale").zone, Zone.STAGE)


    def test_tidy_plumbing_before_an_optional_node_joins_its_producer(self):
        doc = txt2img()
        doc["upscale"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5})
        doc["save"]["inputs"]["images"] = ["upscale", 0]
        canvas, authoring = compiled(doc)
        decode, upscale = authoring.ids["decode"], authoring.ids["upscale"]
        reroute = insert_reroute(canvas, decode, upscale)
        canvas.node_map()[upscale].raw["mode"] = int(NodeMode.BYPASS)
        tidy = annotate(canvas, DEFS, None, regroup=True)
        self.assertTrue(group_of(tidy, upscale).optional)
        self.assertEqual(group_of(tidy, reroute).key, group_of(tidy, decode).key)
        self.assertFalse(tidy.nodes[reroute].optional)
        self.assertIs(tidy.nodes[reroute].role, Role.PLUMBING)
        self.assertEqual(backward_group_links(canvas, tidy), [])


def insert_reroute(canvas: Canvas, origin: str, target: str) -> str:
    """Put a frontend reroute node on the link from ``origin`` to ``target``; its key."""
    key = canvas.raw["last_node_id"] + 1
    link_id = canvas.raw["last_link_id"] + 1
    canvas.raw["last_node_id"], canvas.raw["last_link_id"] = key, link_id
    raw_link = next(raw for raw in canvas.raw["links"] if str(raw[1]) == origin and str(raw[3]) == target)
    old_id, kind = raw_link[0], raw_link[5]
    raw_link[3], raw_link[4] = key, 0
    canvas.raw["links"].append([link_id, key, 0, int(target), 0, kind])
    for raw in canvas.raw["nodes"]:
        if str(raw["id"]) == target:
            for slot in raw["inputs"]:
                if slot.get("link") == old_id:
                    slot["link"] = link_id
    canvas.raw["nodes"].append(
        {
            "id": key,
            "type": "Reroute",
            "pos": [0, 0],
            "size": [75, 26],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [{"name": "", "type": "*", "link": old_id}],
            "outputs": [{"name": "", "type": kind, "links": [link_id]}],
            "properties": {"showOutputText": False, "horizontal": False},
        }
    )
    return str(key)


class Wiring:
    """Adds nodes and links to a compiled canvas. The node types are not in the bundled
    definitions, so annotate reads their slots from the canvas, as tidy does for a node
    pack it has no definitions for."""

    def __init__(self, canvas: Canvas):
        self.raw = canvas.raw

    def node(self, kind: str, inputs: list[tuple[str, str]], outputs: list[tuple[str, str]], mode: int = 0) -> str:
        key = self.raw["last_node_id"] + 1
        self.raw["last_node_id"] = key
        self.raw["nodes"].append(
            {
                "id": key,
                "type": kind,
                "pos": [0, 0],
                "size": [200, 100],
                "flags": {},
                "order": 0,
                "mode": mode,
                "inputs": [{"name": name, "type": kind_, "link": None} for name, kind_ in inputs],
                "outputs": [{"name": name, "type": kind_, "links": []} for name, kind_ in outputs],
                "properties": {},
            }
        )
        return str(key)

    def _raw_node(self, key: str) -> dict:
        return next(raw for raw in self.raw["nodes"] if str(raw["id"]) == key)

    def link(self, origin: str, origin_slot: int, target: str, input_name: str) -> None:
        """Link ``origin``'s output ``origin_slot`` into ``target``'s input ``input_name``,
        replacing the link that input had."""
        target_raw, origin_raw = self._raw_node(target), self._raw_node(origin)
        slot_index, slot = next((i, s) for i, s in enumerate(target_raw["inputs"]) if s["name"] == input_name)
        old = slot.get("link")
        if old is not None:
            old_raw = next(raw for raw in self.raw["links"] if raw[0] == old)
            self.raw["links"].remove(old_raw)
            for out in self._raw_node(str(old_raw[1]))["outputs"]:
                if out.get("links") and old in out["links"]:
                    out["links"].remove(old)
        link_id = self.raw["last_link_id"] + 1
        self.raw["last_link_id"] = link_id
        kind = origin_raw["outputs"][origin_slot]["type"]
        self.raw["links"].append([link_id, int(origin), origin_slot, int(target), slot_index, kind])
        slot["link"] = link_id
        out = origin_raw["outputs"][origin_slot]
        out["links"] = [*(out.get("links") or []), link_id]


class ZoneFollowsDataFlowTest(unittest.TestCase):
    """Shapes whose zone used to run against the data flow (a link from a later group
    into an earlier one), in tidy with ``--regroup``."""

    def test_asset_step_with_a_text_output_joins_the_model_stack(self):
        # A model-and-clip step that also puts out text (trigger words, say), whose model
        # output reaches no sampler: only an optional patch, or a refiner after the decode.
        # Its clip output feeds the prompts, so it belongs before them, in the model stack.
        for consumer in ("optional", "post"):
            with self.subTest(consumer=consumer):
                canvas, authoring = compiled(txt2img())
                ids = authoring.ids
                wiring = Wiring(canvas)
                step = wiring.node(
                    "SyntheticAssetStep",
                    [("model", "MODEL"), ("clip", "CLIP")],
                    [("MODEL", "MODEL"), ("CLIP", "CLIP"), ("text", "STRING")],
                )
                wiring.link(ids["ckpt"], 0, step, "model")
                wiring.link(ids["ckpt"], 1, step, "clip")
                wiring.link(step, 1, ids["pos"], "clip")
                wiring.link(step, 1, ids["neg"], "clip")
                if consumer == "optional":
                    patch = wiring.node("SyntheticModelPatch", [("model", "MODEL")], [("MODEL", "MODEL")], int(NodeMode.BYPASS))
                    wiring.link(step, 0, patch, "model")
                else:
                    refiner = wiring.node("SyntheticImageRefiner", [("image", "IMAGE"), ("model", "MODEL")], [("IMAGE", "IMAGE")])
                    wiring.link(ids["decode"], 0, refiner, "image")
                    wiring.link(step, 0, refiner, "model")
                    wiring.link(refiner, 0, ids["save"], "images")
                tidy = annotate(canvas, DEFS, None, regroup=True)
                self.assertIs(tidy.nodes[step].zone, Zone.MODEL)
                self.assertEqual(group_of(tidy, step).key, group_of(tidy, ids["ckpt"]).key)
                if consumer == "post":
                    self.assertIs(tidy.nodes[refiner].zone, Zone.POSTPROCESS)
                self.assertEqual(backward_group_links(canvas, tidy), [])

    def test_conditioning_unpacked_after_the_decode_stays_in_post_processing(self):
        # A refiner after the decode passes a pipe on; a node unpacks it (models and
        # conditioning) for a second refiner. The unpacker's input comes from
        # post-processing and its conditioning reaches no sampler, so it is not a prompt.
        canvas, authoring = compiled(txt2img())
        ids = authoring.ids
        wiring = Wiring(canvas)
        carried = [("model", "MODEL"), ("clip", "CLIP"), ("vae", "VAE"), ("positive", "CONDITIONING"), ("negative", "CONDITIONING")]
        first = wiring.node("SyntheticPipeRefiner", [("image", "IMAGE"), *carried], [("IMAGE", "IMAGE"), ("pipe", "SYNTH_PIPE")])
        unpack = wiring.node("SyntheticPipeUnpack", [("pipe", "SYNTH_PIPE")], [(name.upper(), kind) for name, kind in carried])
        second = wiring.node("SyntheticPipeRefiner", [("image", "IMAGE"), *carried], [("IMAGE", "IMAGE"), ("pipe", "SYNTH_PIPE")])
        wiring.link(ids["decode"], 0, first, "image")
        for name, origin, slot in (("model", "ckpt", 0), ("clip", "ckpt", 1), ("vae", "ckpt", 2), ("positive", "pos", 0), ("negative", "neg", 0)):
            wiring.link(ids[origin], slot, first, name)
        wiring.link(first, 1, unpack, "pipe")
        wiring.link(first, 0, second, "image")
        for slot, (name, _) in enumerate(carried):
            wiring.link(unpack, slot, second, name)
        wiring.link(second, 0, ids["save"], "images")
        tidy = annotate(canvas, DEFS, None, regroup=True)
        self.assertIs(tidy.nodes[unpack].zone, Zone.POSTPROCESS)
        self.assertEqual(group_of(tidy, unpack).key, group_of(tidy, first).key)
        self.assertEqual(group_of(tidy, second).key, group_of(tidy, first).key)
        self.assertEqual(backward_group_links(canvas, tidy), [])
        # the main prompts, which reach the sampler, are still prompts
        self.assertIs(tidy.nodes[ids["pos"]].zone, Zone.COCKPIT)


class GroupRanksTest(unittest.TestCase):
    """``group_ranks`` follows the data flow through a group loop, with no fallback to key order."""

    def test_groups_after_a_loop_follow_the_data_flow(self):
        # models -> extras -> models is a loop; models feeds stage:7, which feeds sampling:13.
        # Key order would put sampling:13 before stage:7.
        members = {"models": ["1", "3"], "extras": ["2"], "stage:7": ["5"], "sampling:13": ["6"]}
        pairs = [("1", "2"), ("2", "3"), ("3", "5"), ("5", "6")]
        order = {"1": 0, "2": 1, "3": 2, "5": 3, "6": 4}
        ranks = group_ranks(members, pairs, order)
        self.assertLess(ranks["models"], ranks["extras"])  # the loop by the earliest member
        self.assertLess(ranks["models"], ranks["stage:7"])
        self.assertLess(ranks["stage:7"], ranks["sampling:13"])

    def test_a_loop_is_ordered_by_execution_order_not_by_key(self):
        members = {"a": ["2"], "b": ["1", "3"]}
        pairs = [("1", "2"), ("2", "3")]
        ranks = group_ranks(members, pairs, {"1": 0, "2": 1, "3": 2})
        self.assertLess(ranks["b"], ranks["a"])


class StageFallbackTest(Case):
    def test_unclaimed_nodes_become_stage_groups_by_depth(self):
        doc = {
            "image": node("LoadImage", {"image": "a.png"}),
            "invert": node("ImageInvert", {"image": ["image", "IMAGE"]}),
            "save": node("SaveImage", {"images": ["invert", 0], "filename_prefix": "out"}),
        }
        self.build(doc)
        self.assertEqual(self.group("image").key, "stage:0")
        self.assertEqual(self.group("invert").key, "stage:1")
        self.assertEqual(self.group("image").name, "Stage")
        self.assertLess(self.group("image").stage, self.group("invert").stage)
        self.assertIs(self.group("save").zone, Zone.OUTPUT)


class FirstMatchTest(Case):
    def test_earlier_rule_wins(self):
        doc = txt2img()
        doc["lora"] = node("LoraLoaderModelOnly", {"model": ["ckpt", "MODEL"], "lora_name": "a.safetensors", "strength_model": 1.0}, ui_optional=True)
        doc["sampler"]["inputs"]["model"] = ["lora", 0]
        doc["sampler"]["_meta"] = {"ui_group": "mine"}
        self.build(doc)
        self.assertIs(self.note("lora").zone, Zone.OPTIONAL)  # rule 1 over rule 6
        self.assertEqual(self.group("sampler").key, "group:mine")  # rule 2 over rule 5
        self.assertIs(self.note("sampler").zone, Zone.SAMPLING)  # its own zone without intents


class UnknownTypeTest(unittest.TestCase):
    def test_unknown_type_reads_canvas_slots_and_is_no_output(self):
        canvas, authoring = compiled(txt2img())
        nodes = canvas.node_map()
        nodes[authoring.ids["sampler"]].raw["type"] = "SamplerFromAnUninstalledPack"
        nodes[authoring.ids["save"]].raw["type"] = "SaverFromAnUninstalledPack"
        result = annotate(canvas, DEFS, authoring)
        self.assertIs(result.nodes[authoring.ids["sampler"]].zone, Zone.SAMPLING)
        self.assertIsNot(result.nodes[authoring.ids["save"]].zone, Zone.OUTPUT)


class DeterminismTest(unittest.TestCase):
    def test_same_input_same_plan_and_node_order_does_not_matter(self):
        doc = controlnet(shared_reference=True)
        doc["cfg"] = node("PrimitiveFloat", {"value": 4.5})
        doc["sampler"]["inputs"]["cfg"] = ["cfg", 0]
        canvas, authoring = compiled(doc)
        first = annotate(canvas, DEFS, authoring)
        self.assertEqual(first, annotate(Canvas(copy.deepcopy(canvas.raw)), DEFS, authoring))
        for seed in range(5):
            shuffled = copy.deepcopy(canvas.raw)
            random.Random(seed).shuffle(shuffled["nodes"])
            self.assertEqual(annotate(Canvas(shuffled), DEFS, authoring), first)
            self.assertEqual(annotate(Canvas(shuffled), DEFS, None, regroup=True), annotate(canvas, DEFS, None, regroup=True))


class ApplyNotesTest(unittest.TestCase):
    def test_writes_collapsed_titles_and_colours_only(self):
        doc = txt2img()
        doc["cfg"] = node("PrimitiveFloat", {"value": 4.5})
        doc["sampler"]["inputs"]["cfg"] = ["cfg", 0]
        canvas, authoring = compiled(doc)
        before = copy.deepcopy(canvas.raw)
        apply_notes(canvas, annotate(canvas, DEFS, authoring))
        nodes = canvas.node_map()
        pos, cfg = nodes[authoring.ids["pos"]], nodes[authoring.ids["cfg"]]
        self.assertTrue(cfg.collapsed)
        self.assertEqual(pos.title, "Positive prompt")
        colours = node_colours("positive")
        self.assertEqual((pos.raw["color"], pos.raw["bgcolor"]), (colours.color, colours.bgcolor))
        self.assertEqual(nodes[authoring.ids["neg"]].raw["bgcolor"], node_colours("negative").bgcolor)
        allowed = {"flags", "title", "color", "bgcolor"}
        for old, new in zip(before["nodes"], canvas.raw["nodes"]):
            changed = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
            self.assertLessEqual(changed, allowed)
        self.assertEqual(before["links"], canvas.raw["links"])

    def test_expanding_removes_the_flag(self):
        canvas, authoring = compiled(txt2img())
        sampler = canvas.node_map()[authoring.ids["sampler"]]
        sampler.raw["flags"] = {"collapsed": True}
        apply_notes(canvas, annotate(canvas, DEFS, authoring))
        self.assertEqual(sampler.raw["flags"], {})


class NamesTest(unittest.TestCase):
    def test_no_numbers_in_names(self):
        for doc in (txt2img(), two_stage(), custom_sampling(), controlnet(), controlnet(True)):
            canvas, authoring = compiled(doc)
            for group in annotate(canvas, DEFS, authoring).groups:
                self.assertIsNone(re.search(r"\d", group.name), group)


if __name__ == "__main__":
    unittest.main()
