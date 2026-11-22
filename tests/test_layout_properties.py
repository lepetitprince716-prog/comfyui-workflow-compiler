"""Property tests for the layout over random graphs, one test class per preset.

For seeds 0 to 59, ``tests.layout_generator`` draws a graph with random LoRA loaders,
control branches, optional modules, a second sampling stage and value nodes. Each graph is
compiled, laid out with the class's preset (``bento``, ``swimlanes``, ``stepped``, ``cockpit-split``, ``tower-preview``) and routed. The properties: no compile-mode error finding
(G2, and ``COMPILE_ERRORS`` and ``SIMPLE_ERRORS`` of ``tests.layout_cases``: G1, G3 to G8,
C1, C3, C4, C6) on the full and on the reduced graph; zero-hole by column (``tests.layout_cases``): inside a section the groups projected on x
leave no gap wider than 80 px, and a main-row gap wider than 40 px has a bridge under it and
is no wider than the bridge needs, the spine (a ``spine`` corridor) aside, on the full and on
the reduced graph; the graph without its optional modules and control
branches is no wider along x; and in a repeated slot the instances follow the data flow, by
plan group and link by link (the link check also runs the ``ALTERNATING`` seeds). The section
count may differ: a shorter graph that wraps is the wrap rule working.

Each check classifies a failure into short cause codes (``CAUSES``). ``KNOWN_FAILURES``
(``KNOWN`` per preset) lists the seeds where the layout does not hold a property yet: ``(seed, check)`` to the one
cause code it fails with. A listed seed is skipped only when it fails with exactly that
cause; a listed seed that fails with another cause, or that passes, fails the test, so the
list cannot go stale.
"""

import unittest
from functools import lru_cache

from tests import support  # noqa: F401
from tests.graphs import DEFS
from tests.layout_cases import COMPILE_ERRORS, SIMPLE_ERRORS, column_holes, extent_x, gap_faults, routed, sections, top_links
from tests.layout_generator import random_graph, reduced_graph

from cwc.canvas.direction import feedback_links
from cwc.layout.presets import get_preset
from cwc.layout.types import CorridorKind
from cwc.pipeline import compile_document

SEEDS = range(60)
# Seeds past 59 whose LoRA loaders alternate optional and required (optional, required,
# optional, required): the model group is cut into segments and two optional groups share a
# stage with it. The repeat-slot link check runs them as well.
ALTERNATING = (94, 95, 155, 240, 252, 297, 310, 361)
GEOMETRY_CHECKS = ("G2",) + COMPILE_ERRORS + SIMPLE_ERRORS  # every compile-mode error
GRAPHS = {"full": random_graph, "reduced": reduced_graph}


@lru_cache(maxsize=None)
def laid(seed: int, graph: str = "full", preset: str = "bento"):
    """``routed`` of a seed's graph by ``preset`` without the route result, once per run (the
    checks only read it)."""
    return routed(GRAPHS[graph](seed), get_preset(preset))[:5]

CAUSES = {
    "<code>-inside": "a geometry finding whose nodes all lie in one top-level group",
    "<code>-across": "a geometry finding whose nodes lie in different top-level groups (or none)",
    "G5-loop": "G5: a backward link between two top-level groups that link both ways (a group cycle)",
    "G5-order": "G5: a backward link between two top-level groups linked one way only",
    "hole-main": "zero-hole: a gap over 80 px in a section's projection, inside the main row's span",
    "hole-edge": "zero-hole: a gap over 80 px beyond either end of the main row",
    "no-bridge": "bridge-gap: a main-row gap over 40 px with no bridge under it",
    "too-wide": "bridge-gap: a main-row gap with a bridge under it, wider than the bridge needs",
    "wider-wrap": "removal: the reduced graph is wider along x and has more sections",
    "wider": "removal: the reduced graph is wider along x with no more sections",
    "instance-order": "repeat order: a repeated slot's instance holds a group a later instance's group reaches one way",
    "instance-link": "repeat links: a link runs from a repeated slot's instance into an earlier instance of the slot in the same band",
}

# (seed, check) -> cause code; a geometry check is named "<code> full" or "<code> reduced".
# Empty: every property holds on every seed.
KNOWN_FAILURES: dict[tuple[int, str], str] = {}
# swimlanes and cockpit-split, removal: the full graph wraps into more sections, each narrower
# than the reduced graph's, which stays in fewer: it is not too wide for its height, or, in
# cockpit-split, the one cut it would accept is at the spine.
KNOWN: dict[str, dict[tuple[int, str], str]] = {
    "bento": KNOWN_FAILURES,
    "swimlanes": {(3, "removal"): "wider", (51, "removal"): "wider"},
    "stepped": {},
    "cockpit-split": {(3, "removal"): "wider", (14, "removal"): "wider", (54, "removal"): "wider"},
    "tower-preview": {},
    "gallery": {},
    "square": {(37, "removal"): "wider"},
}


