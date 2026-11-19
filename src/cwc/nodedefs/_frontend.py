"""Frontend facts from ``src/cwc/data/frontend_builtins.json``, parsed and checked once.

The file records what ComfyUI frontend 1.59.1 does for each registered widget type, the
value-control widgets it adds, the upload buttons it appends and the node types whose
layout a frontend extension patches by name. Derivation reads only these objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from cwc.resources import read_json

BUILTINS_FILE = "frontend_builtins.json"

DEFAULT_RULES = frozenset({"nullish", "truthy", "constant", "combo", "object", "features", "first_option"})
CONTROL_WHEN = frozenset({"spec", "spec_or_seed_name"})


@dataclass(frozen=True)
class Follower:
    """A persisted companion widget that mirrors one field of the value widget."""

    name: str
    field: str
    to_prompt: bool


@dataclass(frozen=True)
class Control:
    when: str  # "spec": options.control_after_generate is JS-truthy; "spec_or_seed_name": ... ?? name in seed_names
    mode: str  # "spec_string": the option's string, else the fallback mode; otherwise a fixed mode
    filter: bool  # a control_filter_list widget follows the control widget


@dataclass(frozen=True)
class Companion:
    """A persisted widget a remote combo adds after itself."""

    name: str
    default: Any
    to_prompt: bool
    requires: str  # "remote": every remote combo; "refresh_button": remote.refresh_button is JS-truthy


@dataclass(frozen=True)
class WidgetType:
    persist: bool  # the value widget enters widgets_values
    to_prompt: bool  # the value widget enters the API prompt
    default: Any  # fallback initial value
    default_rule: str  # how the spec's default combines with the fallback (DEFAULT_RULES or "option:<key>")
    slot: bool = True  # the widget gets an input slot
    exact: bool = True  # False: derive cannot reproduce this widget's layout
    followers: tuple[Follower, ...] = ()
    control: Control | None = None
    remote_companions: tuple[Companion, ...] = ()
    remote_default: Any = None
    multi_select_default: Any = None
    features_default: tuple[str, ...] = ()
    feature_values: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ValueControl:
    modes: frozenset[str]
    fallback_mode: str
    seed_names: frozenset[str]
    control_name: str
    filter_name: str


@dataclass(frozen=True)
class Upload:
    media_keys: tuple[str, ...]
    media_widget_type: str
    audio_input: str
    audio_key: str
    audio_widget_type: str
    input_name: str


@dataclass(frozen=True)
class Frontend:
    widget_types: Mapping[str, WidgetType]
    value_control: ValueControl
    upload: Upload
    patched_types: frozenset[str]


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ValueError(f"{BUILTINS_FILE}: {message}")


def _widget_type(name: str, raw: Mapping[str, Any]) -> WidgetType:
    _require(isinstance(raw, dict), f"widget_types.{name} is not an object")
    rule = raw.get("default_rule")
    _require(
        isinstance(rule, str) and (rule in DEFAULT_RULES or rule.startswith("option:")),
        f"widget_types.{name}.default_rule {rule!r} is unknown",
    )
    followers = tuple(
        Follower(str(f["name"]), str(f["field"]), bool(f["to_prompt"])) for f in raw.get("followers", ())
    )
    persist = bool(raw["persist"])
    values = raw.get("values")
    _require(
        values == (1 + len(followers) if persist else 0),
        f"widget_types.{name}.values {values!r} disagrees with persist and followers",
    )
    control = None
    if "control" in raw:
        c = raw["control"]
        _require(c.get("when") in CONTROL_WHEN, f"widget_types.{name}.control.when is unknown")
        control = Control(str(c["when"]), str(c["mode"]), bool(c.get("filter", False)))
    companions = tuple(
        Companion(str(c["name"]), c["default"], bool(c["to_prompt"]), str(c["requires"]))
        for c in raw.get("remote_companions", ())
    )
    _require(
        all(c.requires in ("remote", "refresh_button") for c in companions),
        f"widget_types.{name}.remote_companions.requires is unknown",
    )
    return WidgetType(
        persist=persist,
        to_prompt=bool(raw["to_prompt"]),
        default=raw.get("default"),
        default_rule=rule,
        slot=bool(raw.get("slot", True)),
        exact=bool(raw.get("exact", True)),
        followers=followers,
        control=control,
        remote_companions=companions,
        remote_default=raw.get("remote_default"),
        multi_select_default=raw.get("multi_select_default"),
        features_default=tuple(raw.get("features_default", ())),
        feature_values=raw.get("feature_values"),
    )


def parse(raw: Any) -> Frontend:
    """Check and convert the parsed JSON document; raises ``ValueError`` on a bad shape."""
    _require(isinstance(raw, dict) and raw.get("format") == 1, "format must be 1")
    types = raw.get("widget_types")
    _require(isinstance(types, dict) and types, "widget_types is missing")
    vc = raw["value_control"]
    up = raw["upload"]
    patched = raw.get("patched_types")
    _require(isinstance(patched, list) and all(isinstance(p, str) for p in patched), "patched_types is not a list")
    return Frontend(
        widget_types={name: _widget_type(name, spec) for name, spec in types.items()},
        value_control=ValueControl(
            modes=frozenset(vc["modes"]),
            fallback_mode=str(vc["fallback_mode"]),
            seed_names=frozenset(vc["seed_names"]),
            control_name=str(vc["control_name"]),
            filter_name=str(vc["filter_name"]),
        ),
        upload=Upload(
            media_keys=tuple(up["media_keys"]),
            media_widget_type=str(up["media_widget_type"]),
            audio_input=str(up["audio_input"]),
            audio_key=str(up["audio_key"]),
            audio_widget_type=str(up["audio_widget_type"]),
            input_name=str(up["input_name"]),
        ),
        patched_types=frozenset(patched),
    )


@lru_cache(maxsize=1)
def frontend() -> Frontend:
    """The bundled frontend facts (parsed once per process)."""
    return parse(read_json(BUILTINS_FILE))
