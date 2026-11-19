"""Tests of ``tools/sweep.py``, the random-graph sweep and its reconciliation with the known list.

The tool runs as a command, the way the workflow runs it, on a few seeds of each entry. The
reconciliation tests give it a temporary copy of the known list with one record taken out,
added or changed, and check the exit code and the lines that name the mismatch. Seed 45 of
the scatter entry under bento is a recorded failure (C4) of the real list; seed 0 of the wide
entry under bento runs clean.
"""

import contextlib
import errno
import importlib.util
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
from unittest import mock

from tests import support
from cwc.report import Code, CwcError, ExitCode, Finding, Severity

TOOL = support.ROOT / "tools" / "sweep.py"
KNOWN = support.ROOT / "tools" / "sweep_known.json"


def load_tool():
    spec = importlib.util.spec_from_file_location("sweep_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sweep_tool"] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()


def run(*args: str, known: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-B", str(TOOL), *args]
    if known is not None:
        cmd += ["--known", str(known)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


class TempKnown:
    """A copy of the real known list in a temporary directory, edited by ``edit(data)``."""

    def __init__(self, edit):
        self.edit = edit

    def __enter__(self) -> Path:
        self.dir = tempfile.TemporaryDirectory()
        data = json.loads(KNOWN.read_text(encoding="utf-8"))
        self.edit(data)
        path = Path(self.dir.name) / "known.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def __exit__(self, *exc):
        self.dir.cleanup()


SCATTER_45 = {"entry": "scatter", "preset": "bento", "seed": 45, "code": "C4"}


def without_scatter_45(data):
    data["known"] = [r for r in data["known"] if {k: r[k] for k in SCATTER_45} != SCATTER_45]


class EntriesTest(unittest.TestCase):
    def test_each_entry_matches_the_known_list_on_a_few_seeds(self):
        out = run("--preset", "bento", "--seeds", "44:47")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        lines = out.stdout.splitlines()
        for entry in tool.ENTRIES:
            with self.subTest(entry=entry):
                self.assertTrue(any(line.startswith(f"sum {entry} bento 44-47: 3 runs,") for line in lines), out.stdout)
        self.assertIn("bad scatter bento 45 C4 known: ", out.stdout)
        self.assertEqual(lines[-1], "result: OK, 1 known failures, 0 new, 0 gone")

    def test_output_does_not_depend_on_jobs(self):
        args = ("--preset", "bento", "--entry", "narrow", "--entry", "scatter", "--entry", "across", "--seeds", "43:47")
        one, two = run(*args, "--jobs", "1"), run(*args, "--jobs", "2")
        self.assertEqual(one.returncode, 0, one.stdout + one.stderr)
        self.assertEqual(two.returncode, 0, two.stdout + two.stderr)
        self.assertEqual(one.stdout, two.stdout)


class ReconcileTest(unittest.TestCase):
    def test_a_failure_missing_from_the_list_exits_1(self):
        with TempKnown(without_scatter_45) as known:
            out = run("--entry", "scatter", "--preset", "bento", "--seeds", "45:46", known=known)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("bad scatter bento 45 C4 NEW: ", out.stdout)
        self.assertIn("result: MISMATCH, 1 new, 0 gone", out.stdout)

    def test_a_listed_failure_that_no_longer_fails_exits_1(self):
        extra = {"entry": "wide", "preset": "bento", "seed": 0, "code": "G5", "class": "cockpit-frame-cycle"}
        with TempKnown(lambda data: data["known"].append(extra)) as known:
            out = run("--entry", "wide", "--preset", "bento", "--seeds", "0:1", known=known)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("gone wide bento 0 G5: in the known list but did not fail; remove it", out.stdout)
        self.assertIn("result: MISMATCH, 0 new, 1 gone", out.stdout)

    def test_a_changed_code_is_one_new_and_one_gone(self):
        def recode(data):
            for record in data["known"]:
                if {k: record[k] for k in SCATTER_45} == SCATTER_45:
                    record["code"] = "C3"

        with TempKnown(recode) as known:
            out = run("--entry", "scatter", "--preset", "bento", "--seeds", "45:46", known=known)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("bad scatter bento 45 C4 NEW: ", out.stdout)
        self.assertIn("gone scatter bento 45 C3: ", out.stdout)

    def test_update_known_records_the_run_and_the_next_run_matches(self):
        with TempKnown(without_scatter_45) as known:
            args = ("--entry", "scatter", "--preset", "bento", "--seeds", "44:47")
            first = run(*args, "--update-known", known=known)
            data = json.loads(known.read_text(encoding="utf-8"))
            second = run(*args, known=known)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn({**SCATTER_45, "class": "unclassified"}, data["known"])
        self.assertIn("unclassified", data["classes"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

    def test_update_known_refuses_a_range_apart_from_the_covered_one(self):
        with TempKnown(lambda data: None) as known:
            before = known.read_text(encoding="utf-8")
            out = run("--entry", "scatter", "--seeds", "500:501", "--update-known", known=known)
            after = known.read_text(encoding="utf-8")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(before, after)


class KnownFileTest(unittest.TestCase):
    def test_the_list_loads_and_is_written_in_the_tool_s_own_form(self):
        text = KNOWN.read_text(encoding="utf-8")
        known = tool.load_known(KNOWN)
        self.assertEqual(tool.dump_known(known), text)
        self.assertEqual(set(known.covers), set(tool.ENTRIES))
        self.assertTrue(known.records)
        for failure, cls in known.records.items():
            self.assertIn(cls, known.classes, failure)

    def test_malformed_lists_are_refused(self):
        good = json.loads(KNOWN.read_text(encoding="utf-8"))
        record = dict(good["known"][0])

        def edited(change):
            data = json.loads(json.dumps(good))
            change(data)
            return data

        cases = {
            "repeated record": lambda d: d["known"].append(dict(record)),
            "undescribed class": lambda d: d["known"].append({**record, "seed": record["seed"] + 1, "class": "nope"}),
            "seed outside covers": lambda d: d["known"].append({**record, "seed": d["covers"][record["entry"]][1]}),
            "unknown preset": lambda d: d["known"].append({**record, "preset": "nope"}),
            "missing key": lambda d: d["known"].append({k: v for k, v in record.items() if k != "code"}),
            "extra key": lambda d: d["known"].append({**record, "seed": record["seed"] + 1, "note": "x"}),
            "unknown entry in covers": lambda d: d["covers"].update(nope=[0, 1]),
            "empty range": lambda d: d["covers"].update(wide=[5, 5]),
            "wrong format": lambda d: d.update(format=2),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(tool.KnownFileError):
                tool.parse_known(edited(change))


ORIGINAL = json.loads(KNOWN.read_text(encoding="utf-8"))
RECORD = ORIGINAL["known"][0]
SCATTER_BENTO = ("--entry", "scatter", "--preset", "bento")


class Outcome(NamedTuple):
    code: int
    out: str
    err: str


def call(*args: str, known: Path | None = None, stdout=None) -> Outcome:
    """``tool.main`` in this process: its exit code and what it wrote to stdout and stderr."""
    argv = list(args) + ([] if known is None else ["--known", str(known)])
    out, err = stdout or io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = tool.main(argv)
        except SystemExit as exc:
            code = exc.code
    return Outcome(code, out.getvalue(), err.getvalue())


def data_of(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("error:")]


class ToolErrorAssertions(unittest.TestCase):
    def assert_tool_error(self, outcome: Outcome) -> None:
        """Exit code 2 with exactly one ``error:`` line on stderr and no traceback."""
        self.assertEqual(outcome.code, 2, outcome)
        self.assertEqual(len(error_lines(outcome.err)), 1, outcome.err)
        self.assertNotIn("Traceback", outcome.err)


def updated_file(*args: str, edit=lambda data: None) -> tuple[Outcome, dict]:
    """Run ``--update-known`` on a temporary known list edited by ``edit``: outcome and new data."""
    with TempKnown(edit) as known:
        outcome = call(*args, "--update-known", known=known)
        return outcome, data_of(known)


# --- the known file is checked before anything is used -----------------------------------------

BAD_LISTS = {
    "format is true": lambda d: d.update(format=True),
    "format is 1.0": lambda d: d.update(format=1.0),
    "wrong format": lambda d: d.update(format=2),
    "extra top-level key": lambda d: d.update(note="x"),
    "missing top-level key": lambda d: d.pop("classes"),
    "classes is not an object": lambda d: d.update(classes=[]),
    "class description is empty": lambda d: d["classes"].update(blank=""),
    "class description is not text": lambda d: d["classes"].update(blank=5),
    "covers is not an object": lambda d: d.update(covers=[]),
    "unknown entry in covers": lambda d: d["covers"].update(nope=[0, 1]),
    "covers range with three numbers": lambda d: d["covers"].update(wide=[0, 1, 2]),
    "covers range with a float": lambda d: d["covers"].update(wide=[0, 1.5]),
    "covers range with a bool": lambda d: d["covers"].update(wide=[True, 5]),
    "covers range that is empty": lambda d: d["covers"].update(wide=[5, 5]),
    "covers range below zero": lambda d: d["covers"].update(wide=[-1, 5]),
    "known is not a list": lambda d: d.update(known={}),
    "record is not an object": lambda d: d["known"].append(5),
    "record misses a key": lambda d: d["known"].append({k: v for k, v in RECORD.items() if k != "code"}),
    "record has an extra key": lambda d: d["known"].append({**RECORD, "seed": RECORD["seed"] + 1, "note": "x"}),
    "entry is not in covers": lambda d: d["known"].append({**RECORD, "entry": "nope"}),
    "entry is a list": lambda d: d["known"].append({**RECORD, "entry": ["x"]}),
    "entry is a number": lambda d: d["known"].append({**RECORD, "entry": 5}),
    "preset is unknown": lambda d: d["known"].append({**RECORD, "preset": "nope"}),
    "preset is a list": lambda d: d["known"].append({**RECORD, "preset": ["x"]}),
    "seed is a bool": lambda d: d["known"].append({**RECORD, "seed": True}),
    "seed is a float": lambda d: d["known"].append({**RECORD, "seed": 46.0}),
    "seed is outside covers": lambda d: d["known"].append({**RECORD, "seed": d["covers"][RECORD["entry"]][1]}),
    "code is a number": lambda d: d["known"].append({**RECORD, "seed": 46, "code": 5}),
    "code is a list": lambda d: d["known"].append({**RECORD, "seed": 46, "code": ["C4"]}),
    "code is empty": lambda d: d["known"].append({**RECORD, "seed": 46, "code": ""}),
    "code has a space": lambda d: d["known"].append({**RECORD, "seed": 46, "code": "C 4"}),
    "code has a tab": lambda d: d["known"].append({**RECORD, "seed": 46, "code": "C\t4"}),
    "code has a newline": lambda d: d["known"].append({**RECORD, "seed": 46, "code": "C4\n"}),
    "class is a list": lambda d: d["known"].append({**RECORD, "seed": 46, "class": ["x"]}),
    "class is a number": lambda d: d["known"].append({**RECORD, "seed": 46, "class": 5}),
    "class is not described": lambda d: d["known"].append({**RECORD, "seed": 46, "class": "nope"}),
    "repeated record": lambda d: d["known"].append(dict(RECORD)),
}


class MalformedKnownTest(ToolErrorAssertions):
    def test_every_check_of_parse_known_has_a_list_it_refuses(self):
        for name, change in BAD_LISTS.items():
            data = json.loads(json.dumps(ORIGINAL))
            change(data)
            with self.subTest(name), self.assertRaises(tool.KnownFileError):
                tool.parse_known(data)

    def test_a_malformed_list_exits_2_with_one_error_line(self):
        for name, change in BAD_LISTS.items():
            with self.subTest(name), TempKnown(change) as known:
                outcome = call("--entry", "wide", "--preset", "bento", "--seeds", "0:1", known=known)
                self.assert_tool_error(outcome)
                self.assertEqual(outcome.out, "")

    def test_a_known_file_that_cannot_be_read_exits_2_with_one_error_line(self):
        raw = {
            "not json": b"{",
            "empty": b"",
            "an array": b"[]",
            "nested too deep": b"[" * 100000,
            "not utf-8": b"\xff\xfe",
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for name, content in raw.items():
                paths[name] = Path(directory) / name.replace(" ", "-")
                paths[name].write_bytes(content)
            paths["missing"] = Path(directory) / "missing.json"
            paths["a directory"] = Path(directory)
            for name, path in paths.items():
                with self.subTest(name):
                    self.assert_tool_error(call("--entry", "wide", "--seeds", "0:1", known=path))

    def test_a_known_file_nested_too_deep_never_reaches_the_parser(self):
        # The JSON parser's C scanner can overrun the stack on such a document and end the process.
        parser = SimpleNamespace(loads=mock.Mock(side_effect=AssertionError("the parser was called")))
        with tempfile.TemporaryDirectory() as directory:
            for name, content in {"arrays": b"[" * 100000, "objects": b'{"a":' * 100000}.items():
                path = Path(directory) / name
                path.write_bytes(content)
                with self.subTest(name), mock.patch.object(tool, "json", parser):
                    outcome = call("--entry", "wide", "--seeds", "0:1", known=path)
                    self.assert_tool_error(outcome)
                    self.assertIn(f"nested deeper than {tool.MAX_DEPTH} levels", outcome.err)
        parser.loads.assert_not_called()


# --- reconciliation output ---------------------------------------------------------------------


class ReportTest(unittest.TestCase):
    def test_the_sum_line_counts_bad_new_and_gone_runs(self):
        with TempKnown(without_scatter_45) as known:
            new = call(*SCATTER_BENTO, "--seeds", "44:47", known=known)
        self.assertIn("sum scatter bento 44-47: 3 runs, 1 bad, 1 new, 0 gone\n", new.out)

        extra = {"entry": "wide", "preset": "bento", "seed": 0, "code": "G5", "class": "cockpit-frame-cycle"}
        with TempKnown(lambda data: data["known"].append(extra)) as known:
            gone = call("--entry", "wide", "--preset", "bento", "--seeds", "0:1", known=known)
        self.assertIn("sum wide bento 0-1: 1 runs, 0 bad, 0 new, 1 gone\n", gone.out)

        def recode(data):
            for record in data["known"]:
                if {k: record[k] for k in SCATTER_45} == SCATTER_45:
                    record["code"] = "C3"

        with TempKnown(recode) as known:
            both = call(*SCATTER_BENTO, "--seeds", "45:46", known=known)
        self.assertIn("sum scatter bento 45-46: 1 runs, 1 bad, 1 new, 1 gone\n", both.out)

    def test_a_run_with_two_failure_codes_is_one_bad_run_and_two_new_failures(self):
        two_codes = {("wide", "bento", 0): [("E1", "first"), ("G5", "second")]}  # injected result of a run
        with TempKnown(lambda data: None) as known, mock.patch.object(tool, "check", lambda run: two_codes.get(run, [])):
            out = call("--entry", "wide", "--preset", "bento", "--seeds", "0:2", known=known)
        self.assertEqual(out.code, 1, out)
        self.assertIn("sum wide bento 0-2: 2 runs, 1 bad, 2 new, 0 gone\n", out.out)
        self.assertIn("result: MISMATCH, 2 new, 0 gone", out.out)

    def test_a_known_failure_is_a_bad_run_and_not_a_new_one(self):
        out = call(*SCATTER_BENTO, "--seeds", "44:47")
        self.assertEqual(out.code, 0, out)
        self.assertIn("sum scatter bento 44-47: 3 runs, 1 bad, 0 new, 0 gone\n", out.out)
        self.assertEqual(out.out.splitlines()[-1], "result: OK, 1 known failures, 0 new, 0 gone")

    def test_only_the_named_entries_and_presets_run(self):
        out = call("--entry", "wide", "--preset", "bento", "--preset", "stepped", "--seeds", "0:2")
        self.assertEqual(out.code, 0, out)
        sums = [line for line in out.out.splitlines() if line.startswith("sum ")]
        self.assertEqual(sums, ["sum wide bento 0-2: 2 runs, 0 bad, 0 new, 0 gone", "sum wide stepped 0-2: 2 runs, 0 bad, 0 new, 0 gone"])

    def test_without_seeds_each_entry_runs_over_the_range_the_known_file_covers(self):
        def small(data):
            data["covers"] = {"narrow": [0, 2], "wide": [3, 6], "scatter": [1, 2]}
            data["known"] = []

        with TempKnown(small) as known:
            out = call("--entry", "narrow", "--entry", "wide", "--entry", "scatter", "--preset", "bento", known=known)
        sums = [line for line in out.out.splitlines() if line.startswith("sum ")]
        self.assertEqual(
            sums,
            [
                "sum narrow bento 0-2: 2 runs, 0 bad, 0 new, 0 gone",
                "sum wide bento 3-6: 3 runs, 0 bad, 0 new, 0 gone",
                "sum scatter bento 1-2: 1 runs, 0 bad, 0 new, 0 gone",
            ],
        )

    def test_an_entry_the_list_does_not_cover_needs_seeds(self):
        def without_wide(data):
            del data["covers"]["wide"]
            data["known"] = [r for r in data["known"] if r["entry"] != "wide"]

        with TempKnown(without_wide) as known:
            refused = call("--entry", "wide", "--preset", "bento", known=known)
            allowed = call("--entry", "wide", "--preset", "bento", "--seeds", "0:1", known=known)
        self.assertEqual(refused.code, 2, refused)
        self.assertEqual(len(error_lines(refused.err)), 1, refused.err)
        self.assertEqual(allowed.code, 0, allowed)


# --- --update-known --------------------------------------------------------------------------


class UpdateKnownTest(ToolErrorAssertions):
    def test_a_run_that_reproduces_the_list_leaves_the_whole_file_as_it_was(self):
        out, after = updated_file(*SCATTER_BENTO, "--seeds", "44:47")
        self.assertEqual(out.code, 0, out)
        self.assertIn("result: 0 new, 0 gone; the known file was rewritten", out.out)
        self.assertEqual(after, ORIGINAL)  # records outside the runs, classes, covers, no stray class

    def test_a_new_failure_is_recorded_unclassified_and_the_other_records_stay(self):
        out, after = updated_file(*SCATTER_BENTO, "--seeds", "44:47", edit=without_scatter_45)
        self.assertEqual(out.code, 0, out)
        expected = json.loads(json.dumps(ORIGINAL))
        expected["classes"]["unclassified"] = tool.UNCLASSIFIED_TEXT
        for record in expected["known"]:
            if {k: record[k] for k in SCATTER_45} == SCATTER_45:
                record["class"] = "unclassified"
        self.assertEqual(after, expected)

    def test_a_record_inside_the_runs_that_no_longer_fails_is_removed(self):
        extra = {"entry": "wide", "preset": "bento", "seed": 0, "code": "G5", "class": "cockpit-frame-cycle"}
        out, after = updated_file(
            "--entry", "wide", "--preset", "bento", "--seeds", "0:1", edit=lambda data: data["known"].append(extra)
        )
        self.assertEqual(out.code, 0, out)
        self.assertIn("gone wide bento 0 G5: ", out.out)
        self.assertIn("result: 0 new, 1 gone; the known file was rewritten", out.out)
        self.assertEqual(after, ORIGINAL)

    def test_a_wider_run_of_all_presets_widens_the_covered_range_at_the_top(self):
        out, after = updated_file("--entry", "across", "--seeds", "401:404")
        self.assertEqual(out.code, 0, out)
        self.assertEqual(after["covers"], {**ORIGINAL["covers"], "across": [0, 404]})
        self.assertEqual(after["classes"].keys() - ORIGINAL["classes"].keys() <= {"unclassified"}, True)
        added = [r for r in after["known"] if r not in ORIGINAL["known"]]
        self.assertTrue(all(r["entry"] == "across" and r["seed"] >= 401 and r["class"] == "unclassified" for r in added), added)
        self.assertTrue(all(r in after["known"] for r in ORIGINAL["known"]))

    def test_a_wider_run_of_all_presets_widens_the_covered_range_at_the_bottom(self):
        out, after = updated_file("--entry", "across", "--seeds", "3:8", edit=lambda data: data["covers"].update(across=[5, 401]))
        self.assertEqual(out.code, 0, out)
        self.assertEqual(after["covers"], {**ORIGINAL["covers"], "across": [3, 401]})

    def test_a_run_that_reaches_the_covered_range_from_below_is_not_a_gap(self):
        out, after = updated_file("--entry", "across", "--seeds", "0:5", edit=lambda data: data["covers"].update(across=[5, 401]))
        self.assertEqual(out.code, 0, out)
        self.assertEqual(after["covers"]["across"], [0, 401])

    def test_a_run_apart_from_the_covered_range_below_is_refused_and_the_file_stays(self):
        with TempKnown(lambda data: data["covers"].update(across=[5, 401])) as known:
            before = known.read_text(encoding="utf-8")
            out = call("--entry", "across", "--seeds", "0:3", "--update-known", known=known)
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assert_tool_error(out)
        self.assertIn("would leave a gap", out.err)

    def test_widening_in_a_run_of_some_presets_is_refused_and_the_file_stays(self):
        with TempKnown(lambda data: None) as known:
            before = known.read_text(encoding="utf-8")
            out = call("--entry", "across", "--preset", "bento", "--seeds", "401:404", "--update-known", known=known)
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assert_tool_error(out)
        self.assertIn("only in a run of all presets", out.err)

    def test_a_run_of_some_presets_inside_the_covered_range_is_allowed(self):
        out, after = updated_file("--entry", "across", "--preset", "bento", "--seeds", "0:4")
        self.assertEqual(out.code, 0, out)
        self.assertEqual(after, ORIGINAL)

    def test_an_entry_the_list_does_not_cover_is_added_by_a_run_of_all_presets_only(self):
        def without_wide(data):
            del data["covers"]["wide"]
            data["known"] = [r for r in data["known"] if r["entry"] != "wide"]

        out, after = updated_file("--entry", "wide", "--seeds", "5:7", edit=without_wide)
        self.assertEqual(out.code, 0, out)
        self.assertEqual(after["covers"]["wide"], [5, 7])
        with TempKnown(without_wide) as known:
            some = call("--entry", "wide", "--preset", "bento", "--seeds", "5:7", "--update-known", known=known)
            self.assertNotIn("wide", data_of(known)["covers"])
        self.assert_tool_error(some)

    def test_a_wider_run_of_some_presets_is_refused_at_the_bottom_too(self):
        with TempKnown(lambda data: data["covers"].update(across=[5, 401])) as known:
            before = known.read_text(encoding="utf-8")
            out = call("--entry", "across", "--preset", "bento", "--seeds", "3:8", "--update-known", known=known)
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assert_tool_error(out)

    def test_the_known_file_is_read_again_before_it_is_written(self):
        """Another update run may have changed the file while this sweep was running (injected)."""
        other = {"entry": "across", "preset": "bento", "seed": 7, "code": "C4", "class": "cockpit-frame-cycle"}
        replaced = {"entry": "scatter", "preset": "bento", "seed": 46, "code": "C4", "class": "cockpit-segment-untitled"}

        def other_run_writes(data):
            data["known"] += [other, replaced]  # one outside this run's seeds, one inside them

        with TempKnown(without_scatter_45) as known:
            with self.edit_between_sweep_and_write(known, other_run_writes):
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
            after = data_of(known)
        self.assertEqual(out.code, 0, out)
        self.assertIn(other, after["known"])  # outside the runs made: kept as it is on disk now
        self.assertIn({**SCATTER_45, "class": "unclassified"}, after["known"])  # this run's failure
        self.assertNotIn(replaced, after["known"])  # inside the runs made: this run's result decides

    def test_a_known_file_that_became_malformed_is_not_written_over(self):
        with TempKnown(without_scatter_45) as known:
            with self.edit_between_sweep_and_write(known, raw="{ not json"):
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
            self.assertEqual(known.read_text(encoding="utf-8"), "{ not json")
        self.assert_tool_error(out)
        self.assertNotIn("rewritten", out.out)

    def test_a_known_file_that_vanished_is_not_recreated(self):
        with TempKnown(without_scatter_45) as known:
            with self.edit_between_sweep_and_write(known, remove=True):
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
            self.assertFalse(known.exists())
        self.assert_tool_error(out)

    def test_a_run_of_all_presets_does_not_write_over_a_known_file_that_became_unreadable(self):
        """The merge rules would let a run of all presets start a file of its own; that is not what it may do here."""
        for name, change in (("malformed", {"raw": "{ not json"}), ("vanished", {"remove": True})):
            with self.subTest(name), TempKnown(lambda data: None) as known, mock.patch.object(tool, "check", clean_run):
                with self.edit_between_sweep_and_write(known, **change):
                    out = call("--entry", "scatter", "--seeds", "44:47", "--update-known", known=known)
                self.assert_tool_error(out)
                self.assertNotIn("rewritten", out.out)
                self.assertEqual(known.read_text(encoding="utf-8") if known.exists() else None, change.get("raw"))

    @staticmethod
    def edit_between_sweep_and_write(path: Path, edit=None, raw: str | None = None, remove: bool = False):
        """Wrap ``tool.sweep``: after it returns, change the file at ``path`` the way a second run would."""
        real = tool.sweep

        def sweep_then_edit(runs, jobs):
            results = real(runs, jobs)
            if remove:
                path.unlink()
            elif raw is not None:
                path.write_text(raw, encoding="utf-8")
            else:
                data = data_of(path)
                edit(data)
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return results

        return mock.patch.object(tool, "sweep", sweep_then_edit)


class WriteKnownTest(ToolErrorAssertions):
    def test_the_rewritten_file_keeps_its_permission_bits(self):
        with TempKnown(without_scatter_45) as known:
            known.chmod(0o640)
            call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
            self.assertEqual(stat.S_IMODE(known.stat().st_mode), 0o640)

    def test_a_new_file_gets_0644(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.json"
            previous = os.umask(0o077)
            try:
                tool.write_known(path, tool.load_known(KNOWN))
            finally:
                os.umask(previous)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            self.assertEqual(path.read_text(encoding="utf-8"), KNOWN.read_text(encoding="utf-8"))

    def test_a_list_the_loader_would_refuse_is_never_written(self):
        undescribed = tool.Known({}, {"wide": (0, 1)}, {tool.Failure("wide", "bento", 0, "G5"): "nope"})  # not reachable from a run
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known.json"
            with self.assertRaises(tool.KnownFileError):
                tool.write_known(path, undescribed)
            self.assertEqual(os.listdir(directory), [])

    def test_a_directory_that_cannot_be_written_exits_2_after_the_report(self):
        with TempKnown(without_scatter_45) as known:
            directory = known.parent
            directory.chmod(0o555)
            try:
                if os.access(directory, os.W_OK):
                    self.skipTest("the directory is still writable here (running as root), so the failure cannot be made")
                before = known.read_text(encoding="utf-8")
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
                self.assertEqual(known.read_text(encoding="utf-8"), before)
            finally:
                directory.chmod(0o755)
        self.assert_tool_error(out)
        self.assertIn("bad scatter bento 45 C4 NEW: ", out.out)  # the report was written before the write failed
        self.assertNotIn("rewritten", out.out)

    def test_a_replace_that_fails_exits_2_and_leaves_no_temporary_file(self):
        with TempKnown(without_scatter_45) as known:
            before = known.read_text(encoding="utf-8")
            full = OSError(errno.ENOSPC, "No space left on device")  # injected
            with mock.patch.object(tool.os, "replace", side_effect=full):
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
            self.assertEqual(known.read_text(encoding="utf-8"), before)
            self.assertEqual(os.listdir(known.parent), [known.name])
        self.assert_tool_error(out)


# --- output ------------------------------------------------------------------------------------


class Recorder(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self):
        self.flushes += 1
        super().flush()


class ClosedReader(io.StringIO):
    """A stdout whose reader has gone away (injected)."""

    def write(self, text):
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")


class OutputTest(ToolErrorAssertions):
    def test_the_known_file_is_written_only_after_the_report_is_out_and_flushed(self):
        recorder, seen = Recorder(), {}
        real = tool.write_known

        def write_known(path, known):
            seen.update(text=recorder.getvalue(), flushes=recorder.flushes)
            real(path, known)

        with TempKnown(without_scatter_45) as known, mock.patch.object(tool, "write_known", write_known):
            out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known, stdout=recorder)
        self.assertEqual(out.code, 0, out)
        self.assertIn("bad scatter bento 45 C4 NEW: ", seen["text"])
        self.assertIn("sum scatter bento 44-47: 3 runs, 1 bad, 1 new, 0 gone\n", seen["text"])
        self.assertGreaterEqual(seen["flushes"], 1)
        self.assertNotIn("result:", seen["text"])
        self.assertTrue(out.out.endswith("the known file was rewritten with this run's failures\n"), out.out)

    def test_a_reader_that_went_away_ends_the_run_with_exit_2_and_the_file_stays(self):
        with TempKnown(without_scatter_45) as known:
            before = known.read_text(encoding="utf-8")
            with mock.patch.object(tool, "write_known") as write_known:
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known, stdout=ClosedReader())
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assert_tool_error(out)
        write_known.assert_not_called()

    def test_a_closed_stdout_in_a_real_process_ends_with_exit_2_and_no_traceback(self):
        with TempKnown(without_scatter_45) as known:
            before = known.read_text(encoding="utf-8")
            cmd = [sys.executable, "-B", str(TOOL), *SCATTER_BENTO, "--seeds", "44:47", "--update-known", "--known", str(known)]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            proc.stdout.close()  # the reader goes away before the tool has written anything
            err = proc.stderr.read()
            code = proc.wait(timeout=120)
            proc.stderr.close()
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assertEqual(code, 2, err)
        self.assertEqual(len(error_lines(err)), 1, err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("Exception ignored", err)

    def test_no_stdout_at_all_exits_2_before_the_sweep_and_the_file_stays(self):
        err = io.StringIO()
        with TempKnown(without_scatter_45) as known, mock.patch.object(tool, "check") as check:
            before = known.read_text(encoding="utf-8")
            with contextlib.redirect_stdout(None), contextlib.redirect_stderr(err):
                code = tool.main([*SCATTER_BENTO, "--seeds", "44:47", "--update-known", "--known", str(known)])
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assert_tool_error(Outcome(code, "", err.getvalue()))
        check.assert_not_called()

    @unittest.skipIf(os.name != "posix", "closing descriptor 1 for the child is done with a POSIX shell")
    def test_a_stdout_descriptor_closed_before_the_start_exits_2_and_the_file_stays(self):
        with TempKnown(without_scatter_45) as known:
            before = known.read_text(encoding="utf-8")
            cmd = ["/bin/sh", "-c", 'exec "$0" "$@" >&-', sys.executable, "-B", str(TOOL), *SCATTER_BENTO, "--seeds", "44:47"]
            proc = subprocess.run([*cmd, "--update-known", "--known", str(known)], stderr=subprocess.PIPE, text=True, timeout=120)
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assert_tool_error(Outcome(proc.returncode, "", proc.stderr))

    def test_a_stdout_that_is_already_closed_exits_2(self):
        handle = open(os.devnull, "w", encoding="utf-8")
        handle.close()
        err = io.StringIO()
        with TempKnown(without_scatter_45) as known:
            before = known.read_text(encoding="utf-8")
            with contextlib.redirect_stdout(handle), contextlib.redirect_stderr(err):
                code = tool.main([*SCATTER_BENTO, "--seeds", "44:47", "--update-known", "--known", str(known)])
            self.assertEqual(known.read_text(encoding="utf-8"), before)
        self.assert_tool_error(Outcome(code, "", err.getvalue()))

    def closed_stdout(self):
        handle = open(os.devnull, "w", encoding="utf-8")
        handle.close()
        return handle

    def test_a_stdout_that_is_already_closed_is_refused_before_any_run(self):
        err = io.StringIO()
        with mock.patch.object(tool, "check") as check, contextlib.redirect_stdout(self.closed_stdout()), contextlib.redirect_stderr(err):
            code = tool.main(["--entry", "wide", "--preset", "bento", "--seeds", "0:1"])
        self.assert_tool_error(Outcome(code, "", err.getvalue()))
        self.assertIn("standard output is closed", err.getvalue())
        check.assert_not_called()

    def test_a_stdout_that_is_already_closed_is_refused_before_the_arguments_are_parsed(self):
        # From Python 3.14 on, building the argument parser reads sys.stdout and fails on a closed one.
        err = io.StringIO()
        parse = mock.patch.object(tool, "parse_args", side_effect=AssertionError("the arguments were parsed"))
        with parse, contextlib.redirect_stdout(self.closed_stdout()), contextlib.redirect_stderr(err):
            code = tool.main(["--entry", "wide", "--preset", "bento", "--seeds", "0:1"])
        self.assert_tool_error(Outcome(code, "", err.getvalue()))

    def test_any_write_error_of_stdout_exits_2_not_only_a_broken_pipe(self):
        out = call("--entry", "wide", "--preset", "bento", "--seeds", "0:1", stdout=FullDisk())
        self.assert_tool_error(out)
        self.assertIn("No space left on device", out.err)

    def test_a_failed_write_is_a_tool_error_whatever_the_stream_says_of_its_descriptor(self):
        # After a failed write the tool points the descriptor of stdout at the null device, so the
        # interpreter's own flush at exit does not fail again. A stream may have no descriptor to
        # give, or refuse to give one: neither may replace the error of the write.
        class NoDescriptor:
            closed = False

            def write(self, text):
                raise OSError(errno.ENOSPC, "No space left on device")

            def flush(self):
                pass

        class RefusesItsDescriptor(NoDescriptor):
            def fileno(self):
                raise ValueError("I/O operation on closed file")

        for stream in (NoDescriptor(), RefusesItsDescriptor()):
            with self.subTest(type(stream).__name__), contextlib.redirect_stdout(stream):
                with self.assertRaises(tool.ToolError) as caught:
                    tool.emit(["a line"])
                self.assertIn("No space left on device", str(caught.exception))

    def test_a_stdout_closed_between_the_first_look_and_the_write_exits_2(self):
        # The tool looks at stdout before the sweep; the stream can still be closed by the time
        # the report is written, and a write to a closed stream raises ValueError, not OSError.
        class ClosedMeanwhile(io.StringIO):
            def write(self, text):
                raise ValueError("I/O operation on closed file")

        out = call("--entry", "wide", "--preset", "bento", "--seeds", "0:1", stdout=ClosedMeanwhile())
        self.assert_tool_error(out)
        self.assertIn("closed file", out.err)

    def test_a_reader_that_leaves_before_the_result_line_is_told_the_file_was_rewritten(self):
        with TempKnown(without_scatter_45) as known:
            out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known, stdout=LeavesBeforeTheResult())
            after = data_of(known)
        self.assert_tool_error(out)
        self.assertIn("already rewritten", error_lines(out.err)[0])
        self.assertIn({**SCATTER_45, "class": "unclassified"}, after["known"])


class FullDisk(io.StringIO):
    """A stdout on a full disk (injected)."""

    def write(self, text):
        raise OSError(errno.ENOSPC, "No space left on device")


class LeavesBeforeTheResult(io.StringIO):
    """A reader that reads the report and goes away before the last line (injected)."""

    def write(self, text):
        if text.startswith("result:"):
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")
        return super().write(text)


# --- the covered ranges --------------------------------------------------------------------------


def without_scatter(data):
    data["covers"].pop("scatter", None)
    data["known"] = [r for r in data["known"] if r["entry"] != "scatter"]


def scatter_covers(lo: int, hi: int):
    def edit(data):
        without_scatter(data)
        data["covers"]["scatter"] = [lo, hi]

    return edit


def clean_run(run):
    """Every run is clean, at once (injected; these tests are about the known file, not the layout)."""
    return []


class CoverageTest(ToolErrorAssertions):
    def changed_during_the_sweep(self, args: tuple[str, ...], start, other_run) -> tuple[Outcome, dict, dict]:
        """``--update-known`` on a file made by ``start``; ``other_run`` rewrites it after the sweep."""
        expected = json.loads(json.dumps(ORIGINAL))
        start(expected)
        other_run(expected)
        with TempKnown(start) as known, mock.patch.object(tool, "check", clean_run):
            with UpdateKnownTest.edit_between_sweep_and_write(known, other_run):
                out = call(*args, "--update-known", known=known)
            return out, data_of(known), expected

    def assert_refused_as_changed(self, out: Outcome, after: dict, expected: dict) -> None:
        self.assert_tool_error(out)
        self.assertIn("changed during the sweep", out.err)
        self.assertNotIn("rewritten", out.out)
        self.assertEqual(after, expected)  # as the other run left it

    def test_a_cover_that_vanished_during_a_run_of_one_preset_is_not_written(self):
        out, after, expected = self.changed_during_the_sweep((*SCATTER_BENTO, "--seeds", "44:47"), lambda data: None, without_scatter)
        self.assert_refused_as_changed(out, after, expected)

    def test_a_cover_that_moved_apart_during_the_sweep_is_not_written(self):
        out, after, expected = self.changed_during_the_sweep((*SCATTER_BENTO, "--seeds", "44:47"), lambda data: None, scatter_covers(200, 400))
        self.assert_refused_as_changed(out, after, expected)

    def test_two_runs_that_found_an_entry_uncovered_cannot_leave_a_gap_between_them(self):
        out, after, expected = self.changed_during_the_sweep(("--entry", "scatter", "--seeds", "0:3"), without_scatter, scatter_covers(100, 105))
        self.assert_refused_as_changed(out, after, expected)

    def test_one_seed_between_the_runs_is_a_gap_when_the_file_is_read_again(self):
        out, after, expected = self.changed_during_the_sweep(("--entry", "scatter", "--seeds", "0:3"), without_scatter, scatter_covers(4, 10))
        self.assert_refused_as_changed(out, after, expected)
        out, after, expected = self.changed_during_the_sweep(("--entry", "scatter", "--seeds", "5:8"), without_scatter, scatter_covers(0, 4))
        self.assert_refused_as_changed(out, after, expected)

    def test_a_cover_another_run_made_to_touch_this_run_is_extended(self):
        for seeds, other, merged in (("0:3", (3, 10), [0, 10]), ("4:6", (0, 4), [0, 6])):
            with self.subTest(seeds=seeds, other=other):
                out, after, _ = self.changed_during_the_sweep(("--entry", "scatter", "--seeds", seeds), without_scatter, scatter_covers(*other))
                self.assertEqual(out.code, 0, out)
                self.assertEqual(after["covers"]["scatter"], merged)

    def test_one_seed_between_the_run_and_the_covered_range_is_a_gap(self):
        for seeds, covers in (("0:4", [5, 401]), ("402:405", [0, 401])):
            with self.subTest(seeds=seeds, covers=covers), TempKnown(lambda data: data["covers"].update(across=covers)) as known:
                before = known.read_text(encoding="utf-8")
                with mock.patch.object(tool, "check", clean_run):
                    out = call("--entry", "across", "--seeds", seeds, "--update-known", known=known)
                self.assertEqual(known.read_text(encoding="utf-8"), before)
                self.assert_tool_error(out)
                self.assertIn("would leave a gap", out.err)

    def test_the_coverage_rules_are_checked_before_anything_runs(self):
        for args in (("--entry", "across", "--seeds", "0:3"), ("--entry", "across", "--preset", "bento", "--seeds", "3:8")):
            with self.subTest(args), TempKnown(lambda data: data["covers"].update(across=[5, 401])) as known:
                with mock.patch.object(tool, "check") as check:
                    out = call(*args, "--update-known", known=known)
            self.assert_tool_error(out)
            self.assertEqual(out.out, "")
            check.assert_not_called()

    def test_the_help_and_the_description_say_that_two_writers_are_not_locked(self):
        out = call("--help")
        self.assertEqual(out.code, 0, out)
        text = " ".join(out.out.split())
        for words in ("The file is not locked", "the later one wins", "show up as NEW in the next run"):
            self.assertIn(words, text)
        self.assertIn("are not locked against each other", " ".join(tool.__doc__.split()))


# --- writing the known file ----------------------------------------------------------------------


def recording_replace():
    """``os.replace`` that also records (directory of the source, destination) of each call."""
    calls, real = [], os.replace

    def replace(src, dst):
        calls.append((Path(os.path.realpath(Path(src).parent)), Path(os.path.realpath(dst))))
        return real(src, dst)

    return calls, mock.patch.object(tool.os, "replace", replace)


class WriteTargetTest(ToolErrorAssertions):
    def test_the_temporary_file_is_made_next_to_the_known_file(self):
        calls, patch = recording_replace()
        with TempKnown(without_scatter_45) as known:
            with patch:
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
            self.assertEqual(out.code, 0, out)
            self.assertEqual(calls, [(Path(os.path.realpath(known.parent)), Path(os.path.realpath(known)))])

    def test_a_known_file_that_is_a_link_stays_a_link_and_the_file_it_points_to_is_rewritten(self):
        calls, patch = recording_replace()
        with tempfile.TemporaryDirectory() as links, tempfile.TemporaryDirectory() as files:
            target, link = Path(files) / "known.json", Path(links) / "link.json"
            data = json.loads(json.dumps(ORIGINAL))
            without_scatter_45(data)
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"a symbolic link cannot be made here: {exc}")
            with patch:
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=link)
            self.assertEqual(out.code, 0, out)
            self.assertTrue(link.is_symlink())
            self.assertEqual(Path(os.path.realpath(link)), Path(os.path.realpath(target)))
            self.assertIn({**SCATTER_45, "class": "unclassified"}, data_of(target)["known"])
            self.assertEqual(sorted(os.listdir(links)), ["link.json"])
            self.assertEqual(sorted(os.listdir(files)), ["known.json"])
            self.assertEqual(calls, [(Path(os.path.realpath(files)), Path(os.path.realpath(target)))])

    def test_an_interrupt_during_the_write_leaves_the_file_whole_and_no_temporary_file(self):
        with TempKnown(without_scatter_45) as known:
            before = known.read_text(encoding="utf-8")
            with mock.patch.object(tool.os, "replace", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    tool.main([*SCATTER_BENTO, "--seeds", "44:47", "--update-known", "--known", str(known)])
            self.assertEqual(known.read_text(encoding="utf-8"), before)
            self.assertEqual(os.listdir(known.parent), [known.name])

    def test_a_temporary_file_that_cannot_be_removed_does_not_hide_what_stopped_the_write(self):
        full = OSError(errno.ENOSPC, "No space left on device")  # injected
        stuck = PermissionError(errno.EPERM, "Operation not permitted")  # injected
        with TempKnown(without_scatter_45) as known:
            with mock.patch.object(tool.os, "replace", side_effect=full), mock.patch.object(tool.os, "unlink", side_effect=stuck):
                out = call(*SCATTER_BENTO, "--seeds", "44:47", "--update-known", known=known)
            with mock.patch.object(tool.os, "replace", side_effect=KeyboardInterrupt), mock.patch.object(tool.os, "unlink", side_effect=stuck):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
                    tool.main([*SCATTER_BENTO, "--seeds", "44:47", "--update-known", "--known", str(known)])
        self.assert_tool_error(out)
        self.assertIn("No space left on device", out.err)


# --- what main promises about exit codes and limits ------------------------------------------------


class MainTest(ToolErrorAssertions):
    def test_an_unexpected_exception_exits_2_with_its_traceback_never_1(self):
        def boom(run):
            raise ValueError("boom in a check")  # injected

        with mock.patch.object(tool, "check", boom):
            out = call("--entry", "wide", "--preset", "bento", "--seeds", "0:1")
        self.assertEqual(out.code, 2, out)
        self.assertIn("Traceback", out.err)
        self.assertIn("ValueError: boom in a check", out.err)
        self.assertEqual(len(error_lines(out.err)), 1, out.err)
        self.assertEqual(out.out, "")

    def test_keyboard_interrupt_and_system_exit_are_not_swallowed(self):
        with mock.patch.object(tool, "check", side_effect=SystemExit(7)):
            self.assertEqual(call("--entry", "wide", "--preset", "bento", "--seeds", "0:1").code, 7)
        with mock.patch.object(tool, "check", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                tool.main(["--entry", "wide", "--preset", "bento", "--seeds", "0:1"])

    def test_jobs_below_1_and_an_empty_or_reversed_seed_range_are_refused(self):
        for args in (("--jobs", "0", "--seeds", "0:1"), ("--jobs", "-1", "--seeds", "0:1"), ("--seeds", "5:5"), ("--seeds", "6:5")):
            with self.subTest(args), mock.patch.object(tool, "check") as check:
                out = call("--entry", "wide", "--preset", "bento", *args)
            self.assertEqual(out.code, 2, out)
            self.assertEqual(out.out, "")
            check.assert_not_called()

    def test_a_seed_range_below_zero_is_refused_before_anything_runs(self):
        for args in (("--seeds=-1:3",), ("--seeds", "-1:3"), ("--seeds=-5:-2",)):
            with self.subTest(args), mock.patch.object(tool, "check") as check:
                out = call("--entry", "wide", "--preset", "bento", *args)
            self.assertEqual(out.code, 2, out)
            self.assertEqual(out.out, "")
            check.assert_not_called()

    def test_an_entry_the_known_file_does_not_cover_is_refused_by_name_and_without_a_traceback(self):
        def without_wide(data):
            del data["covers"]["wide"]
            data["known"] = [r for r in data["known"] if r["entry"] != "wide"]

        with TempKnown(without_wide) as known, mock.patch.object(tool, "check") as check:
            out = call("--entry", "wide", "--preset", "bento", known=known)
        self.assert_tool_error(out)
        self.assertIn("covers no seeds of 'wide'; pass --seeds", out.err)
        self.assertEqual(out.out, "")
        check.assert_not_called()

    def test_a_plan_of_more_runs_than_the_limit_is_refused_before_anything_runs(self):
        def huge_wide(data):
            data["covers"]["wide"] = [0, 10**12]

        with TempKnown(huge_wide) as known:
            for args, runs in ((("--seeds", "0:1000001"), 1_000_001), (("--seeds", "0:1000000000000"), 10**12), ((), 10**12)):
                with self.subTest(args), mock.patch.object(tool, "check", side_effect=AssertionError("a run was made")) as check:
                    out = call("--entry", "wide", "--preset", "bento", *args, known=known)
                self.assert_tool_error(out)
                self.assertIn(f"would make {runs} runs, more than the limit of 1000000", out.err)
                check.assert_not_called()

    def test_the_limit_counts_every_run_of_the_plan(self):
        known = tool.load_known(KNOWN)

        def plan(seeds, entries=("wide",), presets=("bento",), covers=None):
            args = SimpleNamespace(entry=list(entries), preset=list(presets), seeds=seeds, update_known=False)
            return tool.make_plan(args, known if covers is None else tool.Known(known.classes, covers, {}))

        self.assertEqual(len(plan((0, 1_000_000))), 1)  # exactly the limit
        self.assertEqual(len(plan((0, 33_333), tool.ENTRIES, tool.PRESETS)), 30)  # 999,990 runs
        for case in ((0, 1_000_001), (5, 1_000_006), (0, 10**400)):
            with self.subTest(case), self.assertRaisesRegex(tool.ToolError, "limit of 1000000"):
                plan(case)
        with self.assertRaisesRegex(tool.ToolError, "would make 1000020 runs"):
            plan((0, 33_334), tool.ENTRIES, tool.PRESETS)
        with self.assertRaisesRegex(tool.ToolError, "limit of 1000000"):
            plan(None, covers={**known.covers, "wide": (0, 10**400)})

    def test_the_fallback_is_taken_for_each_way_the_executor_can_be_refused(self):
        refusals = (
            PermissionError(errno.EPERM, "Operation not permitted"),
            OSError(errno.ENOSYS, "Function not implemented"),
            NotImplementedError("no semaphores"),
            ImportError("no _multiprocessing"),
        )
        for refusal in refusals:
            with self.subTest(type(refusal).__name__), mock.patch.object(tool.concurrent.futures, "ProcessPoolExecutor", side_effect=refusal):
                self.assertIsNone(tool._start_executor(2))


# --- workers -----------------------------------------------------------------------------------

# The driver runs ``sweep.main`` with pieces replaced. Its modes come from the environment, not
# from the command line, because a worker process imports the driver again (``spawn`` and
# ``forkserver``) and must replace the same pieces; it inherits the environment.
#   die          the worker that runs seed 45 exits at once
#   slow         each run sleeps SWEEP_SLOW seconds first, and logs to the file SWEEP_TRACE a
#                ``start <pid>`` line when it begins and ``end <pid> <t0> <t1> <SIGINT ignored>``
#                when it ends
#   no-executor  the process-pool executor cannot be built (the fallback pool runs)
#   thread       ``sweep.main`` runs in a thread other than the main thread
#   handlers     after ``sweep.main``, stderr gets ``handlers restored: <bool>`` for SIGINT and SIGTERM
#   start=<m>    the multiprocessing start method is set to <m> (``fork``, ``spawn``, ``forkserver``)
DRIVER = textwrap.dedent(
    """
    import os
    import signal
    import sys
    import time

    sys.path.insert(0, TOOLS_DIR)
    import sweep

    MODES = os.environ.get("SWEEP_DRIVER_MODE", "").split(",")
    real_check = sweep.check

    if "die" in MODES:

        def dying(run):
            if run[2] == 45:
                os._exit(9)
            return real_check(run)

        sweep.check = dying
    if "slow" in MODES:

        def note(line):
            with open(os.environ["SWEEP_TRACE"], "a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def slow(run):
            started = time.time()
            note(f"start {os.getpid()}")
            time.sleep(float(os.environ["SWEEP_SLOW"]))
            result = real_check(run)
            ignored = signal.getsignal(signal.SIGINT) is signal.SIG_IGN
            note(f"end {os.getpid()} {started!r} {time.time()!r} {int(ignored)}")
            return result

        sweep.check = slow
    if "no-executor" in MODES:
        import concurrent.futures

        def refuse(*args, **kwargs):
            raise PermissionError(1, "Operation not permitted")

        concurrent.futures.ProcessPoolExecutor = refuse

    if __name__ == "__main__":
        for mode in MODES:
            if mode.startswith("start="):
                import multiprocessing

                multiprocessing.set_start_method(mode[len("start="):])
        if "thread" in MODES:
            import threading

            codes = []
            thread = threading.Thread(target=lambda: codes.append(sweep.main(sys.argv[1:])))
            thread.start()
            thread.join()
            sys.exit(codes[0] if codes else 3)
        before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
        code = sweep.main(sys.argv[1:])
        if "handlers" in MODES:
            after = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
            print(f"handlers restored: {after == before}", file=sys.stderr)
        sys.exit(code)
    """
).replace("TOOLS_DIR", repr(str(TOOL.parent)))


def driver_env(mode: str, **extra: str) -> dict:
    return {**os.environ, "SWEEP_DRIVER_MODE": mode, **extra}


def executor_available() -> bool:
    """Whether ``concurrent.futures.ProcessPoolExecutor`` can be built here (no worker is started)."""
    executor = tool._start_executor(2)
    if executor is None:
        return False
    executor.shutdown()
    return True


def executor_or_skip(test: unittest.TestCase) -> None:
    if not executor_available():
        test.skipTest(
            "this system refuses concurrent.futures.ProcessPoolExecutor (a sandbox that denies the read of the semaphore limit); "
            "the fallback path is tested apart"
        )

# A stretch of scatter under bento with two known failures close together (seeds 187 and 189).
STRETCH = ("--entry", "scatter", "--preset", "bento", "--seeds", "180:200")


def without_the_stretch_records(data):
    data["known"] = [r for r in data["known"] if not (r["entry"] == "scatter" and r["preset"] == "bento" and 180 <= r["seed"] < 200)]


class WorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.driver = Path(cls.directory.name) / "driver.py"
        cls.driver.write_text(DRIVER, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def drive(self, mode: str, *args: str, known: Path | None = None, **env: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-B", str(self.driver), *args]
        if known is not None:
            cmd += ["--known", str(known)]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=driver_env(mode, **env))

    def test_the_executor_is_used_wherever_the_system_builds_one(self):
        # The skips of the other executor tests ask the tool whether it has an executor, so a tool
        # that never builds one would skip them all. This one asks the system itself.
        try:
            probe = tool.concurrent.futures.ProcessPoolExecutor(2)
        except (OSError, NotImplementedError, ImportError) as exc:
            self.skipTest(f"this system refuses concurrent.futures.ProcessPoolExecutor: {exc}")
        probe.shutdown()
        executor = tool._start_executor(2)
        self.assertIsNotNone(executor)
        executor.shutdown()

    def test_a_worker_that_dies_ends_the_sweep_with_exit_2_instead_of_hanging(self):
        executor = tool._start_executor(2)
        if executor is None:
            self.skipTest(
                "this system refuses concurrent.futures.ProcessPoolExecutor (a sandbox that denies the read of the semaphore limit), "
                "and the fallback pool cannot see a worker that died"
            )
        executor.shutdown()
        out = self.drive("die", *SCATTER_BENTO, "--seeds", "40:50", "--jobs", "2")  # the worker that runs seed 45 exits
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("error: a worker process died; the sweep has no verdict", out.stderr.splitlines())
        self.assertNotIn("Traceback", out.stderr)
        self.assertEqual(out.stdout, "")

    def test_where_the_executor_cannot_start_the_fallback_gives_the_serial_output(self):
        with TempKnown(without_the_stretch_records) as known:
            serial = call(*STRETCH, "--jobs", "1", known=known)
            parallel = self.drive("no-executor", *STRETCH, "--jobs", "3", known=known)
        self.assertEqual(parallel.returncode, 1, parallel.stdout + parallel.stderr)
        self.assertEqual(parallel.stdout, serial.out)

    def assert_runs_in_parallel_workers_that_ignore_sigint(self, mode: str) -> None:
        trace = Path(self.directory.name) / f"trace-{mode}"
        with TempKnown(lambda data: None) as known:
            out = self.drive(
                mode, "--entry", "wide", "--preset", "bento", "--seeds", "0:24", "--jobs", "3", known=known, SWEEP_TRACE=str(trace), SWEEP_SLOW="0.2"
            )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        ends = [line.split()[1:] for line in trace.read_text(encoding="utf-8").splitlines() if line.startswith("end ")]
        self.assertEqual(len(ends), 24)
        spans = [(int(pid), float(t0), float(t1)) for pid, t0, t1, _ in ends]
        self.assertGreaterEqual(len({pid for pid, _, _ in spans}), 2, spans)
        self.assertTrue(
            any(a[0] != b[0] and a[1] < b[2] and b[1] < a[2] for a in spans for b in spans), f"no two runs of different workers overlap: {spans}"
        )
        self.assertEqual({ignored for *_, ignored in ends}, {"1"}, "a worker does not ignore SIGINT")

    def test_the_executor_runs_the_checks_in_several_workers_at_once_and_they_ignore_sigint(self):
        executor_or_skip(self)
        self.assert_runs_in_parallel_workers_that_ignore_sigint("slow")

    def test_the_fallback_pool_runs_the_checks_in_several_workers_at_once_and_they_ignore_sigint(self):
        self.assert_runs_in_parallel_workers_that_ignore_sigint("slow,no-executor")

    def test_a_parallel_sweep_restores_the_signal_handlers_it_replaced(self):
        for mode in ("handlers", "handlers,no-executor"):
            with self.subTest(mode):
                if mode == "handlers":
                    executor_or_skip(self)
                out = self.drive(mode, "--entry", "wide", "--preset", "bento", "--seeds", "0:4", "--jobs", "2")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertIn("handlers restored: True", out.stderr.splitlines())

    def test_a_parallel_sweep_outside_the_main_thread_runs_and_leaves_the_signals_alone(self):
        serial = call("--entry", "wide", "--preset", "bento", "--seeds", "0:4")
        modes = ["thread,no-executor"] + (["thread"] if executor_available() else [])
        for mode in modes:
            with self.subTest(mode):
                out = self.drive(mode, "--entry", "wide", "--preset", "bento", "--seeds", "0:4", "--jobs", "2")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertEqual(out.stdout, serial.out)

    def test_several_failures_come_out_in_the_order_of_the_runs_whatever_the_jobs(self):
        with TempKnown(without_the_stretch_records) as known:
            one = run(*STRETCH, "--jobs", "1", known=known)
            three = run(*STRETCH, "--jobs", "3", known=known)
        self.assertEqual(one.returncode, 1, one.stdout + one.stderr)
        self.assertEqual(three.returncode, 1, three.stdout + three.stderr)
        bad = [line for line in one.stdout.splitlines() if line.startswith("bad ")]
        self.assertGreaterEqual(len(bad), 2, one.stdout)
        self.assertEqual([line.split()[3] for line in bad], sorted((line.split()[3] for line in bad), key=int))
        self.assertEqual(three.stdout, one.stdout)


@unittest.skipIf(os.name != "posix", "process groups, SIGKILL and the signal semantics tested here exist on POSIX only")
class SignalTest(unittest.TestCase):
    """A real sweep process, signalled in the middle of a parallel sweep of slow runs.

    The tool runs in a session of its own, so its process group holds the parent, the workers
    and (with ``spawn``) the resource tracker. Only the parent is signalled, except where a
    terminal's Ctrl-C is imitated. Each run sleeps far longer than the bounds below, so a
    worker that waited for its run or its chunk to end would be caught.
    """

    SLOW = "5"  # seconds per run; a chunk of 8 runs is 40 s of work
    PARENT_BOUND = 2.0  # the parent leaves this soon after the signal
    GROUP_BOUND = 15.0  # nothing of the group is left this soon after the parent left (wide: load)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.driver = self.dir / "driver.py"
        self.driver.write_text(DRIVER, encoding="utf-8")
        self.known = self.dir / "known.json"
        self.known.write_bytes(KNOWN.read_bytes())

    def start(self, mode: str) -> subprocess.Popen:
        self.trace = self.dir / "trace"
        self.out_path, self.err_path = self.dir / "stdout", self.dir / "stderr"
        cmd = [sys.executable, "-B", str(self.driver), "--entry", "wide", "--preset", "bento", "--seeds", "0:60", "--jobs", "3"]
        cmd += ["--known", str(self.known), "--update-known"]
        with open(self.out_path, "wb") as out, open(self.err_path, "wb") as err:
            proc = subprocess.Popen(
                cmd, stdout=out, stderr=err, start_new_session=True, env=driver_env(mode, SWEEP_TRACE=str(self.trace), SWEEP_SLOW=self.SLOW)
            )
        self.addCleanup(proc.wait)
        self.addCleanup(self.kill_group, proc.pid)  # runs first: cleanups run last in, first out
        # Wait until each of the three workers is inside a run: a worker still starting up has
        # not set itself up yet, and is not what these tests are about.
        deadline = time.monotonic() + 120
        while len(self.started_workers()) < 3:
            if proc.poll() is not None or time.monotonic() > deadline:
                self.fail(f"the workers did not start (exit {proc.poll()}): {self.err_path.read_text(encoding='utf-8')}")
            time.sleep(0.05)
        return proc

    def started_workers(self) -> list[str]:
        if not self.trace.exists():
            return []
        lines = self.trace.read_text(encoding="utf-8").splitlines()
        return sorted({line.split()[1] for line in lines if line.startswith("start ") and len(line.split()) == 2})

    @staticmethod
    def kill_group(pgid: int) -> None:
        # PermissionError: on macOS, a group in which every process has ended and waits to be reaped.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)

    def wait_for_the_group(self, pgid: int) -> float:
        """Seconds until no process of the group is left; fails past ``GROUP_BOUND``."""
        start = time.monotonic()
        while True:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return time.monotonic() - start
            except PermissionError:
                # macOS answers this while every process left in the group has ended and waits
                # to be reaped. They are not gone yet: keep waiting, within the same bound.
                pass
            if time.monotonic() - start > self.GROUP_BOUND:
                self.fail(f"processes of the sweep are still alive {self.GROUP_BOUND:g} s after the parent left")
            time.sleep(0.05)

    def interrupt(self, mode: str, signum: int, *, group: bool = False) -> None:
        before = self.known.read_bytes()
        proc = self.start(mode)
        sent = time.monotonic()
        (os.killpg if group else os.kill)(proc.pid, signum)
        code = proc.wait(timeout=60)
        took = time.monotonic() - sent
        self.wait_for_the_group(proc.pid)
        err = self.err_path.read_text(encoding="utf-8")
        if signum == signal.SIGKILL:
            self.assertEqual(code, -signal.SIGKILL, err)
        else:
            self.assertEqual(code, 128 + signum, err)
            self.assertLessEqual(took, self.PARENT_BOUND, err)
            name = signal.Signals(signum).name
            self.assertIn(f"error: interrupted by signal {name}; the sweep has no verdict", err.splitlines())
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.out_path.read_bytes(), b"")
        self.assertEqual(self.known.read_bytes(), before)

    def test_sigterm_to_the_parent_ends_the_executor_sweep_at_once_and_leaves_no_process(self):
        executor_or_skip(self)
        self.interrupt("slow", signal.SIGTERM)

    def test_sigterm_to_the_parent_ends_the_fallback_sweep_at_once_and_leaves_no_process(self):
        self.interrupt("slow,no-executor", signal.SIGTERM)

    def test_sigint_to_the_parent_ends_the_executor_sweep_at_once_and_leaves_no_process(self):
        executor_or_skip(self)
        self.interrupt("slow", signal.SIGINT)

    def test_sigint_to_the_parent_ends_the_fallback_sweep_at_once_and_leaves_no_process(self):
        self.interrupt("slow,no-executor", signal.SIGINT)

    def test_a_ctrl_c_to_the_whole_group_ends_the_executor_sweep_quietly(self):
        executor_or_skip(self)
        self.interrupt("slow", signal.SIGINT, group=True)

    def test_a_ctrl_c_to_the_whole_group_ends_the_fallback_sweep_quietly(self):
        self.interrupt("slow,no-executor", signal.SIGINT, group=True)

    def test_the_executor_workers_end_when_the_parent_is_killed(self):
        executor_or_skip(self)
        self.interrupt("slow", signal.SIGKILL)

    def test_the_fallback_workers_end_when_the_parent_is_killed(self):
        self.interrupt("slow,no-executor", signal.SIGKILL)

    # The start method decides what a worker starts with: a forked one is a copy of the parent,
    # signal handlers and open pipes included. Linux forks by default up to Python 3.13.

    def test_forked_executor_workers_end_when_the_parent_is_killed(self):
        executor_or_skip(self)
        self.interrupt("slow,start=fork", signal.SIGKILL)

    def test_forked_fallback_workers_end_when_the_parent_is_killed(self):
        self.interrupt("slow,no-executor,start=fork", signal.SIGKILL)

    def test_a_forked_worker_sent_sigterm_dies_without_a_word_of_its_own(self):
        # The parent's handler prints the "interrupted" line; a forked worker must not keep it.
        executor_or_skip(self)
        before = self.known.read_bytes()
        proc = self.start("slow,start=fork")
        os.kill(int(self.started_workers()[0]), signal.SIGTERM)
        code = proc.wait(timeout=60)
        self.wait_for_the_group(proc.pid)
        err = self.err_path.read_text(encoding="utf-8")
        self.assertEqual(code, 2, err)
        self.assertEqual(error_lines(err), ["error: a worker process died; the sweep has no verdict"])
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.out_path.read_bytes(), b"")
        self.assertEqual(self.known.read_bytes(), before)


# --- the judgement of one run, with the pieces under it replaced by injected results --------------


def finding(code: str, message: str = "m", severity: str = "error"):
    return SimpleNamespace(code=SimpleNamespace(value=code), severity=SimpleNamespace(value=severity), message=message)


def corridor(kind, x: float, right: float):
    return SimpleNamespace(kind=kind, rect=SimpleNamespace(x=x, right=right))


class NarrowJudgementTest(unittest.TestCase):
    """``check`` on the narrow entry. Every answer of the layout pieces is injected, not real."""

    def check(self, *, findings=(), holes=(), gaps=(), corridors=(), routed=None, **patches):
        stubs = {
            "random_graph": lambda seed: {"graph": "full"},
            "reduced_graph": lambda seed: {"graph": "reduced"},
            "routed": routed
            or (lambda doc, preset: (object(), SimpleNamespace(groups=[]), SimpleNamespace(corridors=list(corridors)), SimpleNamespace(findings=list(findings)), None, None)),
            "top_links": lambda canvas, plan: [],
            "sections": lambda canvas, plan, result, preset: [object()],
            "column_holes": lambda section: list(holes),
            "gap_faults": lambda section, links, cockpit: list(gaps),
            **patches,
        }
        with contextlib.ExitStack() as stack:
            for name, value in stubs.items():
                stack.enter_context(mock.patch.object(tool, name, value))
            compile_document = stack.enter_context(mock.patch.object(tool, "compile_document"))
            result = tool.check(("narrow", "bento", 0))
        compile_document.assert_not_called()
        return result

    def test_a_clean_run_has_no_failure(self):
        self.assertEqual(self.check(), [])

    def test_error_findings_of_the_narrow_codes_count_for_both_graphs(self):
        for code in tool.NARROW_CODES:
            with self.subTest(code):
                self.assertEqual(self.check(findings=[finding(code, "bad")]), [(f"full:{code}", "bad"), (f"reduced:{code}", "bad")])

    def test_other_findings_do_not_count(self):
        self.assertEqual(self.check(findings=[finding("G2", severity="warning"), finding("G2", severity="info"), finding("I1")]), [])

    def test_the_first_message_of_a_code_is_kept_on_one_line_and_codes_are_sorted(self):
        found = self.check(findings=[finding("G5", "z"), finding("G2", "first\n   line"), finding("G2", "second")])
        self.assertEqual(
            found, [("full:G2", "first line"), ("full:G5", "z"), ("reduced:G2", "first line"), ("reduced:G5", "z")]
        )

    def test_a_column_hole_is_a_failure_under_the_code_hole(self):
        self.assertEqual(
            self.check(holes=[(1.0, 2.5)]),
            [("full:hole", "section 0: no group over x 1 to 2.5"), ("reduced:hole", "section 0: no group over x 1 to 2.5")],
        )

    def test_an_unbridged_gap_is_a_failure_under_the_code_gap(self):
        message = "section 0: main-row gap x 3 to 4 (injected cause)"
        self.assertEqual(self.check(gaps=[((3.0, 4.0), "injected cause")]), [("full:gap", message), ("reduced:gap", message)])

    def test_a_gap_over_a_spine_corridor_is_exempt_and_other_corridors_do_not_exempt_it(self):
        gaps = [((3.0, 4.0), "cause")]
        kind = tool.CorridorKind
        self.assertEqual(self.check(gaps=gaps, corridors=[corridor(kind.SPINE, 3.0, 4.0)]), [])
        self.assertTrue(self.check(gaps=gaps, corridors=[corridor(kind.SPINE, 3.0, 5.0)]))
        self.assertTrue(self.check(gaps=gaps, corridors=[corridor(kind.BUS, 3.0, 4.0)]))
        self.assertTrue(self.check(gaps=gaps, corridors=[corridor(kind.SKYBRIDGE, 3.0, 4.0)]))

    def test_an_exception_of_the_pipeline_is_a_failure_named_for_its_type(self):
        def boom(doc, preset):
            raise RuntimeError("boom")

        self.assertEqual(self.check(routed=boom), [("full:crash-RuntimeError", "boom"), ("reduced:crash-RuntimeError", "boom")])

    def test_an_exception_in_a_later_piece_is_caught_too(self):
        def boom(section):
            raise KeyError("k")

        self.assertEqual(self.check(column_holes=boom), [("full:crash-KeyError", "'k'"), ("reduced:crash-KeyError", "'k'")])

    def test_an_exception_of_one_generator_is_a_failure_of_that_graph_only(self):
        def boom(seed):
            raise ValueError("no graph")

        self.assertEqual(self.check(reduced_graph=boom), [("reduced:crash-ValueError", "no graph")])
        self.assertEqual(self.check(random_graph=boom), [("full:crash-ValueError", "no graph")])


class WideJudgementTest(unittest.TestCase):
    """``check`` on the wide entries. The compile, the stack check and the generators are injected."""

    WIDE_ENTRIES = ("wide", "outputs", "scatter", "fed", "across")

    def check(self, entry, *, findings=(), compile_error=None, faults=(), fault_error=None):
        seen = {}

        def compile_document(doc, defs, preset):
            seen.update(doc=doc, preset=preset)
            if compile_error is not None:
                raise compile_error
            return SimpleNamespace(report=SimpleNamespace(findings=list(findings)), authoring="authoring")

        def stack_faults(compiled, plan):
            if fault_error is not None:
                raise fault_error
            return list(faults)

        with contextlib.ExitStack() as stack:
            for name in ("wide_graph", "wide_graph_outputs", "wide_graph_scatter", "wide_graph_fed", "wide_graph_across"):
                stack.enter_context(mock.patch.object(tool.wide_gen, name, lambda seed, name=name: {"builder": name, "seed": seed}))
            stack.enter_context(mock.patch.object(tool, "compile_document", compile_document))
            stack.enter_context(mock.patch.object(tool, "instantiate", lambda authoring: "canvas"))
            stack.enter_context(mock.patch.object(tool, "annotate", lambda canvas, defs, authoring: "plan"))
            stack.enter_context(mock.patch.object(tool, "stack_faults", stack_faults))
            return tool.check((entry, "stepped", 7)), seen

    def test_each_entry_compiles_its_own_generator_under_the_preset(self):
        for entry in self.WIDE_ENTRIES:
            with self.subTest(entry):
                found, seen = self.check(entry)
                builder = "wide_graph" if entry == "wide" else f"wide_graph_{entry}"
                self.assertEqual(seen, {"doc": {"builder": builder, "seed": 7}, "preset": "stepped"})
                self.assertEqual(found, [])

    def test_an_error_finding_counts_under_its_own_code_and_other_severities_do_not(self):
        for entry in self.WIDE_ENTRIES:
            with self.subTest(entry):
                found, _ = self.check(entry, findings=[finding("G5", "x"), finding("C3", "c", "warning"), finding("G5", "again"), finding("E1", "e")])
                self.assertEqual(found, [("E1", "e"), ("G5", "x")])

    def test_a_compile_error_is_a_failure_per_finding_under_raised(self):
        error = CwcError(ExitCode.INPUT_ERROR, [Finding(Code.E1, Severity.ERROR, "bad edge"), Finding(Code.E2, Severity.ERROR, "bad\n  port")])
        for entry in self.WIDE_ENTRIES:
            with self.subTest(entry):
                found, _ = self.check(entry, compile_error=error)
                self.assertEqual(found, [("raised-E1", "bad edge"), ("raised-E2", "bad port")])

    def test_any_other_exception_of_the_compile_is_a_failure_named_for_its_type(self):
        for entry in self.WIDE_ENTRIES:
            with self.subTest(entry):
                found, _ = self.check(entry, compile_error=KeyError("k"))
                self.assertEqual(found, [("crash-KeyError", "'k'")])

    def test_a_stack_fault_is_a_failure_only_for_the_stack_entries(self):
        faults = ["a", "b", "c", "d"]
        for entry in self.WIDE_ENTRIES:
            with self.subTest(entry):
                found, _ = self.check(entry, faults=faults)
                self.assertEqual(found, [] if entry == "wide" else [("stack", "['a', 'b', 'c']")])
                self.assertEqual(self.check(entry, faults=[])[0], [])

    def test_a_stack_fault_and_an_error_finding_are_both_reported(self):
        found, _ = self.check("scatter", findings=[finding("G5", "x")], faults=["a"])
        self.assertEqual(found, [("G5", "x"), ("stack", "['a']")])

    def test_an_exception_of_the_stack_check_is_a_failure_named_for_its_type(self):
        found, _ = self.check("fed", fault_error=ValueError("boom"))
        self.assertEqual(found, [("stack-crash-ValueError", "boom")])
        self.assertEqual(self.check("wide", fault_error=ValueError("boom"))[0], [])


if __name__ == "__main__":
    unittest.main()
