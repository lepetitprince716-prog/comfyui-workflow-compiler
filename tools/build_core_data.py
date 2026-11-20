"""Build the bundled core node data from a raw /object_info export and a raw truth snapshot.

    python3 tools/build_core_data.py OBJECT_INFO TRUTH --out src/cwc/data/

Writes ``core_nodedefs.json`` and ``core_truth.json`` (docs/ARCHITECTURE.md, "Data
files"). The tool keeps core node types only, removes explanatory text (descriptions,
tooltips, search aliases, help, price badges), and withholds every string default longer
than ``MAX_DEFAULT_CHARS``: the value becomes null and ``[type, name]`` is listed in the
file's top-level ``withheld``. Long defaults are prose or code from upstream sources whose
licence this repository does not take on.

Inputs may be plain JSON or gzip, raw or already built: a built file's ``withheld`` list is
carried over, so running the tool on its own output changes nothing. Output is plain ASCII
JSON, indent 1, one trailing newline (text, so the privacy scan and a diff can read it): the
same inputs give the same bytes. The truth snapshot is written with every key sorted. In the node definitions only the top level and the type
names are sorted: inside an entry, key order is data (the frontend creates inputs in
JavaScript object order, and DynamicCombo sub-inputs have no ``input_order``), so it stays
as the export wrote it.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import gzip
import io
import json
import os
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any, Iterable

FORMAT = 1
MAX_DEFAULT_CHARS = 64
# A raw /object_info export is tens of megabytes; an input, or a gzip input once inflated, past this is refused.
MAX_INPUT_BYTES = 256 * 1024 * 1024
CORE_MODULE = "nodes"
CORE_PREFIXES = ("comfy_extras.", "comfy_api_nodes.")
# Keys of a node entry that only explain the node to a person.
ENTRY_TEXT_KEYS = frozenset({"description", "output_tooltips", "search_aliases", "help", "price_badge"})
# Keys anywhere inside an entry's ``input`` that only explain an input or an option.
OPTION_TEXT_KEYS = frozenset({"tooltip", "description", "help"})
NODEDEFS_FILE = "core_nodedefs.json"
TRUTH_FILE = "core_truth.json"

Withheld = set[tuple[str, str]]


def is_core(entry: Any) -> bool:
    module = entry.get("python_module") if isinstance(entry, dict) else None
    return isinstance(module, str) and (module == CORE_MODULE or module.startswith(CORE_PREFIXES))


def _too_long(value: Any) -> bool:
    return isinstance(value, str) and len(value) > MAX_DEFAULT_CHARS


# --- object_info ----------------------------------------------------------------------


def _is_input_spec(value: Any) -> bool:
    """``[type, options]``: how /object_info declares one input, at any depth."""
    return (
        isinstance(value, list)
        and len(value) > 1
        and isinstance(value[0], (str, list))
        and isinstance(value[1], dict)
    )


def _clean(type_name: str, value: Any, name: str, withheld: Withheld) -> None:
    """Clean an ``input`` structure in place. ``name`` is the dotted name of the innermost
    input around ``value`` (DynamicCombo sub-inputs are named ``<combo>.<sub>``, as in
    ``cwc.nodedefs.derive``). Inputs are lists, so an input called like a text key stays."""
    if isinstance(value, dict):
        for key in list(value):
            item = value[key]
            if key in OPTION_TEXT_KEYS and not _is_input_spec(item):
                del value[key]
            elif key == "default" and _too_long(item):
                value[key] = None
                withheld.add((type_name, name))
            elif _is_input_spec(item):
                _clean(type_name, item[1], f"{name}.{key}" if name else key, withheld)
            else:
                _clean(type_name, item, name, withheld)
    elif isinstance(value, list):
        for item in value:
            _clean(type_name, item, name, withheld)


def clean_object_info(object_info: dict[str, Any]) -> tuple[dict[str, Any], Withheld]:
    """Core entries without explanatory text, long defaults nulled; and what was withheld."""
    nodes: dict[str, Any] = {}
    withheld: Withheld = set()
    for type_name, entry in object_info.items():
        if not is_core(entry):
            continue
        entry = {k: copy.deepcopy(v) for k, v in entry.items() if k not in ENTRY_TEXT_KEYS}
        _clean(type_name, entry.get("input"), "", withheld)
        nodes[type_name] = entry
    return nodes, withheld


# --- truth snapshot -------------------------------------------------------------------


def clean_truth(nodes: dict[str, Any], keep: Iterable[str]) -> tuple[dict[str, Any], Withheld]:
    """Truth entries of the ``keep`` types, long widget values nulled; and what was withheld."""
    kept = set(keep)
    out: dict[str, Any] = {}
    withheld: Withheld = set()
    for type_name, entry in nodes.items():
        if type_name not in kept:
            continue
        entry = copy.deepcopy(entry)
        for widget in entry.get("widgets", ()):
            if _too_long(widget.get("value")):
                widget["value"] = None
                withheld.add((type_name, widget["name"]))
        out[type_name] = entry
    return out, withheld


# --- files ----------------------------------------------------------------------------


def canonical_json(doc: Any, *, sort_keys: bool) -> bytes:
    text = json.dumps(doc, sort_keys=sort_keys, indent=1, ensure_ascii=True) + "\n"
    return text.encode("ascii")


def write_atomically(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` through a new temporary file beside it, then rename it over.

    The temporary file is created exclusively under a name nobody can have planted, so a symbolic
    link in the output folder is never written through; a link at ``target`` is replaced.
    """
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            umask = os.umask(0)
            os.umask(umask)
            os.fchmod(handle.fileno(), 0o666 & ~umask)  # mkstemp makes it 0600; a written file follows the umask
            handle.write(data)
        os.replace(name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise


def _sorted(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: mapping[key] for key in sorted(mapping)}


def read_doc(path: Path) -> Any:
    with path.open("rb") as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"{path} is past the limit of {MAX_INPUT_BYTES} bytes")
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as packed:
            data = packed.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError(f"{path} inflates past the limit of {MAX_INPUT_BYTES} bytes")
    return json.loads(data.decode("utf-8"))


