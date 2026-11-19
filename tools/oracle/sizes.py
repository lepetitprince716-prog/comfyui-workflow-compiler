"""Node size measurements (M0 checklist #4 and #6) under the classic and the Vue renderer.

Usage (through m0.py, which prints the report):
    python tools/oracle/m0.py --url URL --only vue_sizes,markdown

vue_sizes: every node of ``sizes_small.json`` and ``sizes_large.json`` is loaded on its own
canvas, once per renderer, and measured: saved size, live size, bounding box, the Vue node
element's box (title bar included) and the DOM parts inside it. The two files differ only in
the saved size, so the report shows whether the Vue renderer grows a node and to what.

markdown: a series of hand-written MarkdownNote bodies (``tests/fixtures/oracle/markdown_cases.json``)
is rendered at two widths under both renderers. Per block kind (heading level, paragraph, table,
list; ASCII or CJK text) the report holds the height per line, row or item and the margins, read
from the DOM; the body height predicted from kinds and counts alone is compared with the
measured one; and the constants that cover both renderers are checked against every case.

Nothing here asserts. Every number depends on the machine's default fonts and on the
window size, both recorded in the report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness import open_session

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "oracle"
TITLE_HEIGHT = 30  # LiteGraph.NODE_TITLE_HEIGHT; the Vue node box starts this far above ``pos``
PREVIEW_IMAGE = (512, 768)  # the PNG that stands in for every image the server does not have
PREVIEW_OUTPUTS = [{"filename": "cwc.png", "subfolder": "", "type": "temp"}]
# Nodes whose execution result is faked so they show an image (LoadImage shows its own value).
OUTPUT_TYPES = {"SaveImage", "PreviewImage"}


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def layout(s: Any, canvas: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    """``layout`` in the page. A busy machine sometimes returns nothing: try again before giving up."""
    for _ in range(4):
        result = s.call("layout", canvas, opts)
        if result is not None:
            return result
    raise RuntimeError("the page returned no layout after 4 tries")


def single_node_canvas(canvas: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """The canvas reduced to one node placed at (100, 100): measured alone, nothing overlaps."""
    only = dict(node, pos=[100, 100])
    return {**canvas, "nodes": [only], "links": []}


def _environment(s: Any) -> dict[str, Any]:
    env = s.page.evaluate(
        "() => ({ user_agent: navigator.userAgent, window: [window.innerWidth, window.innerHeight],"
        " device_pixel_ratio: window.devicePixelRatio })"
    )
    return {**s.versions(), **env}


def measure_node(s: Any, canvas: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    outputs = {str(node["id"]): PREVIEW_OUTPUTS} if node["type"] in OUTPUT_TYPES else {}
    r = layout(s, single_node_canvas(canvas, node), {"outputs": outputs})
    row = r["nodes"][0]
    box = row["vue_box"]
    return {
        "type": node["type"],
        "saved_size": node["size"],
        "load_error": r["load_error"],
        "live_size": row["size"],
        "classic_min_size": row["min_size"],
        "bounding": row["bounding"],
        "canvas_scale": r["scale"],
        "serialized_size": r["serialized_sizes"].get(str(node["id"])),
        "images": row["imgs"],
        "vue_box": box,
        # body = box minus the title bar: comparable to the saved size
        "vue_body": None if box is None else [box[2], box[3] - TITLE_HEIGHT],
        "parts": row["parts"],
        "widget_elements": [
            {"name": w["name"], "tag": w["element"]["tag"], "size": w["element"]["size"]}
            for w in row["widgets"]
            if w["element"]
        ],
    }


def _small_node(type_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    canvas = _load("sizes_small.json")
    return canvas, next(n for n in canvas["nodes"] if n["type"] == type_name)


def check_vue_sizes(url: str) -> dict[str, Any]:
    """#6: node outer box under both renderers, from a small and a large saved size.

    Besides the two fixture files: previews of other aspect ratios (does the Vue image area
    depend on the image?) and long text in the multi-line boxes (does it stretch the node?).
    """
    report: dict[str, Any] = {"nodes": {}, "preview_aspect": {}, "long_text": {}, "environment": {}}
    long_text = "\n".join(f"line {i} of a long prompt" for i in range(1, 41))
    for vue in (False, True):
        label = "vue" if vue else "classic"
        with open_session(url, vue=vue, view_image=PREVIEW_IMAGE) as s:
            report["environment"][label] = _environment(s)
            for variant in ("small", "large"):
                canvas = _load(f"sizes_{variant}.json")
                for node in canvas["nodes"]:
                    report["nodes"].setdefault(node["type"], {}).setdefault(variant, {})[label] = measure_node(s, canvas, node)
            for type_name in ("CLIPTextEncode", "Note"):
                canvas, node = _small_node(type_name)
                node = dict(node, widgets_values=[long_text])
                report["long_text"].setdefault(type_name, {})[label] = measure_node(s, canvas, node)
        for image_size in ((768, 512), (64, 64)):
            with open_session(url, vue=vue, view_image=image_size) as s:
                for type_name in ("LoadImage", "PreviewImage"):
                    canvas, node = _small_node(type_name)
                    key = f"{image_size[0]}x{image_size[1]}"
                    report["preview_aspect"].setdefault(key, {}).setdefault(type_name, {})[label] = measure_node(s, canvas, node)
    return report


# --- markdown (#4) ---------------------------------------------------------------------------

SMALL_HEIGHT = 60  # saved height small enough that the body overflows (classic) or the node must grow (Vue)


def md_canvas(text: str, width: int, height: int) -> dict[str, Any]:
    node = {
        "id": 1, "type": "MarkdownNote", "pos": [100, 100], "size": [width, height], "flags": {}, "order": 0,
        "mode": 0, "inputs": [], "outputs": [], "properties": {}, "widgets_values": [text],
    }
    return {"last_node_id": 1, "last_link_id": 0, "nodes": [node], "links": [], "groups": [], "config": {}, "extra": {}, "version": 0.4}


def md_features(blocks: list[dict[str, Any]]) -> dict[str, float]:
    """Counts the fit uses: lines per heading level, paragraph lines and blocks, table rows, list items."""
    f = {"h1": 0, "h2": 0, "h3": 0, "p_lines": 0, "p_blocks": 0, "table_rows": 0, "table_blocks": 0,
         "list_items": 0, "list_blocks": 0}
    for b in blocks:
        tag = b["tag"]
        if tag in ("h1", "h2", "h3"):
            f[tag] += b["lines"]
        elif tag == "p":
            f["p_lines"] += b["lines"]
            f["p_blocks"] += 1
        elif b.get("rows") is not None:
            f["table_rows"] += len(b["rows"])
            f["table_blocks"] += 1
        elif b.get("items") is not None:
            f["list_items"] += len(b["items"])
            f["list_blocks"] += 1
    return f


def measure_markdown_case(s: Any, vue: bool, text: str, width: int) -> dict[str, Any]:
    r = layout(s, md_canvas(text, width, SMALL_HEIGHT), {})
    row, md = r["nodes"][0], r["markdown"]["1"]
    out: dict[str, Any] = {
        "load_error": r["load_error"],
        "live_size": row["size"],
        "extent": md["extent"],
        "scroll_height": md["scroll_height"],
        "client_height": md["client_height"],
        "features": md_features(md["blocks"]),
        "blocks": md["blocks"],
        "font_size_var": md["font_size_var"],
    }
    if vue:
        box = row["vue_box"]
        out["vue_body"] = box[3] - TITLE_HEIGHT
        out["vue_overhead"] = out["vue_body"] - md["extent"]
    else:
        out["classic_overhead"] = row["size"][1] - md["host_size"][1]
        needed = out["extent"] + out["classic_overhead"]
        out["classic_needed"] = needed
        # Does a node of exactly that height show the whole body, and one 3 high less not?
        for label, height in (("at_needed", needed), ("below_needed", needed - 3)):
            if height < row["min_size"][1]:
                out[f"overflow_{label}"] = None  # classic raises the node to its own minimum height
                continue
            t = layout(s, md_canvas(text, width, height), {})
            m = t["markdown"]["1"]
            out[f"overflow_{label}"] = m["scroll_height"] - m["client_height"]
    return out


def block_kind(b: dict[str, Any]) -> str:
    """Kind of a rendered block: heading level, paragraph, table or list; ``_cjk`` when it holds CJK text.

    The classic renderer's fallback font for CJK gives taller lines than its ASCII font; the
    Vue renderer's lines have a fixed height, so both kinds measure the same there.
    """
    if b.get("rows") is not None:
        return "table"
    tag = "list" if b.get("items") is not None else b["tag"]
    return tag + "_cjk" if b.get("cjk") else tag


def block_count(kind: str, b: dict[str, Any]) -> int:
    """How many units a block's height counts in: text lines, table rows or list items."""
    if kind == "table":
        return len(b["rows"])
    if kind.startswith("list"):
        return len(b["items"])
    return b["lines"]


