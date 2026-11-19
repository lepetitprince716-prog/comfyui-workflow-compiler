"""Annotate: the plan layout follows (groups, zones, node notes), from the graph and intents.

Contract types are in :mod:`cwc.annotate.types`; ``docs/ARCHITECTURE.md`` has the rules.
"""

from __future__ import annotations

from cwc.annotate.graph import GraphView
from cwc.annotate.infer import inferred
from cwc.annotate.kept import kept
from cwc.annotate.palette import node_colours
from cwc.annotate.types import Annotations
from cwc.canvas.model import Canvas
from cwc.normalize.types import Authoring
from cwc.prompt.slots import DefLookup

__all__ = ["annotate", "apply_notes"]


def annotate(canvas: Canvas, defs: DefLookup, authoring: Authoring | None, *, regroup: bool = False) -> Annotations:
    """The plan for ``canvas``. With ``authoring`` (compile) the groups are inferred from the
    graph and the intents. Without it (tidy) they are the canvas's own (``Source.KEPT``),
    unless ``regroup`` asks for inferred groups (tidy with ``regroup`` reads optional from a
    node's bypass mode, since there are no intents). Reads the canvas; writes nothing.

    Tidy without ``regroup`` raises :class:`cwc.report.CwcError` with exit code
    ``INPUT_ERROR`` and one ``G4`` finding per pair of groups that overlap without nesting
    while the centre of a node's classic or Vue box lies in both: such a node belongs to
    both groups in the frontend, so no layout can keep its groups and clear G4. The message
    asks for ``--regroup``."""
    view = GraphView(canvas, defs)
    if authoring is None and not regroup:
        return kept(view, canvas)
    return inferred(view, authoring)


def apply_notes(canvas: Canvas, annotations: Annotations) -> None:
    """Compile only, before layout: write each node's note (``flags.collapsed``, node colour,
    cockpit title) to the canvas. Tidy never calls it, because a tidy run may change only
    geometry."""
    for node in canvas.nodes:
        note = annotations.nodes.get(node.key)
        if note is None:
            continue
        node.set_collapsed(note.collapsed)
        if note.title is not None:
            node.set_title(note.title)
        if note.colour is not None:
            colours = node_colours(note.colour)
            node.set_colours(colours.color, colours.bgcolor)
