"""Checks on src/cwc/data/core_truth.json, the snapshot written by tools/oracle (docs/ARCHITECTURE.md).

The snapshot is public data. Besides its shape, these tests make sure it holds no path,
model name or media name from the machine that produced it.
"""

import json
import re
import unittest

from tests import support

TRUTH = support.ROOT / "src" / "cwc" / "data" / "core_truth.json"
NODEDEFS = support.ROOT / "src" / "cwc" / "data" / "core_nodedefs.json"

# Types the frontend registers itself; they are not in the server's /object_info.
FRONTEND_ONLY = {"Note", "MarkdownNote", "PrimitiveNode", "Reroute"}

FILE_EXTENSION = re.compile(
    r"\.(safetensors|ckpt|pt|pth|bin|gguf|onnx|npz|sft|latent|png|jpe?g|webp|gif|bmp|tiff?|"
    r"mp4|webm|mov|mkv|avi|wav|mp3|flac|ogg|m4a|obj|glb|ply|json|txt|ya?ml|csv)\b",
    re.IGNORECASE,
)
ABSOLUTE_PATH = re.compile(r"^(?:/|~|[A-Za-z]:[\\/]|\\\\)|/Users/|/home/|/private/|/var/")


def _strings(value, where):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key, f"{where}/<key>")
            yield from _strings(item, f"{where}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]")


@unittest.skipUnless(TRUTH.exists(), "src/cwc/data/core_truth.json has not been generated")
class CoreTruthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = TRUTH.read_bytes()
        cls.text = cls.blob.decode("ascii")
        cls.snapshot = json.loads(cls.text)
        cls.nodes = cls.snapshot["nodes"]

    def test_header(self):
        s = self.snapshot
        self.assertEqual(set(s), {"format", "frontend_version", "comfyui_version", "nodes", "withheld"})
        self.assertEqual(s["format"], 1)
        for key in ("frontend_version", "comfyui_version"):
            self.assertRegex(s[key], r"^\d+\.\d+\.\d+$")
        self.assertGreater(len(self.nodes), 0)

    def test_entry_shape(self):
        for type_, node in self.nodes.items():
            with self.subTest(type=type_):
                self.assertEqual(set(node), {"inputs", "outputs", "widgets", "size"})
                for slot in node["inputs"]:
                    self.assertEqual(set(slot), {"name", "type", "widget", "shape"})
                    self.assertIsInstance(slot["name"], str)
                    self.assertIsInstance(slot["type"], str)
                    self.assertIsInstance(slot["widget"], (str, type(None)))
                    self.assertIsInstance(slot["shape"], (int, type(None)))
                for slot in node["outputs"]:
                    self.assertEqual(set(slot), {"name", "type", "is_list"})
                    self.assertIsInstance(slot["name"], str)
                    self.assertIsInstance(slot["type"], str)
                    self.assertIsInstance(slot["is_list"], bool)
                for widget in node["widgets"]:
                    self.assertEqual(set(widget), {"name", "type", "value", "to_prompt"})
                    self.assertIsInstance(widget["name"], str)
                    self.assertIsInstance(widget["type"], str)
                    self.assertIsInstance(widget["to_prompt"], bool)
                self.assertEqual(set(node["size"]), {"classic", "vue"})
                classic = node["size"]["classic"]
                self.assertEqual(len(classic), 2)
                for side in classic:
                    self.assertIsInstance(side, (int, float))
                    self.assertGreater(side, 0)
                self.assertIsNone(node["size"]["vue"])

    def test_only_core_types(self):
        self.assertFalse(FRONTEND_ONLY & set(self.nodes))
        if NODEDEFS.exists():
            core = json.loads(NODEDEFS.read_bytes())["nodes"]
            self.assertFalse(set(self.nodes) - set(core))

    def test_no_machine_specific_strings(self):
        for where, text in _strings(self.nodes, ""):
            with self.subTest(where=where):
                self.assertIsNone(ABSOLUTE_PATH.search(text), "path-like string in snapshot")
                self.assertIsNone(FILE_EXTENSION.search(text), "file name in snapshot")

    def test_combo_values_are_not_file_lists(self):
        # The snapshot keeps only the current value of a combo widget, never its option list.
        for type_, node in self.nodes.items():
            for widget in node["widgets"]:
                if widget["type"] == "combo":
                    with self.subTest(type=type_, widget=widget["name"]):
                        self.assertIsInstance(widget["value"], (str, int, float, bool, type(None)))

    def test_canonical_bytes(self):
        # Plain text: ASCII only, indent 1, sorted keys, one trailing newline.
        self.assertTrue(self.blob.isascii())
        self.assertTrue(self.text.endswith("}\n") and not self.text.endswith("\n\n"))
        canonical = json.dumps(self.snapshot, sort_keys=True, indent=1, ensure_ascii=True) + "\n"
        self.assertEqual(self.text, canonical)


if __name__ == "__main__":
    unittest.main()
