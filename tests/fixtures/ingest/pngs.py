"""Test-only PNG builders. They write chunks with their own CRC code so that tests of
``cwc.emit`` and ``cwc.ingest`` do not rely on the code under test."""

from __future__ import annotations

import json
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, data: bytes, *, crc: int | None = None) -> bytes:
    checksum = zlib.crc32(kind + data) if crc is None else crc
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def text_chunk(keyword: str, text: str) -> bytes:
    return chunk(b"tEXt", keyword.encode("latin-1") + b"\0" + text.encode("utf-8"))


def itxt_chunk(keyword: str, text: str, *, compressed: bool = False, method: int = 0) -> bytes:
    body = text.encode("utf-8")
    if compressed:
        body = zlib.compress(body)
    head = keyword.encode("latin-1") + b"\0" + bytes([int(compressed), method]) + b"en\0" + b"\0"
    return chunk(b"iTXt", head + body)


def ztxt_chunk(keyword: str, text: str, *, method: int = 0) -> bytes:
    return chunk(b"zTXt", keyword.encode("latin-1") + b"\0" + bytes([method]) + zlib.compress(text.encode("utf-8")))


IHDR = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
IDAT = chunk(b"IDAT", zlib.compress(b"\0\x7f"))
IEND = chunk(b"IEND", b"")


def make_png(*extra: bytes, before_idat: tuple[bytes, ...] = (), after_iend: bytes = b"") -> bytes:
    """A valid 1 x 1 PNG; ``extra`` chunks sit between IDAT and IEND."""
    return SIGNATURE + IHDR + b"".join(before_idat) + IDAT + b"".join(extra) + IEND + after_iend


def canvas_doc() -> dict:
    """The smallest canvas the ingest checks accept."""
    return {
        "last_node_id": 1,
        "last_link_id": 0,
        "nodes": [{"id": 1, "type": "T", "pos": [0, 0], "size": [10, 10]}],
        "links": [],
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }


def canvas_png(doc: dict | None = None, *extra: bytes) -> bytes:
    text = json.dumps(canvas_doc() if doc is None else doc, ensure_ascii=True)
    return make_png(text_chunk("workflow", text), *extra)
