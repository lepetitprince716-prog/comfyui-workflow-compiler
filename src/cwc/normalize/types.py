"""What normalize hands to the rest of the compile pipeline.

The authoring document is a strict API prompt plus five relaxations (symbolic ids,
outputs by name, omitted inputs that declare a default, ``_meta`` intents and a
top-level ``_workflow`` key). Normalize turns it into :class:`Authoring`: two strict
prompts (all-enabled and factory state), the id mapping and the parsed intents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from cwc.nodedefs.types import NodeDef
from cwc.prompt.model import Prompt
from cwc.report import Finding


class Role(str, Enum):
    COCKPIT = "cockpit"
    PIPELINE = "pipeline"
    PLUMBING = "plumbing"


class SeedMode(str, Enum):
    FIXED = "fixed"
    RANDOMIZE = "randomize"


@dataclass(frozen=True)
class NodeIntent:
    """The ``_meta`` keys an author may set on a node. All optional."""

    title: str | None = None
    ui_group: str | None = None
    ui_role: Role | None = None
    ui_optional: bool = False
    ui_locked: tuple[str, ...] = ()  # widget names; ("*",) when the author wrote true
    ui_note: str | None = None
    ui_order: int | None = None
    ui_expose: tuple[str, ...] = ()
    ui_seed: SeedMode | None = None


@dataclass(frozen=True)
class GroupInfo:
    title: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ModelSource:
    name: str
    url: str
    directory: str


@dataclass(frozen=True)
class WorkflowInfo:
    """The top-level ``_workflow`` key."""

    format: int = 1
    title: str | None = None
    locale: str = "en"
    preset: str | None = None
    groups: dict[str, GroupInfo] = field(default_factory=dict)
    models: tuple[ModelSource, ...] = ()


@dataclass
class Authoring:
    full: Prompt  # strict prompt with every node enabled
    factory: Prompt  # strict prompt with ui_optional nodes bypassed (what <name>.api.json holds)
    ids: dict[str, str]  # authoring id -> numeric prompt id
    defs: dict[str, NodeDef]  # numeric prompt id -> resolved definition
    intents: dict[str, NodeIntent]  # numeric prompt id -> intent
    workflow: WorkflowInfo
    widget_values: dict[str, dict[str, Any]] = field(default_factory=dict)
    # numeric id -> every persisted widget value by widget name, defaults filled in
    # (pseudo-widgets included), ready for instantiate to lay out in widget order.
    notices: list[Finding] = field(default_factory=list)
    # the E8 info finding on keys accepted but without effect yet, if any; it goes into
    # the report of a successful compile, apart from the warnings normalize returns
