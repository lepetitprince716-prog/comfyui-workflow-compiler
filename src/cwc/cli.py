"""Command line: ``cwc <command> ...``. Exit codes are :class:`cwc.report.ExitCode`."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence, TextIO

from cwc import TOOL_NAME, __version__
from cwc.emit.canonical import json_bytes, write_atomic
from cwc.ingest import load_authoring, read_file
from cwc.nodedefs.derive import slot_layout
from cwc.nodedefs.learn import command as learn_command
from cwc.nodedefs.registry import NodeDefs, read_source
from cwc.nodedefs.types import NodeDef
from cwc.layout.presets import load_presets
from cwc.pipeline import AUTO, Compiled, compile_document, preset_guide
from cwc.render.command import command as render_command
from cwc.report import Code, CwcError, ExitCode, Mode, Report, Severity, sort_findings
from cwc.resources import read_bytes

CLOSEST = 3
GUIDE_FILE = "guide.md"  # the one authoring reference; README and the skill point at `cwc guide`


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="ComfyUI workflow compiler. New here? `cwc guide` explains the input format, "
        "every authoring key and its effect, with an example.",
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    node = commands.add_parser("node", help="show or search node definitions")
    node.add_argument("type", nargs="?", metavar="TYPE", help="node type to show")
    node.add_argument("--search", metavar="KW", help="list types whose name, display name or category contains KW")
    node.add_argument("--nodedefs", metavar="FILE|URL", help="/object_info export or ComfyUI server URL")
    node.add_argument("--json", action="store_true", help="print JSON")
    node.set_defaults(run=_node)

    build = commands.add_parser("compile", help="compile an authoring graph into a canvas and its API prompts")
    build.add_argument("graph", metavar="GRAPH.json", help="authoring file (API prompt with cwc relaxations)")
    build.add_argument(
        "-o",
        "--out",
        required=True,
        metavar="DIR",
        help="output folder; a run removes the outputs of an earlier run of the same graph name that it does "
        "not write itself (a failed run leaves only its report), so an old canvas is never taken for a new one, "
        "and names on stderr what it removed",
    )
    build.add_argument("--nodedefs", metavar="FILE|URL", help="/object_info export or ComfyUI server URL")
    build.add_argument(
        "--preset",
        metavar="NAME",
        help="layout preset, or auto to choose one by the graph's size and shape (default: the graph's _workflow.layout.preset, else bento); `cwc presets` describes each",
    )
    build.add_argument("--all-enabled", action="store_true", help="also write <name>.full.api.json")
    build.add_argument("--allow-unknown", action="store_true", help="accept node types with no definition")
    build.add_argument(
        "--portable",
        action="store_true",
        help="suppress file-picker dropdown warnings when compiling portable workflows against a live server's /object_info",
    )
    build.add_argument("--json", action="store_true", help="print a JSON summary")
    build.set_defaults(run=_compile)

    guide = commands.add_parser("guide", help="print the authoring guide: input format, _meta and _workflow keys, an example")
    guide.set_defaults(run=_guide)

    presets = commands.add_parser("presets", help="list the layout presets, what each looks like and when to pick it")
    presets.add_argument("--json", action="store_true", help="print JSON")
    presets.set_defaults(run=_presets)

    learn = commands.add_parser("learn", help="learn widget templates from canvases paired with their API prompts")
    learn.add_argument("files", nargs="+", metavar="FILE", help="PNG with workflow and prompt chunks, or x_ui.json / x.ui.json beside x_api.json / x.api.json")
    learn.add_argument("-o", "--output", required=True, metavar="FILE", help="templates file to write")
    learn.add_argument("--nodedefs", metavar="FILE|URL", help="/object_info export or ComfyUI server URL")
    learn.add_argument("--json", action="store_true", help="print the summary as JSON")
    learn.set_defaults(run=learn_command)

    render = commands.add_parser("render", help="draw a canvas as an SVG, without a browser")
    render.add_argument("graph", metavar="GRAPH.ui.json", help="canvas workflow (UI JSON, or a PNG carrying one)")
    render.add_argument("-o", "--output", required=True, metavar="OUT.svg", help="SVG file to write")
    render.add_argument("--nodedefs", metavar="FILE|URL", help="/object_info export or ComfyUI server URL")
    render.add_argument("--values", action="store_true", help="draw concise literal widget values inside nodes")
    render.add_argument("--json", action="store_true", help="print the summary as JSON")
    render.set_defaults(run=render_command)
    return parser


# --- cwc node -------------------------------------------------------------------------


def _value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


OPTIONS_SHOWN = 20  # dropdown values printed per input in text; --json lists them all


def _dropdown(nd: NodeDef, widget: str | None) -> tuple[Any, ...] | None:
    """The values of the dropdown widget called ``widget``, or None when it is no dropdown."""
    spec = nd.input(widget) if widget is not None else None
    return spec.combo_values if spec is not None and spec.widget else None


def _options_field(nd: NodeDef, widget: str | None) -> dict[str, Any]:
    values = _dropdown(nd, widget)
    return {} if values is None else {"options": list(values)}


def _options_line(name: str, values: tuple[Any, ...]) -> str:
    if not values:
        return f"  {name} options: none in these definitions; --nodedefs reads a server's list"
    shown = ", ".join(_value(v) for v in values[:OPTIONS_SHOWN])
    more = f" ({OPTIONS_SHOWN} of {len(values)}; --json lists all)" if len(values) > OPTIONS_SHOWN else ""
    return f"  {name} options: {shown}{more}"


def _node_json(nd: NodeDef) -> dict[str, Any]:
    return {
        "type": nd.type,
        "tier": nd.tier.value,
        "display_name": nd.display_name,
        "category": nd.category,
        "python_module": nd.python_module,
        "output_node": nd.output_node,
        "virtual": nd.virtual,
        "layout_complete": nd.layout_complete,
        "names_complete": nd.names_complete,
        "inputs": [
            {"name": s.name, "type": s.type, "widget": s.widget, "optional": s.optional, **_options_field(nd, s.widget)}
            for s in slot_layout(nd)
        ],
        "widgets": [
            {
                "name": w.name,
                "kind": w.kind.value,
                "input": w.input_name,
                "default": w.default,
                "to_prompt": w.to_prompt,
                **_options_field(nd, w.name),
            }
            for w in nd.widgets
        ],
        "outputs": [{"name": o.name, "type": o.type, "is_list": o.is_list} for o in nd.outputs],
    }


def _node_text(nd: NodeDef) -> list[str]:
    sockets = [s for s in slot_layout(nd) if s.widget is None]
    lines = [
        f"{nd.type}  ({nd.display_name or nd.type})  tier: {nd.tier.value}  category: {nd.category or '-'}",
        "inputs:  " + (", ".join(f"{s.name}{'?' if s.optional else ''}: {s.type}" for s in sockets) or "-"),
        "widgets: " + (", ".join(f"{w.name or '<unnamed>'}={_value(w.default)}" for w in nd.widgets) or "-"),
    ]
    for w in nd.widgets:
        values = _dropdown(nd, w.name)
        if w.name is not None and values is not None:
            lines.append(_options_line(w.name, values))
    lines.append("outputs: " + (", ".join(f"{o.name}: {o.type}{'[]' if o.is_list else ''}" for o in nd.outputs) or "-"))
    if not nd.layout_complete:
        lines.append("layout:  incomplete (compile needs a truth or template entry for this type)")
    elif not nd.names_complete:
        lines.append("layout:  some widget names unknown (widgets_values_named is not written)")
    return lines


def _search_line(nd: NodeDef) -> str:
    return "\t".join((nd.type, nd.display_name or "-", nd.category or "-", nd.tier.value))


def _node(args: argparse.Namespace, out: TextIO, err: TextIO) -> ExitCode:
    if (args.type is None) == (args.search is None):
        err.write(f"{TOOL_NAME} node: give either TYPE or --search KW\n")
        return ExitCode.USAGE
    defs = NodeDefs.load(object_info=read_source(args.nodedefs) if args.nodedefs else None)

    if args.search is not None:
        found = defs.search(args.search)
        if args.json:
            rows = [{"type": n.type, "display_name": n.display_name, "category": n.category, "tier": n.tier.value} for n in found]
            out.write(json_bytes(rows).decode("utf-8"))
        else:
            out.writelines(_search_line(n) + "\n" for n in found)
        if not found:  # like grep: an empty answer is a failed check, not an error
            err.write(f"no node type matches {args.search!r}\n")
            return ExitCode.CHECK_FAILED
        return ExitCode.OK

    nd = defs.get(args.type)
    if nd is None:  # the definitions loaded; the name given is wrong
        closest = defs.closest(args.type, CLOSEST)
        if args.json:
            out.write(json_bytes({"type": args.type, "error": "unknown node type", "closest": closest}).decode("utf-8"))
        err.write(f"unknown node type {args.type!r}; closest: {', '.join(closest) or '-'}\n")
        return ExitCode.INPUT_ERROR
    if args.json:
        out.write(json_bytes(_node_json(nd)).decode("utf-8"))
    else:
        out.writelines(line + "\n" for line in _node_text(nd))
    return ExitCode.OK


# --- cwc guide ------------------------------------------------------------------------


def _guide(args: argparse.Namespace, out: TextIO, err: TextIO) -> ExitCode:
    out.write(read_bytes(GUIDE_FILE).decode("utf-8"))
    return ExitCode.OK


# --- cwc presets ----------------------------------------------------------------------


def _presets(args: argparse.Namespace, out: TextIO, err: TextIO) -> ExitCode:
    guide = preset_guide()
    rows = [{"name": name, "description": guide[name][0], "use_when": guide[name][1]} for name in [*load_presets(), AUTO]]
    if args.json:
        out.write(json_bytes(rows).decode("utf-8"))
    else:
        width = max(len(row["name"]) for row in rows)
        for row in rows:
            out.write(f"{row['name']:<{width}}  {row['description']}\n{'':<{width}}  use when: {row['use_when']}\n")
    return ExitCode.OK


# --- cwc compile ----------------------------------------------------------------------

# Artefact kind -> file name suffix after <name>. All of them are written or removed on
# every run, so the folder never mixes outputs of two runs.
ARTEFACTS = {"ui": ".ui.json", "api": ".api.json", "full": ".full.api.json", "report": ".report.json"}


def _remove_artefacts(paths: Iterable[Path]) -> list[tuple[Path, str]]:
    """Best effort: delete what exists. Returns each path that could not be removed, with the reason."""
    left: list[tuple[Path, str]] = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            left.append((path, exc.strerror or str(exc)))
    return left


def _compile(args: argparse.Namespace, out: TextIO, err: TextIO) -> ExitCode:
    source = Path(args.graph)
    paths = {kind: Path(args.out) / f"{source.stem}{suffix}" for kind, suffix in ARTEFACTS.items()}
    files: dict[str, bytes] = {}
    compiled: Compiled | None = None
    try:
        doc = load_authoring(read_file(source, "authoring file"))
        defs = NodeDefs.load(object_info=read_source(args.nodedefs) if args.nodedefs else None)
        compiled = compile_document(
            doc, defs, allow_unknown=args.allow_unknown, preset=args.preset, portable=args.portable
        )
        report = compiled.report
        exit_code = ExitCode.OK if report.ok else ExitCode.CHECK_FAILED
        if report.ok:
            files["ui"] = json_bytes(compiled.canvas.to_json())
            files["api"] = json_bytes(compiled.authoring.factory)
            if args.all_enabled:
                files["full"] = json_bytes(compiled.authoring.full)
    except CwcError as exc:
        report, exit_code = Report(Mode.COMPILE, findings=list(exc.findings)), exc.exit_code
    files["report"] = json_bytes(report.to_json())

    removed: list[Path] = []  # outputs of an earlier run that this run does not replace
    try:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        for kind, path in paths.items():
            if kind in files:
                write_atomic(path, files[kind])
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                removed.append(path)
    except OSError as exc:
        leftover = _remove_artefacts(paths.values())  # never leave a canvas and a report of different runs
        message = f"cannot write to {args.out}: {exc.strerror or exc}"
        err.write(f"{TOOL_NAME} compile: {message}\n")
        for path, reason in leftover:
            err.write(f"{TOOL_NAME} compile: could not remove {path}: {reason}; it is left over from an earlier run\n")
        if args.json:
            failure = _summary(False, ExitCode.WRITE_FAILED, {}, report)
            failure.update({"error": message, "leftover": [str(path) for path, _ in leftover]})
            out.write(json_bytes(failure).decode("utf-8"))
        return ExitCode.WRITE_FAILED

    written = {kind: str(paths[kind]) for kind in ARTEFACTS if kind in files}
    counts = Counter(f.severity.value for f in report.findings)
    if args.json:
        out.write(json_bytes(_summary(report.ok, exit_code, written, report)).decode("utf-8"))
    else:
        out.writelines(f"wrote {path}\n" for path in written.values())
    _print_findings(report, err)
    if removed:
        names = ", ".join(str(path) for path in removed)
        err.write(f"{TOOL_NAME} compile: removed {names} from an earlier run, which this run does not replace\n")
    if not report.ok:
        err.write(f"{TOOL_NAME} compile: {counts['error']} error(s); see {written['report']}\n")
    elif compiled is not None:
        err.write(_success_line(compiled, written["ui"], counts["warning"]) + "\n")
    return exit_code


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _success_line(compiled: Compiled, ui_path: str, warnings: int) -> str:
    metrics = compiled.report.metrics
    auto = metrics.get("preset_auto")
    preset = metrics["preset"] + (f" ({AUTO}: {auto['rule']})" if auto else "")
    return (
        f"{TOOL_NAME} compile: ok; preset {preset}; {_plural(len(compiled.canvas.groups), 'group')}, "
        f"{_plural(compiled.counts.optional_modules, 'optional module')}, {_plural(warnings, 'warning')}; "
        f"preview: {TOOL_NAME} render {ui_path} -o {Path(ui_path).name.removesuffix('.ui.json')}.svg"
    )


# Authoring checks (E1 to E8) speak about what the author wrote, so they reach the terminal at
# every severity. Infos from the canvas checks stay in the report.
AUTHORING_CODES = frozenset(code for code in Code if code.value.startswith("E"))


def _print_findings(report: Report, err: TextIO) -> None:
    """Every error and warning on stderr, with its hint, and the authoring infos: a caller that
    reads only the terminal must not take a run with an ignored key for a clean one."""
    for finding in sort_findings(report.findings):
        if finding.severity is Severity.INFO and finding.code not in AUTHORING_CODES:
            continue
        label = finding.code.value if finding.severity is Severity.ERROR else f"{finding.code.value} {finding.severity.value}"
        err.write(f"{label}: {finding.message}\n")
        if finding.hint:
            err.write(f"  hint: {finding.hint}\n")


def _summary(ok: bool, exit_code: ExitCode, outputs: dict[str, str], report: Report) -> dict[str, Any]:
    counts = Counter(f.severity.value for f in report.findings)
    return {
        "ok": ok,
        "exit_code": int(exit_code),
        "outputs": outputs,
        "findings": {severity.value: counts[severity.value] for severity in Severity},
        # the report's own list, so a program needs no second read for what went wrong
        "finding_list": [f.to_json() for f in sort_findings(report.findings)],
    }


# --- entry point ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    stdout = _ClosedPipeSafe(out or sys.stdout)
    stderr = _ClosedPipeSafe(err or sys.stderr)
    try:
        # argparse prints help, version and usage errors to sys.stdout and sys.stderr itself.
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            args = _parser().parse_args(argv)
    except SystemExit as exc:  # --help, --version, a usage error
        code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    else:
        code = _run(args, stdout, stderr)
    stdout.flush()  # a closed pipe shows up here, not only at exit
    stderr.flush()
    return code


class _ClosedPipeSafe(io.TextIOBase):
    """Standard output or error for a command whose reader may go away (``2>&1 | head``).

    A closed pipe loses the rest of the output but does not change the result: the command
    runs to its end and returns its own exit code. Once the reader is gone, the stream is
    pointed at the null device, as the Python documentation recommends, so the flush at
    interpreter exit does not fail again (which would turn any exit code into 120).
    """

    def __init__(self, stream: TextIO):
        self._stream = stream
        self._gone = False

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not self._gone:
            try:
                return self._stream.write(text)
            except BrokenPipeError:
                self._lost()
        return len(text)

    def flush(self) -> None:
        if not self._gone:
            try:
                self._stream.flush()
            except BrokenPipeError:
                self._lost()

    def _lost(self) -> None:
        self._gone = True
        _silence(self._stream)


def _run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    try:
        return int(args.run(args, out, err))
    except CwcError as exc:
        for finding in exc.findings:
            err.write(f"{TOOL_NAME}: {finding.message}\n")
        return int(exc.exit_code)
    except Exception as exc:  # last resort: a bug must not look like a failed check (exit 1)
        message = f"{type(exc).__name__}: {' '.join(str(exc).split())}"
        err.write(f"{TOOL_NAME}: internal error: {message}\n")
        if getattr(args, "json", False):
            out.write(json_bytes({"ok": False, "exit_code": int(ExitCode.INTERNAL), "error": f"internal error: {message}"}).decode("utf-8"))
        return int(ExitCode.INTERNAL)


def _silence(out: TextIO) -> None:
    """Point ``out`` at the null device, so that the flush at exit does not fail again.

    This is what the Python documentation recommends for a closed standard output.
    """
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, out.fileno())
        finally:
            os.close(null)
    except (OSError, ValueError):  # a stream without a file descriptor has nothing to redirect
        pass
