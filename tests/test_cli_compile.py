import copy
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests import support

from cwc.annotate.types import Zone
from cwc.cli import main
from cwc.layout.presets import load_presets
from cwc.layout.types import Tray
from cwc.report import ExitCode

TXT2IMG = json.loads((support.FIXTURES / "normalize" / "txt2img.json").read_text(encoding="utf-8"))


def with_lora(doc):
    """txt2img with an optional LoRA between the checkpoint and its consumers."""
    doc = copy.deepcopy(doc)
    doc["lora"] = {
        "class_type": "LoraLoader",
        "inputs": {
            "model": ["ckpt", "MODEL"],
            "clip": ["ckpt", "CLIP"],
            "lora_name": "style.safetensors",
            "strength_model": 0.8,
            "strength_clip": 0.8,
        },
        "_meta": {"title": "Style LoRA", "ui_optional": True},
    }
    doc["pos"]["inputs"]["clip"] = ["lora", "CLIP"]
    doc["neg"]["inputs"]["clip"] = ["lora", "CLIP"]
    doc["sampler"]["inputs"]["model"] = ["lora", "MODEL"]
    return doc


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main([str(a) for a in argv], out, err)
    return code, out.getvalue(), err.getvalue()


class Folder(unittest.TestCase):
    """A temporary folder for the graph and the output."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def graph(self, doc, name="graph.json"):
        path = self.dir / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def read(self, suffix):
        return json.loads((self.out / f"graph{suffix}").read_text(encoding="utf-8"))


class CompileTest(Folder):
    def test_writes_canvas_prompt_and_report(self):
        code, out, err = run("compile", self.graph(with_lora(TXT2IMG)), "-o", self.out)
        self.assertEqual(code, ExitCode.OK)
        notice, summary = err.splitlines()  # no error or warning: the success summary, after
        # the one info line on the keys the graph sets that have no effect yet (ui_note and the like)
        self.assertTrue(summary.startswith("cwc compile: ok; "), summary)
        self.assertTrue(notice.startswith("E8 info: ") and "no effect" in notice, notice)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["graph.api.json", "graph.report.json", "graph.ui.json"])
        self.assertEqual(out.count("wrote "), 3)
        report = self.read(".report.json")
        self.assertTrue(report["ok"])
        self.assertEqual(report["mode"], "compile")
        canvas = self.read(".ui.json")
        self.assertEqual(canvas["version"], 0.4)
        modes = {node["type"]: node["mode"] for node in canvas["nodes"]}
        self.assertEqual(modes["LoraLoader"], 4)
        api = self.read(".api.json")
        self.assertNotIn("LoraLoader", {node["class_type"] for node in api.values()})
        self.assertNotIn("_workflow", api)

    def test_all_enabled_adds_the_full_prompt_and_a_later_run_removes_it(self):
        path = self.graph(with_lora(TXT2IMG))
        self.assertEqual(run("compile", path, "-o", self.out, "--all-enabled")[0], ExitCode.OK)
        full = self.read(".full.api.json")
        self.assertIn("LoraLoader", {node["class_type"] for node in full.values()})
        self.assertEqual(run("compile", path, "-o", self.out)[0], ExitCode.OK)
        self.assertFalse((self.out / "graph.full.api.json").exists())

    def test_output_is_deterministic(self):
        path = self.graph(with_lora(TXT2IMG))
        run("compile", path, "-o", self.out, "--all-enabled")
        first = {p.name: p.read_bytes() for p in self.out.iterdir()}
        run("compile", path, "-o", self.out, "--all-enabled")
        self.assertEqual(first, {p.name: p.read_bytes() for p in self.out.iterdir()})

    def test_authoring_error_writes_only_the_report(self):
        good = self.graph(TXT2IMG)
        self.assertEqual(run("compile", good, "-o", self.out)[0], ExitCode.OK)
        doc = copy.deepcopy(TXT2IMG)
        doc["decode"]["class_type"] = "VAEDecodeNope"
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual([p.name for p in self.out.iterdir()], ["graph.report.json"])
        report = self.read(".report.json")
        self.assertFalse(report["ok"])
        self.assertIn("E1", [f["code"] for f in report["findings"]])
        self.assertIn("E1:", err)

    def test_unreadable_input(self):
        code, _, _ = run("compile", self.dir / "missing.json", "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual(self.read_named("missing")["findings"][0]["code"], "I1")

    def test_canvas_given_as_authoring(self):
        canvas = {"nodes": [], "links": [], "version": 0.4}
        code, _, _ = run("compile", self.graph(canvas), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)

    def test_missing_nodedefs_file(self):
        code, _, _ = run("compile", self.graph(TXT2IMG), "-o", self.out, "--nodedefs", self.dir / "none.json")
        self.assertEqual(code, ExitCode.NODEDEFS_MISSING)
        self.assertEqual(self.read(".report.json")["findings"][0]["code"], "D1")

    def test_json_summary(self):
        code, out, _ = run("compile", self.graph(TXT2IMG), "-o", self.out, "--json")
        self.assertEqual(code, ExitCode.OK)
        summary = json.loads(out)
        self.assertEqual(summary["ok"], True)
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(sorted(summary["outputs"]), ["api", "report", "ui"])
        self.assertEqual(sorted(summary["findings"]), ["error", "info", "warning"])

    def test_unwritable_output_folder(self):
        blocker = self.dir / "file"
        blocker.write_text("x", encoding="utf-8")
        code, _, err = run("compile", self.graph(TXT2IMG), "-o", blocker / "sub")
        self.assertEqual(code, ExitCode.WRITE_FAILED)
        self.assertIn("cannot write", err)

    def test_failed_verification_writes_only_the_report_and_removes_stale_outputs(self):
        self.assertEqual(run("compile", self.graph(TXT2IMG), "-o", self.out)[0], ExitCode.OK)
        self.assertTrue((self.out / "graph.ui.json").exists())
        doc = copy.deepcopy(TXT2IMG)
        doc["stray"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}  # normalizes, then fails N1
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.CHECK_FAILED)
        self.assertEqual([p.name for p in self.out.iterdir()], ["graph.report.json"])
        report = self.read(".report.json")
        self.assertFalse(report["ok"])
        [finding] = [f for f in report["findings"] if f["severity"] == "error"]
        self.assertEqual(finding["code"], "N1")
        self.assertIn("N1:", err)
        self.assertIn("see ", err)

    def test_a_verify_finding_names_the_author_id(self):
        doc = copy.deepcopy(TXT2IMG)
        doc["stray"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
        run("compile", self.graph(doc), "-o", self.out)
        [finding] = [f for f in self.read(".report.json")["findings"] if f["code"] == "N1"]
        [canvas_id] = finding["nodes"]
        self.assertEqual(finding["data"]["author_ids"], {canvas_id: "stray"})
        self.assertIn("'stray'", finding["message"])

    def test_write_failure_is_exit_5_and_leaves_no_mix_of_runs(self):
        path = self.graph(TXT2IMG)
        self.assertEqual(run("compile", path, "-o", self.out)[0], ExitCode.OK)
        (self.out / "graph.full.api.json").mkdir()  # a directory squats on an artefact path
        code, _, err = run("compile", path, "-o", self.out, "--all-enabled")
        self.assertEqual(code, ExitCode.WRITE_FAILED)
        self.assertIn("cannot write", err)
        files = sorted(p.name for p in self.out.iterdir() if p.is_file())
        self.assertEqual(files, [])  # no canvas, prompt or report of either run
        self.assertTrue((self.out / "graph.full.api.json").is_dir())  # best effort: it could not be removed
        self.assertIn(f"could not remove {self.out / 'graph.full.api.json'}", err)  # and it is named
        self.assertEqual(err.count("could not remove"), 1)

    def test_write_failure_with_json_prints_the_summary_with_exit_5(self):
        path = self.graph(TXT2IMG)
        (self.out / "graph.full.api.json").mkdir(parents=True)
        code, out, err = run("compile", path, "-o", self.out, "--all-enabled", "--json")
        self.assertEqual(code, ExitCode.WRITE_FAILED)
        summary = json.loads(out)
        self.assertEqual((summary["ok"], summary["exit_code"], summary["outputs"]), (False, 5, {}))
        self.assertEqual(sorted(summary["findings"]), ["error", "info", "warning"])
        self.assertIn("cannot write", summary["error"])
        self.assertEqual(summary["leftover"], [str(self.out / "graph.full.api.json")])
        self.assertIn("cannot write", err)

    def test_an_internal_error_with_json_prints_the_summary_with_exit_70(self):
        with mock.patch("cwc.cli.compile_document", side_effect=RuntimeError("boom\n  with detail")):
            code, out, err = run("compile", self.graph(TXT2IMG), "-o", self.out, "--json")
        self.assertEqual(code, ExitCode.INTERNAL)
        self.assertEqual(json.loads(out), {"ok": False, "exit_code": 70, "error": "internal error: RuntimeError: boom with detail"})
        self.assertEqual(err, "cwc: internal error: RuntimeError: boom with detail\n")

    def test_an_internal_error_is_exit_70_with_one_stderr_line(self):
        with mock.patch("cwc.cli.compile_document", side_effect=RuntimeError("boom\n  with detail")):
            code, out, err = run("compile", self.graph(TXT2IMG), "-o", self.out)
        self.assertEqual(code, ExitCode.INTERNAL)
        self.assertEqual(int(ExitCode.INTERNAL), 70)
        self.assertEqual(out, "")
        self.assertEqual(err, "cwc: internal error: RuntimeError: boom with detail\n")

    def test_non_finite_nodedefs_are_exit_4_not_a_traceback(self):
        info = self.dir / "oi.json"
        info.write_text('{"T": {"input": {"required": {"f": ["FLOAT", {"default": Infinity}]}}, "output": []}}', encoding="utf-8")
        code, _, err = run("compile", self.graph(TXT2IMG), "-o", self.out, "--nodedefs", info)
        self.assertEqual(code, ExitCode.NODEDEFS_MISSING)
        self.assertIn("Infinity", err)
        self.assertEqual(self.read(".report.json")["findings"][0]["code"], "D1")

    def read_named(self, name):
        return json.loads((self.out / f"{name}.report.json").read_text(encoding="utf-8"))


class StaleOutputTest(Folder):
    """A run removes the outputs of an earlier run of the same graph that it does not write
    itself, so an old canvas is never taken for this run's, and says on stderr what it removed."""

    def removed_line(self, err):
        lines = [line for line in err.splitlines() if line.startswith("cwc compile: removed ")]
        self.assertEqual(len(lines), 1, err)
        return lines[0]

    def test_a_failed_run_names_the_outputs_it_removed(self):
        path = self.graph(with_lora(TXT2IMG))
        self.assertEqual(run("compile", path, "-o", self.out, "--all-enabled")[0], ExitCode.OK)
        doc = with_lora(TXT2IMG)
        doc["decode"]["class_type"] = "VAEDecodeNope"
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual([p.name for p in self.out.iterdir()], ["graph.report.json"])
        line = self.removed_line(err)
        for name in ("graph.ui.json", "graph.api.json", "graph.full.api.json"):
            self.assertIn(str(self.out / name), line)
        self.assertNotIn("graph.report.json", line)
        self.assertTrue(err.rstrip("\n").splitlines()[-1].startswith("cwc compile: 1 error(s)"), err)

    def test_a_successful_run_names_the_full_prompt_it_removed(self):
        path = self.graph(with_lora(TXT2IMG))
        self.assertEqual(run("compile", path, "-o", self.out, "--all-enabled")[0], ExitCode.OK)
        code, _, err = run("compile", path, "-o", self.out, "--json")
        self.assertEqual(code, ExitCode.OK)
        line = self.removed_line(err)
        self.assertIn(str(self.out / "graph.full.api.json"), line)
        self.assertNotIn("graph.ui.json", line)

    def test_nothing_to_remove_prints_nothing(self):
        code, _, err = run("compile", self.graph(TXT2IMG), "-o", self.out)
        self.assertEqual(code, ExitCode.OK)
        self.assertNotIn("removed", err)
        doc = copy.deepcopy(TXT2IMG)
        doc["decode"]["class_type"] = "VAEDecodeNope"
        code, _, err = run("compile", self.graph(doc, "other.json"), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertNotIn("removed", err)  # graph.* belongs to another graph name

    def test_the_help_says_so(self):
        code, out, _ = run("compile", "--help")
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("removes", " ".join(out.split()))
        self.assertIn("earlier run", " ".join(out.split()))


def with_primitives(steps_order=None, cfg_order=None):
    """txt2img with steps and cfg set from two primitive nodes in the Controls group: two
    members of the same control kind, neither feeding the other."""
    doc = copy.deepcopy(TXT2IMG)
    doc["steps_p"] = {"class_type": "PrimitiveInt", "inputs": {"value": 28}, "_meta": {"title": "Steps", "ui_role": "cockpit"}}
    doc["cfg_p"] = {"class_type": "PrimitiveFloat", "inputs": {"value": 4.0}, "_meta": {"title": "CFG", "ui_role": "cockpit"}}
    for key, order in (("steps_p", steps_order), ("cfg_p", cfg_order)):
        if order is not None:
            doc[key]["_meta"]["ui_order"] = order
    doc["sampler"]["inputs"]["steps"] = ["steps_p", 0]
    doc["sampler"]["inputs"]["cfg"] = ["cfg_p", 0]
    return doc


class UiOrderTest(Folder):
    """``ui_order`` orders Controls members of one control kind and one step of the data flow,
    lower first; it does not move a member ahead of another kind (the guide says so)."""

    def tops(self, doc, preset="bento"):
        self.assertEqual(run("compile", self.graph(doc), "-o", self.out, "--preset", preset)[0], ExitCode.OK)
        return {node["title"]: node["pos"][1] for node in self.read(".ui.json")["nodes"] if node.get("title")}

    def test_a_lower_order_comes_first_in_every_preset(self):
        for preset in ("bento", "stepped", "swimlanes", "cockpit-split", "tower-preview"):
            with self.subTest(preset=preset):
                plain = self.tops(with_primitives(), preset)
                self.assertLess(plain["CFG"], plain["Steps"])  # by id without ui_order
                swapped = self.tops(with_primitives(1, 2), preset)
                self.assertLess(swapped["Steps"], swapped["CFG"])
                alone = self.tops(with_primitives(steps_order=5), preset)  # members without one go last
                self.assertLess(alone["Steps"], alone["CFG"])

    def test_it_does_not_move_a_member_ahead_of_another_kind(self):
        doc = with_primitives(-5, -4)
        doc["pos"]["_meta"]["ui_order"] = 9
        tops = self.tops(doc)
        self.assertLess(tops["Positive prompt"], tops["Negative prompt"])
        self.assertLess(tops["Negative prompt"], tops["Steps"])
        self.assertLess(tops["Canvas size"], tops["Steps"])


class AuthoringEffectTest(Folder):
    """The report of a compile says which _meta keys took effect on which nodes
    (``metrics.authoring_keys``), and on which nodes a key that is set did nothing."""

    def effects(self, doc):
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.OK, err)
        return {entry["key"]: entry for entry in self.read(".report.json")["metrics"]["authoring_keys"]}

    def test_each_key_names_the_nodes_it_changed(self):
        doc = with_lora(with_primitives(steps_order=1))
        doc["sampler"]["_meta"]["ui_seed"] = "fixed"
        doc["sampler"]["_meta"]["ui_order"] = 3  # not in the Controls group: no effect
        doc["decode"]["_meta"] = {"ui_group": "finish"}
        doc["save"]["_meta"] = {"ui_group": "finish"}
        doc["neg"]["_meta"]["ui_role"] = "pipeline"
        effects = self.effects(doc)
        self.assertEqual(effects["_meta.ui_role"]["nodes"], ["cfg_p", "latent", "neg", "pos", "steps_p"])
        self.assertEqual(effects["_meta.ui_optional"]["nodes"], ["lora"])
        self.assertEqual(effects["_meta.ui_group"]["nodes"], ["decode", "save"])
        self.assertEqual(effects["_meta.ui_seed"]["nodes"], ["sampler"])
        self.assertEqual(effects["_meta.ui_order"]["nodes"], ["steps_p"])
        self.assertEqual(effects["_meta.ui_order"]["no_effect_on"], ["sampler"])
        self.assertIn("lora", effects["_meta.title"]["nodes"])
        self.assertIn("sampler", effects["_meta.title"]["nodes"])
        self.assertNotIn("no_effect_on", effects["_meta.ui_role"])
        self.assertNotIn("_meta.ui_locked", effects)  # E8 names the keys without effect
        canvas = self.read(".ui.json")
        neg = next(node for node in canvas["nodes"] if node.get("title") == "Negative prompt")
        controls = next(group for group in canvas["groups"] if "Controls" in group["title"])
        x, y, w, h = controls["bounding"]
        self.assertFalse(x <= neg["pos"][0] < x + w and y <= neg["pos"][1] < y + h)  # pipeline kept it out

    def test_a_key_another_key_overrides_is_named_as_without_effect(self):
        doc = copy.deepcopy(TXT2IMG)
        doc["pos"]["_meta"]["ui_group"] = "words"  # ui_role cockpit wins: it stays in Controls
        doc["decode"]["_meta"] = {"ui_role": "plumbing", "ui_group": "finish"}  # its group wins: not collapsed
        effects = self.effects(doc)
        self.assertEqual(effects["_meta.ui_group"]["no_effect_on"], ["pos"])
        self.assertEqual(effects["_meta.ui_group"]["nodes"], ["decode"])
        self.assertEqual(effects["_meta.ui_role"]["no_effect_on"], ["decode"])
        canvas = self.read(".ui.json")
        decode = next(node for node in canvas["nodes"] if node["type"] == "VAEDecode")
        self.assertFalse(decode.get("flags", {}).get("collapsed", False))
        self.assertNotIn("Words", [group["title"].split(" ", 1)[1] for group in canvas["groups"]])

    def test_a_graph_without_meta_keys_has_an_empty_list(self):
        doc = copy.deepcopy(TXT2IMG)
        for key, entry in doc.items():
            if key != "_workflow":
                entry.pop("_meta", None)
        self.assertEqual(self.effects(doc), {})


