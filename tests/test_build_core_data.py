"""tools/build_core_data.py: what the bundled core data may hold, and that builds repeat exactly."""

import contextlib
import gzip
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests import support

_SPEC = importlib.util.spec_from_file_location("build_core_data", support.ROOT / "tools" / "build_core_data.py")
build_core_data = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_core_data)

DATA = support.ROOT / "src" / "cwc" / "data"
LONG = "x" * (build_core_data.MAX_DEFAULT_CHARS + 1)
EDGE = "y" * build_core_data.MAX_DEFAULT_CHARS
TEXT_KEYS = build_core_data.ENTRY_TEXT_KEYS | build_core_data.OPTION_TEXT_KEYS
HOME_PATH = re.compile(r"/Users/|/home/|/private/|[A-Za-z]:\\\\Users\\\\")


def object_info():
    return {
        "Core": {
            "input": {
                "required": {
                    "zeta": ["STRING", {"default": LONG, "tooltip": "t", "multiline": True}],
                    "alpha": ["STRING", {"default": EDGE}],
                    "mode": ["COMFY_DYNAMICCOMBO_V3", {"tooltip": "t", "options": [
                        {"key": "a", "description": "d", "inputs": {"required": {"code": ["STRING", {"default": LONG}]}}},
                    ]}],
                },
                "optional": {
                    "m": ["MODEL", {"tooltip": "t"}],
                    "description": ["STRING", {"tooltip": "an input may be called like a text key"}],
                    "group": ["COMFY_DYNAMICGROUP_V3", {"template": {"required": {"s": ["FLOAT", {"tooltip": "t"}]}}}],
                },
            },
            "input_order": {"required": ["zeta", "alpha", "mode"], "optional": ["m"]},
            "output": ["MODEL"],
            "output_tooltips": ["t"],
            "description": "d",
            "search_aliases": ["s"],
            "help": "h",
            "price_badge": {"x": 1},
            "python_module": "nodes",
            "category": "c",
        },
        "Extra": {"input": {}, "output": [], "python_module": "comfy_extras.nodes_x"},
        "Api": {"input": {}, "output": [], "python_module": "comfy_api_nodes.nodes_x"},
        "Custom": {"input": {}, "output": [], "python_module": "custom_nodes.pack"},
        "Lookalike": {"input": {}, "output": [], "python_module": "nodes_custom"},
    }


def truth():
    widget = {"name": "zeta", "type": "text", "value": LONG, "to_prompt": True}
    entry = {"inputs": [], "outputs": [], "widgets": [widget, {"name": "alpha", "type": "text", "value": EDGE, "to_prompt": True}],
             "size": {"classic": [1, 1], "vue": None}}
    return {"format": 1, "frontend_version": "1.0.0", "comfyui_version": "0.1.0",
            "nodes": {"Core": entry, "Custom": json.loads(json.dumps(entry))}}


