"""Derive a :class:`NodeDef` from one ``/object_info`` entry with the frontend's rules.

The rules follow ComfyUI frontend 1.59.1 creating a node with ``LiteGraph.createNode``:

* Input order: the ``required`` keys, then the ``optional`` keys (an optional key that is
  also required keeps the required position and becomes optional), enumerated the way a
  JavaScript object is (canonical integer keys first, ascending). With ``input_order``,
  the listed required names come first, then the listed optional names, then the rest in
  object order. Media upload buttons are appended to ``required`` before ordering, so they
  land after every listed input.
* An input is a widget when ``widgetType ?? type`` is a registered widget type
  (``widget_types`` in ``src/cwc/data/frontend_builtins.json``) and ``forceInput`` is not
  JS-truthy. Every other input is a link-only socket.
* Slots: the frontend first adds the sockets of all link-only inputs, then each widget
  together with its slot (none for a ``socketless`` input). ``NodeDef.inputs`` keeps the declaration order above; the real
  ``inputs`` array order comes from :func:`slot_layout`, which replays that sequence
  (Autogrow groups, MatchType types and DynamicCombo sub-inputs included).
* ``NodeDef.widgets`` is the ``widgets_values`` layout with no DynamicCombo sub-inputs;
  :func:`widget_layout` inserts the sub-widgets of the selected options.

Types a frontend extension patches by name (``patched_types``) and widget types derive
cannot reproduce (``exact: false``) yield ``layout_complete=False``: compile then needs a
truth or template entry for the type.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Union

from cwc.nodedefs._frontend import Control, Frontend, WidgetType, frontend
from cwc.nodedefs.types import (
    AUTOGROW,
    DYNAMIC_COMBO,
    MATCHTYPE,
    AutogrowSpec,
    InputSpec,
    NodeDef,
    OutputSpec,
    SlotSpec,
    Tier,
    WidgetKind,
    WidgetSpec,
)

__all__ = ["from_object_info", "widget_layout", "slot_layout", "match_widget", "WidgetValues"]

# widgets_values as saved (positional) or a mapping from widget name to value.
WidgetValues = Union[Sequence[Any], Mapping[str, Any]]

_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")
_MAX_ARRAY_INDEX = 2**32 - 2
_AUTOGROW_MIN = 1
_AUTOGROW_MAX = 100
# Limits on an Autogrow group, so a hostile definition cannot make cwc build millions of slots.
# The bundled core nodes go up to 100 ordinals and a template of a few inputs; a ``max`` or
# ``min`` past MAX_AUTOGROW, or more than MAX_AUTOGROW_SLOTS slots in one group, is refused.
MAX_AUTOGROW = 1000
MAX_AUTOGROW_SLOTS = 10000


# --- JavaScript semantics -------------------------------------------------------------


def _js_truthy(value: Any) -> bool:
    """``Boolean(value)`` in JavaScript: empty lists and objects are true."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == value and value != 0  # NaN and 0 are false
    if isinstance(value, str):
        return value != ""
    return True


def _js_keys(mapping: Mapping[str, Any]) -> list[str]:
    """Key order of a JavaScript object: canonical array indexes ascending, then insertion order."""
    indexes = sorted(
        (k for k in mapping if _ARRAY_INDEX.fullmatch(k) and int(k) <= _MAX_ARRAY_INDEX), key=int
    )
    taken = set(indexes)
    return indexes + [k for k in mapping if k not in taken]


# --- parsing one input spec -----------------------------------------------------------


