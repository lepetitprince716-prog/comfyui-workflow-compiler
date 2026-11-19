"""Sweep the random-graph generators over seeds and presets, and reconcile with the known failures.

Run from anywhere: ``python3 tools/sweep.py [--entry E ...] [--preset P ...] [--seeds LO:HI]
[--jobs N] [--known PATH] [--update-known]``.

An entry is one generator of test graphs; a run of an entry is one seed under one preset.
Which checks make a run bad depends on the entry:

``narrow``
    ``random_graph(seed)`` and ``reduced_graph(seed)`` from ``tests.layout_generator``, both
    laid out and routed with the preset. Bad when the pipeline raises, when the compile-mode
    geometry report holds an error finding with a code of ``NARROW_CODES``, when a section has
    a column hole (``hole``), or when a main-row gap is not bridged (``gap``; gaps over a spine
    corridor and links into a cockpit frame are exempt). Codes carry the graph as a prefix:
    ``full:G5``, ``reduced:hole``.
``wide``
    ``wide_graph(seed)`` compiled with the preset. Bad when the compile raises
    (``raised-<code>`` for a ``CwcError``, ``crash-<type>`` for anything else) or its report
    holds any error finding.
``outputs``, ``scatter``, ``fed``, ``across``
    The graph of the matching ``wide_graph_<entry>`` entry, judged as ``wide``, and also bad
    when ``tests.test_layout_wide.stack_faults`` finds a stack fault (code ``stack``).

A failure is one (entry, preset, seed, code). The known failures live in a JSON file
(``tools/sweep_known.json`` by default); each record names its entry, preset, seed, code and
class, and the file says what each class is and which seeds of each entry it covers. With no
``--seeds``, an entry runs over the range the known file covers for it. A sweep makes at most
``MAX_RUNS`` runs; a larger plan is refused before anything runs.

The reconciliation is strict, over the runs made: a failure not in the known file is ``new``;
a known record whose run was made and did not show its code is ``gone``. Either one is a
mismatch: exit code 1. ``--update-known`` rewrites the records inside the runs made with this
run's failures (keeping the class of a record that stays, and marking the others
``unclassified``) and leaves the rest of the file as it is.

The covered range of an entry is one half-open range ``[first, last + 1)``, and it holds for
every preset. So ``--update-known`` may extend it only with a run of all presets, and only by
a range that overlaps it or touches it (a run that ends where the range begins, or begins where
it ends); anything else would make the file claim seeds nobody ran, and is refused.

The merge is made onto the file as it is when the sweep ends, not as it was when the sweep
began: the file is read again just before it is written, so records another ``--update-known``
run added meanwhile outside these runs are kept. The coverage rules are checked twice: against
the file read at the start, before the sweep (so a refused run costs nothing), and against the
file read again before the write. When the file changed during the sweep so that this run no
longer fits it, nothing is written and the run exits 2.

Two ``--update-known`` runs are not locked against each other. What holds: the file is always
one whole, valid known file (it is replaced atomically), and the covered range it states is
always the range the writing run saw on its re-read plus the range it ran, with no gap. What
does not hold: when two runs write at the same moment, between one's re-read and its replace,
the later write wins and the records the earlier one added are lost; the next ordinary run
reports their failures as ``NEW``, so the loss is loud, never silent. When two runs overlap in
the seeds they ran, the one that writes last decides the overlap.

The file is replaced atomically (through a temporary file in its own directory, which is
removed whatever stops the write), keeps its permission bits (0644 when it is new), and is not
touched until every report line has been written and flushed. A known file that is a symbolic
link stays a link: the file it points to is rewritten.

Output, on stdout: one line per failure (``bad``) and per gone record (``gone``), then one
``sum`` line per entry and preset, then the verdict. It depends only on the code, the entries,
presets and seeds, and the known file: never on ``--jobs`` or on timing; results are ordered by
the order of the runs, not by the order in which they finish.

Exit codes: 0 every run matches the known file (or ``--update-known`` rewrote it), 1 mismatch
and nothing else, 2 usage error, a known file that is unreadable or malformed, a known file
that cannot be written or changed during the sweep so that this run no longer fits it, a plan
of too many runs, standard output that is closed or that the reader left (when that happens
after the known file was rewritten, the ``error:`` line says so), a worker process that died
(the sweep then has no verdict), or an unexpected exception (its traceback goes to stderr). A
tool error prints one ``error:`` line on stderr and no traceback.

With ``--jobs`` above 1, the workers ignore SIGINT (a Ctrl-C in a terminal reaches the whole
process group; the parent handles it) and end on their own when the parent ends, however it
ends. During the parallel sweep the parent answers SIGINT and SIGTERM at once: one ``error:``
line, exit code 128 plus the signal number, nothing on stdout and the known file untouched.
Two things can add lines to stderr after that one, and change nothing else: with the ``spawn``
and ``forkserver`` start methods, Python's resource tracker warns of the semaphores the parent
left and removes them itself; and a worker that is still starting up when a Ctrl-C reaches the
process group does not ignore it yet, and prints its own traceback.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import multiprocessing
import multiprocessing.connection
import os
import signal
import stat
import sys
import tempfile
import threading
import traceback
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tests import layout_generator_wide as wide_gen  # noqa: E402
from tests.graphs import DEFS  # noqa: E402
from tests.layout_cases import (  # noqa: E402
    COMPILE_ERRORS,
    SIMPLE_ERRORS,
    column_holes,
    gap_faults,
    routed,
    sections,
    top_links,
)
from tests.layout_generator import random_graph, reduced_graph  # noqa: E402
from tests.test_layout_wide import stack_faults  # noqa: E402

from cwc.annotate import annotate  # noqa: E402
from cwc.ingest.limits import MAX_DEPTH, exceeds_depth  # noqa: E402
from cwc.instantiate import instantiate  # noqa: E402
from cwc.layout.presets import get_preset  # noqa: E402
from cwc.layout.types import CorridorKind  # noqa: E402
from cwc.pipeline import compile_document  # noqa: E402
from cwc.report import CwcError  # noqa: E402

ENTRIES = ("narrow", "wide", "outputs", "scatter", "fed", "across")
PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")
NARROW_CODES = ("G2",) + COMPILE_ERRORS + SIMPLE_ERRORS
STACK_ENTRIES = frozenset({"outputs", "scatter", "fed", "across"})
DEFAULT_KNOWN = ROOT / "tools" / "sweep_known.json"
FORMAT = 1
UNCLASSIFIED = "unclassified"
UNCLASSIFIED_TEXT = "Recorded by --update-known and not yet assigned to a cause."
CHUNK = 8
MAX_RUNS = 1_000_000


class ToolError(Exception):
    """The sweep cannot give a verdict or cannot finish its work: exit code 2, one ``error:`` line."""


class KnownFileError(ToolError):
    """The known-failures file is missing, unreadable or malformed."""


class CoverageError(ToolError):
    """A run that ``--update-known`` cannot merge into the covered range of an entry."""


@dataclass(frozen=True, order=True)
class Failure:
    entry: str
    preset: str
    seed: int
    code: str


# --- one run -------------------------------------------------------------------------------


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _narrow_graph(doc, preset) -> dict[str, str]:
    found: dict[str, str] = {}
    try:
        canvas, plan, result, report, _, _ = routed(doc, preset)
        for f in report.findings:
            if f.severity.value == "error" and f.code.value in NARROW_CODES:
                found.setdefault(f.code.value, f.message)
        links = top_links(canvas, plan)
        cockpit = frozenset(g.key for g in plan.groups if g.parent is None and g.zone.value == "cockpit")
        spines = {(c.rect.x, c.rect.right) for c in result.corridors if c.kind is CorridorKind.SPINE}
        for i, sec in enumerate(sections(canvas, plan, result, preset)):
            for hole in column_holes(sec):
                found.setdefault("hole", f"section {i}: no group over x {hole[0]:g} to {hole[1]:g}")
            for gap, cause in gap_faults(sec, links, cockpit):
                if gap not in spines:
                    found.setdefault("gap", f"section {i}: main-row gap x {gap[0]:g} to {gap[1]:g} ({cause})")
    except Exception as exc:  # noqa: BLE001 - a sweep reports whatever happens
        found["crash-" + type(exc).__name__] = str(exc)
    return found


def _narrow(seed: int, preset_name: str) -> dict[str, str]:
    preset = get_preset(preset_name)
    found: dict[str, str] = {}
    for graph, build in (("full", random_graph), ("reduced", reduced_graph)):
        try:
            doc = build(seed)
        except Exception as exc:  # noqa: BLE001
            found[f"{graph}:crash-{type(exc).__name__}"] = str(exc)
            continue
        for code, message in _narrow_graph(doc, preset).items():
            found[f"{graph}:{code}"] = message
    return found


def _wide(entry: str, seed: int, preset_name: str) -> dict[str, str]:
    builder = wide_gen.wide_graph if entry == "wide" else getattr(wide_gen, f"wide_graph_{entry}")
    found: dict[str, str] = {}
    try:
        compiled = compile_document(builder(seed), DEFS, preset=preset_name)
    except CwcError as exc:
        for f in exc.findings:
            found.setdefault("raised-" + f.code.value, f.message)
        return found
    except Exception as exc:  # noqa: BLE001
        found["crash-" + type(exc).__name__] = str(exc)
        return found
    for f in compiled.report.findings:
        if f.severity.value == "error":
            found.setdefault(f.code.value, f.message)
    if entry in STACK_ENTRIES:
        # One compile serves both checks: it is deterministic, and the findings above only
        # read ``compiled``.
        try:
            plan = annotate(instantiate(compiled.authoring), DEFS, compiled.authoring)
            faults = stack_faults(compiled, plan)
        except Exception as exc:  # noqa: BLE001
            found["stack-crash-" + type(exc).__name__] = str(exc)
        else:
            if faults:
                found["stack"] = repr(faults[:3])
    return found


def check(run: tuple[str, str, int]) -> list[tuple[str, str]]:
    """The failures of one run: ``(code, first message)`` sorted by code."""
    entry, preset, seed = run
    found = _narrow(seed, preset) if entry == "narrow" else _wide(entry, seed, preset)
    return sorted((code, _one_line(message)) for code, message in found.items())


# --- parallel runs ---------------------------------------------------------------------------


def _worker_init() -> None:
    """Set up a worker process, on both parallel paths.

    A worker ignores SIGINT: a Ctrl-C in a terminal goes to the whole process group, and the
    parent alone decides what it means. SIGTERM keeps its default meaning in a worker: a forked
    worker starts with the parent's handler, whose message is the parent's to give. A worker
    also ends as soon as its parent ends, however the parent ends (SIGKILL included): a daemon
    thread waits on the parent's sentinel.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    parent = multiprocessing.parent_process()
    if parent is not None:
        threading.Thread(target=_exit_with_parent, args=(parent.sentinel,), name="exit-with-parent", daemon=True).start()


