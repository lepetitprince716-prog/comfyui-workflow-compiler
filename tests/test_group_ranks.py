"""``group_ranks`` against its contract on random group graphs: a group reached one way ranks
after the group it is reached from, a loop's groups keep their execution order, and the
result does not depend on the order of the input."""

import random
import unittest

from tests import support  # noqa: F401

from cwc.annotate.graph import group_ranks

GRAPHS = 3000


def random_case(seed: int):
    rng = random.Random(seed)
    nodes = [f"n{i}" for i in range(rng.randint(2, 14))]
    groups = rng.randint(1, 6)
    members: dict[str, list[str]] = {}
    for key in nodes:
        members.setdefault(f"g{rng.randrange(groups)}", []).append(key)
    pairs = [tuple(rng.sample(nodes, 2)) for _ in range(rng.randint(0, 20))]
    order = {key: i for i, key in enumerate(rng.sample(nodes, len(nodes)))}
    return rng, members, pairs, order


def group_edges(members, pairs) -> dict[str, set[str]]:
    owner = {key: g for g, keys in members.items() for key in keys}
    edges: dict[str, set[str]] = {g: set() for g in members}
    for a, b in pairs:
        if owner[a] != owner[b]:
            edges[owner[a]].add(owner[b])
    return edges


def downstream(edges: dict[str, set[str]], start: str) -> set[str]:
    seen, todo = {start}, [start]
    while todo:
        for nxt in edges[todo.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen - {start}


class Contract(unittest.TestCase):
    def test_minimal_path_through_a_loop(self) -> None:
        # g4 reaches g2 only through gx; gx and g2 form a loop whose earliest member is g2's.
        members = {"g4": ["a"], "gx": ["x"], "g2": ["b1", "b2"]}
        order = {"b1": 0, "a": 1, "x": 2, "b2": 3}
        ranks = group_ranks(members, [("b1", "x"), ("a", "x"), ("x", "b2")], order)
        self.assertEqual(ranks, {"g4": 0, "g2": 1, "gx": 2})

    def test_random_graphs_keep_the_contract(self) -> None:
        one_way = loop_order = input_order = 0
        for seed in range(GRAPHS):
            rng, members, pairs, order = random_case(seed)
            ranks = group_ranks(members, pairs, order)
            shuffled = dict(rng.sample(list(members.items()), len(members)))
            if group_ranks(shuffled, list(reversed(pairs)), order) != ranks:
                input_order += 1
            edges = group_edges(members, pairs)
            down = {g: downstream(edges, g) for g in members}
            first = {g: (min(order[key] for key in keys), g) for g, keys in members.items()}
            for a in members:
                for b in down[a]:
                    if a not in down[b]:
                        one_way += not ranks[a] < ranks[b]
                    elif b in edges[a] and first[a] < first[b]:
                        # an edge inside a loop that follows execution order sets the order
                        loop_order += not ranks[a] < ranks[b]
        self.assertEqual((one_way, loop_order, input_order), (0, 0, 0))

    def test_a_loop_starts_at_its_earliest_group(self) -> None:
        for seed in range(GRAPHS):
            _, members, pairs, order = random_case(seed)
            ranks = group_ranks(members, pairs, order)
            edges = group_edges(members, pairs)
            down = {g: downstream(edges, g) for g in members}
            for g in members:
                unit = [g] + [h for h in down[g] if g in down[h]]
                head = min(unit, key=lambda h: (min(order[key] for key in members[h]), h))
                self.assertEqual(ranks[head], min(ranks[h] for h in unit), (seed, unit, ranks))


if __name__ == "__main__":
    unittest.main()
