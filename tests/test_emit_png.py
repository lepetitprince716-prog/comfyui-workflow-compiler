import json
import unittest
import zlib

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.emit import embed_png, json_bytes, png_chunk
from cwc.ingest import load_canvas, parse_png_chunks, read_png_text
from cwc.report import CwcError
from tests.fixtures.ingest import pngs

OLD_TEXTS = (
    pngs.text_chunk("workflow", '{"old": "workflow"}'),
    pngs.text_chunk("prompt", '{"old": "prompt"}'),
    pngs.itxt_chunk("parameters", "old parameters"),
    pngs.ztxt_chunk("templates", "old templates"),
    pngs.itxt_chunk("workflow", "compressed old", compressed=True),
    pngs.chunk(b"comf", b"private\0data"),
)
KEPT_TEXTS = (pngs.text_chunk("Software", "gimp"), pngs.text_chunk("Workflow", "other case"))
NEW = {"workflow": json_bytes({"n": 1}, ascii=True).decode(), "prompt": '{"1": {}}'}


def types(png: bytes) -> list[bytes]:
    return [chunk.type for chunk in parse_png_chunks(png)]


class PngChunkTest(unittest.TestCase):
    def test_iend_has_its_well_known_bytes(self):
        self.assertEqual(png_chunk(b"IEND", b""), bytes.fromhex("0000000049454e44ae426082"))

    def test_chunks_match_an_independent_writer(self):
        for kind, data in [(b"tEXt", b"k\0v"), (b"IDAT", b"\0" * 300), (b"IHDR", b"")]:
            with self.subTest(kind=kind):
                self.assertEqual(png_chunk(kind, data), pngs.chunk(kind, data))

    def test_a_chunk_round_trips_through_the_parser(self):
        png = pngs.SIGNATURE + pngs.IHDR + png_chunk(b"tEXt", b"k\0v") + pngs.IEND
        self.assertEqual(parse_png_chunks(png)[1].data, b"k\0v")


class EmbedPngTest(unittest.TestCase):
    def cover(self) -> bytes:
        return pngs.make_png(*KEPT_TEXTS[:1], *OLD_TEXTS, KEPT_TEXTS[1], after_iend=b"junk after the end")

    def test_only_the_new_chunks_remain(self):
        out = embed_png(self.cover(), NEW)
        self.assertEqual(read_png_text(out), {"Software": "gimp", "Workflow": "other case", **NEW})
        self.assertEqual(types(out), [b"IHDR", b"IDAT", b"tEXt", b"tEXt", b"tEXt", b"tEXt", b"IEND"])
        self.assertNotIn(b"old", out)
        self.assertNotIn(b"private", out)
        self.assertNotIn(b"junk", out)

    def test_every_old_workflow_chunk_kind_is_removed(self):
        out = embed_png(pngs.make_png(*OLD_TEXTS), {})
        self.assertEqual(types(out), [b"IHDR", b"IDAT", b"IEND"])

    def test_new_chunks_sit_right_before_iend_in_the_given_order(self):
        out = embed_png(self.cover(), {"b": "2", "a": "1"})
        chunks = parse_png_chunks(out)
        self.assertEqual([c.type for c in chunks][-3:], [b"tEXt", b"tEXt", b"IEND"])
        self.assertEqual([c.data for c in chunks][-3:-1], [b"b\0" + b"2", b"a\0" + b"1"])

    def test_a_second_embed_gives_the_same_bytes(self):
        once = embed_png(self.cover(), NEW)
        self.assertEqual(embed_png(once, NEW), once)

    def test_embedding_other_texts_replaces_the_earlier_ones(self):
        once = embed_png(self.cover(), NEW)
        newer = {"workflow": '{"n": 2}', "prompt": '{"2": {}}'}
        out = embed_png(once, newer)
        self.assertEqual(read_png_text(out), {"Software": "gimp", "Workflow": "other case", **newer})
        self.assertEqual(out.count(b"tEXt"), 4)

    def test_a_key_that_is_written_is_replaced_even_if_the_frontend_ignores_it(self):
        cover = pngs.make_png(pngs.text_chunk("cwc", "old"), pngs.text_chunk("kept", "x"))
        out = embed_png(cover, {"cwc": "new"})
        self.assertEqual(read_png_text(out), {"kept": "x", "cwc": "new"})

    def test_a_png_without_old_metadata_is_only_rebuilt(self):
        plain = pngs.make_png(KEPT_TEXTS[0])
        self.assertEqual(embed_png(plain, {}), plain)

    def test_kept_chunks_keep_their_bytes_and_order(self):
        cover = pngs.make_png(pngs.chunk(b"pHYs", b"\0" * 9), pngs.text_chunk("a", "1"), pngs.text_chunk("workflow", "{}"))
        before = [(c.type, c.data) for c in parse_png_chunks(cover) if c.data != b"workflow\0{}"]
        out = embed_png(cover, {"workflow": "[]"})
        after = [(c.type, c.data) for c in parse_png_chunks(out) if c.data != b"workflow\0[]"]
        self.assertEqual(after, before)

    def test_the_image_data_survives(self):
        out = embed_png(self.cover(), NEW)
        idat = next(c for c in parse_png_chunks(out) if c.type == b"IDAT")
        self.assertEqual(zlib.decompress(idat.data), b"\0\x7f")

    def test_the_result_loads_as_a_canvas(self):
        text = json.dumps(pngs.canvas_doc(), ensure_ascii=True)
        out = embed_png(pngs.make_png(), {"workflow": text})
        self.assertEqual(load_canvas(out).raw, pngs.canvas_doc())

    def test_ascii_json_text_with_non_ascii_content_round_trips(self):
        doc = {"title": "你好 \U0001f600"}
        out = embed_png(pngs.make_png(), {"workflow": json_bytes(doc, ascii=True).decode()})
        self.assertEqual(json.loads(read_png_text(out)["workflow"]), doc)

    def test_a_bad_png_is_i1(self):
        good = pngs.make_png()
        broken = {
            "signature": b"\x88PNG\r\n\x1a\n" + good[8:],
            "crc": good[:-1] + bytes([good[-1] ^ 1]),
            "truncated": good[:-12],
            "empty": b"",
        }
        for name, data in broken.items():
            with self.subTest(case=name):
                with self.assertRaises(CwcError) as caught:
                    embed_png(data, NEW)
                self.assertEqual([f.code.value for f in caught.exception.findings], ["I1"])

    def test_bad_keywords_are_refused(self):
        too_long = "k" * 80
        for keyword in ["", too_long, " lead", "trail ", "two  spaces", "tab\t", "nul\0", "你", "del\x7f", "\xa0pad"]:
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    embed_png(pngs.make_png(), {keyword: "v"})

    def test_good_keywords_are_accepted(self):
        for keyword in ["k", "k" * 79, "two words", "caf\xe9", "a~z"]:
            with self.subTest(keyword=keyword):
                out = embed_png(pngs.make_png(), {keyword: "v"})
                self.assertEqual(read_png_text(out), {keyword: "v"})

    def test_text_must_be_ascii_without_nul(self):
        for value in ["caf\xe9", "你", "a\0b"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    embed_png(pngs.make_png(), {"k": value})

    def test_a_rejected_text_leaves_no_partial_result(self):
        with self.assertRaises(ValueError):
            embed_png(pngs.make_png(), {"ok": "1", "bad": "你"})


if __name__ == "__main__":
    unittest.main()