class _Parse:
    """Parses the inputs of one node type and collects why its layout is not reliable."""

    def __init__(self, type_name: str, fe: Frontend):
        self.type_name = type_name
        self.fe = fe
        self.incomplete: list[str] = []

    def _bad(self, message: str) -> ValueError:
        return ValueError(f"object_info {self.type_name!r}: {message}")

    def split(self, name: str, raw: Any) -> tuple[str, dict[str, Any], tuple[Any, ...] | None]:
        """``(type, options, combo values)`` of a V1 input spec."""
        if not isinstance(raw, (list, tuple)) or not raw:
            raise self._bad(f"input {name!r} is not a non-empty list")
        head = raw[0]
        opts = raw[1] if len(raw) > 1 and isinstance(raw[1], dict) else {}
        if isinstance(head, (list, tuple)):
            return "COMBO", opts, tuple(head)
        if not isinstance(head, str):
            raise self._bad(f"input {name!r} has type {head!r}")
        if head == "COMBO":
            listed = opts.get("options")
            return head, opts, tuple(listed) if isinstance(listed, (list, tuple)) else ()
        return head, opts, None

    def input(self, name: str, raw: Any, required: bool) -> InputSpec:
        type_, opts, combo = self.split(name, raw)
        key = _widget_key(type_, opts)
        wtype = self.fe.widget_types.get(key)
        widget = wtype is not None and not _js_truthy(opts.get("forceInput"))
        if not widget and type_ == MATCHTYPE:
            template = opts.get("template")
            allowed = template.get("allowed_types") if isinstance(template, dict) else None
            return self.input(name, [allowed if isinstance(allowed, str) else "*", opts], required)
        autogrow = self.autogrow(name, opts) if not widget and type_ == AUTOGROW else None
        dynamic: tuple[tuple[str, tuple[InputSpec, ...]], ...] = ()
        if widget:
            assert wtype is not None
            if not wtype.exact:
                self.incomplete.append(f"{name}: widget type {key} is not modelled")
            if key == DYNAMIC_COMBO:
                dynamic = self.dynamic_options(name, opts)
                combo = tuple(k for k, _ in dynamic)
            if _js_truthy(opts.get("multi_select")) and _control_applies(name, opts, wtype.control, self.fe):
                self.incomplete.append(f"{name}: multi-select combo with value control")
        return InputSpec(
            name=name,
            type=type_,
            required=required,
            widget=widget,
            options=dict(opts),
            combo_values=combo,
            has_default="default" in opts,
            default=opts.get("default"),
            socketless=widget and wtype is not None and not wtype.slot,
            dynamic_options=dynamic,
            autogrow=autogrow,
        )

    def sections(self, prefix: str, inputs: Any) -> tuple[InputSpec, ...]:
        """``required`` then ``optional`` sub-inputs of a dynamic input, in JS key order."""
        if not isinstance(inputs, dict):
            return ()
        out: list[InputSpec] = []
        for section, required in (("required", True), ("optional", False)):
            entries = inputs.get(section) or {}
            for key in _js_keys(entries):
                out.append(self.input(prefix + key, entries[key], required))
        return tuple(out)

    def dynamic_options(self, name: str, opts: Mapping[str, Any]) -> tuple[tuple[str, tuple[InputSpec, ...]], ...]:
        options = opts.get("options")
        if not isinstance(options, list) or not all(isinstance(o, dict) and isinstance(o.get("key"), str) for o in options):
            self.incomplete.append(f"{name}: unparseable dynamic combo")
            return ()
        result = tuple((o["key"], self.sections(f"{name}.", o.get("inputs"))) for o in options)
        if result:
            first = result[0][1]
            if any(not c.widget for c in first) and not any(c.widget for c in first):
                # The frontend throws while creating such a node (no insertion point).
                self.incomplete.append(f"{name}: default option adds sockets only")
        return result

    def _count(self, name: str, key: str, value: Any) -> int:
        """An Autogrow ``min`` or ``max``: a finite number, where JavaScript reads a falsy one as 0."""
        if not _js_truthy(value):
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise self._bad(f"autogrow {name!r}: {key} is {value!r}, not a number")
        if abs(value) > MAX_AUTOGROW:
            raise self._bad(f"autogrow {name!r}: {key} is {value!r}; the limit is {MAX_AUTOGROW}")
        return int(value)

    def autogrow(self, name: str, opts: Mapping[str, Any]) -> AutogrowSpec | None:
        template = opts.get("template")
        if not isinstance(template, dict) or not isinstance(template.get("input"), dict):
            self.incomplete.append(f"{name}: unparseable autogrow")
            return None
        specs = self.sections("", template["input"])
        if not specs:
            self.incomplete.append(f"{name}: autogrow without template inputs")
            return None
        if any(s.widget for s in specs):
            self.incomplete.append(f"{name}: autogrow template with widgets")
        names = template.get("names")
        names = tuple(str(n) for n in names) if isinstance(names, list) else None
        prefix = template.get("prefix")
        minimum = template.get("min", _AUTOGROW_MIN)
        maximum = len(names) if names is not None else template.get("max", _AUTOGROW_MAX)
        grow = AutogrowSpec(
            template=specs,
            names=names,
            prefix=prefix if isinstance(prefix, str) else "",  # the frontend defaults prefix to ''
            min=self._count(name, "min", minimum),
            max=self._count(name, "max", maximum),
        )
        if len(specs) * grow.max > MAX_AUTOGROW_SLOTS:
            raise self._bad(
                f"autogrow {name!r}: {len(specs)} template inputs times max {grow.max} is more than {MAX_AUTOGROW_SLOTS} slots"
            )
        return grow


