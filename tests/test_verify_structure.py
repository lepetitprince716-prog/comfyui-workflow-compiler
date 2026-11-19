import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.report import Code, Mode, Severity
from cwc.verify import verify_canvas, verify_compile
from tests.fixtures.prompt.build import CanvasBuilder
from tests.fixtures.verify.canvases import DEFS, authoring, node, txt2img


def check(canvas, mode=Mode.COMPILE):
    return verify_canvas(canvas, DEFS, mode=mode)


def found(report, code):
    return [f for f in report.findings if f.code is code]


class CleanCanvasTest(unittest.TestCase):
    def test_example_has_no_structure_finding(self):
        for mode in Mode:
            report = check(txt2img(), mode)
            self.assertEqual([f for f in report.findings if f.code.value.startswith("S")], [], mode)


class VersionTest(unittest.TestCase):
    def test_other_version_is_an_error_in_every_mode(self):
        canvas = txt2img()
        canvas.raw["version"] = 1
        for mode in Mode:
            [finding] = found(check(canvas, mode), Code.S1)
            self.assertIs(finding.severity, Severity.ERROR)

    def test_boolean_version_is_refused(self):
        canvas = txt2img()
        canvas.raw["version"] = True
        self.assertTrue(found(check(canvas), Code.S1))


class FieldsTest(unittest.TestCase):
    def test_missing_order_and_flags(self):
        canvas = txt2img()
        del node(canvas, 5)["order"]
        del node(canvas, 5)["flags"]
        [finding] = found(check(canvas, Mode.BEAUTIFY), Code.S2)
        self.assertEqual(finding.nodes, ("5",))
        self.assertEqual(finding.data["missing"], ["flags", "order"])
        self.assertIs(finding.severity, Severity.ERROR)


class UniqueIdsTest(unittest.TestCase):
    def test_duplicate_node_id_counts_string_and_int_alike(self):
        canvas = txt2img()
        node(canvas, 5)["id"] = "4"
        [finding] = found(check(canvas), Code.S3)
        self.assertEqual(finding.nodes, ("4",))

    def test_duplicate_link_id(self):
        canvas = txt2img()
        canvas.raw["links"].append(list(canvas.raw["links"][0]))
        [finding] = found(check(canvas), Code.S3)
        self.assertEqual(finding.data, {"link": 1})

    def test_unique_ids_pass(self):
        self.assertEqual(found(check(txt2img()), Code.S3), [])


class LinksTest(unittest.TestCase):
    def test_missing_endpoint_blocks_in_every_mode(self):
        canvas = txt2img()
        canvas.raw["links"][0][1] = 99
        for mode in Mode:
            findings = found(check(canvas, mode), Code.S4)
            self.assertTrue(any("origin node 99 does not exist" in f.message and f.severity is Severity.ERROR for f in findings))

    def test_slot_out_of_range(self):
        canvas = txt2img()
        canvas.raw["links"][0][4] = 9
        self.assertTrue(any("has no input 9" in f.message for f in found(check(canvas, Mode.BEAUTIFY), Code.S4)))

    def test_target_input_disagrees(self):
        canvas = txt2img()
        node(canvas, 9)["inputs"][0]["link"] = None  # link 12 still lists it
        findings = found(check(canvas), Code.S4)
        self.assertTrue(any("does not name it" in f.message for f in findings))
        self.assertTrue(all(f.severity is Severity.ERROR for f in findings))
        lenient = found(check(canvas, Mode.BEAUTIFY), Code.S4)
        self.assertTrue(lenient and all(f.severity is Severity.WARNING for f in lenient))

    def test_output_list_disagrees(self):
        canvas = txt2img()
        node(canvas, 8)["outputs"][0]["links"] = []
        self.assertTrue(any("does not list it" in f.message for f in found(check(canvas), Code.S4)))

    def test_input_names_unknown_link(self):
        canvas = txt2img()
        node(canvas, 5)["inputs"][0]["link"] = 77
        self.assertTrue(any("names link 77" in f.message for f in found(check(canvas), Code.S4)))

    def test_output_lists_link_of_another_node(self):
        canvas = txt2img()
        node(canvas, 5)["outputs"][0]["links"].append(1)
        self.assertTrue(any("lists link 1, which starts elsewhere" in f.message for f in found(check(canvas), Code.S4)))

    def test_consistent_tables_pass(self):
        self.assertEqual(found(check(txt2img()), Code.S4), [])

    def test_broken_links_skip_prompt_checks(self):
        canvas = txt2img()
        canvas.raw["links"][0][1] = 99
        report = verify_compile(canvas, authoring(), DEFS)
        self.assertEqual(report.metrics["skipped"], ["B1", "B2", "Q1", "Q2"])
        self.assertFalse([f for f in report.findings if f.code in (Code.Q1, Code.Q2)])


