"""``--preset auto``: the choice rules (``cwc.pipeline.choose_auto``, a pure function of the plan's
counts) and their wiring in ``compile_document``.

Rules, first match in this order: at most 16 nodes and no optional module -> ``tower-preview``;
at least 3 control or mask branches -> ``swimlanes``; at least 60 nodes -> ``cockpit-split``;
else ``bento``. ``auto`` never falls back to another preset when layout refuses its choice.

Every bundled preset is available: ``tower-preview``, ``swimlanes`` and ``cockpit-split`` were
flipped from refused to compiling when each became available
(``test_a_small_graph_chooses_tower_preview_and_compiles``,
``test_parallel_optional_branches_choose_swimlanes_and_compile``,
``test_a_large_graph_chooses_cockpit_split_and_compiles``). A refused choice is tested on a
tower-preview given a tray in the test, a feature layout does not implement.
"""

import copy
import unittest
from dataclasses import replace
from unittest import mock

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img

from cwc.annotate import annotate
from cwc.annotate.types import PlanCounts, Zone
from cwc.canvas.model import Canvas
from cwc.layout.presets import load_presets
from cwc.layout.types import Tray
from cwc.pipeline import AUTO_LARGE_NODES, AUTO_PARALLEL_BRANCHES, AUTO_SMALL_NODES, choose_auto, compile_document
from cwc.report import Code, CwcError, ExitCode, Severity


def pick(nodes, optional=0, control=0):
    return choose_auto(PlanCounts(nodes=nodes, optional_modules=optional, control_branches=control)).preset


