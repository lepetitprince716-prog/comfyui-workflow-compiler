"""What ``cwc compile`` tells its caller on the terminal and in ``--json``: every finding's
message and hint, typo suggestions for ``_meta`` keys and input names, the success summary."""

import copy
import json
import unittest

from tests import support
from tests import test_cli_compile as base
from tests.test_cli_compile import Folder

from tests.graphs import txt2img

from cwc.report import ExitCode

TXT2IMG = base.TXT2IMG
PLAIN = txt2img()  # no _meta, no _workflow
run = base.run


def with_meta(doc, node, **meta):
    doc = copy.deepcopy(doc)
    doc[node].setdefault("_meta", {}).update(meta)
    return doc


class FindingsOnTheTerminalTest(Folder):
    def test_a_warning_and_its_hint_reach_stderr_on_success(self):
        code, _, err = run("compile", self.graph(with_meta(TXT2IMG, "decode", optional=True)), "-o", self.out)
        self.assertEqual(code, ExitCode.OK)
        [line] = [line for line in err.splitlines() if "'optional'" in line and "warning" in line]
        self.assertTrue(line.startswith("E7 warning: node 'decode'"), line)
        self.assertIn("  hint: ", err)

    def test_an_error_hint_reaches_stderr(self):
        doc = copy.deepcopy(TXT2IMG)
        doc["sampler"]["inputs"]["positive"] = ["nowhere", 0]
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        report = json.loads((self.out / "graph.report.json").read_text(encoding="utf-8"))
        [finding] = [f for f in report["findings"] if f["code"] == "E6"]
        self.assertIn(f"E6: {finding['message']}\n  hint: {finding['hint']}\n", err)

    def test_json_carries_the_report_findings(self):
        code, out, _ = run("compile", self.graph(with_meta(TXT2IMG, "decode", optional=True)), "-o", self.out, "--json")
        self.assertEqual(code, ExitCode.OK)
        summary = json.loads(out)
        report = json.loads((self.out / "graph.report.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["finding_list"], report["findings"])
        self.assertIn("warning", {f["severity"] for f in summary["finding_list"]})
        self.assertEqual(summary["findings"]["warning"], sum(f["severity"] == "warning" for f in report["findings"]))

    def test_json_carries_the_findings_of_a_failed_run(self):
        doc = copy.deepcopy(TXT2IMG)
        doc["sampler"]["inputs"]["positive"] = ["nowhere", 0]
        code, out, _ = run("compile", self.graph(doc), "-o", self.out, "--json")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        summary = json.loads(out)
        self.assertEqual([f["code"] for f in summary["finding_list"]], ["E6"])
        self.assertIn("hint", summary["finding_list"][0])



class MetaKeyTest(Folder):
    """``_meta``: an unknown ``ui_*`` key fails, a known key without its prefix warns, other
    tools' keys pass."""

    def test_an_unknown_ui_key_is_an_error_with_a_suggestion(self):
        code, _, err = run("compile", self.graph(with_meta(TXT2IMG, "decode", ui_optinal=True)), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertIn("E7: node 'decode': _meta key 'ui_optinal' is not a cwc key", err)
        self.assertIn("did you mean 'ui_optional'", err)
        self.assertFalse((self.out / "graph.ui.json").exists())

    def test_a_known_key_without_its_prefix_warns_with_the_right_name(self):
        for key, right in (("optional", "ui_optional"), ("group", "ui_group"), ("role", "ui_role")):
            with self.subTest(key=key):
                value = {"optional": True, "group": "extras", "role": "cockpit"}[key]
                code, _, err = run("compile", self.graph(with_meta(TXT2IMG, "decode", **{key: value})), "-o", self.out)
                self.assertEqual(code, ExitCode.OK)
                self.assertIn(f"E7 warning: node 'decode': _meta key {key!r} is ignored; did you mean {right!r}?", err)

    def test_another_tools_key_does_not_fail_the_compile(self):
        code, _, err = run("compile", self.graph(with_meta(TXT2IMG, "decode", some_tool_state={"x": 1})), "-o", self.out)
        self.assertEqual(code, ExitCode.OK)
        self.assertNotIn("E7: ", err)



class OverriddenKeyTest(Folder):
    """A ``_meta`` key that another key of the same node overrides is a warning that names both and
    says how to write it. Removing the overridden key gives the same canvas, which is what makes the
    warning true."""

    def compiled(self, doc, name="graph.json"):
        out = self.out / name.removesuffix(".json")
        code, _, err = run("compile", self.graph(doc, name), "-o", out)
        self.assertEqual(code, ExitCode.OK, err)
        report = json.loads((out / f"{name.removesuffix('.json')}.report.json").read_text(encoding="utf-8"))
        warnings = [f for f in report["findings"] if f["severity"] == "warning" and f["data"].get("overridden_by")]
        return err, warnings, (out / f"{name.removesuffix('.json')}.ui.json").read_bytes(), report

    def check(self, doc, node, key, winner):
        err, warnings, canvas, report = self.compiled(doc)
        [warning] = warnings
        self.assertEqual((warning["code"], warning["nodes"]), ("E7", [node]))
        self.assertEqual((warning["data"]["key"], warning["data"]["overridden_by"]), (key, winner))
        self.assertIn(f"_meta.{key}", warning["message"])
        self.assertIn(f"_meta.{winner}", warning["message"])
        self.assertTrue(warning["hint"])
        self.assertIn(f"E7 warning: {warning['message']}\n  hint: {warning['hint']}\n", err)
        self.assertTrue(report["ok"])
        stripped = copy.deepcopy(doc)
        del stripped[node]["_meta"][key]
        _, none, plain, _ = self.compiled(stripped, "stripped.json")
        self.assertEqual(none, [])
        self.assertEqual(canvas, plain)  # the key did nothing
        return warning

    def test_ui_group_on_a_cockpit_node(self):
        warning = self.check(with_meta(TXT2IMG, "pos", ui_group="words"), "pos", "ui_group", "ui_role")
        self.assertIn("cockpit", warning["message"])

    def test_ui_role_plumbing_on_a_node_in_a_group(self):
        doc = with_meta(TXT2IMG, "decode", ui_role="plumbing", ui_group="finish")
        warning = self.check(doc, "decode", "ui_role", "ui_group")
        self.assertIn("plumbing", warning["message"])

    def test_ui_role_on_an_optional_node(self):
        for role in ("cockpit", "plumbing"):
            with self.subTest(role=role):
                self.check(with_meta(base.with_lora(TXT2IMG), "lora", ui_role=role), "lora", "ui_role", "ui_optional")

    def test_keys_that_do_not_override_each_other_do_not_warn(self):
        for doc in (
            with_meta(base.with_lora(TXT2IMG), "lora", ui_group="styles"),  # names the optional module
            with_meta(TXT2IMG, "decode", ui_role="plumbing"),
            with_meta(TXT2IMG, "decode", ui_role="pipeline", ui_group="finish"),
            with_meta(TXT2IMG, "decode", ui_group="finish"),
            with_meta(base.with_primitives(1, 2), "steps_p", ui_order=3),
            with_meta(TXT2IMG, "decode", ui_order=1),  # not in Controls: nothing else explains it, so no warning
        ):
            _, warnings, _, report = self.compiled(doc)
            self.assertEqual(warnings, [])
        self.assertEqual(self.compiled(TXT2IMG)[1], [])

    def test_the_report_counts_a_group_that_names_an_optional_module_as_effective(self):
        _, _, _, report = self.compiled(with_meta(base.with_lora(TXT2IMG), "lora", ui_group="styles"))
        group = next(entry for entry in report["metrics"]["authoring_keys"] if entry["key"] == "_meta.ui_group")
        self.assertEqual(group["nodes"], ["lora"])
        self.assertNotIn("no_effect_on", group)

    def test_every_overridden_node_gets_its_own_warning(self):
        doc = with_meta(with_meta(TXT2IMG, "pos", ui_group="words"), "neg", ui_group="words")
        _, warnings, _, _ = self.compiled(doc)
        self.assertEqual([w["nodes"] for w in warnings], [["neg"], ["pos"]])


class NoEffectKeyTest(Folder):
    """Keys cwc parses but does not use yet: accepted, and said so in one E8 info finding per
    compile, on one stderr line and in the report with the data key by key, without changing the
    outcome."""

    def compile(self, doc, *argv):
        code, out, err = run("compile", self.graph(doc), "-o", self.out, *argv)
        report = json.loads((self.out / "graph.report.json").read_text(encoding="utf-8"))
        infos = [f for f in report["findings"] if f["severity"] == "info" and f["code"].startswith("E")]
        return code, out, err, report, infos

    # PLAIN, not the fixture graph: the fixture sets some of these keys itself

    def test_each_key_is_named_in_the_one_info_and_the_run_stays_ok(self):
        cases = {
            ("_meta.ui_locked", "sampler"): with_meta(PLAIN, "sampler", ui_locked=["cfg"]),
            ("_meta.ui_expose", "pos"): with_meta(PLAIN, "pos", ui_expose=["text"]),
            ("_workflow.locale", None): {**PLAIN, "_workflow": {"format": 1, "locale": "de"}},
        }
        for (key, node), graph in cases.items():
            with self.subTest(key=key):
                code, _, err, report, infos = self.compile(graph)
                self.assertEqual(code, ExitCode.OK, err)
                self.assertTrue(report["ok"])
                [info] = infos
                self.assertEqual(info["code"], "E8")
                self.assertEqual(info["data"]["keys"], [{"key": key, "nodes": [node]} if node else {"key": key}])
                self.assertIn(key, info["message"])
                self.assertIn(f"E8 info: {info['message']}\n", err)
                self.assertTrue(err.splitlines()[-1].startswith("cwc compile: ok; "), err)
                self.assertIn(" 0 warnings;", err.splitlines()[-1])

    def test_several_keys_make_one_line_that_ends_at_the_guide(self):
        doc = with_meta(PLAIN, "pos", ui_expose=["text"])
        doc = with_meta(doc, "neg", ui_expose=["text"])
        doc = with_meta(doc, "sampler", ui_locked=True)
        doc["_workflow"] = {"format": 1, "locale": "de"}
        code, _, err, report, infos = self.compile(doc)
        self.assertEqual(code, ExitCode.OK, err)
        [info] = infos
        lines = [line for line in err.splitlines() if " info: " in line]
        self.assertEqual(lines, [f"E8 info: {info['message']}"])
        self.assertTrue(info["message"].endswith("`cwc guide` says what each key does"), info["message"])
        self.assertEqual(
            sorted((entry["key"], tuple(entry.get("nodes", ()))) for entry in info["data"]["keys"]),
            [("_meta.ui_expose", ("pos", "neg")), ("_meta.ui_locked", ("sampler",)), ("_workflow.locale", ())],
        )
        for text in ("_meta.ui_expose (nodes 'pos', 'neg')", "_meta.ui_locked (node 'sampler')", "_workflow.locale"):
            self.assertIn(text, info["message"])
        self.assertEqual(sorted(info["nodes"]), ["neg", "pos", "sampler"])
        self.assertEqual(report["findings"][-1]["title"], "authoring key is accepted but has no effect yet")
        self.assertNotIn("E7", [f["code"] for f in report["findings"]])

    def test_json_lists_the_info_and_keeps_ok(self):
        code, out, _, _, _ = self.compile(with_meta(PLAIN, "sampler", ui_locked=["cfg"]), "--json")
        summary = json.loads(out)
        self.assertEqual((code, summary["ok"], summary["exit_code"]), (ExitCode.OK, True, 0))
        self.assertEqual(summary["findings"]["info"], 1)
        self.assertEqual([f["code"] for f in summary["finding_list"] if f["severity"] == "info"], ["E8"])

    def test_keys_that_do_something_get_no_info(self):
        doc = with_meta(PLAIN, "sampler", title="Main", ui_role="cockpit", ui_seed="fixed")
        doc = with_meta(doc, "decode", ui_group="finish")
        doc["_workflow"] = {"format": 1, "layout": {"preset": "bento"}, "groups": {"finish": {"title": "Finishing"}}}
        code, _, err, _, infos = self.compile(doc)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertEqual(infos, [])
        self.assertNotIn(" info: ", err)

    def test_the_readme_code_table_lists_every_authoring_code(self):
        from cwc.report import TITLES, Code

        readme = (support.ROOT / "README.md").read_text(encoding="utf-8")
        for code in Code:
            if code.value.startswith("E"):
                self.assertIn(f"| {code.value} | {TITLES[code]} |", readme)


class InputTypoTest(Folder):
    def misspelt(self, wrong, right):
        doc = copy.deepcopy(TXT2IMG)
        inputs = doc["sampler"]["inputs"]
        inputs[wrong] = inputs.pop(right)
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        report = json.loads((self.out / "graph.report.json").read_text(encoding="utf-8"))
        return code, err, [f for f in report["findings"] if f["severity"] == "error"]

    def test_a_misspelt_widget_input_is_one_error_with_a_suggestion(self):
        code, err, errors = self.misspelt("sampler_nme", "sampler_name")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual([f["code"] for f in errors], ["E2"])
        self.assertIn("E2: node 'sampler': KSampler has no input 'sampler_nme'; did you mean 'sampler_name'?", err)
        self.assertNotIn("E3", err)

    def test_a_misspelt_link_input_is_one_error_with_a_suggestion(self):
        code, err, errors = self.misspelt("postive", "positive")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual([f["code"] for f in errors], ["E2"])
        self.assertIn("did you mean 'positive'?", err)

    def test_a_name_close_to_nothing_gets_no_suggestion(self):
        doc = copy.deepcopy(TXT2IMG)
        doc["sampler"]["inputs"]["zzzz"] = 1
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertIn("E2: node 'sampler': KSampler has no input 'zzzz'\n", err)



def fancy(doc):
    """txt2img with its sampler replaced by a type cwc has no definition for."""
    doc = copy.deepcopy(doc)
    doc["sampler"]["class_type"] = "MyFancySampler"
    return doc


FANCY_INFO = {
    "MyFancySampler": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "steps": ["INT", {"default": 20, "min": 1, "max": 100}],
                "cfg": ["FLOAT", {"default": 7.0, "min": 0.0, "max": 30.0}],
                "sampler_name": [["euler", "dpmpp_2m"]],
                "scheduler": [["normal", "karras"]],
            }
        },
        "input_order": {"required": ["model", "positive", "negative", "latent_image", "steps", "cfg", "sampler_name", "scheduler"]},
        "output": ["LATENT"],
        "output_is_list": [False],
        "output_name": ["LATENT"],
        "name": "MyFancySampler",
        "display_name": "My fancy sampler",
        "category": "sampling",
        "output_node": False,
    }
}


