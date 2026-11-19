"""Round trips through a real ComfyUI frontend, for tests that need one.

The frontend is the oracle served at ``$CWC_ORACLE_URL``; its Playwright lives in the
virtual environment whose interpreter is ``$CWC_ORACLE_PYTHON``. This module starts
``tests/oracle_runner.py batch`` in that interpreter (the tests themselves stay on the
standard library) and returns, per canvas, what the frontend did with it. With either
variable missing, :func:`require` raises ``unittest.SkipTest``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

_ALWAYS, _BYPASS = 0, 4  # LiteGraph node modes

ENV_URL = "CWC_ORACLE_URL"
ENV_PYTHON = "CWC_ORACLE_PYTHON"
RUNNER = Path(__file__).with_name("oracle_runner.py")
TIMEOUT_S = 1500  # the runner makes up to 4 attempts of 4 minutes to start a page, then does the work

# Console text that is not a finding: the frontend asks the server for the example input
# image ``LoadImage`` names (the oracle has none; the runner logs the request as an ``http``
# entry and the browser adds one anonymous ``Failed to load resource`` console error for
# each), and warns about a deprecated field it reads itself.
_DEPRECATED = "output.links is deprecated"
_IMAGE_REQUEST = "/api/view"
_ANONYMOUS_FAILURE = "Failed to load resource"


@dataclass(frozen=True)
class Roundtrip:
    """What a real frontend made of one canvas."""

    load_error: str | None
    prompt_error: str | None
    console: tuple[str, ...]  # messages that are not noise: warnings and errors only
    prompt: dict[str, Any] | None  # graphToPrompt().output
    serialized: dict[str, Any] | None  # app.graph.serialize() after the load
    versions: dict[str, Any] | None = None  # the frontend and ComfyUI versions that did it


def require() -> None:
    """Skip the calling test unless the oracle is configured."""
    missing = [name for name in (ENV_URL, ENV_PYTHON) if not os.environ.get(name)]
    if missing:
        raise unittest.SkipTest(f"real frontend not configured: set {' and '.join(missing)}")


def all_enabled(canvas: dict[str, Any]) -> dict[str, Any]:
    """A copy of a canvas document (``Canvas.to_json()``) with every bypassed node switched on."""
    copy = json.loads(json.dumps(canvas))
    for node in copy["nodes"]:
        if node.get("mode") == _BYPASS:
            node["mode"] = _ALWAYS
    return copy


def roundtrip(canvases: Sequence[dict[str, Any]]) -> list[Roundtrip]:
    """Load each canvas document (``Canvas.to_json()``) in one frontend page, in order."""
    require()
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for index, canvas in enumerate(canvases):
            path = Path(directory) / f"canvas{index:03d}.json"
            path.write_text(json.dumps(canvas), encoding="utf-8")
            paths.append(str(path))
        done = subprocess.run(
            [os.environ[ENV_PYTHON], str(RUNNER), "--url", os.environ[ENV_URL], "batch", *paths],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    if done.returncode != 0:
        raise RuntimeError(f"oracle runner failed ({done.returncode}): {done.stderr.strip()[-2000:]}")
    results = json.loads(done.stdout)
    if len(results) != len(canvases):
        raise RuntimeError(f"oracle returned {len(results)} results for {len(canvases)} canvases")
    return [_result(item) for item in results]


@dataclass(frozen=True)
class Live:
    """What one renderer showed of one canvas: the load, the export and the live geometry."""

    renderer: str  # "classic" or "vue"
    load_error: str | None
    prompt_error: str | None
    console: tuple[str, ...]
    prompt: dict[str, Any] | None
    serialized: dict[str, Any] | None
    nodes: dict[str, dict[str, Any]]  # node key -> the page's row: bounding, vue_box, pos, size, collapsed
    groups: dict[int, dict[str, Any]]  # group id -> the page's row: bounding, member_nodes, child_groups


def live(canvases: Sequence[dict[str, Any]], *, vue: bool) -> list[Live]:
    """Load each canvas document in one page of one renderer (one batch per renderer), in order."""
    require()
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for index, canvas in enumerate(canvases):
            path = Path(directory) / f"canvas{index:03d}.json"
            path.write_text(json.dumps(canvas), encoding="utf-8")
            paths.append(str(path))
        command = [os.environ[ENV_PYTHON], str(RUNNER), "--url", os.environ[ENV_URL], "layout", *(["--vue"] if vue else []), *paths]
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"oracle runner failed ({done.returncode}): {done.stderr.strip()[-2000:]}")
    results = json.loads(done.stdout)
    if len(results) != len(canvases):
        raise RuntimeError(f"oracle returned {len(results)} results for {len(canvases)} canvases")
    renderer = "vue" if vue else "classic"
    return [
        Live(
            renderer=renderer,
            load_error=item.get("load_error"),
            prompt_error=item.get("prompt_error"),
            console=tuple(findings(item.get("console") or [])),
            prompt=item.get("prompt"),
            serialized=item.get("serialized"),
            nodes={row["id"]: row for row in item.get("nodes") or []},
            groups={row["id"]: row for row in item.get("groups") or []},
        )
        for item in results
    ]


def _result(item: dict[str, Any]) -> Roundtrip:
    return Roundtrip(
        load_error=item.get("load_error"),
        prompt_error=item.get("prompt_error"),
        console=tuple(findings(item.get("console") or [])),
        prompt=item.get("prompt"),
        serialized=item.get("serialized"),
        versions=item.get("versions"),
    )


def findings(entries: Sequence[dict[str, Any]]) -> list[str]:
    """Errors, warnings and failed requests of one load, minus the noise above."""
    image_requests = sum(1 for e in entries if e.get("kind") == "http" and _IMAGE_REQUEST in str(e.get("text")))
    out = []
    for entry in entries:
        text = str(entry.get("text", ""))
        if entry.get("kind") == "http":
            if _IMAGE_REQUEST not in text:
                out.append(f"http {entry.get('level')}: {text}")
        elif entry.get("level") in ("error", "warning", "warn") and _DEPRECATED not in text:
            if _ANONYMOUS_FAILURE in text and image_requests > 0:
                image_requests -= 1
                continue
            out.append(f"{entry.get('level')}: {text}")
    return out