def line_fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Exact line through (count, height) points by least squares: height = unit * count + base."""
    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return (sy / sx if sx else 0.0, 0.0)  # one count only: no base can be told apart
    unit = (n * sxy - sx * sy) / denom
    return unit, (sy - unit * sx) / n


def kind_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per block kind, read straight from the DOM: height = unit * count + base, and the margins.

    mt_first / mb_last are the margins when the block opens or closes the note; mt_mid / mb_mid
    when it sits between blocks. A kind with no sample in some position reuses the other one.
    """
    pts: dict[str, list[tuple[int, float]]] = {}
    margins: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        blocks = r["blocks"]
        for i, b in enumerate(blocks):
            k = block_kind(b)
            pts.setdefault(k, []).append((block_count(k, b), b["height"]))
            slots = margins.setdefault(k, {"mt_first": [], "mt_mid": [], "mb_mid": [], "mb_last": []})
            if i > 0:
                slots["mt_mid"].append(b["margin_top"])
            else:
                slots["mt_first"].append(b["margin_top"])
            if i < len(blocks) - 1:
                slots["mb_mid"].append(b["margin_bottom"])
            else:
                slots["mb_last"].append(b["margin_bottom"])
    table: dict[str, dict[str, float]] = {}
    for k, points in sorted(pts.items(), key=lambda kv: kv[0].endswith("_cjk")):
        unit, base = line_fit(points)
        m = margins[k]
        if k.endswith("_cjk") and k.removesuffix("_cjk") in table:
            # Margins do not depend on the text, and a handful of CJK samples cannot tell a
            # base from a unit: take the base (and the margins) of the plain kind.
            plain = table[k.removesuffix("_cjk")]
            base = plain["base"]
            unit = sum((h - base) / n for n, h in points) / len(points)
            table[k] = {**{f: plain[f] for f in ("mt_first", "mt_mid", "mb_mid", "mb_last")},
                        "unit": round(unit, 3), "base": base, "samples": len(points)}
            continue
        mid_t = max(m["mt_mid"], default=None)
        mid_b = max(m["mb_mid"], default=None)
        table[k] = {
            "unit": round(unit, 3),
            "base": round(base, 3),
            "mt_first": max(m["mt_first"], default=0.0),
            "mt_mid": mid_t if mid_t is not None else max(m["mt_first"], default=0.0),
            "mb_mid": mid_b if mid_b is not None else max(m["mb_last"], default=0.0),
            "mb_last": max(m["mb_last"], default=0.0),
            "samples": len(points),
        }
    return table


