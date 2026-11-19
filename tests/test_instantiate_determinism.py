"""The same authoring gives the same bytes."""

import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.emit.canonical import json_bytes
from cwc.instantiate import instantiate
from tests.fixtures.instantiate import graphs


class DeterminismTest(unittest.TestCase):
    def test_two_runs_give_identical_bytes(self):
        for name, (authoring, _) in graphs.cases().items():
            with self.subTest(name):
                first = json_bytes(instantiate(authoring).to_json())
                second = json_bytes(instantiate(authoring).to_json())
                self.assertEqual(first, second)

    def test_a_freshly_normalized_authoring_gives_the_same_bytes(self):
        for name, (authoring, _) in graphs.cases().items():
            if name == "txt2img":
                continue
            fresh = graphs.build(getattr(graphs, name)())
            with self.subTest(name):
                self.assertEqual(json_bytes(instantiate(authoring).to_json()), json_bytes(instantiate(fresh).to_json()))

    def test_instantiate_does_not_change_its_input(self):
        for name, (authoring, _) in graphs.cases().items():
            before = (repr(authoring.full), repr(authoring.factory), repr(authoring.widget_values))
            instantiate(authoring)
            with self.subTest(name):
                self.assertEqual(before, (repr(authoring.full), repr(authoring.factory), repr(authoring.widget_values)))

    def test_positions_are_zero_and_the_output_is_json_clean(self):
        for name, (authoring, _) in graphs.cases().items():
            canvas = instantiate(authoring)
            with self.subTest(name):
                self.assertTrue(all(node.pos == (0, 0) for node in canvas.nodes))
                json_bytes(canvas.to_json())  # raises on NaN or a value JSON cannot hold


if __name__ == "__main__":
    unittest.main()
