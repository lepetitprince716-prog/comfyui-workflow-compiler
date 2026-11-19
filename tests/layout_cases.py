"""Test graphs and the pipeline helper for the layout tests.

``laid_out(doc)`` runs authoring -> instantiate -> annotate -> apply_notes -> layout and
returns the canvas, the plan, the layout result, the compile-mode geometry report and the
canvas JSON as it was just before layout. ``routed(doc)`` runs ``route`` after layout and
returns the same plus the route result; ``laid_out`` stays layout only, so layout
fingerprints stay comparable.
"""

from __future__ import annotations

import copy
from typing import Any

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img

from cwc.annotate import annotate, apply_notes
from cwc.layout import layout
from cwc.layout.presets import get_preset
from cwc.report import Mode
from cwc.route import route
from cwc.verify.geometry import check_geometry

BENTO = get_preset("bento")
COMPILE_ERRORS = ("G1", "G3", "G4", "G6", "G7", "G8", "C1", "C4", "C6")
SIMPLE_ERRORS = ("G5", "C3")


def laid_out(doc: dict[str, Any], preset=BENTO):
    return _pipeline(doc, preset, routing=False)[:5]


def routed(doc: dict[str, Any], preset=BENTO):
    """``laid_out`` with ``route`` run after layout; the sixth item is the ``RouteResult``."""
    return _pipeline(doc, preset, routing=True)


def _pipeline(doc: dict[str, Any], preset, routing: bool):
    canvas, authoring = compiled(doc)
    plan = annotate(canvas, DEFS, authoring)
    apply_notes(canvas, plan)
    before = canvas.to_json()
    result = layout(canvas, plan, preset, DEFS)
    routes = route(canvas, result, DEFS) if routing else None
    report = check_geometry(
        canvas,
        DEFS,
        mode=Mode.COMPILE,
        membership=result.membership,
        preset=preset,
        cockpit=result.cockpit,
    )
    return canvas, plan, result, report, before, routes


def with_lora(optional: bool = True) -> dict[str, Any]:
    doc = txt2img()
    meta = {"ui_optional": True, "ui_group": "extras"} if optional else {}
    doc["lora_a"] = node("LoraLoader", {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "a.safetensors"}, **meta)
    doc["lora_b"] = node("LoraLoader", {"model": ["lora_a", "MODEL"], "clip": ["lora_a", "CLIP"], "lora_name": "b.safetensors"}, **meta)
    doc["sampler"]["inputs"]["model"] = ["lora_b", "MODEL"]
    doc["pos"]["inputs"]["clip"] = ["lora_b", "CLIP"]
    doc["neg"]["inputs"]["clip"] = ["lora_b", "CLIP"]
    return doc


def lora_chain(pattern: str) -> dict[str, Any]:
    """Text to image with a chain of LoRA loaders ``l0``, ``l1``, ... on the model and clip, one
    per letter of ``pattern``: ``O`` optional, ``R`` required. Alternating letters cut the model
    group into segments with an optional group between each two."""
    doc = txt2img()
    prev = "ckpt"
    for i, letter in enumerate(pattern):
        meta = {"ui_optional": True} if letter == "O" else {}
        doc[f"l{i}"] = node("LoraLoader", {"model": [prev, "MODEL"], "clip": [prev, "CLIP"], "lora_name": f"{i}.safetensors"}, **meta)
        prev = f"l{i}"
    doc["sampler"]["inputs"]["model"] = [prev, "MODEL"]
    doc["pos"]["inputs"]["clip"] = [prev, "CLIP"]
    doc["neg"]["inputs"]["clip"] = [prev, "CLIP"]
    return doc


def two_stage() -> dict[str, Any]:
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


def custom_sampling() -> dict[str, Any]:
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


