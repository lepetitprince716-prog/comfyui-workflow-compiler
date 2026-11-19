"""The reroute link model (cwc.canvas.reroutes) against the recorded frontend draw calls.

``tests/fixtures/layout/reroutes.<run>.json`` holds, per canvas, every ``drawLink`` call of one frame
(start, end and both control points the canvas really used) and the segments the rules predict.
The model is built from the canvas alone (nodes, slot anchors, the reroute tables) and must give the
recorded segments, segment by segment, within the recorded tolerance on control points.
"""

from __future__ import annotations

import json
import math
import random
import unittest
from typing import Any

from tests.support import FIXTURES

from cwc.canvas.envelope import Renderer, box_facts
from cwc.canvas.model import Canvas
from cwc.canvas.reroutes import REROUTE_CAP, Reroutes, SegmentKey, link_paths, paths_between
from cwc.nodedefs.registry import NodeDefs

LAYOUT = FIXTURES / "layout"
DEFS = NodeDefs.load()
RUNS = {"classic": Renderer.CLASSIC, "vue": Renderer.VUE, "classic_snap": Renderer.CLASSIC, "classic_offset80": Renderer.CLASSIC}
DRAWN = ("main", "short", "groups", "parent_missing", "extension_reroute_missing", "cycle")
TOLERANCE = 1e-3  # on control points, as recorded


def _recorded(run: str) -> dict[str, Any]:
    return json.loads((LAYOUT / f"reroutes.{run}.json").read_text(encoding="utf-8"))


def _canvases() -> dict[str, Any]:
    return json.loads((LAYOUT / "reroutes.canvases.json").read_text(encoding="utf-8"))


def _paths(name: str, run: str, cap: float):
    """The model's paths for canvas ``name`` as the frontend laid it out in ``run``: node positions and
    sizes are those it serialized after the load (snap to grid moves and resizes nodes)."""
    canvas = Canvas(_canvases()[name])
    nodes = canvas.node_map()
    for loaded in _recorded(run)["results"][name]["serialized"]["nodes"]:
        nodes[str(loaded["id"])].raw["pos"], nodes[str(loaded["id"])].raw["size"] = loaded["pos"], loaded["size"]
    facts = {node.key: box_facts(node, DEFS.get(node.type)) for node in canvas.nodes}
    return link_paths(canvas, facts, RUNS[run], cap)


def _close(got: tuple[float, float], want: list[float]) -> bool:
    return abs(got[0] - want[0]) <= TOLERANCE and abs(got[1] - want[1]) <= TOLERANCE


def _same(segment, a, b, c1, c2) -> bool:
    return all(_close(got, want) for got, want in ((segment.p0, a), (segment.p3, b), (segment.c1, c1), (segment.c2, c2)))


def agreement(run: str) -> tuple[int, int, list]:
    """(agreeing segments, recorded segments, misses) over the predicted rows of every drawn canvas."""
    results = _recorded(run)["results"]
    agree, total, misses = 0, 0, []
    for name in DRAWN:
        if name not in results:
            continue
        draw = results[name]["draw"]
        paths = _paths(name, run, draw["max_spline_offset"])
        for row in draw["predicted"]:
            path = paths[row["link"]]
            if len(path.segments) != len(row["segments"]):
                total += len(row["segments"])
                misses.append((name, row["link"], "segment count", len(path.segments), len(row["segments"])))
                continue
            for index, (got, want) in enumerate(zip(path.segments, row["segments"])):
                total += 1
                if _same(got, want["a"], want["b"], want["c1"], want["c2"]):
                    agree += 1
                else:
                    misses.append((name, row["link"], index))
    return agree, total, misses


def drawn_calls(run: str) -> tuple[int, int, list]:
    """(calls matched, calls recorded, misses): the segments the model draws once each, in draw order,
    against the ``drawLink`` calls of the frame."""
    results = _recorded(run)["results"]
    matched, total, misses = 0, 0, []
    for name in DRAWN:
        if name not in results:
            continue
        draw = results[name]["draw"]
        paths = _paths(name, run, draw["max_spline_offset"])
        seen: set[SegmentKey] = set()
        mine = []
        for path in paths.values():
            for key, segment in zip(path.keys, path.segments):
                if key not in seen:
                    seen.add(key)
                    mine.append(segment)
        total += draw["call_count"]
        left = list(mine)
        for call in draw["calls"]:
            hit = next(
                (s for s in left if _same(s, call["start"], call["end"], *call["control_points"])),
                None,
            )
            if hit is None:
                misses.append((name, call["id"], call["start"], call["end"]))
            else:
                left.remove(hit)
                matched += 1
        if left:
            misses.append((name, "drawn by the model but not recorded", len(left)))
    return matched, total, misses


