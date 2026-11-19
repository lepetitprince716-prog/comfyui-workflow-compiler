import json
import random
import time
import unittest
import zlib
from unittest import mock

from tests import support  # noqa: F401  (puts src/ on sys.path)
from tests.support import json_depth

from cwc.canvas.model import Canvas
from cwc.ingest import limits, load_authoring, load_canvas, parse_png_chunks, read_png_text
from cwc.ingest import png as ingest_png
from cwc.report import CwcError, ExitCode
from tests.fixtures.ingest import pngs


def codes(error: CwcError) -> list[str]:
    return [finding.code.value for finding in error.findings]


class ChunkParserTest(unittest.TestCase):
    def assertRejected(self, data: bytes, code: str = "I1"):
        with self.assertRaises(CwcError) as caught:
            parse_png_chunks(data)
        self.assertEqual(caught.exception.exit_code, ExitCode.INPUT_ERROR)
        self.assertEqual(codes(caught.exception), [code])
        return caught.exception

    def test_a_valid_png_lists_its_chunks(self):
        chunks = parse_png_chunks(pngs.make_png(pngs.text_chunk("k", "v")))
        self.assertEqual([c.type for c in chunks], [b"IHDR", b"IDAT", b"tEXt", b"IEND"])
        self.assertEqual(chunks[2].data, b"k\0v")

    def test_bytes_after_iend_are_ignored(self):
        chunks = parse_png_chunks(pngs.make_png(after_iend=b"trailing garbage"))
        self.assertEqual(chunks[-1].type, b"IEND")

    def test_missing_or_wrong_signature(self):
        self.assertRejected(b"")
        self.assertRejected(b"GIF89a" + b"\0" * 40)
        self.assertRejected(b"\x89PNG\r\n\x1a" + pngs.IHDR + pngs.IEND)  # one byte short

    def test_bad_crc_in_any_chunk(self):
        for name, bad in [
            ("IHDR", pngs.SIGNATURE + pngs.chunk(b"IHDR", b"\0" * 13, crc=1) + pngs.IDAT + pngs.IEND),
            ("tEXt", pngs.make_png(pngs.chunk(b"tEXt", b"k\0v", crc=0))),
            ("IEND", pngs.SIGNATURE + pngs.IHDR + pngs.IDAT + pngs.chunk(b"IEND", b"", crc=0)),
        ]:
            with self.subTest(chunk=name):
                self.assertIn("CRC", self.assertRejected(bad).findings[0].message)

    def test_a_flipped_data_byte_fails_the_crc(self):
        data = bytearray(pngs.make_png(pngs.text_chunk("k", "value")))
        data[data.index(b"value")] ^= 1
        self.assertRejected(bytes(data))

    def test_truncated_files(self):
        data = pngs.make_png()
        for cut in (len(pngs.SIGNATURE), len(pngs.SIGNATURE) + 5, len(data) - 12, len(data) - 1):
            with self.subTest(cut=cut):
                self.assertRejected(data[:cut])

    def test_a_chunk_longer_than_the_file(self):
        bad = pngs.SIGNATURE + pngs.IHDR + (1000).to_bytes(4, "big") + b"tEXt" + b"short"
        self.assertIn("past the end", self.assertRejected(bad).findings[0].message)

    def test_a_chunk_length_above_the_specification_limit(self):
        bad = pngs.SIGNATURE + pngs.IHDR + (2**31).to_bytes(4, "big") + b"tEXt"
        self.assertRejected(bad)

    def test_the_first_chunk_must_be_ihdr(self):
        self.assertRejected(pngs.SIGNATURE + pngs.IDAT + pngs.IEND)

    def test_chunk_types_must_be_letters(self):
        self.assertRejected(pngs.SIGNATURE + pngs.IHDR + pngs.chunk(b"12ab", b"") + pngs.IEND)

    def test_a_missing_iend_is_rejected(self):
        self.assertRejected(pngs.SIGNATURE + pngs.IHDR + pngs.IDAT)

    def test_too_large_is_i3(self):
        with mock.patch.object(ingest_png, "MAX_PNG_BYTES", 50):
            self.assertRejected(pngs.make_png(), "I3")