def controlnet(doc: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doc or txt2img()
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
    return doc


def optional_upscale(doc: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doc or txt2img()
    doc["upscale"] = node("ImageScaleBy", {"image": ["decode", 0], "upscale_method": "lanczos", "scale_by": 1.5}, ui_optional=True)
    doc["save"]["inputs"]["images"] = ["upscale", 0]
    return doc


def long_chain(stages: int) -> dict[str, Any]:
    """Text to image followed by ``stages`` refine stages (upscale, sampler, decode, preview)."""
    doc = txt2img()
    prev = "sampler"
    for i in range(stages):
        doc[f"up{i}"] = node("LatentUpscaleBy", {"samples": [prev, 0], "upscale_method": "nearest-exact", "scale_by": 1.1})
        sampler = copy.deepcopy(doc["sampler"])
        sampler["inputs"]["latent_image"] = [f"up{i}", 0]
        sampler["inputs"]["denoise"] = 0.4
        doc[f"s{i}"] = sampler
        doc[f"d{i}"] = node("VAEDecode", {"samples": [f"s{i}", 0], "vae": ["ckpt", "VAE"]})
        doc[f"p{i}"] = node("PreviewImage", {"images": [f"d{i}", 0]})
        prev = f"s{i}"
    doc["decode"]["inputs"]["samples"] = [prev, 0]
    return doc


SIMPLE = {
    "txt2img": txt2img,
    "optional_lora": with_lora,
    "two_stage": two_stage,
}
OTHERS = {
    "custom_sampling": custom_sampling,
    "controlnet": controlnet,
    "optional_upscale": optional_upscale,
    "lora_controlnet_upscale": lambda: optional_upscale(controlnet(with_lora())),
    "long_chain_6": lambda: long_chain(6),
    "lora_chain_OROR": lambda: lora_chain("OROR"),
    "lora_chain_ROROR": lambda: lora_chain("ROROR"),
}


# The column rule for zero-hole (see cwc.layout.place): a section is a main row (the first preset
# band) with the lower bands under it; a wrap starts a new section below.
MAX_GAP = 80  # BAND_GAP: no wider gap in the union of a section's projections on x
SLOT_GAP = 40  # GAP: a wider gap in a main row needs a bridge under it


def top_links(canvas, plan) -> set[tuple[str, str]]:
    """``(upstream, downstream)`` top-level group keys of every link between two top groups."""
    parent = {g.key: g.parent for g in plan.groups}
    top: dict[str, str] = {}
    for group in plan.groups:
        key = group.key
        while parent[key] is not None:
            key = parent[key]
        for member in group.members:
            top[member] = key
    pairs = ((top.get(str(link.origin_id)), top.get(str(link.target_id))) for link in canvas.links)
    return {(a, b) for a, b in pairs if a is not None and b is not None and a != b}


def sections(canvas, plan, result, preset=BENTO) -> list[dict[str, Any]]:
    """The sections, top to bottom: ``main`` (the main row's columns left to right), ``lower``
    (the lower bands' columns) and ``boxes`` (every canvas group bounding inside the section)."""
    zone = {g.key: g.zone for g in plan.groups}
    main_bands = sorted({c.band for c in result.columns if all(preset.home(zone[k])[0] == 0 for k in c.groups)})
    out = []
    for i, first in enumerate(main_bands):
        last = main_bands[i + 1] - 1 if i + 1 < len(main_bands) else len(result.bands) - 1
        top, bottom = result.bands[first].y, result.bands[last].bottom
        columns = [c for c in result.columns if first <= c.band <= last]
        out.append(
            {
                "main": sorted((c for c in columns if c.band == first), key=lambda c: c.rect.x),
                "lower": [c for c in columns if c.band != first],
                "boxes": [g["bounding"] for g in canvas.raw["groups"] if top <= g["bounding"][1] < bottom],
            }
        )
    return out


def column_holes(section) -> list[tuple[float, float]]:
    """Gaps wider than ``MAX_GAP`` in the union of the section's group boxes projected on x."""
    spans = sorted((b[0], b[0] + b[2]) for b in section["boxes"])
    out, reach = [], spans[0][1]
    for left, right in spans[1:]:
        if left - reach > MAX_GAP:
            out.append((reach, left))
        reach = max(reach, right)
    return out


def unbridged_gaps(section, links, exempt=frozenset()) -> list[tuple[float, float]]:
    """Main-row gaps wider than ``SLOT_GAP`` without a bridge under them, or wider than the
    bridge rule allows: the next column starts no further right than ``SLOT_GAP`` after the
    previous column or after a lower column that feeds it, whichever is further."""
    return [gap for gap, _ in gap_faults(section, links, exempt)]


def gap_faults(section, links, exempt=frozenset()) -> list[tuple[tuple[float, float], str]]:
    """``unbridged_gaps`` with the cause of each: ``no-bridge`` or ``too-wide``."""
    main, lower = section["main"], section["lower"]
    main_keys = {k for c in main for k in c.groups}

    def feeds(column, keys) -> bool:
        return any((a, b) in links for a in column.groups for b in keys if b not in exempt)

    def closure(start, step):
        found = [c for c in lower if start(c)]
        while True:
            more = [c for c in lower if c not in found and any(step(c, d) for d in found)]
            if not more:
                return found
            found += more

    fed = closure(lambda c: any((a, b) in links for a in main_keys for b in c.groups), lambda c, d: feeds(d, c.groups))
    feeding = closure(lambda c: feeds(c, main_keys), lambda c, d: feeds(c, d.groups))
    bridges = [c for c in fed if c in feeding]  # on a path of lower columns from the main row to it
    out = []
    for prev, nxt in zip(main, main[1:]):
        gap = (prev.rect.right, nxt.rect.x)
        if gap[1] - gap[0] <= SLOT_GAP:
            continue
        under = [b for b in bridges if b.rect.x < gap[1] and gap[0] < b.rect.right]
        allowed = max([prev.rect.right + SLOT_GAP] + [c.rect.right + SLOT_GAP for c in lower if feeds(c, nxt.groups)])
        if not under:
            out.append((gap, "no-bridge"))
        elif nxt.rect.x > -(-allowed // 20) * 20:
            out.append((gap, "too-wide"))
    return out


def extent_x(canvas) -> float:
    boxes = [g["bounding"] for g in canvas.raw["groups"]]
    return max(b[0] + b[2] for b in boxes) - min(b[0] for b in boxes)
