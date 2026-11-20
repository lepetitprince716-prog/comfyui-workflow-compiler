"""Compressed PNG text chunks share one inflate budget, so many small bombs cannot add up.

The caller that can pass such a file is every PNG input: ``cwc render``, ``cwc learn`` and any
canvas read through ``load_canvas`` call ``read_png_text``, which reads every ``iTXt`` chunk,
whatever its keyword, before it picks the ``workflow`` or ``prompt`` text.
"""

import unittest
import zlib
from unittest import mock

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.ingest import png as ingest_png
from cwc.ingest import read_png_text
from cwc.report import CwcError
from tests.fixtures.ingest import pngs


def bombs(count: int, size: int) -> bytes:
    return pngs.make_png(*(pngs.itxt_chunk(f"junk{i}", "a" * size, compressed=True) for i in range(count)))


class InflateBudgetTest(unittest.TestCase):
    def test_chunks_that_each_fit_but_not_together_are_i3(self):
        png = bombs(6, 600)  # each inflates to 600 bytes, 3600 in all
        with mock.patch.object(ingest_png, "MAX_JSON_BYTES", 1000):
            with self.assertRaises(CwcError) as caught:
                read_png_text(png)
        self.assertEqual([f.code.value for f in caught.exception.findings], ["I3"])
        self.assertIn("in all", caught.exception.findings[0].message)

    def test_many_chunks_within_the_budget_are_all_read(self):
        png = bombs(6, 100)
        with mock.patch.object(ingest_png, "MAX_JSON_BYTES", 1000):
            texts = read_png_text(png)
        self.assertEqual(sorted(texts), [f"junk{i}" for i in range(6)])
        self.assertEqual({len(v) for v in texts.values()}, {100})

    def test_a_chunk_that_is_skipped_does_not_use_the_budget(self):
        png = pngs.make_png(pngs.ztxt_chunk("z", "a" * 5000), pngs.itxt_chunk("k", "a" * 900, compressed=True))
        with mock.patch.object(ingest_png, "MAX_JSON_BYTES", 1000):
            self.assertEqual(read_png_text(png), {"k": "a" * 900})

    def test_unreadable_streams_are_charged_for_what_they_inflated(self):
        head = b"\0\x01\0en\0\0"  # compressed iTXt flag, method 0, empty language and translated keyword
        junk = [pngs.chunk(b"iTXt", f"j{i}".encode() + head + zlib.compress(b"a" * 600) + b"tail") for i in range(6)]
        with mock.patch.object(ingest_png, "MAX_JSON_BYTES", 1000):
            with self.assertRaises(CwcError) as caught:
                read_png_text(pngs.make_png(*junk))
        self.assertEqual([f.code.value for f in caught.exception.findings], ["I3"])

    def test_the_budget_is_per_file(self):
        png = bombs(1, 900)
        with mock.patch.object(ingest_png, "MAX_JSON_BYTES", 1000):
            for _ in range(3):
                self.assertEqual(len(read_png_text(png)["junk0"]), 900)


if __name__ == "__main__":
    unittest.main()
