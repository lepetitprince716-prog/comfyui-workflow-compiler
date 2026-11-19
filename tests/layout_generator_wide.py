"""Seeded random authoring graphs that cover what ``tests.layout_generator`` does not draw.

``wide_graph(seed)`` is a text-to-image base with a random set of features switched on, each
drawn on its own from ``random.Random(seed)``; ``wide_features(seed)`` names the features the
seed switched on, in ``FEATURES`` order. ``build_wide(seed, features)`` builds the seed's graph
with only ``features`` on: each feature's own choices come from a generator of its own, seeded
by the seed and the feature's name, so switching one feature off leaves the others' choices as
they were (a failing seed can be reduced to the features it needs). ``tests.layout_generator`` writes ``ui_group`` only on
optional nodes, always ends an optional upscale at the save node, and uses one empty latent
node with one positive and one negative prompt; the features here fill those gaps:

- ``group_cockpit``: ``ui_group`` on nodes the cockpit lifts (both prompts, or the empty latent).
- ``group_model``: ``ui_group`` on required nodes of the model chain.
- ``group_post``: ``ui_group`` on required post-processing nodes.
- ``group_interleaved``: two explicit groups whose nodes alternate along one chain (A, B, A, B),
  on the latent or on the model.
- ``post_sandwich``: required post-processing, then an optional module, then required again.
- ``model_alternating``: required and optional LoRA loaders alternate along the model chain.
- ``optional_then_required``: an optional module right after the decode, a required node after it.
- ``loras``: one to three LoRA loaders, each optional or required at random.
- ``type_title``: an empty latent node whose definition has no display name.
- ``two_positive``: two positive prompts merged by a combine node into the sampler.
- ``shared_ints``: two integer value nodes of one type feed the latent's width and height and a
  resize before the save.
- ``control``: a control branch on the conditioning, optional or required.
- ``multistage``: two or three sampling stages, each earlier stage with its decode and preview.
- ``stage_tap``: an early stage's decode feeds its preview and a blend before the final output.
- ``custom_shared``: custom sampling whose noise, sampler choice and scheduler nodes serve every stage.
- ``optional_tail``: an optional upscale right before the save (what the old generator draws).

A feature that needs another one switches it on (``stage_tap``, ``custom_shared``, ``group_outputs``
and ``optional_stage`` need ``multistage``), so every draw is a valid graph: no combination is skipped. A node that serves
only optional nodes is itself optional (the bypass closure).

``wide_graph_across(seed)`` is ``wide_graph(seed)`` with one more feature on, outside the draw
(``EXTRA``), so ``wide_graph`` and ``wide_features`` stay as they were for every seed:

- ``group_across``: one author group holding a node from each end of the data flow, an upstream
  one (the model chain, a prompt or a sampler) and a downstream one (the final decode, a
  required post-processing node or the save), so the group is cut and its segments lie in
  different zones.
  Only required nodes are taken (a required sampler when ``optional_stage`` made one optional):
  verify rejects a group that mixes optional and required nodes (error E7).

Two more entries, each ``wide_graph(seed)`` with features outside the draw (``EXTRA``):

- ``wide_graph_outputs(seed)`` adds ``group_outputs`` (so it always has several stages) and, with
  chance 0.3 from ``random.Random(f"{seed}/outputs_entry")``, ``optional_stage``:
  - ``group_outputs``: one author group ``outputs`` holding output nodes of different stages: a
    non-empty subset of the stage previews, the final save (chance 0.75), a final preview
    (``preview_final``, chance 0.3), and with chance 0.3 one member that is no output node (an early
    decode, the final decode, a later sampler or a post-processing node; a pick that is optional or
    already in a group is dropped: verify rejects a group that mixes optional and required nodes,
    error E7).
    The group has at least two members: when the draw gives one, the save joins (a node another
    group already holds is not taken; ``preview_final`` is then added instead).
  - ``optional_stage``: one sampler after the first is optional, with the latent upscale that
    feeds it; its decode and preview stay required.
- ``wide_graph_scatter(seed)`` adds ``group_scatter``: one author group ``scatter`` holding two
  to four required nodes without a group, taken at random from the whole graph.

- ``wide_graph_fed(seed)`` adds ``stage_control``, ``cond_mix`` (each by a draw of the entry's own,
  at least one) and ``group_fed``:
  - ``stage_control`` (needs ``multistage``): an early stage's decode feeds a ControlNet apply, and
    the apply's outputs condition the next stage's sampler (through a guider of that stage's own
    under ``custom_shared``).
  - ``cond_mix``: a ControlNet apply on the prompts, a conditioning combine of its positive and a
    prompt of its own, and the combine's output conditioning a stage's sampler, directly or through
    a second ControlNet apply.
  - ``group_fed``: one author group ``fed`` of two to four required nodes without a group that
    feed each other: a node the two features add (or the decode they tap) and one to three of the
    nodes directly feeding it or directly fed by it. Such a node's own zone can be earlier than the
    zone of a member feeding it: the decode of an early stage is a control node, a combine is a
    prompt node.

``group_outputs``, ``group_scatter`` and ``group_fed`` run last, after ``group_across``, and take
only nodes no group holds yet.
"""

