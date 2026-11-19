"""The measured per-character width table against ``tests/fixtures/layout/char_widths.measured.json``.

The fixture holds what a real frontend (1.59.1) measured with ``measureText``: 20 node titles at the title
font, 20 widget labels at the widget font, and five collapsed nodes (classic and Vue). The table must give
at least the measured width and stay within 5 % of it. The fixture also holds 52 Unicode strings measured at
both fonts (emoji, a ZWJ family, flags, keycaps, tag flag, wide glyphs, other scripts) and six collapsed titles
with emoji, ZWJ, flags, Cyrillic and wide glyphs, measured in both renderers; the table must give at least the
measured width of each. Those numbers come from the fonts of the machine that recorded the fixture.
"""

from __future__ import annotations

import json
import unittest

from tests.support import FIXTURES

from cwc.canvas.envelope import (
    COLLAPSED_FONT,
    COLLAPSED_PAD,
    TITLE_FONT,
    VUE_COLLAPSED_PAD,
    VUE_MIN_WIDTH,
    WIDGET_FONT,
    title_width,
)
from cwc.canvas.textwidth import roles, text_width

MEASURED = json.loads((FIXTURES / "layout" / "char_widths.measured.json").read_text(encoding="utf-8"))
SLACK = 0.05


class Table(unittest.TestCase):
    def test_every_printable_ascii_character_has_an_advance_and_others_one_em(self) -> None:
        for role in roles().values():
            self.assertEqual(sorted(role.advances), [chr(code) for code in range(32, 127)])
            self.assertEqual(role.default, role.size)

    def test_fonts_are_the_ones_the_frontend_reports(self) -> None:
        self.assertTrue(MEASURED["fonts"]["inter_loaded"])
        self.assertEqual(MEASURED["fonts"]["title"], f"{TITLE_FONT}px Inter")
        self.assertEqual(MEASURED["fonts"]["widget"], f"normal {WIDGET_FONT}px Inter")
        self.assertEqual({role.size for role in roles().values()}, {TITLE_FONT, WIDGET_FONT})
        self.assertEqual(COLLAPSED_FONT, WIDGET_FONT)

    def test_empty_text_is_zero_and_other_sizes_scale_the_title_table(self) -> None:
        self.assertEqual(title_width(""), 0)
        self.assertAlmostEqual(text_width("Load Checkpoint", 28), 2 * text_width("Load Checkpoint", TITLE_FONT))

    def test_a_cjk_character_keeps_the_one_em_default_not_the_emoji_advance(self) -> None:
        for role in roles().values():
            self.assertEqual(role.advance("是"), role.size)
            self.assertGreater(role.emoji, role.size)

    def test_characters_outside_ascii_take_the_default(self) -> None:
        title = roles()["title"]
        self.assertAlmostEqual(title.width("是"), title.default * title.scale)
        self.assertAlmostEqual(title.width("a是"), (title.advances["a"] + title.default) * title.scale)


class Measured(unittest.TestCase):
    def check(self, widths: dict[str, float], size: float) -> None:
        self.assertEqual(len(widths), 20)
        for text, measured in widths.items():
            got = text_width(text, size)
            self.assertGreaterEqual(got, measured, text)
            self.assertLessEqual(got, measured * (1 + SLACK), text)

    def test_twenty_titles_at_the_title_font(self) -> None:
        self.check(MEASURED["titles"], TITLE_FONT)

    def test_twenty_widget_labels_at_the_widget_font(self) -> None:
        self.check(MEASURED["labels"], WIDGET_FONT)


