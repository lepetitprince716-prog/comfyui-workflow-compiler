"""Comment stripping of dynamic prompts runs in linear time and strips what the old regex stripped.

The caller that can pass hostile text is ``canvas_to_prompt``: a canvas file (``cwc render``
and the canvas side of ``cwc learn``) puts any string into a widget whose input has
``dynamicPrompts``, and it reaches ``process``.
"""

import json
import random
import re
import time
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.ingest import load_canvas
from cwc.nodedefs.registry import NodeDefs
from cwc.prompt import dynamic
from cwc.prompt.dynamic import process
from cwc.prompt.from_canvas import canvas_to_prompt

# The comment pattern as it was before the linear scan: the reference for generated input.
REFERENCE = re.compile(r"/\*[\s\S]*?\*/|//[^\n\r  ]*")

# A generous bound: the linear scan needs milliseconds for these inputs, the old regex 20 s or more.
SLOW = 3.0


def timed(fn, *args):
    start = time.monotonic()
    result = fn(*args)
    return time.monotonic() - start, result


class LinearTimeTest(unittest.TestCase):
    def test_unclosed_block_comments_do_not_backtrack(self):
        for text in ("/*a" * 200000, "/*a b" * 24000, "/*a\n" * 30000):
            with self.subTest(text=text[:6], size=len(text)):
                elapsed, result = timed(process, text)
                self.assertLess(elapsed, SLOW)
                self.assertEqual(result.text, text)  # no comment ends, so nothing is removed

    def test_many_closed_and_line_comments_stay_linear(self):
        for text in ("/*/" * 40000, "/*a*/b" * 40000, "//a\nb" * 40000, "//" * 60000, "/*a" * 20000 + "*/" + "/*a" * 20000):
            with self.subTest(text=text[:6]):
                elapsed, _ = timed(process, text)
                self.assertLess(elapsed, SLOW)

    def test_the_canvas_path_is_linear_too(self):
        defs = NodeDefs.load()
        doc = {
            "version": 0.4,
            "last_node_id": 1,
            "last_link_id": 0,
            "nodes": [
                {
                    "id": 1,
                    "type": "CLIPTextEncode",
                    "pos": [0, 0],
                    "size": [400, 200],
                    "mode": 0,
                    "inputs": [
                        {"name": "clip", "type": "CLIP", "link": None},
                        {"name": "text", "type": "STRING", "widget": {"name": "text"}, "link": None},
                    ],
                    "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": []}],
                    "widgets_values": ["/*a" * 40000],
                }
            ],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {},
        }
        elapsed, _ = timed(canvas_to_prompt, load_canvas(json.dumps(doc).encode()), defs)
        self.assertLess(elapsed, SLOW)


class SameResultTest(unittest.TestCase):
    def test_generated_text_loses_the_same_comments_as_the_old_pattern(self):
        rng = random.Random(20261119)
        alphabet = ["/", "/", "*", "*", "a", "\n", "\r", " ", " ", " ", "{", "}", "|", "\\"]
        for _ in range(20000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 24)))
            self.assertEqual(dynamic._strip_comments(text), REFERENCE.sub("", text), repr(text))

    def test_known_edges(self):
        for text, expected in (
            ("a/*b*/c", "ac"),
            ("a//b\nc", "a\nc"),
            ("/*/", "/*/"),
            ("/**/x", "x"),
            ("//*x\ny", "\ny"),
            ("/*a//b*/c", "c"),
            ("a/*b", "a/*b"),
            ("a/*b\n//c\nd", "a/*b\n\nd"),
            ("/", "/"),
            ("x//", "x"),
        ):
            with self.subTest(text=text):
                self.assertEqual(dynamic._strip_comments(text), expected)
                self.assertEqual(REFERENCE.sub("", text), expected)


if __name__ == "__main__":
    unittest.main()
