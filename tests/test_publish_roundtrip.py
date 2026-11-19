"""M1 acceptance on a real graph: API prompt, ``cwc compile``, a real frontend, the same prompt.

Runs only when given a graph and an oracle (``tests/oracle_support.py``):

    CWC_PUBLISH_GRAPH    path of an API prompt (or authoring document) to compile; it stays outside the repository
    CWC_ORACLE_URL, CWC_ORACLE_PYTHON

The compiled canvas is loaded in the frontend twice, in the factory state and with every
bypassed node switched on. Each export must equal the prompt the compiler holds for that
state (``authoring.factory``, ``authoring.full``), and the all-enabled export must equal the
input prompt: a factory export drops the optional nodes, so only the all-enabled one can.
With optional nodes in the graph, the factory export must also lack exactly those nodes.
``serialize()`` after the load must give back the compiled canvas as the full pipeline wrote
it (laid out, grouped and routed): every node field, ``pos`` and the sizes included, the groups,
the links in order, and ``extra.reroutes`` and ``extra.linkExtensions``.
"""

import json
import os
import unittest
from pathlib import Path

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.layout import FONT_SIZE
from cwc.nodedefs.registry import NodeDefs
from cwc.pipeline import compile_document
from cwc.prompt.model import prompt_diff
from tests import oracle_support

GRAPH = os.environ.get("CWC_PUBLISH_GRAPH")


def without(entry, *keys):
    return {key: value for key, value in entry.items() if key not in keys}


@unittest.skipUnless(GRAPH, "set CWC_PUBLISH_GRAPH to an API prompt to run")
class PublishRoundtripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        oracle_support.require()
        cls.doc = json.loads(Path(GRAPH).read_text(encoding="utf-8"))
        cls.compiled = compile_document(cls.doc, NodeDefs.load())
        cls.written = cls.compiled.canvas.to_json()
        cls.factory, cls.enabled = oracle_support.roundtrip([cls.written, oracle_support.all_enabled(cls.written)])

    def test_the_compile_report_is_ok(self):
        findings = [(f.code.value, f.severity.value, f.message) for f in self.compiled.report.findings]
        self.assertTrue(self.compiled.report.ok, findings)

    def test_the_frontend_loads_both_states_without_errors(self):
        for name, result in (("factory", self.factory), ("all enabled", self.enabled)):
            with self.subTest(name):
                self.assertIsNone(result.load_error)
                self.assertIsNone(result.prompt_error)
                self.assertEqual(result.console, ())

    def test_the_frontend_exports_the_input_prompt_with_every_node_enabled(self):
        self.assertEqual(prompt_diff(self.doc, self.enabled.prompt), [])

    def test_the_factory_export_lacks_exactly_the_bypassed_nodes(self):
        bypassed = {str(node["id"]) for node in self.written["nodes"] if node["mode"] == 4}
        self.assertEqual(sorted(self.enabled.prompt.keys() - self.factory.prompt.keys()), sorted(bypassed))
        self.assertEqual(sorted(self.factory.prompt.keys() - self.enabled.prompt.keys()), [])

    def test_the_frontend_exports_the_factory_prompt(self):
        self.assertEqual(prompt_diff(self.compiled.authoring.factory, self.factory.prompt), [])

    def test_enabling_every_node_exports_the_full_prompt(self):
        self.assertEqual(prompt_diff(self.compiled.authoring.full, self.enabled.prompt), [])

    def test_serialize_gives_back_every_node_field(self):
        ours = {node["id"]: node for node in self.written["nodes"]}
        theirs = {node["id"]: node for node in self.factory.serialized["nodes"]}
        self.assertEqual(list(ours), list(theirs))
        for node_id, node in ours.items():
            self.assertEqual(node, theirs[node_id], (node_id, node["type"]))

    def test_the_canvas_is_laid_out_and_routed(self):
        self.assertGreater(len(self.written["groups"]), 1)
        self.assertGreater(len({tuple(node["pos"]) for node in self.written["nodes"]}), 1)
        self.assertTrue(self.written["extra"].get("reroutes"))

    def test_serialize_gives_back_the_groups(self):
        # serialize() leaves out font_size at the frontend's default, which is what layout writes
        theirs = self.factory.serialized.get("groups") or []
        self.assertEqual([without(g, "font_size") for g in self.written["groups"]], [without(g, "font_size") for g in theirs])
        self.assertEqual([g.get("font_size", FONT_SIZE) for g in theirs], [g["font_size"] for g in self.written["groups"]])

    def test_serialize_gives_back_the_reroutes(self):
        for key in ("reroutes", "linkExtensions"):
            with self.subTest(key):
                self.assertEqual(self.written["extra"].get(key), self.factory.serialized["extra"].get(key))

    def test_serialize_gives_back_the_links_in_order(self):
        self.assertEqual(self.written["links"], self.factory.serialized["links"])
        self.assertEqual(self.written["last_node_id"], self.factory.serialized["last_node_id"])
        self.assertEqual(self.written["last_link_id"], self.factory.serialized["last_link_id"])


if __name__ == "__main__":
    unittest.main()