class LastIdsTest(unittest.TestCase):
    def test_stale_last_link_id(self):
        canvas = txt2img()
        canvas.raw["last_link_id"] = 40
        [finding] = found(check(canvas), Code.S5)
        self.assertIs(finding.severity, Severity.ERROR)
        self.assertEqual(finding.data, {"field": "last_link_id", "expected": 12})
        [lenient] = found(check(canvas, Mode.BEAUTIFY), Code.S5)
        self.assertIs(lenient.severity, Severity.WARNING)

    def test_missing_last_node_id(self):
        canvas = txt2img()
        del canvas.raw["last_node_id"]
        self.assertEqual([f.data["field"] for f in found(check(canvas), Code.S5)], ["last_node_id"])

    def test_exact_ids_pass(self):
        self.assertEqual(found(check(txt2img()), Code.S5), [])

    def test_digits_in_other_scripts_are_not_numeric_ids(self):
        canvas = txt2img()
        canvas.raw["nodes"][0]["id"] = "²"  # ingest refuses it; a canvas built in code must not crash
        numeric = [int(n["id"]) for n in canvas.raw["nodes"] if str(n["id"]).isascii() and str(n["id"]).isdigit()]
        canvas.raw["last_node_id"] = max(numeric)
        self.assertEqual(found(check(canvas), Code.S5), [])

    def test_empty_canvas_expects_zero(self):
        canvas = CanvasBuilder(DEFS).canvas()
        self.assertEqual(found(check(canvas), Code.S5), [])


class NamedWidgetsTest(unittest.TestCase):
    def test_matching_table_passes(self):
        canvas = txt2img()
        node(canvas, 6)["widgets_values_named"] = {"seed": 7, "control_after_generate": "fixed", "steps": 20.0}
        self.assertEqual(found(check(canvas), Code.S6), [])

    def test_disagreeing_value(self):
        canvas = txt2img()
        node(canvas, 6)["widgets_values_named"] = {"seed": 8, "control_after_generate": "fixed", "steps": 20}
        [finding] = found(check(canvas), Code.S6)
        self.assertIs(finding.severity, Severity.ERROR)
        self.assertEqual(finding.data["problems"], ["'seed': named 8 != position 0 7"])
        [lenient] = found(check(canvas, Mode.BEAUTIFY), Code.S6)
        self.assertIs(lenient.severity, Severity.WARNING)

    def test_missing_extra_and_short(self):
        canvas = txt2img()
        node(canvas, 6)["widgets_values"] = [7, "fixed"]
        node(canvas, 6)["widgets_values_named"] = {"seed": 7, "cfg": 4}
        [finding] = found(check(canvas), Code.S6)
        self.assertEqual(
            finding.data["problems"],
            [
                "'control_after_generate': missing from widgets_values_named",
                "'steps': widgets_values has no position 2",
                "'cfg': not a widget of TestSampler",
            ],
        )

    def test_long_positional(self):
        canvas = txt2img()
        node(canvas, 5)["widgets_values"] = [512, 768, 1]
        node(canvas, 5)["widgets_values_named"] = {"width": 512, "height": 768}
        [finding] = found(check(canvas), Code.S6)
        self.assertEqual(finding.data["problems"], ["widgets_values has 3 entries, the layout 2"])

    def test_object_widgets_values(self):
        canvas = txt2img()
        node(canvas, 5)["widgets_values"] = {"width": 512}
        node(canvas, 5)["widgets_values_named"] = {"width": 512, "height": 768}
        self.assertEqual(found(check(canvas, Mode.BEAUTIFY), Code.S6), [])
        [finding] = found(check(canvas), Code.S6)
        self.assertIn("not an array", finding.message)

    def test_unknown_type_is_not_zipped(self):
        canvas = txt2img()
        node(canvas, 5)["type"] = "NotKnown"
        node(canvas, 5)["widgets_values_named"] = {"x": 1}
        self.assertEqual(found(check(canvas), Code.S6), [])


if __name__ == "__main__":
    unittest.main()
