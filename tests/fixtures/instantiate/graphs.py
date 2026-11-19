"""Authoring graphs for the instantiate tests, over the bundled core node definitions.

``capture()`` rebuilds the graph behind ``frontend_serialize.json`` (a real frontend's
``serialize()`` of a hand-built graph) as an authoring document, so what instantiate
writes can be held against what the frontend wrote. The other graphs are hand-written
around the layouts that need care: optional module chains, Autogrow groups that
grow with links, DynamicCombo nodes, and match-type nodes with some slots linked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cwc.nodedefs.derive import widget_layout
from cwc.nodedefs.registry import NodeDefs
from cwc.normalize import normalize
from cwc.normalize.types import Authoring
from tests.fixtures.normalize.registry import load_doc, load_registry

HERE = Path(__file__).resolve().parent
DEFS = NodeDefs.load()


def serialize_fixture() -> dict[str, Any]:
    """The frontend's ``serialize()`` of the capture graph (``tests/oracle_runner.py capture``)."""
    return json.loads((HERE / "frontend_serialize.json").read_text(encoding="utf-8"))["serialized"]


def build(doc: dict[str, Any]) -> Authoring:
    authoring, _ = normalize(doc, DEFS, allow_unknown=False)
    return authoring


def cases() -> dict[str, tuple[Authoring, Any]]:
    """Every graph the instantiate tests run, as ``name -> (authoring, definitions)``.

    ``txt2img`` is the normalize fixture over its small test registry; the rest use the
    bundled core definitions. ``capture`` is the frontend graph (not a workflow: some of
    its nodes feed no output)."""
    registry = load_registry()
    txt2img, _ = normalize(load_doc("txt2img.json"), registry, allow_unknown=False)
    return {
        "txt2img": (txt2img, registry),
        "optional_chain": (build(optional_chain()), DEFS),
        "optional_controlnet": (build(optional_controlnet()), DEFS),
        "autogrow": (build(autogrow()), DEFS),
        "dynamic_match": (build(dynamic_match()), DEFS),
        "match_partial": (build(match_partial()), DEFS),
        "dynamic_text": (build(dynamic_text()), DEFS),
        "capture": (build(capture()), DEFS),
    }


def _node(class_type: str, inputs: dict[str, Any], **meta: Any) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs, "_meta": meta}


# --- the frontend capture ---------------------------------------------------------------


def capture() -> dict[str, Any]:
    """The capture graph as an authoring document; ids ``n01``... follow the frontend ids."""
    graph = serialize_fixture()
    names = {node["id"]: f"n{node['id']:02d}" for node in graph["nodes"]}
    targets: dict[tuple[int, str], list[Any]] = {}
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    for _, origin, origin_slot, target, target_slot, _ in graph["links"]:
        name = nodes_by_id[target]["inputs"][target_slot]["name"]
        targets[(target, name)] = [names[origin], origin_slot]
    doc: dict[str, Any] = {}
    for node in graph["nodes"]:
        nodedef = DEFS.get(node["type"])
        named = node.get("widgets_values_named") or {}
        prompt_widgets = {w.name for w in widget_layout(nodedef, named) if w.to_prompt}
        control = {w.name for w in widget_layout(nodedef, named) if w.name is not None and not w.to_prompt}
        inputs = {name: value for name, value in named.items() if name in prompt_widgets}
        inputs.update({name: link for (target, name), link in targets.items() if target == node["id"]})
        meta: dict[str, Any] = {}
        if "title" in node:
            meta["title"] = node["title"]
        if node["mode"] == 4:
            meta["ui_optional"] = True
        for name in control:
            if named.get(name) in ("fixed", "randomize"):
                meta["ui_seed"] = named[name]
        doc[names[node["id"]]] = {"class_type": node["type"], "inputs": inputs, "_meta": meta}
    return doc


# --- optional module chain --------------------------------------------------------------


def optional_chain() -> dict[str, Any]:
    """Text to image with two chained LoRAs and an upscaler, all optional."""
    doc = load_doc("txt2img.json")
    group = {"ui_optional": True, "ui_group": "extras"}
    doc["lora_a"] = _node(
        "LoraLoader",
        {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "style.safetensors"},
        **group,
    )
    doc["lora_b"] = _node(
        "LoraLoader",
        {
            "model": ["lora_a", "MODEL"],
            "clip": ["lora_a", "CLIP"],
            "lora_name": "detail.safetensors",
            "strength_model": 0.6,
            "strength_clip": 0.6,
        },
        **group,
    )
    doc["upscale"] = _node(
        "ImageScaleBy", {"image": ["decode", "IMAGE"], "upscale_method": "lanczos", "scale_by": 1.5}, **group
    )
    doc["sampler"]["inputs"]["model"] = ["lora_b", "MODEL"]
    doc["pos"]["inputs"]["clip"] = ["lora_b", "CLIP"]
    doc["neg"]["inputs"]["clip"] = ["lora_b", 1]
    doc["save"]["inputs"]["images"] = ["upscale", 0]
    return doc


# --- optional ControlNet ----------------------------------------------------------------


