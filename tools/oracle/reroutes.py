"""Native reroutes as a real frontend stores, loads and draws them (M2 router facts).

Usage (runs in the oracle's Python, which has Playwright):
    python tools/oracle/reroutes.py record --url URL
    python tools/oracle/reroutes.py canvases          # only rewrite the synthetic canvases

``record`` builds the synthetic canvases (core nodes, reroutes placed by hand), loads each in the
classic renderer, the Vue renderer and the classic renderer with snap to grid on, and writes
``tests/fixtures/layout/reroutes.canvases.json`` (name -> canvas) and
``tests/fixtures/layout/reroutes.<run>.json`` (name -> measurements). Per canvas the page side
(``js/reroutes.js``) reports the ``serialize()`` result right after the load, the live reroutes and
links, a serialize/load/serialize round trip, what the canvas drew for links through reroutes
(every ``drawLink`` call with its control points, the predicted segments tested with
``isPointInStroke`` against the stroked ``Path2D``, the arcs drawn at each reroute) and, for the
group canvas, group membership of reroutes and the effect of dragging each group.
``--url`` defaults to ``$CWC_ORACLE_URL``.
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

from geometry import RUNS, _canvas, _node  # noqa: E402
from harness import open_session  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures" / "layout"
JS = Path(__file__).resolve().parent / "js" / "reroutes.js"


def _o(name: str, type_: str, links: list[int]) -> dict[str, Any]:
    return {"name": name, "type": type_, "links": links}


def _i(name: str, type_: str, link: int | None) -> dict[str, Any]:
    return {"name": name, "type": type_, "link": link}


def _clip(nid: int, pos: list[float], link: int | None) -> dict[str, Any]:
    return _node(nid, "CLIPTextEncode", pos, [400, 200], ["x"], inputs=[_i("clip", "CLIP", link)],
                 outputs=[_o("CONDITIONING", "CONDITIONING", [])])


def _loader(clip_links: list[int], vae_links: list[int] | None = None) -> dict[str, Any]:
    return _node(1, "CheckpointLoaderSimple", [100, 200], [315, 98], ["model.safetensors"],
                 outputs=[_o("MODEL", "MODEL", []), _o("CLIP", "CLIP", clip_links), _o("VAE", "VAE", vae_links or [])])


def _rr(id_: int, pos: list[float], link_ids: list[int], parent: int | None = None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": id_}
    if parent is not None:
        row["parentId"] = parent
    row.update({"pos": pos, "linkIds": link_ids})
    row.update(extra)
    return row


def _with_extra(canvas: dict[str, Any], **extra: Any) -> dict[str, Any]:
    canvas["extra"] = extra
    return canvas


def main_canvas() -> dict[str, Any]:
    """A chain of three, a bus tree under one output (shared root, two branches, a short branch), one single reroute."""
    nodes = [
        _loader([1, 2, 3, 4], [5]),
        _clip(2, [900, 100], 1), _clip(3, [900, 350], 2), _clip(4, [900, 600], 3), _clip(5, [900, 850], 4),
        _node(6, "VAEDecode", [1450, 1000], [210, 46], [], inputs=[_i("samples", "LATENT", None), _i("vae", "VAE", 5)],
              outputs=[_o("IMAGE", "IMAGE", [])]),
    ]
    links = [[1, 1, 1, 2, 0, "CLIP"], [2, 1, 1, 3, 0, "CLIP"], [3, 1, 1, 4, 0, "CLIP"], [4, 1, 1, 5, 0, "CLIP"],
             [5, 1, 2, 6, 1, "VAE"]]
    reroutes = [
        _rr(1, [453.7, 447.3], [1]), _rr(2, [600, 650], [1], 1), _rr(3, [750, 150], [1], 2),
        _rr(4, [500, 300], [2, 3, 4]), _rr(5, [700, 420], [2], 4), _rr(6, [700, 700], [3], 4),
        _rr(7, [1000, 1150], [5]),
    ]
    ext = [{"id": 1, "parentId": 3}, {"id": 2, "parentId": 5}, {"id": 3, "parentId": 6}, {"id": 4, "parentId": 4},
           {"id": 5, "parentId": 7}]
    return _with_extra(_canvas(nodes, links, []), reroutes=reroutes, linkExtensions=ext)


def short_canvas() -> dict[str, Any]:
    """Short segments (under 80 px), so 0.25 * distance is below the control offset cap; a shared root with two branches."""
    nodes = [_loader([1, 2]), _clip(2, [600, 280], 1), _clip(3, [600, 60], 2)]
    links = [[1, 1, 1, 2, 0, "CLIP"], [2, 1, 1, 3, 0, "CLIP"]]
    reroutes = [_rr(1, [440, 250], [1, 2]), _rr(2, [470, 300], [1], 1), _rr(3, [520, 300], [1], 2), _rr(4, [480, 220], [2], 1)]
    return _with_extra(_canvas(nodes, links, []), reroutes=reroutes,
                       linkExtensions=[{"id": 1, "parentId": 3}, {"id": 2, "parentId": 4}])


def _two_links(reroutes: list[dict[str, Any]], ext: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = [_loader([1, 2]), _clip(2, [900, 100], 1), _clip(3, [900, 400], 2)]
    links = [[1, 1, 1, 2, 0, "CLIP"], [2, 1, 1, 3, 0, "CLIP"]]
    return _with_extra(_canvas(nodes, links, []), reroutes=reroutes, linkExtensions=ext)


def defect_canvases() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    # link 1 through reroute 1 (fine), link 2 through reroute 2 whose parent 99 does not exist
    out["parent_missing"] = _two_links(
        [_rr(1, [500, 150], [1]), _rr(2, [500, 450], [2], 99)],
        [{"id": 1, "parentId": 1}, {"id": 2, "parentId": 2}])
    # link 2 names reroute 77, which is not in the list
    out["extension_reroute_missing"] = _two_links(
        [_rr(1, [500, 150], [1])], [{"id": 1, "parentId": 1}, {"id": 2, "parentId": 77}])
    # an extension names link 55, which does not exist
    out["extension_link_missing"] = _two_links(
        [_rr(1, [500, 150], [1])], [{"id": 1, "parentId": 1}, {"id": 55, "parentId": 1}])
    # linkIds disagree: reroute 1 claims link 2, reroute 2 claims nothing, extensions say 1 -> r1, 2 -> r2
    out["linkids_disagree"] = _two_links(
        [_rr(1, [500, 150], [2]), _rr(2, [500, 450], [])], [{"id": 1, "parentId": 1}, {"id": 2, "parentId": 2}])
    # linkIds only, no extensions at all (the form an older writer produced)
    out["linkids_only"] = _two_links([_rr(1, [500, 150], [1]), _rr(2, [500, 450], [2])], [])
    # a reroute no link passes through, with and without linkIds
    out["orphan"] = _two_links(
        [_rr(1, [500, 150], [1]), _rr(2, [500, 450], []), _rr(3, [600, 450], [2])], [{"id": 1, "parentId": 1}])
    # a parent cycle: r1 -> r2 -> r1
    out["cycle"] = _two_links(
        [_rr(1, [500, 150], [1, 2], 2), _rr(2, [500, 450], [1, 2], 1)], [{"id": 1, "parentId": 1}, {"id": 2, "parentId": 2}])
    # two entries with the same id: the later one wins?
    out["duplicate_id"] = _two_links(
        [_rr(1, [500, 150], [1]), _rr(1, [800, 450], [2])], [{"id": 1, "parentId": 1}, {"id": 2, "parentId": 1}])
    # a reroute without pos
    out["pos_missing"] = _two_links(
        [{"id": 1, "linkIds": [1]}, _rr(2, [500, 450], [2])], [{"id": 1, "parentId": 1}, {"id": 2, "parentId": 2}])
    # one reroute shared by links of two different outputs (not a bus under one output)
    nodes = [
        _node(1, "CheckpointLoaderSimple", [100, 200], [315, 98], ["model.safetensors"],
              outputs=[_o("MODEL", "MODEL", []), _o("CLIP", "CLIP", [1]), _o("VAE", "VAE", [2])]),
        _clip(2, [900, 100], 1),
        _node(3, "VAEDecode", [900, 400], [210, 46], [], inputs=[_i("samples", "LATENT", None), _i("vae", "VAE", 2)],
              outputs=[_o("IMAGE", "IMAGE", [])]),
    ]
    links = [[1, 1, 1, 2, 0, "CLIP"], [2, 1, 2, 3, 1, "VAE"]]
    out["cross_output"] = _with_extra(_canvas(nodes, links, []), reroutes=[_rr(1, [600, 300], [1, 2])],
                                      linkExtensions=[{"id": 1, "parentId": 1}, {"id": 2, "parentId": 1}])
    return out


def floating_canvas() -> dict[str, Any]:
    """Floating links: one dangling to the input side, one dangling from the output side, a flagged reroute with none."""
    nodes = [_loader([1]), _clip(2, [900, 100], 1), _clip(3, [900, 450], None)]
    links = [[1, 1, 1, 2, 0, "CLIP"]]
    canvas = _canvas(nodes, links, [])
    canvas["floatingLinks"] = [
        {"id": 3, "origin_id": 1, "origin_slot": 1, "target_id": -1, "target_slot": -1, "type": "CLIP", "parentId": 1},
        {"id": 4, "origin_id": -1, "origin_slot": -1, "target_id": 3, "target_slot": 0, "type": "CLIP", "parentId": 2},
    ]
    return _with_extra(canvas, reroutes=[
        _rr(1, [500, 350], [], floating={"slotType": "output"}),
        _rr(2, [600, 450], [], floating={"slotType": "input"}),
        _rr(3, [600, 700], [], floating={"slotType": "output"}),
    ])


def schema_v1_canvas() -> dict[str, Any]:
    """The 1.0 schema: top-level reroutes and links as objects carrying parentId."""
    nodes = [_loader([1]), _clip(2, [900, 100], 1)]
    return {
        "version": 1, "state": {"lastGroupId": 0, "lastNodeId": 2, "lastLinkId": 1, "lastRerouteId": 1},
        "nodes": nodes, "groups": [], "config": {}, "extra": {},
        "links": [{"id": 1, "origin_id": 1, "origin_slot": 1, "target_id": 2, "target_slot": 0, "type": "CLIP", "parentId": 1}],
        "reroutes": [_rr(1, [500, 300], [1])],
    }


def groups_canvas() -> dict[str, Any]:
    """One link through nine reroutes placed on and around the edges of a group and a group nested in it."""
    nodes = [_loader([1]), _clip(2, [1300, 200], 1), _clip(3, [450, 260], None)]
    points = [[350, 400], [500, 350], [300, 300], [1100, 300], [600, 100], [600, 700], [600, 120], [1200, 400], [400, 250]]
    reroutes = [_rr(n + 1, p, [1], n if n else None) for n, p in enumerate(points)]
    groups = [
        {"id": 1, "title": "Outer", "bounding": [300, 100, 800, 600], "color": "#3f789e", "flags": {}},
        {"id": 2, "title": "Inner", "bounding": [400, 200, 300, 300], "color": "#3f789e", "flags": {}},
    ]
    canvas = _canvas(nodes, [[1, 1, 1, 2, 0, "CLIP"]], groups)
    return _with_extra(canvas, reroutes=reroutes, linkExtensions=[{"id": 1, "parentId": 9}])


def all_canvases() -> dict[str, dict[str, Any]]:
    out = {"main": main_canvas(), "short": short_canvas(), "groups": groups_canvas(), "floating": floating_canvas(), "schema_v1": schema_v1_canvas()}
    out.update(defect_canvases())
    return out


# Canvases whose drawing is measured (the rest only load and serialize), and the one with group tests.
DRAWN = {"main", "short", "cross_output", "floating", "groups", "parent_missing", "extension_reroute_missing", "cycle"}
GROUP_TESTS = {"groups"}


# Extra runs beyond geometry.RUNS: name -> (vue, settings, canvases measured).
EXTRA_RUNS: dict[str, tuple[bool, dict[str, Any], set[str]]] = {
    "classic_offset80": (False, {"LiteGraph.Reroute.SplineOffset": 80}, {"main", "short"}),
}


def probe_all(canvases: dict[str, dict[str, Any]], url: str, *, vue: bool, settings: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with open_session(url, vue=vue, settings=settings) as s:
        s.page.evaluate(JS.read_text(encoding="utf-8"))
        versions = s.versions()
        for name, canvas in canvases.items():
            s.log.phase = f"canvas:{name}"
            result = s.page.evaluate(
                "([canvas, opts]) => window.__cwcReroutes.probe(canvas, opts)",
                [copy.deepcopy(canvas), {"draw": name in DRAWN, "groups": name in GROUP_TESTS}],
            )
            result["console"] = s.log.take(s.log.phase)
            results[name] = result
    return {"versions": versions, "renderer": "vue" if versions["vue_nodes"] else "classic", "results": results}


def record(url: str) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    canvases = all_canvases()
    (OUT / "reroutes.canvases.json").write_text(json.dumps(canvases, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote tests/fixtures/layout/reroutes.canvases.json")
    runs = {run: (vue, settings, set(canvases)) for run, (vue, settings) in RUNS.items()} | EXTRA_RUNS
    for run, (vue, settings, names) in runs.items():
        picked = {name: canvas for name, canvas in canvases.items() if name in names}
        doc = {"canvases": "reroutes.canvases.json", "run": run, "settings_forced": settings,
               **probe_all(picked, url, vue=vue, settings=settings)}
        path = OUT / f"reroutes.{run}.json"
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reroutes", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="measure the synthetic canvases and write tests/fixtures/layout/reroutes.*.json")
    r.add_argument("--url", default=os.environ.get("CWC_ORACLE_URL"))
    sub.add_parser("canvases", help="only rewrite reroutes.canvases.json")
    args = parser.parse_args(argv)
    if args.cmd == "canvases":
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "reroutes.canvases.json").write_text(json.dumps(all_canvases(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    if not args.url:
        parser.error("--url is required (or set CWC_ORACLE_URL)")
    return record(args.url)


if __name__ == "__main__":
    raise SystemExit(main())
