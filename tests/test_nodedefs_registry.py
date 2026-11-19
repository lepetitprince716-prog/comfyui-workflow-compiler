import gzip
import http.server
import json
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (puts src/ on sys.path)
from tests.support import FIXTURES, ROOT, json_depth

from cwc.ingest import limits
from cwc.nodedefs import registry
from cwc.nodedefs.derive import slot_layout
from cwc.nodedefs.registry import NodeDefs, read_source
from cwc.nodedefs.types import Behavior, Tier, WidgetKind
from cwc.report import CwcError, ExitCode
from cwc.resources import read_bytes

# Synthetic types; none of them is a real node type.
INFO = {
    "Alpha": {
        "input": {
            "required": {
                "seed": ["INT", {"default": 7}],
                "text": ["STRING", {"default": "short"}],
                "model": ["MODEL", {}],
            },
            "optional": {"mask": ["MASK", {}]},
        },
        "output": ["LATENT"],
        "output_name": ["LATENT"],
        "display_name": "Alpha Node",
        "category": "testing/first",
        "python_module": "nodes",
    },
    "Beta": {
        "input": {"required": {"steps": ["INT", {"default": 20}]}},
        "output": ["IMAGE"],
        "display_name": "Beta Thing",
        "category": "testing/second",
        "python_module": "custom_nodes.pack",
    },
}

TRUTH = {
    "format": 1,
    "frontend_version": "1.0.0",
    "comfyui_version": "0.1.0",
    "nodes": {
        "Alpha": {
            "inputs": [
                {"name": "model", "type": "MODEL", "widget": None, "shape": None},
                {"name": "mask", "type": "MASK", "widget": None, "shape": 7},
                {"name": "seed", "type": "INT", "widget": "seed", "shape": None},
                {"name": "text", "type": "STRING", "widget": "text", "shape": None},
            ],
            "outputs": [{"name": "LATENT", "type": "LATENT", "is_list": False}],
            "widgets": [
                {"name": "seed", "type": "number", "value": 3, "to_prompt": True},
                {"name": "control_after_generate", "type": "combo", "value": "fixed", "to_prompt": False},
                {"name": "text", "type": "text", "value": None, "to_prompt": True},
                {"name": "painted", "type": "custom", "value": 1, "to_prompt": True},
            ],
            "size": {"classic": [1, 1], "vue": None},
        },
        "Ghost": {"inputs": [], "outputs": [], "widgets": [], "size": {"classic": [1, 1], "vue": None}},
    },
    "withheld": [["Alpha", "text"]],
}

TEMPLATES = {
    "format": 1,
    "templates": [
        {"type": "Beta", "pack": "pack-b", "ver": "1.0.0", "source": "learned", "widgets": ["steps", None], "evidence": "test"},
        {"type": "Beta", "pack": "pack-b", "ver": "1.0.1", "source": "curated", "widgets": ["steps", "extra"], "evidence": "test"},
        {"type": "Gamma", "pack": "pack-g", "ver": "2.0.0", "source": "learned", "widgets": [None, "w"], "evidence": "test"},
    ],
}

EMPTY_TRUTH = {"format": 1, "frontend_version": "1.0.0", "comfyui_version": "0.1.0", "nodes": {}}
EMPTY_TEMPLATES = {"format": 1, "templates": []}


def load(info=INFO, truth=TRUTH, templates=TEMPLATES):
    return NodeDefs.load(object_info=info, truth=truth, templates=templates, include_core=False)


