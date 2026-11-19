"""Text width from the measured per-character table ``src/cwc/data/char_widths.json``.

The table holds, for the two fonts the frontend draws node text in, an advance for every
character as ``measureText`` reported it in a real frontend page, and a factor that makes the
sum of advances an upper bound of the width of the whole string (kerning and contextual
alternates move a string's width a little off the sum). ``tools/oracle/char_widths.py``
regenerates it. A character's advance is looked up in this order:

1. ``advances``: printable ASCII, each measured.
2. ``listed``: a character outside ASCII measured wider than the bound it would otherwise get,
   plus the joiners and tag characters that sequences are built from (zero-width ZWJ and tags,
   and U+FE0F, which is given the emoji advance because a base drawn as text becomes an emoji
   glyph under it: a gear is 8.4 px alone and 18 px with U+FE0F).
3. ``emoji``: one advance for every code point of the pictographic blocks (U+1F000 to
   U+1FAFF, flags and skin-tone modifiers included), the widest of a sample of emoji. Emoji
   come from the operating system's emoji font, not from Inter: 18 px at the 14 px title font
   and 15 px at the 12 px widget font, wider than one em.
4. ``default``: any other character, the widest of a sample of 40 CJK characters, one em.

A sequence (ZWJ family, flag, skin tone, keycap, tag flag) draws as one glyph but is several
code points; the sum of its code points' advances still bounds it.

What the table covers: every code point of the Basic Multilingual Plane from U+0080 (surrogates and the
private use area left out) and the emoji blocks were measured one by one, and each one wider than its
default or emoji bound is listed. Characters outside the measured set keep the default: the other
planes, which hold Egyptian hieroglyphs and cuneiform (wider than one em, cuneiform up to 4.7 em) and
rare CJK extensions (1.02 em). The numbers hold for the fonts of the machine that recorded the table
(the emoji and fallback fonts are the operating system's); on another system they are unverified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping

from cwc.resources import read_json

TABLE = "char_widths.json"
TITLE_ROLE = "title"


@dataclass(frozen=True)
class FontTable:
    size: float
    advances: Mapping[str, float]
    default: float
    scale: float
    listed: Mapping[str, float]
    emoji: float
    emoji_ranges: tuple[tuple[int, int], ...]
    # Every character looked up so far, with its advance: the table is read once per character of every title.
    memo: dict[str, float] = field(default_factory=dict, init=False, repr=False, compare=False)

    @classmethod
    def from_document(cls, role: Mapping[str, Any]) -> FontTable:
        """The table of one role of the document ``src/cwc/data/char_widths.json`` (``scale`` 1 when absent)."""
        listed = {ch: float(advance) for advance, chars in role["listed"].items() for ch in chars}
        emoji = role["emoji"]
        return cls(
            float(role["size"]),
            role["advances"],
            float(role["default"]),
            float(role.get("scale", 1.0)),
            listed,
            float(emoji["advance"]),
            tuple((int(low), int(high)) for low, high in emoji["ranges"]),
        )

    def advance(self, ch: str) -> float:
        found = self.memo.get(ch)
        if found is None:
            found = self.memo[ch] = self._lookup(ch)
        return found

    def _lookup(self, ch: str) -> float:
        known = self.advances.get(ch)
        if known is not None:
            return known
        known = self.listed.get(ch)
        if known is not None:
            return known
        code = ord(ch)
        if any(low <= code <= high for low, high in self.emoji_ranges):
            return self.emoji
        return self.default

    def total(self, text: str) -> float:
        """The sum of the advances of ``text``, without the safety factor."""
        memo = self.memo
        return sum([memo[ch] if ch in memo else self.advance(ch) for ch in text])

    def width(self, text: str) -> float:
        return self.total(text) * self.scale


@lru_cache(maxsize=None)
def roles() -> Mapping[str, FontTable]:
    document = read_json(TABLE)
    return {name: FontTable.from_document(role) for name, role in document["roles"].items()}


@lru_cache(maxsize=1 << 16)
def text_width(text: str, size: float) -> float:
    """Upper-bound width in px of ``text`` drawn at font ``size``: the table of the role measured at
    exactly that size, else the title table scaled linearly (Inter advances scale with the size; an
    emoji is 1.286 em at 14 px and 1.25 em at 12 px, so the scaled title table bounds it at 12 px;
    at other sizes that is unverified)."""
    table = roles()
    for role in table.values():
        if role.size == size:
            return role.width(text)
    title = table[TITLE_ROLE]
    return title.width(text) * size / title.size
