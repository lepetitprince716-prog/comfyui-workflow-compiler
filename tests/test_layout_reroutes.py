"""The native reroute rules in docs/m2-frontend-facts.md section 6, checked against recorded oracle runs.

The fixtures ``tests/fixtures/layout/reroutes.*.json`` were written by ``tools/oracle/reroutes.py record``
from frontend 1.59.1. These tests check that the records agree with themselves and with the rules,
offline: the serialize round trip, the canonical write form, the spline control points.
"""

from __future__ import annotations

import json
import math
import unittest
from typing import Any

from tests.support import FIXTURES

LAYOUT = FIXTURES / "layout"
RUNS = ("classic", "vue", "classic_snap", "classic_offset80")
DRAWN_OK = ("main", "short", "groups", "parent_missing", "extension_reroute_missing", "cycle")


def _run(run: str) -> dict[str, Any]:
    return json.loads((LAYOUT / f"reroutes.{run}.json").read_text(encoding="utf-8"))


def _canvases() -> dict[str, Any]:
    return json.loads((LAYOUT / "reroutes.canvases.json").read_text(encoding="utf-8"))


def _topology(serialized: dict[str, Any]) -> dict[str, Any]:
    """What a router writes: links, reroutes, link extensions, floating links (viewport state `ds` left out)."""
    extra = serialized["extra"]
    return {
        "links": serialized["links"],
        "reroutes": extra.get("reroutes"),
        "linkExtensions": extra.get("linkExtensions"),
        "floatingLinks": serialized.get("floatingLinks"),
    }


class RoundTrip(unittest.TestCase):
    def test_serialize_is_a_fixed_point_in_every_run(self) -> None:
        for run in RUNS:
            for name, result in _run(run)["results"].items():
                self.assertIsNone(result["load_error"], (run, name))
                self.assertEqual(_topology(result["serialized"]), _topology(result["serialized_again"]), (run, name))

    def test_canonical_form_comes_back_unchanged(self) -> None:
        canvases = _canvases()
        for run in RUNS:
            results = _run(run)["results"]
            for name in ("main", "short", "groups"):
                if name not in results:
                    continue
                canvas, serialized = canvases[name], results[name]["serialized"]
                self.assertEqual(serialized["extra"]["reroutes"], canvas["extra"]["reroutes"], (run, name))
                self.assertEqual(serialized["extra"]["linkExtensions"], canvas["extra"]["linkExtensions"], (run, name))
                self.assertEqual(serialized["links"], canvas["links"], (run, name))

    def test_serialize_writes_the_04_form_with_a_viewport(self) -> None:
        serialized = _run("classic")["results"]["main"]["serialized"]
        self.assertEqual(serialized["version"], 0.4)
        self.assertIn("ds", serialized["extra"])
        self.assertNotIn("reroutes", serialized)

    def test_reroute_link_ids_are_derived_not_read(self) -> None:
        for run in RUNS:
            results = _run(run)["results"]
            if "linkids_disagree" not in results:
                continue
            written = results["linkids_disagree"]["serialized"]["extra"]["reroutes"]
            self.assertEqual([r["linkIds"] for r in written], [[1], [2]], run)
            self.assertEqual(results["linkids_only"]["serialized"]["extra"].get("reroutes"), None, run)
            self.assertEqual(results["linkids_only"]["live"]["reroutes"], [], run)

    def test_what_a_load_keeps(self) -> None:
        for run in ("classic", "vue"):
            r = _run(run)["results"]
            self.assertEqual([x["id"] for x in r["orphan"]["live"]["reroutes"]], [1], run)
            self.assertEqual([x["id"] for x in r["floating"]["live"]["reroutes"]], [1, 2], run)
            self.assertEqual(len(r["floating"]["serialized"]["floatingLinks"]), 2, run)
            self.assertEqual(r["extension_link_missing"]["serialized"]["extra"]["linkExtensions"], [{"id": 1, "parentId": 1}], run)
            self.assertEqual(r["extension_reroute_missing"]["serialized"]["extra"]["linkExtensions"][1], {"id": 2, "parentId": 77}, run)
            self.assertEqual(r["duplicate_id"]["live"]["reroutes"][0]["pos"], [800, 450], run)
            self.assertEqual(r["pos_missing"]["live"]["reroutes"][0]["pos"], [0, 0], run)

    def test_load_does_not_move_reroutes_even_with_snap_to_grid(self) -> None:
        canvas = _canvases()["main"]["extra"]["reroutes"]
        live = _run("classic_snap")["results"]["main"]["live"]["reroutes"]
        self.assertEqual([r["pos"] for r in live], [r["pos"] for r in canvas])


