"""The wrap rule of cwc.layout.place: the quarter threshold, the preference order among cuts,
the fallback when no cut fits, and the G8 cap on the first section's start.

These drive ``place()`` with hand-sized parts: the thresholds are exact comparisons of widths,
which ``layout()`` cannot be steered onto. Every group is one part ``W`` wide and 100 high in a
one-slot main band stacked along x, so a row of n parts is ``sum(W) + 40 (n - 1)`` wide and a
section is too wide when that exceeds ``wrap_ratio * 100``.
"""

from __future__ import annotations

import threading
import unittest
from typing import Callable

from cwc.annotate.types import GroupPlan, Zone
from cwc.canvas.geometry import Rect
from cwc.layout.inner import Part
from cwc.layout.place import place
from cwc.layout.section import Placed
from cwc.layout.types import Axis, Band, Numbering, Preset, Slot

MAIN, LOWER = Zone.SAMPLING, Zone.CONTROL
HEIGHT = 100
# place() returns in milliseconds here; a wrap loop that never ends must fail, not hang the suite
DEADLINE = 20.0


def _preset(wrap_ratio: float, lower: bool = False) -> Preset:
    bands = [Band((Slot((MAIN,), stack=Axis.X),))]
    if lower:
        bands.append(Band((Slot((LOWER,), stack=Axis.X),)))
    return Preset(
        name="wrap-test",
        axis=Axis.X,
        bands=tuple(bands),
        corridors=(),
        wrap_ratio=wrap_ratio,
        guide_width=400,
        numbering=Numbering.NUMBER,
        palette="test",
    )


def _run(
    widths: dict[str, float],
    wrap_ratio: float,
    links: dict[tuple[str, str], int] | None = None,
    fits: Callable[[Placed], bool] = lambda placed: True,
    lower: dict[str, float] | None = None,
) -> Placed:
    """``widths``: main-row groups in plan order; ``lower``: groups of the lower band."""
    groups, units = [], {}
    for zone, members in ((MAIN, widths), (LOWER, lower or {})):
        for key, w in members.items():
            plan = GroupPlan(key=key, zone=zone, name=key, members=())
            groups.append(plan)
            units[key] = [Part(key=key, plan=plan, letter="", box=Rect(0, 0, w, HEIGHT), nodes={})]
    result: list[Placed] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            result.append(place(_preset(wrap_ratio, lower is not None), groups, units, links or {}, x0=0.0, exempt=frozenset(), fits=fits))
        except BaseException as exc:  # re-raised in the test's thread
            errors.append(exc)

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(DEADLINE)
    if worker.is_alive():
        raise AssertionError(f"place() did not return within {DEADLINE} s")
    if errors:
        raise errors[0]
    return result[0]


def _rows(placed: Placed, keys: set[str] | None = None) -> list[list[str]]:
    """The main-row keys of each section, top to bottom, left to right."""
    at: dict[float, list[tuple[float, str]]] = {}
    for part, x, y in placed.parts:
        if keys is None or part.key in keys:
            at.setdefault(y, []).append((x, part.key))
    return [[key for _, key in sorted(at[y])] for y in sorted(at)]


class QuarterThreshold(unittest.TestCase):
    """A cut must move at least a quarter of the section's width (``WRAP_MIN_MOVED``)."""

    def test_a_tail_of_exactly_a_quarter_is_moved(self) -> None:
        # 260 + 40 + 100 = 400 > 2 * 100; the tail is 100 = 400 / 4
        placed = _run({"a": 260, "b": 100}, wrap_ratio=2)
        self.assertEqual(_rows(placed), [["a"], ["b"]])
        self.assertEqual(len(placed.sections), 2)

    def test_a_tail_just_under_a_quarter_stays(self) -> None:
        # 280 + 40 + 100 = 420; the tail 100 < 105
        placed = _run({"a": 280, "b": 100}, wrap_ratio=2)
        self.assertEqual(_rows(placed), [["a", "b"]])
        self.assertEqual(len(placed.sections), 1)

    def test_a_section_not_wider_than_the_ratio_stays(self) -> None:
        # 400 is not wider than 4 * 100 (strict "wider than")
        self.assertEqual(_rows(_run({"a": 260, "b": 100}, wrap_ratio=4)), [["a", "b"]])


