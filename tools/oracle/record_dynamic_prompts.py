"""Record ``tests/fixtures/prompt/dynamic_prompts.json`` from a real frontend.

Usage (needs the oracle, see ``tests/oracle_support.py``):
    CWC_ORACLE_URL=... CWC_ORACLE_PYTHON=... python3 tools/oracle/record_dynamic_prompts.py

Every string of :data:`VALUES` becomes the ``text`` widget of one CLIPTextEncode node in a
hand-built canvas (no compiler involved, so the recording does not depend on this
repository's port). The frontend loads the canvas and ``graphToPrompt`` exports it; the
fixture keeps each raw value next to the text the frontend exported. No value is random:
a choice block either has one option or only equal options.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures" / "prompt" / "dynamic_prompts.json"

VALUES = [
    "",
    "plain text, no markup",
    # comments
    "a /* note */ b",
    "multi /* line\ncomment\nhere */ end",
    "a /* x */ b /* y */ c",
    "a /* unterminated block",
    "a /*/ not closed",
    "a /*/ closed */ d",
    "/* a // b */ c",
    "line // note\nnext",
    "line // note\rnext",
    "line // note\u2028next",
    "line // note\u2029next",
    "line // note\r\nnext",
    "just // comment to the end",
    "x /* y // z */ w // v\nu",
    "see https://example.com end",
    "slashes / single and a/b and */ stray",
    "///",
    "a\\// b",
    # escapes
    "escaped \\{x\\|y\\} ok",
    "bare | pipe and } brace at top level",
    "back\\slash and \\n and C:\\path\\to",
    "double \\\\ backslash",
    "\\\\{x|x}",
    "{\\\\}x",
    "\\\U0001f600 astral after backslash",
    "trailing\\",
    "{a\\",
    # choice blocks
    "photo of {red|red} car",
    "{solo}",
    "{}",
    "a {} b",
    "{|}",
    "{a|a|a}",
    "{a|{a|a}} x",
    "{{a|a}|{a|a}}",
    "{{a}}",
    "{a|a}{b|b}",
    "{a\\|b|a\\|b}",
    "{a\\}|a\\}} tail",
    "{\\\U0001f600|\\\U0001f600}",
    "\U0001f600 {\u732b|\u732b}",
    "{/}",
    "a {/} b {/}{/} c",
    "{a|a",
    "pre {x",
    "a } b { c",
    "\\{b|b}",
]

TYPE = "CLIPTextEncode"


def _canvas(values: list[str]) -> dict[str, Any]:
    nodes = []
    for index, value in enumerate(values, start=1):
        nodes.append(
            {
                "id": index,
                "type": TYPE,
                "pos": [0, 0],
                "size": [400, 200],
                "flags": {},
                "order": index - 1,
                "mode": 0,
                "inputs": [
                    {"name": "clip", "type": "CLIP", "link": None},
                    {"name": "text", "type": "STRING", "widget": {"name": "text"}, "link": None},
                ],
                "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": None}],
                "properties": {},
                "widgets_values": [value],
            }
        )
    return {
        "version": 0.4,
        "last_node_id": len(values),
        "last_link_id": 0,
        "nodes": nodes,
        "links": [],
        "groups": [],
        "config": {},
        "extra": {},
    }


def main() -> int:
    url, python = os.environ["CWC_ORACLE_URL"], os.environ["CWC_ORACLE_PYTHON"]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "canvas.json"
        path.write_text(json.dumps(_canvas(VALUES)), encoding="utf-8")
        done = subprocess.run(
            [python, str(ROOT / "tools" / "oracle" / "oracle.py"), "probe", "--url", url, str(path)],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    if done.returncode != 0:
        print(done.stderr[-2000:], file=sys.stderr)
        return 1
    result = json.loads(done.stdout)
    if result.get("load_error") or result.get("prompt_error"):
        print(result.get("load_error"), result.get("prompt_error"), file=sys.stderr)
        return 1
    cases = []
    for index, value in enumerate(VALUES, start=1):
        exported = result["prompt"][str(index)]["inputs"]["text"]
        cases.append({"value": value, "exported": exported})
    document = {"frontend": result["versions"]["frontend"], "type": TYPE, "cases": cases}
    OUT.write_text(json.dumps(document, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"recorded {len(cases)} cases from frontend {document['frontend']} into {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
