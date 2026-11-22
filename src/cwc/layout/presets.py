"""Layout presets: ``src/cwc/data/presets.json``, parsed and validated at load.

Document shape (every key required unless marked optional; unknown keys are refused)::

    {"format": 1,
     "presets": {
       "<name>": {
         "axis": "x" | "y",
         "wrap_ratio": <number > 1>,
         "guide_width": <positive multiple of 20>,
         "numbering": "number" | "step",
         "palette": "<palette name>",
         "bands": [{"slots": [{"zones": ["<zone>", ...],
                               "stack": "x" | "y",            (optional, default "y")
                               "repeat": "x" | "y" | null,    (optional, default null)
                               "attach": ["<zone>", ...],      (optional, default [])
                               "under_guide": true | false}]}], (optional, default false)
         "corridors": [{"kind": "bus" | "skybridge" | "spine",
                        "after_slot": <band-0 slot index>}],  (spine only)
         "cockpit_split": {"core": ["<cockpit slot>", ...]} | null,  (optional)
         "preview": {"zone": "<zone>", "share": <0 < n < 1>,
                     "min_height": <positive int>} | null,  (optional)
         "tray": {"ratio": <number > 1>, "zones": ["<zone>", ...]} | null  (optional)
       }}}

Rules beyond the shape: every zone of :class:`cwc.annotate.types.Zone` appears in exactly
one slot; a bus needs two bands; a spine's ``after_slot`` is a band-0 slot that has a slot
after it; ``attach`` needs ``repeat``, names zones of its own slot and leaves at least one zone
that opens instances; only band 0 slot 0 may be ``under_guide``; a preview's zone sits in
band 0; a tray needs a preview, its zones sit in band 0 and do not include the preview's zone.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Mapping

from cwc.annotate.palette import palette_names
from cwc.annotate.types import CockpitSlot, Zone
from cwc.canvas.geometry import GRID
from cwc.layout.types import (
    Axis,
    Band,
    CockpitSplit,
    CorridorKind,
    CorridorRule,
    Numbering,
    Preset,
    Preview,
    Slot,
    Tray,
)
from cwc.resources import read_json

FILE = "presets.json"
FORMAT = 1
DEFAULT = "bento"

_PRESET_KEYS = {"axis", "wrap_ratio", "guide_width", "numbering", "palette", "bands", "corridors"}
_OPTIONAL_KEYS = {"cockpit_split", "preview", "tray"}


def load_presets() -> dict[str, Preset]:
    """Every bundled preset by name. Raises ``ValueError`` when the data file is malformed."""
    return dict(_bundled())


def get_preset(name: str) -> Preset:
    """One bundled preset; ``KeyError`` for an unknown name."""
    return _bundled()[name]


@lru_cache(maxsize=1)
def _bundled() -> Mapping[str, Preset]:
    return parse_presets(read_json(FILE))


def parse_presets(doc: Any) -> dict[str, Preset]:
    """Parse and validate a presets document (shape in the module docstring)."""
    _object(doc, {"format", "presets"}, set(), "presets document")
    if doc["format"] != FORMAT:
        raise ValueError(f"presets document: unsupported format {doc['format']!r}")
    presets = doc["presets"]
    if not isinstance(presets, dict) or not presets:
        raise ValueError("presets document: 'presets' must be a non-empty object")
    return {name: _preset(name, entry) for name, entry in presets.items()}


def _preset(name: str, entry: Any) -> Preset:
    where = f"preset {name!r}"
    _object(entry, _PRESET_KEYS, _OPTIONAL_KEYS, where)
    bands = tuple(_band(band, f"{where} band {i}") for i, band in enumerate(_list(entry["bands"], f"{where} bands")))
    if not bands:
        raise ValueError(f"{where}: no bands")
    _zones_complete(bands, where)
    _under_guide(bands, where)
    corridors = tuple(_corridor(c, bands, f"{where} corridor {i}") for i, c in enumerate(_list(entry["corridors"], f"{where} corridors")))
    guide_width = entry["guide_width"]
    if not isinstance(guide_width, int) or isinstance(guide_width, bool) or guide_width <= 0 or guide_width % GRID:
        raise ValueError(f"{where}: guide_width must be a positive multiple of {GRID}")
    palette = entry["palette"]
    if palette not in palette_names():
        raise ValueError(f"{where}: palette must be one of {sorted(palette_names())}")
    preview = _preview(entry.get("preview"), bands, where)
    tray = _tray(entry.get("tray"), bands, preview, where)
    return Preset(
        name=name,
        axis=_enum(Axis, entry["axis"], f"{where} axis"),
        bands=bands,
        corridors=corridors,
        wrap_ratio=_ratio(entry["wrap_ratio"], f"{where} wrap_ratio"),
        guide_width=guide_width,
        numbering=_enum(Numbering, entry["numbering"], f"{where} numbering"),
        palette=palette,
        cockpit_split=_cockpit_split(entry.get("cockpit_split"), where),
        preview=preview,
        tray=tray,
    )


def _band(band: Any, where: str) -> Band:
    _object(band, {"slots"}, set(), where)
    slots = tuple(_slot(slot, f"{where} slot {i}") for i, slot in enumerate(_list(band["slots"], f"{where} slots")))
    if not slots:
        raise ValueError(f"{where}: no slots")
    return Band(slots)


def _slot(slot: Any, where: str) -> Slot:
    _object(slot, {"zones"}, {"stack", "repeat", "attach", "under_guide"}, where)
    zones = tuple(_enum(Zone, z, f"{where} zone") for z in _list(slot["zones"], f"{where} zones"))
    if not zones:
        raise ValueError(f"{where}: no zones")
    repeat = slot.get("repeat")
    attach = tuple(_enum(Zone, z, f"{where} attach") for z in _list(slot.get("attach", []), f"{where} attach"))
    if attach:
        if repeat is None:
            raise ValueError(f"{where}: attach needs repeat")
        if len(set(attach)) != len(attach) or any(z not in zones for z in attach) or set(attach) == set(zones):
            raise ValueError(f"{where}: attach must name zones of this slot, without repeats, and leave one that opens instances")
    under_guide = slot.get("under_guide", False)
    if not isinstance(under_guide, bool):
        raise ValueError(f"{where}: under_guide must be true or false")
    return Slot(
        zones=zones,
        stack=_enum(Axis, slot.get("stack", Axis.Y.value), f"{where} stack"),
        repeat=None if repeat is None else _enum(Axis, repeat, f"{where} repeat"),
        attach=attach,
        under_guide=under_guide,
    )


def _zones_complete(bands: tuple[Band, ...], where: str) -> None:
    listed = [zone for band in bands for slot in band.slots for zone in slot.zones]
    twice = sorted({z.value for z in listed if listed.count(z) > 1})
    missing = sorted(z.value for z in Zone if z not in listed)
    if twice or missing:
        raise ValueError(f"{where}: every zone must appear in exactly one slot (twice: {twice}, missing: {missing})")


def _under_guide(bands: tuple[Band, ...], where: str) -> None:
    for b, band in enumerate(bands):
        for s, slot in enumerate(band.slots):
            if slot.under_guide and (b, s) != (0, 0):
                raise ValueError(f"{where}: only band 0 slot 0 may be under_guide (band {b} slot {s})")


def _band0_zones(bands: tuple[Band, ...]) -> set[Zone]:
    return {zone for slot in bands[0].slots for zone in slot.zones}


def _corridor(entry: Any, bands: tuple[Band, ...], where: str) -> CorridorRule:
    _object(entry, {"kind"}, {"after_slot"}, where)
    kind = _enum(CorridorKind, entry["kind"], f"{where} kind")
    after = entry.get("after_slot")
    if kind is CorridorKind.SPINE:
        last = len(bands[0].slots) - 1
        if not isinstance(after, int) or isinstance(after, bool) or not 0 <= after < last:
            raise ValueError(f"{where}: a spine needs after_slot between 0 and {last - 1}")
    elif after is not None:
        raise ValueError(f"{where}: after_slot is only for a spine")
    if kind is CorridorKind.BUS and len(bands) < 2:
        raise ValueError(f"{where}: a bus needs two bands")
    return CorridorRule(kind, after)


def _cockpit_split(entry: Any, where: str) -> CockpitSplit | None:
    if entry is None:
        return None
    _object(entry, {"core"}, {"axis", "min_nodes"}, f"{where} cockpit_split")
    core = tuple(_enum(CockpitSlot, c, f"{where} cockpit_split core") for c in _list(entry["core"], f"{where} cockpit_split core"))
    if not core or len(set(core)) != len(core):
        raise ValueError(f"{where}: cockpit_split core must be non-empty without repeats")
    axis = _enum(Axis, entry.get("axis", Axis.Y.value), f"{where} cockpit_split axis")
    min_nodes = entry.get("min_nodes", 1)
    if not isinstance(min_nodes, int) or isinstance(min_nodes, bool) or min_nodes < 1:
        raise ValueError(f"{where}: cockpit_split min_nodes must be a positive integer")
    return CockpitSplit(core, axis, min_nodes)


def _preview(entry: Any, bands: tuple[Band, ...], where: str) -> Preview | None:
    if entry is None:
        return None
    _object(entry, {"zone", "share", "min_height"}, {"extra_zones", "dock_steps"}, f"{where} preview")
    zone = _enum(Zone, entry["zone"], f"{where} preview zone")
    if zone not in _band0_zones(bands):
        raise ValueError(f"{where}: preview zone must sit in band 0")
    extra_raw = entry.get("extra_zones", [])
    extra = tuple(_enum(Zone, z, f"{where} preview extra_zones") for z in _list(extra_raw, f"{where} preview extra_zones"))
    if len(set(extra)) != len(extra):
        raise ValueError(f"{where}: preview extra_zones must not contain repeats")
    dock_steps = entry.get("dock_steps", False)
    if not isinstance(dock_steps, bool):
        raise ValueError(f"{where}: preview dock_steps must be true or false")
    if dock_steps and len(bands) < 2:
        raise ValueError(f"{where}: preview dock_steps needs at least two bands")
    share, height = entry["share"], entry["min_height"]
    if not _number(share) or not 0 < share < 1:
        raise ValueError(f"{where}: preview share must be between 0 and 1")
    if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
        raise ValueError(f"{where}: preview min_height must be a positive integer")
    return Preview(zone, float(share), height, extra, dock_steps)


def _tray(entry: Any, bands: tuple[Band, ...], preview: Preview | None, where: str) -> Tray | None:
    if entry is None:
        return None
    _object(entry, {"ratio", "zones"}, set(), f"{where} tray")
    if preview is None:
        raise ValueError(f"{where}: a tray needs a preview")
    zones = tuple(_enum(Zone, z, f"{where} tray zone") for z in _list(entry["zones"], f"{where} tray zones"))
    band0 = _band0_zones(bands)
    if not zones or len(set(zones)) != len(zones) or any(z not in band0 for z in zones):
        raise ValueError(f"{where}: tray zones must be non-empty, without repeats, and sit in band 0")
    if preview.zone in zones:
        raise ValueError(f"{where}: the preview's zone cannot go to the tray")
    return Tray(_ratio(entry["ratio"], f"{where} tray ratio"), zones)


def _object(value: Any, required: set[str], optional: set[str], where: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected an object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        raise ValueError(f"{where}: missing keys {missing}, unknown keys {unknown}")


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{where}: expected a list")
    return value


def _enum(kind: type, value: Any, where: str) -> Any:
    try:
        return kind(value)
    except ValueError:
        raise ValueError(f"{where}: {value!r} is not one of {[m.value for m in kind]}") from None


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _ratio(value: Any, where: str) -> float:
    if not _number(value) or value <= 1:
        raise ValueError(f"{where}: must be a number above 1")
    return float(value)