def parse(blob):
    return json.loads(blob.decode("ascii"))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.nodedefs_blob, self.truth_blob = build_core_data.build(object_info(), truth())
        self.nodedefs = parse(self.nodedefs_blob)
        self.truth = parse(self.truth_blob)

    def test_only_core_modules(self):
        self.assertEqual(list(self.nodedefs["nodes"]), ["Api", "Core", "Extra"])
        self.assertEqual(list(self.truth["nodes"]), ["Core"])

    def test_explanatory_text_is_removed(self):
        core = self.nodedefs["nodes"]["Core"]
        self.assertFalse(TEXT_KEYS & set(core))
        self.assertEqual(list(_text_keys(core["input"])), [])
        self.assertTrue(core["input"]["required"]["zeta"][1]["multiline"])
        self.assertEqual(core["input"]["optional"]["description"], ["STRING", {}])
        self.assertEqual(core["input"]["optional"]["group"][1]["template"]["required"]["s"], ["FLOAT", {}])

    def test_long_defaults_are_withheld(self):
        required = self.nodedefs["nodes"]["Core"]["input"]["required"]
        self.assertIsNone(required["zeta"][1]["default"])
        self.assertEqual(required["alpha"][1]["default"], EDGE)  # exactly the limit stays
        self.assertIsNone(required["mode"][1]["options"][0]["inputs"]["required"]["code"][1]["default"])
        self.assertEqual(self.nodedefs["withheld"], [["Core", "mode.code"], ["Core", "zeta"]])
        widgets = self.truth["nodes"]["Core"]["widgets"]
        self.assertEqual([w["value"] for w in widgets], [None, EDGE])
        self.assertEqual(self.truth["withheld"], [["Core", "zeta"]])

    def test_header(self):
        self.assertEqual(self.nodedefs["format"], 1)
        self.assertEqual(self.nodedefs["comfyui"], "0.1.0")
        self.assertEqual(set(self.truth), {"format", "frontend_version", "comfyui_version", "nodes", "withheld"})

    def test_entry_key_order_is_kept(self):
        # JavaScript object order is input order: it must survive the build.
        required = self.nodedefs["nodes"]["Core"]["input"]["required"]
        self.assertEqual(list(required), ["zeta", "alpha", "mode"])

    def test_deterministic_and_idempotent(self):
        again = build_core_data.build(object_info(), truth())
        self.assertEqual(again, (self.nodedefs_blob, self.truth_blob))
        rebuilt = build_core_data.build(self.nodedefs, self.truth)
        self.assertEqual(rebuilt, (self.nodedefs_blob, self.truth_blob))
        for blob in (self.nodedefs_blob, self.truth_blob):
            self.assertEqual(blob[:2], b"{\n")  # plain text, indent 1

    def test_main_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "oi.json").write_text(json.dumps(object_info()), encoding="utf-8")
            (src / "truth.json.gz").write_bytes(gzip.compress(json.dumps(truth()).encode()))
            out = src / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = build_core_data.main([str(src / "oi.json"), str(src / "truth.json.gz"), "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual((out / "core_nodedefs.json").read_bytes(), self.nodedefs_blob)
            self.assertEqual((out / "core_truth.json").read_bytes(), self.truth_blob)

    def test_bad_truth_is_refused(self):
        with self.assertRaises(ValueError):
            build_core_data.build(object_info(), {"format": 1, "nodes": {}})


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _text_keys(value):
    """Explanatory keys inside an ``input`` structure (an input named like one is a list)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in TEXT_KEYS and not isinstance(item, list):
                yield key
            yield from _text_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _text_keys(item)


def _defaults(value):
    """Every ``default`` value inside an /object_info structure."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "default":
                yield item
            yield from _defaults(item)
    elif isinstance(value, list):
        for item in value:
            yield from _defaults(item)


class BundledDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodedefs = parse((DATA / "core_nodedefs.json").read_bytes())
        cls.truth = parse((DATA / "core_truth.json").read_bytes())

    def test_core_only(self):
        for name, entry in self.nodedefs["nodes"].items():
            with self.subTest(type=name):
                self.assertTrue(build_core_data.is_core(entry))
        self.assertLessEqual(set(self.truth["nodes"]), set(self.nodedefs["nodes"]))

    def test_no_explanatory_fields(self):
        for name, entry in self.nodedefs["nodes"].items():
            self.assertFalse(build_core_data.ENTRY_TEXT_KEYS & set(entry), name)
            self.assertEqual(list(_text_keys(entry["input"])), [], name)

    def test_no_long_defaults(self):
        limit = build_core_data.MAX_DEFAULT_CHARS
        for value in _defaults(self.nodedefs["nodes"]):
            self.assertFalse(isinstance(value, str) and len(value) > limit)
        for entry in self.truth["nodes"].values():
            for widget in entry["widgets"]:
                self.assertFalse(isinstance(widget["value"], str) and len(widget["value"]) > limit)
        self.assertTrue(self.nodedefs["withheld"])
        self.assertTrue(self.truth["withheld"])

    def test_no_home_paths(self):
        for doc in (self.nodedefs, self.truth):
            for text in _strings(doc):
                self.assertIsNone(HOME_PATH.search(text))

    def test_bundled_files_are_their_own_rebuild(self):
        nodedefs_blob, truth_blob = build_core_data.build(self.nodedefs, self.truth)
        self.assertEqual(nodedefs_blob, (DATA / "core_nodedefs.json").read_bytes())
        self.assertEqual(truth_blob, (DATA / "core_truth.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
