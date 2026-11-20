"""tools/build_core_data.py writes through a fresh temporary file and reads its inputs within a limit.

The tool is run by a maintainer on a checkout, with ``--out`` usually ``src/cwc/data/``. A
checkout can hold a symbolic link, and the tool wrote ``<name>.tmp`` in the output folder with
a plain ``write_bytes``, which follows a link of that name to a file elsewhere. Its inputs
(a raw ``/object_info`` export, a truth snapshot) are plain JSON or gzip, and a gzip file was
inflated without any limit.
"""

import contextlib
import gzip
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from tests.test_build_core_data import object_info, truth

_SPEC = importlib.util.spec_from_file_location("build_core_data", support.ROOT / "tools" / "build_core_data.py")
build_core_data = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_core_data)

VICTIM = b"content of a file the tool has no business with\n"


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = build_core_data.main([str(a) for a in args])
    return code, err.getvalue()


class SymlinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "oi.json").write_text(json.dumps(object_info()), encoding="utf-8")
        (self.dir / "truth.json").write_text(json.dumps(truth()), encoding="utf-8")
        self.victim = self.dir / "victim.txt"
        self.victim.write_bytes(VICTIM)
        self.out = self.dir / "out"
        self.out.mkdir()

    def build(self):
        return run([self.dir / "oi.json", self.dir / "truth.json", "--out", self.out])

    def test_a_link_named_like_the_old_temporary_file_is_not_followed(self):
        for name in ("core_nodedefs.json.tmp", "core_truth.json.tmp"):
            os.symlink(self.victim, self.out / name)
        code, _ = self.build()
        self.assertEqual(code, 0)
        self.assertEqual(self.victim.read_bytes(), VICTIM)
        for name in ("core_nodedefs.json", "core_truth.json"):
            self.assertTrue((self.out / name).is_file() and not (self.out / name).is_symlink())
            json.loads((self.out / name).read_bytes())

    def test_a_link_at_the_target_is_replaced_not_written_through(self):
        os.symlink(self.victim, self.out / "core_nodedefs.json")
        code, _ = self.build()
        self.assertEqual(code, 0)
        self.assertEqual(self.victim.read_bytes(), VICTIM)
        self.assertFalse((self.out / "core_nodedefs.json").is_symlink())

    def test_the_files_get_the_mode_an_ordinary_new_file_gets(self):
        plain = self.dir / "plain.txt"
        plain.write_bytes(b"x")
        code, _ = self.build()
        self.assertEqual(code, 0)
        for name in ("core_nodedefs.json", "core_truth.json"):
            self.assertEqual((self.out / name).stat().st_mode & 0o777, plain.stat().st_mode & 0o777)

    def test_no_temporary_file_is_left_behind(self):
        code, _ = self.build()
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["core_nodedefs.json", "core_truth.json"])

    def test_a_failed_write_leaves_no_temporary_file(self):
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.build()
        self.assertEqual(list(self.out.iterdir()), [])


class InputLimitTest(unittest.TestCase):
    def run_with(self, oi_bytes, limit=1000):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "oi.json").write_bytes(oi_bytes)
            (src / "truth.json").write_text(json.dumps(truth()), encoding="utf-8")
            with mock.patch.object(build_core_data, "MAX_INPUT_BYTES", limit):
                return run([src / "oi.json", src / "truth.json", "--out", src / "out"])

    def test_a_gzip_file_that_inflates_past_the_limit_is_refused(self):
        code, err = self.run_with(gzip.compress(b" " * 100_000))
        self.assertEqual(code, 1)
        self.assertIn("limit", err)

    def test_a_plain_file_past_the_limit_is_refused(self):
        code, err = self.run_with(b" " * 5000)
        self.assertEqual(code, 1)
        self.assertIn("limit", err)

    def test_inputs_within_the_limit_are_read_plain_and_packed(self):
        document = json.dumps(object_info()).encode()
        for data in (document, gzip.compress(document)):
            with self.subTest(packed=data[:2] == b"\x1f\x8b"):
                self.assertEqual(self.run_with(data, limit=len(document))[0], 0)

    def test_a_truncated_gzip_file_is_an_error_not_a_traceback(self):
        packed = gzip.compress(json.dumps(object_info()).encode())
        code, err = self.run_with(packed[:-10], limit=10_000_000)
        self.assertEqual(code, 1)
        self.assertIn("build_core_data:", err)


if __name__ == "__main__":
    unittest.main()
