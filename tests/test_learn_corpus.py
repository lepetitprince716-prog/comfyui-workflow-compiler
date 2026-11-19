"""Held-out accuracy of learned templates on a real corpus, which stays outside the repository.

Runs only when all three variables are set:

    CWC_LEARN_CORPUS    folder with canvas/API file pairs (x_ui.json beside x_api.json, or x.ui.json beside x.api.json)
    CWC_LEARN_PNGS      folder with PNG files carrying workflow and prompt chunks
    CWC_LEARN_NODEDEFS  /object_info export of the machine that made them

``tools/eval_templates.py --split structure --folds 5`` puts every workflow structure in one
of five folds; for each fold it learns from the other four and measures the length of the
layout of every node in the fold, so every node is measured once. The done condition of M1 is
about the templates tier (nodes whose definition is a learned template), both sources together,
pooled over the folds; the all-node rate is asserted too. Rates are compared as raw counts.

The pooled tally is the sum of the fold tallies, so it cannot show that a fold dropped a sample
or that one sample sits in two folds. ``setUpClass`` records the folds the tool built, and the
test checks those lists and the number of samples read directly.
"""

import argparse
import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import support

CORPUS = os.environ.get("CWC_LEARN_CORPUS")
PNGS = os.environ.get("CWC_LEARN_PNGS")
NODEDEFS = os.environ.get("CWC_LEARN_NODEDEFS")
MIN_PERCENT = 99
MIN_NODES = 100  # fewer held-out nodes than this says the folder is not the corpus
FOLDS = 5


def load_eval_tool():
    spec = importlib.util.spec_from_file_location("eval_templates", support.ROOT / "tools" / "eval_templates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def at_least(row, percent=MIN_PERCENT):
    return row["match"] * 100 >= row["nodes"] * percent


@unittest.skipUnless(CORPUS and PNGS and NODEDEFS, "set CWC_LEARN_CORPUS, CWC_LEARN_PNGS and CWC_LEARN_NODEDEFS to run")
class CorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        args = argparse.Namespace(
            pairs=Path(CORPUS),
            pngs=Path(PNGS),
            nodedefs=NODEDEFS,
            holdout=20,
            seed="cwc-m1",
            split="structure",
            folds=FOLDS,
            templates_out=None,
        )
        tool = load_eval_tool()
        cls.folds = []  # (items read, [(train, test) per fold]) of each call to the tool's _folds
        build = tool._folds

        def recording(items, namespace):
            folds = build(items, namespace)
            cls.folds.append((items, folds))
            return folds

        with mock.patch.object(tool, "_folds", recording):
            cls.report = tool.evaluate(args)

    def test_held_out_templates_tier_length_match(self):
        row = self.report["length_match_by_tier"]["all/templates"]
        self.assertGreaterEqual(row["nodes"], MIN_NODES)
        self.assertTrue(at_least(row), f"{row['match']} of {row['nodes']} held-out template nodes (pooled over folds)")

    def test_held_out_length_match_of_all_nodes(self):
        row = self.report["length_match"]["all"]
        self.assertGreaterEqual(row["nodes"], MIN_NODES)
        self.assertTrue(at_least(row), f"{row['match']} of {row['nodes']} held-out nodes")

    def test_corpus_gave_samples_to_learn_from(self):
        for source in ("pairs", "png"):
            self.assertGreater(self.report["samples"]["test"].get(source, 0), 0)
        self.assertEqual(len(self.report["folds"]), FOLDS)
        for fold in self.report["folds"]:
            self.assertGreater(sum(fold["samples"]["train"].values()), 0)
            self.assertGreater(fold["learned"]["templates"], 0)

    def test_every_sample_is_held_out_in_exactly_one_fold(self):
        [(items, folds)] = self.folds
        read = self.report["split"]["test_samples"]  # len(items), counted before the folds were made
        self.assertEqual(read, len(items))
        self.assertGreater(read, 0)
        self.assertEqual(len(folds), FOLDS)
        held = [id(item) for _, test in folds for item in test]
        self.assertEqual(len(held), len(frozenset(held)), "a sample is held out in two folds")
        self.assertEqual(frozenset(held), frozenset(id(item) for item in items), "a sample is held out in no fold")
        for train, test in folds:
            self.assertFalse(frozenset(id(i) for i in train) & frozenset(id(i) for i in test), "a sample is on both sides of a fold")
            self.assertEqual(len(train) + len(test), read)
        reported = [sum(fold["samples"]["test"].values()) for fold in self.report["folds"]]
        self.assertEqual(reported, [len(test) for _, test in folds])
        self.assertEqual(sum(reported), read)


if __name__ == "__main__":
    unittest.main()
