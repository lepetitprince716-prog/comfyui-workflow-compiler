import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import support

from cwc.cli import main
from cwc.report import ExitCode

CUSTOM = {
    "Zeta": {
        "input": {"required": {"amount": ["FLOAT", {"default": 0.5}], "image": ["IMAGE", {}]}},
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "display_name": "Zeta Filter",
        "category": "testing",
        "python_module": "custom_nodes.zeta",
    }
}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out, err)
    return code, out.getvalue(), err.getvalue()


class NodeShowTest(unittest.TestCase):
    def test_truth_type(self):
        code, out, err = run("node", "KSampler")
        self.assertEqual((code, err), (ExitCode.OK, ""))
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 10)
        self.assertIn("tier: truth", lines[0])
        widgets = next(line for line in lines if line.startswith("widgets:"))
        self.assertIn("seed=0, control_after_generate=", widgets)
        self.assertTrue(any(line.startswith("inputs:") and "model: MODEL" in line for line in lines))
        self.assertTrue(any(line.startswith("outputs:") and "LATENT" in line for line in lines))

    def test_json(self):
        code, out, _ = run("node", "KSampler", "--json")
        self.assertEqual(code, ExitCode.OK)
        doc = json.loads(out)
        self.assertEqual(doc["tier"], "truth")
        names = [w["name"] for w in doc["widgets"]]
        self.assertEqual(names[names.index("seed") + 1], "control_after_generate")
        self.assertEqual(doc["inputs"][0], {"name": "model", "type": "MODEL", "widget": None, "optional": False})

    def test_builtin(self):
        code, out, _ = run("node", "Note")
        self.assertEqual(code, ExitCode.OK)
        self.assertIn("tier: builtin", out)

    def test_unknown_type_is_an_input_error_with_three_suggestions(self):
        # A name that is not a node type is a wrong input (3); 4 stays for definitions that
        # cannot be loaded at all.
        code, out, err = run("node", "KSamplr")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual(out, "")
        self.assertIn("KSampler", err)
        self.assertEqual(err.split("closest: ", 1)[1].strip().count(", "), 2)
        code, out, err = run("node", "KSamplr", "--json")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual(len(json.loads(out)["closest"]), 3)
        self.assertIn("KSampler", err)

    def test_type_and_search_are_exclusive(self):
        self.assertEqual(run("node")[0], ExitCode.USAGE)
        self.assertEqual(run("node", "KSampler", "--search", "x")[0], ExitCode.USAGE)

    def test_usage_errors_and_help_go_to_the_given_streams(self):
        code, out, err = run("compile")
        self.assertEqual((code, out), (ExitCode.USAGE, ""))
        self.assertIn("usage:", err)
        code, out, err = run("--help")
        self.assertEqual((code, err), (ExitCode.OK, ""))
        self.assertIn("usage:", out)


LETTERS = [f"choice_{i:02d}" for i in range(25)]
COMBOS = {
    "Picker": {
        "input": {
            "required": {
                "short": [["a", "b", "c"], {}],
                "long": [LETTERS, {}],
                "strength": ["FLOAT", {"default": 1.0}],
                "image": ["IMAGE", {}],
            }
        },
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "display_name": "Picker",
        "category": "testing",
        "python_module": "custom_nodes.picker",
    }
}