class TierTest(unittest.TestCase):
    def setUp(self):
        self.defs = load()

    def test_tiers(self):
        self.assertEqual(self.defs.tier_of("Alpha"), Tier.TRUTH)
        self.assertEqual(self.defs.tier_of("Beta"), Tier.TEMPLATES)
        self.assertEqual(self.defs.tier_of("Gamma"), Tier.TEMPLATES)
        self.assertEqual(self.defs.tier_of("Note"), Tier.BUILTIN)
        self.assertIsNone(self.defs.tier_of("Ghost"))  # truth without /object_info is not a definition
        plain = load(truth=EMPTY_TRUTH, templates=EMPTY_TEMPLATES)
        self.assertEqual(plain.tier_of("Alpha"), Tier.DERIVED)
        self.assertEqual(plain.tier_of("Beta"), Tier.DERIVED)

    def test_truth_beats_templates(self):
        templates = {"format": 1, "templates": [{"type": "Alpha", "source": "curated", "widgets": ["x"]}]}
        self.assertEqual(load(templates=templates).tier_of("Alpha"), Tier.TRUTH)

    def test_builtins(self):
        note = self.defs.get("Note")
        self.assertTrue(note.virtual)
        self.assertEqual([w.name for w in note.widgets], ["text"])
        reroute = self.defs.get("Reroute")
        self.assertEqual([(s.name, s.type) for s in slot_layout(reroute)], [("", "*")])
        self.assertFalse(self.defs.get("PrimitiveNode").layout_complete)
        behaviors = {name: self.defs.get(name).behavior for name in ("Note", "MarkdownNote", "Reroute", "PrimitiveNode")}
        self.assertEqual(
            behaviors,
            {
                "Note": Behavior.NOTE,
                "MarkdownNote": Behavior.NOTE,
                "Reroute": Behavior.PASSTHROUGH,
                "PrimitiveNode": Behavior.PRIMITIVE,
            },
        )
        self.assertIsNone(self.defs.get("Alpha").behavior)

    def test_default_load_has_core_and_builtins(self):
        defs = NodeDefs.load()
        tiers = {defs.tier_of(name) for name in defs}
        self.assertEqual(tiers, {Tier.TRUTH, Tier.BUILTIN})
        self.assertGreaterEqual(len(defs), 706)


