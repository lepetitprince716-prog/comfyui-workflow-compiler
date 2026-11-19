"""The SVG of a canvas, drawn from the same geometry the checks read.

Boxes come from :mod:`cwc.canvas.envelope` (classic solid, Vue dashed), anchors from
:func:`cwc.canvas.envelope.slot_anchors` and link paths from :func:`cwc.canvas.reroutes.link_paths`
(each segment drawn as the cubic the frontend draws; a segment several links share is drawn once, by
the first link in table order); nothing here measures anything itself. The output is deterministic: elements follow the order of
the canvas tables, numbers are rounded to two decimals, and no clock or random value is used.

Structure, back to front (every element has a stable ``id`` or ``class`` so tests and reviewers
can select it): ``rect.background``; ``rect#first-screen``; ``g#groups`` with one
``g.group#group-<index>`` per group; ``g#links`` with one ``path.link#link-<id>`` per drawable
link (class ``rerouted`` when it runs through reroutes; it holds only the segments no earlier
link drew); ``g#reroutes``; ``g#nodes`` with
one ``g.node#node-<id>`` per node (class ``bypassed`` for mode 4, ``collapsed`` for a collapsed
node) holding ``rect.classic``, ``rect.vue``, ``text.title``, ``circle.slot`` and ``text.widget``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

from cwc.canvas.envelope import BoxFacts, Point, Renderer, box_facts, classic_box, slot_anchors, title_text, vue_box
from cwc.canvas.geometry import TITLE_HEIGHT, Rect, bounding
from cwc.canvas.links import Spline
from cwc.canvas.model import Canvas, Node, NodeMode, id_order
from cwc.canvas.reroutes import LinkPath, Reroutes, SegmentKey, link_paths
from cwc.prompt.slots import DefLookup

FIRST_SCREEN = Rect(0, 0, 1600, 900)
MARGIN = 40
BACKGROUND = "#222222"  # the frontend's canvas clear colour
GROUP_COLOR = "#3f789e"  # the frontend's default group colour
TEXT = "#dddddd"
MUTED_TEXT = "#aaaaaa"
NODE_FILL = "#353535"
CLASSIC_STROKE = "#999999"
VUE_STROKE = "#66ccff"
LINK_STROKE = "#99aaaa"
SLOT_FILL = "#99cc99"
BYPASS_OPACITY = 0.4
GROUP_OPACITY = 0.2
TITLE_SIZE = 14
WIDGET_SIZE = 12
SLOT_RADIUS = 3.5
REROUTE_RADIUS = 4
DASH = "6 4"
FONT = "sans-serif"

_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")
# What XML 1.0 refuses in text: C0 controls other than tab, newline and carriage return, lone
# surrogates and the two non-characters at the end of the basic plane.
_NOT_XML = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")
_ENTITIES = {'"': "&quot;", "'": "&apos;"}


@dataclass(frozen=True)
class Rendering:
    """The SVG text and what was drawn."""

    svg: str
    view_box: tuple[float, float, float, float]
    groups: int
    nodes: int
    links: int  # drawn
    links_unresolved: int  # an end node or a slot is missing: not drawn
    reroutes: int  # reroute points drawn


def render_svg(canvas: Canvas, defs: DefLookup) -> str:
    """The SVG text for ``canvas``; deterministic, standard library only."""
    return render(canvas, defs).svg


def render(canvas: Canvas, defs: DefLookup) -> Rendering:
    nodes = sorted(canvas.nodes, key=lambda node: id_order(node.key))
    facts = {node.key: box_facts(node, defs.get(node.type)) for node in nodes}
    reroutes = Reroutes.read(canvas)
    shapes = _link_shapes(link_paths(canvas, facts, Renderer.CLASSIC))

    extent: list[Rect] = [FIRST_SCREEN]
    groups = canvas.groups
    extent.extend(group.bounding for group in groups)
    for node in nodes:
        extent.append(classic_box(node, facts[node.key]))
        extent.append(vue_box(node, facts[node.key]))
    for _, segments, _ in shapes:
        extent.extend(_points_rect(segment.sample()) for segment in segments)
    extent.extend(Rect(x, y, 0, 0) for x, y in reroutes.points.values())
    box = bounding(extent).inflate(MARGIN, MARGIN, MARGIN, MARGIN)

    body = [
        f'<rect class="background" x="{_n(box.x)}" y="{_n(box.y)}" width="{_n(box.w)}" height="{_n(box.h)}" fill="{BACKGROUND}"/>',
        f'<rect id="first-screen" x="0" y="0" width="{_n(FIRST_SCREEN.w)}" height="{_n(FIRST_SCREEN.h)}" '
        f'fill="none" stroke="{MUTED_TEXT}" stroke-width="2" stroke-dasharray="{DASH}"/>',
        '<g id="groups">',
        *(_group(index, group.title, group.bounding, group.raw.get("color")) for index, group in enumerate(groups)),
        "</g>",
        f'<g id="links" fill="none" stroke="{LINK_STROKE}" stroke-width="2" stroke-linejoin="round">',
        *(_link(link_id, segments, rerouted) for link_id, segments, rerouted in shapes),
        "</g>",
        f'<g id="reroutes" fill="{LINK_STROKE}">',
        *(
            f'<circle class="reroute" id="reroute-{rid}" cx="{_n(x)}" cy="{_n(y)}" r="{REROUTE_RADIUS}"/>'
            for rid, (x, y) in reroutes.points.items()
        ),
        "</g>",
        '<g id="nodes">',
        *(_node(node, facts[node.key], defs) for node in nodes),
        "</g>",
    ]
    view = (box.x, box.y, box.w, box.h)
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_n(box.x)} {_n(box.y)} {_n(box.w)} {_n(box.h)}" '
        f'width="{_n(box.w)}" height="{_n(box.h)}" font-family="{FONT}">'
    )
    svg = "\n".join([head, *body, "</svg>"]) + "\n"
    return Rendering(
        svg=svg,
        view_box=view,
        groups=len(groups),
        nodes=len(nodes),
        links=len(shapes),
        links_unresolved=len({link.id for link in canvas.links}) - len(shapes),
        reroutes=len(reroutes.points),
    )


# --- links ----------------------------------------------------------------------------


def _link_shapes(paths: dict[int, LinkPath]) -> list[tuple[int, list[Spline], bool]]:
    """Each drawable link as ``(id, segments, rerouted)``: the segments it draws itself, in order. A
    reroute's segment belongs to the first link that reaches it, as in the frontend; a link id listed
    twice is one link."""
    out: list[tuple[int, list[Spline], bool]] = []
    drawn: set[SegmentKey] = set()
    for path in paths.values():
        own = [segment for key, segment in zip(path.keys, path.segments) if key not in drawn]
        drawn.update(path.keys)
        out.append((path.link_id, own, path.rerouted))
    return out


def _link(link_id: int, segments: list[Spline], rerouted: bool) -> str:
    parts: list[str] = []
    end: Point | None = None
    for segment in segments:
        if segment.p0 != end:
            parts.append(f"M {_p(segment.p0)}")
        parts.append(f"C {_p(segment.c1)} {_p(segment.c2)} {_p(segment.p3)}")
        end = segment.p3
    cls = "link rerouted" if rerouted else "link"
    return f'<path class="{cls}" id="link-{link_id}" d="{" ".join(parts)}"/>'


# --- groups and nodes -----------------------------------------------------------------


def _group(index: int, title: str, rect: Rect, color: Any) -> str:
    fill = color if isinstance(color, str) and _COLOR.match(color) else GROUP_COLOR
    return (
        f'<g class="group" id="group-{index}">'
        f'<rect x="{_n(rect.x)}" y="{_n(rect.y)}" width="{_n(rect.w)}" height="{_n(rect.h)}" '
        f'fill="{fill}" fill-opacity="{GROUP_OPACITY}" stroke="{fill}" stroke-width="2"/>'
        f'<text x="{_n(rect.x + 10)}" y="{_n(rect.y + TITLE_HEIGHT - 8)}" font-size="{TITLE_SIZE}" fill="{TEXT}">{_text(title)}</text>'
        "</g>"
    )


def _node(node: Node, facts: BoxFacts, defs: DefLookup) -> str:
    x, y = node.pos
    classic, vue = classic_box(node, facts), vue_box(node, facts)
    classes = ["node"]
    if node.collapsed:
        classes.append("collapsed")
    dim = node.mode == NodeMode.BYPASS
    if dim:
        classes.append("bypassed")
    opacity = f' opacity="{BYPASS_OPACITY}"' if dim else ""
    parts = [
        f'<g class="{" ".join(classes)}" id="node-{_attr(node.key)}"{opacity}>',
        f"<title>{_text(f'{node.type} (id {node.key})')}</title>",
        _rect("classic", classic, f'fill="{NODE_FILL}" fill-opacity="0.85" stroke="{CLASSIC_STROKE}" stroke-width="1.5"'),
        _rect("vue", vue, f'fill="none" stroke="{VUE_STROKE}" stroke-width="1.5" stroke-dasharray="{DASH}"'),
        f'<text class="title" x="{_n(x + 10)}" y="{_n(y - 10)}" font-size="{TITLE_SIZE}" fill="{TEXT}">'
        f"{_text(title_text(node, defs.get(node.type)))}</text>",
    ]
    inputs, outputs = slot_anchors(node, facts, Renderer.CLASSIC)
    for kind, points in (("in", inputs), ("out", outputs)):
        for point in dict.fromkeys(points):  # a collapsed node anchors every slot at one point
            parts.append(f'<circle class="slot {kind}" cx="{_n(point[0])}" cy="{_n(point[1])}" r="{SLOT_RADIUS}" fill="{SLOT_FILL}"/>')
    if not node.collapsed:
        for name, offset in facts.classic_widgets.items():
            parts.append(
                f'<text class="widget" x="{_n(x + 15)}" y="{_n(y + offset + WIDGET_SIZE / 3)}" '
                f'font-size="{WIDGET_SIZE}" fill="{MUTED_TEXT}">{_text(name)}</text>'
            )
    parts.append("</g>")
    return "".join(parts)


def _rect(cls: str, rect: Rect, style: str) -> str:
    return f'<rect class="{cls}" x="{_n(rect.x)}" y="{_n(rect.y)}" width="{_n(rect.w)}" height="{_n(rect.h)}" {style}/>'


# --- numbers and text -----------------------------------------------------------------


def _points_rect(points: list[Point]) -> Rect:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _n(value: float) -> str:
    """A number with at most two decimals and no trailing zeros, ``-0`` written ``0``."""
    if not math.isfinite(value):
        raise ValueError(f"cannot draw a non-finite coordinate: {value}")
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _p(point: Point) -> str:
    return f"{_n(point[0])} {_n(point[1])}"


def _clean(text: str) -> str:
    return _NOT_XML.sub("", text)


def _text(text: str) -> str:
    """Element text: characters XML cannot hold dropped, ``& < > " '`` escaped."""
    return escape(_clean(text), _ENTITIES)


def _attr(text: str) -> str:
    """Attribute value: as :func:`_text`, with white space written as spaces."""
    return _text(re.sub(r"\s", " ", text))
