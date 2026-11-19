"""The node definition registry: every known type with the best definition available.

Sources, most trusted first, and the tier each one gives a type:

* ``BUILTIN``: frontend-only nodes from ``src/cwc/data/frontend_builtins.json``.
* ``TRUTH``: a truth snapshot entry merged onto the type's ``/object_info`` entry
  (:func:`cwc.nodedefs.truth.merge`); needs both.
* ``TEMPLATES``: a widget template (:mod:`cwc.nodedefs.templates`), with or without an
  ``/object_info`` entry.
* ``DERIVED``: the ``/object_info`` entry alone (:func:`cwc.nodedefs.derive.from_object_info`).

``/object_info`` comes from the bundled core data and, on top of it, from a user source
(``--nodedefs`` file or URL) whose entries replace bundled ones of the same type. A
default listed in a source's ``withheld`` counts as unknown: ``has_default`` is false.
Every source is checked once here; a bad one raises ``CwcError(NODEDEFS_MISSING)``.
"""

from __future__ import annotations

import difflib
import json
import urllib.error
import urllib.request
import zlib
from dataclasses import replace
from typing import Any, Iterator, Mapping

from cwc.ingest.limits import MAX_DEPTH, MAX_JSON_BYTES, exceeds_depth, reject_constant
from cwc.nodedefs import templates as templates_mod
from cwc.nodedefs import truth as truth_mod
from cwc.nodedefs.derive import from_object_info
from cwc.nodedefs.types import Behavior, InputSpec, NodeDef, OutputSpec, SlotSpec, Tier, WidgetKind, WidgetSpec
from cwc.report import Code, CwcError, ExitCode, Finding, Severity
from cwc.resources import read_bytes, read_json

CORE_FILE = "core_nodedefs.json"
TRUTH_FILE = "core_truth.json"
TEMPLATES_FILE = "templates.json"
BUILTINS_FILE = "frontend_builtins.json"
OBJECT_INFO_PATH = "/api/object_info"
URL_TIMEOUT = 30  # seconds
URL_SCHEMES = ("http://", "https://")

ObjectInfo = Mapping[str, Mapping[str, Any]]


def _missing(message: str) -> CwcError:
    return CwcError(ExitCode.NODEDEFS_MISSING, [Finding(Code.D1, Severity.ERROR, message)])


# --- reading sources ------------------------------------------------------------------


def _too_big(where: str, what: str) -> CwcError:
    return _missing(f"node definitions from {where}: {what} past the limit of {MAX_JSON_BYTES} bytes")


def _gunzip(data: bytes, where: str) -> bytes:
    """The members of a gzip file, inflated within ``MAX_JSON_BYTES`` in all.

    Raises ``ValueError`` for a broken or cut-short stream and ``CwcError`` D1 when the
    result would pass the limit, so a small bomb never gets to allocate its output.
    """
    out: list[bytes] = []
    size = 0
    while data:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            piece = inflater.decompress(data, MAX_JSON_BYTES + 1 - size)
        except zlib.error as exc:
            raise ValueError(f"gzip data is broken: {exc}") from None
        size += len(piece)
        if size > MAX_JSON_BYTES:
            raise _too_big(where, "the gzip data inflates")
        if not inflater.eof:
            raise ValueError("gzip data ends early")
        out.append(piece)
        data = inflater.unused_data.lstrip(b"\0")  # padding between or after members
        if data and data[:2] != b"\x1f\x8b":
            raise ValueError("gzip data is followed by bytes that are not a gzip member")
    return b"".join(out)


def _parse_json(data: bytes, where: str) -> Any:
    """Parse a source. ``NaN`` and ``Infinity`` are refused as ``JSON.parse`` refuses them: the
    frontend that wrote or reads the document would reject it as a whole, and no output
    can carry them. Gzip is inflated within ``MAX_JSON_BYTES``. A document nested deeper than
    ``MAX_DEPTH`` is D1 and never reaches the parser, whose C scanner can overrun the stack."""
    try:
        if data[:2] == b"\x1f\x8b":
            data = _gunzip(data, where)
        text = data.decode("utf-8")
        if exceeds_depth(text):
            raise _missing(f"node definitions from {where} nest deeper than the limit of {MAX_DEPTH} levels")
        return json.loads(text, parse_constant=reject_constant)
    except (OSError, EOFError, UnicodeDecodeError, ValueError) as exc:
        raise _missing(f"node definitions from {where} are not valid JSON: {exc}") from None


