import io
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.cli import main
from cwc.nodedefs.learn import NO_TEMPLATES
from cwc.nodedefs.registry import NodeDefs
from cwc.report import ExitCode
from tests.fixtures.ingest.pngs import SIGNATURE, make_png, text_chunk
from tests.fixtures.learn.samples import (
    OBJECT_INFO,
    base_defs,
    canvas,
    node,
    prompt,
    stack_inputs,
    stack_node,
)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out, err)
    return code, out.getvalue(), err.getvalue()


def stack_docs(*, ver="1.2.0", text="hello"):
    """A canvas and the prompt of one run with a stack node."""
    return canvas(stack_node(3, ver=ver, text=text)), prompt(n3=("Xi Stack", stack_inputs(text)))


def dumps(doc):
    return json.dumps(doc, ensure_ascii=True)


class Folder:
    """Files of one test, in a temporary folder."""

    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def png(self, name, ui, api):
        path = self.root / name
        path.write_bytes(make_png(text_chunk("workflow", dumps(ui)), text_chunk("prompt", dumps(api))))
        return str(path)

    def pair(self, stem, ui, api, *, suffixes=("_ui.json", "_api.json")):
        paths = [self.root / (stem + suffix) for suffix in suffixes]
        paths[0].write_text(dumps(ui), encoding="utf-8")
        paths[1].write_text(dumps(api), encoding="utf-8")
        return tuple(str(p) for p in paths)

    def write(self, name, data):
        path = self.root / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return str(path)

    def out(self, name="templates.json"):
        return str(self.root / name)

    def nodedefs(self):
        return self.write("object_info.json", dumps(OBJECT_INFO))


