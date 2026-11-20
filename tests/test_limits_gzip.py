"""A gzip file of very many members is inflated in time linear in its size.

The caller that can pass one is ``--nodedefs`` with a gzip file: ``read_source`` hands the
bytes to ``_parse_json``, which inflates every member.
"""

import gzip
import io
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.cli import main
from cwc.nodedefs import registry
from cwc.report import ExitCode

SLOW = 2.0  # milliseconds with the linear scan, many seconds with a copy of the rest of the file per member


class ManyMembersTest(unittest.TestCase):
    def test_hundreds_of_thousands_of_empty_members(self):
        blob = gzip.compress(b"") * 200000  # 4 MB
        start = time.monotonic()
        self.assertEqual(registry._gunzip(blob, "test"), b"")
        self.assertLess(time.monotonic() - start, SLOW)

    def test_the_cli_reports_an_empty_document_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "many.json.gz"
            path.write_bytes(gzip.compress(b"") * 200000)
            start = time.monotonic()
            err = io.StringIO()
            code = main(["node", "KSampler", "--nodedefs", str(path)], io.StringIO(), err)
            elapsed = time.monotonic() - start
        self.assertEqual(code, ExitCode.NODEDEFS_MISSING)
        self.assertIn("not valid JSON", err.getvalue())
        self.assertLess(elapsed, SLOW)

    def test_padding_between_many_members_costs_nothing_either(self):
        blob = (gzip.compress(b"") + b"\0" * 1000) * 20000
        start = time.monotonic()
        self.assertEqual(registry._gunzip(blob, "test"), b"")
        self.assertLess(time.monotonic() - start, SLOW)


class SameBytesTest(unittest.TestCase):
    def test_generated_member_sequences_inflate_to_their_payloads(self):
        rng = random.Random(20261119)
        for _ in range(400):
            payloads = [
                bytes(rng.choices(b"abc{}\n", k=rng.choice([0, 1, 5, 700, 5000, 70000]))) for _ in range(rng.randrange(1, 6))
            ]
            blob = b"".join(
                gzip.compress(p, compresslevel=rng.choice([1, 9])) + b"\0" * rng.choice([0, 0, 1, 7, 600]) for p in payloads
            )
            self.assertEqual(registry._gunzip(blob, "test"), b"".join(payloads))

    def test_a_document_in_many_small_members_still_parses(self):
        doc = json.dumps({"T": {"input": {"required": {}}, "output": []}}).encode()
        blob = b"".join(gzip.compress(doc[i : i + 3]) for i in range(0, len(doc), 3))
        self.assertEqual(json.loads(registry._gunzip(blob, "test")), json.loads(doc))


if __name__ == "__main__":
    unittest.main()