class ReadPngTextTest(unittest.TestCase):
    def test_text_chunk_kinds(self):
        png = pngs.make_png(
            pngs.text_chunk("a", "plain"),
            pngs.itxt_chunk("b", "international"),
            pngs.itxt_chunk("c", "squeezed", compressed=True),
            pngs.ztxt_chunk("d", "deflated"),
            pngs.chunk(b"comf", b"e\0comfy"),
        )
        self.assertEqual(
            read_png_text(png),
            {"a": "plain", "b": "international", "c": "squeezed", "e": "comfy"},
        )

    def test_ztxt_is_not_read(self):
        png = pngs.make_png(pngs.text_chunk("workflow", "A"), pngs.ztxt_chunk("workflow", "B"), pngs.ztxt_chunk("only", "z"))
        self.assertEqual(read_png_text(png), {"workflow": "A"})

    def test_text_is_utf8_and_keywords_are_latin1(self):
        png = pngs.make_png(pngs.text_chunk("caf\xe9", "你好"))
        self.assertEqual(read_png_text(png), {"caf\xe9": "你好"})

    def test_later_chunks_overwrite_earlier_ones_and_keywords_are_case_sensitive(self):
        png = pngs.make_png(pngs.text_chunk("k", "one"), pngs.text_chunk("K", "upper"), pngs.itxt_chunk("k", "two"))
        self.assertEqual(read_png_text(png), {"k": "two", "K": "upper"})

    def test_unreadable_text_chunks_are_skipped(self):
        png = pngs.make_png(
            pngs.chunk(b"tEXt", b"no separator"),
            pngs.chunk(b"iTXt", b"z\0\x01\0en\0\0not deflate data"),
            pngs.itxt_chunk("method", "x", compressed=True, method=7),
            pngs.text_chunk("ok", "yes"),
        )
        self.assertEqual(read_png_text(png), {"ok": "yes"})

    def test_a_truncated_deflate_stream_is_skipped(self):
        cut = zlib.compress(b"hello world")[:-6]
        png = pngs.make_png(pngs.chunk(b"iTXt", b"z\0\x01\0en\0\0" + cut), pngs.text_chunk("ok", "1"))
        self.assertEqual(read_png_text(png), {"ok": "1"})

    def test_bytes_after_the_zlib_stream_make_the_chunk_unreadable(self):
        head = b"\x01\0en\0\0"
        png = pngs.make_png(
            pngs.text_chunk("k", "earlier"),
            pngs.chunk(b"iTXt", b"k\0" + head + zlib.compress(b"later") + b"junk"),
            pngs.chunk(b"iTXt", b"clean\0" + head + zlib.compress(b"fine")),
        )
        self.assertEqual(read_png_text(png), {"k": "earlier", "clean": "fine"})

    def test_a_compressed_itxt_chunk_without_data_is_skipped(self):
        png = pngs.make_png(pngs.text_chunk("k", "earlier"), pngs.chunk(b"iTXt", b"k\0\x01\0en\0\0"))
        self.assertEqual(read_png_text(png), {"k": "earlier"})

    def test_an_itxt_flag_other_than_one_means_plain_text(self):
        for flag in (0, 2, 9, 255):
            with self.subTest(flag=flag):
                png = pngs.make_png(pngs.chunk(b"iTXt", b"k\0" + bytes([flag, 0]) + b"en\0\0hello"))
                self.assertEqual(read_png_text(png), {"k": "hello"})

    def test_the_method_byte_only_matters_when_compressed(self):
        png = pngs.make_png(pngs.chunk(b"iTXt", b"k\0\0\x07en\0\0plain"))
        self.assertEqual(read_png_text(png), {"k": "plain"})

    def test_an_itxt_chunk_with_missing_nuls_gives_empty_text_and_overwrites(self):
        for tail in (b"", b"\0", b"en", b"en\0", b"en\0tr", b"en\0tr\0"):
            with self.subTest(tail=tail):
                png = pngs.make_png(pngs.text_chunk("k", "earlier"), pngs.chunk(b"iTXt", b"k\0\0\0" + tail))
                self.assertEqual(read_png_text(png), {"k": ""})

    def test_the_itxt_language_and_translated_keyword_are_skipped(self):
        png = pngs.make_png(pngs.chunk(b"iTXt", b"k\0\0\0de\0\xc3\x9cber\0text"))
        self.assertEqual(read_png_text(png), {"k": "text"})

    def test_a_short_itxt_chunk_takes_its_flag_and_method_from_the_bytes_after_it(self):
        # The data ends right after the keyword, so the flag and method are the first CRC bytes.
        def keyword_with(first: int, second_nonzero: bool) -> str:
            for i in range(10**7):
                keyword = f"K{i}"
                crc = zlib.crc32(b"iTXt" + keyword.encode() + b"\0").to_bytes(4, "big")
                if crc[0] == first and (crc[1] != 0) == second_nonzero:
                    return keyword
            raise AssertionError("no keyword found")

        plain = keyword_with(0, True)  # flag 0: plain text, empty
        squeezed = keyword_with(1, True)  # flag 1, method not 0: skipped
        png = pngs.make_png(
            pngs.text_chunk(plain, "earlier"),
            pngs.text_chunk(squeezed, "earlier"),
            pngs.chunk(b"iTXt", plain.encode() + b"\0"),
            pngs.chunk(b"iTXt", squeezed.encode() + b"\0"),
        )
        self.assertEqual(read_png_text(png), {plain: "", squeezed: "earlier"})

    def test_text_chunks_after_iend_are_read_and_the_last_one_wins(self):
        png = pngs.make_png(pngs.text_chunk("workflow", "A"), after_iend=pngs.text_chunk("workflow", "B") + pngs.text_chunk("x", "y"))
        self.assertEqual(read_png_text(png), {"workflow": "B", "x": "y"})
        self.assertEqual([c.type for c in parse_png_chunks(png)][-1], b"IEND")

    def test_bytes_after_iend_are_walked_as_the_frontend_walks_them(self):
        before = pngs.text_chunk("workflow", "kept")
        for name, tail, expected in [
            ("garbage", b"trailing garbage", {"workflow": "kept"}),
            ("zero padding", b"\0" * 16, {"workflow": "kept"}),
            ("bad crc", pngs.chunk(b"tEXt", b"k\0v", crc=0), {"workflow": "kept", "k": "v"}),
            ("cut chunk", pngs.text_chunk("k", "value")[:-2], {"workflow": "kept", "k": "value"}),
            ("itxt cut at its keyword", pngs.chunk(b"iTXt", b"k\0")[:-4], {"workflow": "kept", "k": ""}),
        ]:
            with self.subTest(tail=name):
                self.assertEqual(read_png_text(pngs.make_png(before, after_iend=tail)), expected)

    def test_a_length_field_cut_short_after_iend_is_rejected(self):
        for tail in (b"\n", b"\0\0\0"):
            with self.subTest(tail=tail):
                with self.assertRaises(CwcError) as caught:
                    read_png_text(pngs.make_png(after_iend=tail))
                self.assertEqual(codes(caught.exception), ["I1"])

    def test_a_leading_byte_order_mark_is_removed(self):
        png = pngs.make_png(pngs.chunk(b"tEXt", b"k\0\xef\xbb\xbfhi"))
        self.assertEqual(read_png_text(png), {"k": "hi"})

    def test_invalid_utf8_is_replaced(self):
        png = pngs.make_png(pngs.chunk(b"tEXt", b"k\0\xff\xfe"))
        self.assertEqual(read_png_text(png), {"k": "��"})

    def test_a_png_without_text_gives_an_empty_dict(self):
        self.assertEqual(read_png_text(pngs.make_png()), {})

    def test_a_bad_png_is_i1(self):
        with self.assertRaises(CwcError) as caught:
            read_png_text(b"not a png")
        self.assertEqual(codes(caught.exception), ["I1"])

    def test_a_decompression_bomb_is_i3(self):
        png = pngs.make_png(pngs.itxt_chunk("k", "a" * 5000, compressed=True))
        with mock.patch.object(ingest_png, "MAX_JSON_BYTES", 1000):
            with self.assertRaises(CwcError) as caught:
                read_png_text(png)
        self.assertEqual(codes(caught.exception), ["I3"])
        png = pngs.make_png(pngs.ztxt_chunk("k", "a" * 5000))  # ignored, so never inflated
        with mock.patch.object(ingest_png, "MAX_JSON_BYTES", 1000):
            self.assertEqual(read_png_text(png), {})


