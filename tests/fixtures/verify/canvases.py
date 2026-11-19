"""Hand-written canvases and authoring results for the verify tests.

The definitions are the prompt fixtures (``tests/fixtures/prompt/defs.json``). The main
example is a text-to-image graph with two optional modules (a LoRA and an upscaler),
laid out in groups the way a compiled canvas is:

    1 loader -> 2 lora (optional) -> 3, 4 encoders -> 6 sampler <- 5 latent
    6 -> 7 decode -> 8 upscale (optional) -> 9 save

Groups (``GROUPS``): "loader" holds 1, "lora" 2, "prompts" 3-7, "upscale" 8, "save" 9.
"""

from __future__ import annotations

import copy
from typing import Any

from cwc.canvas.model import Canvas, NodeMode
from cwc.normalize.types import Authoring, NodeIntent, WorkflowInfo
from tests.fixtures.prompt.build import CanvasBuilder, load_defs

DEFS = load_defs()
BYPASS = int(NodeMode.BYPASS)
MUTED = int(NodeMode.NEVER)

OPTIONAL = ("2", "8")

# Node frames: pos is the body's top-left; the builder gives every node size [200, 100].
POSITIONS = {1: (0, 0), 2: (300, 0), 3: (600, 0), 4: (600, 200), 5: (600, 400), 6: (900, 0), 7: (1200, 0), 8: (1500, 0), 9: (1800, 0)}
# Group boxes; a node is a member when the centre of its frame (title bar included) is inside.
GROUPS = {
    "loader": [-20, -60, 260, 600],
    "lora": [280, -60, 240, 160],
    "prompts": [580, -60, 880, 600],
    "upscale": [1480, -60, 240, 160],
    "save": [1780, -60, 240, 160],
}
OUTER = [-40, -100, 2100, 700]  # a box around every node


def _entry(class_type: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": class_type}}


FULL = {
    "1": _entry("TestLoader", {"ckpt_name": "m.safetensors"}),
    "2": _entry("TestLora", {"lora_name": "l.safetensors", "strength": 0.8, "model": ["1", 0], "clip": ["1", 1]}),
    "3": _entry("TestEncode", {"text": "a cat", "clip": ["2", 1]}),
    "4": _entry("TestEncode", {"text": "blurry", "clip": ["2", 1]}),
    "5": _entry("TestLatent", {"width": 512, "height": 768}),
    "6": _entry("TestSampler", {"seed": 7, "steps": 20, "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent": ["5", 0]}),
    "7": _entry("TestDecode", {"samples": ["6", 0], "vae": ["1", 2]}),
    "8": _entry("TestUpscale", {"scale": 1.5, "image": ["7", 0]}),
    "9": _entry("TestSave", {"filename_prefix": "out", "images": ["8", 0]}),
}

FACTORY = {
    "1": FULL["1"],
    "3": _entry("TestEncode", {"text": "a cat", "clip": ["1", 1]}),
    "4": _entry("TestEncode", {"text": "blurry", "clip": ["1", 1]}),
    "5": FULL["5"],
    "6": _entry("TestSampler", {"seed": 7, "steps": 20, "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent": ["5", 0]}),
    "7": FULL["7"],
    "9": _entry("TestSave", {"filename_prefix": "out", "images": ["7", 0]}),
}


def authoring(full: dict | None = None, factory: dict | None = None, optional: tuple[str, ...] = OPTIONAL) -> Authoring:
    full = copy.deepcopy(FULL if full is None else full)
    factory = copy.deepcopy(FACTORY if factory is None else factory)
    return Authoring(
        full=full,
        factory=factory,
        ids={key: key for key in full},
        defs={key: DEFS.get(entry["class_type"]) for key, entry in full.items()},
        intents={key: NodeIntent(ui_optional=key in optional) for key in full},
        workflow=WorkflowInfo(),
    )


def txt2img_builder(modes: dict[int, int] | None = None) -> CanvasBuilder:
    """The example graph; optional nodes bypassed unless ``modes`` says otherwise."""
    modes = {2: BYPASS, 8: BYPASS, **(modes or {})}
    b = CanvasBuilder(DEFS)
    b.add(1, "TestLoader", widgets=["m.safetensors"], mode=modes.get(1, 0))
    b.add(2, "TestLora", widgets=["l.safetensors", 0.8], mode=modes.get(2, 0))
    b.add(3, "TestEncode", widgets=["a cat"], mode=modes.get(3, 0))
    b.add(4, "TestEncode", widgets=["blurry"], mode=modes.get(4, 0))
    b.add(5, "TestLatent", widgets=[512, 768], mode=modes.get(5, 0))
    b.add(6, "TestSampler", widgets=[7, "fixed", 20], mode=modes.get(6, 0))
    b.add(7, "TestDecode", mode=modes.get(7, 0))
    b.add(8, "TestUpscale", widgets=[1.5], mode=modes.get(8, 0))
    b.add(9, "TestSave", widgets=["out"], mode=modes.get(9, 0))
    b.link(1, 0, 2, "model")
    b.link(1, 1, 2, "clip")
    b.link(2, 1, 3, "clip")
    b.link(2, 1, 4, "clip")
    b.link(2, 0, 6, "model")
    b.link(3, 0, 6, "positive")
    b.link(4, 0, 6, "negative")
    b.link(5, 0, 6, "latent")
    b.link(6, 0, 7, "samples")
    b.link(1, 2, 7, "vae")
    b.link(7, 0, 8, "image")
    b.link(8, 0, 9, "images")
    return b


def place(canvas: Canvas, positions: dict[int, tuple[int, int]]) -> Canvas:
    for raw in canvas.raw["nodes"]:
        if raw["id"] in positions:
            raw["pos"] = list(positions[raw["id"]])
    return canvas


def add_group(canvas: Canvas, title: str, bounding: list[int]) -> Canvas:
    groups = canvas.raw["groups"]
    groups.append({"id": len(groups) + 1, "title": title, "bounding": list(bounding), "color": "#3f789e", "font_size": 24, "flags": {}})
    return canvas


def txt2img(modes: dict[int, int] | None = None, *, grouped: bool = True) -> Canvas:
    """The example canvas; with ``grouped`` each optional module has a group of its own."""
    canvas = place(txt2img_builder(modes).canvas(), POSITIONS)
    if grouped:
        for title, box in GROUPS.items():
            add_group(canvas, title, box)
    return canvas


def node(canvas: Canvas, node_id: int) -> dict[str, Any]:
    return next(raw for raw in canvas.raw["nodes"] if raw["id"] == node_id)
