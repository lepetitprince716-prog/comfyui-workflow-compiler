"""The section a wrap gives a lower-band group: the section of its latest producer.

A lower-band group with producers (main-row groups or other lower-band groups) goes to the
section of the latest of them; one with no producer goes to the first section it links to. A
section is laid out before the next one, so a lower group in an earlier section than a group
feeding it would dock before that producer has an x (the control group left of its prompts,
C3). The wrap's trials lay out only the two sections they split, which holds while a lower
item's section never crosses the cut between closed sections and the open rest.
"""

from __future__ import annotations

import sys
import unittest
from typing import Any
from unittest import mock

from tests import support  # noqa: F401
from tests.graphs import DEFS
from tests.layout_generator import random_graph
from tests.layout_generator_wide import build_wide, wide_graph

import cwc.layout.place  # noqa: F401  (the module; ``cwc.layout.place`` the attribute is the function)
from cwc.layout.items import Item, Seq
from cwc.layout.types import Axis
from cwc.pipeline import compile_document

PLACE = sys.modules["cwc.layout.place"]
PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")
CONTROL = "group:control"
# The wide generator's graphs where the control group, fed by the cockpit and by a prompt group
# the wrap cut into a later section, used to stay in the cockpit's section.
WITNESSES = {
    "build_wide(327)": lambda: build_wide(327, ["model_alternating", "two_positive", "control", "multistage"]),
    "wide_graph(179)": lambda: wide_graph(179),
}


