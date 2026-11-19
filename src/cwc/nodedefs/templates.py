"""Widget templates: ``widgets_values`` layouts learned from saved canvases or written by hand.

A template lists the widget names of one node type in ``widgets_values`` order; ``null``
marks a slot whose name is unknown (the definition then has ``names_complete`` false).
Templates cover types without a truth entry, typically custom nodes whose frontend
extensions add widgets that ``/object_info`` does not describe.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from cwc.nodedefs.derive import match_widget
from cwc.nodedefs.types import NodeDef, PackInfo, Tier, WidgetKind, WidgetSpec

FORMAT = 1
SOURCES = ("curated", "learned")  # preference order when a type has several templates


@dataclass(frozen=True)
class Template:
    type: str
    widgets: tuple[str | None, ...]
    source: str
    pack: PackInfo


def _bad(index: int, message: str) -> ValueError:
    return ValueError(f"templates: entry {index}: {message}")


def _template(index: int, raw: Any) -> Template:
    if not isinstance(raw, dict):
        raise _bad(index, "not an object")
    type_name, widgets, source = raw.get("type"), raw.get("widgets"), raw.get("source")
    if not isinstance(type_name, str) or not type_name:
        raise _bad(index, "type must be a non-empty string")
    if not isinstance(widgets, list) or not all(w is None or isinstance(w, str) for w in widgets):
        raise _bad(index, "widgets must be a list of names or nulls")
    if source not in SOURCES:
        raise _bad(index, f"source must be one of {', '.join(SOURCES)}")
    pack, ver = raw.get("pack"), raw.get("ver")
    return Template(
        type_name,
        tuple(widgets),
        source,
        PackInfo(cnr_id=pack if isinstance(pack, str) else None, ver=ver if isinstance(ver, str) else None),
    )


def parse_templates(doc: Any) -> dict[str, tuple[Template, ...]]:
    """Every template of each type in preference order: curated before learned, then file
    order. The first is the type's default; :func:`by_version` picks per package version.
    Raises ``ValueError``."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or not isinstance(doc.get("templates"), list):
        raise ValueError(f"templates: expected format {FORMAT} with a templates list")
    entries = [_template(i, raw) for i, raw in enumerate(doc["templates"])]
    chosen: dict[str, list[Template]] = {}
    for entry in sorted(entries, key=lambda t: SOURCES.index(t.source)):  # stable: file order within a source
        chosen.setdefault(entry.type, []).append(entry)
    return {name: tuple(options) for name, options in chosen.items()}


def by_version(options: tuple[Template, ...]) -> dict[str, Template]:
    """The preferred template for each package version named by ``options``."""
    picked: dict[str, Template] = {}
    for template in options:
        if template.pack.ver is not None:
            picked.setdefault(template.pack.ver, template)
    return picked


def apply(template: Template, derived: NodeDef | None) -> NodeDef:
    """The TEMPLATES definition: widgets from the template, placed on the derived definition
    when ``/object_info`` knows the type, otherwise on an empty one (no ports).

    As with truth, a DynamicCombo type keeps the derived ``layout_complete``: its layout
    for chosen values is replayed from ``/object_info``.
    """
    base = derived or NodeDef(template.type, Tier.TEMPLATES, (), (), (), display_name=template.type)
    widgets = tuple(
        WidgetSpec(None, WidgetKind.EXTRA, None) if name is None else match_widget(base, name)
        for name in template.widgets
    )
    complete = base.layout_complete or not base.has_dynamic
    return replace(base, tier=Tier.TEMPLATES, widgets=widgets, pack=template.pack, layout_complete=complete)
