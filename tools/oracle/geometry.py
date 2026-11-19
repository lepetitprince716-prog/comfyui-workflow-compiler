"""Canvas geometry as a real frontend sees it (M2 layout checks).

Usage (runs in the oracle's Python, which has Playwright):
    python tools/oracle/geometry.py probe CANVAS.json [CANVAS.json ...] --url URL [--vue] [--settings JSON]
    python tools/oracle/geometry.py record --url URL [--canvas NAME ...]

``probe`` loads each canvas in one page of the chosen renderer and prints a JSON list, one
object per canvas: per node the live ``pos``/``size``, ``boundingRect`` (the visual box,
title band included), the Vue node element's box under ``--vue``, and the anchor of every
input and output slot; per group its ``bounding`` and the nodes and groups
``recomputeInsideNodes`` puts in it; per link the slot anchors, the spline control points
predicted by the rule in docs/m2-frontend-facts.md and how many of 21 sampled curve points
lie on the path the canvas stroked; and ``app.graph.serialize()`` taken right after the load.
``--url`` defaults to ``$CWC_ORACLE_URL``.

``record`` builds the measurement canvases (core nodes, synthetic titles), probes them in the
classic renderer, the Vue renderer and the classic renderer with snap to grid on, and writes
``tests/fixtures/layout/``. ``--canvas`` limits it to the named canvases, so recording a new
canvas leaves the fixtures of the others untouched.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import open_session  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures" / "layout"

# Renderer runs recorded by ``record``: name -> (vue, settings).
RUNS: dict[str, tuple[bool, dict[str, Any]]] = {
    "classic": (False, {}),
    "vue": (True, {}),
    "classic_snap": (False, {"pysssss.SnapToGrid": True}),
}


def probe(canvases: list[dict[str, Any]], url: str, *, vue: bool, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out = []
    with open_session(url, vue=vue, settings=settings) as s:
        versions = s.versions()
        for index, canvas in enumerate(canvases):
            s.log.phase = f"canvas{index}"
            result = s.call("geometry", canvas, {})
            result["versions"] = versions
            result["renderer"] = "vue" if versions["vue_nodes"] else "classic"
            result["console"] = s.log.take(s.log.phase)
            out.append(result)
    return out


def _node(nid: int, type_: str, pos: list[float], size: list[float], widgets: list[Any], *,
          title: str | None = None, collapsed: bool = False,
          inputs: list[dict[str, Any]] | None = None, outputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": nid, "type": type_, "pos": pos, "size": size,
        "flags": {"collapsed": True} if collapsed else {}, "order": nid - 1, "mode": 0,
        "inputs": inputs or [], "outputs": outputs or [],
        "properties": {"Node name for S&R": type_}, "widgets_values": widgets,
    }
    if title is not None:
        node["title"] = title
    return node


def _canvas(nodes: list[dict[str, Any]], links: list[list[Any]], groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "last_node_id": max(n["id"] for n in nodes), "last_link_id": max((link[0] for link in links), default=0),
        "nodes": nodes, "links": links, "groups": groups, "config": {}, "extra": {}, "version": 0.4,
    }


def boxes_canvas() -> dict[str, Any]:
    """Nested, overlapping and undersized groups; off-grid, oversized and collapsed nodes; links."""
    o = lambda name, type_, links: {"name": name, "type": type_, "links": links}  # noqa: E731
    i = lambda name, type_, link: {"name": name, "type": type_, "link": link}  # noqa: E731
    nodes = [
        _node(1, "CheckpointLoaderSimple", [100, 200], [315, 98], ["model.safetensors"],
              outputs=[o("MODEL", "MODEL", [1]), o("CLIP", "CLIP", [2, 3, 4]), o("VAE", "VAE", [5])]),
        _node(2, "CLIPTextEncode", [500, 100], [400, 200], ["positive"], title="P", collapsed=True,
              inputs=[i("clip", "CLIP", 2)], outputs=[o("CONDITIONING", "CONDITIONING", [6])]),
        _node(3, "CLIPTextEncode", [500, 300], [400, 200], ["unused"], collapsed=True,
              title="A synthetic long title for the collapsed width",
              inputs=[i("clip", "CLIP", 3)], outputs=[o("CONDITIONING", "CONDITIONING", [])]),
        _node(4, "EmptyLatentImage", [103.7, 457.3], [315, 106], [512, 512, 1],
              outputs=[o("LATENT", "LATENT", [8])]),
        _node(5, "KSampler", [1000, 200], [400, 500], [0, "fixed", 20, 8, "euler", "normal", 1],
              inputs=[i("model", "MODEL", 1), i("positive", "CONDITIONING", 6), i("negative", "CONDITIONING", 7),
                      i("latent_image", "LATENT", 8)],
              outputs=[o("LATENT", "LATENT", [9])]),
        _node(6, "VAEDecode", [1500, 200], [210, 46], [],
              inputs=[i("samples", "LATENT", 9), i("vae", "VAE", 5)], outputs=[o("IMAGE", "IMAGE", [10])]),
        _node(7, "SaveImage", [1800, 200], [300, 300], ["cwc"], inputs=[i("images", "IMAGE", 10)]),
        _node(8, "CLIPTextEncode", [500, 600], [400, 200], ["negative"], title="N",
              inputs=[i("clip", "CLIP", 4)], outputs=[o("CONDITIONING", "CONDITIONING", [7])]),
    ]
    links = [
        [1, 1, 0, 5, 0, "MODEL"], [2, 1, 1, 2, 0, "CLIP"], [3, 1, 1, 3, 0, "CLIP"], [4, 1, 1, 8, 0, "CLIP"],
        [5, 1, 2, 6, 1, "VAE"], [6, 2, 0, 5, 1, "CONDITIONING"], [7, 8, 0, 5, 2, "CONDITIONING"],
        [8, 4, 0, 5, 3, "LATENT"], [9, 5, 0, 6, 0, "LATENT"], [10, 6, 0, 7, 0, "IMAGE"],
    ]
    groups = [
        {"id": 1, "title": "Outer", "bounding": [60, 100, 1400, 650], "color": "#3f789e", "flags": {}},
        {"id": 2, "title": "Inner", "bounding": [80, 140, 380, 450], "color": "#3f789e", "flags": {}},
        {"id": 3, "title": "Overlap", "bounding": [1350, 150, 600, 300], "color": "#3f789e", "flags": {}},
        {"id": 4, "title": "Tiny", "bounding": [1500, 800, 100, 50], "color": "#3f789e", "flags": {}},
        {"id": 5, "title": "Off grid", "bounding": [1503.3, 900.7, 401.1, 203.9], "color": "#3f789e", "flags": {}},
    ]
    return _canvas(nodes, links, groups)


def links_canvas() -> dict[str, Any]:
    """Links running backwards, vertically and between collapsed nodes, for the spline rule."""
    o = lambda name, type_, links: {"name": name, "type": type_, "links": links}  # noqa: E731
    i = lambda name, type_, link: {"name": name, "type": type_, "link": link}  # noqa: E731
    nodes = [
        _node(1, "CheckpointLoaderSimple", [1200, 100], [315, 98], ["model.safetensors"],
              outputs=[o("MODEL", "MODEL", []), o("CLIP", "CLIP", [1, 2]), o("VAE", "VAE", [3])]),
        _node(2, "CLIPTextEncode", [100, 100], [400, 200], ["backwards"], inputs=[i("clip", "CLIP", 1)],
              outputs=[o("CONDITIONING", "CONDITIONING", [])]),
        _node(3, "CLIPTextEncode", [1200, 700], [400, 200], ["below"], collapsed=True, title="Below",
              inputs=[i("clip", "CLIP", 2)], outputs=[o("CONDITIONING", "CONDITIONING", [])]),
        _node(4, "VAEDecode", [1700, 400], [210, 46], [], collapsed=True,
              inputs=[i("samples", "LATENT", None), i("vae", "VAE", 3)], outputs=[o("IMAGE", "IMAGE", [])]),
    ]
    links = [[1, 1, 1, 2, 0, "CLIP"], [2, 1, 1, 3, 0, "CLIP"], [3, 1, 2, 4, 1, "VAE"]]
    return _canvas(nodes, links, [])


def multiline_canvas() -> dict[str, Any]:
    """Multi-line text boxes under the Vue renderer: one box at several saved heights, a box
    with a plain widget below it, two boxes, two boxes between a linked slot and a widget,
    plain widgets above two boxes (two of them advanced), a collapsed copy of each kind, and
    widget inputs fed by a link: two text boxes and an advanced widget."""
    o = lambda name, type_: {"name": name, "type": type_, "links": []}  # noqa: E731
    i = lambda name, type_: {"name": name, "type": type_, "link": None}  # noqa: E731
    cond, text = [o("CONDITIONING", "CONDITIONING")], [o("STRING", "STRING")]
    clip = [i("clip", "CLIP")]
    kinds: dict[str, tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]] = {
        "CLIPTextEncode": (["one box"], clip, cond),
        "StringTrim": (["box above a combo", "Both"], [], text),
        "StringConcatenate": (["box a", "box b", ","], [], text),
        "CLIPTextEncodeFlux": (["box l", "box t5", 3.5], clip, cond),
        "CLIPTextEncodeSDXL": ([1024, 1024, 0, 0, 1024, 1024, "box g", "box l"], clip, cond),
    }
    # (type, saved height, collapsed); one row of the canvas per tuple of the outer list
    rows = [
        [("CLIPTextEncode", 60, False), ("CLIPTextEncode", 102, False), ("CLIPTextEncode", 300, False),
         ("CLIPTextEncode", 450, False)],
        [("StringTrim", 60, False), ("StringTrim", 200, False), ("StringTrim", 400, False),
         ("CLIPTextEncode", 200, False)],
        [("StringConcatenate", 60, False), ("StringConcatenate", 200, False), ("StringConcatenate", 400, False),
         ("CLIPTextEncodeFlux", 60, False)],
        [("CLIPTextEncodeFlux", 250, False), ("CLIPTextEncodeFlux", 450, False), ("CLIPTextEncodeSDXL", 100, False),
         ("CLIPTextEncodeSDXL", 500, False)],
        [("CLIPTextEncode", 200, True), ("StringTrim", 200, True), ("StringConcatenate", 200, True),
         ("CLIPTextEncodeFlux", 250, True)],
    ]
    nodes = []
    for r, row in enumerate(rows):
        for c, (type_, height, collapsed) in enumerate(row):
            widgets, inputs, outputs = kinds[type_]
            nodes.append(_node(len(nodes) + 1, type_, [100 + 500 * c, 100 + 600 * r], [400, height],
                               copy.deepcopy(widgets), collapsed=collapsed,
                               inputs=copy.deepcopy(inputs), outputs=copy.deepcopy(outputs)))
    # Last row: widget inputs fed by links (a linked text box, a linked advanced widget).
    w = lambda name, type_, link: {"name": name, "type": type_, "widget": {"name": name}, "link": link}  # noqa: E731
    y, first = 100 + 600 * len(rows), len(nodes) + 1
    nodes += [
        _node(first, "PrimitiveInt", [100, y], [300, 82], [7, "fixed"], outputs=[o("INT", "INT") | {"links": [1]}]),
        _node(first + 1, "CLIPTextEncodeSDXL", [500, y], [400, 500], copy.deepcopy(kinds["CLIPTextEncodeSDXL"][0]),
              inputs=clip + [w("crop_w", "INT", 1)], outputs=copy.deepcopy(cond)),
        _node(first + 2, "StringTrim", [1000, y], [400, 200], ["source", "Both"], outputs=[o("STRING", "STRING") | {"links": [2, 3]}]),
        _node(first + 3, "CLIPTextEncode", [1500, y], [400, 200], ["linked"],
              inputs=copy.deepcopy(clip) + [w("text", "STRING", 2)], outputs=copy.deepcopy(cond)),
        _node(first + 4, "StringConcatenate", [2000, y], [400, 400], ["linked", "box b", ","],
              inputs=[w("string_a", "STRING", 3)], outputs=copy.deepcopy(text)),
    ]
    # Consecutive linked rows, and a linked number that has a value-control combo.
    y += 600
    nodes[first - 1 + 0]["outputs"][0]["links"] += [4, 5, 6, 7]
    nodes += [
        _node(first + 5, "EmptyLatentImage", [500, y], [315, 106], [512, 512, 1],
              inputs=[w("width", "INT", 4), w("height", "INT", 5), w("batch_size", "INT", 6)],
              outputs=[o("LATENT", "LATENT")]),
        _node(first + 6, "KSampler", [1000, y], [400, 300], [0, "fixed", 20, 8, "euler", "normal", 1],
              inputs=[i("model", "MODEL"), i("positive", "CONDITIONING"), i("negative", "CONDITIONING"),
                      i("latent_image", "LATENT"), w("seed", "INT", 7)],
              outputs=[o("LATENT", "LATENT")]),
    ]
    links = [[1, first, 0, first + 1, 1, "INT"], [2, first + 2, 0, first + 3, 1, "STRING"],
             [3, first + 2, 0, first + 4, 0, "STRING"], [4, first, 0, first + 5, 0, "INT"],
             [5, first, 0, first + 5, 1, "INT"], [6, first, 0, first + 5, 2, "INT"],
             [7, first, 0, first + 6, 4, "INT"]]
    return _canvas(nodes, links, [])


CANVASES = {"boxes": boxes_canvas, "links": links_canvas, "multiline": multiline_canvas}


def record(url: str, only: list[str] | None = None) -> int:
    """Write the fixtures of ``only`` (every canvas when ``None``); other fixtures stay as they are."""
    OUT.mkdir(parents=True, exist_ok=True)
    unknown = sorted(set(only or ()) - set(CANVASES))
    if unknown:
        raise SystemExit(f"unknown canvas: {', '.join(unknown)} (known: {', '.join(CANVASES)})")
    built = {name: make() for name, make in CANVASES.items() if only is None or name in only}
    for name, canvas in built.items():
        (OUT / f"{name}.canvas.json").write_text(json.dumps(canvas, indent=1) + "\n", encoding="utf-8")
    for run, (vue, settings) in RUNS.items():
        results = probe([copy.deepcopy(c) for c in built.values()], url, vue=vue, settings=settings)
        for name, result in zip(built, results):
            doc = {"canvas": f"{name}.canvas.json", "run": run, "settings_forced": settings, **result}
            path = OUT / f"{name}.{run}.json"
            path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geometry", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="load canvases, print node boxes, slot anchors, groups, links")
    p.add_argument("canvases", type=Path, nargs="+")
    p.add_argument("--url", default=os.environ.get("CWC_ORACLE_URL"))
    p.add_argument("--vue", action="store_true", help="enable Comfy.VueNodes.Enabled for this page")
    p.add_argument("--settings", type=json.loads, default=None, help="extra frontend settings as a JSON object")
    r = sub.add_parser("record", help="measure the built-in canvases and write tests/fixtures/layout/")
    r.add_argument("--url", default=os.environ.get("CWC_ORACLE_URL"))
    r.add_argument("--canvas", action="append", choices=sorted(CANVASES), default=None,
                   help="record only this canvas (repeatable); default: all")
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("--url is required (or set CWC_ORACLE_URL)")
    if args.cmd == "record":
        return record(args.url, args.canvas)
    canvases = [json.loads(path.read_text(encoding="utf-8")) for path in args.canvases]
    json.dump(probe(canvases, args.url, vue=args.vue, settings=args.settings), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
