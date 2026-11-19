"""Real-frontend runner for the tests; runs under the oracle's Python (it needs Playwright).

Usage:
    python tests/oracle_runner.py --url URL probe WORKFLOW.json [WORKFLOW.json ...]
    python tests/oracle_runner.py --url URL capture [GRAPH.json]
    python tests/oracle_runner.py --url URL batch FILE.json [FILE.json ...]
    python tests/oracle_runner.py --url URL layout [--vue] FILE.json [FILE.json ...]

``probe`` loads each canvas in one page (``window.__cwc.probe`` from tools/oracle) and
prints a JSON list with, per canvas, the load error, the console messages of that load,
``graphToPrompt().output`` and ``app.graph.serialize()``.

``capture`` builds :data:`CAPTURE_GRAPH` (or the graph in GRAPH.json) in the frontend the way a user would (create,
set widgets, retitle, set modes, connect) and prints the serialized graph. Its output,
with the console messages dropped, is ``tests/fixtures/instantiate/frontend_serialize.json``:
the reference for the shape instantiate writes. Every value in it is written here by hand.

``batch`` runs every file in one page: a JSON list is a capture graph, a JSON object
a workflow to probe. It prints one result per file, in order.

``layout`` loads each canvas in one page of the chosen renderer (classic, or Vue with ``--vue``) and
prints one result per file: the load error, the console of that load, ``graphToPrompt().output``,
``app.graph.serialize()``, and per node the live boxes and per group the bounding and the members
(``window.__cwc.geometry``, without the link rows).

The test side is ``tests/oracle_support.py``; it starts this script in a subprocess.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "oracle"))

from harness import open_session  # noqa: E402

# Nodes in creation order (ids 1..n). ``set``: widget values; ``connect``: links
# [origin index, origin slot, target input name] into this node.
CAPTURE_GRAPH: list[dict[str, Any]] = [
    {"type": "CheckpointLoaderSimple", "set": {"ckpt_name": "base.safetensors"}},
    {
        "type": "LoraLoader",
        "mode": 4,
        "set": {"lora_name": "style.safetensors", "strength_model": 0.8},
        "connect": [[0, 0, "model"], [0, 1, "clip"]],
    },
    {"type": "CLIPTextEncode", "title": "Positive prompt", "set": {"text": "a cat"}, "connect": [[1, 1, "clip"]]},
    {"type": "CLIPTextEncode", "set": {"text": "blurry"}, "connect": [[1, 1, "clip"]]},
    {"type": "EmptyLatentImage", "set": {"width": 1024, "height": 768}},
    {"type": "PrimitiveInt", "set": {"value": 30}},
    {
        "type": "KSampler",
        "set": {"seed": 42, "control_after_generate": "fixed", "cfg": 4.5},
        "connect": [[1, 0, "model"], [2, 0, "positive"], [3, 0, "negative"], [4, 0, "latent_image"], [5, 0, "steps"]],
    },
    {"type": "VAEDecode", "connect": [[6, 0, "samples"], [0, 2, "vae"]]},
    {"type": "SaveImage", "set": {"filename_prefix": "cwc_shape"}, "connect": [[7, 0, "images"]]},
    {"type": "LoadImage", "set": {"image": "example.png"}},
    {"type": "ControlNetLoader", "set": {"control_net_name": "control.safetensors"}},
    {
        "type": "ControlNetApplyAdvanced",
        "set": {"strength": 0.7},
        "connect": [[2, 0, "positive"], [3, 0, "negative"], [10, 0, "control_net"], [9, 0, "image"], [0, 2, "vae"]],
    },
    {"type": "CLIPLoader", "set": {"clip_name": "te.safetensors"}},
    {
        "type": "ResizeImageMaskNode",
        "set": {"resize_type": "scale by multiplier", "resize_type.multiplier": 0.5},
        "connect": [[9, 0, "input"]],
    },
    {"type": "BatchImagesNode", "connect": [[9, 0, "images.image0"], [13, 0, "images.image1"]]},
    {"type": "PreviewImage", "connect": [[14, 0, "images"]]},
]

_CAPTURE_JS = r"""
async (specs) => {
  const frames = window.__cwc.frames;
  await window.app.loadGraphData({ nodes: [], links: [], groups: [], version: 0.4, last_node_id: 0, last_link_id: 0 }, true, false, null, {});
  const g = window.app.graph;
  const made = [];
  for (const s of specs) {
    const n = window.LiteGraph.createNode(s.type);
    if (!n) throw new Error('cannot create ' + s.type);
    g.add(n);
    n.pos = [100 + made.length * 50, 100 + made.length * 50];
    for (const [name, value] of Object.entries(s.set || {})) {
      const w = (n.widgets || []).find((w) => w.name === name);
      if (!w) throw new Error('no widget ' + name + ' on ' + s.type);
      w.value = value;
      if (w.callback) w.callback(value, window.app.canvas, n);
    }
    if (s.title) n.title = s.title;
    if (s.mode !== undefined) n.mode = s.mode;
    made.push(n);
  }
  for (let i = 0; i < specs.length; i++) {
    for (const [from, fromSlot, name] of specs[i].connect || []) {
      const target = made[i];
      const slot = target.inputs.findIndex((x) => x.name === name);
      if (slot < 0) throw new Error('no input ' + name + ' on ' + target.type);
      made[from].connect(fromSlot, target, slot);
    }
  }
  await frames(4);
  return JSON.parse(JSON.stringify(g.serialize()));
}
"""


# Page start can take minutes on a loaded host; the default in tools/oracle is 2 minutes.
# A page whose script chunks were answered with 502 by the proxy never starts, however long it
# waits, so the wait is cut short and the page opened again.
BOOT_TIMEOUT_MS = 240_000
BOOT_ATTEMPTS = 4
SETTLE_MS = 5_000


def capture(url: str, graph: list[dict[str, Any]]) -> dict[str, Any]:
    return batch(url, [graph])[0]


def probe_all(url: str, workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return batch(url, workflows)


@contextmanager
def _session(url: str, vue: bool = False) -> Iterator[Any]:
    """An open frontend page; a page that does not start in time is replaced, up to BOOT_ATTEMPTS."""
    for attempt in range(1, BOOT_ATTEMPTS + 1):
        stack = ExitStack()
        try:
            session = stack.enter_context(open_session(url, vue=vue, timeout_ms=BOOT_TIMEOUT_MS))
        except Exception as error:  # noqa: BLE001 - playwright's TimeoutError, or a failed connection
            print(f"oracle page start {attempt}/{BOOT_ATTEMPTS} failed: {str(error)[:120]}", file=sys.stderr)
            if attempt == BOOT_ATTEMPTS:
                raise
        else:
            with stack:
                yield session
            return


def batch(url: str, items: list[Any]) -> list[dict[str, Any]]:
    """Capture (a list) or probe (a dict) each item in one page, in order."""
    out = []
    with _session(url) as s:
        versions = s.versions()
        for index, item in enumerate(items):
            s.log.phase = f"item{index}"
            if isinstance(item, list):
                result = {"serialized": s.page.evaluate(_CAPTURE_JS, item)}
            else:
                result = s.call("probe", item, {})
            out.append(
                {
                    "versions": versions,
                    "load_error": result.get("load_error"),
                    "prompt_error": result.get("prompt_error"),
                    "console": s.log.take(s.log.phase),
                    "prompt": result.get("prompt"),
                    "serialized": result.get("serialized"),
                }
            )
    return out


def layout_batch(url: str, canvases: list[dict[str, Any]], *, vue: bool) -> list[dict[str, Any]]:
    """Load each canvas in one page of one renderer and report the live geometry, the export and the console."""
    out = []
    with _session(url, vue) as s:
        versions = s.versions()
        for index, canvas in enumerate(canvases):
            s.log.phase = f"item{index}"
            result = s.call("geometry", canvas, {"prompt": True, "links": False, "frames": 30})
            try:  # the load's own requests (image, folder lists) end inside this item's console
                s.page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            except Exception:  # noqa: BLE001 - a busy network only means more console noise
                pass
            out.append(
                {
                    "versions": versions,
                    "load_error": result.get("load_error"),
                    "prompt_error": result.get("prompt_error"),
                    "console": s.log.take(s.log.phase),
                    "prompt": result.get("prompt"),
                    "serialized": result.get("serialized"),
                    "nodes": result.get("nodes"),
                    "groups": result.get("groups"),
                }
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("workflows", nargs="+", type=Path)
    c = sub.add_parser("capture")
    c.add_argument("graph", nargs="?", type=Path)
    b = sub.add_parser("batch")
    b.add_argument("files", nargs="+", type=Path)
    lay = sub.add_parser("layout")
    lay.add_argument("--vue", action="store_true")
    lay.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "layout":
        result: Any = layout_batch(args.url, [json.loads(path.read_text(encoding="utf-8")) for path in args.files], vue=args.vue)
    elif args.cmd == "capture":
        graph = CAPTURE_GRAPH if args.graph is None else json.loads(args.graph.read_text(encoding="utf-8"))
        result = capture(args.url, graph)
    elif args.cmd == "batch":
        result = batch(args.url, [json.loads(path.read_text(encoding="utf-8")) for path in args.files])
    else:
        result = probe_all(args.url, [json.loads(path.read_text(encoding="utf-8")) for path in args.workflows])
    json.dump(result, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