class Agreement(unittest.TestCase):
    def test_every_recorded_segment_in_every_run(self) -> None:
        for run in RUNS:
            agree, total, misses = agreement(run)
            self.assertGreater(total, 0, run)
            self.assertEqual((agree, misses), (total, []), run)

    def test_the_drawn_segments_are_the_recorded_draw_calls(self) -> None:
        for run in RUNS:
            matched, total, misses = drawn_calls(run)
            self.assertGreater(total, 0, run)
            self.assertEqual((matched, misses), (total, []), run)

    def test_recorded_counts(self) -> None:
        # Pins the size of the comparison so an emptied fixture cannot pass.
        counts = {run: agreement(run)[:2] for run in RUNS}
        self.assertEqual(counts["classic"], counts["vue"])
        self.assertEqual(counts["classic"], counts["classic_snap"])
        self.assertEqual(counts["classic"][0], counts["classic"][1])
        self.assertGreater(counts["classic"][1], 25)
        self.assertGreater(counts["classic_offset80"][1], 0)

    def test_the_default_cap_is_the_recorded_default(self) -> None:
        self.assertEqual(REROUTE_CAP, _recorded("classic")["results"]["main"]["draw"]["max_spline_offset"])


def _plain_read(entries: list[dict[str, Any]]) -> tuple[dict[int, tuple[float, float]], dict[int, int]]:
    """The load rules of section 6.2 as written: each assignment walks up from its parent through
    the reroutes defined so far and is refused when it meets the reroute itself."""
    points: dict[int, tuple[float, float]] = {}
    assigned: dict[int, int] = {}
    for raw in entries:
        rid = raw["id"]
        points[rid] = (float(raw["pos"][0]), float(raw["pos"][1]))
        if "parentId" not in raw:
            assigned.pop(rid, None)
            continue
        current: int | None = raw["parentId"]
        while current is not None and current in points and current != rid:
            current = assigned.get(current)
        if current != rid:
            assigned[rid] = raw["parentId"]
    return points, {rid: parent for rid, parent in assigned.items() if parent in points}


def _plain_chain(points: dict[int, Any], parents: dict[int, int], current: int | None) -> tuple[int, ...]:
    out: list[int] = []
    while current in points and current not in out:
        out.append(current)
        current = parents.get(current)
    return tuple(reversed(out))