def _length(a: list[float], b: list[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


class Drawing(unittest.TestCase):
    def test_every_predicted_segment_is_a_recorded_draw_call(self) -> None:
        for run in RUNS:
            for name, result in _run(run)["results"].items():
                if name not in DRAWN_OK:
                    continue
                for row in result["draw"]["predicted"]:
                    for seg in row["segments"]:
                        self.assertTrue(seg["matched_call"], (run, name, row["link"], seg["key"]))

    def test_first_and_last_segment_use_the_plain_spline_offset(self) -> None:
        for run in RUNS:
            for name in ("main", "short"):
                if name not in _run(run)["results"]:
                    continue
                draw = _run(run)["results"][name]["draw"]
                for row in draw["predicted"]:
                    first, last = row["segments"][0], row["segments"][-1]
                    a, b = first["a"], first["b"]
                    self.assertAlmostEqual(first["c1"][0], a[0] + 0.25 * _length(a, b), places=6)
                    self.assertAlmostEqual(first["c1"][1], a[1], places=6)
                    a, b = last["a"], last["b"]
                    self.assertAlmostEqual(last["c2"][0], b[0] - 0.25 * _length(a, b), places=6)
                    self.assertAlmostEqual(last["c2"][1], b[1], places=6)

    def test_reroute_end_control_is_capped_by_the_offset_setting(self) -> None:
        for run in RUNS:
            for name in ("main", "short"):
                results = _run(run)["results"]
                if name not in results:
                    continue
                draw = results[name]["draw"]
                cap = draw["max_spline_offset"]
                self.assertEqual(cap, 80 if run == "classic_offset80" else 20, run)
                live = {r["id"]: r for r in draw["reroutes_after_draw"]}
                for row in draw["predicted"]:
                    for seg in row["segments"][:-1]:
                        r = live[int(seg["key"].split(":")[1])]
                        self.assertAlmostEqual(math.hypot(r["control_point"][0], r["control_point"][1]),
                                               min(cap, 0.25 * _length(seg["a"], seg["b"])), places=6, msg=(run, name, seg["key"]))
                        self.assertAlmostEqual(seg["c2"][0], seg["b"][0] + r["control_point"][0], places=6)

    def test_shared_segments_are_drawn_once(self) -> None:
        draw = _run("classic")["results"]["main"]["draw"]
        unique = {s["key"] for row in draw["predicted"] for s in row["segments"] if s["key"].startswith("reroute:")}
        finals = len(draw["predicted"])
        self.assertEqual(draw["call_count"], len(unique) + finals)
        self.assertEqual(len(unique), 7)

    def test_reroute_circle_radii(self) -> None:
        for run in ("classic", "vue"):
            draw = _run(run)["results"]["main"]["draw"]
            self.assertEqual(draw["reroute_radius"], 10)
            for circle in draw["circles"]:
                self.assertEqual(circle["radii"], [10, 8], (run, circle["id"]))


class Groups(unittest.TestCase):
    def test_reroute_membership_is_a_half_open_point_test(self) -> None:
        for run in RUNS:
            results = _run(run)["results"]
            if "groups" not in results:
                continue
            canvas = _canvases()["groups"]
            points = {r["id"]: r["pos"] for r in canvas["extra"]["reroutes"]}
            for group in results["groups"]["group_tests"]["groups"]:
                x, y, w, h = group["bounding"]
                expected = sorted(i for i, p in points.items() if x <= p[0] < x + w and y <= p[1] < y + h)
                self.assertEqual(group["reroute_ids"], expected, (run, group["title"]))

    def test_a_dragged_group_moves_each_member_once(self) -> None:
        for run in RUNS:
            results = _run(run)["results"]
            if "groups" not in results:
                continue
            tests = results["groups"]["group_tests"]
            members = {g["id"]: g for g in tests["groups"]}
            for move in tests["moves"]:
                group = members[move["group"]]
                self.assertEqual(move["moved_reroutes"], group["reroute_ids"], run)
                self.assertEqual(move["shifted_by_delta_reroutes"], group["reroute_ids"], run)
                self.assertEqual(move["shifted_by_delta_nodes"], sorted(int(n) for n in group["node_ids"]), run)


if __name__ == "__main__":
    unittest.main()
