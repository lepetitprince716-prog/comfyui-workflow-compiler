"""Learning widget templates from canvases paired with the API prompt of the same run.

A sample is a canvas and the prompt it was queued as; node ids are shared. For every root
node the saved ``widgets_values`` are aligned, position by position and in order, with the
node's prompt inputs:

* A position whose value equals a prompt input's value (JS-number equality, ``__value__``
  unwrapped) and fits the declared input type takes that input's name. A widget input fed
  by a link matches any value that fits its type.
* A position no prompt input explains takes the name of a widget the definition keeps out
  of the prompt (value control, filter, upload button, companions; from
  :func:`cwc.nodedefs.derive.widget_layout`) when the value is plausible for it.
* Anything else is ``None``: a slot whose name is unknown.

The alignment keeps both sequences in order and maximises the evidence (an equal value
counts twice, a plausible pseudo widget or linked widget once), so a value repeated in
another input cannot pull a name out of place.

Instances are grouped by ``(type, properties.ver)``. A group's template has the most common
``widgets_values`` length; each position takes the name most aligned instances gave it
(unknown names do not vote). Instances of another length, and aligned instances that
disagree with a chosen name, are counted as conflicts in ``evidence``. Nodes absent from
the prompt (muted, bypassed) and nodes too large to align (:data:`MAX_ALIGN_CELLS`) vote on
the length only. A position no instance named takes
the derived name when the derived layout suits every instance of the chosen length.

Templates are written only for types whose definition without templates gets the length
wrong for some instance, has incomplete names, or is unknown. Types the registry takes
from truth or the frontend builtins keep those (truth first), and DynamicCombo types
replay their layout from ``/object_info``, so neither gets a template. Nodes of subgraph
types are skipped.

Entries of one type are ordered newest first, which makes the first one the type's
default for nodes without a version: dotted numeric versions by value, then other
versions (commit hashes) by instance count, then the entry without a version.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence, TextIO

from cwc.canvas.model import Canvas
from cwc.emit.canonical import json_bytes, write_atomic
from cwc.ingest.canvas import PROMPT_KEY, WORKFLOW_KEY, load_canvas
from cwc.ingest.limits import parse_json, read_file
from cwc.ingest.png import SIGNATURE, read_png_text
from cwc.nodedefs._frontend import frontend
from cwc.nodedefs.derive import widget_layout
from cwc.nodedefs.registry import NodeDefs, read_source
from cwc.nodedefs.templates import FORMAT
from cwc.nodedefs.types import InputSpec, NodeDef, Tier, WidgetKind, WidgetSpec
from cwc.prompt.model import is_link, unwrap_prompt_value, values_equal
from cwc.report import CwcError, ExitCode

__all__ = ["Sample", "Learner", "align", "pair_path", "read_sample", "iter_samples", "command"]

NO_TEMPLATES = {"format": FORMAT, "templates": []}
CANVAS_SUFFIXES = (("_ui.json", "_api.json"), (".ui.json", ".api.json"))
RATIO_DIGITS = 4
_DOTTED = re.compile(r"v?(\d+(?:\.\d+)*)")

# Alignment evidence: an equal value is worth more than a merely plausible one.
_EQUAL, _PLAUSIBLE = 2, 1

# The alignment tables have ``len(values) * len(candidates)`` cells and cost about 3
# microseconds each (500 x 500 takes about a second). Nodes of real workflows are far below
# this limit; a node above it is skipped, counted as ``too_large`` and votes on its
# length only.
MAX_ALIGN_CELLS = 250_000


@dataclass(frozen=True)
class Sample:
    """A canvas and the API prompt it was queued as (node id -> ``{"class_type", "inputs"}``)."""

    canvas: Canvas
    prompt: Mapping[str, Mapping[str, Any]]


# --- aligning one node ----------------------------------------------------------------


@dataclass(frozen=True)
class _Token:
    """A widget the node's values may hold, in the order the frontend writes them."""

    name: str
    value: Any = None  # the prompt value (VALUE tokens)
    spec: InputSpec | None = None  # the declared input, when the definition has one
    linked: bool = False  # a widget input fed by a link: its prompt value is the link
    pseudo: WidgetSpec | None = None  # a widget kept out of the prompt


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__  # str, list, dict


def _fits(value: Any, spec: InputSpec | None) -> bool:
    """The value could be held by a widget of the declared input type."""
    if spec is None:
        return True
    kind = _kind(value)
    if spec.type in ("INT", "FLOAT"):
        return kind == "number"
    if spec.type == "BOOLEAN":
        return kind == "bool"
    if spec.type == "STRING":
        return kind == "str"
    if spec.type == "COMBO":
        return kind == "list" if spec.options.get("multi_select") else kind in ("str", "number", "bool")
    return True