class UnknownTypeHintTest(Folder):
    def test_the_hint_names_the_two_ways_through(self):
        code, _, err = run("compile", self.graph(fancy(TXT2IMG)), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        lines = err.splitlines()
        hint = lines[lines.index("E1: node 'sampler': unknown type 'MyFancySampler'") + 1]
        self.assertTrue(hint.startswith("  hint: "), hint)
        self.assertIn("--allow-unknown", hint)
        self.assertIn("--nodedefs", hint)
        self.assertIn("/api/object_info", hint)
        self.assertNotIn("cwc snapshot", err)

    def test_no_hint_names_a_command_cwc_does_not_have(self):
        doc = fancy(TXT2IMG)
        doc["sampler"]["_meta"]["ui_optional"] = True
        for argv in ((), ("--allow-unknown",)):
            with self.subTest(argv=argv):
                _, _, err = run("compile", self.graph(doc), "-o", self.out, *argv)
                self.assertIn("hint: ", err)
                self.assertNotIn("cwc snapshot", err)

    def test_the_nodedefs_the_hint_describes_make_the_type_known(self):
        info = self.dir / "object_info.json"
        info.write_text(json.dumps(FANCY_INFO), encoding="utf-8")
        code, _, err = run("compile", self.graph(fancy(TXT2IMG)), "-o", self.out, "--nodedefs", info)
        self.assertNotIn("E1", err)
        self.assertEqual(code, ExitCode.OK, err)



class UnknownTypeEchoTest(Folder):
    """An unknown type is one E1; the links that name its outputs are not reported again."""

    def unknown_loader(self):
        doc = copy.deepcopy(PLAIN)
        doc["ckpt"]["class_type"] = "MyFancyLoader"  # pos, neg, sampler and decode name its outputs
        return doc

    def errors(self):
        report = json.loads((self.out / "graph.report.json").read_text(encoding="utf-8"))
        return [f["code"] for f in report["findings"] if f["severity"] == "error"]

    def test_links_by_output_name_add_no_error(self):
        code, _, err = run("compile", self.graph(self.unknown_loader()), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual(self.errors(), ["E1"])
        self.assertNotIn("E6", err)
        self.assertIn("cwc compile: 1 error(s);", err)

    def test_with_allow_unknown_the_named_outputs_are_still_errors(self):
        code, _, err = run("compile", self.graph(self.unknown_loader()), "-o", self.out, "--allow-unknown")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual(sorted(frozenset(self.errors())), ["E6"])
        self.assertIn("whose type is unknown", err)

    def test_a_link_to_a_missing_node_is_still_an_error(self):
        doc = self.unknown_loader()
        doc["sampler"]["inputs"]["positive"] = ["nowhere", 0]
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual(sorted(self.errors()), ["E1", "E6"])
        self.assertIn("links to node 'nowhere', which does not exist", err)


class SummaryTest(Folder):
    def test_success_prints_one_summary_line(self):
        code, _, err = run("compile", self.graph(base.with_lora(TXT2IMG)), "-o", self.out, "--preset", "auto")
        self.assertEqual(code, ExitCode.OK)
        canvas = json.loads((self.out / "graph.ui.json").read_text(encoding="utf-8"))
        groups = len(canvas["groups"])
        [notice] = [line for line in err.splitlines(keepends=True) if line.startswith("E8 info: ")]  # keys of the fixture
        self.assertEqual(
            err,
            notice + f"cwc compile: ok; preset bento (auto: no other rule holds); {groups} groups, 1 optional module, "
            f"0 warnings; preview: cwc render {self.out / 'graph.ui.json'} -o graph.svg\n",
        )

    def test_the_summary_counts_warnings_and_names_a_chosen_preset(self):
        doc = with_meta(TXT2IMG, "decode", optional=True)
        code, _, err = run("compile", self.graph(doc), "-o", self.out, "--preset", "stepped")
        self.assertEqual(code, ExitCode.OK)
        summary = err.splitlines()[-1]
        self.assertTrue(summary.startswith("cwc compile: ok; preset stepped; "), summary)
        self.assertIn(" 0 optional modules, 1 warning;", summary)

    def test_a_failed_run_prints_no_summary(self):
        doc = copy.deepcopy(TXT2IMG)
        doc["sampler"]["inputs"]["positive"] = ["nowhere", 0]
        _, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertNotIn("cwc compile: ok", err)


class PresetsCommandTest(unittest.TestCase):
    def test_lists_every_preset_and_auto_with_a_description(self):
        from cwc.layout.presets import load_presets

        code, out, err = run("presets")
        self.assertEqual((code, err), (ExitCode.OK, ""))
        names = [line.split()[0] for line in out.splitlines() if line and not line.startswith(" ")]
        self.assertEqual(names, [*load_presets(), "auto"])
        for line in out.splitlines():
            if line and not line.startswith(" "):
                self.assertGreater(len(line.split(None, 1)[1]), 20, line)

    def test_json(self):
        from cwc.layout.presets import load_presets

        code, out, _ = run("presets", "--json")
        self.assertEqual(code, ExitCode.OK)
        rows = json.loads(out)
        self.assertEqual([row["name"] for row in rows], [*load_presets(), "auto"])
        self.assertTrue(all(row["description"] and row["use_when"] for row in rows))

    def test_the_help_names_the_command(self):
        _, out, _ = run("--help")
        self.assertIn("presets", out)


class PresetDescriptionTest(Folder):
    """What `cwc presets` says each preset does is what the code does: auto's rules and their
    thresholds, and the geometry each description names, measured on a compiled canvas."""

    def descriptions(self):
        code, out, _ = run("presets", "--json")
        self.assertEqual(code, ExitCode.OK)
        return {row["name"]: row["description"] for row in json.loads(out)}

    def groups(self, preset):
        out = self.out / preset
        code, _, err = run("compile", self.graph(base.with_lora(TXT2IMG)), "-o", out, "--preset", preset)
        self.assertEqual(code, ExitCode.OK, err)
        canvas = json.loads((out / "graph.ui.json").read_text(encoding="utf-8"))
        return [(g["title"], *g["bounding"]) for g in canvas["groups"]]

    def test_auto_names_its_rules_in_order_with_the_code_thresholds(self):
        from cwc.annotate.types import PlanCounts
        from cwc.pipeline import AUTO_LARGE_NODES, AUTO_PARALLEL_BRANCHES, AUTO_SMALL_NODES, choose_auto

        text = self.descriptions()["auto"]
        rules = [
            (f"at most {AUTO_SMALL_NODES} nodes and no optional module", "tower-preview"),
            (f"{AUTO_PARALLEL_BRANCHES} or more control or mask branches", "swimlanes"),
            (f"{AUTO_LARGE_NODES} nodes or more", "cockpit-split"),
            ("otherwise", "bento"),
        ]
        places = []
        for condition, preset in rules:
            self.assertIn(f"{condition}: {preset}", text)
            places.append(text.index(f"{condition}: {preset}"))
        self.assertEqual(places, sorted(places))
        # the same boundaries, asked of the code
        counts = dict(nodes=AUTO_SMALL_NODES, optional_modules=0, control_branches=0)
        self.assertEqual(choose_auto(PlanCounts(**counts)).preset, "tower-preview")
        self.assertEqual(choose_auto(PlanCounts(**{**counts, "nodes": AUTO_SMALL_NODES + 1})).preset, "bento")
        self.assertEqual(choose_auto(PlanCounts(**{**counts, "nodes": 20, "control_branches": AUTO_PARALLEL_BRANCHES})).preset, "swimlanes")
        self.assertEqual(choose_auto(PlanCounts(**{**counts, "nodes": AUTO_LARGE_NODES, "control_branches": AUTO_PARALLEL_BRANCHES - 1})).preset, "cockpit-split")

    def test_no_description_promises_a_link_corridor(self):
        # the router does not route through corridors yet (docs/ARCHITECTURE.md, layout rules)
        for name, text in self.descriptions().items():
            with self.subTest(preset=name):
                self.assertNotIn("corridor", text)

    def test_the_gaps_and_sizes_named_are_on_the_canvas(self):
        from cwc.canvas.geometry import snap_up
        from cwc.layout.presets import load_presets
        from cwc.layout.section import BAND_GAP, SCREEN_W

        text = self.descriptions()
        presets = load_presets()
        # swimlanes: an empty strip above the row, the canvas starts that much lower than bento's
        self.assertIn(f"empty strip {BAND_GAP} px high above each row", text["swimlanes"])
        lower = min(g[2] for g in self.groups("swimlanes")) - min(g[2] for g in self.groups("bento"))
        self.assertEqual(lower, BAND_GAP)
        # cockpit-split: a wider gap after Controls than between the other groups of the row
        self.assertIn(f"a gap of {BAND_GAP} px", text["cockpit-split"])
        row = sorted(self.groups("cockpit-split"), key=lambda g: g[1])
        controls = next(g for g in row if g[0].endswith("Controls"))
        after = min((g for g in row if g[1] > controls[1] and g[2] == controls[2]), key=lambda g: g[1])
        self.assertEqual(after[1] - (controls[1] + controls[3]), BAND_GAP)
        # tower-preview: the output group's size
        preview = presets["tower-preview"].preview
        width = snap_up(round(preview.share * SCREEN_W, 6))
        self.assertIn(f"at least {width} px wide and {preview.min_height} px high", text["tower-preview"])
        output = next(g for g in self.groups("tower-preview") if g[0].endswith("Output"))
        self.assertGreaterEqual((output[3], output[4]), (width, preview.min_height))
        # bento: the wrap ratio
        self.assertIn(f"wider than {presets['bento'].wrap_ratio:g} times its height", text["bento"])
        # stepped: step titles, the models and Controls groups one above the other
        self.assertIn("Step 1, Step 2", text["stepped"])
        stepped = self.groups("stepped")
        self.assertTrue(all(g[0].startswith(f"Step {i} ") for i, g in enumerate(stepped, 1)), stepped)
        models, controls = (next(g for g in stepped if g[0].endswith(t)) for t in ("Models", "Controls"))
        self.assertIn("one above the other in the first column", text["stepped"])
        self.assertEqual(models[1], controls[1])
        self.assertLess(models[2] + models[4], controls[2])


class UnknownPresetTest(Folder):
    def test_the_error_lists_the_names(self):
        code, _, err = run("compile", self.graph(TXT2IMG), "-o", self.out, "--preset", "bentoo")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertIn("unknown preset 'bentoo'", err)
        for name in ("auto", "bento", "cockpit-split", "stepped", "swimlanes", "tower-preview"):
            self.assertIn(name, err.split("unknown preset 'bentoo'", 1)[1])



def guide_example():
    text = run("guide")[1]
    block = text.split("```json\n", 1)[1].split("```", 1)[0]
    return text, json.loads(block)


class GuideTest(Folder):
    def test_prints_the_bundled_guide(self):
        from cwc.resources import read_bytes

        code, out, err = run("guide")
        self.assertEqual((code, err), (ExitCode.OK, ""))
        self.assertEqual(out, read_bytes("guide.md").decode("utf-8"))

    def test_names_every_key_the_compiler_reads(self):
        from cwc.normalize.intents import _KEYS
        from cwc.normalize.workflow_info import GROUP_KEYS, LAYOUT_KEYS, MODEL_KEYS, TOP_KEYS

        text = run("guide")[1]
        for key in [*_KEYS, *TOP_KEYS, *LAYOUT_KEYS, *GROUP_KEYS, *MODEL_KEYS]:
            self.assertIn(f"`{key}`" if key in _KEYS or key in TOP_KEYS else key, text)

    def test_the_example_compiles_as_described(self):
        _, doc = guide_example()
        code, _, err = run("compile", self.graph(doc), "-o", self.out)
        self.assertEqual(code, ExitCode.OK, err)
        self.assertIn(", 1 optional module, 0 warnings;", err)
        canvas = self.read(".ui.json")
        modes = {node["type"]: node["mode"] for node in canvas["nodes"]}
        self.assertEqual(modes["LoraLoader"], 4)
        controls = next(g for g in canvas["groups"] if g["title"].endswith("Controls"))
        x, y, w, h = controls["bounding"]
        inside = {n["type"] for n in canvas["nodes"] if x <= n["pos"][0] < x + w and y <= n["pos"][1] < y + h}
        self.assertEqual(inside, {"CLIPTextEncode", "EmptyLatentImage", "KSampler"})
        sampler = next(n for n in canvas["nodes"] if n["type"] == "KSampler")
        self.assertIn("fixed", sampler["widgets_values"])
        self.assertNotIn("LoraLoader", {node["class_type"] for node in self.read(".api.json").values()})

    def test_the_help_names_the_command(self):
        out = run("--help")[1]
        self.assertIn("guide", out)


if __name__ == "__main__":
    unittest.main()