def optional_controlnet() -> dict[str, Any]:
    """Text to image with an optional ControlNet between the encoders and the sampler.

    The ControlNet node has two CONDITIONING inputs and two CONDITIONING outputs, so a bypass
    must send output 1 (``negative``) to input 1, not to the first input of the type."""
    doc = load_doc("txt2img.json")
    group = {"ui_optional": True, "ui_group": "controlnet"}
    doc["cnl"] = _node("ControlNetLoader", {"control_net_name": "cn.safetensors"}, **group)
    doc["img"] = _node("LoadImage", {"image": "pose.png"}, **group)
    doc["cn"] = _node(
        "ControlNetApplyAdvanced",
        {
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "control_net": ["cnl", 0],
            "image": ["img", "IMAGE"],
            "strength": 0.8,
            "start_percent": 0.0,
            "end_percent": 1.0,
        },
        **group,
    )
    doc["sampler"]["inputs"]["positive"] = ["cn", 0]
    doc["sampler"]["inputs"]["negative"] = ["cn", 1]
    return doc


# --- dynamic prompts --------------------------------------------------------------------


def dynamic_text() -> dict[str, Any]:
    """Prompts whose text the frontend would rewrite as a dynamic prompt if it were stored as written:
    a URL, braces, pipes, block and line comments, a backslash before an ordinary character."""
    doc = load_doc("txt2img.json")
    doc["pos"]["inputs"]["text"] = "see https://example.com/ref, {soft|hard} light /* note */ | a \\n b\nsecond // line"
    doc["neg"]["inputs"]["text"] = "a } b { c \\{ \\| \\\\ C:\\path //"
    return doc


# --- Autogrow ---------------------------------------------------------------------------


def autogrow() -> dict[str, Any]:
    """An Autogrow group linked past its fresh slots, with ``vae`` after it, fed by an optional node."""
    return {
        "clip": _node("CLIPLoader", {"clip_name": "te.safetensors", "type": "stable_diffusion", "device": "default"}),
        "ckpt": _node("CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "src_a": _node("LoadImage", {"image": "a.png"}),
        "src_b": _node("LoadImage", {"image": "b.png"}),
        "flip": _node("ImageInvert", {"image": ["src_b", "IMAGE"]}, ui_optional=True),
        "encode": _node(
            "TextEncodeMOSSImageEdit",
            {
                "clip": ["clip", "CLIP"],
                "images.image_0": ["src_a", "IMAGE"],
                "images.image_1": ["flip", "IMAGE"],
                "images.image_2": ["src_a", "IMAGE"],
                "vae": ["ckpt", "VAE"],
                "prompt": "make it blue",
                "edit_format": "ai-toolkit",
            },
        ),
        "zero": _node("ConditioningZeroOut", {"conditioning": ["encode", "CONDITIONING"]}),
        "latent": _node("EmptyLatentImage", {"width": 512, "height": 512, "batch_size": 1}),
        "sampler": _node(
            "KSampler",
            {
                "model": ["ckpt", "MODEL"],
                "positive": ["encode", "CONDITIONING"],
                "negative": ["zero", "CONDITIONING"],
                "latent_image": ["latent", "LATENT"],
                "sampler_name": "euler",
                "scheduler": "normal",
            },
        ),
        "decode": _node("VAEDecode", {"samples": ["sampler", "LATENT"], "vae": ["ckpt", "VAE"]}),
        "save": _node("SaveImage", {"images": ["decode", "IMAGE"]}),
    }


# --- DynamicCombo and match-type nodes -----------------------------------------------------


def dynamic_match() -> dict[str, Any]:
    """A DynamicCombo node whose chosen option adds a widget (fed by an optional node) and
    two match-type nodes: a resizer whose output takes the image type, and a switch."""
    return {
        "src": _node("LoadImage", {"image": "a.png"}),
        "factor": _node("PrimitiveFloat", {"value": 0.5}, ui_optional=True),
        "resize": _node(
            "ResizeImageMaskNode",
            {
                "input": ["src", "IMAGE"],
                "resize_type": "scale by multiplier",
                "resize_type.multiplier": ["factor", "FLOAT"],
                "scale_method": "area",
            },
        ),
        "flip": _node("ImageInvert", {"image": ["resize", 0]}),
        "pick": _node("ComfySwitchNode", {"on_false": ["flip", "IMAGE"], "on_true": ["resize", 0], "switch": True}),
        "out": _node("PreviewImage", {"images": ["pick", 0]}),
    }


def match_partial() -> dict[str, Any]:
    """Match-type nodes with only some of a template's slots linked: a switch with one slot,
    a Branch group with two slots (the third, empty, takes the type too), and a resizer
    that gets a mask, so its union input stays a union."""
    return {
        "src": _node("LoadImage", {"image": "a.png"}),
        "other": _node("LoadImage", {"image": "b.png"}),
        "pick": _node("ComfySwitchNode", {"on_true": ["src", 0], "switch": True}),
        "branch": _node(
            "BranchNode",
            {
                "autogrow.branch0": ["src", 0],
                "autogrow.branch1": ["other", 0],
                "branch": "branch0",
                "branch_names": {"__value__": ["branch0", "branch1"]},  # the frontend names them after the slots
            },
        ),
        "mask": _node("ImageToMask", {"image": ["src", 0], "channel": "red"}),
        "resize": _node(
            "ResizeImageMaskNode",
            {"input": ["mask", 0], "resize_type": "scale by multiplier", "resize_type.multiplier": 0.5, "scale_method": "area"},
        ),
        "back": _node("MaskToImage", {"mask": ["resize", 0]}),
        "out_pick": _node("PreviewImage", {"images": ["pick", 0]}),
        "out_branch": _node("PreviewImage", {"images": ["branch", 0]}),
        "out_back": _node("PreviewImage", {"images": ["back", 0]}),
    }
