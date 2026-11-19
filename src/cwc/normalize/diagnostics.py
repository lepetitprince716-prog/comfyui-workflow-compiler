"""Collects the findings of one normalize run."""

from __future__ import annotations

from typing import Any, Iterable

from cwc.report import Code, Finding, Severity

HINT_LIST_LIMIT = 12


class Findings:
    """Every check appends here; normalize decides at the end whether to raise."""

    def __init__(self) -> None:
        self.items: list[Finding] = []

    def error(self, code: Code, message: str, nodes: Iterable[str] = (), hint: str = "", data: dict[str, Any] | None = None) -> None:
        self._add(code, Severity.ERROR, message, nodes, hint, data)

    def warning(self, code: Code, message: str, nodes: Iterable[str] = (), hint: str = "", data: dict[str, Any] | None = None) -> None:
        self._add(code, Severity.WARNING, message, nodes, hint, data)

    def _add(self, code: Code, severity: Severity, message: str, nodes: Iterable[str], hint: str, data: dict[str, Any] | None) -> None:
        self.items.append(Finding(code, severity, message, tuple(nodes), hint, dict(data or {})))

    @property
    def has_errors(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.items)


def listing(values: Iterable[Any], limit: int = HINT_LIST_LIMIT) -> str:
    """Comma-separated preview of ``values`` for a hint; the full list goes in ``data``."""
    items = [str(v) for v in values]
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f", ... ({len(items)} in total)"
