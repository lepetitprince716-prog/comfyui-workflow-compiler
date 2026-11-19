"""Record ``tests/fixtures/instantiate/frontend_exports.json`` from a real frontend.

Usage (needs the oracle, see ``tests/oracle_support.py``; run it from the repository root):
    CWC_ORACLE_URL=... CWC_ORACLE_PYTHON=... python3 tools/oracle/record_frontend_exports.py

Every graph of ``tests/fixtures/instantiate/graphs.py`` is instantiated, loaded in the
frontend in its factory state (optional nodes bypassed) and with every node enabled, and
exported with ``graphToPrompt``. The fixture keeps both exports per graph. The offline test
``tests/test_instantiate_frontend_exports.py`` holds ``authoring.factory`` and
``authoring.full`` to them, so a fault of the bypass resolver (which the compiler's own
checks share with the exporter it models) shows without a frontend.

Run it again after adding a graph to ``graphs.py`` or changing what instantiate writes, and
read the diff of the fixture: it is the frontend's answer, not ours.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from cwc.instantiate import instantiate  # noqa: E402
from tests import oracle_support  # noqa: E402
from tests.fixtures.instantiate import graphs  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "instantiate" / "frontend_exports.json"


def main() -> int:
    cases = graphs.cases()
    canvases = []
    for authoring, _ in cases.values():
        written = instantiate(authoring).to_json()
        canvases += [written, oracle_support.all_enabled(written)]
    results = oracle_support.roundtrip(canvases)
    recorded = {}
    for index, name in enumerate(cases):
        factory, enabled = results[2 * index], results[2 * index + 1]
        for state, result in (("factory", factory), ("all_enabled", enabled)):
            if result.load_error or result.prompt_error or result.prompt is None:
                print(f"{name} ({state}): {result.load_error or result.prompt_error}", file=sys.stderr)
                return 1
        recorded[name] = {"factory": factory.prompt, "all_enabled": enabled.prompt}
    versions = results[0].versions or {}
    document = {"frontend": versions.get("frontend"), "comfyui": versions.get("comfyui"), "cases": recorded}
    OUT.write_text(json.dumps(document, indent=1, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    print(f"recorded {len(recorded)} graphs from frontend {document['frontend']} into {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