def _widget_key(type_: str, opts: Mapping[str, Any]) -> str:
    """``widgetType ?? type``."""
    override = opts.get("widgetType")
    return type_ if override is None else str(override)


def _ordered_inputs(type_name: str, info: Mapping[str, Any], fe: Frontend) -> list[tuple[str, Any, bool]]:
    """``(name, raw spec, optional)`` in the order the frontend creates them."""
    section = info.get("input") or {}
    if not isinstance(section, dict):
        raise ValueError(f"object_info {type_name!r}: input is not an object")
    required = section.get("required") or {}
    optional = section.get("optional") or {}
    if not isinstance(required, dict) or not isinstance(optional, dict):
        raise ValueError(f"object_info {type_name!r}: required/optional is not an object")
    required = dict(required)
    _append_upload(required, fe)

    merged: dict[str, tuple[Any, bool]] = {}
    for key in _js_keys(required):
        merged[key] = (required[key], False)
    for key in _js_keys(optional):
        merged[key] = (optional[key], True)  # an existing key keeps its position
    names = _js_keys(merged)

    order = info.get("input_order")
    if not _js_truthy(order) or not isinstance(order, dict):
        return [(n, *merged[n]) for n in names]
    picked: list[str] = []
    for section, want_optional in (("required", False), ("optional", True)):
        listed = order.get(section) or []
        if not isinstance(listed, list) or not all(isinstance(n, str) for n in listed):
            raise ValueError(f"object_info {type_name!r}: input_order.{section} is not a list of names")
        for n in listed:
            if n in merged and merged[n][1] == want_optional:
                picked.append(n)
    seen = set(picked)
    picked += [n for n in names if n not in seen]
    return [(n, *merged[n]) for n in picked]


def _append_upload(required: dict[str, Any], fe: Frontend) -> None:
    """The image/video and audio upload extensions add ``upload`` to ``required``."""
    up = fe.upload
    for key in _js_keys(required):
        raw = required[key]
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        opts = raw[1] if len(raw) > 1 and isinstance(raw[1], dict) else None
        if not opts:
            continue
        is_combo = isinstance(raw[0], (list, tuple)) or raw[0] == "COMBO"
        if is_combo and any(opts.get(k) is True for k in up.media_keys):
            required[up.input_name] = [up.media_widget_type, {**opts, "imageInputName": key}]
            break
    audio = required.get(up.audio_input)
    if isinstance(audio, (list, tuple)) and len(audio) > 1 and isinstance(audio[1], dict):
        if audio[1].get(up.audio_key) is True:
            required[up.input_name] = [up.audio_widget_type, {}]


def _outputs(type_name: str, info: Mapping[str, Any]) -> tuple[OutputSpec, ...]:
    types = info.get("output") or []
    names = info.get("output_name") or []
    lists = info.get("output_is_list") or []
    matches = info.get("output_matchtypes") or []
    if not isinstance(types, list):
        raise ValueError(f"object_info {type_name!r}: output is not a list")
    for key, value in (("output_name", names), ("output_is_list", lists), ("output_matchtypes", matches)):
        if not isinstance(value, list):
            raise ValueError(f"object_info {type_name!r}: {key} is not a list")
    out = []
    for i, t in enumerate(types):
        name = names[i] if i < len(names) else None
        type_ = "COMBO" if isinstance(t, (list, tuple)) else str(t)
        out.append(
            OutputSpec(
                name=str(name) if _js_truthy(name) else f"output_{i}",
                type="*" if type_ == MATCHTYPE else type_,
                is_list=_js_truthy(lists[i]) if i < len(lists) else False,
                match=matches[i] if i < len(matches) and isinstance(matches[i], str) else None,
            )
        )
    return tuple(out)


