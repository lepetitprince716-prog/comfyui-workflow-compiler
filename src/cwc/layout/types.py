"""Layout's contract: the preset it reads and the result it returns.

A preset (``src/cwc/data/presets.json``, parsed by :mod:`cwc.layout.presets`) says where each zone
goes: bands stacked top to bottom, each band a row of slots, each slot an ordered list of
zones. It holds relative order only, never coordinates, so an empty slot, band or corridor
collapses to nothing. A :class:`LayoutResult` says what layout wrote and what routing (M3)
needs: bands, columns, corridor rectangles, the reserved guide rectangle, group numbers.

Checks never read a ``LayoutResult``; they read the written canvas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from cwc.annotate.types import CockpitSlot, Zone
from cwc.canvas.geometry import Rect

Point = tuple[float, float]


class Axis(str, Enum):
    X = "x"  # left to right
    Y = "y"  # top to bottom


class CorridorKind(str, Enum):
    BUS = "bus"  # horizontal, between consecutive bands
    SKYBRIDGE = "skybridge"  # horizontal, above every group
    SPINE = "spine"  # vertical, between two slots of a band


class Numbering(str, Enum):
    NUMBER = "number"  # "01", "02", split groups "02a", "02b"
    STEP = "step"  # "Step 1", "Step 2"


@dataclass(frozen=True)
class Slot:
    """One place in a band. ``zones``: the zones whose groups go here, in this order.
    ``stack``: the direction the slot's groups follow one another. ``repeat``: when set,
    the slot opens one instance per topological stage of its groups, the instances laid
    out along this axis (a new sampling stage adds a column; a ``Y`` repeat stacks stages as
    strips). ``attach``: zones of a repeated slot whose groups open no instance of their own;
    each such group joins the instance of the lowest-ranked opening group downstream of it,
    or the last instance when none is (a control branch sits in the column of the stage it
    feeds). ``under_guide``: the slot shares column 0 with the reserved ``00`` guide
    rectangle and starts below it; only band 0 slot 0 may set it, and without a reserved
    guide the slot is simply column 0."""

    zones: tuple[Zone, ...]
    stack: Axis = Axis.Y
    repeat: Axis | None = None
    attach: tuple[Zone, ...] = ()
    under_guide: bool = False


@dataclass(frozen=True)
class Band:
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class CorridorRule:
    """Where a corridor goes. ``after_slot`` is the slot index of band 0 a spine follows;
    ``None`` for the other kinds."""

    kind: CorridorKind
    after_slot: int | None = None


@dataclass(frozen=True)
class CockpitSplit:
    """The cockpit as two boxes: the ``core`` slots first, every other slot second.
    ``axis`` is ``Y`` (stacked, default) or ``X`` (side by side); ``min_nodes`` requires at
    least that many cockpit members before splitting."""

    core: tuple[CockpitSlot, ...]
    axis: Axis = Axis.Y
    min_nodes: int = 1


@dataclass(frozen=True)
class Preview:
    """A reserved main preview: the slot of ``zone`` (in band 0) — plus any ``extra_zones`` —
    takes at least ``share`` of the first screen's width and ``min_height`` px of height for
    groups holding an expanded preview node. When ``dock_steps`` is true and the preset has a
    lower band, intermediate output groups fed by earlier stages dock in band 1 under the
    stages that follow their producer while the final output stays at the end of band 0."""

    zone: Zone
    share: float
    min_height: int
    extra_zones: tuple[Zone, ...] = ()
    dock_steps: bool = False


@dataclass(frozen=True)
class Tray:
    """An overflow tray: when the tallest slot of band 0 exceeds ``ratio`` times the preview
    height, the groups of ``zones`` leave their band-0 slot for a tray band below. Decided once
    in round 1 and frozen."""

    ratio: float
    zones: tuple[Zone, ...]


@dataclass(frozen=True)
class Preset:
    """A parsed preset. ``axis``: the main growth axis, along which main-line groups advance
    in topological order (C3). ``wrap_ratio``: a band wider than ``wrap_ratio`` times its
    height wraps its tail into a new band below. ``guide_width``: width of the ``00`` guide
    rectangle reserved at ``x = 0`` when the plan asks for it. Every zone appears in exactly
    one slot (checked by the parser), so every group has a home."""

    name: str
    axis: Axis
    bands: tuple[Band, ...]
    corridors: tuple[CorridorRule, ...]
    wrap_ratio: float
    guide_width: int
    numbering: Numbering
    palette: str
    cockpit_split: CockpitSplit | None = None
    preview: Preview | None = None
    tray: Tray | None = None
    wrap_rows: bool = False

    def home(self, zone: Zone) -> tuple[int, int]:
        """``(band, slot)`` of ``zone``."""
        for b, band in enumerate(self.bands):
            for s, slot in enumerate(band.slots):
                if zone in slot.zones:
                    return (b, s)
        raise KeyError(zone)


@dataclass(frozen=True)
class Column:
    """One placed slot instance: its rectangle and the groups in it, in placement order.
    ``stage`` is the topological stage of a repeated slot's instance, ``None`` otherwise."""

    band: int
    slot: int
    rect: Rect
    groups: tuple[str, ...]
    stage: int | None = None


@dataclass(frozen=True)
class Corridor:
    """A free strip reserved for links between groups. ``id`` is stable between the two
    layout rounds; ``tracks`` is how many 20 px tracks it holds."""

    id: str
    kind: CorridorKind
    rect: Rect
    tracks: int


@dataclass(frozen=True)
class Widening:
    """Round 2 input (M3): the round-1 result, whose band split, group order, layers and
    in-layer order are kept, and the tracks routing counted per corridor id."""

    previous: LayoutResult
    tracks: Mapping[str, int]


@dataclass(frozen=True)
class LayoutResult:
    """What layout wrote and decided.

    ``bands``: the rectangle of each band after wrapping, top to bottom. ``columns``: every
    placed slot instance. ``corridors``: the reserved link corridors. ``guide``: the reserved
    ``00`` guide rectangle, ``None`` when not asked for (no node is written for it in M2).
    ``labels``: group key to its number label ("01", "02a", "Step 3"); empty for a ``KEPT``
    plan, whose titles do not change. ``group_index``: group key to its index in the written
    ``groups`` array. ``membership``: node key to the index of its intended innermost group
    in the written ``groups`` array, ``None`` for an ungrouped node (the G3 target).
    ``waypoints``: for a link spanning layers inside a group, the centres of the dummy slots
    reserved for it in the intermediate layers, in order from output to input; routing turns
    them into reroutes. ``cockpit``: the indices in the written ``groups`` array of the cockpit's
    own groups, ascending: one index, one per part when C1 split the cockpit (``01a``, ``01b``),
    empty without a cockpit. It is what ``check_geometry(cockpit=...)`` takes; a group nested in
    the cockpit is not listed, the checks find it by containment.
    """

    preset: str
    bands: tuple[Rect, ...]
    columns: tuple[Column, ...]
    corridors: tuple[Corridor, ...]
    guide: Rect | None
    labels: Mapping[str, str]
    group_index: Mapping[str, int]
    membership: Mapping[str, int | None]
    waypoints: Mapping[int, tuple[Point, ...]] = field(default_factory=dict)
    cockpit: tuple[int, ...] = ()
