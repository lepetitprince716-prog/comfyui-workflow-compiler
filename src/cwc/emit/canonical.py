"""Canonical JSON bytes and atomic file writes for everything cwc outputs."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


def json_bytes(obj: Any, *, ascii: bool = False) -> bytes:
    """Two-space indented UTF-8 JSON with a trailing newline.

    Key order is the order the caller built, so the same input gives the same bytes.
    ``ascii=True`` escapes every non-ASCII character (for PNG text chunks). NaN and
    infinities raise ``ValueError``: ``JSON.parse`` cannot read them.
    """
    text = json.dumps(obj, indent=2, ensure_ascii=ascii, allow_nan=False)
    return (text + "\n").encode("utf-8")


def write_atomic(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace ``path`` with ``data`` so readers see the old file or the new one, never a part.

    The bytes go to a temporary file in the same directory, are flushed to disk and then
    renamed over the target. A new file gets the usual permissions (0666 less the umask);
    an existing file keeps its mode.
    """
    target = Path(path)
    directory = target.parent
    try:
        mode = target.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = None
    while True:
        temp = directory / f".{target.name}.{secrets.token_hex(6)}.tmp"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            break
        except FileExistsError:
            continue
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _fsync_directory(directory)


def _fsync_directory(directory: Path) -> None:
    """Make the rename durable where the platform allows opening a directory."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
