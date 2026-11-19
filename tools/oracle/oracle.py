"""Real-frontend oracle for comfyui-workflow-compiler.

Usage:
    python tools/oracle/oracle.py probe WORKFLOW.json --url URL [--vue]
    python tools/oracle/oracle.py truth --url URL -o OUT.json.gz [--stats STATS.json]

``probe`` loads a canvas into the frontend, then prints one JSON object: console
messages raised during load and export, each node's live geometry, widgets and slots,
the API prompt and workflow from ``graphToPrompt()``, and ``app.graph.serialize()``.

``truth`` instantiates every node type the server reports in ``/object_info`` with
``LiteGraph.createNode`` and writes the truth snapshot described in
docs/ARCHITECTURE.md ("Truth snapshot format"). Needs Playwright and a Chrome install.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import open_session  # noqa: E402

TRUTH_FORMAT = 1


def probe(workflow: dict[str, Any], url: str, *, vue: bool, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    with open_session(url, vue=vue, settings=settings) as s:
        versions = s.versions()
        s.log.phase = "probe"
        result = s.call("probe", workflow, {})
        result["versions"] = versions
        result["renderer"] = "vue" if versions["vue_nodes"] else "classic"
        result["boot_seconds"] = round(s.boot_seconds, 2)
        result["console"] = s.log.take("probe")
        return result


def canonical_gzip(obj: Any) -> bytes:
    """Deterministic gzip of canonical JSON: sorted keys, no header name, mtime 0."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, compresslevel=9, mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


def truth(url: str, *, add_to_graph: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (snapshot, stats). Only types listed by the server's /object_info are kept."""
    started = time.monotonic()
    with open_session(url) as s:
        versions = s.versions()
        core_types = set(
            s.page.evaluate("async () => Object.keys(await (await fetch('/api/object_info')).json())")
        )
        s.log.phase = "truth"
        raw = s.call("truth", {"add": add_to_graph})
        boot = s.boot_seconds
        page_console = s.log.take("truth")
    nodes = {}
    frontend_only = []
    for name, entry in raw["nodes"].items():
        if name not in core_types:
            frontend_only.append(name)
            continue
        entry.pop("core")
        nodes[name] = entry
    snapshot = {
        "format": TRUTH_FORMAT,
        "frontend_version": versions["frontend"],
        "comfyui_version": versions["comfyui"],
        "nodes": nodes,
    }
    stats = {
        "registered_types": raw["types"],
        "object_info_types": len(core_types),
        "snapshot_types": len(nodes),
        "missing_from_frontend": sorted(core_types - set(raw["nodes"]) - set(raw["failed"])),
        "frontend_only_types": sorted(frontend_only),
        "failed": dict(sorted(raw["failed"].items())),
        "messages": dict(sorted(raw["messages"].items())),
        "page_console": page_console,
        "boot_seconds": round(boot, 2),
        "create_loop_seconds": round(raw["ms"] / 1000, 3),
        "total_seconds": round(time.monotonic() - started, 2),
    }
    return snapshot, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oracle", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="load a canvas, export it, report geometry and console")
    p.add_argument("workflow", type=Path)
    p.add_argument("--url", required=True)
    p.add_argument("--vue", action="store_true", help="enable Comfy.VueNodes.Enabled for this page")
    t = sub.add_parser("truth", help="instantiate every core node type, write the truth snapshot")
    t.add_argument("--url", required=True)
    t.add_argument("-o", "--out", required=True, type=Path)
    t.add_argument("--stats", type=Path, help="write timing and failures here (default: stderr)")
    args = parser.parse_args(argv)

    if args.cmd == "probe":
        workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
        json.dump(probe(workflow, args.url, vue=args.vue), sys.stdout, indent=1, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    snapshot, stats = truth(args.url)
    args.out.write_bytes(canonical_gzip(snapshot))
    text = json.dumps(stats, indent=1, sort_keys=True) + "\n"
    if args.stats:
        args.stats.write_text(text, encoding="utf-8")
    else:
        sys.stderr.write(text)
    return 1 if stats["failed"] or stats["missing_from_frontend"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