def control_branches(optional):
    """txt2img with three ControlNet branches side by side on the base prompts, merged by
    conditioning combines that are not optional; 23 nodes."""
    doc = txt2img()
    meta = {"ui_optional": True} if optional else {}
    for i in range(3):
        doc[f"img{i}"] = node("LoadImage", {"image": f"r{i}.png"}, **meta)
        doc[f"edge{i}"] = node("Canny", {"image": [f"img{i}", "IMAGE"], "low_threshold": 0.4, "high_threshold": 0.8}, **meta)
        doc[f"cnl{i}"] = node("ControlNetLoader", {"control_net_name": f"cn{i}.safetensors"}, **meta)
        doc[f"cn{i}"] = node(
            "ControlNetApplyAdvanced",
            {
                "positive": ["pos", 0],
                "negative": ["neg", 0],
                "control_net": [f"cnl{i}", 0],
                "image": [f"edge{i}", 0],
                "strength": 0.8,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            **meta,
        )
    for sign, slot in (("p", 0), ("n", 1)):
        doc[f"{sign}01"] = node("ConditioningCombine", {"conditioning_1": ["cn0", slot], "conditioning_2": ["cn1", slot]})
        doc[f"{sign}012"] = node("ConditioningCombine", {"conditioning_1": [f"{sign}01", 0], "conditioning_2": ["cn2", slot]})
    doc["sampler"]["inputs"]["positive"] = ["p012", 0]
    doc["sampler"]["inputs"]["negative"] = ["n012", 0]
    return doc


def chained_branches(optional):
    """txt2img with three ControlNet branches in series on the conditioning; 19 nodes."""
    doc = txt2img()
    meta = {"ui_optional": True} if optional else {}
    pos, neg = ["pos", 0], ["neg", 0]
    for i in range(3):
        doc[f"img{i}"] = node("LoadImage", {"image": f"r{i}.png"}, **meta)
        doc[f"edge{i}"] = node("Canny", {"image": [f"img{i}", "IMAGE"], "low_threshold": 0.4, "high_threshold": 0.8}, **meta)
        doc[f"cnl{i}"] = node("ControlNetLoader", {"control_net_name": f"cn{i}.safetensors"}, **meta)
        doc[f"cn{i}"] = node(
            "ControlNetApplyAdvanced",
            {
                "positive": pos,
                "negative": neg,
                "control_net": [f"cnl{i}", 0],
                "image": [f"edge{i}", 0],
                "strength": 0.8,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            **meta,
        )
        pos, neg = [f"cn{i}", 0], [f"cn{i}", 1]
    doc["sampler"]["inputs"]["positive"] = pos
    doc["sampler"]["inputs"]["negative"] = neg
    return doc


def long_model_chain(total):
    """txt2img with a chain of model-only LoRAs between the checkpoint and the sampler, ``total`` nodes."""
    doc = txt2img()
    previous = ["ckpt", "MODEL"]
    for i in range(total - len(doc)):
        doc[f"lora{i}"] = node("LoraLoaderModelOnly", {"model": previous, "lora_name": f"l{i}.safetensors", "strength_model": 0.5})
        previous = [f"lora{i}", 0]
    doc["sampler"]["inputs"]["model"] = previous
    return doc


def authored(doc, preset):
    doc = copy.deepcopy(doc)
    doc["_workflow"] = {"format": 1, "layout": {"preset": preset}}
    return doc


class RulesTest(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual((AUTO_SMALL_NODES, AUTO_PARALLEL_BRANCHES, AUTO_LARGE_NODES), (16, 3, 60))

    def test_every_choice_is_a_bundled_preset(self):
        presets = load_presets()
        for counts in ((5, 0, 0), (20, 0, 3), (80, 1, 0), (30, 1, 0)):
            with self.subTest(counts=counts):
                self.assertIn(pick(*counts), presets)

    def test_one_case_per_rule(self):
        self.assertEqual(pick(10), "tower-preview")
        self.assertEqual(pick(30, optional=1, control=4), "swimlanes")
        self.assertEqual(pick(90, optional=2), "cockpit-split")
        self.assertEqual(pick(30, optional=1, control=1), "bento")

    def test_the_first_matching_rule_wins(self):
        # small with three control branches and no optional module: the first rule
        self.assertEqual(pick(16, optional=0, control=3), "tower-preview")
        # with an optional module the first rule fails and the branches decide
        self.assertEqual(pick(16, optional=1, control=3), "swimlanes")
        # large with three branches: the branch rule comes before the size rule
        self.assertEqual(pick(60, optional=0, control=3), "swimlanes")

    def test_node_boundary_16_and_17(self):
        self.assertEqual(pick(16), "tower-preview")
        self.assertEqual(pick(17), "bento")
        self.assertEqual(pick(16, optional=1), "bento")

    def test_branch_boundary_2_and_3(self):
        self.assertEqual(pick(30, control=2), "bento")
        self.assertEqual(pick(30, control=3), "swimlanes")

    def test_node_boundary_59_and_60(self):
        self.assertEqual(pick(59), "bento")
        self.assertEqual(pick(60), "cockpit-split")

    def test_the_rule_is_named(self):
        self.assertEqual(choose_auto(PlanCounts(30, 1, 0)).rule, "no other rule holds")
        self.assertIn("16", choose_auto(PlanCounts(5, 0, 0)).rule)


class ControlCountTest(unittest.TestCase):
    """What ``PlanCounts.control_branches`` counts today, measured on three ControlNet branches."""

    def counts(self, doc):
        canvas, authoring = compiled(doc)
        return annotate(canvas, DEFS, authoring).counts

    def test_three_required_branches_count_three(self):
        self.assertEqual(self.counts(control_branches(optional=False)), PlanCounts(23, 0, 3))

    def test_three_optional_branches_count_three(self):
        # the optional groups' members have the control zone when no node is optional
        self.assertEqual(self.counts(control_branches(optional=True)), PlanCounts(23, 3, 3))

    def test_chained_branches_are_one_group_and_count_once(self):
        self.assertEqual(self.counts(chained_branches(optional=False)), PlanCounts(19, 0, 1))
        self.assertEqual(self.counts(chained_branches(optional=True)), PlanCounts(19, 1, 1))

    def kept_counts(self, doc, enclose=False):
        """The kept plan's counts of the canvas bento compiles for ``doc`` (optional nodes are
        bypassed there); ``enclose`` adds one group around every other group."""
        raw = compile_document(doc, DEFS, preset="bento").canvas.to_json()
        if enclose:
            boxes = [group["bounding"] for group in raw["groups"]]
            left, top = min(b[0] for b in boxes) - 20, min(b[1] for b in boxes) - 20
            right, bottom = max(b[0] + b[2] for b in boxes) + 20, max(b[1] + b[3] for b in boxes) + 20
            raw["groups"].append({**raw["groups"][0], "title": "All", "bounding": [left, top, right - left, bottom - top]})
        return annotate(Canvas(raw), DEFS, None).counts

    def test_the_kept_plan_counts_the_same_way(self):
        self.assertEqual(self.kept_counts(control_branches(optional=True)), PlanCounts(23, 3, 3))
        self.assertEqual(self.kept_counts(control_branches(optional=False)), PlanCounts(23, 0, 3))
        self.assertEqual(self.kept_counts(chained_branches(optional=True)), PlanCounts(19, 1, 1))

    def test_the_kept_plan_counts_top_level_groups_only(self):
        # the three control groups are nested in one group of another zone: no top-level branch
        self.assertEqual(self.kept_counts(control_branches(optional=False), enclose=True), PlanCounts(23, 0, 0))


class CompileTest(unittest.TestCase):
    def refused(self, doc, preset=None):
        with self.assertRaises(CwcError) as caught:
            compile_document(doc, DEFS, preset=preset)
        self.assertEqual(caught.exception.exit_code, ExitCode.INPUT_ERROR)
        [finding] = caught.exception.findings
        self.assertEqual((finding.code, finding.severity), (Code.E7, Severity.ERROR))
        return finding.message

    def hint(self, doc, preset):
        with self.assertRaises(CwcError) as caught:
            compile_document(doc, DEFS, preset=preset)
        return caught.exception.findings[0].hint

    def test_a_middle_graph_chooses_bento_and_compiles(self):
        doc = chained_branches(optional=True)
        for done in (compile_document(doc, DEFS, preset="auto"), compile_document(authored(doc, "auto"), DEFS)):
            self.assertEqual(done.layout.preset, "bento")
            self.assertEqual(done.report.metrics["preset"], "bento")
            self.assertEqual(
                done.report.metrics["preset_auto"],
                {"rule": "no other rule holds", "nodes": 19, "optional_modules": 1, "control_branches": 1},
            )
            self.assertEqual([f for f in done.report.findings if f.severity is Severity.ERROR], [])

    def test_parallel_optional_branches_choose_swimlanes_and_compile(self):
        done = compile_document(control_branches(optional=True), DEFS, preset="auto")
        self.assertEqual(done.layout.preset, "swimlanes")
        self.assertEqual(done.report.metrics["preset"], "swimlanes")
        self.assertEqual(
            done.report.metrics["preset_auto"],
            {"rule": "at least 3 control or mask branches", "nodes": 23, "optional_modules": 3, "control_branches": 3},
        )
        self.assertEqual([f for f in done.report.findings if f.severity is Severity.ERROR], [])

    def test_a_named_preset_writes_no_auto_metric(self):
        done = compile_document(control_branches(optional=True), DEFS, preset="bento")
        self.assertNotIn("preset_auto", done.report.metrics)

    def test_a_small_graph_chooses_tower_preview_and_compiles(self):
        for done in (compile_document(txt2img(), DEFS, preset="auto"), compile_document(authored(txt2img(), "auto"), DEFS)):
            self.assertEqual(done.layout.preset, "tower-preview")
            self.assertEqual(done.report.metrics["preset"], "tower-preview")
            self.assertEqual(
                done.report.metrics["preset_auto"],
                {"rule": "at most 16 nodes and no optional module", "nodes": 7, "optional_modules": 0, "control_branches": 0},
            )
            self.assertEqual([f for f in done.report.findings if f.severity is Severity.ERROR], [])

    def test_auto_does_not_fall_back_when_layout_refuses_its_choice(self):
        presets = load_presets()
        trayed = replace(presets["tower-preview"], tray=Tray(1.3, (Zone.PROMPT, Zone.POSTPROCESS)))
        with mock.patch("cwc.pipeline.load_presets", lambda: {**presets, "tower-preview": trayed}):
            for message in (self.refused(txt2img(), "auto"), self.refused(authored(txt2img(), "auto"))):
                self.assertIn("preset 'auto' is not available yet for this graph: it chose 'tower-preview'", message)
                self.assertIn("nodes 7, optional_modules 0, control_branches 0", message)
                self.assertIn("uses tray, which layout does not implement yet", message)
            hint = self.hint(txt2img(), "auto")
        self.assertTrue(hint.startswith("presets: auto, bento"), hint)

    def test_a_large_graph_chooses_cockpit_split_and_compiles(self):
        done = compile_document(long_model_chain(60), DEFS, preset="auto")
        self.assertEqual(done.layout.preset, "cockpit-split")
        self.assertEqual(done.report.metrics["preset"], "cockpit-split")
        self.assertEqual(
            done.report.metrics["preset_auto"],
            {"rule": "at least 60 nodes", "nodes": 60, "optional_modules": 0, "control_branches": 0},
        )
        self.assertEqual([f for f in done.report.findings if f.severity is Severity.ERROR], [])

    def test_the_argument_beats_an_authored_auto(self):
        done = compile_document(authored(txt2img(), "auto"), DEFS, preset="bento")
        self.assertEqual(done.layout.preset, "bento")
        self.assertNotIn("preset_auto", done.report.metrics)


if __name__ == "__main__":
    unittest.main()
