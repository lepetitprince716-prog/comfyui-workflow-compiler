"""``read_file`` stops at its limit however large the file turns out to be.

The callers that can pass such a path are the file arguments of ``compile``, ``render`` and
``learn``. A FIFO or a device reports ``st_size == 0``, and a regular file can grow after
the size check, so the check alone does not bound the read.
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.ingest import limits
from cwc.report import CwcError


def i3(caught: CwcError) -> bool:
    return [f.code.value for f in caught.findings] == ["I3"]


class BoundedReadTest(unittest.TestCase):
    def through_a_pipe(self, payload: bytes):
        """``read_file`` on a named pipe that delivers ``payload`` (``st_size`` is 0), limit 1000."""
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "stream.fifo"
            os.mkfifo(fifo)

            def feed():
                with open(fifo, "wb") as handle:
                    try:
                        handle.write(payload)
                    except BrokenPipeError:  # the reader gave up at the limit
                        pass

            writer = threading.Thread(target=feed)
            writer.start()
            try:
                with mock.patch.object(limits, "MAX_PNG_BYTES", 1000):
                    return limits.read_file(fifo, "authoring file")
            finally:
                writer.join(10)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_a_pipe_that_delivers_more_than_the_limit_is_i3(self):
        for size in (1001, 5000):
            with self.subTest(size=size), self.assertRaises(CwcError) as caught:
                self.through_a_pipe(b"x" * size)
            self.assertTrue(i3(caught.exception))
            self.assertIn("past the limit of 1000 bytes", caught.exception.findings[0].message)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_a_pipe_that_delivers_exactly_the_limit_is_read(self):
        self.assertEqual(self.through_a_pipe(b"x" * 1000), b"x" * 1000)

    @unittest.skipUnless(os.path.exists("/dev/zero"), "needs /dev/zero")
    def test_an_endless_device_is_i3(self):
        with mock.patch.object(limits, "MAX_PNG_BYTES", 1000):
            with self.assertRaises(CwcError) as caught:
                limits.read_file("/dev/zero", "authoring file")
        self.assertTrue(i3(caught.exception))

    def test_a_file_at_the_limit_is_read_and_one_byte_more_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_bytes(b"y" * 1000)
            with mock.patch.object(limits, "MAX_PNG_BYTES", 1000):
                self.assertEqual(limits.read_file(path, "authoring file"), b"y" * 1000)
            path.write_bytes(b"y" * 1001)
            with mock.patch.object(limits, "MAX_PNG_BYTES", 1000):
                with self.assertRaises(CwcError) as caught:
                    limits.read_file(path, "authoring file")
        self.assertTrue(i3(caught.exception))

    def test_a_file_that_grows_after_the_size_check_is_i3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_bytes(b"y" * 10)
            real_stat = Path.stat

            def stat_then_grow(self, *args, **kwargs):
                result = real_stat(self, *args, **kwargs)
                if self == path:
                    path.write_bytes(b"y" * 5000)
                return result

            with mock.patch.object(limits, "MAX_PNG_BYTES", 1000), mock.patch.object(Path, "stat", stat_then_grow):
                with self.assertRaises(CwcError) as caught:
                    limits.read_file(path, "authoring file")
        self.assertTrue(i3(caught.exception))

    def test_a_missing_file_is_still_i1(self):
        with self.assertRaises(CwcError) as caught:
            limits.read_file("/nonexistent/cwc-input.json", "authoring file")
        self.assertEqual([f.code.value for f in caught.exception.findings], ["I1"])


if __name__ == "__main__":
    unittest.main()
