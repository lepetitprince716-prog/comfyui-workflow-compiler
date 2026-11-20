"""Comma-separated slot types are compared in time linear in their length.

The callers that can pass long lists: ``bypass_slot_index`` and the link check of ``compile``
(``normalize/inputs.py``) take both types from node definitions, and ``--nodedefs`` is
user-supplied.
"""

import io
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.cli import main
from cwc.prompt import bypass
from cwc.prompt.bypass import is_valid_connection
from cwc.report import ExitCode

SLOW = 2.0  # the linear comparison needs milliseconds for these lists, the pairwise one many seconds


def reference(type_a, type_b):
    """``is_valid_connection`` as it was before the lists were compared as sets: every pair."""
    if bypass._js_loose_equal(type_a, "") or type_a == bypass.WILDCARD:
        type_a = 0
    if bypass._js_loose_equal(type_b, "") or type_b == bypass.WILDCARD:
        type_b = 0
    if (
        not bypass._js_truthy(type_a)
        or not bypass._js_truthy(type_b)
        or bypass._js_loose_equal(type_a, type_b)
        or (bypass._js_loose_equal(type_a, bypass._JS_EVENT) and bypass._js_loose_equal(type_b, bypass._JS_EVENT))
    ):
        return True
    a = bypass._js_string(type_a).lower()
    b = bypass._js_string(type_b).lower()
    if "," not in a and "," not in b:
        return a == b
    return any(reference(x, y) for x in a.split(",") for y in b.split(","))


class LinearTest(unittest.TestCase):
    def test_two_long_disjoint_lists(self):
        a = ",".join(f"T{i}" for i in range(2500))
        b = ",".join(f"U{i}" for i in range(2500))
        start = time.monotonic()
        self.assertFalse(is_valid_connection(a, b))
        self.assertLess(time.monotonic() - start, SLOW)

    def test_compile_with_long_type_lists_in_the_definitions(self):
        names = {"src": ",".join(f"T{i}" for i in range(2500)), "dst": ",".join(f"U{i}" for i in range(2500))}
        defs = {
            "SrcNode": {"input": {"required": {}}, "output": [names["src"]], "output_name": ["out"], "output_is_list": [False]},
            "DstNode": {"input": {"required": {"inp": [names["dst"]]}}, "output": [], "output_name": [], "output_is_list": []},
        }
        graph = {"1": {"class_type": "SrcNode", "inputs": {}}, "2": {"class_type": "DstNode", "inputs": {"inp": ["1", 0]}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "defs.json").write_text(json.dumps(defs), encoding="utf-8")
            (root / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
            start = time.monotonic()
            code = main(["compile", str(root / "graph.json"), "-o", str(root / "out"), "--nodedefs", str(root / "defs.json")], io.StringIO(), io.StringIO())
            elapsed = time.monotonic() - start
        self.assertEqual(code, ExitCode.INPUT_ERROR)  # E4: the two types never match
        self.assertLess(elapsed, SLOW)


class SameAnswerTest(unittest.TestCase):
    def test_generated_types_give_the_answers_of_the_pairwise_comparison(self):
        rng = random.Random(20261119)
        # empty and "*" members are wildcards; "-1", " -1" and "-1.0" all equal the event type as numbers
        pieces = ["", "*", "a", "A", "b", "IMAGE", "image", "-1", " -1", "-1.0", "0", "e,"]
        scalars = [None, 0, -1, 1, "", "*", "a", "-1", ["a", "b"], ["-1", "-1.0"], True]

        def side():
            if rng.random() < 0.1:
                return rng.choice(scalars)
            return ",".join(rng.choice(pieces) for _ in range(rng.randrange(1, 6)))

        for _ in range(30000):
            a, b = side(), side()
            self.assertEqual(is_valid_connection(a, b), reference(a, b), (a, b))

    def test_known_answers(self):
        for a, b, expected in (
            ("INT,FLOAT", "float,string", True),
            ("INT,FLOAT", "STRING,MODEL", False),
            ("INT,", "STRING", True),
            ("-1,x", "-1.0,y", True),
            ("-1,x", "y", False),
            ("a,b", "c,d", False),
        ):
            with self.subTest(a=a, b=b):
                self.assertEqual(is_valid_connection(a, b), expected)


if __name__ == "__main__":
    unittest.main()
