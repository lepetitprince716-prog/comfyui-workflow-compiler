"""Dynamic prompts: the port of ``processDynamicPrompt``, its inverse, and where they are applied.

``tests/fixtures/prompt/dynamic_prompts.json`` holds texts a real frontend 1.59.1 exported
(``tools/oracle/record_dynamic_prompts.py``); the port is held against it offline.
"""

import json
import random
import unittest
from pathlib import Path

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.canvas.model import Canvas
from cwc.instantiate import instantiate
from cwc.nodedefs.derive import from_object_info, widget_layout
from cwc.nodedefs.registry import NodeDefs
from cwc.nodedefs.types import Tier
from cwc.normalize import normalize
from cwc.prompt.dynamic import DynamicText, dynamic_widgets, escape, process
from cwc.prompt.from_canvas import canvas_to_prompt
from cwc.prompt.model import prompt_diff
from cwc.report import Code, CwcError, Severity
from tests.fixtures.instantiate import graphs
from tests.fixtures.normalize.registry import load_doc

FIXTURE = Path(__file__).parent / "fixtures" / "prompt" / "dynamic_prompts.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
DEFS = graphs.DEFS


def _encode_canvas(text, *, pushed=None):
    """One text encoder holding ``text``; with ``pushed``, a primitive in front of it pushes that value."""
    raw = {
        "id": 1,
        "type": "CLIPTextEncode",
        "mode": 0,
        "inputs": [{"name": "clip", "type": "CLIP", "link": None}, {"name": "text", "type": "STRING", "widget": {"name": "text"}, "link": None}],
        "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": None}],
        "widgets_values": [text],
    }
    nodes = [raw]
    links = []
    if pushed is not None:
        raw["inputs"][1]["link"] = 1
        nodes.append(
            {
                "id": 2,
                "type": "PrimitiveNode",
                "mode": 0,
                "inputs": [],
                "outputs": [{"name": "STRING", "type": "STRING", "links": [1]}],
                "widgets_values": [pushed],
            }
        )
        links.append([1, 2, 0, 1, 1, "STRING"])
    canvas = Canvas.empty()
    canvas.raw["nodes"] = nodes
    canvas.raw["links"] = links
    return canvas


class RecordedExportTest(unittest.TestCase):
    def test_the_fixture_covers_what_the_port_has_to_get_right(self):
        values = [case["value"] for case in CASES]
        self.assertGreaterEqual(len(values), 30)
        for needle in ("/*", "\n", "\r", " ", " ", "//", "\\{", "\\|", "{}", "{/}", "\U0001f600"):
            self.assertTrue(any(needle in value for value in values), needle)
        self.assertTrue(any(value.endswith("\\") for value in values))

    def test_process_gives_what_the_frontend_exported(self):
        for case in CASES:
            with self.subTest(case["value"]):
                self.assertEqual(process(case["value"]), DynamicText(case["exported"], False))


class ProcessTest(unittest.TestCase):
    def test_a_choice_block_takes_its_first_option_and_is_random(self):
        self.assertEqual(process("a {x|y} b"), DynamicText("a x b", True))
        self.assertEqual(process("{x|{y|z}}"), DynamicText("x", True))

    def test_equal_options_are_not_random(self):
        self.assertEqual(process("{x|x}"), DynamicText("x", False))

    def test_an_option_that_is_itself_random_makes_the_text_random(self):
        self.assertTrue(process("{{a|b}|{a|b}}").random)
        self.assertEqual(process("{{a|b}|{a|b}}").text, "a")

    def test_options_that_process_to_the_same_text_are_not_random(self):
        self.assertFalse(process("{a /* c */|a }").random)
        self.assertFalse(process("{\\{|\\{}").random)

    def test_the_unterminated_block_keeps_its_option(self):
        self.assertEqual(process("a {b|b"), DynamicText("a b", False))

    def test_nesting_beyond_the_recursion_limit_raises_value_error(self):
        with self.assertRaises(ValueError):
            process("{" * 5000 + "x")


