"""Link checks: G2 (links through nodes), G5 (links drawn right to left) and G10 (mean length).

Anchors and curves come from :mod:`cwc.canvas.links`, paths through reroutes from
:mod:`cwc.canvas.reroutes`. G2 tests each link, segment by segment, under both renderers; G10
measures the classic paths. G5 judges a link by its two end anchors only, whatever reroutes lie
between them: a reroute is a waypoint of the route, not a different place for the link to end.
"""

from __future__ import annotations

import math
from typing import Any

from cwc.canvas.direction import G5_SHARE, direction
from cwc.canvas.envelope import Point, Renderer
from cwc.canvas.box_index import BoxIndex
from cwc.canvas.model import Link, Node, id_order
from cwc.canvas.reroutes import LinkPath
from cwc.report import Code, Finding, Severity
from cwc.verify.geometry_view import GeometryView
from cwc.verify.policy import Policy

LARGE_CANVAS = 100  # nodes; above it G2 allows a share of the links
G2_SHARE = 0.02
EPSILON = 1e-6  # a curve within this of a box edge is on the edge, not inside the box


def g2_limit(nodes: int, links: int) -> int:
    return 0 if nodes <= LARGE_CANVAS else int(links * G2_SHARE)


def link_crossings(view: GeometryView, renderer: Renderer, links: list[Link]) -> dict[int, tuple[str, ...]]:
    """For each drawable link in ``links``: the keys of the nodes (id order) whose open box under
    ``renderer`` any segment of its path crosses, the link's own ends excluded. Links with nothing
    crossed are left out."""
    boxes = view.boxes[renderer]
    index = BoxIndex([(node.key, boxes[node.key].inflate(-EPSILON, -EPSILON, -EPSILON, -EPSILON)) for node in view.nodes])
    order = {node.key: i for i, node in enumerate(view.nodes)}
    samples = view.samples[renderer]
    # The polyline of a link is its sampled segments joined end to start (LinkPath.points), so the
    # boxes it crosses are the union of those each segment and each join crosses, its ends taken out
    # after. Links through the same reroutes share segment lists (GeometryView.samples) and joins,
    # which are looked up once.
    by_part: dict[int, tuple[str, ...]] = {}
    by_join: dict[tuple[Point, Point], tuple[str, ...]] = {}
    out: dict[int, tuple[str, ...]] = {}
    for link in links:
        parts = samples.get(link.id)
        if parts is None:
            continue
        found: set[str] = set()
        previous = None
        for part in parts:
            if previous is not None:
                join = (previous[-1], part[0])
                got = by_join.get(join)
                if got is None:
                    got = by_join[join] = index.crossed(join)
                found.update(got)
            got = by_part.get(id(part))
            if got is None:
                got = by_part[id(part)] = index.crossed(part)
            found.update(got)
            previous = part
        found.discard(str(link.origin_id))
        found.discard(str(link.target_id))
        if found:
            out[link.id] = tuple(sorted(found, key=order.__getitem__))
    return out


def _collapsed_end(link: Link, nodes: dict[str, Node]) -> bool:
    return any(key in nodes and nodes[key].collapsed for key in (str(link.origin_id), str(link.target_id)))


