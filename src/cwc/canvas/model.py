"""The one internal representation: a canvas graph in the frontend's 0.4 workflow format.

The raw JSON document is the source of truth. :class:`Canvas`, :class:`Node`,
:class:`Link` and :class:`Group` are views over it: they read typed values from the
raw dictionaries and write back only through named mutators. Anything cwc does not
model (unknown keys, extension data, floating links) is never touched, so a beautify
run is lossless by construction and the equivalence check only has to confirm it.

``ingest`` validates the document shape at the boundary; code holding a ``Canvas``
may rely on that shape.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterator

from cwc.canvas.geometry import TITLE_HEIGHT, Rect

NodeId = int | str  # the frontend accepts both; cwc writes ints
ROOT_VERSION = 0.4


def is_ascii_decimal(text: str) -> bool:
    """True for a non-empty string of ASCII digits, the only strings ``int()`` reads as an id
    (``str.isdigit`` also accepts ``"²"``, which ``int()`` refuses)."""
    return text.isascii() and text.isdigit()


def id_order(key: str) -> tuple[int, int | str]:
    """Sort key for node ids: numeric ids in numeric order, other ids after them by text."""
    return (0, int(key)) if is_ascii_decimal(key) else (1, key)


class NodeMode(IntEnum):
    ALWAYS = 0
    ON_EVENT = 1
    NEVER = 2  # muted
    ON_TRIGGER = 3
    BYPASS = 4


def vec2(value: Any) -> tuple[float, float]:
    """Read a frontend vector: ``[x, y]`` or the object form ``{"0": x, "1": y}``."""
    if isinstance(value, dict):
        return (float(value.get("0", value.get(0, 0))), float(value.get("1", value.get(1, 0))))
    return (float(value[0]), float(value[1]))


def _number(value: float) -> int | float:
    """Write integral floats as ints so output bytes do not depend on float formatting."""
    return int(value) if float(value).is_integer() else value


class Node:
    """View over one entry of ``nodes``."""

    __slots__ = ("raw",)

    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    @property
    def id(self) -> NodeId:
        return self.raw["id"]

    @property
    def key(self) -> str:
        """Node id as a string, the form used in prompts and reports."""
        return str(self.raw["id"])

    @property
    def type(self) -> str:
        return self.raw["type"]

    @property
    def pos(self) -> tuple[float, float]:
        return vec2(self.raw["pos"])

    @property
    def size(self) -> tuple[float, float]:
        return vec2(self.raw["size"])

    @property
    def mode(self) -> int:
        return int(self.raw.get("mode", NodeMode.ALWAYS))

    @property
    def flags(self) -> dict[str, Any]:
        return self.raw.get("flags") or {}

    @property
    def collapsed(self) -> bool:
        return bool(self.flags.get("collapsed"))

    @property
    def pinned(self) -> bool:
        return bool(self.flags.get("pinned"))

    @property
    def title(self) -> str | None:
        return self.raw.get("title")

    @property
    def properties(self) -> dict[str, Any]:
        return self.raw.get("properties") or {}

    @property
    def inputs(self) -> list[dict[str, Any]]:
        return self.raw.get("inputs") or []

    @property
    def outputs(self) -> list[dict[str, Any]]:
        return self.raw.get("outputs") or []

    @property
    def widgets_values(self) -> list[Any] | dict[str, Any] | None:
        return self.raw.get("widgets_values")

    @property
    def widgets_values_named(self) -> dict[str, Any] | None:
        return self.raw.get("widgets_values_named")

    def frame(self) -> Rect:
        """Visual box in classic mode including the title bar (pos is the body's top-left)."""
        x, y = self.pos
        w, h = self.size
        if self.collapsed:
            return Rect(x, y - TITLE_HEIGHT, w, TITLE_HEIGHT)
        return Rect(x, y - TITLE_HEIGHT, w, h + TITLE_HEIGHT)

    # Mutators. These are the only writes beautify may make to an existing node.

    def set_pos(self, x: float, y: float) -> None:
        self.raw["pos"] = [_number(x), _number(y)]

    def set_size(self, w: float, h: float) -> None:
        self.raw["size"] = [_number(w), _number(h)]

    # Compile only (annotate's node notes); tidy never calls these.

    def set_collapsed(self, collapsed: bool) -> None:
        """``flags.collapsed``: written as true, and removed rather than written false (the
        frontend's own form for an expanded node)."""
        flags = self.raw.setdefault("flags", {})
        if collapsed:
            flags["collapsed"] = True
        else:
            flags.pop("collapsed", None)

    def set_title(self, title: str) -> None:
        self.raw["title"] = title

    def set_colours(self, color: str, bgcolor: str) -> None:
        self.raw["color"] = color
        self.raw["bgcolor"] = bgcolor


@dataclass(frozen=True)
class Link:
    """One ``links`` tuple: ``[id, origin_id, origin_slot, target_id, target_slot, type]``."""

    id: int
    origin_id: NodeId
    origin_slot: int
    target_id: NodeId
    target_slot: int
    type: Any

    @classmethod
    def from_raw(cls, raw: list[Any]) -> Link:
        return cls(int(raw[0]), raw[1], int(raw[2]), raw[3], int(raw[4]), raw[5])

    def to_raw(self) -> list[Any]:
        return [self.id, self.origin_id, self.origin_slot, self.target_id, self.target_slot, self.type]


class Group:
    """View over one entry of ``groups``."""

    __slots__ = ("raw",)

    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    @property
    def id(self) -> int | None:
        return self.raw.get("id")

    @property
    def title(self) -> str:
        return self.raw.get("title", "")

    @property
    def bounding(self) -> Rect:
        x, y, w, h = self.raw["bounding"]
        return Rect(float(x), float(y), float(w), float(h))

    @property
    def color(self) -> str | None:
        value = self.raw.get("color")
        return value if isinstance(value, str) and value else None

    @property
    def pinned(self) -> bool:
        return bool((self.raw.get("flags") or {}).get("pinned"))

    def set_bounding(self, rect: Rect) -> None:
        self.raw["bounding"] = [_number(v) for v in rect.as_list()]


class Canvas:
    """View over a whole 0.4 workflow document."""

    __slots__ = ("raw",)

    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    @classmethod
    def empty(cls) -> Canvas:
        """A new, empty document in the key order the frontend writes."""
        return cls(
            {
                "last_node_id": 0,
                "last_link_id": 0,
                "nodes": [],
                "links": [],
                "groups": [],
                "config": {},
                "extra": {},
                "version": ROOT_VERSION,
            }
        )

    # Reading.

    @property
    def nodes(self) -> list[Node]:
        return [Node(raw) for raw in self.raw.get("nodes", [])]

    def node_map(self) -> dict[str, Node]:
        return {node.key: node for node in self.nodes}

    @property
    def links(self) -> list[Link]:
        return [Link.from_raw(raw) for raw in self.raw.get("links", []) if raw is not None]

    def link_map(self) -> dict[int, Link]:
        return {link.id: link for link in self.links}

    @property
    def groups(self) -> list[Group]:
        return [Group(raw) for raw in self.raw.get("groups", [])]

    @property
    def extra(self) -> dict[str, Any]:
        return self.raw.setdefault("extra", {})

    @property
    def reroutes(self) -> list[dict[str, Any]]:
        return list(self.raw.get("extra", {}).get("reroutes") or [])

    @property
    def link_extensions(self) -> dict[int, int]:
        """Link id to the reroute nearest its input."""
        out: dict[int, int] = {}
        for item in self.raw.get("extra", {}).get("linkExtensions") or []:
            out[int(item["id"])] = int(item["parentId"])
        return out

    @property
    def floating_links(self) -> list[Any]:
        return list(self.raw.get("floatingLinks") or [])

    def iter_input_links(self, node: Node) -> Iterator[tuple[int, dict[str, Any], Link]]:
        """(input index, input slot, link) for each linked input of ``node``."""
        links = self.link_map()
        for index, slot in enumerate(node.inputs):
            link_id = slot.get("link")
            if link_id is not None and int(link_id) in links:
                yield index, slot, links[int(link_id)]

    # Writing whole tables (compile builds them; beautify replaces only geometry tables).

    def set_groups(self, groups: list[dict[str, Any]]) -> None:
        self.raw["groups"] = groups

    def set_reroutes(self, reroutes: list[dict[str, Any]], link_extensions: list[dict[str, Any]]) -> None:
        extra = self.extra
        if reroutes:
            extra["reroutes"] = reroutes
        else:
            extra.pop("reroutes", None)
        if link_extensions:
            extra["linkExtensions"] = link_extensions
        else:
            extra.pop("linkExtensions", None)

    def to_json(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw)
