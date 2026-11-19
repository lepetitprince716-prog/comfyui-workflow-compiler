"""Small authoring graphs over the bundled core node definitions, for the annotate tests.

``txt2img()`` is the base graph (loader, two prompts, empty latent, sampler, decode,
save) with symbolic ids; tests add or rewire nodes, then ``compiled`` normalizes and
instantiates it into a canvas, so the canvas has the slot types and links a real compile
writes.
"""

from __future__ import annotations

import copy
from typing import Any

from cwc.annotate.types import Annotations
from cwc.canvas.model import Canvas
from cwc.instantiate import instantiate
from cwc.normalize import normalize
from cwc.normalize.types import Authoring

from tests.fixtures.instantiate.graphs import DEFS

__all__ = ["DEFS", "backward_group_links", "bare_canvas", "compiled", "ids", "node", "txt2img"]


def node(class_type: str, inputs: dict[str, Any] | None = None, **meta: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"class_type": class_type, "inputs": dict(inputs or {})}
    if meta:
        entry["_meta"] = meta
    return entry


def txt2img() -> dict[str, Any]:
    return {
        "ckpt": node("CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "pos": node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": "a cat"}),
        "neg": node("CLIPTextEncode", {"clip": ["ckpt", "CLIP"], "text": "blurry"}),
        "latent": node("EmptyLatentImage", {"width": 1024, "height": 1024, "batch_size": 1}),
        "sampler": node(
            "KSampler",
            {
                "model": ["ckpt", "MODEL"],
                "positive": ["pos", 0],
                "negative": ["neg", 0],
                "latent_image": ["latent", 0],
                "seed": 1,
                "steps": 20,
                "cfg": 5.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        ),
        "decode": node("VAEDecode", {"samples": ["sampler", 0], "vae": ["ckpt", "VAE"]}),
        "save": node("SaveImage", {"images": ["decode", 0], "filename_prefix": "out"}),
    }


def compiled(doc: dict[str, Any]) -> tuple[Canvas, Authoring]:
    """The canvas instantiate writes for ``doc``, and the authoring it came from."""
    authoring, findings = normalize(copy.deepcopy(doc), DEFS, allow_unknown=False)
    errors = [f for f in findings if f.severity.value == "error"]
    if errors:
        raise AssertionError(f"test graph does not normalize: {errors}")
    return instantiate(authoring), authoring


def ids(authoring: Authoring) -> dict[str, str]:
    """Authoring id -> canvas node key."""
    return dict(authoring.ids)


def bare_canvas(nodes: list[dict[str, Any]], groups: list[tuple[str, list[float]]] | None = None) -> Canvas:
    """A canvas of unlinked nodes at given places: each item has ``id``, ``type``, ``pos``,
    ``size`` and optionally ``flags``, ``mode`` and ``title``; ``groups``: (title, bounding)."""
    canvas = Canvas.empty()
    raw_nodes = []
    for item in nodes:
        raw = {"id": item["id"], "type": item["type"], "pos": list(item["pos"]), "size": list(item["size"])}
        raw["flags"] = dict(item.get("flags", {}))
        raw["mode"] = item.get("mode", 0)
        raw["inputs"], raw["outputs"], raw["properties"] = [], [], {}
        if "title" in item:
            raw["title"] = item["title"]
        raw_nodes.append(raw)
    canvas.raw["nodes"] = raw_nodes
    canvas.raw["last_node_id"] = max((n["id"] for n in nodes), default=0)
    canvas.raw["groups"] = [
        {"id": index + 1, "title": title, "bounding": list(bounding), "color": "#3f789e", "flags": {}}
        for index, (title, bounding) in enumerate(groups or [])
    ]
    return canvas


def backward_group_links(canvas: Canvas, annotations: Annotations) -> list[tuple[str, str]]:
    """Group pairs (origin group, target group) whose node link does not go forward in
    ``GroupPlan.stage``. Empty exactly when the group graph is acyclic and the ranks
    follow it: a cycle would force one of its links backward or level."""
    owner = {key: group for group in annotations.groups for key in group.members}
    bad = set()
    for link in canvas.links:
        a, b = owner.get(str(link.origin_id)), owner.get(str(link.target_id))
        if a is not None and b is not None and a.key != b.key and a.stage >= b.stage:
            bad.add((a.key, b.key))
    return sorted(bad)