def check_crossings(view: GeometryView, policy: Policy) -> tuple[list[Finding], dict[str, Any]]:
    """G2. A link counts when any segment of its path crosses a node box under either renderer
    (reroutes at the default spline offset); in beautify mode a crossing whose crossed nodes are all
    pinned does not count."""
    links = view.canvas.links
    nodes = {node.key: node for node in view.nodes}
    per_renderer = {renderer: link_crossings(view, renderer, links) for renderer in Renderer}
    drawable = [link for link in links if link.id in view.paths[Renderer.CLASSIC]]
    counted = []
    for link in drawable:
        classic = per_renderer[Renderer.CLASSIC].get(link.id, ())
        vue = per_renderer[Renderer.VUE].get(link.id, ())
        crossed = sorted(set(classic) | set(vue), key=id_order)
        if not crossed:
            continue
        if not policy.strict and all(nodes[key].pinned for key in crossed):
            continue
        counted.append((link, classic, vue, crossed))
    limit = g2_limit(len(view.nodes), len(drawable))  # 2 % of the links checked
    metrics = {
        "count": len(counted),
        "limit": limit,
        "links": len(links),
        "links_checked": len(drawable),
        "links_unresolved": len(links) - len(drawable),
        "crossing_classic": len(per_renderer[Renderer.CLASSIC]),
        "crossing_vue": len(per_renderer[Renderer.VUE]),
        "crossing_links": sorted(link.id for link, *_ in counted),
        "collapsed_end": sum(1 for link, *_ in counted if _collapsed_end(link, nodes)),
    }
    findings = []
    if len(counted) > limit:
        severity = Severity.ERROR if policy.strict else Severity.WARNING
        for link, classic, vue, crossed in counted:
            ends = (str(link.origin_id), str(link.target_id))
            findings.append(
                Finding(
                    Code.G2,
                    severity,
                    f"link {link.id} from node {ends[0]} to node {ends[1]} passes through node(s) {', '.join(crossed)}",
                    nodes=tuple(sorted(set(ends) | set(crossed), key=id_order)),
                    data={
                        "link": link.id,
                        "classic": list(classic),
                        "vue": list(vue),
                        "collapsed_end": _collapsed_end(link, nodes),
                    },
                )
            )
    return findings, metrics


def check_direction(
    view: GeometryView, policy: Policy, exempt: frozenset[int] = frozenset(), *, row_wraps: frozenset[int] = frozenset()
) -> tuple[list[Finding], dict[str, Any]]:
    """G5: the share of links whose output anchor lies right of the input anchor (classic
    anchors), feedback links, ``exempt`` links (those into the cockpit) and ``row_wraps``
    (downward cross-band links in a ``wrap_rows`` preset) left out. One finding when the share
    is over 5 %."""
    counted = direction(view.canvas, view.splines[Renderer.CLASSIC], exempt | row_wraps)
    backward, share = list(counted.backward), counted.share
    drawable = view.splines[Renderer.CLASSIC].keys()
    metrics = {
        "links": len(counted.considered),
        "right_to_left": len(backward),
        "share": round(share, 4),
        "limit": G5_SHARE,
        "feedback_links": len(counted.feedback),
        "cockpit_links": len(exempt & drawable),
        "right_to_left_links": backward,
    }
    if row_wraps:
        metrics["row_wrap_links"] = len(row_wraps & drawable)
    findings = []
    if counted.over:
        findings.append(
            Finding(
                Code.G5,
                Severity.ERROR if policy.strict else Severity.WARNING,
                f"{len(backward)} of {len(counted.considered)} links ({share:.1%}) are drawn right to left; the limit is {G5_SHARE:.0%}",
                data={"links": backward, "share": round(share, 4)},
            )
        )
    return findings, metrics


def path_length(path: LinkPath) -> float:
    """The sampled length of a link: the sum over its segments (one for a link without reroutes)."""
    return _sampled_length([segment.sample() for segment in path.segments], {})


def _sampled_length(parts, memo: dict[int, float]) -> float:
    """The sum over ``parts`` of each one's polyline length, in order. ``memo`` holds the length
    of each part list already measured, by identity: links through the same reroutes share
    them (GeometryView.samples), and a part's length is the same float however often it is summed."""
    total = 0.0
    for points in parts:
        length = memo.get(id(points))
        if length is None:
            length = memo[id(points)] = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        total += length
    return total


def mean_length(view: GeometryView) -> dict[str, Any]:
    """G10: the mean sampled length of the classic paths of every drawable link, a rerouted link
    counting the sum of its segments."""
    memo: dict[int, float] = {}
    lengths = [_sampled_length(parts, memo) for parts in view.samples[Renderer.CLASSIC].values()]
    mean = sum(lengths) / len(lengths) if lengths else 0.0
    return {"mean": round(mean, 3), "links": len(lengths)}
