"""Drive a real ComfyUI frontend in headless Chrome.

This is the only place in the repository that uses a third-party package (Playwright).
The frontend runs against a live ComfyUI server; the harness never changes that server:
settings are injected into the page by rewriting the ``GET /api/settings`` response,
and every write request to ``/api/settings`` and ``/api/userdata`` is answered locally
without reaching the server.
"""

from __future__ import annotations

import json
import re
import struct
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

JS_DIR = Path(__file__).with_name("js")

# Settings forced in every session. Values not listed keep the server's value.
BASE_SETTINGS: dict[str, Any] = {
    "Comfy.TutorialCompleted": True,
    "Comfy.Validation.Workflows": False,  # the frontend default, pinned so a server setting cannot change it
}

_SETTINGS_PATH = re.compile(r"/api/settings(?:/|$|\?)")
_WRITE_GUARDED = re.compile(r"/api/(?:settings|userdata)(?:/|$|\?)")


@dataclass
class ConsoleLog:
    """Console messages and failed requests, split into named phases."""

    phase: str = "boot"
    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, level: str, text: str) -> None:
        self.entries.append({"phase": self.phase, "kind": kind, "level": level, "text": text})

    def take(self, phase: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["phase"] == phase]


def solid_png(width: int, height: int, rgb: tuple[int, int, int] = (90, 120, 160)) -> bytes:
    """A solid-colour PNG, standard library only. Stands in for an image the server does not have."""
    row = b"\x00" + bytes(rgb) * width

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b"")


def _js(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8")


class Session:
    """One page with a loaded frontend. Use :func:`open_session`."""

    def __init__(self, page: Any, log: ConsoleLog, boot_seconds: float) -> None:
        self.page = page
        self.log = log
        self.boot_seconds = boot_seconds
        page.evaluate(_js("lib.js"))

    def call(self, fn: str, *args: Any) -> Any:
        """Call ``window.__cwc.<fn>(...args)`` in the page and return its JSON result."""
        return self.page.evaluate(
            "([fn, args]) => window.__cwc[fn](...args)", [fn, list(args)]
        )

    def versions(self) -> dict[str, Any]:
        return self.call("versions")


def _route_api(settings: dict[str, Any]):
    def handle(route: Any, request: Any) -> None:
        url = request.url
        if request.method != "GET" and _WRITE_GUARDED.search(url):
            route.fulfill(status=200, content_type="application/json", body="{}")
            return
        if request.method == "GET" and _SETTINGS_PATH.search(url):
            response = route.fetch()
            try:
                data = response.json()
            except Exception:  # noqa: BLE001 - a non-JSON body is passed through unchanged
                route.fulfill(response=response)
                return
            if isinstance(data, dict) and "/api/settings/" not in url:
                data.update(settings)
                route.fulfill(response=response, body=json.dumps(data))
                return
            route.fulfill(response=response)
            return
        route.continue_()

    return handle


@contextmanager
def open_session(
    url: str,
    *,
    vue: bool = False,
    settings: dict[str, Any] | None = None,
    viewport: tuple[int, int] = (1920, 1080),
    timeout_ms: int = 120_000,
    settle_ms: int = 5_000,
    view_image: tuple[int, int] | None = None,
) -> Iterator[Session]:
    """Open the frontend. view_image: answer every ``GET /api/view`` with a solid PNG of that size."""
    from playwright.sync_api import sync_playwright

    merged = dict(BASE_SETTINGS)
    merged["Comfy.VueNodes.Enabled"] = vue
    merged.update(settings or {})
    log = ConsoleLog()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                ignore_https_errors=True,
            )
            context.route(re.compile(r".*/api/(?:settings|userdata).*"), _route_api(merged))
            if view_image is not None:
                png = solid_png(*view_image)
                context.route(
                    re.compile(r".*/api/view\?.*"),
                    lambda route, request: route.fulfill(status=200, content_type="image/png", body=png),
                )
            page = context.new_page()
            page.on(
                "console",
                lambda m: log.add("console", m.type, m.text)
                if m.type in ("error", "warning")
                else None,
            )
            page.on("pageerror", lambda e: log.add("pageerror", "error", str(e)))
            page.on(
                "response",
                lambda r: log.add("http", str(r.status), r.url) if r.status >= 400 else None,
            )
            started = time.monotonic()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_function(
                "() => window.app && window.app.graph && window.app.canvas && window.LiteGraph",
                timeout=timeout_ms,
            )
            boot = time.monotonic() - started
            # Let the start-up graph's own requests finish so they do not leak into later phases.
            try:
                page.wait_for_load_state("networkidle", timeout=settle_ms)
            except Exception:  # noqa: BLE001 - a busy network only means more boot noise
                pass
            yield Session(page, log, boot)
        finally:
            browser.close()
