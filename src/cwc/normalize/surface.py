"""The input names an authoring node may write, given its definition and chosen values.

Legal names are the definition's inputs with the DynamicCombo sub-inputs of the chosen
options (``combo.sub``) and the Autogrow slots (``group.prefixN``), plus persisted
companion widgets that enter the prompt (a frontend export carries them, and a strict
prompt must stay a valid authoring document). Widgets the frontend keeps out of the
prompt (upload buttons, value controls, followers) are known but not writable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from cwc.nodedefs.derive import widget_layout
from cwc.nodedefs.types import DYNAMIC_GROUP, InputSpec, NodeDef, WidgetSpec


class Kind(str, Enum):
    WIDGET = "widget"  # backend input with a persisted widget that enters the prompt
    SOCKET = "socket"  # link-only input
    COMPANION = "companion"  # persisted widget next to a value widget that enters the prompt
    UNPERSISTED = "unpersisted"  # backend input whose widget the frontend does not persist
    FRONTEND = "frontend"  # widget kept out of the prompt; not an input


@dataclass(frozen=True)
class Target:
    name: str
    kind: Kind
    type: str
    required: bool = False
    spec: InputSpec | None = None  # the declared input (SOCKET, WIDGET, UNPERSISTED)
    widget: WidgetSpec | None = None  # the persisted widget (WIDGET, COMPANION, FRONTEND)

    @property
    def linkable(self) -> bool:
        if self.kind is Kind.SOCKET:
            return True
        return self.kind in (Kind.WIDGET, Kind.UNPERSISTED) and self.spec is not None and not self.spec.socketless


@dataclass(frozen=True)
class Surface:
    layout: tuple[WidgetSpec, ...]  # widgets_values layout for the chosen values
    targets: dict[str, Target]  # in declaration order
    open_groups: tuple[str, ...]  # DynamicGroup inputs: "<name>.<key>" passes unchecked
    legal: tuple[str, ...]  # legal names for hints; Autogrow ranges shortened

    def open_group_key(self, name: str) -> bool:
        return any(name.startswith(group + ".") for group in self.open_groups)


def surface(nodedef: NodeDef, values: Mapping[str, Any]) -> Surface:
    """``values``: the literal (unwrapped) values the author wrote, by input name."""
    layout = widget_layout(nodedef, values)
    by_name = {w.name: w for w in layout if w.name is not None}
    targets: dict[str, Target] = {}
    legal: list[str] = []
    groups: list[str] = []

    def visit(specs: tuple[InputSpec, ...]) -> None:
        for spec in specs:
            if spec.autogrow is not None:
                grow = spec.autogrow
                names = []
                for ordinal in range(grow.max):
                    for t in grow.template:
                        name = grow.slot_name(spec.name, ordinal, t.name)
                        names.append(name)
                        targets.setdefault(name, Target(name, Kind.SOCKET, t.type, ordinal < grow.min and t.required, t))
                if names:
                    legal.append(names[0] if len(names) == 1 else f"{names[0]} ... {names[-1]}")
                continue
            if spec.type == DYNAMIC_GROUP:
                groups.append(spec.name)
                legal.append(f"{spec.name}.*")
                continue
            if not spec.widget:
                targets[spec.name] = Target(spec.name, Kind.SOCKET, spec.type, spec.required, spec)
                legal.append(spec.name)
                continue
            widget = by_name.get(spec.name)
            if widget is None:
                kind = Kind.UNPERSISTED
            else:
                kind = Kind.WIDGET if widget.to_prompt else Kind.FRONTEND
            targets[spec.name] = Target(spec.name, kind, spec.type, spec.required, spec, widget)
            if kind is not Kind.FRONTEND:
                legal.append(spec.name)
            if spec.dynamic_options:
                chosen = values[spec.name] if spec.name in values else (widget.default if widget else None)
                children = dict(spec.dynamic_options).get(chosen, ()) if isinstance(chosen, str) else ()
                visit(children)

    visit(nodedef.inputs)
    for widget in layout:
        if widget.name is not None and widget.name not in targets:
            kind = Kind.COMPANION if widget.to_prompt else Kind.FRONTEND
            targets[widget.name] = Target(widget.name, kind, "", False, None, widget)
            if kind is Kind.COMPANION:
                legal.append(widget.name)
    return Surface(layout, targets, tuple(groups), tuple(legal))
