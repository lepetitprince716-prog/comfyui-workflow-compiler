import argparse
import contextlib
import copy
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tests import support

from cwc.canvas.model import Canvas
from tests.fixtures.learn.samples import canvas, node, prompt, sample, stack_inputs

spec = importlib.util.spec_from_file_location("eval_templates", support.ROOT / "tools" / "eval_templates.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def linked(first_id, second_id, *, title="", values=0.5, pos=0):
    """Two nodes joined by one link; ids, widgets and cosmetics are the arguments."""
    a = node(first_id, "Xi Stack", [{}, "a", values, []])
    b = node(second_id, "Mu Sampler", [1, "fixed", 20])
    a["pos"], b["pos"] = [pos, pos], [pos + 5, pos]
    a["title"] = title
    doc = canvas(a, b)
    doc["links"] = [[7, first_id, 0, second_id, 1, "CLIP"]]
    return doc


class StructureKeyTest(unittest.TestCase):
    def test_ids_order_values_and_cosmetics_do_not_change_the_key(self):
        base = tool.structure_key(Canvas(linked(1, 2)))
        self.assertEqual(base, tool.structure_key(Canvas(linked(40, 9, title="x", values=0.9, pos=300))))
        swapped = linked(1, 2)
        swapped["nodes"].reverse()
        self.assertEqual(base, tool.structure_key(Canvas(swapped)))

    def test_types_and_links_change_the_key(self):
        base = tool.structure_key(Canvas(linked(1, 2)))
        other_slot = linked(1, 2)
        other_slot["links"][0][4] = 2
        self.assertNotEqual(base, tool.structure_key(Canvas(other_slot)))
        no_link = linked(1, 2)
        no_link["links"] = []
        self.assertNotEqual(base, tool.structure_key(Canvas(no_link)))
        retyped = linked(1, 2)
        retyped["nodes"][1]["type"] = "Nu Loader"
        self.assertNotEqual(base, tool.structure_key(Canvas(retyped)))

    def test_direction_matters(self):
        forward = linked(1, 2)
        backward = copy.deepcopy(forward)
        backward["links"] = [[7, 2, 0, 1, 1, "CLIP"]]
        self.assertNotEqual(tool.structure_key(Canvas(forward)), tool.structure_key(Canvas(backward)))


class SplitTest(unittest.TestCase):
    def items(self, count):
        out = []
        for i in range(count):
            doc = linked(1 + i, 100 + i, values=i / 10)
            doc["nodes"].extend(node(500 + i * 10 + k, "Nu Loader", ["a.png", "image"]) for k in range(i % 5 + 1))
            out.append(("png", f"f{i}.png", sample(doc, prompt(n1=("Xi Stack", stack_inputs())))))
        return out

    def args(self, **kw):
        return argparse.Namespace(**{"holdout": 50, "seed": "s", "split": "structure", **kw})

    def test_one_structure_lands_on_one_side(self):
        items = self.items(40)
        train, test = tool._split(items, self.args())
        sides = {}
        for side, group in (("train", train), ("test", test)):
            for _, _, s in group:
                sides.setdefault(tool.structure_key(s.canvas), set()).add(side)
        self.assertEqual(len(items), len(train) + len(test))
        self.assertTrue(all(len(v) == 1 for v in sides.values()))
        self.assertEqual(len(sides), 5)

    def test_split_is_deterministic(self):
        items = self.items(40)
        first = [k for _, k, _ in tool._split(items, self.args())[1]]
        again = [k for _, k, _ in tool._split(list(reversed(items)), self.args())[1]]
        self.assertEqual(sorted(first), sorted(again))

    def test_file_split_ignores_structure(self):
        train, test = tool._split(self.items(40), self.args(split="file", holdout=50))
        self.assertTrue(train and test)

    def test_holdout_is_range_checked(self):
        for bad in (-1, 101):
            with self.assertRaises(ValueError):
                tool._split(self.items(2), self.args(holdout=bad))
        self.assertEqual(tool._split(self.items(4), self.args(holdout=0))[1], [])
        self.assertEqual(tool._split(self.items(4), self.args(holdout=100))[0], [])


class FoldsTest(unittest.TestCase):
    def items(self, count):
        return SplitTest.items(self, count)

    def args(self, **kw):
        return argparse.Namespace(**{"holdout": 20, "seed": "s", "split": "structure", "folds": 4, **kw})

    def test_every_structure_is_held_out_in_exactly_one_fold(self):
        items = self.items(40)
        folds = tool._folds(items, self.args())
        self.assertEqual(len(folds), 4)
        held = {}
        for index, (train, test) in enumerate(folds):
            self.assertEqual(len(train) + len(test), len(items))
            for _, _, s in test:
                held.setdefault(tool.structure_key(s.canvas), set()).add(index)
            trained = {tool.structure_key(s.canvas) for _, _, s in train}
            self.assertFalse(trained & {tool.structure_key(s.canvas) for _, _, s in test})
        self.assertEqual(len(held), 5)
        self.assertTrue(all(len(v) == 1 for v in held.values()))
        self.assertEqual(sum(len(test) for _, test in folds), len(items))

    def test_fold_rule_is_the_documented_hash(self):
        import hashlib

        digest = hashlib.sha256(b"seed:key").hexdigest()
        self.assertEqual(tool.fold_of("seed", "key", 5), int(digest[:8], 16) % 5)

    def test_folds_are_deterministic_and_seed_dependent(self):
        items = self.items(40)

        def names(folds):
            return [sorted(k for _, k, _ in test) for _, test in folds]

        self.assertEqual(names(tool._folds(items, self.args())), names(tool._folds(list(reversed(items)), self.args())))
        self.assertNotEqual(names(tool._folds(items, self.args())), names(tool._folds(items, self.args(seed="t"))))

    def test_folds_need_the_structure_split_and_at_least_two(self):
        for bad in (dict(split="file"), dict(folds=1), dict(folds=0), dict(templates_out="x.json")):
            with self.assertRaises(ValueError):
                tool._folds(self.items(2), self.args(**bad))

    def test_cli_rejects_bad_fold_arguments(self):
        base = ["--pairs", "x", "--nodedefs", "y"]
        for extra in (["--folds", "5"], ["--split", "structure", "--folds", "1"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                tool.main(base + extra)

    def test_pooled_counts_measure_every_node_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            total = 0
            for i in range(12):
                doc = linked(1 + i, 100 + i)
                doc["nodes"].extend(node(500 + i * 10 + k, "Nu Loader", ["a.png", "image"]) for k in range(i % 4))
                total += len(doc["nodes"])
                (root / f"w{i}_ui.json").write_text(json.dumps(doc))
                api = prompt(n1=("Xi Stack", stack_inputs()))
                (root / f"w{i}_api.json").write_text(json.dumps(api))
            object_info = support.ROOT / "tests" / "fixtures" / "learn" / "object_info.json"
            args = argparse.Namespace(
                pairs=root, pngs=None, nodedefs=str(object_info), holdout=20, seed="s", split="structure", folds=3, templates_out=None
            )
            report = tool.evaluate(args)
        self.assertEqual(report["split"]["folds"], 3)
        self.assertEqual(len(report["folds"]), 3)
        self.assertEqual(report["samples"]["test"], {"pairs": 12})
        self.assertEqual(report["length_match"]["all"]["nodes"], total)
        self.assertEqual(report["length_match"]["all"]["nodes"], sum(f["length_match"].get("all", {"nodes": 0})["nodes"] for f in report["folds"]))
        self.assertEqual(sum(sum(f["samples"]["test"].values()) for f in report["folds"]), 12)
        for row in report["mismatches"]:
            self.assertIn(row["fold"], range(3))


if __name__ == "__main__":
    unittest.main()