def _plausible(value: Any, widget: WidgetSpec) -> bool:
    """The value could be held by a widget the frontend keeps out of the prompt."""
    if widget.kind is WidgetKind.CONTROL:
        return isinstance(value, str) and value in frontend().value_control.modes
    if widget.kind in (WidgetKind.FILTER, WidgetKind.UPLOAD):
        return isinstance(value, str)
    return widget.default is None or _kind(value) == _kind(widget.default)


def _gain(value: Any, token: _Token) -> int:
    if token.pseudo is not None:
        return _PLAUSIBLE if _plausible(value, token.pseudo) else 0
    if token.linked:
        return _PLAUSIBLE if _fits(value, token.spec) else 0
    return _EQUAL if values_equal(value, token.value) and _fits(value, token.spec) else 0


def _tokens(
    values: Sequence[Any], inputs: Mapping[str, Any], slots: Sequence[Any], base: NodeDef | None
) -> list[_Token]:
    """Prompt inputs in prompt order (link-only sockets left out), with the base layout's
    out-of-prompt widgets inserted after the prompt widget they follow there."""
    widget_slots = {s.get("name") for s in slots if isinstance(s, dict) and s.get("widget")}
    prompt: list[_Token] = []
    for name, raw in inputs.items():
        spec = base.input(name) if base is not None else None
        if is_link(raw):
            if name in widget_slots or (spec is not None and spec.widget):
                prompt.append(_Token(name, spec=spec, linked=True))
            continue
        prompt.append(_Token(name, unwrap_prompt_value(raw), spec))
    if base is None:
        return prompt
    position = {t.name: i for i, t in enumerate(prompt)}
    after: dict[int, list[_Token]] = {}
    anchor = -1
    for widget in widget_layout(base, values):
        if widget.to_prompt:
            anchor = max(anchor, position.get(widget.name or "", -1))
        elif widget.name is not None:
            after.setdefault(anchor, []).append(_Token(widget.name, pseudo=widget))
    tokens = list(after.get(-1, ()))
    for i, token in enumerate(prompt):
        tokens.append(token)
        tokens.extend(after.get(i, ()))
    return tokens