class ComboOptionsTest(unittest.TestCase):
    """A dropdown input lists its values: all of them in --json, the first 20 in text."""

    def picker(self, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "object_info.json"
            path.write_text(json.dumps(COMBOS), encoding="utf-8")
            return run("node", "Picker", "--nodedefs", str(path), *extra)

    def test_text_lists_each_dropdown_after_the_widgets_line(self):
        code, out, err = self.picker()
        self.assertEqual((code, err), (ExitCode.OK, ""))
        lines = out.splitlines()
        widgets = next(i for i, line in enumerate(lines) if line.startswith("widgets:"))
        self.assertEqual(lines[widgets + 1], '  short options: "a", "b", "c"')
        long_line = lines[widgets + 2]
        self.assertTrue(long_line.startswith("  long options: "), long_line)
        shown = re.findall(r'"([^"]+)"', long_line)
        self.assertEqual(shown, LETTERS[:20])
        self.assertTrue(long_line.endswith(f"(20 of {len(LETTERS)}; --json lists all)"), long_line)
        self.assertFalse(any("strength options" in line or "image options" in line for line in lines))

    def test_json_gives_every_value_wherever_the_input_appears(self):
        code, out, _ = self.picker("--json")
        self.assertEqual(code, ExitCode.OK)
        doc = json.loads(out)
        inputs = {entry["name"]: entry for entry in doc["inputs"]}
        widgets = {entry["name"]: entry for entry in doc["widgets"]}
        self.assertEqual(inputs["short"]["options"], ["a", "b", "c"])
        self.assertEqual(widgets["short"]["options"], ["a", "b", "c"])
        self.assertEqual(inputs["long"]["options"], LETTERS)
        self.assertEqual(widgets["long"]["options"], LETTERS)
        self.assertNotIn("options", inputs["image"])
        self.assertNotIn("options", inputs["strength"])
        self.assertNotIn("options", widgets["strength"])

    def test_ksampler_sampler_name_is_listed_where_the_trial_looked(self):
        code, out, _ = run("node", "KSampler", "--json")
        doc = json.loads(out)
        sampler = next(entry for entry in doc["inputs"] if entry["name"] == "sampler_name")
        self.assertIn("euler", sampler["options"])
        widget = next(entry for entry in doc["widgets"] if entry["name"] == "sampler_name")
        self.assertEqual(widget["options"], sampler["options"])
        control = next(entry for entry in doc["widgets"] if entry["name"] == "control_after_generate")
        self.assertNotIn("options", control)
        code, text, _ = run("node", "KSampler")
        line = next(line for line in text.splitlines() if line.startswith("  sampler_name options: "))
        self.assertIn(f"(20 of {len(sampler['options'])}; --json lists all)", line)

    def test_a_dropdown_without_values_says_where_they_come_from(self):
        code, out, _ = run("node", "CheckpointLoaderSimple", "--json")
        entry = next(entry for entry in json.loads(out)["inputs"] if entry["name"] == "ckpt_name")
        self.assertEqual(entry["options"], [])
        code, out, _ = run("node", "CheckpointLoaderSimple")
        self.assertIn("  ckpt_name options: none in these definitions; --nodedefs reads a server's list", out.splitlines())


class NodeSearchTest(unittest.TestCase):
    def test_search(self):
        code, out, _ = run("node", "--search", "ksampler")
        self.assertEqual(code, ExitCode.OK)
        rows = [line.split("\t") for line in out.splitlines()]
        self.assertIn("KSampler", [row[0] for row in rows])
        self.assertTrue(all(len(row) == 4 for row in rows))
        self.assertEqual([row[0] for row in rows], sorted(row[0] for row in rows))

    def test_search_json_and_no_match(self):
        code, out, _ = run("node", "--search", "ksampler", "--json")
        self.assertEqual(code, ExitCode.OK)
        self.assertTrue(all(set(row) == {"type", "display_name", "category", "tier"} for row in json.loads(out)))

    def test_no_match_exits_1_with_empty_output(self):
        code, out, err = run("node", "--search", "no such word anywhere")
        self.assertEqual((code, out), (ExitCode.CHECK_FAILED, ""))
        self.assertIn("no node type matches", err)
        self.assertEqual(len(err.splitlines()), 1)
        code, out, err = run("node", "--search", "no such word anywhere", "--json")
        self.assertEqual((code, json.loads(out)), (ExitCode.CHECK_FAILED, []))
        self.assertIn("no node type matches", err)


class NodedefsOptionTest(unittest.TestCase):
    def test_file_adds_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "object_info.json"
            path.write_text(json.dumps(CUSTOM), encoding="utf-8")
            code, out, _ = run("node", "Zeta", "--nodedefs", str(path))
            self.assertEqual(code, ExitCode.OK)
            self.assertIn("tier: derived", out)
            self.assertIn("amount=0.5", out)
            code, out, _ = run("node", "KSampler", "--nodedefs", str(path))
            self.assertIn("tier: truth", out)  # core stays available

    def test_unreadable_source_exits_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run("node", "KSampler", "--nodedefs", str(Path(tmp) / "absent.json"))
        self.assertEqual(code, ExitCode.NODEDEFS_MISSING)
        self.assertIn("cannot read node definitions", err)


class ModuleEntryTest(unittest.TestCase):
    def test_python_dash_m(self):
        env = {**os.environ, "PYTHONPATH": str(support.ROOT / "src")}
        result = subprocess.run(
            [sys.executable, "-m", "cwc", "node", "KSampler"], capture_output=True, text=True, env=env, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tier: truth", result.stdout)

    def test_a_closed_stdout_is_not_an_error(self):
        # ``cwc node --search e | head -1``: the reader leaves after one line.
        env = {**os.environ, "PYTHONPATH": str(support.ROOT / "src")}
        child = subprocess.Popen(
            [sys.executable, "-m", "cwc", "node", "--search", "e"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert child.stdout is not None and child.stderr is not None
        self.assertTrue(child.stdout.readline())
        child.stdout.close()
        stderr = child.stderr.read()
        child.stderr.close()
        self.assertEqual(child.wait(timeout=60), 0)
        self.assertEqual(stderr, b"")

    def test_a_closed_stderr_keeps_every_exit_code(self):
        # ``cwc ... 2>&1 | head -1`` once the reader has left: both streams are a closed pipe.
        env = {**os.environ, "PYTHONPATH": str(support.ROOT / "src")}

        def closed_run(*argv):
            read_end, write_end = os.pipe()
            os.close(read_end)
            try:
                command = [sys.executable, "-m", "cwc", *argv]
                return subprocess.run(command, stdout=write_end, stderr=write_end, env=env, check=False).returncode
            finally:
                os.close(write_end)

        with tempfile.TemporaryDirectory() as tmp:
            graph = Path(tmp) / "graph.json"
            doc = json.loads((support.FIXTURES / "normalize" / "txt2img.json").read_text(encoding="utf-8"))
            doc["stray"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}  # fails N1
            graph.write_text(json.dumps(doc), encoding="utf-8")
            cases = {
                ExitCode.OK: ("compile", str(support.FIXTURES / "normalize" / "txt2img.json"), "-o", f"{tmp}/a"),
                ExitCode.CHECK_FAILED: ("compile", str(graph), "-o", f"{tmp}/b"),
                ExitCode.USAGE: ("compile",),
                ExitCode.INPUT_ERROR: ("compile", f"{tmp}/absent.json", "-o", f"{tmp}/c"),
                ExitCode.NODEDEFS_MISSING: ("node", "KSampler", "--nodedefs", f"{tmp}/absent.json"),
            }
            for expected, argv in cases.items():
                with self.subTest(expected=expected.name):
                    self.assertEqual(closed_run(*argv), expected)
            self.assertEqual(closed_run("--help"), ExitCode.OK)


class BrokenPipeTest(unittest.TestCase):
    class _Closed(io.StringIO):
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

    def test_a_write_to_a_closed_pipe_returns_ok_without_a_message(self):
        out, err = self._Closed(), io.StringIO()
        self.assertEqual(main(["node", "KSampler"], out, err), ExitCode.OK)
        self.assertEqual(err.getvalue(), "")

    def test_a_closed_pipe_keeps_the_exit_code_of_a_failed_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Path(tmp) / "graph.json"
            doc = json.loads((support.FIXTURES / "normalize" / "txt2img.json").read_text(encoding="utf-8"))
            doc["stray"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}  # fails N1
            graph.write_text(json.dumps(doc), encoding="utf-8")
            err = io.StringIO()
            code = main(["compile", str(graph), "-o", str(Path(tmp) / "out"), "--json"], self._Closed(), err)
        self.assertEqual(code, ExitCode.CHECK_FAILED)

    def test_a_flush_to_a_closed_pipe_returns_ok_too(self):
        class Late(io.StringIO):
            def flush(self):
                raise BrokenPipeError(32, "Broken pipe")

        out, err = Late(), io.StringIO()
        self.assertEqual(main(["node", "KSampler"], out, err), ExitCode.OK)
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