def from_object_info(type_name: str, info: Mapping[str, Any]) -> NodeDef:
    """Build a DERIVED :class:`NodeDef`; raises ``ValueError`` when the entry is malformed."""
    if not isinstance(info, Mapping):
        raise ValueError(f"object_info {type_name!r}: entry is not an object")
    fe = frontend()
    parse = _Parse(type_name, fe)
    inputs = tuple(parse.input(n, raw, not opt) for n, raw, opt in _ordered_inputs(type_name, info, fe))
    widgets, _ = _Layout(fe, None, expand=False, assigned=False).run(inputs)
    complete = type_name not in fe.patched_types and not parse.incomplete
    return NodeDef(
        type=type_name,
        tier=Tier.DERIVED,
        inputs=inputs,
        outputs=_outputs(type_name, info),
        widgets=widgets,
        display_name=str(info.get("display_name") or ""),
        category=str(info.get("category") or ""),
        python_module=str(info.get("python_module") or ""),
        output_node=_js_truthy(info.get("output_node")),
        layout_complete=complete,
    )


# --- widget and slot layout -----------------------------------------------------------


def _control_applies(name: str, opts: Mapping[str, Any], control: Control | None, fe: Frontend) -> bool:
    if control is None:
        return False
    cag = opts.get("control_after_generate")
    if control.when == "spec_or_seed_name" and cag is None:
        return name in fe.value_control.seed_names
    return _js_truthy(cag)


def _name_override(value: Any, fe: Frontend) -> str | None:
    """A control_after_generate string that is not a mode names the control widget."""
    if not isinstance(value, str) or value.strip() == "" or value in fe.value_control.modes:
        return None
    return value


def _control_widgets(spec: InputSpec, control: Control, fe: Frontend) -> list[WidgetSpec]:
    vc = fe.value_control
    opts = spec.options
    cag = opts.get("control_after_generate")
    override = _name_override(cag, fe)
    if control.mode == "spec_string":
        mode = cag if isinstance(cag, str) else vc.fallback_mode
    else:
        mode = control.mode
    if not mode or mode == override:
        mode = vc.fallback_mode
    prefix = opts.get("control_prefix")
    name = override or (f"{prefix} {vc.control_name}" if _js_truthy(prefix) else vc.control_name)
    out = [WidgetSpec(name, WidgetKind.CONTROL, spec.name, mode, to_prompt=False)]
    if control.filter and not _js_truthy(opts.get("multi_select")):
        listed = opts.get("control_filter_list")
        if isinstance(listed, str):
            fname = listed
        else:
            fname = f"{prefix} {vc.filter_name}" if _js_truthy(prefix) else vc.filter_name
        out.append(WidgetSpec(fname, WidgetKind.FILTER, spec.name, "", to_prompt=False))
    return out


def _initial(spec: InputSpec, wt: WidgetType) -> Any:
    """The value a freshly created widget holds."""
    rule, default, opts = wt.default_rule, spec.default, spec.options
    if rule == "nullish":
        value = default if default is not None else wt.default
    elif rule == "truthy":
        value = default if _js_truthy(default) else wt.default
    elif rule == "combo":
        if _js_truthy(opts.get("multi_select")):
            value = wt.multi_select_default
        elif _js_truthy(default):
            value = default
        elif spec.combo_values:
            value = spec.combo_values[0]
        elif _js_truthy(opts.get("remote")):
            value = wt.remote_default
        else:
            value = None
    elif rule == "first_option":
        value = spec.combo_values[0] if spec.combo_values else None
    elif rule == "object":
        value = default if isinstance(default, dict) else wt.default
    elif rule == "features":
        if default is not None:
            value = default
        else:
            features = opts.get("features")
            wanted = features if isinstance(features, list) else list(wt.features_default)
            value = {k: v for k, v in (wt.feature_values or {}).items() if k in wanted}
    elif rule.startswith("option:"):
        picked = opts.get(rule.split(":", 1)[1])
        value = picked if picked is not None else wt.default
    else:  # "constant"
        value = wt.default
    return copy.deepcopy(value)


