"""Layout with the bento preset in compile mode, measured by the geometry checks."""

import json
import time
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, txt2img
from tests.layout_cases import (
    BENTO,
    COMPILE_ERRORS,
    OTHERS,
    SIMPLE,
    SIMPLE_ERRORS,
    controlnet,
    laid_out,
    long_chain,
    optional_upscale,
    with_lora,
)
from tests.layout_cases import column_holes, extent_x, sections, top_links, unbridged_gaps
from tests.layout_generator import random_graph, reduced_graph

from cwc.annotate import annotate, apply_notes
from cwc.canvas.geometry import GRID
from cwc.layout import layout


def errors(report, codes):
    return [f"{f.code.value}: {f.message}" for f in report.findings if f.severity.value == "error" and f.code.value in codes]


def strip(doc):
    """The document without what layout may write."""
    doc = json.loads(json.dumps(doc))
    doc.pop("groups", None)
    for raw in doc["nodes"]:
        raw.pop("pos", None)
        raw.pop("size", None)
    return doc


class BentoChecksTest(unittest.TestCase):
    def check(self, name, build, codes):
        canvas, plan, result, report, before = laid_out(build())
        with self.subTest(graph=name):
            self.assertEqual(errors(report, codes), [])
            self.assertEqual(strip(canvas.raw), strip(before))
            sizes = {str(raw["id"]): raw["size"] for raw in before["nodes"]}
            for node in canvas.nodes:
                w, h = node.size
                self.assertGreaterEqual(w, sizes[node.key][0], node.key)
                self.assertEqual(h, sizes[node.key][1], node.key)
                self.assertTrue(all(v % GRID == 0 for v in node.pos), (node.key, node.pos))
            groups = canvas.raw["groups"]
            for group in groups:
                self.assertTrue(all(v % GRID == 0 for v in group["bounding"]), group)
                self.assertEqual(sorted(group), ["bounding", "color", "flags", "font_size", "id", "title"])
            numbers = sorted({int(g["title"][:2]) for g in groups})
            self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
            self.assertEqual([g["id"] for g in groups], list(range(1, len(groups) + 1)))
        return report

    def test_simple_graphs_are_clean(self):
        for name, build in SIMPLE.items():
            self.check(name, build, COMPILE_ERRORS + SIMPLE_ERRORS)

    def test_other_graphs_have_no_compile_errors(self):
        for name, build in OTHERS.items():
            self.check(name, build, COMPILE_ERRORS)

    def test_cockpit_is_group_01_at_the_top_right_of_the_guide(self):
        canvas, plan, result, report, _ = laid_out(with_lora())
        cockpit = canvas.raw["groups"][result.group_index["cockpit"]]
        self.assertTrue(cockpit["title"].startswith("01 "))
        self.assertEqual(cockpit["bounding"][:2], [500, 0])
        self.assertEqual(result.guide.as_list(), [0, 0, 460, 900])


def boundings(laid):
    canvas, _, result = laid[:3]
    return {key: canvas.raw["groups"][i]["bounding"] for key, i in result.group_index.items()}


class ZeroHoleTest(unittest.TestCase):
    """Zero-hole by column (``tests.layout_cases``)."""

    def assert_zero_hole(self, laid):
        canvas, plan, result = laid[:3]
        links = top_links(canvas, plan)
        cockpit = frozenset(g.key for g in plan.groups if g.parent is None and g.zone.value == "cockpit")
        for section in sections(canvas, plan, result):
            self.assertEqual(column_holes(section), [])
            self.assertEqual(unbridged_gaps(section, links, cockpit), [])

    def test_removing_the_optional_modules_is_no_wider(self):
        for far_doc, near_doc in (
            (optional_upscale(controlnet(with_lora())), controlnet()),
            (optional_upscale(with_lora()), txt2img()),
        ):
            far, near = laid_out(far_doc), laid_out(near_doc)
            self.assert_zero_hole(far)
            self.assert_zero_hole(near)
            self.assertLessEqual(extent_x(near[0]), extent_x(far[0]))

    def test_removing_the_control_branch_collapses_band_1(self):
        # Without a control group there is no lower band: every band is a section's main row.
        bare = laid_out(with_lora(optional=False))
        self.assert_zero_hole(bare)
        self.assertEqual(len(bare[2].bands), len(sections(*bare[:3])))
        self.assertEqual(len(bare[2].corridors), len(bare[2].bands) - 1)
        self.assertEqual(len(laid_out(controlnet())[2].corridors), len(laid_out(controlnet())[2].bands) - 1)


