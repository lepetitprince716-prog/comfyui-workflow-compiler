"""The compiler's strict prompts equal what a real frontend exported for the same graphs.

``tests/fixtures/instantiate/frontend_exports.json`` holds, per graph of
``tests/fixtures/instantiate/graphs.py``, the ``graphToPrompt`` output of the frontend that
loaded the instantiated canvas in its factory state and with every node enabled
(``tools/oracle/record_frontend_exports.py``). ``authoring.factory`` and ``authoring.full``
come from the bypass resolver in ``cwc.prompt.bypass``, and the compile check Q2 compares the
canvas with the same resolver; only this fixture holds the resolver to the frontend offline.
"""

import json
import unittest

from tests import support

from cwc.prompt.model import prompt_diff
from tests.fixtures.instantiate import graphs

RECORDED = json.loads((support.FIXTURES / "instantiate" / "frontend_exports.json").read_text(encoding="utf-8"))
CASES = graphs.cases()


class FrontendExportsTest(unittest.TestCase):
    def test_every_graph_is_recorded(self):
        self.assertEqual(sorted(RECORDED["cases"]), sorted(CASES))
        self.assertTrue(RECORDED["frontend"])

    def test_the_factory_prompt_is_the_frontend_export(self):
        for name, (authoring, _) in CASES.items():
            with self.subTest(name):
                self.assertEqual(prompt_diff(RECORDED["cases"][name]["factory"], authoring.factory), [])

    def test_the_full_prompt_is_the_frontend_export_with_every_node_enabled(self):
        for name, (authoring, _) in CASES.items():
            with self.subTest(name):
                self.assertEqual(prompt_diff(RECORDED["cases"][name]["all_enabled"], authoring.full), [])

    def test_the_recording_covers_bypassed_nodes(self):
        # a fixture with no bypassed node would not hold the resolver to anything
        differing = [n for n, c in RECORDED["cases"].items() if c["factory"] != c["all_enabled"]]
        self.assertIn("optional_chain", differing)
        self.assertIn("autogrow", differing)
        self.assertIn("optional_controlnet", differing)

    def test_a_bypassed_controlnet_hands_each_conditioning_to_its_own_output(self):
        # the frontend's own export: output 1 of the bypassed node takes input 1, not the first CONDITIONING input
        factory = RECORDED["cases"]["optional_controlnet"]["factory"]
        sampler = next(node for node in factory.values() if node["class_type"] == "KSampler")
        texts = {name: factory[sampler["inputs"][name][0]]["inputs"]["text"] for name in ("positive", "negative")}
        self.assertEqual(texts, {"positive": "a cat", "negative": "blurry, lowres"})


if __name__ == "__main__":
    unittest.main()
