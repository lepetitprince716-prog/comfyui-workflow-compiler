"""Reading an authoring file: an API prompt with cwc's optional relaxations.

Only the document envelope is checked here (JSON object, size and depth limits, not a
canvas). The authoring rules themselves (E1 to E7) belong to ``normalize``.
"""

from __future__ import annotations

from typing import Any

from cwc.ingest.limits import MAX_NODES, input_error, parse_json
from cwc.ingest.png import SIGNATURE
from cwc.report import Code

WORKFLOW_INFO_KEY = "_workflow"


def load_authoring(data: bytes) -> dict[str, Any]:
    if data.startswith(SIGNATURE):
        raise input_error(
            Code.I1,
            "input is a PNG; authoring files are JSON",
            hint="use `cwc beautify` for a canvas PNG, or `cwc extract` for the prompt inside it",
        )
    doc = parse_json(data, "authoring JSON")
    if not isinstance(doc, dict):
        raise input_error(Code.I1, "authoring file is not a JSON object")
    if isinstance(doc.get("nodes"), list) and ("links" in doc or "version" in doc):
        raise input_error(
            Code.I1,
            "input is a canvas workflow, not an authoring file",
            hint="use `cwc beautify` to tidy an existing canvas",
        )
    count = len(doc) - (WORKFLOW_INFO_KEY in doc)
    if count > MAX_NODES:
        raise input_error(Code.I3, f"authoring file has {count} nodes; the limit is {MAX_NODES}")
    return doc