from __future__ import annotations

import random
from typing import Any

from tests import support  # noqa: F401
from tests.graphs import node, txt2img

FEATURES = (
    "group_cockpit",
    "group_model",
    "group_post",
    "group_interleaved",
    "post_sandwich",
    "model_alternating",
    "optional_then_required",
    "loras",
    "type_title",
    "two_positive",
    "shared_ints",
    "control",
    "multistage",
    "stage_tap",
    "custom_shared",
    "optional_tail",
)
# Features no seed draws; an entry of its own switches them on.
EXTRA = ("group_across", "group_outputs", "optional_stage", "group_scatter", "stage_control", "cond_mix", "group_fed")
# The chance each feature is drawn on: low enough that some seeds hold one or two features,
# so a failure class has a small witness.
P_FEATURE = 0.3
# Features that switch on another.
NEEDS = {
    "stage_tap": "multistage",
    "custom_shared": "multistage",
    "group_outputs": "multistage",
    "optional_stage": "multistage",
    "stage_control": "multistage",
}
# The chance ``wide_graph_outputs`` adds ``optional_stage``.
P_OPTIONAL_STAGE = 0.3
# The chance ``wide_graph_fed`` adds each of ``stage_control`` and ``cond_mix`` (one is always on).
P_FED_FEATURE = 0.5

_UP = {"upscale_method": "nearest-exact", "scale_by": 1.0}
_IMG_UP = {"upscale_method": "lanczos", "scale_by": 1.0}


def wide_features(seed: int) -> tuple[str, ...]:
    """The features ``wide_graph(seed)`` switches on, in ``FEATURES`` order."""
    rng = random.Random(seed)
    return closed(name for name in FEATURES if rng.random() < P_FEATURE)


def closed(features) -> tuple[str, ...]:
    """``features`` with the features they need, in ``FEATURES`` order, then ``EXTRA`` order."""
    on = set(features)
    on |= {NEEDS[name] for name in on if name in NEEDS}
    unknown = on - set(FEATURES) - set(EXTRA)
    if unknown:
        raise ValueError(f"unknown features: {sorted(unknown)}")
    return tuple(name for name in FEATURES + EXTRA if name in on)


def wide_graph(seed: int) -> dict[str, Any]:
    """The graph for ``seed``: an API graph with ``_meta`` authoring hints."""
    return build_wide(seed, wide_features(seed))


def wide_across_features(seed: int) -> tuple[str, ...]:
    """The features ``wide_graph_across(seed)`` switches on: the seed's draw and ``group_across``."""
    return closed(wide_features(seed) + ("group_across",))


def wide_graph_across(seed: int) -> dict[str, Any]:
    """``wide_graph(seed)`` with an author group across the data flow (``group_across``)."""
    return build_wide(seed, wide_across_features(seed))


def wide_outputs_features(seed: int) -> tuple[str, ...]:
    """The features ``wide_graph_outputs(seed)`` switches on: the seed's draw, ``group_outputs``
    and, by a draw of the entry's own, ``optional_stage``."""
    extra = ("group_outputs",)
    if random.Random(f"{seed}/outputs_entry").random() < P_OPTIONAL_STAGE:
        extra += ("optional_stage",)
    return closed(wide_features(seed) + extra)


def wide_graph_outputs(seed: int) -> dict[str, Any]:
    """``wide_graph(seed)`` with an author group of output nodes of different stages."""
    return build_wide(seed, wide_outputs_features(seed))


