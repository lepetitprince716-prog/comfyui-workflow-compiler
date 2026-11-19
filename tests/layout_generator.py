"""Seeded random authoring graphs for the layout property tests.

``random_graph(seed)`` is a text-to-image base plus a random selection of modules drawn
from ``random.Random(seed)``: LoRA loaders in the model chain, ControlNet-style branches
with their own image loaders, an optional upscale after the decode, a second sampling
stage, and small value nodes feeding widgets. ``reduced_graph(seed)`` is the same draw with
every optional module and every control branch left out (the "removal" counterpart): same
spec, flags off. Both are valid for compile, with the bypass closure B1-B3 intact: a node
that serves only optional nodes is itself optional.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from tests import support  # noqa: F401
from tests.graphs import node, txt2img


@dataclass(frozen=True)
class Control:
    optional: bool
    canny: bool


@dataclass(frozen=True)
class Spec:
    loras: tuple[bool, ...]  # one entry per LoRA loader: is it optional
    controls: tuple[Control, ...]
    upscale: bool
    stage2: bool
    values: tuple[str, ...]  # widget targets "<node id>.<input>" a value node feeds


# Value-node targets, in one fixed order so a seed draws the same ones in both graphs.
_FIXED_TARGETS = ("sampler.steps", "sampler.cfg", "latent.width", "latent.height")


def _targets(loras: int, controls: int, upscale: bool, stage2: bool) -> list[str]:
    out = list(_FIXED_TARGETS)
    out += [f"lora{i}.strength_model" for i in range(loras)]
    out += [f"cn{j}.strength" for j in range(controls)]
    if upscale:
        out.append("upscale.scale_by")
    if stage2:
        out.append("sampler2.denoise")
    return out


def draw(seed: int) -> Spec:
    rng = random.Random(seed)
    loras = tuple(rng.random() < 0.6 for _ in range(rng.randint(0, 4)))
    controls = tuple(Control(optional=rng.random() < 0.6, canny=rng.random() < 0.5) for _ in range(rng.randint(0, 3)))
    upscale = rng.random() < 0.5
    stage2 = rng.random() < 0.4
    pool = _targets(len(loras), len(controls), upscale, stage2)
    values = tuple(rng.sample(pool, rng.randint(0, 3)))
    return Spec(loras, controls, upscale, stage2, values)


def random_graph(seed: int) -> dict[str, Any]:
    """The full graph for ``seed``."""
    return build(draw(seed), reduced=False)


def reduced_graph(seed: int) -> dict[str, Any]:
    """``seed``'s graph without its optional LoRAs, its control branches and its upscale."""
    return build(draw(seed), reduced=True)


def build(spec: Spec, *, reduced: bool) -> dict[str, Any]:
    doc = txt2img()
    optional_ids: set[str] = set()  # ids of the nodes that carry ui_optional

    def add(key: str, class_type: str, inputs: dict[str, Any], *, optional: bool = False, group: str | None = None) -> None:
        meta: dict[str, Any] = {}
        if optional:
            meta = {"ui_optional": True}
            optional_ids.add(key)
            if group:
                meta["ui_group"] = group
        doc[key] = node(class_type, inputs, **meta)

    kept_loras = [i for i, optional in enumerate(spec.loras) if not (reduced and optional)]
    kept_controls = [] if reduced else list(range(len(spec.controls)))
    upscale = spec.upscale and not reduced

    # The model chain.
    model, clip = ["ckpt", "MODEL"], ["ckpt", "CLIP"]
    for i in kept_loras:
        add(
            f"lora{i}",
            "LoraLoader",
            {"model": model, "clip": clip, "lora_name": f"l{i}.safetensors", "strength_model": 1.0, "strength_clip": 1.0},
            optional=spec.loras[i],
            group="extras",
        )
        model, clip = [f"lora{i}", "MODEL"], [f"lora{i}", "CLIP"]
    doc["sampler"]["inputs"]["model"] = model
    doc["pos"]["inputs"]["clip"] = clip
    doc["neg"]["inputs"]["clip"] = clip

    # The control branches, chained on the conditioning.
    positive, negative = ["pos", 0], ["neg", 0]
    for j in kept_controls:
        control = spec.controls[j]
        flags = {"optional": control.optional, "group": f"control{j}"}
        add(f"img{j}", "LoadImage", {"image": f"pose{j}.png"}, **flags)
        source = [f"img{j}", "IMAGE"]
        if control.canny:
            add(f"edges{j}", "Canny", {"image": source, "low_threshold": 0.4, "high_threshold": 0.8}, **flags)
            source = [f"edges{j}", 0]
        add(f"cnl{j}", "ControlNetLoader", {"control_net_name": f"cn{j}.safetensors"}, **flags)
        add(
            f"cn{j}",
            "ControlNetApplyAdvanced",
            {
                "positive": positive,
                "negative": negative,
                "control_net": [f"cnl{j}", 0],
                "image": source,
                "strength": 0.8,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            **flags,
        )
        positive, negative = [f"cn{j}", 0], [f"cn{j}", 1]
    doc["sampler"]["inputs"]["positive"] = positive
    doc["sampler"]["inputs"]["negative"] = negative

    # The second sampling stage.
    decode = "decode"
    if spec.stage2:
        doc["preview"] = node("PreviewImage", {"images": ["decode", 0]})
        doc["up"] = node("LatentUpscaleBy", {"samples": ["sampler", 0], "upscale_method": "nearest-exact", "scale_by": 1.5})
        second = {key: (list(value) if isinstance(value, list) else value) for key, value in doc["sampler"]["inputs"].items()}
        second["latent_image"] = ["up", 0]
        second["denoise"] = 0.5
        doc["sampler2"] = node("KSampler", second)
        doc["decode2"] = node("VAEDecode", {"samples": ["sampler2", 0], "vae": ["ckpt", "VAE"]})
        decode = "decode2"
    doc["save"]["inputs"]["images"] = [decode, 0]

    # The optional upscale after the last decode.
    if upscale:
        add("upscale", "ImageScaleBy", {"image": [decode, 0], "upscale_method": "lanczos", "scale_by": 1.5}, optional=True)
        doc["save"]["inputs"]["images"] = ["upscale", 0]

    # Value nodes: one per target still present; optional exactly when the target is.
    for k, target in enumerate(spec.values):
        owner, name = target.split(".")
        if owner not in doc:
            continue
        current = doc[owner]["inputs"][name]
        class_type = "PrimitiveFloat" if isinstance(current, float) else "PrimitiveInt"
        add(f"val{k}", class_type, {"value": current}, optional=owner in optional_ids, group=None)
        doc[owner]["inputs"][name] = [f"val{k}", 0]
    return doc
