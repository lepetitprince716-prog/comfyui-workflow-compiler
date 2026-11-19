"""The compile pipeline (``cwc.pipeline.compile_document``) with layout and routing in it.

``compile_document`` runs normalize, instantiate, annotate, apply_notes, layout, route, then
``verify_compile`` and ``check_geometry`` in compile mode, merged into one report. These tests
hold it to: zero error findings on every case of ``tests.layout_cases`` and on seeds 0 to 59 of
``tests.layout_generator`` (full and reduced) under ``bento``, G2 included; the same canvas bytes
and the same layout and route results as the test helper ``tests.layout_cases.routed``; the
preset priority (argument, then ``_workflow.layout.preset``, then ``bento``); and a clear
authoring error, not a crash, for an unknown preset or one layout does not implement yet.
"""

import copy
import random
import unittest
from dataclasses import replace
from unittest import mock

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, txt2img
from tests.layout_cases import OTHERS, SIMPLE, routed
from tests.layout_generator import random_graph, reduced_graph

from cwc import pipeline
from cwc.annotate import annotate, apply_notes
from cwc.annotate.types import Zone
from cwc.emit.canonical import json_bytes
from cwc.layout import layout
from cwc.layout.presets import get_preset, load_presets
from cwc.layout.types import Axis, Tray
from cwc.pipeline import compile_document
from cwc.report import Code, CwcError, ExitCode, Finding, Mode, Report, Severity
from cwc.route import RouteResult

CASES = {**SIMPLE, **OTHERS}
SEEDS = range(60)


def trayed():
    """tower-preview given a tray, a preset feature layout does not implement: the bundled
    presets as ``cwc.pipeline.load_presets`` would return them with this one in."""
    presets = load_presets()
    tower = replace(presets["tower-preview"], tray=Tray(1.3, (Zone.PROMPT, Zone.POSTPROCESS)))
    return {**presets, "tower-preview": tower}


def errors(report):
    return [f"{f.code.value}: {f.message}" for f in report.findings if f.severity is Severity.ERROR]


def authored(doc, preset):
    doc = copy.deepcopy(doc)
    doc["_workflow"] = {"format": 1, "layout": {"preset": preset}}
    return doc


class CleanTest(unittest.TestCase):
    def test_case_table_compiles_without_errors(self):
        for name, make in CASES.items():
            with self.subTest(case=name):
                report = compile_document(make(), DEFS).report
                self.assertEqual(errors(report), [])
                self.assertIn("G2", report.metrics["geometry"])

    def test_generated_graphs_compile_without_errors(self):
        for seed in SEEDS:
            for name, build in (("full", random_graph), ("reduced", reduced_graph)):
                with self.subTest(seed=seed, graph=name):
                    self.assertEqual(errors(compile_document(build(seed), DEFS).report), [])


class SameAsHelperTest(unittest.TestCase):
    def test_canvas_bytes_layout_and_routes_match_the_test_helper(self):
        for name, make in CASES.items():
            with self.subTest(case=name):
                done = compile_document(make(), DEFS)
                canvas, _plan, result, _report, _before, routes = routed(make())
                self.assertEqual(json_bytes(done.canvas.to_json()), json_bytes(canvas.to_json()))
                self.assertEqual(done.layout, result)
                self.assertEqual(done.routes, routes)

    def test_the_report_merges_both_checks(self):
        done = compile_document(CASES["two_stage"](), DEFS)
        canvas, _plan, result, geometry, _before, routes = routed(CASES["two_stage"]())
        metrics = done.report.metrics
        self.assertEqual(metrics["preset"], "bento")
        self.assertEqual(metrics["geometry"], geometry.metrics)
        self.assertEqual(metrics["groups"], len(canvas.groups))  # verify_compile ran on the laid-out canvas
        self.assertEqual(
            metrics["route"],
            {
                "rerouted": len(routes.rerouted),
                "dummy": routes.dummy,
                "unresolved": [],
                "short_channels": len(routes.shortfalls),
                "shared_edges": routes.shared_edges,
            },
        )
        self.assertGreater(metrics["route"]["rerouted"], 0)
        for finding in geometry.findings:
            self.assertIn(finding, done.report.findings)
        self.assertEqual(done.report.mode, Mode.COMPILE)

    def test_output_is_deterministic(self):
        one = compile_document(CASES["lora_controlnet_upscale"](), DEFS)
        two = compile_document(CASES["lora_controlnet_upscale"](), DEFS)
        self.assertEqual(json_bytes(one.canvas.to_json()), json_bytes(two.canvas.to_json()))
        self.assertEqual(json_bytes(one.report.to_json()), json_bytes(two.report.to_json()))