class _Layout:
    """Replays the frontend's widget and slot creation for one node."""

    def __init__(self, fe: Frontend, values: WidgetValues | None, *, expand: bool, assigned: bool):
        self.fe = fe
        self.values = values
        self.expand = expand
        self.assigned = assigned
        self.widgets: list[WidgetSpec] = []
        self.slots: list[SlotSpec] = []

    def run(self, inputs: Sequence[InputSpec]) -> tuple[tuple[WidgetSpec, ...], tuple[SlotSpec, ...]]:
        for spec in inputs:
            if not spec.widget:
                self.socket(spec)
        for spec in inputs:
            if spec.widget:
                self.widget(spec)
        return tuple(self.widgets), tuple(self.slots)

    def socket(self, spec: InputSpec) -> None:
        grow = spec.autogrow
        if grow is None:
            self.slots.append(SlotSpec(spec.name, spec.type, None, not spec.required, _match_id(spec)))
            return
        for ordinal in range(max(1, grow.min + 1)):
            if ordinal >= grow.max:
                break
            for t in grow.template:
                name = grow.slot_name(spec.name, ordinal, t.name)
                self.slots.append(SlotSpec(name, t.type, None, ordinal >= grow.min or not t.required, _match_id(t)))

    def selected(self, spec: InputSpec, position: int, default: Any) -> Any:
        values = self.values
        if isinstance(values, Mapping):
            return values.get(spec.name, default)
        if values is not None and not isinstance(values, (str, bytes)) and position < len(values):
            return values[position]
        return default

    def widget(self, spec: InputSpec) -> None:
        fe = self.fe
        key = _widget_key(spec.type, spec.options)
        wt = fe.widget_types[key]
        value = _initial(spec, wt)
        position = len(self.widgets)
        if wt.persist:
            upload = key in (fe.upload.media_widget_type, fe.upload.audio_widget_type)
            kind = WidgetKind.UPLOAD if upload else WidgetKind.VALUE
            self.widgets.append(WidgetSpec(spec.name, kind, spec.name, value, to_prompt=wt.to_prompt))
            for f in wt.followers:
                field_value = value.get(f.field) if isinstance(value, dict) else None
                self.widgets.append(WidgetSpec(f.name, WidgetKind.COMPANION, spec.name, field_value, f.to_prompt))
        remote = spec.options.get("remote")
        if isinstance(remote, dict) and not _js_truthy(spec.options.get("multi_select")):
            for c in wt.remote_companions:
                if c.requires == "remote" or _js_truthy(remote.get("refresh_button")):
                    self.widgets.append(WidgetSpec(c.name, WidgetKind.COMPANION, spec.name, copy.deepcopy(c.default), c.to_prompt))
        if _control_applies(spec.name, spec.options, wt.control, fe):
            assert wt.control is not None
            self.widgets.extend(_control_widgets(spec, wt.control, fe))

        own_slot = None if spec.socketless else SlotSpec(spec.name, spec.type, spec.name, not spec.required, _match_id(spec))
        children: tuple[InputSpec, ...] = ()
        if self.expand and spec.dynamic_options:
            chosen = self.selected(spec, position, value)
            children = dict(spec.dynamic_options).get(chosen, ()) if isinstance(chosen, str) else ()
        # A fresh node adds the default option's sub-inputs before the combo's own slot;
        # assigning a value afterwards re-inserts them right after it.
        if own_slot is not None and self.assigned:
            self.slots.append(own_slot)
        for child in children:
            if child.widget:
                self.widget(child)
            else:
                self.socket(child)
        if own_slot is not None and not self.assigned:
            self.slots.append(own_slot)