def errors(report, codes):
    return [f for f in report.findings if f.severity.value == "error" and f.code.value in codes]


def top_of(plan) -> dict[str, str]:
    parent = {g.key: g.parent for g in plan.groups}
    out = {}
    for group in plan.groups:
        key = group.key
        while parent[key] is not None:
            key = parent[key]
        for member in group.members:
            out[member] = key
    return out


def cause(code, finding, canvas, plan) -> str:
    """The cause code of one error finding (``CAUSES``)."""
    top = top_of(plan)
    if code == "G5":
        pairs = top_links(canvas, plan)
        ends = {link.id: (top.get(str(link.origin_id)), top.get(str(link.target_id))) for link in canvas.links}
        backward = [ends[link] for link in (finding.data or {}).get("links", []) if link in ends]
        return "G5-loop" if any((b, a) in pairs for a, b in backward) else "G5-order"
    tops = {top.get(key) for key in finding.nodes}
    return f"{code}-inside" if len(tops) == 1 and None not in tops else f"{code}-across"


class GeneratorTest(unittest.TestCase):
    def test_generated_graphs_compile_clean(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                for name, build in (("full", random_graph), ("reduced", reduced_graph)):
                    report = compile_document(build(seed), DEFS).report
                    found = [f"{f.code.value}: {f.message}" for f in report.findings if f.severity.value == "error"]
                    self.assertEqual(found, [], f"seed {seed}, generator ({name}): the graph does not compile")


class LayoutPropertyTest(unittest.TestCase):
    """The properties under ``bento``; a subclass sets ``PRESET`` to run them under another."""

    PRESET = "bento"

    def laid(self, seed: int, graph: str = "full"):
        return laid(seed, graph, self.PRESET)

    def sections(self, canvas, plan, result):
        return sections(canvas, plan, result, get_preset(self.PRESET))

    def run_seeds(self, check, classify, seeds=SEEDS):
        """``classify(seed)`` returns ``(causes, detail)``: the cause codes of the failure, empty
        when the property holds. A seed listed for ``check`` is skipped only when its causes are
        exactly the listed one."""
        for seed in seeds:
            with self.subTest(seed=seed, check=check):
                causes, detail = classify(seed)
                listed = KNOWN[self.PRESET].get((seed, check))
                if listed is None:
                    self.assertEqual(causes, [], f"seed {seed}, {check}: {detail}")
                elif not causes:
                    self.fail(f"seed {seed}, {check}: listed in KNOWN_FAILURES as {listed} but it passes now")
                elif causes == [listed]:
                    self.skipTest(f"known failure: {listed}")
                else:
                    self.fail(f"seed {seed}, {check}: listed as {listed}, fails with {causes}: {detail}")

    def test_geometry_errors(self):
        for graph in GRAPHS:
            for code in GEOMETRY_CHECKS:

                def classify(seed, code=code, graph=graph):
                    canvas, plan, _, report, _ = self.laid(seed, graph)
                    found = errors(report, (code,))
                    return sorted(dict.fromkeys(cause(code, f, canvas, plan) for f in found)), [f.message for f in found]

                self.run_seeds(f"{code} {graph}", classify)

    def test_no_hole_by_column(self):
        for graph in GRAPHS:

            def classify(seed, graph=graph):
                canvas, plan, result = self.laid(seed, graph)[:3]
                causes, found = [], []
                for i, section in enumerate(self.sections(canvas, plan, result)):
                    main = section["main"]
                    span = (min(c.rect.x for c in main), max(c.rect.right for c in main))
                    for left, right in column_holes(section):
                        found.append((i, (left, right)))
                        causes.append("hole-main" if span[0] <= left and right <= span[1] else "hole-edge")
                return sorted(dict.fromkeys(causes)), f"(section, (left, right)) gaps over 80 px: {found}"

            self.run_seeds(f"zero-hole {graph}", classify)

    def test_main_row_gaps_hold_bridges(self):
        for graph in GRAPHS:

            def classify(seed, graph=graph):
                canvas, plan, result = self.laid(seed, graph)[:3]
                links = top_links(canvas, plan)
                cockpit = frozenset(g.key for g in plan.groups if g.parent is None and g.zone.value == "cockpit")
                spines = {(c.rect.x, c.rect.right) for c in result.corridors if c.kind is CorridorKind.SPINE}  # a gap by design
                found = [
                    (i, gap, cause)
                    for i, section in enumerate(self.sections(canvas, plan, result))
                    for gap, cause in gap_faults(section, links, cockpit)
                    if gap not in spines
                ]
                return sorted(dict.fromkeys(cause for *_, cause in found)), f"(section, gap, cause): {found}"

            self.run_seeds(f"bridge-gap {graph}", classify)

    def test_removing_modules_does_not_widen(self):
        def classify(seed):
            large = self.laid(seed)
            small = self.laid(seed, "reduced")
            width = [extent_x(one[0]) for one in (small, large)]
            count = [len(self.sections(*one[:3])) for one in (small, large)]
            if width[0] <= width[1]:
                return [], ""
            cause = "wider-wrap" if count[0] > count[1] else "wider"
            return [cause], f"extent along x {width[0]} > {width[1]}, sections {count[0]} vs {count[1]}"

        self.run_seeds("removal", classify)

    def test_repeat_instances_follow_the_data_flow(self):
        """In a repeated slot, an instance never holds a group reached, one way along the links
        between top-level groups, from a group of a later instance."""
        for graph in GRAPHS:

            def classify(seed, graph=graph):
                canvas, plan, result = self.laid(seed, graph)[:3]
                edges: dict[str, list[str]] = {}
                for a, b in sorted(top_links(canvas, plan)):
                    edges.setdefault(a, []).append(b)

                def down(key):
                    seen, todo = [key], [key]
                    while todo:
                        for nxt in edges.get(todo.pop(), []):
                            if nxt not in seen:
                                seen.append(nxt)
                                todo.append(nxt)
                    return seen[1:]

                slots: dict[tuple[int, int], list] = {}
                for column in result.columns:
                    if column.stage is not None:
                        slots.setdefault((column.band, column.slot), []).append(column)
                found = []
                for columns in slots.values():
                    columns.sort(key=lambda c: c.stage)
                    for i, early in enumerate(columns):
                        for late in columns[i + 1 :]:
                            for a in late.groups:
                                for b in early.groups:
                                    if b in down(a) and a not in down(b):
                                        found.append((a, late.stage, b, early.stage))
                return (["instance-order"] if found else []), f"(later group, stage, earlier group it reaches, stage): {found}"

            self.run_seeds(f"repeat order {graph}", classify)

    def test_repeat_links_run_left_to_right(self):
        """In a repeated slot, a link between nodes of two instances in one band starts in the
        instance further left, feedback links aside. The check reads instances, so a group cut
        into segments (each its own instance) is judged per segment, which the plan-group check
        above cannot see: a group and the optional groups between its segments reach each other."""
        for graph in GRAPHS:

            def classify(seed, graph=graph):
                canvas, _, result = self.laid(seed, graph)[:3]
                instances = [c for c in result.columns if c.stage is not None]

                def instance(node):
                    x, y = node.pos
                    return next((c for c in instances if c.rect.x <= x < c.rect.right and c.rect.y <= y < c.rect.y + c.rect.h), None)

                nodes = canvas.node_map()
                feedback = feedback_links(canvas)
                found = []
                for link in canvas.links:
                    origin, target = nodes.get(str(link.origin_id)), nodes.get(str(link.target_id))
                    if link.id in feedback or origin is None or target is None:
                        continue
                    a, b = instance(origin), instance(target)
                    if a is None or b is None or a is b or (a.band, a.slot) != (b.band, b.slot):
                        continue
                    if a.rect.x > b.rect.x:
                        found.append((link.id, a.groups, a.stage, b.groups, b.stage))
                return (["instance-link"] if found else []), f"(link, from groups, stage, into groups, stage): {found}"

            self.run_seeds(f"repeat links {graph}", classify, seeds=(*SEEDS, *ALTERNATING))


class SwimlanesPropertyTest(LayoutPropertyTest):
    PRESET = "swimlanes"


class SteppedPropertyTest(LayoutPropertyTest):
    PRESET = "stepped"


class CockpitSplitPropertyTest(LayoutPropertyTest):
    PRESET = "cockpit-split"


class TowerPreviewPropertyTest(LayoutPropertyTest):
    PRESET = "tower-preview"


class GalleryPropertyTest(LayoutPropertyTest):
    PRESET = "gallery"


class SquarePropertyTest(LayoutPropertyTest):
    PRESET = "square"


if __name__ == "__main__":
    unittest.main()