class Chains(unittest.TestCase):
    """What a load keeps decides the chain (docs/m2-frontend-facts.md section 6.2)."""

    def chains(self, name: str) -> dict[int, tuple[int, ...]]:
        canvas = Canvas(_canvases()[name])
        table = Reroutes.read(canvas)
        return {link.id: table.chain(link.id) for link in canvas.links}

    def test_a_chain_and_a_bus_tree(self) -> None:
        self.assertEqual(self.chains("main"), {1: (1, 2, 3), 2: (4, 5), 3: (4, 6), 4: (4,), 5: (7,)})

    def test_an_extension_naming_a_missing_reroute_is_a_plain_spline(self) -> None:
        self.assertEqual(self.chains("extension_reroute_missing"), {1: (1,), 2: ()})

    def test_a_dangling_parent_ends_the_chain(self) -> None:
        self.assertEqual(self.chains("parent_missing"), {1: (1,), 2: (2,)})

    def test_a_cycle_is_cut_at_the_assignment_that_would_close_it(self) -> None:
        self.assertEqual(self.chains("cycle"), {1: (2, 1), 2: (2,)})

    def test_the_later_duplicate_wins_and_a_missing_pos_is_the_origin(self) -> None:
        self.assertEqual(Reroutes.read(Canvas(_canvases()["duplicate_id"])).points, {1: (800.0, 450.0)})
        self.assertEqual(Reroutes.read(Canvas(_canvases()["pos_missing"])).points[1], (0.0, 0.0))

    def test_the_read_matches_the_plain_walk_on_random_tables(self) -> None:
        # forward references, duplicate ids, self parents, dangling parents and cycles of any length;
        # links 1 to 4 exist, an extension names link 5 too; some floating links
        rng = random.Random(7)
        for _ in range(400):
            n = rng.randint(1, 9)
            entries = []
            for _ in range(rng.randint(1, 14)):
                entry: dict[str, Any] = {"id": rng.randint(1, n), "pos": [rng.randint(0, 9), 0]}
                if rng.random() < 0.85:
                    entry["parentId"] = rng.randint(1, n + 1)
                entries.append(entry)
            extensions = [{"id": k, "parentId": rng.randint(1, n + 1)} for k in range(1, 6)]
            links = [[k, 1, 0, 2, 0, "X"] for k in range(1, 5)]
            floating = [{"id": 10 + k, "parentId": rng.randint(1, n + 1)} for k in range(rng.randint(0, 2))]
            canvas = Canvas(
                {"nodes": [], "links": links, "floatingLinks": floating, "extra": {"reroutes": entries, "linkExtensions": extensions}}
            )
            table = Reroutes.read(canvas)
            points, parents = _plain_read(entries)
            ends = {e["id"]: e["parentId"] for e in extensions}
            starts = [ends[k] for k in range(1, 5)] + [f["parentId"] for f in floating]
            kept = {rid for start in starts for rid in _plain_chain(points, parents, start)}
            self.assertEqual(list(table.points), [rid for rid in points if rid in kept], entries)
            self.assertEqual(table.points, {rid: points[rid] for rid in table.points}, entries)
            self.assertEqual(table.parents, {rid: p for rid, p in parents.items() if rid in kept}, entries)
            for k in range(1, 5):
                self.assertEqual(table.chain(k), _plain_chain(points, parents, ends[k]), entries)
            self.assertEqual(table.chain(5), (), entries)

    def test_a_load_drops_the_reroutes_no_link_passes_through(self) -> None:
        # docs/m2-frontend-facts.md 6.2 and 6.5, against the frontend's own serialize after the load
        for run in ("classic", "vue"):
            results = _recorded(run)["results"]
            for name, raw in _canvases().items():
                if name == "schema_v1":  # the 1.0 form is not read by the canvas model
                    continue
                kept = sorted(r["id"] for r in results[name]["serialized"].get("extra", {}).get("reroutes", []))
                self.assertEqual(sorted(Reroutes.read(Canvas(raw)).points), kept, (run, name))

    def test_an_orphan_reroute_is_not_read_and_the_written_table_is_untouched(self) -> None:
        canvas = Canvas(_canvases()["orphan"])
        before = canvas.to_json()
        table = Reroutes.read(canvas)
        self.assertEqual(list(table.points), [1])
        self.assertEqual(canvas.to_json(), before)

    def test_a_reroute_that_is_its_own_parent_is_refused(self) -> None:
        canvas = Canvas(_canvases()["main"])
        canvas.raw["extra"]["reroutes"][0]["parentId"] = 1
        table = Reroutes.read(canvas)
        self.assertEqual(table.chain(1), (1, 2, 3))
        self.assertNotIn(1, table.parents)


class Shape(unittest.TestCase):
    def test_a_link_without_reroutes_is_one_spline(self) -> None:
        (path,) = (p for p in _paths("extension_reroute_missing", "classic", 20).values() if not p.rerouted)
        self.assertEqual(len(path.segments), 1)
        self.assertEqual(path.keys, (SegmentKey("link", path.link_id),))

    def test_segments_are_contiguous_and_keyed(self) -> None:
        for name in DRAWN:
            for path in _paths(name, "classic", 20).values():
                for a, b in zip(path.segments, path.segments[1:]):
                    self.assertEqual(a.p3, b.p0)
                self.assertEqual(len(path.segments), len(path.chain) + 1)
                self.assertEqual([k.id for k in path.keys[:-1]], list(path.chain))
                self.assertEqual(path.keys[-1], SegmentKey("link", path.link_id))

    def test_shared_segments_have_one_value(self) -> None:
        paths = _paths("main", "classic", 20)
        by_key: dict[SegmentKey, set] = {}
        for path in paths.values():
            for key, segment in zip(path.keys, path.segments):
                if key.kind == "reroute":
                    by_key.setdefault(key, set()).add(segment)
        self.assertEqual(len(by_key), 7)
        self.assertTrue(all(len(values) == 1 for values in by_key.values()))

    def test_the_cap_limits_the_reroute_control_offset(self) -> None:
        low = paths_between(Canvas(_canvases()["main"]), {1: ((406.0, 234.0), (910.0, 114.0))}, cap=5.0)[1]
        high = paths_between(Canvas(_canvases()["main"]), {1: ((406.0, 234.0), (910.0, 114.0))}, cap=500.0)[1]
        reroute = low.segments[0].p3
        self.assertAlmostEqual(math.dist(low.segments[0].c2, reroute), 5.0)
        self.assertGreater(math.dist(high.segments[0].c2, reroute), 5.0)
        self.assertEqual(low.segments[0].c1, high.segments[0].c1)  # the first control point is never capped


if __name__ == "__main__":
    unittest.main()