def _exit_with_parent(sentinel) -> None:
    multiprocessing.connection.wait([sentinel])
    os._exit(1)


def _end_on_signal(signum: int, frame) -> None:
    """Leave at once: the workers end by themselves when this process is gone."""
    with contextlib.suppress(Exception):
        sys.stderr.write(f"error: interrupted by signal {signal.Signals(signum).name}; the sweep has no verdict\n")
        sys.stderr.flush()
    os._exit(128 + signum)


@contextlib.contextmanager
def _signals_end_the_process():
    """While the workers run, SIGINT and SIGTERM end this process at once.

    Without this, the executor answers a SIGINT only after the queued work is done, and a
    SIGTERM kills the parent without a word. Handlers can only be set in the main thread;
    elsewhere the signals keep their handlers.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {}
    try:
        for name in ("SIGINT", "SIGTERM"):
            signum = getattr(signal, name, None)
            if signum is not None:
                previous[signum] = signal.signal(signum, _end_on_signal)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _start_executor(jobs: int) -> concurrent.futures.ProcessPoolExecutor | None:
    """A process pool executor, or ``None`` where the system refuses to build one.

    The executor reads the system semaphore limit when it is built; a sandbox that denies that
    read makes the constructor raise ``PermissionError``.
    """
    try:
        return concurrent.futures.ProcessPoolExecutor(jobs, initializer=_worker_init)
    except (OSError, NotImplementedError, ImportError):
        return None


def sweep(runs: list[tuple[str, str, int]], jobs: int) -> dict[tuple[str, str, int], list[tuple[str, str]]]:
    if jobs <= 1:
        return {run: check(run) for run in runs}
    with _signals_end_the_process():
        executor = _start_executor(jobs)
        if executor is None:
            # Fallback for a system that refuses the executor. ``multiprocessing.Pool`` cannot see
            # a worker that was killed from outside: it waits for that worker's results forever,
            # so on this path the workflow's job timeout is the only bound.
            with multiprocessing.Pool(jobs, initializer=_worker_init) as pool:
                return dict(zip(runs, pool.map(check, runs, chunksize=CHUNK)))
        try:
            with executor:
                # ``map`` yields in the order of ``runs``, whatever the order of completion.
                return dict(zip(runs, executor.map(check, runs, chunksize=CHUNK)))
        except BrokenProcessPool as exc:
            raise ToolError("a worker process died; the sweep has no verdict") from exc


# --- the known file ------------------------------------------------------------------------


@dataclass
class Known:
    classes: dict[str, str]
    covers: dict[str, tuple[int, int]]
    records: dict[Failure, str]  # failure -> class


def load_known(path: Path) -> Known:
    try:
        text = path.read_text(encoding="utf-8")
        if exceeds_depth(text):
            # Never handed to the parser: its C scanner can overrun the stack on such a document.
            raise KnownFileError(f"{path}: nested deeper than {MAX_DEPTH} levels")
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        raise KnownFileError(f"{path}: {exc}") from exc
    return parse_known(data, str(path))


def parse_known(data, where: str = "known file") -> Known:
    def fail(message: str):
        raise KnownFileError(f"{where}: {message}")

    if not isinstance(data, dict) or type(data.get("format")) is not int or data["format"] != FORMAT:
        fail(f"expected an object with \"format\": {FORMAT}")
    if sorted(data) != ["classes", "covers", "format", "known"]:
        fail("expected exactly the keys classes, covers, format, known")
    classes = data["classes"]
    if not isinstance(classes, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in classes.items()):
        fail("classes must map a class name to its description")
    covers: dict[str, tuple[int, int]] = {}
    if not isinstance(data["covers"], dict):
        fail("covers must map an entry to [first seed, last seed + 1]")
    for entry, span in data["covers"].items():
        if entry not in ENTRIES:
            fail(f"covers: unknown entry {entry!r}")
        if not (isinstance(span, list) and len(span) == 2 and all(type(n) is int for n in span) and 0 <= span[0] < span[1]):
            fail(f"covers[{entry!r}] must be [first seed, last seed + 1]")
        covers[entry] = (span[0], span[1])
    if not isinstance(data["known"], list):
        fail("known must be a list of records")
    records: dict[Failure, str] = {}
    for i, rec in enumerate(data["known"]):
        if not isinstance(rec, dict) or sorted(rec) != ["class", "code", "entry", "preset", "seed"]:
            fail(f"known[{i}] must have exactly the keys entry, preset, seed, code, class")
        entry, preset, seed, code, cls = rec["entry"], rec["preset"], rec["seed"], rec["code"], rec["class"]
        for field, value in (("entry", entry), ("preset", preset), ("code", code), ("class", cls)):
            if not isinstance(value, str):
                fail(f"known[{i}]: {field} must be a string")
        if entry not in covers:
            fail(f"known[{i}]: entry {entry!r} has no covers range")
        if preset not in PRESETS:
            fail(f"known[{i}]: unknown preset {preset!r}")
        if type(seed) is not int or not covers[entry][0] <= seed < covers[entry][1]:
            fail(f"known[{i}]: seed {seed!r} is outside the covered range of {entry!r}")
        if not code or any(ch.isspace() for ch in code):
            fail(f"known[{i}]: code must be one word")
        if cls not in classes:
            fail(f"known[{i}]: class {cls!r} is not described in classes")
        failure = Failure(entry, preset, seed, code)
        if failure in records:
            fail(f"known[{i}] repeats {entry} {preset} {seed} {code}")
        records[failure] = cls
    return Known(classes, covers, records)


def dump_known(known: Known) -> str:
    """The file text: one record per line, records in entry, preset, seed, code order."""
    used = set(known.records.values())
    classes = {k: v for k, v in known.classes.items() if k != UNCLASSIFIED or UNCLASSIFIED in used}
    records = [
        {"entry": f.entry, "preset": f.preset, "seed": f.seed, "code": f.code, "class": known.records[f]}
        for f in sorted(known.records, key=lambda f: (ENTRIES.index(f.entry), PRESETS.index(f.preset), f.seed, f.code))
    ]
    covers = ", ".join(f"{json.dumps(e)}: {json.dumps(list(known.covers[e]))}" for e in ENTRIES if e in known.covers)
    lines = ["{", f'  "format": {FORMAT},', '  "classes": {']
    lines.append(",\n".join(f"    {json.dumps(k)}: {json.dumps(v)}" for k, v in classes.items()))
    lines += ["  },", '  "covers": {' + covers + "},", '  "known": [']
    lines.append(",\n".join("    " + json.dumps(rec) for rec in records))
    lines += ["  ]", "}"]
    return "\n".join(line for line in lines if line) + "\n"


def write_known(path: Path, known: Known) -> None:
    """Replace ``path`` atomically; the file keeps its permission bits (0644 when it is new).

    When ``path`` is a symbolic link, the file it points to is replaced and the link stays. The
    temporary file sits in the target's own directory, so the replace never crosses file
    systems, and it is removed when an exception stops the write (``KeyboardInterrupt``
    included); a removal that fails does not hide the error that stopped the write. A signal
    that ends the process outright (SIGTERM, SIGKILL) leaves the temporary file next to the
    known file, which itself stays whole.
    """
    text = dump_known(known)
    parse_known(json.loads(text), "the rewritten known file")  # never write a file load_known refuses
    target = Path(os.path.realpath(path))
    tmp = None
    try:
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            mode = 0o644
        fd, tmp = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException as exc:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        if isinstance(exc, OSError):
            raise ToolError(f"{path}: cannot write the known file: {exc}") from exc
        raise


def update_known_file(path: Path, plan: list[tuple[str, str, range]], results) -> None:
    """Merge this run's results onto the file as it is now, then write it.

    Another ``--update-known`` run may have changed the file since this one read it; the merge
    must not undo that outside the runs made here. A file that has since become unreadable or
    malformed is not written over, nor one whose covered ranges no longer fit this run.
    """
    current = load_known(path)
    try:
        merged = updated(current, plan, results)
    except CoverageError as exc:
        raise ToolError(f"{path}: the known file changed during the sweep and no longer fits this run, so it was not written: {exc}") from exc
    write_known(path, merged)


# --- reconciliation and report ---------------------------------------------------------------


def report(
    plan: list[tuple[str, str, range]],
    results: dict[tuple[str, str, int], list[tuple[str, str]]],
    known: Known,
) -> tuple[list[str], list[Failure], list[Failure]]:
    """The output lines, the new failures and the gone records."""
    lines: list[str] = []
    sums: list[str] = []
    new_all: list[Failure] = []
    gone_all: list[Failure] = []
    for entry, preset, seeds in plan:
        bad = new = gone = 0
        for seed in seeds:
            found = results[(entry, preset, seed)]
            codes = {code for code, _ in found}
            bad += bool(found)
            for code, message in found:
                failure = Failure(entry, preset, seed, code)
                status = "known" if failure in known.records else "NEW"
                if status == "NEW":
                    new += 1
                    new_all.append(failure)
                lines.append(f"bad {entry} {preset} {seed} {code} {status}: {message}")
            for failure in sorted(f for f in known.records if (f.entry, f.preset, f.seed) == (entry, preset, seed)):
                if failure.code not in codes:
                    gone += 1
                    gone_all.append(failure)
                    lines.append(f"gone {entry} {preset} {seed} {failure.code}: in the known list but did not fail; remove it")
        sums.append(f"sum {entry} {preset} {seeds.start}-{seeds.stop}: {len(seeds)} runs, {bad} bad, {new} new, {gone} gone")
    return lines + sums, new_all, gone_all


def merged_cover(entry: str, old: tuple[int, int] | None, seeds: range, all_presets: bool) -> tuple[int, int]:
    """The covered range of ``entry`` once a run of ``seeds`` is merged into ``old``.

    The one place of the coverage rules. Ranges are half-open: a run that ends where the
    covered range begins, or begins where it ends, extends it; one seed between them is a gap.
    A gap, or an extension by a run of only some presets, raises ``CoverageError``.
    """
    lo, hi = seeds.start, seeds.stop
    if old is not None and (hi < old[0] or lo > old[1]):
        # The file covers one contiguous range per entry: a run apart from it would make the
        # file claim seeds nobody ran.
        raise CoverageError(f"--update-known with {entry} seeds {lo}:{hi} would leave a gap in the covered {old[0]}:{old[1]}")
    widens = old is None or lo < old[0] or hi > old[1]
    if widens and not all_presets:
        # A covered range holds for every preset.
        raise CoverageError(f"--update-known can widen the range of {entry} only in a run of all presets")
    if old is None:
        return lo, hi
    return min(lo, old[0]), max(hi, old[1])


def _merged_covers(covers: dict[str, tuple[int, int]], plan: list[tuple[str, str, range]]) -> dict[str, tuple[int, int]]:
    merged = dict(covers)
    for entry in dict.fromkeys(e for e, _, _ in plan):
        seeds = next(s for e, _, s in plan if e == entry)
        all_presets = {p for e, p, _ in plan if e == entry} == set(PRESETS)
        merged[entry] = merged_cover(entry, covers.get(entry), seeds, all_presets)
    return merged


def updated(known: Known, plan: list[tuple[str, str, range]], results) -> Known:
    """``known`` with the records inside the runs of ``plan`` replaced by this run's failures.

    Raises ``CoverageError`` when the runs of ``plan`` do not fit the covered ranges of ``known``.
    """
    covers = _merged_covers(known.covers, plan)
    ran = {(e, p, s) for e, p, seeds in plan for s in seeds}
    records = {f: c for f, c in known.records.items() if (f.entry, f.preset, f.seed) not in ran}
    for run in sorted(ran):
        for code, _ in results[run]:
            failure = Failure(*run, code)
            records[failure] = known.records.get(failure, UNCLASSIFIED)
    classes = dict(known.classes)
    classes.setdefault(UNCLASSIFIED, UNCLASSIFIED_TEXT)
    return Known(classes, covers, records)


def parse_seeds(text: str) -> tuple[int, int]:
    try:
        lo_text, hi_text = text.split(":")
        lo, hi = int(lo_text), int(hi_text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected LO:HI, got {text!r}") from None
    if not 0 <= lo < hi:
        raise argparse.ArgumentTypeError(f"expected 0 <= LO < HI, got {text!r}")
    return lo, hi


def main(argv: list[str] | None = None) -> int:
    try:
        _stdout()  # first of all: a report that cannot be written is not worth a sweep
        return run_sweep(parse_args(argv))
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - exit 1 means a mismatch and nothing else
        traceback.print_exc()
        print("error: unexpected exception (traceback above); the sweep has no verdict", file=sys.stderr)
        return 2


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """The arguments, or ``SystemExit`` 2 with argparse's usage message.

    ``main`` looks at standard output before it calls this: from Python 3.14 on, building the
    parser itself reads ``sys.stdout``, and fails on one that is closed.
    """
    parser = argparse.ArgumentParser(
        description="Sweep the random-graph generators and reconcile with the known failures.",
        epilog="Exit codes: 0 all runs match the known file, 1 mismatch, 2 usage or tool error.",
    )
    parser.add_argument("--entry", action="append", choices=ENTRIES, help="an entry to run (repeatable; default: all)")
    parser.add_argument("--preset", action="append", choices=PRESETS, help="a preset to run (repeatable; default: all)")
    parser.add_argument("--seeds", type=parse_seeds, metavar="LO:HI", help="seeds LO to HI-1 for every entry (default: the range the known file covers per entry)")
    parser.add_argument("--jobs", type=int, default=1, metavar="N", help="worker processes (default: 1)")
    parser.add_argument("--known", type=Path, default=DEFAULT_KNOWN, metavar="PATH", help="the known-failures file (default: tools/sweep_known.json)")
    parser.add_argument(
        "--update-known",
        action="store_true",
        help=(
            "rewrite the known records inside the runs made with this run's failures. The file is not locked:"
            " it is always whole and valid, and its covered ranges never get a gap, but of two runs that write"
            " at the same moment the later one wins, and the records the earlier one added show up as NEW in"
            " the next run"
        ),
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    return args


def make_plan(args: argparse.Namespace, known: Known) -> list[tuple[str, str, range]]:
    entries = [e for e in ENTRIES if e in (args.entry or ENTRIES)]
    presets = [p for p in PRESETS if p in (args.preset or PRESETS)]
    plan: list[tuple[str, str, range]] = []
    for entry in entries:
        if args.seeds is None and entry not in known.covers:
            raise ToolError(f"the known file covers no seeds of {entry!r}; pass --seeds")
        lo, hi = args.seeds or known.covers[entry]
        plan += [(entry, preset, range(lo, hi)) for preset in presets]
    total = sum(seeds.stop - seeds.start for _, _, seeds in plan)
    if total > MAX_RUNS:
        raise ToolError(f"the sweep would make {total} runs, more than the limit of {MAX_RUNS}; pass a smaller --seeds range")
    if args.update_known:
        _merged_covers(known.covers, plan)  # refuse before the sweep what the write would refuse
    return plan


def _stdout():
    out = sys.stdout
    if out is None:  # descriptor 1 was closed before the interpreter started
        raise ToolError("cannot write the report: standard output is closed")
    if getattr(out, "closed", False):  # the file object was closed since
        raise ToolError("cannot write the report: standard output is closed")
    return out


def emit(lines: list[str]) -> None:
    """Write the lines to stdout and flush; a closed stdout or a reader that went away is a tool error."""
    out = _stdout()
    try:
        for line in lines:
            print(line, file=out)
        out.flush()
    except (OSError, ValueError) as exc:  # ValueError: a file object that is already closed
        # Whatever is still buffered would fail again when the interpreter exits (exit 120).
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, out.fileno())
            finally:
                os.close(fd)
        except (AttributeError, OSError, ValueError):
            pass
        raise ToolError(f"cannot write the report to standard output: {exc}") from exc


def run_sweep(args: argparse.Namespace) -> int:
    known = load_known(args.known)
    plan = make_plan(args, known)
    runs = [(entry, preset, seed) for entry, preset, seeds in plan for seed in seeds]
    results = sweep(runs, args.jobs)
    lines, new, gone = report(plan, results, known)
    if args.update_known:
        emit(lines)  # the known file is touched only after the report is out
        update_known_file(args.known, plan, results)
        try:
            emit([f"result: {len(new)} new, {len(gone)} gone; the known file was rewritten with this run's failures"])
        except ToolError as exc:
            raise ToolError(f"{exc}; the known file was already rewritten with this run's failures") from exc
        return 0
    if new or gone:
        emit(
            lines
            + [
                f"result: MISMATCH, {len(new)} new, {len(gone)} gone; fix the layout, or after review"
                " rerun with --update-known to record this run's failures"
            ]
        )
        return 1
    emit(lines + [f"result: OK, {sum(1 for r in results.values() for _ in r)} known failures, 0 new, 0 gone"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