def _built(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("format") == FORMAT and isinstance(doc.get("nodes"), dict)


def _previous(doc: dict[str, Any], types: Iterable[str]) -> Withheld:
    """Withheld pairs a built input already lists, limited to the types kept."""
    kept = set(types)
    return {(t, n) for t, n in doc.get("withheld", ()) if t in kept}


def _listed(withheld: Withheld) -> list[list[str]]:
    return [list(pair) for pair in sorted(withheld)]


def build(object_info_doc: Any, truth_doc: Any) -> tuple[bytes, bytes]:
    """``(core_nodedefs bytes, core_truth bytes)``; raises ``ValueError`` on a bad input shape."""
    if not _built(truth_doc) or not isinstance(truth_doc.get("comfyui_version"), str):
        raise ValueError("truth snapshot: expected format 1 with comfyui_version and nodes")
    raw_info = object_info_doc["nodes"] if _built(object_info_doc) else object_info_doc
    if not isinstance(raw_info, dict):
        raise ValueError("object_info: expected an object of node types")

    nodes, held = clean_object_info(raw_info)
    if _built(object_info_doc):
        held |= _previous(object_info_doc, nodes)
    nodedefs = _sorted(
        {"format": FORMAT, "comfyui": truth_doc["comfyui_version"], "nodes": _sorted(nodes), "withheld": _listed(held)}
    )

    truth_nodes, truth_held = clean_truth(truth_doc["nodes"], nodes)
    truth_held |= _previous(truth_doc, truth_nodes)
    truth = {
        "format": FORMAT,
        "frontend_version": truth_doc["frontend_version"],
        "comfyui_version": truth_doc["comfyui_version"],
        "nodes": truth_nodes,
        "withheld": _listed(truth_held),
    }
    return canonical_json(nodedefs, sort_keys=False), canonical_json(truth, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("object_info", type=Path, help="raw /object_info export (JSON, may be gzip)")
    parser.add_argument("truth", type=Path, help="raw truth snapshot from tools/oracle (JSON, may be gzip)")
    parser.add_argument("--out", type=Path, required=True, help="output folder, usually src/cwc/data/")
    args = parser.parse_args(argv)
    try:
        nodedefs, truth = build(read_doc(args.object_info), read_doc(args.truth))
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        print(f"build_core_data: {exc}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in ((NODEDEFS_FILE, nodedefs), (TRUTH_FILE, truth)):
        target = args.out / name
        write_atomically(target, data)
        print(f"{target.name}: {len(data)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
