import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.report import Code, Severity
from cwc.verify import verify_compile
from tests.fixtures.verify.canvases import (
    BYPASS,
    DEFS,
    GROUPS,
    MUTED,
    OUTER,
    add_group,
    authoring,
    node,
    place,
    txt2img,
    txt2img_builder,
    POSITIONS,
)


def verify(canvas, auth=None):
    return verify_compile(canvas, auth or authoring(), DEFS)


def found(report, code):
    return [f for f in report.findings if f.code is code]


class CleanTest(unittest.TestCase):
    def test_example_passes_every_check(self):
        report = verify(txt2img())
        self.assertEqual(report.findings, [])
        self.assertTrue(report.ok)

    def test_example_without_groups_passes(self):
        self.assertEqual(verify(txt2img(grouped=False)).findings, [])


class UnfedInputTest(unittest.TestCase):
    def test_bypassed_node_without_matching_input(self):
        # Decode (LATENT, VAE -> IMAGE) bypassed: nothing of type IMAGE reaches the save node.
        report = verify(txt2img({7: BYPASS}))
        [finding] = found(report, Code.B1)
        self.assertEqual((finding.nodes, finding.severity), (("9",), Severity.ERROR))
        self.assertEqual(finding.data, {"inputs": ["images"]})

    def test_bypass_passes_matching_input_through(self):
        self.assertEqual(found(verify(txt2img()), Code.B1), [])

    def test_widget_socket_falls_back_to_its_value(self):
        b = txt2img_builder()
        b.add(10, "TestIntGen", mode=BYPASS)
        b.link(10, 0, 6, "steps")
        canvas = place(b.canvas(), POSITIONS)
        self.assertEqual(found(verify(canvas), Code.B1), [])


class OptionalOnlyTest(unittest.TestCase):
    def canvas(self):
        # A non-optional INT source feeding only the optional upscaler's scale socket.
        b = txt2img_builder()
        b.add(10, "TestIntGen")
        b.link(10, 0, 8, "scale")
        canvas = place(b.canvas(), {**POSITIONS, 10: (1500, 300)})
        return canvas

    def test_upstream_of_optional_only(self):
        [finding] = found(verify(self.canvas()), Code.B2)
        self.assertEqual((finding.nodes, finding.severity), (("10",), Severity.ERROR))
        self.assertEqual(finding.data, {"consumers": ["8"]})

    def test_marked_optional_passes(self):
        canvas = self.canvas()
        node(canvas, 10)["mode"] = BYPASS
        self.assertEqual(found(verify(canvas), Code.B2), [])

    def test_upstream_reaching_output_through_bypass(self):
        # The loader feeds the bypassed LoRA, which hands MODEL and CLIP on.
        self.assertEqual(found(verify(txt2img()), Code.B2), [])


class PurityTest(unittest.TestCase):
    def test_optional_node_not_bypassed(self):
        findings = found(verify(txt2img({2: 0})), Code.B3)
        self.assertEqual([f.nodes for f in findings], [("2",)])
        self.assertIn("not 4", findings[0].message)

    def test_required_node_bypassed_or_muted(self):
        for mode in (BYPASS, MUTED):
            findings = found(verify(txt2img({5: mode})), Code.B3)
            self.assertEqual([f.nodes for f in findings], [("5",)], mode)

    def test_group_mixing_optional_and_active(self):
        canvas = txt2img(grouped=False)
        add_group(canvas, "lora", [280, -60, 540, 160])  # reaches the centre of encoder 3
        [finding] = found(verify(canvas), Code.B3)
        self.assertEqual(finding.nodes, ("3",))
        self.assertEqual(finding.data, {"group": 0, "optional": ["2"]})

    def test_optional_group_inside_a_non_optional_group(self):
        canvas = txt2img()
        add_group(canvas, "everything", OUTER)
        [finding] = found(verify(canvas), Code.B3)
        self.assertEqual(finding.data["group"], len(GROUPS))
        self.assertEqual(finding.data["optional"], ["2", "8"])
        self.assertEqual(finding.nodes, ("1", "3", "4", "5", "6", "7", "9"))

    def test_nested_optional_groups_and_notes(self):
        b = txt2img_builder()
        b.add(10, "TestNote", ins=[], outs=[], widgets=["toggle with Ctrl+B"])
        canvas = place(b.canvas(), {**POSITIONS, 10: (300, 300)})
        add_group(canvas, "extras", [280, -60, 240, 500])  # LoRA and the note
        add_group(canvas, "lora", GROUPS["lora"])
        self.assertEqual(found(verify(canvas), Code.B3), [])


if __name__ == "__main__":
    unittest.main()
