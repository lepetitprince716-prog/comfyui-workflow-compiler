"""Test-only node definition registry for normalize: ``get`` and ``closest`` over core
definitions derived from the ``/object_info`` fixtures."""

from __future__ import annotations

import copy
import difflib
import json
from pathlib import Path
from typing import Any

from cwc.nodedefs.derive import from_object_info
from cwc.nodedefs.types import NodeDef

HERE = Path(__file__).resolve().parent
SOURCES = (HERE.parent / "nodedefs" / "core_sample.json", HERE / "nodedefs.json")


class Registry:
    def __init__(self, defs: dict[str, NodeDef]):
        self._defs = dict(defs)

    def get(self, type: str) -> NodeDef | None:
        return self._defs.get(type)

    def closest(self, type: str, n: int = 3) -> list[str]:
        return difflib.get_close_matches(type, sorted(self._defs), n=n, cutoff=0.0)

    def with_def(self, nodedef: NodeDef) -> "Registry":
        return Registry({**self._defs, nodedef.type: nodedef})


def load_registry() -> Registry:
    defs: dict[str, NodeDef] = {}
    for path in SOURCES:
        for name, info in json.loads(path.read_text(encoding="utf-8"))["nodes"].items():
            defs[name] = from_object_info(name, info)
    return Registry(defs)


def load_doc(name: str) -> dict[str, Any]:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def fresh(doc: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(doc)