class LoadCanvasFromPngTest(unittest.TestCase):
    def assertRejected(self, data: bytes, code: str):
        with self.assertRaises(CwcError) as caught:
            load_canvas(data)
        self.assertEqual(caught.exception.exit_code, ExitCode.INPUT_ERROR)
        self.assertEqual(codes(caught.exception), [code])
        return caught.exception

    def test_the_workflow_chunk_is_the_canvas(self):
        canvas = load_canvas(pngs.canvas_png())
        self.assertIsInstance(canvas, Canvas)
        self.assertEqual(canvas.raw, pngs.canvas_doc())

    def test_the_workflow_wins_over_the_prompt_chunk(self):
        png = pngs.canvas_png(None, pngs.text_chunk("prompt", json.dumps({"1": {"class_type": "X", "inputs": {}}})))
        self.assertEqual(load_canvas(png).raw, pngs.canvas_doc())

    def test_a_compressed_workflow_chunk_is_read(self):
        png = pngs.make_png(pngs.itxt_chunk("workflow", json.dumps(pngs.canvas_doc()), compressed=True))
        self.assertEqual(load_canvas(png).raw, pngs.canvas_doc())

    def test_a_workflow_chunk_after_iend_wins_as_in_the_frontend(self):
        other = dict(pngs.canvas_doc(), last_node_id=7)
        png = pngs.make_png(pngs.text_chunk("workflow", json.dumps(pngs.canvas_doc())), after_iend=pngs.text_chunk("workflow", json.dumps(other)))
        self.assertEqual(load_canvas(png).raw, other)

    def test_a_ztxt_workflow_does_not_replace_the_text_workflow(self):
        other = dict(pngs.canvas_doc(), last_node_id=7)
        png = pngs.canvas_png(None, pngs.ztxt_chunk("workflow", json.dumps(other)))
        self.assertEqual(load_canvas(png).raw, pngs.canvas_doc())

    def test_a_png_with_only_a_prompt_is_i4(self):
        png = pngs.make_png(pngs.text_chunk("prompt", json.dumps({"1": {"class_type": "X", "inputs": {}}})))
        error = self.assertRejected(png, "I4")
        self.assertIn("only an API prompt", error.findings[0].message)
        self.assertIn("cwc extract", error.findings[0].hint)

    def test_a_png_with_no_workflow_or_prompt_is_i4(self):
        self.assertRejected(pngs.make_png(pngs.text_chunk("Software", "x")), "I4")
        self.assertRejected(pngs.make_png(), "I4")

    def test_a_workflow_chunk_that_is_not_json_is_i1(self):
        self.assertRejected(pngs.make_png(pngs.text_chunk("workflow", "{nope")), "I1")

    def test_a_workflow_chunk_holding_an_api_prompt_is_i1(self):
        png = pngs.make_png(pngs.text_chunk("workflow", json.dumps({"1": {"class_type": "X", "inputs": {}}})))
        self.assertRejected(png, "I1")

    def test_a_workflow_chunk_with_version_1_is_i2(self):
        doc = {**pngs.canvas_doc(), "version": 1}
        self.assertRejected(pngs.canvas_png(doc), "I2")

    def test_a_corrupt_png_is_i1(self):
        data = bytearray(pngs.canvas_png())
        data[-20] ^= 0xFF
        self.assertRejected(bytes(data), "I1")