class FailureTest(unittest.TestCase):
    def test_a_geometry_error_makes_the_report_fail_without_raising(self):
        def failing(*args, **kwargs):
            report = Report(Mode.COMPILE)
            report.add(Finding(Code.G1, Severity.ERROR, "node frames overlap", nodes=("1", "2")))
            return report

        with mock.patch("cwc.pipeline.check_geometry", side_effect=failing):
            done = compile_document(txt2img(), DEFS)
        self.assertFalse(done.report.ok)
        self.assertEqual(errors(done.report), ["G1: node frames overlap"])

    def test_an_unresolved_link_is_a_g2_error(self):
        canvas, _ = compiled(txt2img())
        link = sorted(canvas.links, key=lambda link: link.id)[0]
        stuck = RouteResult(rerouted=(), dummy=0, unresolved=(link.id,), channels=(), shared_edges=0)
        with mock.patch("cwc.pipeline.route", return_value=stuck):
            done = compile_document(txt2img(), DEFS)
        self.assertFalse(done.report.ok)
        # the stub leaves the plain splines, which clear every box, so the check reports nothing
        # for this link and the finding is the router's own
        [finding] = [f for f in done.report.blocking() if f.data.get("unresolved")]
        self.assertEqual((finding.code, finding.data), (Code.G2, {"link": link.id, "unresolved": True}))
        self.assertEqual(finding.nodes, tuple(sorted({str(link.origin_id), str(link.target_id)}, key=int)))
        self.assertEqual(done.report.metrics["route"]["unresolved"], [link.id])

    def test_an_unresolved_link_that_really_crosses_a_box_is_one_g2_error(self):
        # layout is wrapped to move the nodes after it, so routing fails for some links and the
        # check finds the plain splines of those links crossing boxes: both would report the link
        real = pipeline.layout

        def jittered(canvas, plan, preset, defs, **kwargs):
            result = real(canvas, plan, preset, defs, **kwargs)
            rng = random.Random(5)
            for node in canvas.raw["nodes"]:
                node["pos"] = [node["pos"][0] + rng.randint(-25, 25) * 10, node["pos"][1] + rng.randint(-25, 25) * 10]
            return result

        with mock.patch("cwc.pipeline.layout", side_effect=jittered):
            done = compile_document(random_graph(61), DEFS)
        stuck = list(done.routes.unresolved)
        self.assertGreater(len(stuck), 1)
        g2 = [f for f in done.report.findings if f.code is Code.G2]
        self.assertEqual(sorted(f.data["link"] for f in g2), sorted(stuck))
        for finding in g2:
            self.assertEqual((finding.severity, finding.data["unresolved"]), (Severity.ERROR, True))
            self.assertNotIn("links", finding.data)
        # at least one is the check's own finding, which keeps what it measured
        self.assertTrue(any("classic" in f.data for f in g2))
        self.assertEqual(done.report.metrics["route"]["unresolved"], stuck)


