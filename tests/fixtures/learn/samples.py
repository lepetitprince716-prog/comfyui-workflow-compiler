"""Hand-written samples for the learn tests: canvases and prompts built from short specs.

The node types are fictional (``object_info.json`` beside this file) except the core
sampler, which the bundled truth covers.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from cwc.canvas.model import Canvas
from cwc.nodedefs.learn import NO_TEMPLATES, Sample
from cwc.nodedefs.registry import NodeDefs

OBJECT_INFO = json.loads((Path(__file__).with_name("object_info.json")).read_text(encoding="utf-8"))


@cache
def base_defs() -> NodeDefs:
    """The definitions learning starts from, loaded once per process (a load takes seconds).
    A registry is read-only, so tests may share it."""
    return NodeDefs.load(object_info=OBJECT_INFO, templates=NO_TEMPLATES)


def node(node_id: int, type_name: str, values: Any, *, ver: str | None = None, mode: int = 0, slots: list | None = None) -> dict:
    raw: dict[str, Any] = {"id": node_id, "type": type_name, "pos": [0, 0], "size": [100, 100], "mode": mode}
    raw["properties"] = {"cnr_id": "pack-" + type_name.split()[0].lower(), "ver": ver} if ver else {}
    if slots is not None:
        raw["inputs"] = slots
    if values is not None:
        raw["widgets_values"] = values
    return raw


def canvas(*nodes: dict, subgraphs: list | None = None) -> dict:
    doc: dict[str, Any] = {
        "last_node_id": max((n["id"] for n in nodes), default=0),
        "last_link_id": 0,
        "nodes": list(nodes),
        "links": [],
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }
    if subgraphs is not None:
        doc["definitions"] = {"subgraphs": subgraphs}
    return doc


def prompt(**entries: tuple[str, dict]) -> dict:
    """``prompt(n3=("Xi Stack", {...}))``: node id 3 with that class and inputs."""
    return {key[1:]: {"class_type": cls, "inputs": inputs} for key, (cls, inputs) in entries.items()}


def sample(doc: dict, api: dict) -> Sample:
    return Sample(Canvas(doc), api)


# A stack node whose extension adds a metadata object before ``text`` and a list after
# ``strength``: four saved values where /object_info declares two widgets.
STACK_META = {"version": 1}
STACK_LIST = [{"name": "x", "on": True}]


def stack_node(node_id: int, *, ver: str | None = "1.2.0", text: str = "hello", mode: int = 0) -> dict:
    return node(node_id, "Xi Stack", [STACK_META, text, 0.5, STACK_LIST], ver=ver, mode=mode, slots=[{"name": "clip", "type": "CLIP", "link": 1}])


def stack_inputs(text: str = "hello") -> dict:
    return {"meta": STACK_META, "text": text, "strength": 0.5, "stack": {"__value__": STACK_LIST}, "clip": ["9", 0]}
