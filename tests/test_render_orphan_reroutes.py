"""A reroute no link passes through is not drawn: a load drops it (docs/m2-frontend-facts.md 6.2)."""

from __future__ import annotations

import unittest

from tests.test_render import NoDefs, by_class, canvas, node, parse

from cwc.render import render_svg
from cwc.render.svg import render

LINKS = [[1, 1, 0, 2, 0, "X"]]
KEPT = {"id": 1, "pos": [400, 300]}
ORPHAN = {"id": 2, "pos": [9000, 7000]}


def _canvas(reroutes: list[dict], extra: dict | None = None):
    extension = {"linkExtensions": [{"id": 1, "parentId": 1}]}
    return canvas([node(1, 0, 0), node(2, 800, 0)], links=LINKS, extra={"reroutes": reroutes, **extension, **(extra or {})})


class OrphanReroutes(unittest.TestCase):
    def test_an_orphan_is_not_drawn_and_does_not_widen_the_view_box(self) -> None:
        with_orphan = render(_canvas([KEPT, ORPHAN]), NoDefs())
        without = render(_canvas([KEPT]), NoDefs())
        self.assertEqual(with_orphan.reroutes, 1)
        self.assertEqual(with_orphan.view_box, without.view_box)
        self.assertEqual(with_orphan.svg, without.svg)

    def test_the_orphan_circle_is_absent(self) -> None:
        root = parse(render_svg(_canvas([KEPT, ORPHAN]), NoDefs()))
        self.assertEqual([el.get("id") for el in by_class(root, "circle", "reroute")], ["reroute-1"])

    def test_a_reroute_named_only_by_link_ids_is_an_orphan(self) -> None:
        stale = {"id": 3, "pos": [5000, 5000], "linkIds": [1]}
        self.assertEqual(render(_canvas([KEPT, stale]), NoDefs()).reroutes, 1)

    def test_a_floating_link_keeps_its_reroute(self) -> None:
        cv = _canvas([KEPT, ORPHAN])
        cv.raw["floatingLinks"] = [{"id": 9, "origin_id": 1, "origin_slot": 0, "target_id": -1, "target_slot": -1, "parentId": 2, "type": "X"}]
        self.assertEqual(render(cv, NoDefs()).reroutes, 2)

    def test_the_written_table_is_not_changed_by_a_render(self) -> None:
        cv = _canvas([KEPT, ORPHAN])
        before = cv.to_json()
        render(cv, NoDefs())
        self.assertEqual(cv.to_json(), before)


if __name__ == "__main__":
    unittest.main()
