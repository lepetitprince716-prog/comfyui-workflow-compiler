"""Collects the findings of one normalize run."""

from __future__ import annotations

from typing import Any, Iterable

from cwc.report import Code, Finding, Severity

HINT_LIST_LIMIT = 12


class Findings:
    """Every check appends here; normalize decides at the end whether to raise."""

    def __init__(self) -> None:
        self.items: list[Finding] = []
        # key path -> author ids of the nodes that set it, in the order the keys were read;
        # keys accepted but without effect yet, reported together by :func:`no_effect_notice`
        self.unused: dict[str, list[str]] = {}

    def error(self, code: Code, message: str, nodes: Iterable[str] = (), hint: str = "", data: dict[str, Any] | None = None) -> None:
        self._add(code, Severity.ERROR, message, nodes, hint, data)

    def warning(self, code: Code, message: str, nodes: Iterable[str] = (), hint: str = "", data: dict[str, Any] | None = None) -> None:
        self._add(code, Severity.WARNING, message, nodes, hint, data)

    def info(self, code: Code, message: str, nodes: Iterable[str] = (), hint: str = "", data: dict[str, Any] | None = None) -> None:
        self._add(code, Severity.INFO, message, nodes, hint, data)

    def _add(self, code: Code, severity: Severity, message: str, nodes: Iterable[str], hint: str, data: dict[str, Any] | None) -> None:
        self.items.append(Finding(code, severity, message, tuple(nodes), hint, dict(data or {})))

    @property
    def has_errors(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.items)


def no_effect(out: Findings, key: str, node: str | None = None) -> None:
    """Note an authoring key cwc accepts and checks but does not use yet; ``node`` is the
    author id of the node whose ``_meta`` sets it, None for a ``_workflow`` key."""
    nodes = out.unused.setdefault(key, [])
    if node is not None and node not in nodes:
        nodes.append(node)


def no_effect_notice(unused: dict[str, list[str]]) -> list[Finding]:
    """One E8 info finding for every key :func:`no_effect` noted, so a compile prints one line
    however many keys and nodes there are; ``data["keys"]`` keeps them key by key."""
    if not unused:
        return []
    named = []
    for key, nodes in unused.items():
        if nodes:
            label = "node" if len(nodes) == 1 else "nodes"
            named.append(f"{key} ({label} {', '.join(repr(n) for n in nodes)})")
        else:
            named.append(key)
    every_node = list(dict.fromkeys(n for nodes in unused.values() for n in nodes))
    return [
        Finding(
            Code.E8,
            Severity.INFO,
            f"accepted, but no effect on the canvas in this version of cwc: {'; '.join(named)}; "
            "`cwc guide` says what each key does",
            tuple(every_node),
            data={"keys": [{"key": key, "nodes": nodes} if nodes else {"key": key} for key, nodes in unused.items()]},
        )
    ]


def listing(values: Iterable[Any], limit: int = HINT_LIST_LIMIT) -> str:
    """Comma-separated preview of ``values`` for a hint; the full list goes in ``data``."""
    items = [str(v) for v in values]
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f", ... ({len(items)} in total)"