class GroupShapeTest(unittest.TestCase):
    """R2: a group keeps its shape when an optional group cuts its chain in the middle; the
    group is cut there into consecutive parts and the optional group sits between them."""

    def test_model_chain_keeps_one_row_with_and_without_optional_loras(self):
        # Seed 5: two optional LoRAs between the checkpoint and two kept LoRAs of the Model group.
        shapes = []
        for doc in (random_graph(5), reduced_graph(5)):
            canvas, plan, result = laid_out(doc)[:3]
            group = next(g for g in plan.groups if g.zone.value == "model")
            nodes = canvas.node_map()
            spots = sorted(nodes[key].pos for key in group.members)
            parts = sorted(key for key in result.group_index if key == group.key or key.startswith(f"{group.key}/"))
            heights = [canvas.raw["groups"][result.group_index[key]]["bounding"][3] for key in parts]
            self.assertEqual(len(spots), 3)
            self.assertEqual(len({x for x, _ in spots}), 3, spots)  # one node per layer
            self.assertEqual(len({y for _, y in spots}), 1, spots)  # one row
            shapes.append((parts, max(heights)))
        (full, full_h), (reduced, reduced_h) = shapes
        self.assertEqual(full, ["model/a", "model/b"])
        self.assertEqual(reduced, ["model"])
        self.assertEqual(full_h, reduced_h)

    def test_the_optional_group_bridges_the_cut(self):
        canvas, plan, result = laid_out(random_graph(5))[:3]
        box = boundings((canvas, plan, result))
        extras = next(g.key for g in plan.groups if g.zone.value == "optional")
        self.assertEqual([result.labels["model/a"], result.labels["model/b"]], ["02a", "02b"])
        self.assertLess(box["model/a"][0] + box["model/a"][2], box[extras][0])
        self.assertLess(box[extras][0] + box[extras][2], box["model/b"][0])
        self.assertGreater(box[extras][1], box["model/a"][1])  # in the lower band
        nodes = canvas.node_map()
        for key in next(g for g in plan.groups if g.zone.value == "model").members:
            part = "model/a" if nodes[key].type == "CheckpointLoaderSimple" else "model/b"
            self.assertEqual(result.membership[key], result.group_index[part], key)


class DeterminismTest(unittest.TestCase):
    def test_same_input_same_bytes(self):
        one = laid_out(optional_upscale(controlnet(with_lora())))[0].raw
        two = laid_out(optional_upscale(controlnet(with_lora())))[0].raw
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))


# The speed test below holds the layout to half a second on a machine as fast as the one it was
# written on, and to a proportionally longer time on a slower one. The yardstick is a fixed
# pure-Python workload of the same kind as the layout (dictionary and list inserts, lookups and
# deletes, tuple sorts, float comparisons) that is timed in the same process just before.
# REFERENCE_SECONDS is what that workload takes on the development machine: the fastest of
# at least 9 runs on each of CPython 3.10, 3.13 and 3.14 gave 0.097, 0.100 and 0.101 s, and the
# largest of the three is the constant. Taking the largest means a slower machine is granted a
# little less extra time, and a faster one never more.
REFERENCE_SECONDS = 0.101
LAYOUT_SECONDS_ON_THE_REFERENCE = 0.5


def reference_workload() -> int:
    """A deterministic stand-in for the layout's work. Returns a checksum so none of it is dead."""
    cells: dict[tuple[int, int], float] = {}
    columns: dict[int, list[tuple[float, int]]] = {}
    for i in range(250000):
        cell = (i * 37 % 211, i * 91 % 307)
        cells[cell] = i * 0.25
        columns.setdefault(cell[0], []).append((cells[cell], i))
    checksum = 0
    for column, entries in columns.items():
        entries.sort(key=lambda entry: (entry[0] % 17.5, entry[1]))
        top = 0.0
        for height, i in entries:
            if height > top:
                top = height
            if (i + column) % 3 == 0 and (column, i % 307) in cells:
                del cells[(column, i % 307)]
            checksum += i & 7
        checksum += int(top) & 15
    ordered = sorted(cells.items(), key=lambda item: (item[0][1], -item[1], item[0][0]))
    return checksum + len(ordered) + (int(ordered[0][1]) if ordered else 0)


def reference_seconds(runs: int = 5) -> float:
    """The fastest of ``runs`` timings of the reference workload: a busy machine slows a run, not all."""
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        reference_workload()
        times.append(time.perf_counter() - start)
    return min(times)


class SpeedTest(unittest.TestCase):
    def test_200_nodes_under_half_a_second(self):
        reference = reference_seconds()
        limit = LAYOUT_SECONDS_ON_THE_REFERENCE * max(1.0, reference / REFERENCE_SECONDS)
        # The fastest of three runs: a busy machine slows one run, not all three.
        times = []
        for _ in range(3):
            canvas, authoring = compiled(long_chain(48))
            self.assertGreaterEqual(len(canvas.nodes), 199)
            plan = annotate(canvas, DEFS, authoring)
            apply_notes(canvas, plan)
            start = time.perf_counter()
            layout(canvas, plan, BENTO, DEFS)
            times.append(time.perf_counter() - start)
        elapsed = min(times)
        print(
            f"\nlayout of {len(canvas.nodes)} nodes: {elapsed:.3f} s (fastest of three),"
            f" reference {reference:.3f} s, limit {limit:.3f} s"
        )
        self.assertLess(elapsed, limit, times)


if __name__ == "__main__":
    unittest.main()
