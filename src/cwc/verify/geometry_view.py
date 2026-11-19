"""One canvas read once for every geometry check: node boxes, anchors and curves.

The view holds what :mod:`cwc.canvas.envelope`, :mod:`cwc.canvas.links` and
:mod:`cwc.canvas.reroutes` derive from the canvas and the node definitions, so G1-G10 share one reading and never look at layout
internals.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from cwc.canvas.envelope import BoxFacts, Point, Renderer, box_facts, classic_box, envelope, vue_box
from cwc.canvas.geometry import Rect
from cwc.canvas.links import SEGMENTS, Spline, canvas_splines
from cwc.canvas.model import Canvas, Node, id_order
from cwc.canvas.reroutes import LinkPath, Reroutes, paths_between
from cwc.prompt.slots import DefLookup


@dataclass(frozen=True)
class GeometryView:
    canvas: Canvas
    nodes: tuple[Node, ...]  # id order
    facts: dict[str, BoxFacts]
    envelopes: dict[str, Rect]
    boxes: dict[Renderer, dict[str, Rect]]
    splines: dict[Renderer, dict[int, Spline]]  # drawable links by link id, anchor to anchor (G5)
    paths: dict[Renderer, dict[int, LinkPath]]  # the same links as drawn, through their reroutes (G2, G10)

    @classmethod
    def build(cls, canvas: Canvas, defs: DefLookup) -> GeometryView:
        facts = {node.key: box_facts(node, defs.get(node.type)) for node in canvas.nodes}
        return cls.from_facts(canvas, facts)

    @classmethod
    def from_facts(cls, canvas: Canvas, facts: dict[str, BoxFacts]) -> GeometryView:
        """The view for given box facts (``build`` derives them from the node definitions; a test
        passes measured ones)."""
        nodes = tuple(sorted(canvas.nodes, key=lambda node: id_order(node.key)))
        boxes = {
            Renderer.CLASSIC: {node.key: classic_box(node, facts[node.key]) for node in nodes},
            Renderer.VUE: {node.key: vue_box(node, facts[node.key]) for node in nodes},
        }
        splines = {renderer: canvas_splines(canvas, facts, renderer) for renderer in Renderer}
        # link_paths(canvas, facts, renderer) is paths_between over the end anchors of these same
        # splines (same links, same order); passing them saves finding every anchor a second time,
        # and the reroute table is read once for both renderers
        table = Reroutes.read(canvas)
        paths = {
            renderer: paths_between(canvas, {link_id: (curve.p0, curve.p3) for link_id, curve in splines[renderer].items()}, table=table)
            for renderer in Renderer
        }
        return cls(
            canvas=canvas,
            nodes=nodes,
            facts=facts,
            envelopes={node.key: envelope(node, facts[node.key]) for node in nodes},
            boxes=boxes,
            splines=splines,
            paths=paths,
        )

    @functools.cached_property
    def samples(self) -> dict[Renderer, dict[int, tuple[list[Point], ...]]]:
        """Each drawn path of :attr:`paths`, every segment sampled at ``SEGMENTS`` steps
        (:meth:`cwc.canvas.links.Spline.sample`). A segment drawn alike under both renderers, as
        the legs between two reroutes are, is sampled once and its list shared; readers do not
        change the lists."""
        seen: dict[Spline, list[Point]] = {}
        out: dict[Renderer, dict[int, tuple[list[Point], ...]]] = {}
        for renderer in Renderer:
            per_link: dict[int, tuple[list[Point], ...]] = {}
            # links of a bus tree hold the same segment objects (paths_between): look those up by
            # identity before hashing the curve
            same: dict[int, list[Point]] = {}
            for link_id, path in self.paths[renderer].items():
                parts = []
                for segment in path.segments:
                    points = same.get(id(segment))
                    if points is None:
                        points = seen.get(segment)
                        if points is None:
                            points = seen[segment] = segment.sample(SEGMENTS)
                        same[id(segment)] = points
                    parts.append(points)
                per_link[link_id] = tuple(parts)
            out[renderer] = per_link
        return out