class LoadAuthoringTest(unittest.TestCase):
    def assertRejected(self, data: bytes, code: str):
        with self.assertRaises(CwcError) as caught:
            load_authoring(data)
        self.assertEqual(caught.exception.exit_code, ExitCode.INPUT_ERROR)
        self.assertEqual(codes(caught.exception), [code])
        return caught.exception

    def test_an_api_prompt_with_relaxations_is_returned_as_is(self):
        doc = {"1": {"class_type": "A", "inputs": {}}, "_workflow": {"name": "x"}}
        self.assertEqual(load_authoring(json.dumps(doc).encode()), doc)

    def test_a_canvas_workflow_is_refused(self):
        error = self.assertRejected(json.dumps(pngs.canvas_doc()).encode(), "I1")
        self.assertIn("canvas workflow", error.findings[0].message)
        self.assertIn("beautify", error.findings[0].hint)

    def test_a_canvas_without_links_but_with_version_is_refused(self):
        self.assertRejected(json.dumps({"nodes": [], "version": 0.4}).encode(), "I1")
        self.assertRejected(json.dumps({"nodes": [], "links": []}).encode(), "I1")

    def test_a_canvas_inside_a_png_is_refused(self):
        error = self.assertRejected(pngs.canvas_png(), "I1")
        self.assertIn("PNG", error.findings[0].message)

    def test_not_an_object_or_not_json(self):
        self.assertRejected(b"[1, 2]", "I1")
        self.assertRejected(b'"text"', "I1")
        self.assertRejected(b"{nope", "I1")
        self.assertRejected(b"", "I1")

    def test_the_node_limit_counts_nodes_but_not_the_workflow_info(self):
        entry = {"class_type": "A", "inputs": {}}
        at_limit = {str(i): entry for i in range(limits.MAX_NODES)}
        at_limit["_workflow"] = {}
        self.assertEqual(len(load_authoring(json.dumps(at_limit).encode())), limits.MAX_NODES + 1)
        over = {str(i): entry for i in range(limits.MAX_NODES + 1)}
        self.assertIn("limit", self.assertRejected(json.dumps(over).encode(), "I3").findings[0].message)