class TruthMergeTest(unittest.TestCase):
    def setUp(self):
        self.alpha = load().get("Alpha")

    def test_order_and_values_from_truth(self):
        self.assertEqual([w.name for w in self.alpha.widgets], ["seed", "control_after_generate", "text", "painted"])
        self.assertEqual(self.alpha.widgets[0].default, 3)
        self.assertEqual(self.alpha.widgets[1].default, "fixed")
        self.assertEqual([s.name for s in slot_layout(self.alpha)], ["model", "mask", "seed", "text"])
        self.assertEqual([s.optional for s in slot_layout(self.alpha)], [False, True, False, False])

    def test_size_from_truth(self):
        self.assertEqual(self.alpha.size, (1.0, 1.0))
        self.assertIsNone(load().get("Beta").size)  # templates record no size

    def test_kinds_from_derive(self):
        kinds = [(w.kind, w.input_name) for w in self.alpha.widgets]
        self.assertEqual(
            kinds,
            [
                (WidgetKind.VALUE, "seed"),
                (WidgetKind.CONTROL, "seed"),
                (WidgetKind.VALUE, "text"),
                (WidgetKind.EXTRA, None),
            ],
        )

    def test_metadata_from_object_info(self):
        self.assertEqual(self.alpha.display_name, "Alpha Node")
        self.assertTrue(self.alpha.input("seed").required)
        self.assertFalse(self.alpha.input("mask").required)
        self.assertTrue(self.alpha.input("seed").has_default)
        self.assertEqual(self.alpha.input("seed").default, 7)  # the declared default stays in the input

    def test_withheld_truth_value_falls_back_to_object_info(self):
        # The truth value is withheld; /object_info still declares the default.
        self.assertEqual(self.alpha.widgets[2].default, "short")

    def test_withheld_in_object_info_means_no_default(self):
        info = {"format": 1, "nodes": INFO, "withheld": [["Alpha", "text"]]}
        alpha = load(info=info).get("Alpha")
        self.assertFalse(alpha.input("text").has_default)
        self.assertIsNone(alpha.input("text").default)
        self.assertIsNone(alpha.widgets[2].default)
        derived = load(info=info, truth=EMPTY_TRUTH).get("Alpha")
        self.assertFalse(derived.input("text").has_default)
        self.assertIsNone(next(w for w in derived.widgets if w.name == "text").default)


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.defs = load()

    def test_curated_wins_and_places_on_object_info(self):
        beta = self.defs.get("Beta")
        self.assertEqual([w.name for w in beta.widgets], ["steps", "extra"])
        self.assertEqual(beta.widgets[0].kind, WidgetKind.VALUE)
        self.assertEqual(beta.widgets[0].default, 20)
        self.assertEqual(beta.widgets[1].kind, WidgetKind.EXTRA)
        self.assertEqual((beta.pack.cnr_id, beta.pack.ver), ("pack-b", "1.0.1"))
        self.assertEqual([o.type for o in beta.outputs], ["IMAGE"])
        self.assertTrue(beta.names_complete)

    def test_null_slot_means_names_incomplete(self):
        gamma = self.defs.get("Gamma")
        self.assertEqual([w.name for w in gamma.widgets], [None, "w"])
        self.assertTrue(gamma.layout_complete)
        self.assertFalse(gamma.names_complete)
        self.assertEqual((gamma.inputs, gamma.outputs), ((), ()))

    def test_bad_template_is_refused(self):
        bad = {"format": 1, "templates": [{"type": "X", "source": "guessed", "widgets": []}]}
        with self.assertRaises(CwcError) as caught:
            load(templates=bad)
        self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.defs = load()

    def test_closest(self):
        self.assertEqual(self.defs.closest("alpha")[0], "Alpha")
        self.assertEqual(self.defs.closest("Alpah", n=1), ["Alpha"])
        self.assertEqual(len(self.defs.closest("zzzz")), 3)
        self.assertEqual(self.defs.closest("Alpah"), self.defs.closest("Alpah"))

    def test_search(self):
        self.assertEqual([n.type for n in self.defs.search("alpha")], ["Alpha"])  # type name
        self.assertEqual([n.type for n in self.defs.search("THING")], ["Beta"])  # display name
        self.assertEqual([n.type for n in self.defs.search("testing/")], ["Alpha", "Beta"])  # category
        self.assertEqual(self.defs.search("no such word"), [])

    def test_user_object_info_overrides_core(self):
        core = NodeDefs.load()
        name = next(n for n in core if core.tier_of(n) is Tier.TRUTH)
        replaced = {name: {"input": {"required": {"only": ["INT", {"default": 1}]}}, "output": []}}
        defs = NodeDefs.load(object_info=replaced, truth=EMPTY_TRUTH)
        self.assertEqual([i.name for i in defs.get(name).inputs], ["only"])
        self.assertEqual(defs.tier_of(name), Tier.DERIVED)

    def test_bundled_truth_skips_a_user_entry_from_another_version(self):
        # The bundled truth fits the bundled core version only: a user entry whose ports
        # changed must fall back to its own derived layout.
        core = NodeDefs.load()
        name = next(n for n in core if core.tier_of(n) is Tier.TRUTH)
        replaced = {name: {"input": {"required": {"only": ["INT", {"default": 1}]}}, "output": []}}
        self.assertEqual(NodeDefs.load(object_info=replaced).tier_of(name), Tier.DERIVED)

    def test_bundled_truth_still_fits_an_identical_user_entry(self):
        core = NodeDefs.load()
        name = next(n for n in core if core.tier_of(n) is Tier.TRUTH and core.get(n).layout_complete)
        raw = read_bytes("core_nodedefs.json")
        entry = json.loads(raw)["nodes"][name]
        self.assertEqual(NodeDefs.load(object_info={name: entry}).tier_of(name), Tier.TRUTH)

    def test_malformed_entry_is_refused(self):
        with self.assertRaises(CwcError) as caught:
            load(info={"Bad": {"input": {"required": {"a": "INT"}}}})
        self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
        self.assertEqual(caught.exception.findings[0].code.value, "D1")

    def test_every_malformed_shape_is_a_d1_that_names_the_type_and_the_problem(self):
        # Each entry used to end in a TypeError or KeyError traceback.
        cases = {
            "input_order.required is not a list of names": {
                "input": {"required": {"a": ["INT", {}]}},
                "input_order": {"required": [["a"]]},
                "output": [],
            },
            "input_order.optional is not a list of names": {
                "input": {"required": {"a": ["INT", {}]}},
                "input_order": {"optional": "a"},
                "output": [],
            },
            "output_name is not a list": {"output": ["INT"], "output_name": {"k": "x"}},
            "output_is_list is not a list": {"output": ["INT"], "output_is_list": {"k": True}},
            "max is [3], not a number": {
                "input": {"required": {"g": ["COMFY_AUTOGROW_V3", {"template": {"input": {"required": {"x": ["IMAGE", {}]}}, "max": [3]}}]}},
                "output": [],
            },
        }
        for problem, entry in cases.items():
            with self.subTest(problem), self.assertRaises(CwcError) as caught:
                load(info={"Foo": entry})
            self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
            [finding] = caught.exception.findings
            self.assertEqual(finding.code.value, "D1")
            self.assertIn("'Foo'", finding.message)
            self.assertIn(problem, finding.message)

    def test_an_unanticipated_shape_is_still_a_d1(self):
        with mock.patch("cwc.nodedefs.registry.from_object_info", side_effect=TypeError("odd")):
            with self.assertRaises(CwcError) as caught:
                load(info={"Foo": {}})
        self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
        self.assertIn("TypeError: odd", caught.exception.findings[0].message)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/api/object_info":
            body = json.dumps(INFO).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


