"""Measure text widths in a real ComfyUI frontend and write ``src/cwc/data/char_widths.json``.

Usage (runs in the oracle's Python, which has Playwright; see ``tests/oracle_support.py``):
    python tools/oracle/char_widths.py record [--url URL] [--measure-only OUT.json]

Two font roles decide what cwc needs to know about text (frontend 1.59.1, see ``fonts_from_page``):

* ``title``: ``LGraphNode.titleFontStyle`` (14 px Inter), the font a node title is drawn in and
  that ``computeSize`` measures the title with.
* ``widget``: ``LGraphNode.innerFontStyle`` (``normal 12 px Inter``), the font of slot and widget
  labels, and the font ``LGraphNode.measure`` measures a collapsed node's title with.

For each role the page's own ``CanvasRenderingContext2D.measureText`` gives the advance of every
printable ASCII character and of a sample of CJK characters. Twenty titles and twenty widget
labels are measured whole as well, and so is every core display name and input name (about
1500 strings), to size the safety margin of a per-character sum (kerning and contextual
alternates make the whole string differ from the sum). Five collapsed nodes with titles of
different lengths are loaded in both renderers to fit the collapsed-width constants.

``record`` writes ``src/cwc/data/char_widths.json`` and ``tests/fixtures/layout/char_widths.measured.json``.
The measurement is deterministic for one frontend build and one Inter file; the numbers carry
no machine data. ``--url`` defaults to ``$CWC_ORACLE_URL``.

Characters outside printable ASCII are measured too (``MEASURED_RANGES``, one by one): a character
wider than the bound it would otherwise get (the CJK default, or the emoji advance inside the
emoji blocks) is listed with its own advance. The emoji advance is the widest of ``EMOJI_SAMPLE``.
Emoji and fallback glyphs come from the operating system's fonts, so the numbers hold for the machine
that recorded them. Sixty-odd whole strings in ``UNICODE_STRINGS`` (emoji, sequences, wide glyphs,
other scripts) are measured at both fonts for the fixture, and ``record`` refuses to write a table
that does not bound every one of them.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))

from geometry import _canvas, _node  # noqa: E402
from harness import open_session  # noqa: E402

from cwc.canvas.textwidth import FontTable  # noqa: E402

TABLE = ROOT / "src" / "cwc" / "data" / "char_widths.json"
FIXTURE = ROOT / "tests" / "fixtures" / "layout" / "char_widths.measured.json"
NODEDEFS = ROOT / "src" / "cwc" / "data" / "core_nodedefs.json"

PRINTABLE = [chr(code) for code in range(32, 127)]
# Forty CJK characters: 28 common ideographs, 4 hiragana, 3 katakana, 3 hangul syllables, 2 fullwidth forms.
CJK = list("的一是不了人我在有他这中大来上国个到说们为子和你地出道也" "あいうえ" "カタナ" "한글는" "，！")

# Real titles are taken from the core display names at evenly spaced length ranks; these four
# synthetic ones add the shapes the core names lack (one is the 46-character title of the layout fixtures).
SYNTHETIC_TITLES = [
    "A synthetic long title for the collapsed width",
    "Sampler: step 20 / cfg 8.0 (seed 123456789) - final pass v2.1",
    "load_image_and_apply_control_after_generate_before_save",
    "An exceptionally long node title that a user typed to describe in one line what this whole group of nodes does",
]
# Code points measured one by one (inclusive): the whole Basic Multilingual Plane but the surrogates and the private
# use area (Latin, Greek, Cyrillic, Hebrew, Arabic, Indic, Thai, punctuation, arrows, math, box drawing, dingbats,
# symbols, CJK, Hangul, Arabic presentation forms), the emoji blocks, and the tag characters of tag flags.
# Not measured: the other planes (hieroglyphs, cuneiform, rare CJK extensions).
MEASURED_RANGES = [(0x80, 0xD7FF), (0xF900, 0xFFFF), (0x1F000, 0x1FAFF), (0xE0000, 0xE007F)]
# The pictographic blocks one advance covers: Mahjong, cards, enclosed alphanumerics and ideographs,
# pictographs, emoticons, transport, alchemical, geometric shapes extended, arrows C, supplemental
# pictographs, chess, pictographs extended-A. Regional indicators and skin-tone modifiers are inside.
EMOJI_RANGES = [(0x1F000, 0x1FAFF)]
EMOJI_SAMPLE = list("😀🎨🖼📷🔥✨⭐🚀🧪💾🎲🔧⚙🌙☀❤✅❌⚠📝🔍🎬🧠🪄🫠🇯🏳🏴👨👩👧🏽🏿🀄🆎🈁🐀🍎🚗")
# Characters a sequence is built from, listed with their own measured advance whatever it is (zero-width joiner,
# the tag characters of tag flags). U+FE0F is listed with the emoji advance: under it a text-default base is
# drawn as an emoji (measured: base + U+FE0F is never wider than the base plus one emoji advance).
JOINER = 0x200D
VARIATION_EMOJI = 0xFE0F
ALWAYS_LISTED = [JOINER, *range(0xE0000, 0xE0080)]

# Whole strings measured at both fonts for the fixture and checked against the table before it is written.
ZWJ, VS16, KEYCAP = "\u200d", "\ufe0f", "\u20e3"
TAGS = "".join(chr(0xE0000 + ord(c)) for c in "gbeng") + "\U000e007f"
UNICODE_STRINGS = [
    "🚀", "🧠", "🔥", "🎨", "🚀🚀🚀🚀🚀🚀🚀🚀",
    "🎨 Style LoRA", "🔥 Upscale ×2", "✨ Final output ✨", "📷 Load reference image", "🧠 Model + CLIP 🧪",
    "⚙" + VS16 + " Settings", "❤" + VS16, "☀" + VS16 + "☁" + VS16 + "☂" + VS16, "1" + VS16 + KEYCAP + "2" + VS16 + KEYCAP,
    "👍🏽", "👍🏿 ok", "👨" + ZWJ + "👩" + ZWJ + "👧" + ZWJ + "👦", "👨" + ZWJ + "👩" + ZWJ + "👧 Family portrait prompt",
    "🏳" + VS16 + ZWJ + "🌈", "👩🏽" + ZWJ + "❤" + VS16 + ZWJ + "💋" + ZWJ + "👨🏿",
    "🇯🇵", "🇺🇸🇬🇧🇩🇪🇫🇷", "🏴" + TAGS,
    "﷽", "⸻", "⸺", "﷽ ⸻ ⸺", "Ǆ Ǉ Ǌ", "Ѹ", "ℵ Ⅷ", "⏏ ⌚ ⏰", "✅ ❌ ✔ ✘ ✦ ★", "🀄 🂡 🃏",
    "Привет, мир", "Заголовок узла на русском", "Ελληνικά κείμενο", "Ŝtřǐñg Ÿ Ǆ ŉ", "עברית כותרת",
    "مرحبا بالعالم", "عنوان العقدة", "नमस्ते दुनिया", "क" + "\u094d" + ZWJ + "ष", "สวัสดีชาวโลก", "ကျွန်ုပ်", "ಕನ್ನಡ ಶೀರ್ಷಿಕೆ",
    "→ ← ↔ ⇒ ⇄", "┌─┐│└┘ ╔═╗", "∑ ∫ √ ∞ ≠ ≤", "© ® ™ № ¿ §",
    "日本語のタイトル", "한국어 제목", "中文标题",
]
# Collapsed titles for the Unicode fit: each long enough that the collapsed Vue box is wider than its 225 px floor.
UNICODE_COLLAPSED_TITLES = [
    "🚀 Upscale the finished image with the tiled model and sharpen the result 🎨",
    "🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠🧠 a long run of emoji before the text",
    "👨" + ZWJ + "👩" + ZWJ + "👧" + ZWJ + "👦 Family portrait prompt for the second conditioning pass",
    "🇯🇵🇺🇸🇬🇧 flags ❤" + VS16 + " and ⚙" + VS16 + " variation selectors in a long collapsed title",
    "Заголовок узла на русском языке для проверки ширины свёрнутого узла в двух рендерерах",
    "﷽ ⸻ wide glyphs inside a long collapsed title to check the listed advances",
]

REAL_TITLES = 16
WIDGET_LABELS = 20

# Titles for the collapsed-width fit. Each is long enough that the collapsed Vue box is wider
# than its 225 px floor, so the title decides the width.
COLLAPSED_TITLES = [
    "Positive prompt for the main subject",
    "Upscale the finished image with the tiled model and sharpen",
    "Encode the second conditioning pass for the detail refinement stage",
    "Combine every regional prompt into a single conditioning and apply the area weights",
    "Final decode of the refined latent back to pixels with the tiled VAE before saving the preview",
]

MEASURE_JS = """
([fonts, chars, cjk, strings]) => {
  const ctx = document.createElement('canvas').getContext('2d');
  const width = (font, text) => { ctx.font = font; return ctx.measureText(text).width; };
  const out = {};
  for (const [role, font] of Object.entries(fonts)) {
    out[role] = {
      advances: Object.fromEntries(chars.map((c) => [c, width(font, c)])),
      cjk: Object.fromEntries(cjk.map((c) => [c, width(font, c)])),
      strings: Object.fromEntries(strings.map((s) => [s, width(font, s)])),
    };
  }
  return out;
}
"""

SCAN_JS = """
([fonts, ranges, sample]) => {
  const ctx = document.createElement('canvas').getContext('2d');
  const out = {};
  for (const [role, font] of Object.entries(fonts)) {
    ctx.font = font;
    const scan = ranges.map(([low, high]) => {
      const widths = [];
      for (let code = low; code <= high; code++) widths.push(ctx.measureText(String.fromCodePoint(code)).width);
      return widths;
    });
    out[role] = { scan, sample: Object.fromEntries(sample.map((c) => [c, ctx.measureText(c).width])) };
  }
  return out;
}
"""

FONTS_JS = """
() => {
  const node = window.LiteGraph.createNode('CLIPTextEncode');
  return {
    title: node.titleFontStyle,
    widget: node.innerFontStyle,
    inter_loaded: document.fonts.check('14px Inter') && document.fonts.check('normal 12px Inter'),
    node_font: window.LiteGraph.NODE_FONT,
    text_size: window.LiteGraph.NODE_TEXT_SIZE,
    subtext_size: window.LiteGraph.NODE_SUBTEXT_SIZE,
  };
}
"""

VUE_HEADER_JS = """
() => {
  const title = document.querySelector('[data-testid=node-title]');
  const header = document.querySelector('.lg-node-header');
  const style = (el) => { const s = getComputedStyle(el); return { font: s.font, family: s.fontFamily, size: s.fontSize, weight: s.fontWeight }; };
  return { title: title ? style(title) : null, header: header ? style(header) : null };
}
"""


def all_strings(nodedefs: dict[str, Any]) -> list[str]:
    """Every core display name and input name: the strings the safety margin is checked against."""
    out = {*SYNTHETIC_TITLES, *COLLAPSED_TITLES}
    for entry in nodedefs["nodes"].values():
        if entry.get("display_name"):
            out.add(entry["display_name"])
        for group in entry.get("input", {}).values():
            out.update(group)
    return sorted(out)


def pick_titles(nodedefs: dict[str, Any]) -> list[str]:
    """Sixteen core display names at evenly spaced length ranks, then the synthetic titles."""
    names = sorted({v["display_name"] for v in nodedefs["nodes"].values() if v.get("display_name")}, key=lambda s: (len(s), s))
    picked = [names[round(i * (len(names) - 1) / (REAL_TITLES - 1))] for i in range(REAL_TITLES)]
    return picked + SYNTHETIC_TITLES


def pick_labels(nodedefs: dict[str, Any]) -> list[str]:
    """Twenty input names (the widget labels) at evenly spaced length ranks."""
    names: set[str] = set()
    for entry in nodedefs["nodes"].values():
        for group in entry.get("input", {}).values():
            names.update(group)
    ranked = sorted(names, key=lambda s: (len(s), s))
    return [ranked[round(i * (len(ranked) - 1) / (WIDGET_LABELS - 1))] for i in range(WIDGET_LABELS)]


def collapsed_canvas() -> dict[str, Any]:
    nodes = [
        _node(index + 1, "CLIPTextEncode", [100, 100 + 120 * index], [1200, 200], ["x"], title=title, collapsed=True)
        for index, title in enumerate([*COLLAPSED_TITLES, *UNICODE_COLLAPSED_TITLES])
    ]
    return _canvas(nodes, [], [])


def measure(url: str) -> dict[str, Any]:
    defs = json.loads(NODEDEFS.read_text(encoding="utf-8"))
    titles, labels = pick_titles(defs), pick_labels(defs)
    strings = sorted({*all_strings(defs), *titles, *labels, *UNICODE_STRINGS, *UNICODE_COLLAPSED_TITLES})
    canvas = collapsed_canvas()
    with open_session(url, vue=False) as s:
        versions = s.versions()
        fonts = s.page.evaluate(FONTS_JS)
        if not fonts.get("inter_loaded"):
            raise SystemExit("char_widths: Inter is not loaded in the page; a table measured in a fallback font is refused")
        role_fonts = {"title": fonts["title"], "widget": fonts["widget"]}
        raw = s.page.evaluate(MEASURE_JS, [role_fonts, PRINTABLE, CJK, strings])
        scanned = s.page.evaluate(SCAN_JS, [role_fonts, MEASURED_RANGES, EMOJI_SAMPLE])
        s.log.phase = "classic"
        classic = s.call("geometry", copy.deepcopy(canvas), {})
    with open_session(url, vue=True) as s:
        s.log.phase = "vue"
        vue = s.call("geometry", copy.deepcopy(canvas), {})
        header = s.page.evaluate(VUE_HEADER_JS)
        vue_versions = s.versions()
    collapsed = []
    for title, c_node, v_node in zip([*COLLAPSED_TITLES, *UNICODE_COLLAPSED_TITLES], classic["nodes"], vue["nodes"]):
        collapsed.append({
            "title": title,
            "classic_collapsed_width": c_node["collapsed_width"],
            "classic_bounding_width": c_node["bounding"][2],
            "vue_box_width": v_node["vue_box"][2],
            "vue_bounding_width": v_node["bounding"][2],
        })
    return {
        "frontend": versions["frontend"],
        "comfyui": versions["comfyui"],
        "vue_nodes_on_vue_run": vue_versions["vue_nodes"],
        "fonts": fonts,
        "vue_header": header,
        "titles": titles,
        "labels": labels,
        "measured": raw,
        "scanned": scanned,
        "collapsed": collapsed,
    }


def _up(value: float) -> float:
    """Round up to a thousandth, so a rounded advance is never below the measured one."""
    return math.ceil(value * 1000 - 1e-9) / 1000


def table_sum(role: dict[str, Any], text: str) -> float:
    """The sum of advances of ``text`` by the lookup the library uses (``FontTable``), before the factor."""
    return FontTable.from_document(role).total(text)


def _in_ranges(code: int, ranges: list[tuple[int, int]]) -> bool:
    return any(low <= code <= high for low, high in ranges)


def listed_advances(measured_scan: dict[str, Any], default: float, emoji: float) -> dict[int, float]:
    """Code point to advance for every measured one wider than the bound it would otherwise get (the
    emoji advance inside ``EMOJI_RANGES``, else the default), and the sequence characters."""
    listed: dict[int, float] = {}
    for (low, high), widths in zip(MEASURED_RANGES, measured_scan["scan"]):
        for code, width in zip(range(low, high + 1), widths):
            bound = emoji if _in_ranges(code, EMOJI_RANGES) else default
            if code in ALWAYS_LISTED or _up(width) > bound:
                listed[code] = _up(width)
    listed[VARIATION_EMOJI] = emoji
    return listed


def build_table(data: dict[str, Any]) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    for name in ("title", "widget"):
        measured = data["measured"][name]
        font = data["fonts"][name]
        size = float(font.split("px")[0].split()[-1])
        default = _up(max(measured["cjk"].values()))
        emoji = _up(max(data["scanned"][name]["sample"].values()))
        listed = listed_advances(data["scanned"][name], default, emoji)
        by_advance: dict[str, str] = {}
        for code in sorted(listed):
            key = repr(listed[code])
            by_advance[key] = by_advance.get(key, "") + chr(code)
        role: dict[str, Any] = {
            "font": font,
            "size": size,
            "advances": {ch: _up(w) for ch, w in measured["advances"].items()},
            "default": default,
            "emoji": {"advance": emoji, "ranges": [list(r) for r in EMOJI_RANGES]},
            "listed": by_advance,
        }
        # Safety margin: a factor on the sum, the largest ratio of a whole string's measured width
        # to the sum of its characters' advances over every measured string (kerning is almost
        # always negative, so the ratio is usually below 1; contextual alternates make a few
        # strings wider than their sum, by about one pixel whatever their length), rounded up.
        ordinary = {t: w for t, w in measured["strings"].items() if t not in UNICODE_STRINGS and t not in UNICODE_COLLAPSED_TITLES}
        ratio = max(w / table_sum(role, text) for text, w in ordinary.items() if table_sum(role, text) > 0)
        role["scale"] = math.ceil(max(1.0, ratio) * 10000 - 1e-9) / 10000
        role["scale_basis"] = len(ordinary)
        # The factor is not stretched for the Unicode strings: the table has to bound them as it is.
        unbounded = [t for t in UNICODE_STRINGS if measured["strings"][t] > FontTable.from_document(role).width(t)]
        if unbounded:
            raise SystemExit(f"char_widths: the {name} table does not bound {unbounded!r}")
        roles[name] = role
    return {
        "header": {
            "what": "Per-character text advances in px for the node title and widget label fonts.",
            "how": "CanvasRenderingContext2D.measureText of each character in the real frontend page, "
            "rounded up to 0.001; 'default' is the largest advance of a sample of 40 CJK characters "
            "and stands for every character not listed; 'listed' maps an advance to the characters "
            "outside ASCII measured at it (wider than the default, or than 'emoji' inside its ranges), "
            "'emoji' is the advance of every code point in its ranges, the widest of a sample of emoji "
            "(the operating system's emoji font, not Inter); 'scale' multiplies a string's sum of advances.",
            "frontend": data["frontend"],
            "comfyui": data["comfyui"],
            "source": {
                "title": "LGraphNode.titleFontStyle, src/lib/litegraph/src/LGraphNode.ts:464-466; the title is drawn "
                "with it at :4157 and computeSize measures it at :2190-2191",
                "widget": "LGraphNode.innerFontStyle, src/lib/litegraph/src/LGraphNode.ts:468-470; a collapsed node's "
                "title is measured with it at :2532-2539, slot and widget text is drawn with it "
                "(src/lib/litegraph/src/LGraphCanvas.ts:5926, inner_text_font at :747-749)",
            },
            "regenerate": "python tools/oracle/char_widths.py record",
        },
        "roles": roles,
    }


def collapsed_fit(data: dict[str, Any]) -> dict[str, float]:
    """The collapsed-width constants: box width minus the whole-string width at the widget font."""
    whole = data["measured"]["widget"]["strings"]
    five = data["collapsed"][: len(COLLAPSED_TITLES)]
    classic = [c["classic_collapsed_width"] - whole[c["title"]] for c in five]
    vue = [c["vue_box_width"] - whole[c["title"]] for c in five]
    return {
        "classic_pad_min": round(min(classic), 3), "classic_pad_max": round(max(classic), 3),
        "vue_pad_min": round(min(vue), 3), "vue_pad_max": round(max(vue), 3),
    }


def record(url: str, measure_only: Path | None) -> int:
    data = measure(url)
    if measure_only is not None:
        measure_only.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {measure_only}")
        return 0
    table = build_table(data)
    TABLE.write_text(json.dumps(table, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    fixture = {
        "frontend": data["frontend"],
        "fonts": data["fonts"],
        "vue_header": data["vue_header"],
        "titles": {s: data["measured"]["title"]["strings"][s] for s in data["titles"]},
        "labels": {s: data["measured"]["widget"]["strings"][s] for s in data["labels"]},
        "collapsed_title_widths": {s: data["measured"]["widget"]["strings"][s] for s in COLLAPSED_TITLES},
        "collapsed": data["collapsed"][: len(COLLAPSED_TITLES)],
        "collapsed_fit": collapsed_fit(data),
        "unicode": {
            role: {s: data["measured"][role]["strings"][s] for s in UNICODE_STRINGS} for role in ("title", "widget")
        },
        "collapsed_unicode": data["collapsed"][len(COLLAPSED_TITLES):],
    }
    FIXTURE.write_text(json.dumps(fixture, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {TABLE.relative_to(ROOT)} and {FIXTURE.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="char_widths", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="measure and write src/cwc/data/char_widths.json and the fixture")
    r.add_argument("--url", default=os.environ.get("CWC_ORACLE_URL"))
    r.add_argument("--measure-only", type=Path, help="write the raw measurements here and stop")
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("--url is required (or set CWC_ORACLE_URL)")
    return record(args.url, args.measure_only)


if __name__ == "__main__":
    raise SystemExit(main())