class ParseJsonLimitsTest(unittest.TestCase):
    def assertRejected(self, data: bytes, code: str):
        with self.assertRaises(CwcError) as caught:
            limits.parse_json(data, "doc")
        self.assertEqual(codes(caught.exception), [code])
        return caught.exception

    def test_a_bom_is_allowed(self):
        self.assertEqual(limits.parse_json(b"\xef\xbb\xbf{\"a\": 1}", "doc"), {"a": 1})

    def test_not_utf8_not_json_and_javascript_constants_are_i1(self):
        self.assertRejected(b"\xff\xfe{}", "I1")
        self.assertRejected(b"{'a': 1}", "I1")
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(constant=constant):
                self.assertRejected(b'{"a": ' + constant + b"}", "I1")

    def test_size_limit_is_i3(self):
        with mock.patch.object(limits, "MAX_JSON_BYTES", 10):
            self.assertRejected(b'{"a": "0123456789"}', "I3")
            self.assertEqual(limits.parse_json(b'{"a":1}', "doc"), {"a": 1})

    def test_nesting_at_the_limit_passes_and_one_more_fails(self):
        at_limit = b"[" * limits.MAX_DEPTH + b"]" * limits.MAX_DEPTH
        self.assertEqual(json_depth(limits.parse_json(at_limit, "doc")), limits.MAX_DEPTH)
        over = b"[" * (limits.MAX_DEPTH + 1) + b"]" * (limits.MAX_DEPTH + 1)
        self.assertIn("deep", self.assertRejected(over, "I3").findings[0].message)

    def test_nesting_of_objects_counts_too(self):
        doc = b'{"a":' * (limits.MAX_DEPTH + 1) + b"1" + b"}" * (limits.MAX_DEPTH + 1)
        self.assertRejected(doc, "I3")

    def test_absurd_nesting_does_not_crash(self):
        self.assertRejected(b"[" * 200000 + b"]" * 200000, "I3")

    def test_wide_documents_are_not_nesting(self):
        self.assertEqual(len(limits.parse_json(b"[" + b",".join([b"[1]"] * 5000) + b"]", "doc")), 5000)

    def nested(self, level: bytes, depth: int) -> bytes:
        """``depth`` arrays inside one another, each holding ``level`` (a JSON value) and the next."""
        return b"".join(b"[" + level + b"," for _ in range(depth)) + b"1" + b"]" * depth

    def assertNestingIsExactly(self, level: bytes):
        """At the limit the document is read, one level more is refused: the count is exact."""
        at_limit = self.nested(level, limits.MAX_DEPTH)
        self.assertEqual(json_depth(limits.parse_json(at_limit, "doc")), limits.MAX_DEPTH)
        self.assertRejected(self.nested(level, limits.MAX_DEPTH + 1), "I3")

    def test_a_document_past_the_depth_limit_never_reaches_the_parser(self):
        # The parser's C scanner recurses once per level and, on some Python versions and
        # systems, overruns the C stack instead of raising: the depth is measured before it.
        with mock.patch.object(limits.json, "loads", side_effect=AssertionError("the parser was called")):
            self.assertRejected(b"[" * 200000 + b"]" * 200000, "I3")
            self.assertRejected(b'{"a":' * 200000 + b"1" + b"}" * 200000, "I3")
            self.assertRejected(b"[" * (limits.MAX_DEPTH + 1), "I3")  # never closed

    def test_the_depth_error_names_the_limit(self):
        message = self.assertRejected(b"[" * 200000, "I3").findings[0].message
        self.assertIn(f"deeper than the limit of {limits.MAX_DEPTH}", message)

    def test_brackets_in_strings_are_not_nesting(self):
        self.assertNestingIsExactly(json.dumps("[" * 1000).encode())
        self.assertNestingIsExactly(json.dumps("{[]}" * 500).encode())
        doc = json.dumps({"[" * 1000: ["]" * 1000, "}{" * 1000]}).encode()
        self.assertEqual(limits.parse_json(doc, "doc"), json.loads(doc))

    def test_a_string_with_an_escaped_quote_keeps_its_brackets(self):
        for content in ('"[', '\\"[', '\\\\"{', 'a\\"]\\"{'):
            with self.subTest(content=content):
                self.assertNestingIsExactly(json.dumps(content).encode())

    def test_a_string_ending_in_an_escaped_backslash_ends_there(self):
        # The brackets after it are real: reading its closing quote as escaped would hide them.
        for content in ("\\", "x\\\\", "\\\\\\", "[\\"):
            with self.subTest(content=content):
                self.assertNestingIsExactly(json.dumps(content).encode())

    def test_objects_and_arrays_count_together(self):
        def alternating(depth: int) -> bytes:
            opening = [b"[" if level % 2 == 0 else b'{"k":' for level in range(depth)]
            closing = [b"]" if level % 2 == 0 else b"}" for level in range(depth)]
            return b"".join(opening) + b"1" + b"".join(reversed(closing))

        self.assertEqual(json_depth(limits.parse_json(alternating(limits.MAX_DEPTH), "doc")), limits.MAX_DEPTH)
        self.assertRejected(alternating(limits.MAX_DEPTH + 1), "I3")

    def test_documents_that_are_not_json_stay_i1(self):
        for data in (b'["abc', b"[}", b'{"a": [1}', b'["a\\"', b'"abc' + b"[" * 200000, b"]" * 200000):
            with self.subTest(data=data[:20]):
                self.assertRejected(data, "I1")

    def test_a_document_longer_than_one_scan_window_is_counted_whole(self):
        gap = b" " * (limits._WINDOW + 3)
        half = limits.MAX_DEPTH // 2 + 1
        self.assertRejected(b"[" * half + gap + b"[" * half + gap + b"1" + b"]" * 2 * half, "I3")
        self.assertEqual(json_depth(limits.parse_json(b"[" * half + gap + b"]" * half, "doc")), half)
        # a string that runs across windows is not read as brackets in the next one
        long_string = json.dumps({"k": "[" * (2 * limits._WINDOW + 5), "j": "{" * 7}).encode()
        self.assertEqual(len(limits.parse_json(long_string, "doc")["k"]), 2 * limits._WINDOW + 5)

    def test_the_scan_agrees_with_the_depth_of_what_the_parser_returns(self):
        rng = random.Random(20261118)
        pieces = list('[]{}"\\ ab,:') + ["\\\\", '\\"', "\u00e9", "\U0001f600"]

        def value(levels: int):
            pick = rng.random()
            if levels == 0 or pick < 0.25:
                return rng.choice([1, None, "".join(rng.choice(pieces) for _ in range(rng.randint(0, 10)))])
            if pick < 0.6:
                return [value(levels - 1) for _ in range(rng.randint(0, 3))]
            return {"".join(rng.choice(pieces) for _ in range(3)): value(levels - 1) for _ in range(rng.randint(0, 3))}

        for _ in range(1500):
            doc = json.dumps(value(rng.randint(0, 7)), ensure_ascii=rng.random() < 0.5)
            for limit in (1, 2, 4):
                with mock.patch.object(limits, "MAX_DEPTH", limit):
                    too_deep = json_depth(json.loads(doc)) > limit
                    if too_deep:
                        self.assertRejected(doc.encode(), "I3")
                    else:
                        self.assertEqual(limits.parse_json(doc.encode(), "doc"), json.loads(doc))

    def test_the_scan_costs_little_next_to_the_parse(self):
        # Many short strings and shallow brackets, about 8 MiB: the depth scan runs in C, so the
        # whole read stays within three times what json.loads alone takes.
        data = b"[" + b",".join([b'{"name":"abcd","v":[1,"x"]}'] * 300000) + b"]"
        self.assertGreater(len(data), 8 * 1024 * 1024)
        text = data.decode()
        parse, parser = [], []
        for _ in range(3):  # the fastest of three: a busy machine slows a run, not every run
            start = time.perf_counter()
            limits.parse_json(data, "doc")
            parse.append(time.perf_counter() - start)
            start = time.perf_counter()
            json.loads(text)
            parser.append(time.perf_counter() - start)
        self.assertLess(min(parse), 3 * min(parser), (parse, parser))


if __name__ == "__main__":
    unittest.main()