class SourceTest(unittest.TestCase):
    def test_file_plain_and_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "oi.json"
            plain.write_text(json.dumps(INFO), encoding="utf-8")
            packed = Path(tmp) / "oi.json.gz"
            packed.write_bytes(gzip.compress(json.dumps(INFO).encode("utf-8")))
            self.assertEqual(read_source(str(plain)), INFO)
            self.assertEqual(read_source(str(packed)), INFO)

    def test_missing_or_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.json"
            broken.write_text("{", encoding="utf-8")
            for source in (str(Path(tmp) / "absent.json"), str(broken)):
                with self.subTest(source=source), self.assertRaises(CwcError) as caught:
                    read_source(source)
                self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)

    def test_non_finite_numbers_are_refused_like_the_frontend_refuses_them(self):
        # Python reads Infinity and NaN, JSON.parse does not, and the canonical writer cannot write them.
        with tempfile.TemporaryDirectory() as tmp:
            for constant in ("Infinity", "-Infinity", "NaN"):
                path = Path(tmp) / "oi.json"
                path.write_text('{"T": {"input": {"required": {"f": ["FLOAT", {"max": %s}]}}, "output": []}}' % constant, encoding="utf-8")
                with self.subTest(constant), self.assertRaises(CwcError) as caught:
                    read_source(str(path))
                self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
                [finding] = caught.exception.findings
                self.assertEqual(finding.code.value, "D1")
                self.assertIn(constant, finding.message)

    def assertD1(self, source: str, text: str = ""):
        with self.assertRaises(CwcError) as caught:
            read_source(source)
        self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
        [finding] = caught.exception.findings
        self.assertEqual(finding.code.value, "D1")
        self.assertIn(text, finding.message)

    def test_a_file_past_the_size_limit_is_d1(self):
        raw = json.dumps(INFO).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oi.json"
            path.write_bytes(raw)
            with mock.patch.object(registry, "MAX_JSON_BYTES", len(raw)):
                self.assertEqual(read_source(str(path)), INFO)  # exactly at the limit
            with mock.patch.object(registry, "MAX_JSON_BYTES", len(raw) - 1):
                self.assertD1(str(path), "limit")

    def test_gzip_is_inflated_within_the_limit(self):
        raw = json.dumps(INFO).encode("utf-8")
        bomb = gzip.compress(b'{"T": "' + b"a" * 10**6 + b'"}')
        self.assertLess(len(bomb), 2000)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oi.json.gz"
            path.write_bytes(bomb)
            with mock.patch.object(registry, "MAX_JSON_BYTES", 5000):
                self.assertD1(str(path), "inflates past the limit")
            path.write_bytes(gzip.compress(raw))
            with mock.patch.object(registry, "MAX_JSON_BYTES", len(raw)):
                self.assertEqual(read_source(str(path)), INFO)
            with mock.patch.object(registry, "MAX_JSON_BYTES", len(raw) - 1):
                self.assertD1(str(path), "inflates past the limit")

    def test_the_limit_counts_all_gzip_members(self):
        first, second = b'{"A": "' + b"x" * 3000, b'y"}'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oi.json.gz"
            path.write_bytes(gzip.compress(first) + b"\0\0" + gzip.compress(second) + b"\0")
            with mock.patch.object(registry, "MAX_JSON_BYTES", len(first) + len(second)):
                self.assertEqual(read_source(str(path)), {"A": "x" * 3000 + "y"})
            with mock.patch.object(registry, "MAX_JSON_BYTES", len(first) + len(second) - 1):
                self.assertD1(str(path), "inflates past the limit")

    def test_a_cut_or_padded_gzip_file_is_d1(self):
        packed = gzip.compress(json.dumps(INFO).encode("utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in [("cut", packed[:-9]), ("junk", packed + b"junk"), ("header", b"\x1f\x8b" + b"no")]:
                path = Path(tmp) / f"{name}.gz"
                path.write_bytes(data)
                with self.subTest(name):
                    self.assertD1(str(path), "not valid JSON")

    def nested_document(self, levels: int) -> bytes:
        """``levels`` objects inside one another, the innermost empty."""
        return b'{"T":' * (levels - 1) + b"{}" + b"}" * (levels - 1)

    def test_a_document_past_the_depth_limit_never_reaches_the_parser(self):
        # The parser's C scanner can overrun the stack on a deep document: the depth is measured before it.
        deep = self.nested_document(200000)
        stub = types.SimpleNamespace(loads=mock.Mock(side_effect=AssertionError("the parser was called")))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(registry, "json", stub):
            for name, data in [("oi.json", deep), ("oi.json.gz", gzip.compress(deep))]:
                path = Path(tmp) / name
                path.write_bytes(data)
                with self.subTest(name):
                    self.assertD1(str(path), f"deeper than the limit of {limits.MAX_DEPTH} levels")
        stub.loads.assert_not_called()

    def test_a_document_exactly_at_the_depth_limit_is_read(self):
        at_limit = self.nested_document(limits.MAX_DEPTH)
        expected = json.loads(at_limit)
        self.assertEqual(json_depth(expected), limits.MAX_DEPTH)
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in [("oi.json", at_limit), ("oi.json.gz", gzip.compress(at_limit))]:
                path = Path(tmp) / name
                path.write_bytes(data)
                with self.subTest(name):
                    self.assertEqual(read_source(str(path)), expected)
            path = Path(tmp) / "over.json"
            path.write_bytes(self.nested_document(limits.MAX_DEPTH + 1))
            self.assertD1(str(path), "deeper than the limit")

    def test_the_bundled_node_definitions_fit_the_depth_limit(self):
        # The deepest real document there is: a limit set below it would refuse every run.
        bundled = ROOT / "src" / "cwc" / "data" / registry.CORE_FILE
        doc = read_source(str(bundled))
        self.assertEqual(doc, json.loads(bundled.read_bytes()))
        self.assertLessEqual(json_depth(doc), limits.MAX_DEPTH)

    def test_a_fixture_shaped_like_a_server_reply_reads_as_it_always_did(self):
        path = FIXTURES / "nodedefs" / "core_sample.json"
        self.assertEqual(read_source(str(path)), json.loads(path.read_bytes()))

    def test_a_response_past_the_size_limit_is_d1(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                with mock.patch.object(registry, "MAX_JSON_BYTES", 100):
                    self.assertD1(base, "limit")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_url(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                self.assertEqual(read_source(base + "/"), INFO)
                with self.assertRaises(CwcError) as caught:
                    read_source(base + "/missing")
            self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
