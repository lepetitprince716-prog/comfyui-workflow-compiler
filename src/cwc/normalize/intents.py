"""Node ``_meta`` intents, and the purity rule for optional groups.

Unknown ``_meta`` keys are kept out of the strict prompt and reported as warnings (the
backend ignores them, but a misspelt ``ui_*`` key would silently do nothing).
"""

from __future__ import annotations

from typing import Any, Callable

from cwc.normalize.diagnostics import Findings
from cwc.normalize.types import NodeIntent, Role, SeedMode
from cwc.report import Code

LOCK_ALL = "*"


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _nonempty(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() != "" else None


def _boolean(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _names(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and all(isinstance(v, str) and v != "" for v in value):
        return tuple(dict.fromkeys(value))
    return None


def _locked(value: Any) -> tuple[str, ...] | None:
    if value is True:
        return (LOCK_ALL,)
    if value is False:
        return ()
    return _names(value)


def _role(value: Any) -> Role | None:
    return Role(value) if isinstance(value, str) and value in {r.value for r in Role} else None


def _seed(value: Any) -> SeedMode | None:
    return SeedMode(value) if isinstance(value, str) and value in {m.value for m in SeedMode} else None


# key -> (parser returning None on a bad value, expected-value text)
_KEYS: dict[str, tuple[Callable[[Any], Any], str]] = {
    "title": (_string, "a string"),
    "ui_group": (_nonempty, "a non-empty group id string"),
    "ui_role": (_role, " | ".join(r.value for r in Role)),
    "ui_optional": (_boolean, "true or false"),
    "ui_locked": (_locked, "true or a list of widget names"),
    "ui_note": (_string, "a string"),
    "ui_order": (_integer, "an integer"),
    "ui_expose": (_names, "a list of widget names"),
    "ui_seed": (_seed, " | ".join(m.value for m in SeedMode)),
}


def parse_intent(author_id: str, meta: dict[str, Any], out: Findings) -> NodeIntent:
    fields: dict[str, Any] = {}
    for key, value in meta.items():
        if key not in _KEYS:
            out.warning(
                Code.E7,
                f"node {author_id!r}: _meta key {key!r} is not known and is ignored",
                [author_id],
                hint=f"known keys: {', '.join(_KEYS)}",
            )
            continue
        parse, expected = _KEYS[key]
        parsed = parse(value)
        if parsed is None:
            out.error(Code.E7, f"node {author_id!r}: _meta.{key} must be {expected}, got {value!r}", [author_id])
            continue
        fields[key] = parsed
    return NodeIntent(**fields)


def check_group_purity(intents: dict[str, NodeIntent], out: Findings) -> None:
    """E7 when one ``ui_group`` holds both ``ui_optional`` and non-optional nodes.

    ``intents`` is keyed by author id.
    """
    members: dict[str, list[str]] = {}
    for author_id, intent in intents.items():
        if intent.ui_group is not None:
            members.setdefault(intent.ui_group, []).append(author_id)
    for group in sorted(members):
        ids = sorted(members[group])
        optional = [i for i in ids if intents[i].ui_optional]
        required = [i for i in ids if not intents[i].ui_optional]
        if optional and required:
            out.error(
                Code.E7,
                f"group {group!r} mixes optional nodes ({', '.join(optional)}) with non-optional nodes ({', '.join(required)})",
                ids,
                hint="an optional group must hold only ui_optional nodes so one bypass toggle wakes all of it; "
                "move the nodes to separate groups or mark them all optional",
                data={"group": group, "optional": optional, "non_optional": required},
            )