class PreferenceOrder(unittest.TestCase):
    """Among the cuts that pay: a head not too wide first, then the fewest links between the
    two sections, then the longest head. Four parts of 200: the row is 920 wide; the cut after
    the third part moves 200 < 230 and never pays."""

    WIDTHS = {"a": 200, "b": 200, "c": 200, "d": 200}

    def test_ties_take_the_longest_head(self) -> None:
        # ratio 5: heads of 200 and 440 both fit under 500, no links
        self.assertEqual(_rows(_run(self.WIDTHS, wrap_ratio=5)), [["a", "b"], ["c", "d"]])

    def test_fewer_crossing_links_beat_a_longer_head(self) -> None:
        # cutting after b crosses the b -> c link, cutting after a crosses nothing
        rows = _run(self.WIDTHS, wrap_ratio=5, links={("b", "c"): 1})
        self.assertEqual(_rows(rows)[0], ["a"])
        self.assertEqual(_rows(rows), [["a"], ["b", "c"], ["d"]])

    def test_a_head_not_too_wide_beats_fewer_crossing_links(self) -> None:
        # ratio 3: the head a+b (440) is too wide, a alone (200) is not; a -> b makes the cut
        # after a cross 5 links and the cut after b none
        rows = _run(self.WIDTHS, wrap_ratio=3, links={("a", "b"): 5})
        self.assertEqual(_rows(rows)[0], ["a"])

    def test_the_first_cut_in_order_that_fits_is_taken(self) -> None:
        # the preferred cut (after b) is refused; the next one (after a) is taken
        def fits(placed: Placed) -> bool:
            return _rows(placed)[0] != ["a", "b"]

        self.assertEqual(_rows(_run(self.WIDTHS, wrap_ratio=5, fits=fits))[0], ["a"])


class NoCutFits(unittest.TestCase):
    WIDTHS = {"a": 200, "b": 200, "c": 200, "d": 200}

    def test_the_section_stays_whole_and_is_laid_out_again(self) -> None:
        calls = []

        def fits(placed: Placed) -> bool:
            calls.append(placed)
            # without the fallback the search repeats on the same tail forever
            self.assertLess(len(calls), 20, "the wrap keeps searching after every cut was refused")
            return False

        placed = _run(self.WIDTHS, wrap_ratio=5, fits=fits)
        self.assertEqual(len(calls), 2)  # the cuts after a and after b pay; both refused
        self.assertEqual(_rows(placed), [["a", "b", "c", "d"]])
        self.assertEqual(len(placed.sections), 1)
        # the items are where their parts are, not where the last trial left them
        parts = {part.key: (x, y) for part, x, y in placed.parts}
        self.assertEqual({item.groups[0]: (item.x, item.y) for item in placed.items}, parts)
        self.assertEqual(parts, {"a": (0, 0), "b": (240, 0), "c": (480, 0), "d": (720, 0)})


class FirstScreenCap(unittest.TestCase):
    """A lower item that only feeds the first main-row group ends at that group's left edge, so
    the row moves right by the item's width; in the first section the start is capped at
    ``1600 - min(first group's width, 600)``, snapped down (G8)."""

    def _start(self, first: float, lower: float) -> float:
        placed = _run({"m": first, "n": 200}, wrap_ratio=100, links={("l", "m"): 1}, lower={"l": lower})
        return {part.key: x for part, x, _ in placed.parts}["m"]

    def test_the_row_moves_right_by_the_lower_item_below_the_cap(self) -> None:
        self.assertEqual(self._start(200, 500), 500)

    def test_a_narrow_first_group_keeps_its_whole_title_on_screen(self) -> None:
        self.assertEqual(self._start(200, 3000), 1400)

    def test_a_wide_first_group_keeps_600_of_its_title_on_screen(self) -> None:
        self.assertEqual(self._start(800, 3000), 1000)


if __name__ == "__main__":
    unittest.main()
