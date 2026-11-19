import copy
import json
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.canvas.geometry import Rect, bounding, snap_down, snap_up
from cwc.canvas.model import Canvas, Link, NodeMode, id_order, is_ascii_decimal, vec2
from cwc.prompt.model import is_link, prompt_diff, unwrap_prompt_value, values_equal, wrap_widget_value
from cwc.report import Code, Finding, Mode, Report, Severity


def _doc() -> dict:
    return {
        "last_node_id": 2,
        "last_link_id": 1,
        "nodes": [
            {"id": 1, "type": "A", "pos": [0, 0], "size": [100, 50], "flags": {}, "order": 0, "mode": 0,
             "outputs": [{"name": "X", "type": "X", "links": [1]}], "properties": {}, "x-unknown": {"k": 1}},
            {"id": 2, "type": "B", "pos": {"0": 200, "1": 40}, "size": [100, 50], "flags": {"collapsed": True},
             "order": 1, "mode": 4, "inputs": [{"name": "x", "type": "X", "link": 1}], "properties": {}},
        ],
        "links": [[1, 1, 0, 2, 0, "X"]],
        "groups": [{"id": 1, "title": "G", "bounding": [-20, -60, 400, 200], "color": "#335", "flags": {}}],
        "config": {},
        "extra": {"reroutes": [{"id": 1, "pos": [150, 10], "linkIds": [1]}], "linkExtensions": [{"id": 1, "parentId": 1}],
                  "vendor": {"keep": True}},
        "version": 0.4,
    }


class CanvasModelTest(unittest.TestCase):
    def test_id_order_reads_ascii_digits_only(self):
        # "²".isdigit() is true and int("²") raises: such an id sorts as text instead.
        keys = ["b", "10", "²", "9", "a"]
        self.assertEqual(sorted(keys, key=id_order), ["9", "10", "a", "b", "²"])
        self.assertFalse(is_ascii_decimal("²"))
        self.assertFalse(is_ascii_decimal(""))
        self.assertTrue(is_ascii_decimal("007"))

    def test_round_trip_is_lossless(self):
        doc = _doc()
        self.assertEqual(Canvas(copy.deepcopy(doc)).to_json(), doc)

    def test_views_read_typed_values(self):
        canvas = Canvas(_doc())
        a, b = canvas.nodes
        self.assertEqual(b.pos, (200.0, 40.0))
        self.assertTrue(b.collapsed)
        self.assertEqual(b.mode, NodeMode.BYPASS)
        self.assertEqual(a.frame(), Rect(0, -30, 100, 80))
        self.assertEqual(b.frame(), Rect(200, 10, 100, 30))
        self.assertEqual(canvas.link_map()[1], Link(1, 1, 0, 2, 0, "X"))
        self.assertEqual(canvas.link_extensions, {1: 1})
        self.assertEqual([(i, link.id) for i, _, link in canvas.iter_input_links(b)], [(0, 1)])

    def test_mutators_touch_only_their_field(self):
        doc = _doc()
        canvas = Canvas(doc)
        node = canvas.nodes[0]
        node.set_pos(40.0, 60.5)
        node.set_size(120.0, 80)
        canvas.groups[0].set_bounding(Rect(0, 0, 10.0, 20))
        expected = _doc()
        expected["nodes"][0]["pos"] = [40, 60.5]
        expected["nodes"][0]["size"] = [120, 80]
        expected["groups"][0]["bounding"] = [0, 0, 10, 20]
        self.assertEqual(canvas.to_json(), expected)
        self.assertEqual(json.dumps(canvas.to_json()["nodes"][0]["pos"]), "[40, 60.5]")

    def test_set_reroutes_removes_empty_tables(self):
        canvas = Canvas(_doc())
        canvas.set_reroutes([], [])
        self.assertNotIn("reroutes", canvas.raw["extra"])
        self.assertNotIn("linkExtensions", canvas.raw["extra"])
        self.assertEqual(canvas.raw["extra"]["vendor"], {"keep": True})

    def test_empty_canvas_shape(self):
        self.assertEqual(Canvas.empty().raw["version"], 0.4)

    def test_vec2_object_form(self):
        self.assertEqual(vec2({"0": 1, "1": 2}), (1.0, 2.0))


class GeometryTest(unittest.TestCase):
    def test_rect_relations(self):
        a = Rect(0, 0, 10, 10)
        self.assertTrue(a.overlaps(Rect(5, 5, 10, 10)))
        self.assertFalse(a.overlaps(Rect(10, 0, 5, 5)))  # touching edges do not overlap
        self.assertTrue(a.contains_rect(Rect(1, 1, 2, 2)))
        self.assertEqual(bounding([a, Rect(20, -5, 1, 1)]), Rect(0, -5, 21, 15))

    def test_snapping(self):
        self.assertEqual(snap_up(21), 40)
        self.assertEqual(snap_up(40), 40)
        self.assertEqual(snap_down(39), 20)
        self.assertEqual(snap_up(-1), 0)


class PromptModelTest(unittest.TestCase):
    def test_link_shape(self):
        self.assertTrue(is_link(["3", 0]))
        self.assertFalse(is_link(["3", True]))
        self.assertFalse(is_link([3, 0]))

    def test_value_wrapping(self):
        self.assertEqual(wrap_widget_value([1, 2]), {"__value__": [1, 2]})
        self.assertEqual(unwrap_prompt_value({"__value__": [1, 2]}), [1, 2])
        self.assertEqual(wrap_widget_value(3), 3)

    def test_js_number_equality(self):
        self.assertTrue(values_equal(4, 4.0))
        self.assertFalse(values_equal(True, 1))
        self.assertTrue(values_equal({"a": [1, 2.0]}, {"a": [1.0, 2]}))

    def test_prompt_diff(self):
        a = {"1": {"class_type": "A", "inputs": {"x": 1, "y": ["2", 0]}}, "2": {"class_type": "B", "inputs": {}}}
        b = {"1": {"class_type": "A", "inputs": {"x": 1.0, "y": ["2", 1]}}, "3": {"class_type": "B", "inputs": {}}}
        self.assertEqual(
            prompt_diff(a, b),
            ["node 2 missing", "node 3 unexpected", "node 1 input 'y': ['2', 0] != ['2', 1]"],
        )


class ReportTest(unittest.TestCase):
    def test_ok_and_deterministic_order(self):
        report = Report(Mode.COMPILE)
        report.add(Finding(Code.G1, Severity.WARNING, "w"))
        report.add(Finding(Code.E3, Severity.ERROR, "e", nodes=("10",)))
        report.metrics["b"] = 1
        report.metrics["a"] = 2
        self.assertFalse(report.ok)
        out = report.to_json()
        self.assertEqual([f["code"] for f in out["findings"]], ["E3", "G1"])
        self.assertEqual(list(out["metrics"]), ["a", "b"])

    def test_every_code_has_a_title(self):
        from cwc.report import TITLES

        self.assertEqual(set(TITLES), set(Code))


if __name__ == "__main__":
    unittest.main()
