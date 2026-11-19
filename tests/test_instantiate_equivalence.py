"""Instantiate keeps the prompts: the canvas exports the factory prompt as written, the
all-enabled prompt once its bypassed nodes are enabled, and passes the compile checks."""

import copy
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.canvas.model import Canvas, NodeMode
from cwc.instantiate import instantiate
from cwc.nodedefs import derive
from cwc.prompt.from_canvas import canvas_to_prompt
from cwc.prompt.model import prompt_diff
from cwc.report import Code
from cwc.verify import verify_compile
from tests.fixtures.instantiate import graphs

CASES = graphs.cases()
WORKFLOWS = [name for name in CASES if name != "capture"]  # the capture graph has unused branches


def export(canvas, defs):
    return canvas_to_prompt(canvas, defs, widget_layout=derive.widget_layout)


def enabled(canvas):
    raw = copy.deepcopy(canvas.raw)
    for node in raw["nodes"]:
        if node["mode"] == NodeMode.BYPASS:
            node["mode"] = int(NodeMode.ALWAYS)
    return Canvas(raw)


class EquivalenceTest(unittest.TestCase):
    def test_the_canvas_exports_the_factory_prompt(self):
        for name, (authoring, defs) in CASES.items():
            with self.subTest(name):
                self.assertEqual(prompt_diff(authoring.factory, export(instantiate(authoring), defs)), [])

    def test_enabling_every_bypassed_node_exports_the_full_prompt(self):
        for name, (authoring, defs) in CASES.items():
            with self.subTest(name):
                self.assertEqual(prompt_diff(authoring.full, export(enabled(instantiate(authoring)), defs)), [])

    def test_the_compile_checks_pass(self):
        for name in WORKFLOWS:
            authoring, defs = CASES[name]
            with self.subTest(name):
                report = verify_compile(instantiate(authoring), authoring, defs)
                self.assertTrue(report.ok, [(f.code.value, f.message) for f in report.findings])

    def test_the_capture_graph_fails_only_for_branches_that_feed_nothing(self):
        authoring, defs = CASES["capture"]
        report = verify_compile(instantiate(authoring), authoring, defs)
        self.assertEqual({f.code for f in report.findings}, {Code.N1})

    def test_only_ui_optional_nodes_are_bypassed(self):
        for name, (authoring, _) in CASES.items():
            canvas = instantiate(authoring)
            with self.subTest(name):
                bypassed = {str(node.id) for node in canvas.nodes if node.mode == NodeMode.BYPASS}
                self.assertEqual(bypassed, {key for key, intent in authoring.intents.items() if intent.ui_optional})

    def test_optional_chains_are_present(self):
        authoring, _ = CASES["optional_chain"]
        self.assertEqual(sum(1 for i in authoring.intents.values() if i.ui_optional), 3)
        self.assertEqual(len(instantiate(authoring).nodes), len(authoring.full))

    def test_optional_nodes_feeding_grown_or_chosen_slots_resolve_in_the_factory_prompt(self):
        authoring, defs = CASES["autogrow"]
        flip = authoring.ids["flip"]
        encode = authoring.factory[authoring.ids["encode"]]["inputs"]
        self.assertNotIn(flip, authoring.factory)
        self.assertEqual(encode["images.image_1"], [authoring.ids["src_b"], 0])
        authoring, defs = CASES["dynamic_match"]
        resize = authoring.factory[authoring.ids["resize"]]["inputs"]
        self.assertEqual(resize["resize_type.multiplier"], 1.0)  # the widget default replaces the removed primitive


if __name__ == "__main__":
    unittest.main()
