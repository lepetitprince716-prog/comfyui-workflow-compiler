"""Colour classes and palettes: ``src/cwc/data/palettes.json``, parsed and validated at load.

Annotate names colours by class, never by value: a group's class comes from its zone
(:func:`zone_class`), a node's from its role in the prompt (``positive``, ``negative``).
Layout turns a group class into a colour through the preset's palette (:func:`group_colour`);
node colours are the same under every palette (:func:`node_colours`). Layout depends on
annotate, so this module lives here.

Document shape (every key required, unknown keys refused)::

    {"format": 1,
     "zone_classes": {"<zone>": "<class>", ...},          (every Zone, at most seven classes)
     "nodes": {"<node class>": {"color": "#rrggbb", "bgcolor": "#rrggbb"}, ...},
     "palettes": {"<name>": {"<class>": "#rrggbb", ...}}}  (every class of zone_classes, no other)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from cwc.annotate.types import Zone
from cwc.resources import read_json

FILE = "palettes.json"
FORMAT = 1
MAX_CLASSES = 7  # C6: at most seven group colour classes
POSITIVE = "positive"
NEGATIVE = "negative"

_HEX = re.compile(r"#[0-9a-f]{6}")


@dataclass(frozen=True)
class NodeColours:
    color: str
    bgcolor: str


@dataclass(frozen=True)
class Palettes:
    zone_classes: Mapping[Zone, str]
    nodes: Mapping[str, NodeColours]
    palettes: Mapping[str, Mapping[str, str]]


def zone_class(zone: Zone) -> str:
    """The group colour class of ``zone``."""
    return _bundled().zone_classes[zone]


def group_colour(palette: str, colour_class: str) -> str:
    """The group colour of ``colour_class`` under ``palette``; ``KeyError`` for an unknown name."""
    return _bundled().palettes[palette][colour_class]


def node_colours(node_class: str) -> NodeColours:
    """The node title and body colours of ``node_class``; ``KeyError`` for an unknown name."""
    return _bundled().nodes[node_class]


def palette_names() -> frozenset[str]:
    return frozenset(_bundled().palettes)


@lru_cache(maxsize=1)
def _bundled() -> Palettes:
    return parse_palettes(read_json(FILE))


def parse_palettes(doc: Any) -> Palettes:
    """Parse and validate a palettes document (shape in the module docstring)."""
    _object(doc, {"format", "zone_classes", "nodes", "palettes"}, "palettes document")
    if doc["format"] != FORMAT:
        raise ValueError(f"palettes document: unsupported format {doc['format']!r}")
    zone_classes = _zone_classes(doc["zone_classes"])
    classes = set(zone_classes.values())
    nodes = doc["nodes"]
    if not isinstance(nodes, dict) or set(nodes) != {POSITIVE, NEGATIVE}:
        raise ValueError(f"palettes document: nodes must name exactly {POSITIVE!r} and {NEGATIVE!r}")
    parsed_nodes = {}
    for name, entry in nodes.items():
        _object(entry, {"color", "bgcolor"}, f"node class {name!r}")
        parsed_nodes[name] = NodeColours(_hex(entry["color"], f"node class {name!r}"), _hex(entry["bgcolor"], f"node class {name!r}"))
    palettes = doc["palettes"]
    if not isinstance(palettes, dict) or not palettes:
        raise ValueError("palettes document: 'palettes' must be a non-empty object")
    parsed = {}
    for name, entry in palettes.items():
        where = f"palette {name!r}"
        _object(entry, classes, where)
        parsed[name] = {cls: _hex(value, f"{where} class {cls!r}") for cls, value in sorted(entry.items())}
    return Palettes(zone_classes, parsed_nodes, parsed)


def _zone_classes(entry: Any) -> dict[Zone, str]:
    _object(entry, {zone.value for zone in Zone}, "zone_classes")
    out: dict[Zone, str] = {}
    for zone in Zone:
        value = entry[zone.value]
        if not isinstance(value, str) or not value:
            raise ValueError(f"zone_classes: class of {zone.value!r} must be a non-empty string")
        out[zone] = value
    if len(set(out.values())) > MAX_CLASSES:
        raise ValueError(f"zone_classes: more than {MAX_CLASSES} classes")
    return out


def _object(value: Any, keys: set[str], where: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected an object")
    missing, unknown = sorted(keys - set(value)), sorted(set(value) - keys)
    if missing or unknown:
        raise ValueError(f"{where}: missing keys {missing}, unknown keys {unknown}")


def _hex(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"{where}: {value!r} is not a lower-case #rrggbb colour")
    return value