def wide_scatter_features(seed: int) -> tuple[str, ...]:
    """The features ``wide_graph_scatter(seed)`` switches on: the seed's draw and ``group_scatter``."""
    return closed(wide_features(seed) + ("group_scatter",))


def wide_graph_scatter(seed: int) -> dict[str, Any]:
    """``wide_graph(seed)`` with an author group of required nodes taken from anywhere."""
    return build_wide(seed, wide_scatter_features(seed))


def wide_fed_features(seed: int) -> tuple[str, ...]:
    """The features ``wide_graph_fed(seed)`` switches on: the seed's draw, ``stage_control`` and
    ``cond_mix`` by draws of the entry's own (at least one), and ``group_fed``."""
    rng = random.Random(f"{seed}/fed_entry")
    steering = ("stage_control", "cond_mix")
    extra = [name for name in steering if rng.random() < P_FED_FEATURE] or [rng.choice(steering)]
    return closed(wide_features(seed) + (*extra, "group_fed"))


def wide_graph_fed(seed: int) -> dict[str, Any]:
    """``wide_graph(seed)`` with nodes whose own zone is earlier than the zone of the node feeding
    them, and an author group of nodes that feed each other (``group_fed``)."""
    return build_wide(seed, wide_fed_features(seed))


def build_wide(seed: int, features) -> dict[str, Any]:
    """``seed``'s graph with exactly ``features`` on (and the features they need)."""
    on = frozenset(closed(features))

    def draw(name: str) -> random.Random:
        # One generator per choice, so a choice does not depend on which features are on.
        return random.Random(f"{seed}/{name}")

    doc = txt2img()

    def meta(optional: bool = False, group: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if optional:
            out["ui_optional"] = True
        if group:
            out["ui_group"] = group
        return out

    # The model chain: a pattern of LoRA loaders, "O" optional and "R" required.
    pattern = ""
    if "model_alternating" in on:
        pattern = draw("model_alternating").choice(("ROR", "ORO", "ROROR", "OROR", "RORO"))
    elif "loras" in on:
        loras = draw("loras")
        pattern = "".join(loras.choice("OR") for _ in range(loras.randint(1, 3)))
    model_group = "model_tweaks" if "group_model" in on else None
    if model_group and draw("group_model").random() < 0.5:
        doc["ckpt"]["_meta"] = {"ui_group": model_group}
    model, clip = ["ckpt", "MODEL"], ["ckpt", "CLIP"]
    for i, letter in enumerate(pattern):
        optional = letter == "O"
        group = model_group if not optional else draw(f"lora{i}").choice((None, f"lora_opt{i}"))
        doc[f"lora{i}"] = node(
            "LoraLoader",
            {"model": model, "clip": clip, "lora_name": f"l{i}.safetensors", "strength_model": 1.0, "strength_clip": 1.0},
            **meta(optional, group),
        )
        model, clip = [f"lora{i}", "MODEL"], [f"lora{i}", "CLIP"]
    if model_group:
        # A required model patch inside the explicit group, so the group holds a node even
        # when the chain has no required LoRA.
        doc["patch"] = node("ModelSamplingDiscrete", {"model": model, "sampling": "eps", "zsnr": False}, ui_group=model_group)
        model = ["patch", 0]
    interleave_on_model = draw("group_interleaved").random() < 0.5
    if "group_interleaved" in on and interleave_on_model:
        for k, name in enumerate(("weave_a", "weave_b", "weave_a", "weave_b")):
            doc[f"mw{k}"] = node("ModelSamplingDiscrete", {"model": model, "sampling": "eps", "zsnr": False}, ui_group=name)
            model = [f"mw{k}", 0]
    doc["pos"]["inputs"]["clip"] = clip
    doc["neg"]["inputs"]["clip"] = clip

    # The prompts.
    positive, negative = ["pos", 0], ["neg", 0]
    if "two_positive" in on:
        doc["pos2"] = node("CLIPTextEncode", {"clip": clip, "text": "a dog"})
        doc["both"] = node("ConditioningCombine", {"conditioning_1": ["pos", 0], "conditioning_2": ["pos2", 0]})
        positive = ["both", 0]
    if "group_cockpit" in on:
        if draw("group_cockpit").random() < 0.5:
            doc["pos"]["_meta"] = {"ui_group": "prompts"}
            doc["neg"]["_meta"] = {"ui_group": "prompts"}
        else:
            doc["latent"]["_meta"] = {"ui_group": "size"}
    if "control" in on:
        control = draw("control")
        optional = control.random() < 0.5
        flags = meta(optional, "control" if optional or control.random() < 0.5 else None)
        doc["img"] = node("LoadImage", {"image": "pose.png"}, **flags)
        doc["cnl"] = node("ControlNetLoader", {"control_net_name": "cn.safetensors"}, **flags)
        doc["cn"] = node(
            "ControlNetApplyAdvanced",
            {
                "positive": positive,
                "negative": negative,
                "control_net": ["cnl", 0],
                "image": ["img", "IMAGE"],
                "strength": 0.8,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            **flags,
        )
        positive, negative = ["cn", 0], ["cn", 1]

    # The latent.
    if "type_title" in on:
        doc["latent"]["class_type"] = "EmptySD3LatentImage"
    if "shared_ints" in on:
        doc["w"] = node("PrimitiveInt", {"value": 1024})
        doc["h"] = node("PrimitiveInt", {"value": 1024})
        doc["latent"]["inputs"]["width"] = ["w", 0]
        doc["latent"]["inputs"]["height"] = ["h", 0]
    latent = ["latent", 0]
    if "group_interleaved" in on and not interleave_on_model:
        for k, name in enumerate(("weave_a", "weave_b", "weave_a", "weave_b")):
            doc[f"lw{k}"] = node("LatentUpscaleBy", {"samples": latent, **_UP}, ui_group=name)
            latent = [f"lw{k}", 0]

    # The sampling stages.
    stages = draw("multistage").randint(2, 3) if "multistage" in on else 1
    custom = "custom_shared" in on
    if custom:
        doc["noise"] = node("RandomNoise", {"noise_seed": 3})
        doc["guider"] = node("CFGGuider", {"model": model, "positive": positive, "negative": negative, "cfg": 4.0})
        doc["pick"] = node("KSamplerSelect", {"sampler_name": "euler"})
        doc["sched"] = node("BasicScheduler", {"model": model, "scheduler": "normal", "steps": 20, "denoise": 1.0})
    ksampler = {key: (list(value) if isinstance(value, list) else value) for key, value in doc["sampler"]["inputs"].items()}
    ksampler.update(model=model, positive=positive, negative=negative)
    decodes = []
    for i in range(stages):
        key = "sampler" if i == 0 else f"sampler{i}"
        if custom:
            inputs = {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["pick", 0], "sigmas": ["sched", 0], "latent_image": latent}
            doc[key] = node("SamplerCustomAdvanced", inputs)
        else:
            inputs = dict(ksampler, latent_image=latent, denoise=1.0 if i == 0 else 0.5)
            doc[key] = node("KSampler", inputs)
        decode = "decode" if i == stages - 1 else f"decode_s{i}"
        doc[decode] = node("VAEDecode", {"samples": [key, 0], "vae": ["ckpt", "VAE"]})
        decodes.append(decode)
        if i < stages - 1:
            doc[f"preview{i}"] = node("PreviewImage", {"images": [decode, 0]})
            doc[f"up{i}"] = node("LatentUpscaleBy", {"samples": [key, 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
            latent = [f"up{i}", 0]
    if "optional_stage" in on:
        # The sampler and the one node that serves only it (the bypass closure); its decode and
        # preview stay required.
        i = draw("optional_stage").randint(1, stages - 1)
        for key in (f"sampler{i}", f"up{i - 1}"):
            doc[key]["_meta"] = {"ui_optional": True}

    # The nodes ``stage_control`` and ``cond_mix`` add or tap, bar loaders, images and prompts: the
    # seeds of ``group_fed``.
    steering: list[str] = []

    def sampler_key(stage: int) -> str:
        return "sampler" if stage == 0 else f"sampler{stage}"

    def stage_flags(stage: int) -> dict[str, Any]:
        # A node that serves only an optional sampler is optional (the bypass closure).
        return meta(bool(doc[sampler_key(stage)].get("_meta", {}).get("ui_optional")))

    def conditioning(stage: int) -> tuple[list, list]:
        inputs = doc[sampler_key(stage)]["inputs"]
        if custom:
            inputs = doc[inputs["guider"][0]]["inputs"]
        return list(inputs["positive"]), list(inputs["negative"])

    def condition(stage: int, positive: list, negative: list) -> None:
        inputs = doc[sampler_key(stage)]["inputs"]
        if custom:
            name = inputs["guider"][0]
            if sum(value["inputs"].get("guider") == [name, 0] for value in doc.values()) > 1:
                # The guider serves other stages too: this stage gets a guider of its own (one
                # that serves no stage would reach no output).
                doc[f"guider_s{stage}"] = node("CFGGuider", dict(doc[name]["inputs"]), **stage_flags(stage))
                inputs["guider"] = [f"guider_s{stage}", 0]
                name = f"guider_s{stage}"
            inputs = doc[name]["inputs"]
        inputs.update(positive=positive, negative=negative)

    def apply_control(key: str, loader: str, positive: list, negative: list, image: list, flags: dict[str, Any]) -> None:
        doc[loader] = node("ControlNetLoader", {"control_net_name": f"{loader}.safetensors"}, **flags)
        inputs = {
            "positive": positive,
            "negative": negative,
            "control_net": [loader, 0],
            "image": image,
            "strength": 0.6,
            "start_percent": 0.0,
            "end_percent": 1.0,
        }
        doc[key] = node("ControlNetApplyAdvanced", inputs, **flags)

    if "cond_mix" in on:
        mix = draw("cond_mix")
        stage = mix.randrange(stages)
        flags = stage_flags(stage)
        positive_in, negative_in = conditioning(stage)
        doc["mix_img"] = node("LoadImage", {"image": "mix.png"}, **flags)
        apply_control("mix_cn", "mix_cnl", positive_in, negative_in, ["mix_img", "IMAGE"], flags)
        doc["mix_pos"] = node("CLIPTextEncode", {"clip": clip, "text": "a red fox"}, **flags)
        doc["mix_comb"] = node("ConditioningCombine", {"conditioning_1": ["mix_cn", 0], "conditioning_2": ["mix_pos", 0]}, **flags)
        steering += ["mix_cn", "mix_comb"]
        if mix.random() < 0.5:
            condition(stage, ["mix_comb", 0], ["mix_cn", 1])
        else:
            doc["mix_img2"] = node("LoadImage", {"image": "mix2.png"}, **flags)
            apply_control("mix_cn2", "mix_cnl2", ["mix_comb", 0], ["mix_cn", 1], ["mix_img2", "IMAGE"], flags)
            condition(stage, ["mix_cn2", 0], ["mix_cn2", 1])
            steering.append("mix_cn2")
    if "stage_control" in on:
        # An early stage's decode is of the control zone (it feeds a ControlNet), though a sampler
        # feeds it.
        source = draw("stage_control").randrange(stages - 1)
        stage = source + 1
        flags = stage_flags(stage)
        positive_in, negative_in = conditioning(stage)
        apply_control("tile_cn", "tile_cnl", positive_in, negative_in, [f"decode_s{source}", 0], flags)
        condition(stage, ["tile_cn", 0], ["tile_cn", 1])
        steering += [f"decode_s{source}", "tile_cn"]
    if custom and steering:
        # A stage with a guider of its own can leave the shared guider to optional samplers only.
        for key in [k for k in doc if k.startswith("guider")]:
            users = [value for value in doc.values() if value["inputs"].get("guider") == [key, 0]]
            if users and all(value.get("_meta", {}).get("ui_optional") for value in users):
                doc[key]["_meta"] = {**doc[key].get("_meta", {}), "ui_optional": True}

    # Post-processing, as a chain of (kind, optional) from the final decode to the save:
    # "scale" an image upscale, "resize" the resize fed by the shared ints, "tap" the blend of
    # an early decode.
    chain: list[tuple[str, bool]] = []
    if "optional_then_required" in on:
        chain += [("scale", True), ("scale", False)]
    if "post_sandwich" in on:
        chain += [("scale", False), ("scale", True), ("scale", False)]
    if "group_post" in on and not any(not optional for _, optional in chain):
        chain.append(("scale", False))
    if "shared_ints" in on:
        chain.insert(draw("shared_ints").randint(0, len(chain)), ("resize", False))
    if "stage_tap" in on:
        tap = draw("stage_tap")
        chain.insert(tap.randint(0, len(chain)), ("tap", tap.random() < 0.5))
    if "optional_tail" in on:
        chain.append(("scale", True))
    post_group = "finish" if "group_post" in on else None
    image = ["decode", 0]
    for k, (kind, optional) in enumerate(chain):
        key = f"post{k}"
        flags = meta(optional, post_group if not optional else draw(f"post{k}").choice((None, f"post_opt{k}")))
        if kind == "scale":
            doc[key] = node("ImageScaleBy", {"image": image, **_IMG_UP}, **flags)
        elif kind == "resize":
            inputs = {"image": image, "upscale_method": "lanczos", "width": ["w", 0], "height": ["h", 0], "crop": "disabled"}
            doc[key] = node("ImageScale", inputs, **flags)
        else:
            inputs = {"image1": image, "image2": [decodes[0], 0], "blend_factor": 0.2, "blend_mode": "normal"}
            doc[key] = node("ImageBlend", inputs, **flags)
        image = [key, 0]
    doc["save"]["inputs"]["images"] = image

    if "group_across" in on:
        # Every upstream candidate reaches every downstream one: the final decode follows each
        # sampler, and the post chain and the save follow the final decode.
        def required(key: str) -> bool:
            return not doc[key].get("_meta", {}).get("ui_optional")

        upstream = ["ckpt", *(k for k in doc if k.startswith("lora") and required(k)), "pos", "neg"]
        upstream += [k for k in ("patch",) if k in doc] + [k for k in doc if k.startswith("sampler") and required(k)]
        downstream = ["decode", *(k for k in doc if k.startswith("post") and required(k)), "save"]
        across = draw("group_across")
        for key in (across.choice(upstream), across.choice(downstream)):
            doc[key]["_meta"] = {**doc[key].get("_meta", {}), "ui_group": "across"}

    def grouped(key: str) -> bool:
        return bool(doc[key].get("_meta", {}).get("ui_group"))

    def free(key: str) -> bool:
        """A required node no group holds yet."""
        return not grouped(key) and not doc[key].get("_meta", {}).get("ui_optional")

    def join(key: str, group: str) -> None:
        doc[key]["_meta"] = {**doc[key].get("_meta", {}), "ui_group": group}

    def final_preview() -> str:
        doc["preview_final"] = node("PreviewImage", {"images": list(doc["save"]["inputs"]["images"])})
        return "preview_final"

    if "group_outputs" in on:
        pick = draw("group_outputs")
        members = [f"preview{i}" for i in range(stages - 1) if pick.random() < 0.5]
        if not members:
            members = [pick.choice([f"preview{i}" for i in range(stages - 1)])]
        if pick.random() < 0.75:
            members.append("save")
        if pick.random() < 0.3:
            members.append(final_preview())
        if pick.random() < 0.3:
            others = [f"decode_s{i}" for i in range(stages - 1)] + ["decode"]
            others += [f"sampler{i}" for i in range(1, stages)]
            others += [k for k in doc if k.startswith("post")]
            # The list does not depend on ``optional_stage`` or ``group_post``: a pick that is
            # optional or grouped already is dropped, so switching those features off leaves the
            # other members as they were.
            extra = pick.choice(others)
            if free(extra):
                members.append(extra)
        members = [key for key in dict.fromkeys(members) if not grouped(key)]
        if len(members) < 2:
            # The previews are never grouped elsewhere, so one member is a preview; the save, or
            # a new final preview when another group holds the save, makes the second.
            members.append("save" if "save" not in members and not grouped("save") else final_preview())
        for key in members:
            join(key, "outputs")

    if "group_scatter" in on:
        pick = draw("group_scatter")
        candidates = [key for key in doc if free(key)]
        for key in pick.sample(candidates, min(pick.randint(2, 4), len(candidates))):
            join(key, "scatter")

    if "group_fed" in on:
        pick = draw("group_fed")

        def neighbours(key: str) -> list[str]:
            """The required nodes without a group that feed ``key`` or that it feeds directly."""
            fed_by = {v[0] for v in doc[key]["inputs"].values() if isinstance(v, list) and v[0] in doc}
            feeds = {k for k, value in doc.items() if any(isinstance(v, list) and v[0] == key for v in value["inputs"].values())}
            return [k for k in doc if k in fed_by | feeds and k != key and free(k)]

        # A seed with no free neighbour is passed over for the next one in the draw's order.
        for seed_key in pick.sample(steering, len(steering)):
            near = neighbours(seed_key)
            if free(seed_key) and near:
                for key in [seed_key, *pick.sample(near, min(pick.randint(1, 3), len(near)))]:
                    join(key, "fed")
                break
    return doc
