"""Embedding workflow text into a PNG.

Old metadata goes first: every ``tEXt``, ``iTXt`` and ``zTXt`` chunk whose keyword is
``workflow``, ``prompt``, ``parameters`` or ``templates`` (the keys the frontend acts on,
``src/scripts/app.ts``) or one of the keys being written, and every ``comf`` chunk. A
cover image made by ComfyUI or another tool would otherwise carry a stale prompt, or
private data, next to the new one. New text goes in plain ``tEXt`` chunks right before
``IEND``, as ComfyUI's own ``SaveImage`` writes them.
"""

from __future__ import annotations

import struct
import zlib

from cwc.ingest.png import COMF, IEND, SIGNATURE, TEXT, parse_png_chunks, text_keyword

STRIPPED_KEYWORDS = frozenset({"workflow", "prompt", "parameters", "templates"})


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """One serialised chunk: length, type, data, CRC-32 of type and data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def embed_png(png: bytes, texts: dict[str, str]) -> bytes:
    """``png`` with old workflow metadata removed and ``texts`` added as ``tEXt`` chunks.

    Values must be ASCII (serialise JSON with ``ensure_ascii``); keywords must be 1 to 79
    printable Latin-1 characters without leading, trailing or double spaces. A bad PNG
    raises ``CwcError`` I1. Embedding the same texts twice gives the same bytes.
    """
    new_chunks = [png_chunk(TEXT, _text_payload(keyword, value)) for keyword, value in texts.items()]
    stripped = STRIPPED_KEYWORDS | set(texts)
    out = [SIGNATURE]
    for chunk in parse_png_chunks(png):
        if chunk.type == COMF or text_keyword(chunk) in stripped:
            continue
        if chunk.type == IEND:
            out.extend(new_chunks)
        out.append(png_chunk(chunk.type, chunk.data))
    return b"".join(out)


def _text_payload(keyword: str, value: str) -> bytes:
    try:
        key = keyword.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(f"PNG keyword {keyword!r} is not Latin-1") from None
    printable = all(32 <= b <= 126 or 161 <= b <= 255 for b in key)
    if not (1 <= len(key) <= 79 and printable and key == key.strip(b" ") and b"  " not in key):
        raise ValueError(f"invalid PNG keyword {keyword!r}")
    if not value.isascii() or "\0" in value:
        raise ValueError(f"PNG text for {keyword!r} must be ASCII without NUL; serialise it with ensure_ascii")
    return key + b"\0" + value.encode("ascii")