def read_source(source: str) -> Any:
    """The parsed ``/object_info`` document at ``source``: a file (JSON, may be gzip) or the
    base URL of a running ComfyUI server, read from ``<url>/api/object_info``. A document, or
    its gzip data once inflated, past ``MAX_JSON_BYTES`` is D1 (a real export of every type is
    a few megabytes compressed, tens uncompressed)."""
    if source.startswith(URL_SCHEMES):
        url = source.rstrip("/") + OBJECT_INFO_PATH
        try:
            with urllib.request.urlopen(url, timeout=URL_TIMEOUT) as response:
                data = response.read(MAX_JSON_BYTES + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise _missing(f"cannot read node definitions from {url}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise _missing(f"cannot read node definitions from {url}: {exc}") from None
        if len(data) > MAX_JSON_BYTES:
            raise _too_big(url, "the response runs")
        return _parse_json(data, url)
    try:
        with open(source, "rb") as handle:
            data = handle.read(MAX_JSON_BYTES + 1)
    except OSError as exc:
        raise _missing(f"cannot read node definitions file {source}: {exc.strerror or exc}") from None
    if len(data) > MAX_JSON_BYTES:
        raise _too_big(source, "the file runs")
    return _parse_json(data, source)


def _split(doc: Any, where: str) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """``(entries by type, withheld input names by type)`` from a raw ``/object_info``
    document or a built one (``{"format": 1, "nodes": ..., "withheld": ...}``)."""
    if not isinstance(doc, dict):
        raise _missing(f"node definitions from {where}: expected an object")
    built = doc.get("format") == 1 and isinstance(doc.get("nodes"), dict)
    nodes = doc["nodes"] if built else doc
    bad = sorted(name for name, entry in nodes.items() if not isinstance(entry, dict))
    if bad:
        raise _missing(f"node definitions from {where}: entry {bad[0]!r} is not an object")
    held: dict[str, set[str]] = {}
    for pair in doc.get("withheld", ()) if built else ():
        if isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) for p in pair):
            held.setdefault(pair[0], set()).add(pair[1])
        else:
            raise _missing(f"node definitions from {where}: withheld holds {pair!r}")
    return dict(nodes), held


# --- building definitions -------------------------------------------------------------


def _withhold_input(spec: InputSpec, names: set[str]) -> InputSpec:
    dynamic = tuple((key, tuple(_withhold_input(c, names) for c in children)) for key, children in spec.dynamic_options)
    if spec.name in names:
        spec = replace(spec, has_default=False, default=None)
    return replace(spec, dynamic_options=dynamic)


def _withhold(nodedef: NodeDef, names: set[str]) -> NodeDef:
    """Treat the defaults of the ``names`` inputs as unknown."""
    inputs = tuple(_withhold_input(spec, names) for spec in nodedef.inputs)
    widgets = tuple(
        replace(w, default=None) if w.kind is WidgetKind.VALUE and w.input_name in names else w for w in nodedef.widgets
    )
    return replace(nodedef, inputs=inputs, widgets=widgets)


def _derive(type_name: str, entry: Mapping[str, Any], withheld: set[str], where: str) -> NodeDef:
    try:
        nodedef = from_object_info(type_name, entry)
    except ValueError as exc:
        raise _missing(f"node definitions from {where}: {exc}") from None
    except (TypeError, LookupError, AttributeError, ArithmeticError, RecursionError) as exc:
        # derive reads user-supplied entries; a shape it did not anticipate is still a bad entry.
        raise _missing(f"node definitions from {where}: object_info {type_name!r}: malformed entry ({type(exc).__name__}: {exc})") from None
    return _withhold(nodedef, withheld) if withheld else nodedef


def _builtin(type_name: str, raw: Mapping[str, Any]) -> NodeDef:
    """A frontend-only node: its slots and widgets are fixed by the frontend extension."""
    slots = tuple(SlotSpec(str(s["name"]), str(s["type"])) for s in raw["inputs"])
    widgets = tuple(
        WidgetSpec(str(w["name"]), WidgetKind.EXTRA, None, w.get("default"), bool(w.get("to_prompt", True)))
        for w in raw["widgets"]
    )
    return NodeDef(
        type=type_name,
        tier=Tier.BUILTIN,
        inputs=tuple(InputSpec(s.name, s.type, True, False) for s in slots),
        outputs=tuple(OutputSpec(str(o["name"]), str(o["type"])) for o in raw["outputs"]),
        widgets=widgets,
        display_name=str(raw.get("title") or type_name),
        category=str(raw.get("category") or ""),
        virtual=True,
        layout_complete=bool(raw["widgets_known"]),
        slots=slots,
        behavior=Behavior(raw["behavior"]),
    )


def _checked(parse: Any, doc: Any, where: str) -> Any:
    try:
        return parse(doc)
    except ValueError as exc:
        raise _missing(f"{where}: {exc}") from None


# --- the registry ---------------------------------------------------------------------


def _layout_key(nodedef: NodeDef) -> tuple[Any, ...]:
    """What must match for a truth snapshot of one ComfyUI version to fit another's entry."""
    return (
        tuple((spec.name, spec.type, spec.required, spec.widget) for spec in nodedef.inputs),
        tuple((w.name, w.kind) for w in nodedef.widgets),
        tuple((out.name, out.type) for out in nodedef.outputs),
    )