class PresetTest(unittest.TestCase):
    def titles(self, done):
        return [group["title"] for group in done.canvas.raw["groups"]]

    def test_default_is_bento(self):
        done = compile_document(txt2img(), DEFS)
        self.assertEqual((done.layout.preset, done.report.metrics["preset"]), ("bento", "bento"))
        self.assertTrue(self.titles(done)[0].startswith("01 "))

    def test_authored_preset_beats_the_default(self):
        done = compile_document(authored(txt2img(), "stepped"), DEFS)
        self.assertEqual(done.layout.preset, "stepped")
        self.assertTrue(self.titles(done)[0].startswith("Step 1 "))
        self.assertEqual(errors(done.report), [])

    def test_argument_beats_the_authored_preset(self):
        done = compile_document(authored(txt2img(), "stepped"), DEFS, preset="bento")
        self.assertEqual(done.layout.preset, "bento")
        done = compile_document(authored(txt2img(), "bento"), DEFS, preset="stepped")
        self.assertEqual(done.layout.preset, "stepped")

    def raises(self, doc, preset=None):
        with self.assertRaises(CwcError) as caught:
            compile_document(doc, DEFS, preset=preset)
        self.assertEqual(caught.exception.exit_code, ExitCode.INPUT_ERROR)
        return [(f.code, f.severity, f.message) for f in caught.exception.findings]

    def test_unknown_names_are_authoring_errors(self):
        self.assertEqual(self.raises(txt2img(), "nope"), [(Code.E7, Severity.ERROR, "unknown preset 'nope'")])
        self.assertEqual(
            self.raises(authored(txt2img(), "nope")),
            [(Code.E7, Severity.ERROR, "unknown preset 'nope' in _workflow.layout.preset")],
        )
        # a misspelt authored name is reported even when the argument overrides it
        self.assertEqual(len(self.raises(authored(txt2img(), "nope"), "bento")), 1)

    def test_auto_compiles_under_its_choice(self):
        # auto picks tower-preview for txt2img
        for done in (compile_document(txt2img(), DEFS, preset="auto"), compile_document(authored(txt2img(), "auto"), DEFS)):
            self.assertEqual(done.layout.preset, "tower-preview")
            self.assertEqual(errors(done.report), [])

    def test_auto_is_refused_while_its_choice_is_unavailable(self):
        with mock.patch("cwc.pipeline.load_presets", trayed):
            [(code, _, message)] = self.raises(txt2img(), "auto")
            self.assertEqual(code, Code.E7)
            self.assertIn("'auto' is not available yet", message)
            self.assertEqual(len(self.raises(authored(txt2img(), "auto"))), 1)

    def test_unimplemented_presets_are_refused(self):
        with mock.patch("cwc.pipeline.load_presets", trayed):
            [(code, _, message)] = self.raises(txt2img(), "tower-preview")
        self.assertEqual(code, Code.E7)
        self.assertIn("preset 'tower-preview' is not available yet", message)
        self.assertIn("uses tray, which layout does not implement yet", message)


class LayoutEntryTest(unittest.TestCase):
    def test_layout_refuses_preset_features_it_does_not_implement(self):
        canvas, authoring = compiled(txt2img())
        plan = annotate(canvas, DEFS, authoring)
        apply_notes(canvas, plan)
        with self.assertRaisesRegex(NotImplementedError, "uses tray,"):
            layout(canvas, plan, trayed()["tower-preview"], DEFS)

    def test_layout_refuses_a_slot_repeated_along_y(self):
        # Instances repeated along y would stack at one x and a later stage's links would run
        # back left; every bundled preset that repeats a slot, with that slot turned to y.
        repeating = [
            (name, b, s)
            for name, preset in load_presets().items()
            for b, band in enumerate(preset.bands)
            for s, slot in enumerate(band.slots)
            if slot.repeat is not None
        ]
        self.assertTrue(repeating)
        for name, b, s in repeating:
            with self.subTest(preset=name, band=b, slot=s):
                preset = get_preset(name)
                band = preset.bands[b]
                slots = tuple(replace(slot, repeat=Axis.Y) if i == s else slot for i, slot in enumerate(band.slots))
                bands = tuple(replace(band, slots=slots) if i == b else other for i, other in enumerate(preset.bands))
                canvas, authoring = compiled(txt2img())
                plan = annotate(canvas, DEFS, authoring)
                apply_notes(canvas, plan)
                with self.assertRaisesRegex(NotImplementedError, "uses repeat y,"):
                    layout(canvas, plan, replace(preset, bands=bands), DEFS)

    def test_layout_accepts_every_bundled_preset(self):
        for name in ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview"):
            with self.subTest(preset=name):
                canvas, authoring = compiled(txt2img())
                plan = annotate(canvas, DEFS, authoring)
                apply_notes(canvas, plan)
                self.assertEqual(layout(canvas, plan, get_preset(name), DEFS).preset, name)


if __name__ == "__main__":
    unittest.main()
