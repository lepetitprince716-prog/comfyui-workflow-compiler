"""Truth snapshots: node layouts a real frontend recorded (format in docs/ARCHITECTURE.md).

A snapshot fixes what only the frontend knows for sure: the order of the ``inputs`` slots,
the output slots, and the persisted widgets with their initial values. Everything else
(required, has_default, combo options, ranges) still comes from ``/object_info``, so a
truth entry is merged onto the derived definition of the same type.

A value listed in ``withheld`` was removed from the bundled data (an over-long upstream
default); it counts as unknown.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any

from cwc.nodedefs.derive import match_widget, slot_layout
from cwc.nodedefs.types import NodeDef, OutputSpec, SlotSpec, Tier, WidgetKind, WidgetSpec

FORMAT = 1


@dataclass(frozen=True)
class TruthWidget:
    name: str
    value: Any
    to_prompt: bool
    withheld: bool = False


@dataclass(frozen=True)
class TruthNode:
    slots: tuple[SlotSpec, ...]  # ``optional`` is filled in by :func:`merge`
    outputs: tuple[OutputSpec, ...]
    widgets: tuple[TruthWidget, ...]
    size: tuple[float, float] | None = None  # classic renderer, right after creation


def _bad(where: str, message: str) -> ValueError:
    return ValueError(f"truth snapshot: {where}: {message}")


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise _bad(where, "expected a string")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _bad(where, "expected a list of objects")
    return value


def _node(type_name: str, raw: Any, withheld: set[str]) -> TruthNode:
    if not isinstance(raw, dict):
        raise _bad(type_name, "entry is not an object")
    slots = tuple(
        SlotSpec(
            _str(s.get("name"), f"{type_name}.inputs"),
            _str(s.get("type"), f"{type_name}.inputs"),
            s.get("widget") if isinstance(s.get("widget"), str) else None,
        )
        for s in _list(raw.get("inputs"), f"{type_name}.inputs")
    )
    outputs = tuple(
        OutputSpec(
            _str(o.get("name"), f"{type_name}.outputs"),
            _str(o.get("type"), f"{type_name}.outputs"),
            o.get("is_list") is True,
        )
        for o in _list(raw.get("outputs"), f"{type_name}.outputs")
    )
    widgets = []
    for w in _list(raw.get("widgets"), f"{type_name}.widgets"):
        name = _str(w.get("name"), f"{type_name}.widgets")
        widgets.append(TruthWidget(name, w.get("value"), w.get("to_prompt") is not False, name in withheld))
    return TruthNode(slots, outputs, tuple(widgets), _classic_size(raw.get("size"), type_name))


def _classic_size(raw: Any, type_name: str) -> tuple[float, float] | None:
    classic = raw.get("classic") if isinstance(raw, dict) else None
    if classic is None:
        return None
    if not (isinstance(classic, list) and len(classic) == 2 and all(isinstance(v, (int, float)) for v in classic)):
        raise _bad(f"{type_name}.size", "classic must be [width, height]")
    return (float(classic[0]), float(classic[1]))


def parse_truth(doc: Any) -> dict[str, TruthNode]:
    """Check and convert a snapshot document; raises ``ValueError`` on a bad shape."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or not isinstance(doc.get("nodes"), dict):
        raise _bad("document", f"expected format {FORMAT} with a nodes object")
    held: dict[str, set[str]] = {}
    for pair in doc.get("withheld", ()):
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) for p in pair)):
            raise _bad("withheld", "expected [type, widget name] pairs")
        held.setdefault(pair[0], set()).add(pair[1])
    return {name: _node(name, raw, held.get(name, set())) for name, raw in doc["nodes"].items()}


def merge(derived: NodeDef, truth: TruthNode) -> NodeDef:
    """The TRUTH definition: slot and widget order, widget initial values and outputs from
    the snapshot; inputs and the rest of the metadata from the derived definition.

    A withheld widget value falls back to the derived default when the input declares
    one, and is None otherwise. A DynamicCombo type keeps the derived ``layout_complete``,
    because its layout for chosen values is replayed from ``/object_info``.
    """
    known = {slot.name: slot for slot in slot_layout(derived)}
    slots = tuple(
        replace(slot, optional=known[slot.name].optional, match=known[slot.name].match) if slot.name in known else slot
        for slot in truth.slots
    )
    # The snapshot does not record match-type templates; outputs keep the derived ones.
    outputs = tuple(
        replace(out, match=derived.outputs[i].match) if i < len(derived.outputs) else out
        for i, out in enumerate(truth.outputs)
    )
    widgets = tuple(_widget(derived, w) for w in truth.widgets)
    return replace(
        derived,
        tier=Tier.TRUTH,
        outputs=outputs,
        widgets=widgets,
        slots=slots,
        layout_complete=derived.layout_complete or not derived.has_dynamic,
        size=truth.size,
    )


def _widget(derived: NodeDef, recorded: TruthWidget) -> WidgetSpec:
    base = match_widget(derived, recorded.name)
    if not recorded.withheld:
        default = copy.deepcopy(recorded.value)
    else:
        spec = derived.input(base.input_name) if base.input_name is not None else None
        known = base.kind is not WidgetKind.EXTRA and spec is not None and spec.has_default
        default = base.default if known else None
    return replace(base, default=default, to_prompt=recorded.to_prompt)
