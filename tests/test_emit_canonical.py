import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.emit import json_bytes, write_atomic


class JsonBytesTest(unittest.TestCase):
    def test_two_space_indent_and_a_trailing_newline(self):
        self.assertEqual(json_bytes({"a": [1, 2], "b": {}}), b'{\n  "a": [\n    1,\n    2\n  ],\n  "b": {}\n}\n')

    def test_key_order_is_the_callers(self):
        self.assertEqual(json_bytes({"b": 1, "a": 2}), b'{\n  "b": 1,\n  "a": 2\n}\n')

    def test_non_ascii_is_written_as_utf8_unless_ascii_is_requested(self):
        self.assertEqual(json_bytes({"k": "你"}), '{\n  "k": "你"\n}\n'.encode("utf-8"))
        self.assertEqual(json_bytes({"k": "你\U0001f600"}, ascii=True), b'{\n  "k": "\\u4f60\\ud83d\\ude00"\n}\n')

    def test_nan_and_infinity_are_refused(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    json_bytes({"v": value})

    def test_output_parses_back_to_the_same_value_and_bytes(self):
        value = {"a": [1, 2.5, None, True, "x"], "b": {"c": []}}
        data = json_bytes(value)
        self.assertEqual(json.loads(data), value)
        self.assertEqual(json_bytes(json.loads(data)), data)

    def test_scalars_are_valid_documents(self):
        self.assertEqual(json_bytes(3), b"3\n")


class WriteAtomicTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir())

    def test_a_new_file_gets_the_bytes_and_no_temporary_remains(self):
        write_atomic(self.dir / "out.json", b"data")
        self.assertEqual((self.dir / "out.json").read_bytes(), b"data")
        self.assertEqual(self.leftovers(), ["out.json"])

    def test_a_new_file_gets_the_default_permissions(self):
        write_atomic(self.dir / "new", b"x")
        umask = os.umask(0)
        os.umask(umask)
        self.assertEqual(stat.S_IMODE((self.dir / "new").stat().st_mode), 0o666 & ~umask)

    def test_an_existing_file_is_replaced_and_keeps_its_mode(self):
        target = self.dir / "out"
        target.write_bytes(b"old")
        os.chmod(target, 0o640)
        write_atomic(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertEqual(self.leftovers(), ["out"])

    def test_the_data_and_the_directory_entry_are_flushed_to_disk(self):
        with mock.patch("os.fsync") as fsync:
            write_atomic(self.dir / "out", b"x")
        self.assertEqual(fsync.call_count, 2)  # the file, then its directory

    def test_a_failed_write_keeps_the_old_file_and_removes_the_temporary(self):
        target = self.dir / "out"
        target.write_bytes(b"old")
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_atomic(target, b"new")
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(self.leftovers(), ["out"])

    def test_a_missing_directory_raises_and_creates_nothing(self):
        with self.assertRaises(FileNotFoundError):
            write_atomic(self.dir / "missing" / "out", b"x")
        self.assertEqual(self.leftovers(), [])

    def test_accepts_str_paths_and_empty_data(self):
        write_atomic(str(self.dir / "empty"), b"")
        self.assertEqual((self.dir / "empty").read_bytes(), b"")

    def test_a_temporary_name_collision_is_retried(self):
        names = iter(["aaaaaaaaaaaa", "aaaaaaaaaaaa", "bbbbbbbbbbbb"])
        (self.dir / ".out.aaaaaaaaaaaa.tmp").write_bytes(b"squatter")
        with mock.patch("secrets.token_hex", side_effect=lambda n: next(names)):
            write_atomic(self.dir / "out", b"x")
        self.assertEqual((self.dir / "out").read_bytes(), b"x")
        self.assertEqual((self.dir / ".out.aaaaaaaaaaaa.tmp").read_bytes(), b"squatter")


if __name__ == "__main__":
    unittest.main()
