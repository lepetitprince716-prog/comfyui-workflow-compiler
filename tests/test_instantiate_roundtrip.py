"""A real frontend loads what instantiate writes.

Needs the oracle (``CWC_ORACLE_URL``, ``CWC_ORACLE_PYTHON``, see ``tests/oracle_support.py``);
without them every test here is skipped. Each canvas is loaded in a real ComfyUI frontend,
exported with its own ``graphToPrompt`` and written back with ``serialize()``.
"""

import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.instantiate import instantiate
from cwc.prompt.model import prompt_diff
from tests import oracle_support
from tests.fixtures.instantiate import graphs


def nodes_by_id(document):
    return {node["id"]: node for node in document["nodes"]}


def without(node, *keys):
    return {key: value for key, value in node.items() if key not in keys}


class RoundtripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        oracle_support.require()
        cls.cases = graphs.cases()
        cls.written = {name: instantiate(authoring).to_json() for name, (authoring, _) in cls.cases.items()}
        cls.loaded = dict(zip(cls.written, oracle_support.roundtrip(list(cls.written.values()))))

    def test_every_canvas_loads_and_exports_without_errors(self):
        for name, result in self.loaded.items():
            with self.subTest(name):
                self.assertIsNone(result.load_error)
                self.assertIsNone(result.prompt_error)
                self.assertEqual(result.console, ())

    def test_the_frontend_exports_the_factory_prompt(self):
        for name, (authoring, _) in self.cases.items():
            with self.subTest(name):
                self.assertEqual(prompt_diff(authoring.factory, self.loaded[name].prompt), [])

    def test_serialize_writes_back_the_same_nodes(self):
        # pos is layout's; size has its own test; the frontend may list the links in another order
        for name, written in self.written.items():
            ours, theirs = nodes_by_id(written), nodes_by_id(self.loaded[name].serialized)
            with self.subTest(name):
                self.assertEqual(list(ours), list(theirs))
                for node_id, node in ours.items():
                    self.assertEqual(without(node, "pos", "size"), without(theirs[node_id], "pos", "size"), node["type"])

    def test_serialize_writes_back_the_same_links(self):
        for name, written in self.written.items():
            with self.subTest(name):
                self.assertEqual(sorted(written["links"]), sorted(self.loaded[name].serialized["links"]))
                self.assertEqual(written["last_node_id"], self.loaded[name].serialized["last_node_id"])
                self.assertEqual(written["last_link_id"], self.loaded[name].serialized["last_link_id"])

    def test_the_frontend_does_not_resize_what_instantiate_sized(self):
        changes = {}
        for name, written in self.written.items():
            theirs = nodes_by_id(self.loaded[name].serialized)
            for node_id, node in nodes_by_id(written).items():
                if node["size"] != theirs[node_id]["size"]:
                    changes[(name, node_id, node["type"])] = (node["size"], theirs[node_id]["size"])
        self.assertEqual(changes, {})

    def test_a_second_load_changes_nothing(self):
        again = oracle_support.roundtrip([result.serialized for result in self.loaded.values()])
        for name, second in zip(self.loaded, again):
            first = nodes_by_id(self.loaded[name].serialized)
            with self.subTest(name):
                self.assertIsNone(second.load_error)
                self.assertEqual(second.console, ())
                for node_id, node in nodes_by_id(second.serialized).items():
                    self.assertEqual(without(node, "pos"), without(first[node_id], "pos"), first[node_id]["type"])
                self.assertEqual(sorted(second.serialized["links"]), sorted(self.loaded[name].serialized["links"]))


if __name__ == "__main__":
    unittest.main()
