"""Typed node definitions: ports, widgets and where the knowledge came from.

A :class:`NodeDef` answers the two questions every other module asks about a node type:
which sockets it has, and which values it persists in ``widgets_values`` (in order,
including the pseudo-widgets the frontend adds). Building one from ``/object_info``,
from a frontend snapshot or from a widget template is the job of the loaders in this
package; everything downstream reads only these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Tier(str, Enum):
    """Where a definition came from, most trusted first."""

    TRUTH = "truth"  # instantiated by a real frontend
    TEMPLATES = "templates"  # learned from saved canvases or curated by hand
    DERIVED = "derived"  # inferred from /object_info with the frontend's rules
    BUILTIN = "builtin"  # frontend-only virtual node (notes, legacy reroute, primitive)


class WidgetKind(str, Enum):
    VALUE = "value"  # the widget of an input declared by the backend
    CONTROL = "control_after_generate"  # mode combo added after seed-like widgets
    FILTER = "control_filter_list"  # filter string added after a controlled combo
    UPLOAD = "upload"  # upload button added after a media combo
    EXTRA = "extra"  # added by a frontend extension; known only from truth or templates
    COMPANION = "companion"  # persisted widget the widget constructor adds next to the value widget


class Behavior(str, Enum):
    """What a frontend-only node does (``nodes.<type>.behavior`` in the builtins data)."""

    NOTE = "note"  # text on the canvas, no sockets
    PASSTHROUGH = "passthrough"  # legacy reroute: forwards its one input
    PRIMITIVE = "primitive"  # pushes its value into the widgets it is linked to


DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"
AUTOGROW = "COMFY_AUTOGROW_V3"
MATCHTYPE = "COMFY_MATCHTYPE_V3"
DYNAMIC_GROUP = "COMFY_DYNAMICGROUP_V3"


@dataclass(frozen=True)
class InputSpec:
    """One declared input, in declaration order (required first, then optional)."""

    name: str
    type: str  # "COMBO" for list-form combos; socket types such as "MODEL" otherwise
    required: bool
    widget: bool  # True: edited as a widget (and still linkable); False: link-only socket
    options: Mapping[str, Any] = field(default_factory=dict)
    combo_values: tuple[Any, ...] | None = None
    has_default: bool = False  # the definition carries an explicit "default" key
    default: Any = None
    socketless: bool = False  # a widget without an input slot
    # DynamicCombo: option key -> the sub-inputs that appear when it is selected.
    dynamic_options: tuple[tuple[str, tuple[InputSpec, ...]], ...] = ()
    autogrow: AutogrowSpec | None = None  # set for COMFY_AUTOGROW_V3 inputs


@dataclass(frozen=True)
class AutogrowSpec:
    """Slots ``<group>.<names[i]>`` or ``<group>.<prefix><i>``; link-only, never widgets."""

    template: tuple[InputSpec, ...]
    names: tuple[str, ...] | None
    prefix: str | None
    min: int
    max: int

    def slot_name(self, group: str, ordinal: int, template_key: str) -> str:
        if self.names is not None:
            return f"{group}.{self.names[ordinal]}"
        base = self.prefix if len(self.template) == 1 and self.prefix is not None else template_key
        return f"{group}.{base}{ordinal}"


@dataclass(frozen=True)
class WidgetSpec:
    """One slot of ``widgets_values`` (widgets with ``serialize !== false``)."""

    name: str | None  # None only for EXTRA widgets learned without a name
    kind: WidgetKind
    input_name: str | None  # the InputSpec this widget belongs to
    default: Any = None
    to_prompt: bool = True  # False when options.serialize is false (kept out of the API prompt)


@dataclass(frozen=True)
class SlotSpec:
    """One entry of a node's ``inputs`` array, in the frontend's slot order."""

    name: str
    type: str
    widget: str | None = None  # the widget name for a widget-backed slot
    optional: bool = False  # drawn hollow (an optional input)
    match: str | None = None  # template id of a match-type (COMFY_MATCHTYPE_V3) input


@dataclass(frozen=True)
class OutputSpec:
    name: str
    type: str
    is_list: bool = False
    match: str | None = None  # template id whose linked input type this output takes


@dataclass(frozen=True)
class PackInfo:
    cnr_id: str | None = None
    ver: str | None = None
    aux_id: str | None = None


@dataclass(frozen=True)
class NodeDef:
    type: str
    tier: Tier
    inputs: tuple[InputSpec, ...]
    outputs: tuple[OutputSpec, ...]
    widgets: tuple[WidgetSpec, ...]  # static layout; see widget_layout() for dynamic combos
    display_name: str = ""
    category: str = ""
    python_module: str = ""
    output_node: bool = False
    virtual: bool = False  # frontend-only node, never part of the prompt
    pack: PackInfo | None = None
    # False when the widget layout cannot be derived reliably (a frontend extension
    # patches this type, or it uses an input kind derive does not model). Compile then
    # needs a truth or template entry for the type.
    layout_complete: bool = True
    # The ``inputs`` array of a freshly created node when a real frontend recorded it
    # (TRUTH and BUILTIN); None means derive.slot_layout replays it from ``inputs``.
    slots: tuple[SlotSpec, ...] | None = None
    # Classic-renderer size of a freshly created node, when a real frontend recorded it.
    size: tuple[float, float] | None = None
    behavior: Behavior | None = None  # set for frontend-only (BUILTIN) nodes

    @property
    def has_dynamic(self) -> bool:
        """Some input is a DynamicCombo, so the layout depends on the selected values."""
        return any(spec.dynamic_options for spec in self.inputs)

    @property
    def names_complete(self) -> bool:
        """Every widget slot has a known name, so widgets_values_named may be written."""
        return self.layout_complete and all(w.name is not None for w in self.widgets)

    def input(self, name: str) -> InputSpec | None:
        for spec in self.inputs:
            if spec.name == name:
                return spec
        return None

    def output_index(self, name: str) -> list[int]:
        """Indexes of outputs called ``name`` (by name or by type); more than one is ambiguous."""
        by_name = [i for i, out in enumerate(self.outputs) if out.name == name]
        if by_name:
            return by_name
        return [i for i, out in enumerate(self.outputs) if out.type == name]


# ``NodeDef.widgets`` is the layout with no DynamicCombo sub-inputs selected. The layout
# for concrete values (sub-widgets inserted right after their combo's own slots, in
# option order, named ``<combo>.<sub>``) comes from ``cwc.nodedefs.derive.widget_layout``.
