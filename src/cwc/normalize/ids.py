"""Top-level keys of an authoring document: node entries, the ``_workflow`` key, and ids.

A key that is a canonical positive integer keeps its number. Every other node key is a
symbolic id; symbolic ids get numbers from ``max(numeric ids) + 1`` upwards in sorted
(code point) order, so the same document always gets the same numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from cwc.ingest.authoring import WORKFLOW_INFO_KEY
from cwc.normalize.diagnostics import Findings
from cwc.report import Code

RESERVED_PREFIX = "_"
NODE_KEYS = ("class_type", "inputs", "_meta")

_POSITIVE = re.compile(r"[1-9][0-9]*")
_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class RawNode:
    """One node entry whose envelope is valid; inputs and ``_meta`` are not checked yet."""

    author_id: str
    class_type: str
    inputs: dict[str, Any]
    meta: dict[str, Any]


def split_document(doc: dict[str, Any], out: Findings) -> tuple[Any, list[RawNode]]:
    """``(raw _workflow value or None, node entries with a valid envelope)``."""
    workflow = None
    nodes: list[RawNode] = []
    for key, entry in doc.items():
        if key == WORKFLOW_INFO_KEY:
            workflow = entry
            continue
        if not _valid_id(key, out):
            continue
        node = _node(key, entry, out)
        if node is not None:
            nodes.append(node)
    return workflow, nodes


def _valid_id(key: str, out: Findings) -> bool:
    if key.startswith(RESERVED_PREFIX):
        out.error(
            Code.E7,
            f"top-level key {key!r} is reserved",
            [key],
            hint=f"only {WORKFLOW_INFO_KEY!r} may start with {RESERVED_PREFIX!r}; rename the node",
        )
        return False
    if key.strip() == "" or ":" in key:
        out.error(Code.E7, f"node id {key!r} is empty or contains ':'", [key], hint="use a plain name such as \"sampler\"")
        return False
    if _DIGITS.fullmatch(key) and not _POSITIVE.fullmatch(key):
        out.error(
            Code.E7,
            f"node id {key!r} is a number without a canonical positive form",
            [key],
            hint="numeric ids are positive integers without leading zeros; use a name or a number such as \"1\"",
        )
        return False
    return True


def _node(key: str, entry: Any, out: Findings) -> RawNode | None:
    if not isinstance(entry, dict):
        out.error(Code.E7, f"node {key!r} is not an object", [key], hint='write {"class_type": ..., "inputs": {...}}')
        return None
    class_type = entry.get("class_type")
    inputs = entry.get("inputs", {})
    meta = entry.get("_meta", {})
    ok = True
    if not isinstance(class_type, str) or class_type == "":
        out.error(Code.E7, f"node {key!r} has no class_type string", [key], hint="set class_type to a node type name")
        ok = False
    if not isinstance(inputs, dict):
        out.error(Code.E7, f"node {key!r}: inputs is not an object", [key], hint="inputs maps input names to values or links")
        ok = False
    if not isinstance(meta, dict):
        out.error(Code.E7, f"node {key!r}: _meta is not an object", [key], hint="_meta holds title and ui_* keys")
        ok = False
    extra = sorted(k for k in entry if k not in NODE_KEYS)
    if extra:
        out.warning(
            Code.E7,
            f"node {key!r}: keys {', '.join(extra)} are not part of a prompt node and are dropped",
            [key],
            hint="a node holds class_type, inputs and _meta only",
        )
    if not ok:
        return None
    return RawNode(key, class_type, inputs, meta)


def assign_ids(author_ids: list[str]) -> dict[str, str]:
    """Author id -> numeric prompt id (decimal string). Ids must already be valid."""
    numeric = [key for key in author_ids if _POSITIVE.fullmatch(key)]
    symbolic = sorted(key for key in author_ids if not _POSITIVE.fullmatch(key))
    next_id = max((int(key) for key in numeric), default=0) + 1
    ids = {key: key for key in numeric}
    for offset, key in enumerate(symbolic):
        ids[key] = str(next_id + offset)
    return ids
