"""Severity by run mode: how compile differs from beautify and check.

Compile output is held to every rule. Beautify and check read canvases cwc did not
write (historic ``last_link_id``, extension-owned object ``widgets_values``), so the
rules that only compile output must meet are reported without blocking there.
"""

from __future__ import annotations

from dataclasses import dataclass

from cwc.report import Mode, Severity


@dataclass(frozen=True)
class Policy:
    strict: bool

    @classmethod
    def for_mode(cls, mode: Mode) -> Policy:
        return cls(strict=mode is Mode.COMPILE)

    @property
    def compile_only(self) -> Severity:
        """Severity of a rule that blocks compile output and is only reported elsewhere."""
        return Severity.ERROR if self.strict else Severity.WARNING