class LearnCliTest(unittest.TestCase):
    """Runs ``cwc learn``; the registry load (seconds) is replaced by the shared one, and the
    first test below runs the real load."""

    def setUp(self):
        self.folder = Folder(self)
        defs = base_defs()  # before the patch: building it calls the real load
        patch = mock.patch.object(NodeDefs, "load", side_effect=lambda **_: defs)
        self.load = patch.start()
        self.addCleanup(patch.stop)

    def learn(self, *files, extra=()):
        return run("learn", *files, "-o", self.folder.out(), "--nodedefs", self.folder.nodedefs(), *extra)

    def templates(self):
        return json.loads(Path(self.folder.out()).read_text(encoding="utf-8"))

    def test_png_with_workflow_and_prompt_chunks(self):
        png = self.folder.png("run.png", *stack_docs())
        code, out, err = self.learn(png)
        self.assertEqual((code, err), (ExitCode.OK, ""))
        self.assertEqual(out.splitlines()[0], "samples: 1 of 1 files")
        (entry,) = self.templates()["templates"]
        self.assertEqual((entry["type"], entry["ver"], entry["widgets"]), ("Xi Stack", "1.2.0", ["meta", "text", "strength", "stack"]))
        self.assertEqual(entry["source"], "learned")

    def test_learning_starts_from_the_definitions_without_templates(self):
        self.learn(self.folder.png("run.png", *stack_docs()))
        (call,) = self.load.call_args_list
        self.assertEqual(call.kwargs, {"object_info": OBJECT_INFO, "templates": NO_TEMPLATES})

    def test_canvas_and_api_files_in_both_naming_schemes(self):
        files = (
            *self.folder.pair("a", *stack_docs(ver="1.2.0")),
            *self.folder.pair("b", *stack_docs(ver="2.0.0"), suffixes=(".ui.json", ".api.json")),
        )
        code, out, err = self.learn(*files, extra=["--json"])
        self.assertEqual((code, err), (ExitCode.OK, ""))
        summary = json.loads(out)
        self.assertEqual((summary["files"], summary["samples"], summary["skipped"]), (4, 2, {}))
        self.assertEqual([e["ver"] for e in self.templates()["templates"]], ["2.0.0", "1.2.0"])

    def test_a_canvas_file_alone_finds_its_api_file(self):
        ui, _ = self.folder.pair("a", *stack_docs())
        code, out, _ = self.learn(ui, extra=["--json"])
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(json.loads(out)["samples"], 1)

    def test_files_without_a_pair_are_skipped_and_counted(self):
        good = self.folder.png("run.png", *stack_docs())
        _, api = self.folder.pair("a", *stack_docs())
        lonely = self.folder.write("lonely_ui.json", dumps(stack_docs()[0]))
        unnamed = self.folder.write("plain.json", dumps(stack_docs()[0]))
        code, out, err = self.learn(good, lonely, unnamed, api, extra=["--json"])
        self.assertEqual((code, err), (ExitCode.OK, ""))
        summary = json.loads(out)
        self.assertEqual((summary["files"], summary["samples"]), (4, 1))
        self.assertEqual(summary["skipped"], {"no_pair": 3})  # an API file alone has no canvas to pair with
        self.assertEqual(summary["templates"], 1)

    def test_png_missing_a_chunk_is_skipped(self):
        only_workflow = self.folder.write("w.png", make_png(text_chunk("workflow", dumps(stack_docs()[0]))))
        good = self.folder.png("run.png", *stack_docs())
        code, out, _ = self.learn(only_workflow, good, extra=["--json"])
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(json.loads(out)["skipped"], {"no_chunks": 1})

    def test_json_summary(self):
        good = self.folder.png("run.png", *stack_docs())
        lonely = self.folder.write("x_ui.json", dumps(stack_docs()[0]))
        code, out, err = self.learn(good, lonely, extra=["--json"])
        self.assertEqual((code, err), (ExitCode.OK, ""))
        self.assertEqual(
            json.loads(out),
            {"files": 2, "samples": 1, "skipped": {"no_pair": 1}, "nodes": {"aligned": 1, "nodes": 1}, "types": 1, "templates": 1},
        )

    def test_text_summary(self):
        good = self.folder.png("run.png", *stack_docs())
        lonely = self.folder.write("x_ui.json", dumps(stack_docs()[0]))
        _, out, _ = self.learn(good, lonely)
        self.assertEqual(
            out, "samples: 1 of 2 files\nskipped: no_pair 1\nnodes: aligned 1, nodes 1\ntemplates: 1 for 1 types\n"
        )

    def test_nodes_a_template_cannot_describe_are_counted_apart(self):
        ui = canvas(
            node(1, "KSampler", [1, "fixed", 20, 8, "euler", "normal", 1]),
            node(2, "Mu Sampler", [1, "fixed", 20]),
            subgraphs=[{"id": "sub"}],
        )
        api = prompt(n1=("KSampler", {}), n2=("Mu Sampler", {"seed": 1, "steps": 20}))
        _, out, _ = self.learn(self.folder.png("run.png", ui, api), extra=["--json"])
        nodes = json.loads(out)["nodes"]
        self.assertEqual((nodes["fixed"], nodes["aligned"], nodes["nodes"]), (1, 1, 2))
        self.assertEqual(self.templates(), {"format": 1, "templates": []})

    def test_output_does_not_depend_on_the_order_of_the_files(self):
        files = [
            self.folder.png("a.png", *stack_docs(ver="1.2.0")),
            self.folder.png("b.png", *stack_docs(ver="2.0.0")),
            self.folder.png("c.png", *stack_docs(ver=None)),
            self.folder.png("d.png", *stack_docs(ver="abc123")),
            *self.folder.pair("e", *stack_docs(ver="1.2.0", text="other")),
            self.folder.write("f_ui.json", "{}"),
        ]
        seen = set()
        for order in itertools.islice(itertools.permutations(files), 0, 720, 37):
            code, out, _ = self.learn(*order, extra=["--json"])
            self.assertEqual(code, ExitCode.OK)
            seen.add((out, Path(self.folder.out()).read_bytes()))
        self.assertEqual(len(seen), 1)
        self.assertEqual([e["ver"] for e in json.loads(next(iter(seen))[1])["templates"]], ["2.0.0", "1.2.0", "abc123", None])

    def test_second_run_replaces_the_output(self):
        self.learn(self.folder.png("run.png", *stack_docs()))
        first = Path(self.folder.out()).read_bytes()
        self.learn(self.folder.png("run.png", *stack_docs()))
        self.assertEqual(Path(self.folder.out()).read_bytes(), first)


class LearnCliFailureTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder(self)
        defs = base_defs()  # before the patch: building it calls the real load
        patch = mock.patch.object(NodeDefs, "load", side_effect=lambda **_: defs)
        patch.start()
        self.addCleanup(patch.stop)

    def learn(self, *files, extra=()):
        return run("learn", *files, "-o", self.folder.out(), *extra)

    def assertRejected(self, files, reason):
        code, out, err = self.learn(*files, "--json")
        self.assertEqual(code, ExitCode.INPUT_ERROR)
        self.assertEqual(json.loads(out)["skipped"], reason)
        self.assertIn("no file gave a canvas paired with its API prompt", err)

    def test_nothing_usable_exits_3_and_keeps_an_existing_output(self):
        Path(self.folder.out()).write_bytes(b"previous")
        self.assertRejected([self.folder.write("x_ui.json", dumps(stack_docs()[0]))], {"no_pair": 1})
        self.assertEqual(Path(self.folder.out()).read_bytes(), b"previous")

    def test_missing_file_is_invalid(self):
        self.assertRejected([str(self.folder.root / "absent.png")], {"invalid": 1})

    def test_malformed_json_is_invalid(self):
        ui, _ = self.folder.pair("a", *stack_docs())
        Path(ui).write_text("{not json", encoding="utf-8")
        self.assertRejected([ui], {"invalid": 1})

    def test_api_file_that_is_not_a_prompt_is_invalid(self):
        ui, api = self.folder.pair("a", stack_docs()[0], {"3": {"class_type": "Xi Stack"}})
        self.assertRejected([ui], {"invalid": 1})
        Path(api).write_text("[]", encoding="utf-8")
        self.assertRejected([ui], {"invalid": 1})

    def test_png_whose_prompt_holds_nan_is_invalid(self):
        text = dumps(prompt(n3=("Xi Stack", {"strength": 0.5}))).replace("0.5", "NaN")
        png = self.folder.write("nan.png", make_png(text_chunk("workflow", dumps(stack_docs()[0])), text_chunk("prompt", text)))
        self.assertRejected([png], {"invalid": 1})

    def test_png_whose_workflow_chunk_holds_a_prompt_is_invalid(self):
        png = self.folder.png("swapped.png", stack_docs()[1], stack_docs()[1])
        self.assertRejected([png], {"invalid": 1})

    def test_damaged_png_is_invalid(self):
        self.assertRejected([self.folder.write("bad.png", SIGNATURE + b"damaged")], {"invalid": 1})

    def test_one_bad_file_does_not_stop_the_others(self):
        bad = self.folder.write("bad.png", SIGNATURE + b"damaged")
        good = self.folder.png("run.png", *stack_docs())
        code, out, _ = self.learn(bad, good, "--json")
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual((json.loads(out)["samples"], json.loads(out)["skipped"]), (1, {"invalid": 1}))

    def test_unreadable_nodedefs_exits_4(self):
        png = self.folder.png("run.png", *stack_docs())
        code, out, err = run("learn", png, "-o", self.folder.out(), "--nodedefs", str(self.folder.root / "absent.json"))
        self.assertEqual((code, out), (ExitCode.NODEDEFS_MISSING, ""))
        self.assertIn("cannot read node definitions", err)
        self.assertFalse(Path(self.folder.out()).exists())

    def test_unwritable_output_exits_5(self):
        png = self.folder.png("run.png", *stack_docs())
        code, out, err = run("learn", png, "-o", str(self.folder.root / "no" / "such" / "templates.json"))
        self.assertEqual((code, out), (ExitCode.WRITE_FAILED, ""))
        self.assertIn("cannot write", err)

    def test_missing_arguments_are_usage_errors(self):
        for argv in (["learn"], ["learn", "a.png"]):
            code, out, err = run(*argv)
            self.assertEqual((code, out), (ExitCode.USAGE, ""))
            self.assertIn("usage:", err)


class LearnCliEndToEndTest(unittest.TestCase):
    """One run with the real registry load."""

    def test_templates_from_files_and_nodedefs(self):
        folder = Folder(self)
        png = folder.png("run.png", *stack_docs())
        code, _, err = run("learn", png, "-o", folder.out(), "--nodedefs", folder.nodedefs())
        self.assertEqual((code, err), (ExitCode.OK, ""))
        (entry,) = json.loads(Path(folder.out()).read_text(encoding="utf-8"))["templates"]
        self.assertEqual(entry["widgets"], ["meta", "text", "strength", "stack"])


if __name__ == "__main__":
    unittest.main()