def authored(doc, preset):
    doc = copy.deepcopy(doc)
    doc["_workflow"] = {**doc["_workflow"], "layout": {"preset": preset}}
    return doc


class LayoutTest(Folder):
    """``cwc compile`` lays out and routes the canvas, with the preset chosen by priority."""

    def titles(self):
        return [group["title"] for group in self.read(".ui.json")["groups"]]

    def test_the_canvas_is_grouped_on_the_grid_and_routed(self):
        self.assertEqual(run("compile", self.graph(TXT2IMG), "-o", self.out)[0], ExitCode.OK)
        canvas = self.read(".ui.json")
        self.assertGreater(len(canvas["groups"]), 1)
        for node in canvas["nodes"]:
            self.assertTrue(all(float(v) % 20 == 0 for v in node["pos"]), (node["id"], node["pos"]))
        self.assertGreater(len({tuple(node["pos"]) for node in canvas["nodes"]}), 1)
        self.assertTrue(canvas["extra"]["reroutes"])
        report = self.read(".report.json")
        self.assertEqual(report["metrics"]["preset"], "bento")
        self.assertGreater(report["metrics"]["route"]["rerouted"], 0)
        self.assertEqual(report["metrics"]["route"]["unresolved"], [])
        self.assertIn("G2", report["metrics"]["geometry"])

    def test_the_authored_preset_beats_the_default(self):
        self.assertEqual(run("compile", self.graph(authored(TXT2IMG, "stepped")), "-o", self.out)[0], ExitCode.OK)
        self.assertEqual(self.read(".report.json")["metrics"]["preset"], "stepped")
        self.assertTrue(self.titles()[0].startswith("Step 1 "))

    def test_the_command_line_preset_beats_the_authored_one(self):
        path = self.graph(authored(TXT2IMG, "stepped"))
        self.assertEqual(run("compile", path, "-o", self.out, "--preset", "bento")[0], ExitCode.OK)
        self.assertEqual(self.read(".report.json")["metrics"]["preset"], "bento")
        self.assertTrue(self.titles()[0].startswith("01 "))
        path = self.graph(authored(TXT2IMG, "bento"))
        self.assertEqual(run("compile", path, "-o", self.out, "--preset", "stepped")[0], ExitCode.OK)
        self.assertTrue(self.titles()[0].startswith("Step 1 "))

    def test_swimlanes_compiles(self):
        self.assertEqual(run("compile", self.graph(TXT2IMG), "-o", self.out, "--preset", "swimlanes")[0], ExitCode.OK)
        self.assertEqual(self.read(".report.json")["metrics"]["preset"], "swimlanes")
        self.assertTrue(self.titles()[0].startswith("01 "))

    def refused(self, *argv, doc=TXT2IMG):
        self.assertEqual(run("compile", self.graph(TXT2IMG), "-o", self.out)[0], ExitCode.OK)  # outputs to remove
        code, _, err = run("compile", self.graph(doc), "-o", self.out, *argv)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual([p.name for p in self.out.iterdir()], ["graph.report.json"])
        [finding] = self.read(".report.json")["findings"]
        self.assertEqual(finding["code"], "E7")
        self.assertIn("E7: ", err)
        return finding["message"]

    def test_an_unknown_preset_is_an_input_error(self):
        self.assertEqual(self.refused("--preset", "nope"), "unknown preset 'nope'")
        self.assertEqual(self.refused(doc=authored(TXT2IMG, "nope")), "unknown preset 'nope' in _workflow.layout.preset")

    def test_auto_compiles_under_tower_preview(self):
        self.assertEqual(run("compile", self.graph(TXT2IMG), "-o", self.out, "--preset", "auto")[0], ExitCode.OK)
        self.assertEqual(self.read(".report.json")["metrics"]["preset"], "tower-preview")

    def test_unimplemented_presets_are_input_errors(self):
        # tower-preview given a tray, a preset feature layout does not implement
        presets = load_presets()
        tower = replace(presets["tower-preview"], tray=Tray(1.3, (Zone.PROMPT, Zone.POSTPROCESS)))
        with mock.patch("cwc.pipeline.load_presets", lambda: {**presets, "tower-preview": tower}):
            self.assertIn("'auto' is not available yet", self.refused("--preset", "auto"))
            self.assertIn("preset 'tower-preview' is not available yet", self.refused("--preset", "tower-preview"))

    def test_cockpit_split_compiles(self):
        self.assertEqual(run("compile", self.graph(TXT2IMG), "-o", self.out, "--preset", "cockpit-split")[0], ExitCode.OK)
        self.assertEqual(self.read(".report.json")["metrics"]["preset"], "cockpit-split")



class AutoPresetTest(Folder):
    """``--preset auto`` and an authored ``auto`` choose the same preset; the help names it."""

    def test_auto_from_the_argument_and_from_the_document_agree(self):
        doc = with_lora(TXT2IMG)  # an optional module: the small-graph rule does not hold, so bento
        reports = []
        for path, argv in ((self.graph(doc), ("--preset", "auto")), (self.graph(authored(doc, "auto")), ())):
            self.assertEqual(run("compile", path, "-o", self.out, *argv)[0], ExitCode.OK)
            reports.append(self.read(".report.json")["metrics"])
        self.assertEqual(reports[0]["preset"], "bento")
        self.assertEqual(reports[0]["preset_auto"]["rule"], "no other rule holds")
        self.assertEqual([(m["preset"], m["preset_auto"]) for m in reports][0], (reports[1]["preset"], reports[1]["preset_auto"]))

    def test_the_help_names_auto(self):
        code, out, _ = run("compile", "--help")
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("auto to choose one", " ".join(out.split()))


if __name__ == "__main__":
    unittest.main()