def match_widget(nodedef: NodeDef, name: str) -> WidgetSpec:
    """The widget called ``name`` in the derived layout; else a VALUE widget for the input
    of that name; else an EXTRA widget. Used to place widgets recorded by truth or templates."""
    for widget in nodedef.widgets:
        if widget.name == name:
            return widget
    if nodedef.input(name) is not None:
        return WidgetSpec(name, WidgetKind.VALUE, name)
    return WidgetSpec(name, WidgetKind.EXTRA, None)


def widget_layout(nodedef: NodeDef, values: WidgetValues | None = None) -> tuple[WidgetSpec, ...]:
    """The ``widgets_values`` layout once DynamicCombo values are chosen.

    ``values`` is the saved ``widgets_values`` list (read positionally as the layout
    grows) or a mapping from widget name to value. A combo without a value takes its
    first option, as a new node does; a value that is not an option adds no sub-widgets.
    Nodes without DynamicCombo inputs return ``nodedef.widgets``.
    """
    if not nodedef.has_dynamic:
        return nodedef.widgets
    widgets, _ = _Layout(frontend(), values, expand=True, assigned=False).run(nodedef.inputs)
    return widgets


def slot_layout(
    nodedef: NodeDef, values: WidgetValues | None = None, linked: Collection[str] = ()
) -> tuple[SlotSpec, ...]:
    """The node's ``inputs`` array in frontend order; the one source of slot order.

    With ``values=None`` this is a node right after ``createNode`` (the truth snapshot):
    a DynamicCombo's default sub-inputs come before the combo's own slot. With values (a
    node whose widget values were assigned, as on load) every DynamicCombo's sub-inputs
    follow its own slot.

    A recorded order (``nodedef.slots``, from truth or builtins) is used as is, unless
    values are given for a node with DynamicCombo inputs: then the order is replayed.

    ``linked`` names the slots that have a link. Every Autogrow group then grows the way
    the frontend grows it while links are connected: one slot per ordinal up to the last
    linked one, plus one empty slot after it (up to the group's maximum), placed after the
    group's last slot. With nothing linked a group keeps its fresh ``min + 1`` slots.
    """
    if nodedef.slots is not None and (values is None or not nodedef.has_dynamic):
        slots = nodedef.slots
    else:
        _, slots = _Layout(frontend(), values, expand=True, assigned=values is not None).run(nodedef.inputs)
    return _grow(nodedef, slots, frozenset(linked)) if linked else slots


def _grow(nodedef: NodeDef, slots: tuple[SlotSpec, ...], linked: frozenset[str]) -> tuple[SlotSpec, ...]:
    out = list(slots)
    for spec in _autogrow_inputs(nodedef.inputs, out):
        grow = spec.autogrow
        assert grow is not None
        names = [[grow.slot_name(spec.name, n, t.name) for t in grow.template] for n in range(grow.max)]
        used = [n for n, row in enumerate(names) if linked.intersection(row)]
        count = min(grow.max, max(grow.min + 1, used[-1] + 2 if used else 0))
        present = frozenset(slot.name for slot in out)
        missing = [
            SlotSpec(names[n][i], t.type, None, n >= grow.min or not t.required, _match_id(t))
            for n in range(count)
            for i, t in enumerate(grow.template)
            if names[n][i] not in present
        ]
        if missing:
            at = max(i for i, slot in enumerate(out) if slot.name.startswith(spec.name + ".")) + 1
            out[at:at] = missing
    return tuple(out)


def _autogrow_inputs(specs: Sequence[InputSpec], slots: Sequence[SlotSpec]) -> list[InputSpec]:
    """Autogrow inputs with at least one slot in ``slots`` (top level or a chosen sub-input)."""
    found: list[InputSpec] = []
    for spec in specs:
        if spec.autogrow is not None and any(slot.name.startswith(spec.name + ".") for slot in slots):
            found.append(spec)
        for _, children in spec.dynamic_options:
            found.extend(_autogrow_inputs(children, slots))
    return found


def _match_id(spec: InputSpec) -> str | None:
    """The template id of a match-type input (its ``template.template_id`` option)."""
    template = spec.options.get("template")
    template_id = template.get("template_id") if isinstance(template, Mapping) else None
    return template_id if isinstance(template_id, str) else None
