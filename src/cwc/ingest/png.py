"""Reading PNG files: the chunk stream, checked once, and the text chunks in it.

``parse_png_chunks`` is the only PNG parser in cwc; ``emit`` uses it before rewriting a
cover image. It is stricter than the frontend reader (``src/scripts/metadata/png.ts``,
``getFromPngBuffer`` at v1.59.1, which checks only the first four signature bytes):
bad CRCs, chunks running past the end and a missing ``IEND`` are rejected, so cwc never
rewrites a file it cannot read back exactly. It stops at ``IEND``; emit drops whatever
follows.

``read_png_text`` reads text the way ``getFromPngBuffer`` does, so that cwc and the
frontend load the same ``workflow`` or ``prompt`` from one file:

* Only ``tEXt``, ``comf`` and ``iTXt`` chunks carry text. ``zTXt`` is ignored.
* The scan does not stop at ``IEND``: text chunks after it count, and the last chunk
  with a keyword wins. After ``IEND`` the bytes are walked as the frontend walks them:
  each length field is followed to the next header, with no CRC or type check, until the
  offset passes the end, so padding or appended data does not stop the read. A chunk
  that runs past the end is cut at the end of the file. A header cut short (fewer than
  four bytes left for the length) is I1, where the frontend throws.
* The keyword is Latin-1 up to the first NUL. The text is UTF-8 with a leading byte order
  mark removed (``TextDecoder`` does that); invalid bytes become U+FFFD.
* ``iTXt``: the chunk is compressed only when its flag byte is exactly 1, any other value
  means plain text. The language tag and the translated keyword are skipped up to their
  NULs, but never past the end of the chunk, so a chunk cut short gives empty text. The
  flag and method bytes are read from the file even where they lie past the chunk end
  (the CRC), as the frontend does. A compressed chunk with a method other than 0, or
  whose zlib data does not inflate, is skipped and leaves an earlier value in place.
* A zlib stream followed by more bytes does not inflate. ``DecompressionStream('deflate')``
  rejects trailing data; this is read from the Compression Streams specification and
  measured on Node 22.22, not run in Chromium.

Where cwc deliberately differs, the file is read more strictly or not at all:

* Up to ``IEND``, a bad signature, a bad CRC, a chunk that runs past the end, an invalid
  chunk type and a missing ``IHDR`` or ``IEND`` are rejected (I1); the frontend reads
  such files as far as it gets.
* A text chunk with no NUL inside it is skipped. The frontend keeps scanning into the
  next chunk (and loops forever at the end of the file), so it stores the text under a
  keyword that includes bytes of other chunks.
* The size limits stay: the file (``MAX_PNG_BYTES``) and the inflated text
  (``MAX_JSON_BYTES``, I3).
"""

from __future__ import annotations

import zlib
from collections.abc import Iterator
from dataclasses import dataclass

from cwc.ingest.limits import MAX_JSON_BYTES, MAX_PNG_BYTES, input_error
from cwc.report import Code

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CHUNK_LENGTH = 2**31 - 1  # PNG specification limit

TEXT = b"tEXt"
ITXT = b"iTXt"
ZTXT = b"zTXt"
COMF = b"comf"  # ComfyUI's private text chunk, same layout as tEXt
IHDR = b"IHDR"
IEND = b"IEND"


@dataclass(frozen=True)
class Chunk:
    type: bytes
    data: bytes


def _walk(data: bytes, *, past_iend: bool) -> Iterator[tuple[bytes, int, int]]:
    """``(type, start, length)`` of every chunk, ``start`` being the first data byte.

    Checks the file up to ``IEND``: size, signature, ``IHDR`` first, CRCs, chunk types, and
    an ``IEND``. With ``past_iend`` the walk goes on after ``IEND`` the frontend's way
    (:func:`_frontend_tail`).
    """
    if len(data) > MAX_PNG_BYTES:
        raise input_error(Code.I3, f"PNG is {len(data)} bytes; the limit is {MAX_PNG_BYTES}")
    if not data.startswith(SIGNATURE):
        raise input_error(Code.I1, "not a PNG: the signature is missing")
    pos = len(SIGNATURE)
    first = True
    ended = False
    while not ended:
        if pos + 8 > len(data):
            raise input_error(Code.I1, f"PNG ends at byte {len(data)} without an IEND chunk")
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if length > MAX_CHUNK_LENGTH or end > len(data):
            raise input_error(Code.I1, f"PNG chunk at byte {pos} runs past the end of the file")
        if not kind.isalpha():
            raise input_error(Code.I1, f"PNG chunk at byte {pos} has an invalid type")
        if zlib.crc32(data[pos + 4 : pos + 8 + length]) != int.from_bytes(data[pos + 8 + length : end], "big"):
            raise input_error(Code.I1, f"PNG chunk {kind.decode('ascii')} at byte {pos} fails its CRC")
        if first and kind != IHDR:
            raise input_error(Code.I1, "PNG does not start with an IHDR chunk")
        first = False
        yield kind, pos + 8, length
        pos = end
        ended = kind == IEND
    if past_iend:
        yield from _frontend_tail(data, pos)


