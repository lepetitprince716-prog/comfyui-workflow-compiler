"""The router (``cwc.route``) on the layout test graphs: the canonical write form of
``docs/m2-frontend-facts.md`` section 6.6, no routed link through a box at the default and at an
unlimited reroute spline cap, plain splines left alone, and the same bytes on every run."""

import json
import math
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS
from tests.layout_cases import OTHERS, SIMPLE, routed

from cwc.canvas.envelope import Renderer, box_facts, classic_box, vue_box
from cwc.canvas.links import SEGMENTS, crosses
from cwc.canvas.reroutes import Reroutes, link_paths

CASES = {**SIMPLE, **OTHERS}


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.done = {name: routed(make()) for name, make in CASES.items()}

    def test_canonical_form(self):
        for name, (canvas, *_rest) in self.done.items():
            with self.subTest(case=name):
                extra = canvas.raw.get("extra", {})
                reroutes, extensions = extra.get("reroutes", []), extra.get("linkExtensions", [])
                ids = [r["id"] for r in reroutes]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(isinstance(i, int) and i > 0 for i in ids))
                self.assertNotIn("floatingLinks", canvas.raw)
                by_id = {r["id"]: r for r in reroutes}
                for r in reroutes:
                    self.assertLessEqual(set(r), {"id", "pos", "linkIds", "parentId"})
                    self.assertNotIn("floating", r)
                    if "parentId" in r:
                        self.assertIn(r["parentId"], by_id)
                    self.assertTrue(all(float(v) % 20 == 0 for v in r["pos"]), r)
                table = Reroutes.read(canvas)
                links = {link.id: link for link in canvas.links}
                through: dict[int, list[int]] = {}
                for ext in extensions:
                    for rid in table.chain(ext["id"]):
                        through.setdefault(rid, []).append(ext["id"])
                self.assertEqual({r["id"]: r["linkIds"] for r in reroutes}, {rid: sorted(ls) for rid, ls in through.items()})
                for rid, members in through.items():
                    outputs = {(str(links[i].origin_id), links[i].origin_slot) for i in members}
                    self.assertEqual(len(outputs), 1, f"reroute {rid} shared across outputs")
                self.assertEqual([e["id"] for e in extensions], sorted(e["id"] for e in extensions))

    def test_no_box_crossed_at_any_cap(self):
        for name, (canvas, *_rest) in self.done.items():
            nodes = canvas.node_map()
            facts = {key: box_facts(node, DEFS.get(node.type)) for key, node in nodes.items()}
            for renderer, box in ((Renderer.CLASSIC, classic_box), (Renderer.VUE, vue_box)):
                boxes = {key: box(node, facts[key]).inflate(-1e-6, -1e-6, -1e-6, -1e-6) for key, node in nodes.items()}
                for cap in (20.0, math.inf):
                    paths = link_paths(canvas, facts, renderer, cap)
                    for link in canvas.links:
                        path = paths[link.id].points(SEGMENTS)
                        own = {str(link.origin_id), str(link.target_id)}
                        hit = [key for key, rect in boxes.items() if key not in own and crosses(path, rect)]
                        with self.subTest(case=name, renderer=renderer.value, cap=cap, link=link.id):
                            self.assertEqual(hit, [])

    def test_result_counts(self):
        for name, (canvas, _plan, _result, report, _before, routes) in self.done.items():
            with self.subTest(case=name):
                self.assertEqual(report.metrics["G2"]["count"], 0)
                self.assertEqual(routes.unresolved, ())
                self.assertEqual(set(routes.rerouted), set(canvas.link_extensions))
                ids = [c.id for c in routes.channels]
                self.assertEqual(ids, sorted(set(ids)))

    def test_same_bytes_every_run(self):
        for name in ("two_stage", "long_chain_6"):
            with self.subTest(case=name):
                again = routed(CASES[name]())[0]
                self.assertEqual(json.dumps(again.to_json(), sort_keys=True), json.dumps(self.done[name][0].to_json(), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