class EscapeTest(unittest.TestCase):
    def test_a_plain_text_is_unchanged(self):
        self.assertEqual(escape("a cat, 1girl (masterpiece:1.2)"), "a cat, 1girl (masterpiece:1.2)")

    def test_braces_and_pipes_get_a_backslash(self):
        self.assertEqual(escape("{a|b}"), "\\{a\\|b\\}")

    def test_a_slash_that_would_start_a_comment_becomes_a_block(self):
        self.assertEqual(escape("https://x"), "https:{/}/x")
        self.assertEqual(escape("a /* b */"), "a {/}* b */")
        self.assertEqual(escape("a/b and */"), "a/b and */")

    def test_a_single_trailing_backslash_has_no_spelling(self):
        self.assertIsNone(escape("trailing\\"))
        self.assertIsNone(escape("{a\\"))
        self.assertEqual(escape("two\\\\"), "two\\\\")

    def test_every_fixture_text_round_trips_or_has_no_spelling(self):
        for case in CASES:
            self._round_trips(case["value"])
            self._round_trips(case["exported"])

    def test_generated_texts_round_trip_or_have_no_spelling(self):
        rng = random.Random(7)
        alphabet = list("ab {}|\\/*\n\r") + [" ", "\U0001f600", "//", "/*", "*/", "\\{"]
        spelled = 0
        for _ in range(3000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
            spelled += self._round_trips(text) is not None
        self.assertGreater(spelled, 2000)

    def test_only_a_trailing_backslash_run_of_odd_length_has_no_spelling(self):
        rng = random.Random(11)
        alphabet = list("ab {}|\\/*\n")
        for _ in range(3000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 10)))
            run = len(text) - len(text.rstrip("\\"))
            self.assertEqual(escape(text) is None, run % 2 == 1, repr(text))

    def _round_trips(self, text):
        spelled = escape(text)
        if spelled is not None:
            self.assertEqual(process(spelled), DynamicText(text, False), repr(text))
        return spelled


class FlaggedWidgetsTest(unittest.TestCase):
    def test_the_flag_survives_the_truth_merge(self):
        nodedef = DEFS.get("CLIPTextEncode")
        self.assertIs(nodedef.tier, Tier.TRUTH)
        self.assertEqual(dynamic_widgets(nodedef, widget_layout(nodedef)), frozenset({"text"}))

    def test_a_node_without_flagged_inputs_has_none(self):
        nodedef = DEFS.get("KSampler")
        self.assertEqual(dynamic_widgets(nodedef, widget_layout(nodedef)), frozenset())

    def test_every_type_with_a_flagged_string_input_keeps_it(self):
        found = 0
        for name in DEFS:
            nodedef = DEFS.get(name)
            flagged = {
                spec.name
                for spec in nodedef.inputs
                if spec.widget and spec.type == "STRING" and spec.options.get("dynamicPrompts") is True
            }
            found += bool(flagged)
            self.assertEqual(dynamic_widgets(nodedef, widget_layout(nodedef)), flagged, name)
        self.assertGreater(found, 30)


    def test_a_flagged_string_under_a_dynamic_combo_is_not_counted(self):
        # Read from frontend 1.59.1, not measured: the DynamicCombo setter re-creates its
        # sub-widgets on load, after Comfy.DynamicPrompts patched serializeValue in nodeCreated.
        flagged = ["STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}]
        info = {
            "input": {
                "required": {
                    "mode": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "a", "inputs": {"required": {"note": flagged}}}]}],
                    "text": flagged,
                }
            },
            "output": [],
        }
        nodedef = from_object_info("X", info)
        layout = widget_layout(nodedef, {"mode": "a"})
        self.assertIn("mode.note", [w.name for w in layout])
        self.assertEqual(dynamic_widgets(nodedef, layout), frozenset({"text"}))


class CanvasExportTest(unittest.TestCase):
    def _text(self, canvas):
        return canvas_to_prompt(canvas, DEFS)["1"]["inputs"]["text"]

    def test_a_flagged_widget_exports_the_processed_text(self):
        for case in CASES:
            with self.subTest(case["value"]):
                self.assertEqual(self._text(_encode_canvas(case["value"])), case["exported"])

    def test_a_value_pushed_by_a_primitive_is_processed_too(self):
        canvas = _encode_canvas("ignored", pushed="a /* note */ b https://x")
        self.assertEqual(self._text(canvas), "a  b https:")

    def test_a_random_block_exports_its_first_option(self):
        self.assertEqual(self._text(_encode_canvas("{red|blue} car")), "red car")

    def test_a_widget_without_the_flag_keeps_its_text(self):
        nodedef = DEFS.get("SaveImage")
        self.assertEqual(dynamic_widgets(nodedef, widget_layout(nodedef)), frozenset())
        canvas = Canvas.empty()
        canvas.raw["nodes"] = [
            {"id": 1, "type": "SaveImage", "mode": 0, "inputs": [], "outputs": [], "widgets_values": ["a /* b */ {c|d}"]}
        ]
        prompt = canvas_to_prompt(canvas, DEFS)
        self.assertEqual(prompt["1"]["inputs"]["filename_prefix"], "a /* b */ {c|d}")


