"""What each ``_meta`` key did to the compiled canvas, read from the plan and not from the intent.

``authoring_effects`` is the report's ``metrics["authoring_keys"]``; ``overridden_keys`` turns
the part of it that another key of the same node explains into warnings.
"""

from __future__ import annotations

from typing import Any

from cwc.annotate.infer import author_group
from cwc.annotate.types import AUTHOR_GROUP_PREFIX, Annotations, Zone
from cwc.canvas.model import Canvas, id_order
from cwc.normalize.types import Authoring, NodeIntent, Role
from cwc.report import Code, Finding, Severity

EFFECT_KEYS = ("title", "ui_group", "ui_role", "ui_optional", "ui_seed", "ui_order")


def _group_applies(intent: NodeIntent, canvas_id: str, plan: Annotations, intents: dict[str, NodeIntent]) -> bool:
    """Whether the node sits in the group its ``ui_group`` names: a group of its own, or an
    optional module that this group id names."""
    own = f"{AUTHOR_GROUP_PREFIX}{intent.ui_group}"
    for group in plan.groups:
        if canvas_id not in group.members:
            continue
        if group.key == own:
            return True
        return group.zone is Zone.OPTIONAL and author_group(group.key, group.zone, list(group.members), intents) == intent.ui_group
    return False


def _took_effect(
    key: str, intent: NodeIntent, canvas_id: str, plan: Annotations, titles: dict[str, str | None], intents: dict[str, NodeIntent]
) -> bool | None:
    """Whether the ``_meta`` key ``key`` changed the canvas for this node; None when not set.

    Each answer reads the compiled result, not the intent: ``title`` the node's title,
    ``ui_group`` the group the node ended up in (or the optional module the id names),
    ``ui_role`` the node's role in the plan, ``ui_optional`` the bypass, ``ui_order`` membership
    of the Controls group (the only place it sorts by). ``ui_seed`` is written into the widget
    whenever normalize accepted it.
    """
    note = plan.nodes[canvas_id]
    if key == "title":
        return None if intent.title is None else titles[canvas_id] == intent.title
    if key == "ui_group":
        return None if intent.ui_group is None else _group_applies(intent, canvas_id, plan, intents)
    if key == "ui_role":
        if intent.ui_role is None:
            return None
        return (note.role is Role.COCKPIT) == (intent.ui_role is Role.COCKPIT) and (
            intent.ui_role is not Role.PLUMBING or note.role is Role.PLUMBING
        )
    if key == "ui_optional":
        return note.optional if intent.ui_optional else None
    if key == "ui_order":
        return None if intent.ui_order is None else note.cockpit is not None
    if key == "ui_seed":
        return None if intent.ui_seed is None else True
    raise ValueError(key)


def authoring_effects(authoring: Authoring, plan: Annotations, canvas: Canvas) -> list[dict[str, Any]]:
    """``metrics["authoring_keys"]``: for each ``_meta`` key with an effect that some node sets,
    the authoring ids of the nodes it changed and, when any, of those it did not
    (``no_effect_on``). The keys without any effect yet are E8's, not listed here."""
    author = {prompt_id: author_id for author_id, prompt_id in authoring.ids.items()}
    titles = {key: node.title for key, node in canvas.node_map().items()}
    out: list[dict[str, Any]] = []
    for key in EFFECT_KEYS:
        changed: list[str] = []
        missed: list[str] = []
        for prompt_id, intent in authoring.intents.items():
            effect = _took_effect(key, intent, prompt_id, plan, titles, authoring.intents)
            if effect is not None:
                (changed if effect else missed).append(author[prompt_id])
        if changed or missed:
            entry: dict[str, Any] = {"key": f"_meta.{key}", "nodes": sorted(changed, key=id_order)}
            if missed:
                entry["no_effect_on"] = sorted(missed, key=id_order)
            out.append(entry)
    return out


def _overrider(key: str, intent: NodeIntent) -> str | None:
    """The key of the same node that wins over ``key`` (named by ``no_effect_on``), when one
    does: ``ui_optional`` keeps a node in its optional module, whatever its role; a ``ui_role`` of
    ``cockpit`` keeps a node in Controls, whatever its group; a ``ui_group`` keeps a node
    uncollapsed, whatever ``plumbing`` says. None when the cause is something else."""
    if key == "ui_group" and intent.ui_role is Role.COCKPIT and not intent.ui_optional:
        return "ui_role"
    if key == "ui_role" and intent.ui_role in (Role.COCKPIT, Role.PLUMBING):
        if intent.ui_optional:
            return "ui_optional"
        if intent.ui_role is Role.PLUMBING and intent.ui_group is not None:
            return "ui_group"
    return None


def _override_text(key: str, winner: str, intent: NodeIntent) -> tuple[str, str]:
    """The warning's message (which key loses to which) and its hint (how to write it)."""
    role = intent.ui_role.value if intent.ui_role is not None else ""
    if key == "ui_group":
        return (
            f"_meta.ui_group {intent.ui_group!r} is ignored: _meta.ui_role is 'cockpit' on the same node, which keeps it in the Controls group",
            "remove ui_group to keep the node in Controls, or remove ui_role to put it in the group",
        )
    if winner == "ui_group":
        return (
            f"_meta.ui_role 'plumbing' is ignored: _meta.ui_group is {intent.ui_group!r} on the same node, and a node in a named group is not collapsed",
            "remove ui_role, or remove ui_group to collapse the node",
        )
    wanted = "move the node into Controls" if role == "cockpit" else "collapse the node"
    return (
        f"_meta.ui_role {role!r} is ignored: _meta.ui_optional is true on the same node, which keeps it in its optional module",
        f"remove ui_role, or remove ui_optional to {wanted}",
    )


def overridden_keys(authoring: Authoring, effects: list[dict[str, Any]]) -> list[Finding]:
    """One E7 warning for each ``_meta`` key that another key of the same node overrides, from the
    ``no_effect_on`` lists of ``effects``. A key that did nothing for another reason is not warned
    about: the report still lists it, and the author gets no explanation cwc cannot back."""
    intents = {author: authoring.intents[prompt_id] for author, prompt_id in authoring.ids.items()}
    found: list[tuple[tuple[int, int | str], str, Finding]] = []
    for entry in effects:
        key = entry["key"].removeprefix("_meta.")
        for node in entry.get("no_effect_on", ()):
            winner = _overrider(key, intents[node])
            if winner is None:
                continue
            message, hint = _override_text(key, winner, intents[node])
            finding = Finding(
                Code.E7,
                Severity.WARNING,
                f"node {node!r}: {message}",
                (node,),
                hint,
                {"key": key, "overridden_by": winner},
            )
            found.append((id_order(node), key, finding))
    return [finding for _, _, finding in sorted(found, key=lambda item: item[:2])]
