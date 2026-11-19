import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.report import Code, Mode, Severity
from cwc.verify import verify_canvas, verify_compile
from tests.fixtures.prompt.build import CanvasBuilder
from tests.fixtures.verify.canvases import (
    BYPASS,
    DEFS,
    GROUPS,
    MUTED,
    POSITIONS,
    add_group,
    authoring,
    node,
    place,
    txt2img,
    txt2img_builder,
)


def check(canvas, mode=Mode.BEAUTIFY):
    return verify_canvas(canvas, DEFS, mode=mode)


def found(report, code):
    return [f for f in report.findings if f.code is code]


def with_extra(add, grouped=True):
    """The example canvas plus whatever ``add(builder)`` adds."""
    b = txt2img_builder()
    add(b)
    canvas = place(b.canvas(), POSITIONS)
    if grouped:
        for title, box in GROUPS.items():
            add_group(canvas, title, box)
    return canvas


class DeadNodeTest(unittest.TestCase):
    def dead_encoder(self, b):
        b.add(10, "TestEncode", widgets=["unused"])
        b.link(1, 1, 10, "clip")

    def test_dead_node_blocks_compile_and_warns_elsewhere(self):
        canvas = with_extra(self.dead_encoder)
        [strict] = found(check(canvas, Mode.COMPILE), Code.N1)
        self.assertEqual((strict.nodes, strict.severity), (("10",), Severity.ERROR))
        [lenient] = found(check(canvas), Code.N1)
        self.assertIs(lenient.severity, Severity.WARNING)

    def test_verify_compile_reports_dead_node(self):
        canvas = with_extra(self.dead_encoder)
        report = verify_compile(canvas, authoring(), DEFS)
        self.assertEqual([f.nodes for f in found(report, Code.N1)], [("10",)])

    def test_bypassed_nodes_count_as_enabled(self):
        self.assertEqual(found(check(txt2img(), Mode.COMPILE), Code.N1), [])

    def test_frontend_only_nodes_are_not_dead_and_pass_links(self):
        def add(b):
            b.add(10, "TestNote", ins=[], outs=[], widgets=["read me"])
            b.add(11, "TestImage")
            b.add(12, "TestReroute", ins=[("", "*")], outs=["*"])
            b.add(13, "TestSave", widgets=["second"])
            b.link(11, 0, 12, 0)
            b.link(12, 0, 13, "images")

        self.assertEqual(found(check(with_extra(add), Mode.COMPILE), Code.N1), [])

    def test_unknown_type_may_be_an_output(self):
        def add(b):
            b.add(10, "TestImage")
            b.add(11, "SomePackSaver", ins=[("images", "IMAGE")], outs=[])
            b.link(10, 0, 11, "images")

        self.assertEqual(found(check(with_extra(add)), Code.N1), [])


class NoOutputTest(unittest.TestCase):
    def canvas(self):
        b = CanvasBuilder(DEFS)
        b.add(1, "TestLoader", widgets=["m.safetensors"])
        b.add(2, "TestEncode", widgets=["x"])
        b.link(1, 1, 2, "clip")
        return b.canvas()

    def test_no_output_node(self):
        [finding] = found(check(self.canvas(), Mode.COMPILE), Code.N2)
        self.assertIs(finding.severity, Severity.ERROR)
        self.assertIs(found(check(self.canvas()), Code.N2)[0].severity, Severity.WARNING)

    def test_no_output_node_hides_dead_nodes(self):
        self.assertEqual(found(check(self.canvas()), Code.N1), [])

    def test_output_node_present(self):
        self.assertEqual(found(check(txt2img()), Code.N2), [])