class InstantiateTest(unittest.TestCase):
    def test_the_canvas_holds_the_escaped_text_and_exports_the_authored_one(self):
        authoring = graphs.build(graphs.dynamic_text())
        canvas = instantiate(authoring)
        encoders = [node for node in canvas.to_json()["nodes"] if node["type"] == "CLIPTextEncode"]
        self.assertEqual(len(encoders), 2)
        for node in encoders:
            written = node["widgets_values"][0]
            self.assertEqual(node["widgets_values_named"], {"text": written})
            self.assertEqual(process(written).random, False)
        self.assertEqual(prompt_diff(authoring.factory, canvas_to_prompt(canvas, DEFS)), [])

    def test_the_url_is_written_so_that_it_survives(self):
        doc = load_doc("txt2img.json")
        doc["pos"]["inputs"]["text"] = "see https://example.com end"
        authoring = graphs.build(doc)
        canvas = instantiate(authoring)
        node = next(n for n in canvas.to_json()["nodes"] if n["id"] == int(authoring.ids["pos"]))
        self.assertEqual(node["widgets_values"], ["see https:{/}/example.com end"])
        self.assertEqual(canvas_to_prompt(canvas, DEFS)[authoring.ids["pos"]]["inputs"]["text"], "see https://example.com end")


class NormalizeTest(unittest.TestCase):
    def _findings(self, text):
        doc = load_doc("txt2img.json")
        doc["pos"]["inputs"]["text"] = text
        try:
            _, findings = normalize(doc, DEFS, allow_unknown=False)
        except CwcError as error:
            findings = error.findings
        return [f for f in findings if f.code is Code.E5]

    def test_a_text_that_cannot_be_exported_is_an_e5_error_naming_node_and_input(self):
        findings = self._findings("trailing\\")
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.ERROR)
        self.assertIn("'pos'", findings[0].message)
        self.assertIn("'text'", findings[0].message)

    def test_a_text_with_markup_is_fine(self):
        self.assertEqual(self._findings("see https://example.com {a|b} /* c */ \\{"), [])

    def test_any_odd_run_of_trailing_backslashes_is_refused_and_the_text_says_so(self):
        for text in ("a\\", "a\\\\\\", "\\", "{a|b}\\\\\\\\\\"):
            with self.subTest(text=text):
                [finding] = self._findings(text)
                self.assertIn("odd number of backslashes", finding.message)
        for text in ("a\\\\", "a\\\\\\\\", "a\\b"):
            with self.subTest(text=text):
                self.assertEqual(self._findings(text), [])


def _custom_defs(default):
    info = {
        "input": {
            "required": {
                "clip": ["CLIP", {}],
                "text": ["STRING", {"multiline": True, "dynamicPrompts": True, "default": default}],
            }
        },
        "output": ["CONDITIONING"],
    }
    source = {"input": {"required": {}}, "output": ["STRING"]}
    return NodeDefs.load(object_info={"CustomEncode": info, "CustomString": source})


class DefaultTest(unittest.TestCase):
    """The default of a dynamic-prompt input is written to the canvas too, so it needs a spelling."""

    def _findings(self, default, mutate):
        doc = load_doc("txt2img.json")
        doc["pos"]["class_type"] = "CustomEncode"
        del doc["pos"]["inputs"]["text"]
        mutate(doc)
        try:
            _, findings = normalize(doc, _custom_defs(default), allow_unknown=False)
        except CwcError as error:
            findings = error.findings
        return [f for f in findings if f.severity is Severity.ERROR]

    def test_an_omitted_input_with_an_unspellable_default_is_e5_not_a_crash(self):
        [finding] = self._findings("tail\\", lambda doc: None)
        self.assertIs(finding.code, Code.E5)
        self.assertEqual(finding.nodes, ("pos",))
        self.assertIn("'text'", finding.message)
        self.assertIn("declared default", finding.message)
        self.assertIn("odd number of backslashes", finding.message)

    def test_a_linked_input_still_stores_its_default(self):
        def link(doc):
            doc["src"] = {"class_type": "CustomString", "inputs": {}}
            doc["pos"]["inputs"]["text"] = ["src", 0]

        [finding] = self._findings("tail\\", link)
        self.assertIs(finding.code, Code.E5)
        self.assertIn("declared default", finding.message)
        self.assertIn("is linked", finding.hint)

    def test_an_author_text_replaces_the_default_check(self):
        def write(doc):
            doc["pos"]["inputs"]["text"] = "fine"

        self.assertEqual(self._findings("tail\\", write), [])

    def test_a_default_that_has_a_spelling_passes_and_instantiate_writes_it(self):
        self.assertEqual(self._findings("a {b|c} https://x", lambda doc: None), [])
        doc = load_doc("txt2img.json")
        doc["pos"]["class_type"] = "CustomEncode"
        del doc["pos"]["inputs"]["text"]
        defs = _custom_defs("see https://x")
        authoring, _ = normalize(doc, defs, allow_unknown=False)
        canvas = instantiate(authoring)
        node = next(n for n in canvas.to_json()["nodes"] if n["id"] == int(authoring.ids["pos"]))
        self.assertEqual(node["widgets_values"], ["see https:{/}/x"])


if __name__ == "__main__":
    unittest.main()
