import io
import json
import os
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

    def test_unknown_type_exits_4_with_three_suggestions(self):
        code, out, err = run("node", "KSamplr")
        self.assertEqual(code, ExitCode.NODEDEFS_MISSING)
        self.assertEqual(out, "")
        self.assertIn("KSampler", err)
        self.assertEqual(err.split("closest: ", 1)[1].strip().count(", "), 2)
        code, out, _ = run("node", "KSamplr", "--json")
        self.assertEqual(code, ExitCode.NODEDEFS_MISSING)
        self.assertEqual(len(json.loads(out)["closest"]), 3)

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
        code, out, err = run("node", "--search", "no such word anywhere")
        self.assertEqual((code, out), (ExitCode.OK, ""))
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
                ExitCode.NODEDEFS_MISSING: ("node", "KSamplr"),
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