def predict_extent(blocks: list[dict[str, Any]], table: dict[str, dict[str, float]]) -> float:
    """Body height from block kinds and counts alone, margins collapsing between neighbours.

    Only (kind, count) of each block is used, never its measured height, so the residual
    against the measured extent tests the constants and the collapse rule together.
    """
    total = 0.0
    kinds = [block_kind(b) for b in blocks]
    for i, (b, k) in enumerate(zip(blocks, kinds)):
        t = table[k]
        total += t["unit"] * block_count(k, b) + t["base"]
        if i == 0:
            total += t["mt_first"]
        if i == len(blocks) - 1:
            total += t["mb_last"]
        else:
            total += max(t["mb_mid"], table[kinds[i + 1]]["mt_mid"])
    return total


def envelope_constants(tables: dict[str, dict[str, dict[str, float]]]) -> dict[str, float]:
    """One constant set that is never below either renderer, in the per-line form (a height per
    line, row or item, and a gap per block).

    Each block is charged its height plus its largest margin (a collapsed gap never exceeds
    either neighbour's margin), so a sum of these is an upper bound of the exact height.
    """
    def variants(t: dict[str, dict[str, float]], kind: str) -> list[dict[str, float]]:
        return [v for k, v in t.items() if k in (kind, kind + "_cjk")]

    def over(kind: str, field: str) -> float:
        return max((v[field] for t in tables.values() for v in variants(t, kind)), default=0.0)

    def gap(kind: str) -> float:
        return max(
            (max(v["mt_mid"], v["mb_mid"], v["mb_last"]) for t in tables.values() for v in variants(t, kind)),
            default=0.0,
        )

    return {
        "h1_line": round(over("h1", "unit") + gap("h1"), 2),
        "h2_line": round(over("h2", "unit") + gap("h2"), 2),
        "h3_line": round(over("h3", "unit") + gap("h3"), 2),
        "p_line": over("p", "unit"),
        "p_block_gap": gap("p"),
        "table_row": over("table", "unit"),
        "table_block_extra": max(over("table", "base"), 0.0) + gap("table"),
        "list_item": over("list", "unit"),
        "list_block_extra": max(over("list", "base"), 0.0) + gap("list"),
    }


