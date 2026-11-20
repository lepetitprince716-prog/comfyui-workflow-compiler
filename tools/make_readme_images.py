"""Regenerate the pictures in docs/images/ that README.md and README.zh-CN.md show.

Usage, from the folder that holds ``pyproject.toml`` (the paths below are relative to it, and
the script finds them from its own location, so another working directory works too):

    python tools/make_readme_images.py --url URL [--only NAME,...] [--keep DIR]

Needs Python 3.10 or newer with Playwright and Pillow installed, and Google Chrome (the harness
launches it headless through Playwright's ``chrome`` channel). ``cwc`` itself is imported from
``src/``, so it does not have to be installed.

URL is a running ComfyUI server (see tools/oracle/ and docs/m0-findings.md). The script compiles
the graphs in ``docs/images/src/`` with ``cwc compile`` (in process, from ``src/``), opens each
canvas in the real frontend through ``tools/oracle/harness.py`` in both colour palettes, and
writes ``docs/images/<name>-<palette>.png``:

- ``hero``: the left part of the ``bento`` canvas of ``hires.json`` (the groups in
  ``HERO_GROUPS``), zoomed in the frontend so that node titles stay legible at 830 pixels.
- ``<preset>``: the whole canvas of ``landscape.json`` in each of the five presets, at the
  frontend's scale 1, shrunk to the same width. A canvas narrower than ``PRESET_RATIO`` (width
  over height) is widened to it with the frontend's empty grid, so none shows taller than that.

What the script changes in the page, and nothing else: the frontend's own menus, tabs and toasts
are hidden; canvas text is drawn in the system sans-serif, because the frontend's bundled Inter
renders slanted in headless Chrome; the minimap, canvas info and link midpoint markers are off;
the missing-model highlight is cleared (the server has no model files). The server is never
changed: the harness answers settings writes in the page. The pictures are then reduced to 256
colours so the page stays light.

Same server version, same machine fonts: in most runs the same bytes, though now and then one
picture comes out different (seen once: one picture of the twelve). Frontend and server
versions are printed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools" / "oracle"))

from cwc.cli import main as cwc_main  # noqa: E402
from harness import open_session  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: E402

SRC = ROOT / "docs" / "images" / "src"
OUT = ROOT / "docs" / "images"
PALETTES = ("dark", "light")
WIDTH = 1660  # every picture is shown at 830, the width of GitHub's content column
PAD = 40  # screen pixels around the content
HERO_GROUPS = ("01 Controls", "02 Models", "03 Sampling")
PRESET_RATIO = 2.1  # a narrower canvas is widened to this width over height


@dataclass(frozen=True)
class Shot:
    name: str
    graph: str  # file in docs/images/src/
    preset: str
    groups: tuple[str, ...] | None  # crop to these groups and zoom to WIDTH, or None: whole canvas


SHOTS = (
    Shot("hero", "hires.json", "bento", HERO_GROUPS),
    *(
        Shot(preset, "landscape.json", preset, None)
        for preset in ("bento", "swimlanes", "cockpit-split", "tower-preview", "stepped")
    ),
)

SETTINGS = {
    "Comfy.Minimap.Visible": False,
    "Comfy.Graph.CanvasInfo": False,
    "Comfy.Graph.LinkMarkers": 0,
    "Comfy.ErrorSystem.ShowMissingModels": False,
}

HIDE_CHROME = """
#graph-canvas-container > div.pointer-events-none { display: none !important; }
.z-toast, .p-toast, .p-dialog-mask { display: none !important; }
* { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif !important; font-style: normal !important; }
textarea.comfy-multiline-input { font-family: Menlo, Consolas, monospace !important; }
"""

LOAD = r"""
async ([wf, place]) => {
  // Load the canvas. Without ``place``, return the content bounds and the group boxes. With
  // ``place`` = [cx, cy, scale], draw the canvas at that scale with canvas point (cx, cy) in the
  // middle of the viewport.
  const frames = (n) => new Promise((r) => { const s = () => (n-- <= 0 ? r() : requestAnimationFrame(s)); s(); });
  const LG = window.LiteGraph;
  const sans = '-apple-system, "Helvetica Neue", Arial, sans-serif';
  LG.NODE_FONT = sans;
  LG.GROUP_FONT = sans;
  await window.app.loadGraphData(wf, true, false, null, {});
  const c = window.app.canvas;
  c.title_text_font = '14px ' + sans;
  c.inner_text_font = 'normal 12px ' + sans;
  c.render_info = false;
  c.setDirty(true, true);
  await frames(6);
  c.draw(true, true);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const add = (b) => { x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); x1 = Math.max(x1, b[0] + b[2]); y1 = Math.max(y1, b[1] + b[3]); };
  for (const n of window.app.graph._nodes) { n.has_errors = false; add(n.boundingRect); }
  const groups = {};
  for (const g of window.app.graph._groups) { const b = g._bounding || g.bounding; add(b); groups[g.title] = Array.from(b); }
  for (const r of (window.app.graph.reroutes?.values?.() ?? [])) add([r.pos[0] - 10, r.pos[1] - 10, 20, 20]);
  if (!place) return {bounds: [x0, y0, x1, y1], groups};
  const [cx, cy, scale] = place;
  c.ds.scale = scale;
  c.ds.offset[0] = c.canvas.width / window.devicePixelRatio / (2 * scale) - cx;
  c.ds.offset[1] = c.canvas.height / window.devicePixelRatio / (2 * scale) - cy;
  c.deselectAll && c.deselectAll();
  c.setDirty(true, true);
  await frames(12);
  c.draw(true, true);
  await frames(6);
  return null;
}
"""


def compile_canvas(shot: Shot, work: Path) -> dict:
    out = work / shot.name
    graph = SRC / shot.graph
    with redirect_stdout(io.StringIO()):
        code = cwc_main(["compile", str(graph), "-o", str(out), "--preset", shot.preset, "--json"])
    if code != 0:
        raise SystemExit(f"cwc compile {shot.graph} --preset {shot.preset} exited {code}")
    return json.loads((out / f"{graph.stem}.ui.json").read_text(encoding="utf-8"))


def run_in_page(url: str, palette: str, viewport: tuple[int, int], work):
    """Open the frontend and call ``work(session)``. The frontend's boot now and then stalls in
    headless Chrome; such a boot is retried, up to three times."""
    settings = {**SETTINGS, "Comfy.ColorPalette": palette}
    for attempt in range(3):
        try:
            with open_session(url, settings=settings, viewport=viewport, timeout_ms=45_000) as s:
                return work(s)
        except PlaywrightTimeout:
            if attempt == 2:
                raise
            print(f"frontend boot timed out, retrying ({attempt + 1})", file=sys.stderr)
    raise AssertionError("unreachable")


def plan(shot: Shot, measured: dict) -> tuple[tuple[float, float, float], tuple[int, int]]:
    """Where to look, at what scale, through what viewport: ``((cx, cy, scale), (w, h))``."""
    if shot.groups is None:
        x0, y0, x1, y1 = measured["bounds"]
        scale = 1.0
        width, height = math.ceil(x1 - x0) + 2 * PAD, math.ceil(y1 - y0) + 2 * PAD
        if width / height < PRESET_RATIO:
            width = math.ceil(height * PRESET_RATIO)
    else:
        missing = [g for g in shot.groups if g not in measured["groups"]]
        if missing:
            raise SystemExit(f"{shot.name}: no group titled {missing} in the canvas")
        boxes = [measured["groups"][g] for g in shot.groups]
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        scale = (WIDTH - 2 * PAD) / (x1 - x0)
        width, height = WIDTH, math.ceil((y1 - y0) * scale) + 2 * PAD
    return ((x0 + x1) / 2, (y0 + y1) / 2, scale), (width, height)


def finish(png: bytes) -> bytes:
    """Shrink to WIDTH and reduce to 256 colours."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.width != WIDTH:
        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
        out, format="PNG", optimize=True
    )
    return out.getvalue()