class DuplicateTest(unittest.TestCase):
    def test_same_type_and_inputs(self):
        def add(b):
            b.add(10, "TestEncode", widgets=["a cat"])
            b.link(2, 1, 10, "clip")
            b.add(11, "TestSave", widgets=["dup"])
            b.link(10, 0, 11, "images")

        [finding] = found(check(with_extra(add), Mode.COMPILE), Code.N3)
        self.assertEqual((finding.nodes, finding.severity), (("3", "10"), Severity.INFO))

    def test_numbers_compare_as_javascript(self):
        def add(b):
            b.add(10, "TestLatent", widgets=[512.0, 768])
            b.add(11, "TestDecode")
            b.link(10, 0, 11, "samples")

        self.assertEqual([f.nodes for f in found(check(with_extra(add)), Code.N3)], [("5", "10")])

    def test_other_source_or_value_is_not_a_duplicate(self):
        def add(b):
            b.add(10, "TestEncode", widgets=["a cat"])
            b.link(1, 1, 10, "clip")  # 3 takes clip from 2
            b.add(11, "TestEncode", widgets=["a dog"])
            b.link(2, 1, 11, "clip")

        self.assertEqual(found(check(with_extra(add)), Code.N3), [])


class LegacyRerouteTest(unittest.TestCase):
    def test_reroute_node_is_reported(self):
        def add(b):
            b.add(10, "TestReroute", ins=[("", "*")], outs=["*"])
            b.add(11, "TestDecode")
            b.link(6, 0, 10, 0)
            b.link(10, 0, 11, "samples")

        [finding] = found(check(with_extra(add)), Code.N4)
        self.assertEqual((finding.nodes, finding.severity, finding.data), (("10",), Severity.INFO, {"consumers": 1}))

    def test_no_reroute_node(self):
        self.assertEqual(found(check(txt2img()), Code.N4), [])


class StrayOffNodeTest(unittest.TestCase):
    def test_bypassed_outside_a_module(self):
        canvas = txt2img(grouped=False)
        [first, second] = found(check(canvas), Code.N5)
        self.assertEqual((first.nodes, second.nodes), (("2",), ("8",)))
        self.assertIs(first.severity, Severity.INFO)

    def test_bypassed_inside_their_groups(self):
        self.assertEqual(found(check(txt2img()), Code.N5), [])

    def test_muted_group_is_a_module(self):
        self.assertEqual(found(check(txt2img({8: MUTED})), Code.N5), [])

    def test_muted_node_among_active_ones(self):
        [finding] = found(check(txt2img({7: MUTED})), Code.N5)
        self.assertEqual(finding.nodes, ("7",))
        self.assertIn("muted", finding.message)

    def test_group_mixing_modes_is_not_a_module(self):
        canvas = txt2img({3: BYPASS})
        self.assertEqual([f.nodes for f in found(check(canvas), Code.N5)], [("3",)])


class EmptyAndStrayTest(unittest.TestCase):
    def test_empty_group(self):
        canvas = add_group(txt2img(), "nothing", [5000, 5000, 200, 200])
        [finding] = found(check(canvas), Code.N6)
        self.assertEqual(finding.data, {"group": 5})
        self.assertIs(finding.severity, Severity.INFO)

    def test_stray_note(self):
        def add(b):
            b.add(10, "TestNote", ins=[], outs=[], widgets=["hello"])

        canvas = with_extra(add)
        node(canvas, 10)["pos"] = [4000, 4000]
        [finding] = found(check(canvas), Code.N6)
        self.assertEqual(finding.nodes, ("10",))

    def test_grouped_note_and_ungrouped_canvas(self):
        def add(b):
            b.add(10, "TestNote", ins=[], outs=[], widgets=["hello"])

        grouped = with_extra(add)
        node(grouped, 10)["pos"] = [0, 300]  # inside "loader"
        self.assertEqual(found(check(grouped), Code.N6), [])
        self.assertEqual(found(check(with_extra(add, grouped=False)), Code.N6), [])

    def test_compile_verification_skips_tidiness(self):
        canvas = add_group(txt2img(), "nothing", [5000, 5000, 200, 200])
        report = verify_compile(canvas, authoring(), DEFS)
        self.assertEqual([f for f in report.findings if f.code in (Code.N4, Code.N5, Code.N6)], [])


if __name__ == "__main__":
    unittest.main()
