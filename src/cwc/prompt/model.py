"""The strict API prompt that ``POST /prompt`` accepts, and value helpers shared by
normalize, instantiate and the equivalence checks.

A prompt maps a node id (decimal string) to ``{"inputs", "class_type", "_meta"}``.
Linked inputs are ``[source_id, output_index]``; widget values whose value is a list
are wrapped as ``{"__value__": [...]}`` so the backend does not read them as links.
"""

from __future__ import annotations

import math
from typing import Any

from cwc.canvas.model import id_order

Prompt = dict[str, dict[str, Any]]

VALUE_WRAPPER = "__value__"


def is_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def wrap_widget_value(value: Any) -> Any:
    """Canvas widget value -> prompt value."""
    return {VALUE_WRAPPER: value} if isinstance(value, list) else value


def unwrap_prompt_value(value: Any) -> Any:
    """Prompt value -> canvas widget value."""
    if isinstance(value, dict) and set(value) == {VALUE_WRAPPER}:
        return value[VALUE_WRAPPER]
    return value


def values_equal(a: Any, b: Any) -> bool:
    """JSON value equality with JavaScript number semantics (4 == 4.0, bool != number)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return float(a) == float(b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    return a == b


def prompt_diff(expected: Prompt, actual: Prompt) -> list[str]:
    """Differences in ``class_type`` and ``inputs`` (``_meta`` is ignored), as readable lines."""
    lines: list[str] = []
    for node_id in sorted(expected.keys() - actual.keys(), key=id_order):
        lines.append(f"node {node_id} missing")
    for node_id in sorted(actual.keys() - expected.keys(), key=id_order):
        lines.append(f"node {node_id} unexpected")
    for node_id in sorted(expected.keys() & actual.keys(), key=id_order):
        a, b = expected[node_id], actual[node_id]
        if a.get("class_type") != b.get("class_type"):
            lines.append(f"node {node_id} class_type {a.get('class_type')!r} != {b.get('class_type')!r}")
        ai, bi = a.get("inputs", {}), b.get("inputs", {})
        for name in sorted(ai.keys() | bi.keys()):
            if name not in bi:
                lines.append(f"node {node_id} input {name!r} missing")
            elif name not in ai:
                lines.append(f"node {node_id} input {name!r} unexpected")
            elif not values_equal(ai[name], bi[name]):
                lines.append(f"node {node_id} input {name!r}: {ai[name]!r} != {bi[name]!r}")
    return lines


def sort_prompt(prompt: Prompt) -> Prompt:
    """Same prompt with node ids in numeric order (non-numeric ids after, by text)."""
    return {key: prompt[key] for key in sorted(prompt, key=id_order)}


def drop_dangling_links(prompt: Prompt) -> Prompt:
    """Remove link inputs whose origin node is not in the prompt (graphToPrompt's last pass).

    The frontend tests any two-element array, so a wrapped list never matches here.
    """
    for entry in prompt.values():
        inputs = entry.get("inputs", {})
        for name in [n for n, v in inputs.items() if isinstance(v, list) and len(v) == 2 and str(v[0]) not in prompt]:
            del inputs[name]
    return prompt
