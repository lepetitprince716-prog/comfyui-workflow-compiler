"""``order``: the execution order the frontend recomputes on load (Kahn, first in first out)."""

import random
import unittest
from collections import deque

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.instantiate import instantiate
from cwc.instantiate.order import execution_order
from tests.fixtures.instantiate import graphs


def kahn(raw):
    """Reference: Kahn over a written canvas, starting from the nodes without a linked input
    in node-list order, following each output's link list in order."""
    nodes = {n["id"]: n for n in raw["nodes"]}
    target = {link[0]: link[3] for link in raw["links"]}
    pending = {i: sum(1 for slot in n["inputs"] if slot["link"] is not None) for i, n in nodes.items()}
    queue = deque(i for i in nodes if pending[i] == 0)
    order = []
    while queue:
        i = queue.popleft()
        order.append(i)
        for out in nodes[i]["outputs"]:
            for link in out["links"] or []:
                pending[target[link]] -= 1
                if pending[target[link]] == 0:
                    queue.append(target[link])
    return {i: index for index, i in enumerate(order)}


def relabel(doc, seed):
    """The same graph under a shuffled assignment of author ids (ids follow sorted keys)."""
    keys = sorted(key for key in doc if not key.startswith("_"))
    shuffled = keys[:]
    random.Random(seed).shuffle(shuffled)
    new = {old: f"k{index:02d}" for index, old in enumerate(shuffled)}

    def fix(value):
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in new:
            return [new[value[0]], value[1]]
        return value

    out = {key: value for key, value in doc.items() if key.startswith("_")}
    for key in keys:
        entry = doc[key]
        out[new[key]] = {**entry, "inputs": {name: fix(value) for name, value in entry["inputs"].items()}}
    return out


class ExecutionOrderTest(unittest.TestCase):
    def test_queue_order_follows_the_node_list_not_depth(self):
        # starts: 1 and 3; 1 releases 2, 3 releases 4, so the queue goes 1, 3, 2, 4
        order = execution_order(["1", "2", "3", "4"], [("1", "2"), ("3", "4")])
        self.assertEqual(order, {"1": 0, "3": 1, "2": 2, "4": 3})

    def test_a_node_waits_for_every_input(self):
        order = execution_order(["1", "2", "3", "4"], [("1", "3"), ("2", "3"), ("3", "4")])
        self.assertEqual(order, {"1": 0, "2": 1, "3": 2, "4": 3})

    def test_targets_join_in_the_order_the_links_are_listed(self):
        order = execution_order(["1", "2", "3"], [("1", "3"), ("1", "2")])
        self.assertEqual(order, {"1": 0, "3": 1, "2": 2})

    def test_nodes_in_a_cycle_follow_in_list_order(self):
        order = execution_order(["1", "2", "3"], [("2", "3"), ("3", "2")])
        self.assertEqual(order, {"1": 0, "2": 1, "3": 2})


class InstantiateOrderTest(unittest.TestCase):
    def test_hand_computed_order_of_the_optional_chain(self):
        authoring = graphs.build(graphs.optional_chain())
        ids = authoring.ids
        order = {node.key: node.raw["order"] for node in instantiate(authoring).nodes}
        by_author = {author: order[numeric] for author, numeric in ids.items()}
        # the nodes without a linked input, in list order (ids follow the sorted author keys),
        # come first; each node then joins the queue when its last input has been taken
        self.assertEqual(by_author["ckpt"], 0)
        self.assertEqual(by_author["latent"], 1)
        self.assertEqual(by_author["lora_a"], 2)
        self.assertEqual(by_author["lora_b"], 3)
        self.assertLess(by_author["sampler"], by_author["decode"])
        self.assertLess(by_author["decode"], by_author["upscale"])
        self.assertLess(by_author["upscale"], by_author["save"])

    def test_every_graph_matches_the_reference(self):
        for name, (authoring, _) in graphs.cases().items():
            canvas = instantiate(authoring)
            with self.subTest(name):
                expected = kahn(canvas.raw)
                self.assertEqual({n["id"]: n["order"] for n in canvas.raw["nodes"]}, expected)

    def test_order_is_a_permutation_with_inputs_before_targets(self):
        for name, (authoring, _) in graphs.cases().items():
            canvas = instantiate(authoring)
            with self.subTest(name):
                order = {n["id"]: n["order"] for n in canvas.raw["nodes"]}
                self.assertEqual(sorted(order.values()), list(range(len(order))))
                for _, origin, _, target, _, _ in canvas.raw["links"]:
                    self.assertLess(order[origin], order[target])

    def test_shuffled_ids_still_match_the_reference(self):
        for seed in range(6):
            for build in (graphs.optional_chain, graphs.autogrow, graphs.dynamic_match):
                doc = relabel(build(), seed)
                with self.subTest(build=build.__name__, seed=seed):
                    canvas = instantiate(graphs.build(doc))
                    self.assertEqual({n["id"]: n["order"] for n in canvas.raw["nodes"]}, kahn(canvas.raw))


if __name__ == "__main__":
    unittest.main()