def align(
    values: Sequence[Any],
    inputs: Mapping[str, Any],
    slots: Sequence[Any] = (),
    base: NodeDef | None = None,
    max_cells: int | None = None,
) -> tuple[str | None, ...] | None:
    """The widget name of each position of ``values`` (``None`` when unknown).

    ``inputs`` is the node's prompt ``inputs`` in prompt order, ``slots`` the node's canvas
    ``inputs`` array (it tells linked widgets from link-only sockets), ``base`` the
    definition without templates, when one exists. The result is ``None`` when
    ``len(values)`` times the number of candidates exceeds ``max_cells`` (default
    :data:`MAX_ALIGN_CELLS`): the alignment
    is quadratic, and a node that large is not aligned.
    """
    tokens = _tokens(values, inputs, slots, base)
    n, m = len(values), len(tokens)
    if n * m > (MAX_ALIGN_CELLS if max_cells is None else max_cells):
        return None
    # best[i][j]: the most evidence aligning values[i:] with tokens[j:].
    best = [[0] * (m + 1) for _ in range(n + 1)]
    gain = [[_gain(values[i], tokens[j]) for j in range(m)] for i in range(n)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            match = gain[i][j] + best[i + 1][j + 1] if gain[i][j] else -1
            best[i][j] = max(match, best[i + 1][j], best[i][j + 1])
    names: list[str | None] = []
    i = j = 0
    while i < n:
        if j < m and gain[i][j] and best[i][j] == gain[i][j] + best[i + 1][j + 1]:
            names.append(tokens[j].name)
            i, j = i + 1, j + 1
        elif j < m and best[i][j] == best[i][j + 1]:
            j += 1
        else:
            names.append(None)
            i += 1
    return tuple(names)


# --- aggregating instances ------------------------------------------------------------


@dataclass
class _Group:
    """Every instance of one ``(type, ver)``."""

    base: tuple[str | None, ...] | None  # widget names of the definition without templates
    packs: Counter[str] = field(default_factory=Counter)  # package id by instance count
    lengths: Counter[int] = field(default_factory=Counter)
    names: dict[int, Counter[tuple[str | None, ...]]] = field(default_factory=dict)  # by length
    base_fits: Counter[int] = field(default_factory=Counter)  # by length: instances the base layout fits


def _chosen_names(
    length: int, aligned: Counter[tuple[str | None, ...]], fallback: tuple[str | None, ...] | None
) -> list[str | None]:
    """Per position the name most instances gave it (``fallback`` votes once where none
    did); a name kept at two positions stays at the one with more votes."""
    picked: list[tuple[str | None, int]] = []
    for pos in range(length):
        votes: Counter[str] = Counter()
        for names, count in aligned.items():
            if names[pos] is not None:
                votes[names[pos]] += count
        if not votes and fallback is not None and fallback[pos] is not None:
            votes[fallback[pos]] = 1
        name = min(votes, key=lambda nm: (-votes[nm], nm)) if votes else None
        picked.append((name, votes[name] if name is not None else 0))
    keep: dict[str, int] = {}
    for pos, (name, count) in enumerate(picked):
        if name is not None and (name not in keep or count > picked[keep[name]][1]):
            keep[name] = pos
    return [name if name is not None and keep[name] == pos else None for pos, (name, _) in enumerate(picked)]


def _entry(type_name: str, ver: str | None, group: _Group) -> dict[str, Any]:
    length = min(group.lengths, key=lambda n: (-group.lengths[n], n))
    aligned = group.names.get(length, Counter())
    # Where the definition without templates fits every instance of this length, its names
    # stand in for positions no prompt value explained.
    fits = group.base is not None and group.base_fits[length] == group.lengths[length]
    names = _chosen_names(length, aligned, group.base if fits else None)
    instances = sum(group.lengths.values())
    disagree = sum(
        count for got, count in aligned.items() if any(g is not None and g != c for g, c in zip(got, names))
    )
    conflicts = instances - group.lengths[length] + disagree
    return {
        "type": type_name,
        "pack": min(group.packs, key=lambda p: (-group.packs[p], p)) if group.packs else None,
        "ver": ver,
        "source": "learned",
        "widgets": names,
        "evidence": {
            "instances": instances,
            "aligned": sum(sum(c.values()) for c in group.names.values()),
            "lengths": {str(n): group.lengths[n] for n in sorted(group.lengths)},
            "conflicts": conflicts,
            "conflict_ratio": round(conflicts / instances, RATIO_DIGITS),
        },
    }


def _newest_first(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    ver, instances = entry["ver"], entry["evidence"]["instances"]
    if ver is None:
        return (2, -instances, "")
    dotted = _DOTTED.fullmatch(ver)
    if dotted:
        return (0, tuple(-int(p) for p in dotted.group(1).split(".")) + (1,), ver)
    return (1, -instances, ver)


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class Learner:
    """Collects samples and produces the learned templates document."""

    def __init__(self, defs: NodeDefs):
        self.defs = defs  # definitions without templates (truth, builtins, derived)
        self.counts: Counter[str] = Counter()
        self._groups: dict[tuple[str, str | None], _Group] = {}
        self._wrong: set[str] = set()  # types whose base layout length is wrong somewhere

    def add(self, sample: Sample) -> None:
        subgraphs = (sample.canvas.raw.get("definitions") or {}).get("subgraphs") or []
        subgraph_ids = {s.get("id") for s in subgraphs if isinstance(s, dict)}
        for node in sample.canvas.nodes:
            self.counts["nodes"] += 1
            values = node.widgets_values if node.widgets_values is not None else []
            base = self.defs.get(node.type)
            if node.type in subgraph_ids:
                self.counts["subgraph"] += 1
            elif not isinstance(values, list):
                self.counts["named_values"] += 1
            elif base is not None and base.tier in (Tier.TRUTH, Tier.BUILTIN):
                self.counts["fixed"] += 1
            elif base is not None and base.has_dynamic:
                self.counts["dynamic"] += 1
            else:
                self._add_node(node.type, node.properties, node.inputs, values, base, sample.prompt.get(node.key))

    def _add_node(
        self,
        type_name: str,
        properties: Mapping[str, Any],
        slots: Sequence[Any],
        values: list[Any],
        base: NodeDef | None,
        entry: Any,
    ) -> None:
        ver = _str_or_none(properties.get("ver"))
        pack = _str_or_none(properties.get("cnr_id")) or _str_or_none(properties.get("aux_id"))
        group = self._groups.get((type_name, ver))
        if group is None:
            known = base is not None and base.names_complete
            group = self._groups[(type_name, ver)] = _Group(tuple(w.name for w in base.widgets) if known else None)
        if pack is not None:
            group.packs[pack] += 1
        group.lengths[len(values)] += 1
        if base is not None and base.names_complete and len(base.widgets) == len(values):
            group.base_fits[len(values)] += 1
        else:
            self._wrong.add(type_name)
        if not isinstance(entry, dict) or entry.get("class_type") != type_name:
            self.counts["without_prompt"] += 1
            return
        names = align(values, entry["inputs"], slots, base)
        if names is None:
            self.counts["too_large"] += 1
            return
        self.counts["aligned"] += 1
        group.names.setdefault(len(values), Counter())[names] += 1

    def document(self) -> dict[str, Any]:
        """``{"format": 1, "templates": [...]}``; the same samples give the same document."""
        entries = [_entry(t, ver, g) for (t, ver), g in self._groups.items() if t in self._wrong]
        entries.sort(key=lambda e: (e["type"], _newest_first(e)))
        return {"format": FORMAT, "templates": entries}


# --- reading samples ------------------------------------------------------------------


def pair_path(path: Path) -> Path | None:
    """The API file named like the canvas file ``path``; None for other names."""
    for canvas, api in CANVAS_SUFFIXES:
        if path.name.endswith(canvas):
            return path.with_name(path.name[: -len(canvas)] + api)
    return None


def _is_api_name(path: Path) -> bool:
    return any(path.name.endswith(api) for _, api in CANVAS_SUFFIXES)


def _prompt(doc: Any) -> dict[str, Any] | None:
    """The document when it is an API prompt (every entry has a class_type and inputs)."""
    if not isinstance(doc, dict) or not doc:
        return None
    ok = all(
        isinstance(v, dict) and isinstance(v.get("class_type"), str) and isinstance(v.get("inputs"), dict)
        for v in doc.values()
    )
    return doc if ok else None


def read_sample(path: Path, skipped: Counter[str]) -> Sample | None:
    """The sample in one PNG or canvas JSON file; None (counted in ``skipped``) when there is none."""
    try:
        data = read_file(path, "sample file")
        if data.startswith(SIGNATURE):
            texts = read_png_text(data)
            if WORKFLOW_KEY not in texts or PROMPT_KEY not in texts:
                skipped["no_chunks"] += 1
                return None
            canvas = load_canvas(texts[WORKFLOW_KEY].encode("utf-8"))
            prompt = _prompt(parse_json(texts[PROMPT_KEY].encode("utf-8"), "PNG prompt chunk"))
        else:
            partner = pair_path(path)
            if partner is None or not partner.is_file():
                skipped["no_pair"] += 1
                return None
            canvas = load_canvas(data)
            prompt = _prompt(parse_json(read_file(partner, "API prompt file"), "API prompt JSON"))
    except (CwcError, OSError):
        skipped["invalid"] += 1
        return None
    if prompt is None:
        skipped["invalid"] += 1
        return None
    return Sample(canvas, prompt)


def iter_samples(paths: Iterable[str | Path], skipped: Counter[str]) -> Iterator[Sample]:
    """Samples from PNG files and canvas JSON files; every file that gives none is counted
    in ``skipped`` by reason. An API file whose canvas file is also listed is read with it."""
    listed = [Path(p) for p in paths]
    partners = {pair_path(p) for p in listed}
    for path in listed:
        if _is_api_name(path) and path in partners:
            continue  # read with its canvas file
        sample = read_sample(path, skipped)
        if sample is not None:
            yield sample


# --- cwc learn ------------------------------------------------------------------------


def command(args: argparse.Namespace, out: TextIO, err: TextIO) -> ExitCode:
    """``cwc learn FILE... -o templates.json [--nodedefs FILE|URL] [--json]``."""
    object_info = read_source(args.nodedefs) if args.nodedefs else None
    learner = Learner(NodeDefs.load(object_info=object_info, templates=NO_TEMPLATES))
    skipped: Counter[str] = Counter()
    samples = 0
    for sample in iter_samples(args.files, skipped):
        samples += 1
        learner.add(sample)
    doc = learner.document()
    summary = {
        "files": len(args.files),
        "samples": samples,
        "skipped": {k: skipped[k] for k in sorted(skipped) if skipped[k]},
        "nodes": {k: learner.counts[k] for k in sorted(learner.counts)},
        "types": len({e["type"] for e in doc["templates"]}),
        "templates": len(doc["templates"]),
    }
    if samples:  # a run that learned nothing leaves an existing output file alone
        try:
            write_atomic(args.output, json_bytes(doc))
        except OSError as exc:
            err.write(f"cwc learn: cannot write {args.output}: {exc.strerror or exc}\n")
            return ExitCode.WRITE_FAILED
    if args.json:
        out.write(json_bytes(summary).decode("utf-8"))
    else:
        out.write(_summary_text(summary))
    if samples == 0:
        err.write("cwc learn: no file gave a canvas paired with its API prompt\n")
        return ExitCode.INPUT_ERROR
    return ExitCode.OK


def _summary_text(summary: Mapping[str, Any]) -> str:
    lines = [f"samples: {summary['samples']} of {summary['files']} files"]
    if summary["skipped"]:
        lines.append("skipped: " + ", ".join(f"{k} {v}" for k, v in summary["skipped"].items()))
    lines.append("nodes: " + ", ".join(f"{k} {v}" for k, v in summary["nodes"].items()))
    lines.append(f"templates: {summary['templates']} for {summary['types']} types")
    return "\n".join(lines) + "\n"