class _Run:
    """The ``_assign`` calls of each ``place`` run in one compile, with that run's links and
    exempt keys."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def compile(self, doc: dict[str, Any], preset: str):
        real_assign, real_place = PLACE._assign, PLACE.place

        def assign(sections, lower, links):
            out = real_assign(sections, lower, links)
            self.runs[-1]["calls"].append(([list(row) for row in sections], out))
            self.runs[-1]["links"] = links
            return out

        def place(*args, **kwargs):
            self.runs.append({"calls": [], "exempt": kwargs.get("exempt"), "links": {}})
            return real_place(*args, **kwargs)

        with mock.patch.object(PLACE, "_assign", assign), mock.patch("cwc.layout.place", place):
            return compile_document(doc, DEFS, preset=preset)


def _final(run: dict[str, Any]) -> tuple[dict[str, int], dict[Item, int], dict[str, Item]]:
    """Group key -> section, lower item -> section, lower group key -> its item, as placed."""
    sections, out = run["calls"][-1]
    where = {key: s for s, row in enumerate(sections) for item in row for key in item.groups}
    owner = {key: item for item in out for key in item.groups}
    for key, item in owner.items():
        where[key] = out[item]
    return where, out, owner


def _common(a: list[list[Item]], b: list[list[Item]]) -> int:
    """How many leading sections ``a`` and ``b`` hold the same items in."""
    n = 0
    for ra, rb in zip(a, b):
        if len(ra) != len(rb) or any(x is not y for x, y in zip(ra, rb)):
            break
        n += 1
    return n


def _errors(compiled) -> list[str]:
    return [f"{f.code.value}: {f.message}" for f in compiled.report.findings if f.severity.value == "error"]


class WitnessGraphs(unittest.TestCase):
    def test_the_control_group_follows_its_prompt_group(self) -> None:
        for name, make in WITNESSES.items():
            for preset in PRESETS:
                with self.subTest(graph=name, preset=preset):
                    probe = _Run()
                    compiled = probe.compile(make(), preset)
                    self.assertEqual(_errors(compiled), [])
                    (run,) = [r for r in probe.runs if r["calls"]]
                    where, out, owner = _final(run)
                    if CONTROL not in owner:
                        continue  # this preset keeps the control group in the main row
                    producers = [a for (a, b) in run["links"] if b == CONTROL and a in where]
                    self.assertTrue(producers)
                    section = where[CONTROL]
                    self.assertEqual(section, max(where[a] for a in producers))
                    groups = compiled.canvas.raw["groups"]
                    index = compiled.layout.group_index
                    x = groups[index[CONTROL]]["bounding"][0]
                    prompts = [a for a in producers if a.startswith("prompt:")]
                    self.assertTrue(prompts)
                    for key in prompts:
                        self.assertEqual(where[key], section, key)
                        left, _, width, _ = groups[index[key]]["bounding"]
                        self.assertGreaterEqual(x, left + width, key)

    def test_the_witnesses_wrap_the_prompt_group_past_the_cockpit(self) -> None:
        # Guards the witnesses: without a wrap between the cockpit and the prompt group they test nothing.
        for name, make in WITNESSES.items():
            with self.subTest(graph=name):
                probe = _Run()
                probe.compile(make(), "bento")
                where, _, owner = _final(next(r for r in probe.runs if r["calls"]))
                self.assertIn(CONTROL, owner)
                self.assertLess(where["cockpit"], where[CONTROL])


def _item(key: str, band: int) -> Item:
    return Item(band, 0, 0, None, [key], Seq(Axis.X, []))


class AssignRule(unittest.TestCase):
    def setUp(self) -> None:
        self.a, self.b, self.c = _item("a", 0), _item("b", 0), _item("c", 0)
        self.sections = [[self.a], [self.b], [self.c]]

    def assign(self, lower: list[Item], links: dict[tuple[str, str], int]) -> dict[str, int]:
        return {item.groups[0]: s for item, s in PLACE._assign(self.sections, lower, links).items()}

    def test_a_lower_group_fed_by_a_lower_group_follows_it(self) -> None:
        # ``late`` is fed from section 2; ``fed`` is fed from section 0 and by ``late``. Listed
        # first, ``fed`` waits for ``late``.
        fed, late = _item("fed", 1), _item("late", 1)
        links = {("a", "fed"): 1, ("late", "fed"): 1, ("c", "late"): 1, ("fed", "c"): 1}
        self.assertEqual(self.assign([fed, late], links), {"fed": 2, "late": 2})

    def test_a_lower_group_goes_to_its_latest_main_row_producer(self) -> None:
        mixed = _item("mixed", 1)
        links = {("a", "mixed"): 1, ("b", "mixed"): 1, ("mixed", "c"): 1}
        self.assertEqual(self.assign([mixed], links), {"mixed": 1})

    def test_a_lower_group_without_producers_keeps_the_first_section_it_links_to(self) -> None:
        source, chained = _item("source", 1), _item("chained", 1)
        links = {("source", "b"): 1, ("source", "c"): 1, ("chained", "source"): 1}
        # ``chained`` feeds ``source`` and has no section of its own to give: ``source`` takes the
        # first section it links to, and ``chained``, with no producer, takes ``source``'s.
        self.assertEqual(self.assign([source, chained], links), {"source": 1, "chained": 1})
        alone = _item("alone", 1)
        self.assertEqual(self.assign([alone], {("alone", "c"): 1, ("alone", "b"): 1}), {"alone": 1})
        self.assertEqual(self.assign([alone], {}), {"alone": 0})

    def test_lower_groups_feeding_each_other_in_a_cycle_settle(self) -> None:
        one, two = _item("one", 1), _item("two", 1)
        links = {("b", "one"): 1, ("one", "two"): 1, ("two", "one"): 1}
        self.assertEqual(self.assign([one, two], links), {"one": 1, "two": 1})


class RandomGraphs(unittest.TestCase):
    """On random graphs: every trial's sections agree with the placed ones before the cut, a
    lower group sits in its latest producer's section, and a consumer of a lower group sits in an
    earlier section only when the main row already runs backward there: a main-row group feeding
    the lower group, directly or through other lower groups, sits after that consumer.
    ``wide_graph(232)`` holds one: a lower group fed from section 1 feeds a group nested in the
    cockpit's item in section 0."""

    GRAPHS = [(f"random_graph({s})", random_graph, s) for s in range(8)] + [
        (f"wide_graph({s})", wide_graph, s) for s in (179, 181, 232, 254, 281, 327, 377, 12, 40)
    ]

    def test_properties(self) -> None:
        wrapped = 0
        for name, make, seed in self.GRAPHS:
            for preset in PRESETS:
                probe = _Run()
                probe.compile(make(seed), preset)
                for run in probe.runs:
                    calls = run["calls"]
                    if not calls:
                        continue
                    wrapped += len(calls[-1][0]) > 1
                    with self.subTest(graph=name, preset=preset):
                        self._trials_agree(calls)
                        self._order(run)
        self.assertGreater(wrapped, 10)  # the seeds must exercise the wrap

    def _trials_agree(self, calls) -> None:
        # Two calls that share their first ``n`` sections place every lower item the same way
        # relative to ``n``: in the same section before it, or both at or after it.
        for i, (sections_i, out_i) in enumerate(calls):
            for sections_j, out_j in calls[i + 1 :]:
                n = _common(sections_i, sections_j)
                for item, s in out_i.items():
                    self.assertEqual(min(s, n), min(out_j[item], n), item.groups)

    def _order(self, run) -> None:
        where, out, owner = _final(run)
        for item, s in out.items():
            producers = [where[a] for (a, b) in run["links"] if b in item.groups and a in where and owner.get(a) is not item]
            if producers:
                self.assertEqual(s, max(producers), item.groups)
            consumers = [where[b] for (a, b) in run["links"] if a in item.groups and b in where and b not in run["exempt"] and owner.get(b) is not item]
            ancestors = self._main_ancestors(item, run["links"], where, owner)
            for c in consumers:
                self.assertTrue(c >= s or any(p > c for p in ancestors), item.groups)

    @staticmethod
    def _main_ancestors(item: Item, links, where: dict[str, int], owner: dict[str, Item]) -> list[int]:
        """The sections of the main-row groups feeding ``item``, directly or through lower items."""
        seen, todo, out = {item}, [item], []
        while todo:
            current = todo.pop()
            for a, b in links:
                if b not in current.groups or a not in where:
                    continue
                if a not in owner:
                    out.append(where[a])
                elif owner[a] not in seen:
                    seen.add(owner[a])
                    todo.append(owner[a])
        return out


if __name__ == "__main__":
    unittest.main()
