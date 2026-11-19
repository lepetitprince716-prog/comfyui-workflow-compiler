"""Measure what a frontend exports for a canvas compiled with ``--allow-unknown`` over a type it knows.

Usage (needs the oracle, see ``tests/oracle_support.py``; run it from the repository root):
    CWC_ORACLE_URL=... CWC_ORACLE_PYTHON=... python3 tools/oracle/probe_unknown_types.py

One core type at a time is removed from the compiler's definitions (``KSampler``, which has a
``control_after_generate`` widget the API prompt leaves out, and ``ImageScale``, whose widgets
are all in the prompt). A text to image graph that uses it is compiled with ``allow_unknown``
and loaded in the frontend, which does know the type. The export is compared with the input
prompt. The result is section #9 of ``docs/m0-findings.md``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from cwc.nodedefs.registry import NodeDefs  # noqa: E402
from cwc.pipeline import compile_document  # noqa: E402
from cwc.prompt.model import prompt_diff  # noqa: E402
from tests import oracle_support  # noqa: E402

FULL = NodeDefs.load()


def without(*types: str) -> NodeDefs:
    return NodeDefs({name: FULL.get(name) for name in FULL if name not in types})


def graph(scrambled: bool = False) -> dict:
    """Inputs in the order a frontend writes an API export: the node's own input order."""
    sampler = {
        "model": ["ckpt", 0], "seed": 123456, "steps": 28, "cfg": 4.0, "sampler_name": "euler",
        "scheduler": "normal", "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["latent", 0],
        "denoise": 0.75,
    }  # fmt: skip
    scale = {"image": ["decode", 0], "upscale_method": "lanczos", "width": 640, "height": 384, "crop": "center"}
    if scrambled:
        scale = {"image": ["decode", 0], "crop": "center", "height": 384, "width": 640, "upscale_method": "lanczos"}
    return {
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ckpt", 1], "text": "a cat"}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ckpt", 1], "text": "blurry"}},
        "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 768, "batch_size": 2}},
        "sampler": {"class_type": "KSampler", "inputs": sampler},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["ckpt", 2]}},
        "scale": {"class_type": "ImageScale", "inputs": scale},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["scale", 0], "filename_prefix": "x"}},
    }


VARIANTS = [
    ("control: all types known", graph(), FULL),
    ("KSampler unknown", graph(), without("KSampler")),
    ("ImageScale unknown, authoring order = frontend order", graph(), without("ImageScale")),
    ("ImageScale unknown, scrambled authoring order", graph(True), without("ImageScale")),
]


def main() -> int:
    compiled = []
    for name, doc, defs in VARIANTS:
        result = compile_document(doc, defs, allow_unknown=True)
        print(name, "| report ok:", result.report.ok, [(f.code.value, f.severity.value) for f in result.report.findings])
        compiled.append(result)
    for (name, _, _), result, loaded in zip(VARIANTS, compiled, oracle_support.roundtrip([c.canvas.to_json() for c in compiled])):
        print("==", name, "| load_error:", loaded.load_error, "| prompt_error:", loaded.prompt_error, "| console:", list(loaded.console))
        diff = prompt_diff(result.authoring.factory, loaded.prompt)
        print(f"  diff against the input prompt: {len(diff)} line(s)")
        for line in diff:
            print("   ", line)
        for node in loaded.prompt.values():
            if node["class_type"] in ("KSampler", "ImageScale"):
                print("  exported", node["class_type"], json.dumps(node["inputs"], sort_keys=True))
        for node in result.canvas.to_json()["nodes"]:
            if node["type"] in ("KSampler", "ImageScale"):
                print("  written ", node["type"], "widgets_values", node.get("widgets_values"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