def envelope_extent(blocks: list[dict[str, Any]], c: dict[str, float]) -> float:
    total = 0.0
    for b in blocks:
        k = block_kind(b).removesuffix("_cjk")
        n = block_count(block_kind(b), b)
        if k in ("h1", "h2", "h3"):
            total += n * c[f"{k}_line"]
        elif k == "p":
            total += n * c["p_line"] + c["p_block_gap"]
        elif k == "table":
            total += n * c["table_row"] + c["table_block_extra"]
        else:
            total += n * c["list_item"] + c["list_block_extra"]
    return total


def residuals(values: list[float]) -> dict[str, float]:
    return {
        "cases": len(values),
        "rms": round((sum(v * v for v in values) / len(values)) ** 0.5, 3),
        "max_abs": round(max(abs(v) for v in values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def measure_corpus(url: str, vue: bool, spec: dict[str, Any], widths: list[int], settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    with open_session(url, vue=vue, settings=settings) as s:
        env = _environment(s)
        for case in spec["cases"]:
            for width in widths:
                m = measure_markdown_case(s, vue, case["text"], width)
                m.update(case=case["name"], kind=case["kind"], width=width)
                rows.append(m)
    return env, rows


def check_markdown(url: str) -> dict[str, Any]:
    """#4: MarkdownNote body height per renderer and width, constants read per block kind.

    ``kind_table``: height per line, row or item and margins, read from the DOM per renderer.
    ``prediction``: residual of the body height predicted from block kinds and counts only.
    ``envelope``: the constants a compiler can use for both renderers, and its slack against
    what a node must be to show the whole body (classic: extent + overhead; Vue: extent + overhead).
    ``font_size_14``: the same at Comfy.TextareaWidget.FontSize 14 (width 400 only).
    """
    spec = _load("markdown_cases.json")
    report: dict[str, Any] = {"environment": {}, "cases": {}, "kind_table": {}, "prediction": {}, "overhead": {}}
    tables: dict[str, dict[str, dict[str, float]]] = {}
    all_rows: dict[str, list[dict[str, Any]]] = {}
    for vue in (False, True):
        label = "vue" if vue else "classic"
        env, rows = measure_corpus(url, vue, spec, spec["widths"])
        all_rows[label] = rows
        report["environment"][label] = env
        report["cases"][label] = rows
        tables[label] = kind_table(rows)
        report["kind_table"][label] = tables[label]
        report["prediction"][label] = residuals([r["extent"] - predict_extent(r["blocks"], tables[label]) for r in rows])
        key = "vue_overhead" if vue else "classic_overhead"
        # Vue: only cases where the node grew past its own minimum say anything about the overhead.
        values = [r[key] for r in rows if (not vue) or r["vue_body"] > r["live_size"][1] + 0.5]
        report["overhead"][label] = {"min": min(values), "max": max(values), "cases": len(values)}
    env_const = envelope_constants(tables)
    report["envelope"] = {"constants": env_const, "pad": {k: v["max"] for k, v in report["overhead"].items()}}
    pad = max(v["max"] for v in report["overhead"].values())
    slack = []
    for label, rows in all_rows.items():
        for r in rows:
            # Vue's own overhead is only readable where the node grew; the measured constant stands for the rest.
            need = r["extent"] + report["overhead"][label]["max"]
            slack.append((envelope_extent(r["blocks"], env_const) + pad - need, f"{label}:{r['case']}:{r['width']}"))
    report["envelope"]["slack"] = residuals([v for v, _ in slack])  # min below 0 would mean the envelope under-sizes a case
    report["envelope"]["tightest_case"] = min(slack)[1]
    report["font_size_14"] = {}
    for vue in (False, True):
        label = "vue" if vue else "classic"
        env, rows = measure_corpus(url, vue, spec, [400], {"Comfy.TextareaWidget.FontSize": 14})
        table14 = kind_table(rows)
        report["font_size_14"][label] = {
            "font_size_var": rows[0]["font_size_var"],
            "kind_table": table14,
            "overhead": sorted({r["classic_overhead"] if not vue else round(r["vue_overhead"], 2) for r in rows if (not vue) or r["vue_body"] > r["live_size"][1] + 0.5}),
            "prediction": residuals([r["extent"] - predict_extent(r["blocks"], table14) for r in rows]),
        }
    return report
