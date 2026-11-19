"""Where a route starts and ends next to its anchors (``cwc.route``).

A source box whose right edge is off the 20 px grid blocks, through the clearance, the column
just right of its output anchors on every line beside them; a target box whose left edge is off
the grid does the same left of its input anchors. The route starts and ends in the first column
the clearance leaves free, and a first or last grid point the search had to move off the line of
its anchors stays a reroute, so the segment from the anchor does not run slanted into a box.

Each canvas here routes a single link past a long obstacle, and every routed link is checked as
drawn through its reroutes under both renderers, at the default and at an unlimited reroute
spline cap.
"""

import math
import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS

from cwc.canvas.envelope import Renderer, box_facts, classic_box, vue_box
from cwc.canvas.links import SEGMENTS, crosses
from cwc.canvas.model import Canvas
from cwc.canvas.reroutes import link_paths
from cwc.layout.types import LayoutResult
from cwc.route import _points, route


def _node(nid, title, pos, size, *, collapsed=False, inputs=(), outputs=()):
    return {
        "id": nid,
        "type": "T",
        "title": title,
        "pos": list(pos),
        "size": list(size),
        "flags": {"collapsed": True} if collapsed else {},
        "order": nid - 1,
        "mode": 0,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "properties": {},
    }


def _canvas(source, obstacle, target):
    source["outputs"] = [{"name": "o", "type": "X", "links": [1]}]
    target["inputs"] = [{"name": "i", "type": "X", "link": 1}]
    return Canvas(
        {
            "last_node_id": 3,
            "last_link_id": 1,
            "nodes": [source, obstacle, target],
            "links": [[1, 1, 0, 3, 0, "X"]],
            "groups": [],
            "config": {},
            "extra": {},
            "version": 0.4,
        }
    )


def collapsed_source(width, title_length=40):
    """A collapsed source whose right edge is off the grid (a 40-character title puts it at
    397.5 classic; a narrower saved width cuts it there), its output on the line of a long
    obstacle's top edge, and a target below and far right."""
    return _canvas(
        _node(1, "A" * title_length, (0, 100), (width, 130), collapsed=True),
        _node(2, "Obstacle", (480, 100), (1500, 60)),
        _node(3, "Target", (2200, 400), (300, 60)),
    )


def offgrid_target(x):
    """A target at ``x``, off the grid, with a long obstacle in front of it on its input's line
    and the source above and far left."""
    return _canvas(
        _node(1, "Source", (0, 100), (300, 60)),
        _node(2, "Obstacle", (600, 380), (1540, 60)),
        _node(3, "Target", (x, 400), (300, 60)),
    )


def _route(canvas):
    return route(canvas, LayoutResult("bento", (), (), (), None, {}, {}, {}), DEFS)


class AnchorCellsTest(unittest.TestCase):
    def assert_routed_clear(self, canvas, label):
        result = _route(canvas)
        self.assertEqual(result.unresolved, (), label)
        self.assertEqual(result.rerouted, (1,), label)
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
                    self.assertEqual(hit, [], f"{label} {renderer.value} cap {cap}")

    def test_collapsed_source_every_width(self):
        # the classic right edge runs 380..397.5: off the grid for every width but 380
        for width in range(380, 401):
            with self.subTest(width=width):
                self.assert_routed_clear(collapsed_source(width), f"width {width}")

    def test_collapsed_source_start_moved_off_its_line(self):
        # a 50-character title puts the Vue right edge at about 458, so the first free column
        # (x = 480) meets the obstacle on the output's line and the start moves up; the first
        # grid point then has to stay a reroute
        for width in (380, 398, 400):
            with self.subTest(width=width):
                self.assert_routed_clear(collapsed_source(width, title_length=50), f"width {width}")

    def test_offgrid_target(self):
        for x in range(2200, 2221):
            with self.subTest(x=x):
                self.assert_routed_clear(offgrid_target(x), f"target x {x}")

    def test_end_points_kept_off_their_line(self):
        # a path one line above its output's line: right 6, down 8, right 6
        path = [(i, 3) for i in range(20, 27)] + [(26, j) for j in range(4, 12)] + [(i, 11) for i in range(27, 33)]
        corners = [6, 14]
        level = _points(path, corners, (3, 11))
        self.assertEqual(level[0], (500.0, 60.0))  # the flank: the start is redundant on its line
        self.assertEqual(level[-1], (540.0, 220.0))
        off = _points(path, corners, (4, 12))
        self.assertEqual(off[0], (400.0, 60.0))
        self.assertEqual(off[-1], (640.0, 220.0))
        self.assertEqual(off[1:-1], level)


if __name__ == "__main__":
    unittest.main()