def _same_layout(
    derived: NodeDef, core: Mapping[str, tuple[Mapping[str, Any], set[str]]], name: str, where: str
) -> bool:
    """True when a user-supplied entry has the same ports and widgets as the bundled core entry.

    The bundled truth was captured against the bundled core version; a user's machine may
    run another version whose node changed, and then only the user's own entry is trusted.
    """
    if name not in core:
        return False
    entry, held = core[name]
    return _layout_key(derived) == _layout_key(_derive(name, entry, held, where))


class NodeDefs:
    """Read-only lookup from node type to :class:`NodeDef`."""

    def __init__(self, defs: Mapping[str, NodeDef], versioned: Mapping[tuple[str, str], NodeDef] | None = None):
        self._defs = {name: defs[name] for name in sorted(defs)}
        # (type, package version) -> the definition from that version's template.
        self._versioned = dict(versioned or {})

    @classmethod
    def load(
        cls,
        object_info: Any = None,
        truth: Any = None,
        templates: Any = None,
        include_core: bool = True,
    ) -> NodeDefs:
        """Build the registry.

        ``object_info`` is a parsed ``/object_info`` document (raw or built, see
        :func:`read_source`); its entries replace bundled core entries of the same type.
        ``truth`` and ``templates`` are parsed documents that replace the bundled snapshot
        and template table; None loads the bundled ones. ``include_core=False`` leaves the
        bundled core ``/object_info`` out.
        """
        entries: dict[str, tuple[Mapping[str, Any], set[str], str]] = {}
        core: dict[str, tuple[Mapping[str, Any], set[str]]] = {}
        if include_core:
            nodes, held = _split(_parse_json(read_bytes(CORE_FILE), CORE_FILE), CORE_FILE)
            core = {name: (entry, held.get(name, set())) for name, entry in nodes.items()}
            entries.update((name, (entry, held.get(name, set()), CORE_FILE)) for name, entry in nodes.items())
        if object_info is not None:
            nodes, held = _split(object_info, "--nodedefs")
            entries.update((name, (entry, held.get(name, set()), "--nodedefs")) for name, entry in nodes.items())
        bundled_truth = truth is None
        truth_nodes = _checked(truth_mod.parse_truth, read_json(TRUTH_FILE) if bundled_truth else truth, "truth")
        chosen = _checked(
            templates_mod.parse_templates, read_json(TEMPLATES_FILE) if templates is None else templates, "templates"
        )

        defs: dict[str, NodeDef] = {}
        versioned: dict[tuple[str, str], NodeDef] = {}

        def from_templates(name: str, derived: NodeDef | None) -> NodeDef:
            options = chosen[name]
            for ver, template in templates_mod.by_version(options).items():
                versioned[(name, ver)] = templates_mod.apply(template, derived)
            return templates_mod.apply(options[0], derived)

        for name, (entry, held, where) in entries.items():
            derived = _derive(name, entry, held, where)
            truth_applies = name in truth_nodes and (
                not bundled_truth or where == CORE_FILE or _same_layout(derived, core, name, where)
            )
            if truth_applies:
                defs[name] = truth_mod.merge(derived, truth_nodes[name])
            elif name in chosen:
                defs[name] = from_templates(name, derived)
            else:
                defs[name] = derived
        for name in chosen:
            if name not in defs:
                defs[name] = from_templates(name, None)
        for name, raw in read_json(BUILTINS_FILE)["nodes"].items():
            defs[name] = _builtin(name, raw)
        return cls(defs, {key: nd for key, nd in versioned.items() if defs[key[0]].tier is Tier.TEMPLATES})

    def get(self, type: str, ver: str | None = None) -> NodeDef | None:
        """The definition of ``type``. ``ver`` is a canvas node's ``properties.ver``: when a
        template was recorded for that package version, its definition is returned;
        otherwise (and without ``ver``) the type's default one."""
        if ver is not None:
            found = self._versioned.get((type, ver))
            if found is not None:
                return found
        return self._defs.get(type)

    def tier_of(self, type: str) -> Tier | None:
        nodedef = self._defs.get(type)
        return nodedef.tier if nodedef is not None else None

    def __contains__(self, type: object) -> bool:
        return type in self._defs

    def __iter__(self) -> Iterator[str]:
        return iter(self._defs)

    def __len__(self) -> int:
        return len(self._defs)

    def closest(self, type: str, n: int = 3) -> list[str]:
        """The ``n`` known types most similar to ``type`` (case-insensitive ``difflib`` ratio)."""
        by_lower: dict[str, str] = {}
        for name in self._defs:
            by_lower.setdefault(name.lower(), name)
        return [by_lower[m] for m in difflib.get_close_matches(type.lower(), sorted(by_lower), n=n, cutoff=0.0)]

    def search(self, keyword: str) -> list[NodeDef]:
        """Types whose name, display name or category contains ``keyword`` (any case), by name."""
        needle = keyword.casefold()
        return [
            nd
            for nd in self._defs.values()
            if any(needle in field.casefold() for field in (nd.type, nd.display_name, nd.category))
        ]
