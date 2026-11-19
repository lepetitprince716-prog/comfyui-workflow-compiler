"""Fail when a string literal in src/cwc names a node type.

Node structure, roles and presets belong in src/cwc/data/ so that new models and node packs
need a data update, not a code release. This lint collects every string constant in
src/cwc (docstrings excluded) and compares it with the node type names listed in the
bundled data files.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "cwc"
DATA = ROOT / "src" / "cwc" / "data"


def known_node_types() -> set[str]:
    names: set[str] = set()
    core = DATA / "core_nodedefs.json"
    if core.is_file():
        names |= set(json.loads(core.read_text(encoding="utf-8"))["nodes"])
    builtins = DATA / "frontend_builtins.json"
    if builtins.is_file():
        names |= set(json.loads(builtins.read_text(encoding="utf-8"))["nodes"])
    return names


def docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def main() -> int:
    names = known_node_types()
    if not names:
        print("lint_no_node_names: no node type list in src/cwc/data/; nothing to check", file=sys.stderr)
        return 1
    failures: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        skip = docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if node.value in names:
                    failures.append(f"{path.relative_to(ROOT)}:{node.lineno}: node type name {node.value!r}")
    for line in failures:
        print(line)
    if failures:
        print(f"{len(failures)} node type name(s) in src/cwc; move them to src/cwc/data/", file=sys.stderr)
        return 1
    print(f"lint_no_node_names: ok ({len(names)} node types checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