class Unicode(unittest.TestCase):
    """Emoji, sequences and wide glyphs against what the real frontend measured."""

    def test_every_unicode_string_is_bounded_at_both_fonts(self) -> None:
        self.assertEqual(len(MEASURED["unicode"]["title"]), 52)
        for role, size in (("title", TITLE_FONT), ("widget", WIDGET_FONT)):
            for text, measured in MEASURED["unicode"][role].items():
                self.assertGreaterEqual(text_width(text, size), measured, (role, text))

    def test_one_emoji_is_the_measured_advance_within_five_percent(self) -> None:
        for text in ("🚀", "🧠", "🔥", "🎨"):
            for role, size in (("title", TITLE_FONT), ("widget", WIDGET_FONT)):
                measured = MEASURED["unicode"][role][text]
                self.assertGreater(measured, size)  # wider than one em: the old default was not a bound
                self.assertGreaterEqual(text_width(text, size), measured)
                self.assertLessEqual(text_width(text, size), measured * (1 + SLACK))

    def test_a_zwj_family_a_flag_and_a_keycap_are_bounded_by_their_code_points(self) -> None:
        zwj, vs16, keycap = "\u200d", "\ufe0f", "\u20e3"
        family = "👨" + zwj + "👩" + zwj + "👧" + zwj + "👦"
        keycaps = "1" + vs16 + keycap + "2" + vs16 + keycap
        for text in (family, "🇯🇵", "🇺🇸🇬🇧🇩🇪🇫🇷", keycaps):
            self.assertGreaterEqual(text_width(text, TITLE_FONT), MEASURED["unicode"]["title"][text], text)
        title = roles()["title"]
        self.assertEqual(title.advance(zwj), 0.0)  # a joiner draws nothing: a family is its four emoji
        self.assertAlmostEqual(title.total(family), 4 * title.emoji)
        self.assertEqual(title.advance(vs16), title.emoji)  # a text base drawn as emoji under it

    def test_the_wide_glyphs_have_their_own_measured_advances(self) -> None:
        title, widget = roles()["title"], roles()["widget"]
        for char in ("\ufdfd", "\u2e3b", "\u2e3a"):
            at_title, at_widget = MEASURED["unicode"]["title"][char], MEASURED["unicode"]["widget"][char]
            self.assertGreaterEqual(title.advance(char), at_title, hex(ord(char)))
            self.assertGreaterEqual(widget.advance(char), at_widget, hex(ord(char)))
            self.assertLess(title.advance(char), at_title * 1.01, hex(ord(char)))
        self.assertGreater(title.advance("\ufdfd"), 6 * title.default)

    def test_an_emoji_code_point_nobody_measured_still_gets_the_emoji_advance(self) -> None:
        title = roles()["title"]
        self.assertEqual(title.advance("\U0001faff"), title.emoji)
        self.assertEqual(title.advance("\U0001f1ef"), title.emoji)  # a regional indicator

    def test_the_table_stays_ascii_only_where_it_was_before(self) -> None:
        for text in ("Load Checkpoint", "KSampler (Advanced)"):
            self.assertEqual(roles()["title"].width(text), roles()["title"].total(text) * roles()["title"].scale)


class CollapsedUnicode(unittest.TestCase):
    def test_six_collapsed_titles_in_both_renderers(self) -> None:
        self.assertEqual(len(MEASURED["collapsed_unicode"]), 6)
        for entry in MEASURED["collapsed_unicode"]:
            text = title_width(entry["title"], COLLAPSED_FONT)
            self.assertGreaterEqual(text + COLLAPSED_PAD, entry["classic_collapsed_width"], entry["title"])
            self.assertGreaterEqual(max(VUE_MIN_WIDTH, text + VUE_COLLAPSED_PAD), entry["vue_box_width"], entry["title"])


class CollapsedWidths(unittest.TestCase):
    def test_five_collapsed_titles_in_both_renderers(self) -> None:
        self.assertEqual(len(MEASURED["collapsed"]), 5)
        for entry in MEASURED["collapsed"]:
            text = title_width(entry["title"], COLLAPSED_FONT)
            self.assertGreaterEqual(text + COLLAPSED_PAD, entry["classic_collapsed_width"])
            self.assertLessEqual(text + COLLAPSED_PAD, entry["classic_collapsed_width"] * (1 + SLACK))
            vue = max(VUE_MIN_WIDTH, text + VUE_COLLAPSED_PAD)
            self.assertGreaterEqual(vue, entry["vue_box_width"])
            self.assertLessEqual(vue, entry["vue_box_width"] * (1 + SLACK))
            self.assertEqual(entry["vue_box_width"], entry["vue_bounding_width"])

    def test_the_pads_are_the_fitted_ones(self) -> None:
        fit = MEASURED["collapsed_fit"]
        self.assertEqual((fit["classic_pad_min"], fit["classic_pad_max"]), (COLLAPSED_PAD, COLLAPSED_PAD))
        self.assertGreaterEqual(VUE_COLLAPSED_PAD, fit["vue_pad_max"])
        self.assertLess(VUE_COLLAPSED_PAD, fit["vue_pad_max"] + 0.01)

    def test_the_vue_header_is_the_widget_font(self) -> None:
        header = MEASURED["vue_header"]["title"]
        self.assertEqual((header["size"], header["weight"]), (f"{WIDGET_FONT}px", "400"))


if __name__ == "__main__":
    unittest.main()