def shoot(url: str, shot: Shot, workflow: dict, palette: str) -> tuple[bytes, dict]:
    measured = run_in_page(url, palette, (1600, 900), lambda s: s.page.evaluate(LOAD, [workflow, None]))
    place, viewport = plan(shot, measured)

    def capture(s):
        s.page.add_style_tag(content=HIDE_CHROME)
        s.page.evaluate(LOAD, [workflow, list(place)])
        s.page.wait_for_timeout(500)
        return s.page.screenshot(type="png", animations="disabled", caret="hide"), s.versions()

    png, versions = run_in_page(url, palette, viewport, capture)
    return finish(png), versions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="a running ComfyUI server")
    parser.add_argument("--only", help="comma-separated picture names (default: all)")
    parser.add_argument("--keep", type=Path, help="keep the compiled canvases in this folder")
    args = parser.parse_args(argv)
    shots = [s for s in SHOTS if not args.only or s.name in args.only.split(",")]
    versions: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = args.keep or Path(tmp)
        for shot in shots:
            workflow = compile_canvas(shot, work)
            for palette in PALETTES:
                data, versions = shoot(args.url, shot, workflow, palette)
                name = f"{shot.name}-{palette}.png"
                (OUT / name).write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()[:12]
                print(f"{name:28} {len(data) // 1024:5d} KiB  {digest}")
    print("frontend", versions.get("frontend"), "server", versions.get("comfyui"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