def _frontend_tail(data: bytes, pos: int) -> Iterator[tuple[bytes, int, int]]:
    """The chunks after ``IEND`` as ``getFromPngBuffer`` walks them: follow each length
    field, check nothing else, stop once the offset passes the end. A chunk running past
    the end is cut there; a length field cut short is I1, where ``DataView.getUint32``
    throws in the frontend. Every step moves on by at least 12 bytes."""
    while pos < len(data):
        if pos + 4 > len(data):
            raise input_error(Code.I1, f"bytes {pos}..{len(data)} after IEND are too short for a chunk length")
        length = int.from_bytes(data[pos : pos + 4], "big")
        start = pos + 8
        yield data[pos + 4 : start], min(start, len(data)), max(0, min(length, len(data) - start))
        pos = start + length + 4


def parse_png_chunks(data: bytes) -> list[Chunk]:
    """Every chunk from ``IHDR`` to ``IEND``. Raises ``CwcError`` I1 (I3 when too large)."""
    return [Chunk(kind, data[start : start + length]) for kind, start, length in _walk(data, past_iend=False)]


def text_keyword(chunk: Chunk) -> str | None:
    """Keyword of a text chunk (tEXt, iTXt, zTXt, comf); None for other chunks.

    Covers ``zTXt`` too because ``emit`` removes old metadata of every kind; reading
    (:func:`read_png_text`) leaves ``zTXt`` alone.
    """
    if chunk.type not in (TEXT, ITXT, ZTXT, COMF):
        return None
    keyword, sep, _ = chunk.data.partition(b"\0")
    return keyword.decode("latin-1") if sep else None


def read_png_text(data: bytes) -> dict[str, str]:
    """Keyword -> text of the text chunks the frontend reads (see the module docstring).

    Keywords are case-sensitive and the last chunk with a keyword wins. Raises ``CwcError``
    I1 for a file that is not a well-formed PNG and I3 for one over a size limit.
    """
    texts: dict[str, str] = {}
    for kind, start, length in _walk(data, past_iend=True):
        if kind in (TEXT, COMF, ITXT):
            found = _frontend_text(data, kind, start, start + length)
            if found is not None:
                texts[found[0]] = found[1]
    return texts


def _frontend_text(data: bytes, kind: bytes, start: int, end: int) -> tuple[str, str] | None:
    """``(keyword, text)`` of the chunk whose data is ``data[start:end]``, or None to skip it.

    Offsets run over the whole file, as in ``getFromPngBuffer``, because the iTXt flag and
    method bytes of a short chunk come from the bytes after it.
    """
    keyword_end = data.find(b"\0", start, end)
    if keyword_end < 0:
        return None
    keyword = data[start:keyword_end].decode("latin-1")
    text_start = keyword_end + 1
    compressed = False
    if kind == ITXT:
        # past the end of the file the frontend reads ``undefined``: not 1, and not 0
        compressed = text_start < len(data) and data[text_start] == 1
        method = data[text_start + 1] if text_start + 1 < len(data) else None
        text_start += 2
        for _ in range(2):  # language tag, translated keyword
            while text_start < end and data[text_start] != 0:
                text_start += 1
            if text_start < end:
                text_start += 1
        if compressed and method != 0:
            return None
    content = data[text_start:end]
    if compressed:
        inflated = _inflate(content)
        if inflated is None:
            return None
        content = inflated
    return keyword, content.decode("utf-8-sig", errors="replace")


def _inflate(data: bytes) -> bytes | None:
    """The zlib stream ``data`` holds, exactly; None when it is broken, cut short or has trailing bytes."""
    inflater = zlib.decompressobj()
    try:
        out = inflater.decompress(data, MAX_JSON_BYTES + 1)
    except zlib.error:
        return None
    if len(out) > MAX_JSON_BYTES:
        raise input_error(Code.I3, f"a compressed PNG text chunk inflates past {MAX_JSON_BYTES} bytes")
    return out if inflater.eof and not inflater.unused_data else None
