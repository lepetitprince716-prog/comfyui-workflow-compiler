"""M0 checks against a real frontend: run the fixtures in tests/fixtures/oracle and report.

Usage:
    python tools/oracle/m0.py --url URL [--only inputs,widgets,dynamic,vue_sizes,markdown] [-o REPORT.json]

Each check loads its fixtures with ``oracle.probe`` (one fresh page per load), keeps the
facts docs/m0-findings.md cites, and prints them as JSON. Nothing here asserts: the
findings document states the conclusions, this script reproduces the evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import open_session  # noqa: E402
from oracle import probe  # noqa: E402
from sizes import check_markdown, check_vue_sizes  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "oracle"
NAMED_ON = {"Comfy.Workflow.NamedValuesRestore": True}


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _slots(node: dict[str, Any]) -> list[str]:
    """Saved inputs as compact strings: name[w] for widget sockets, =link when linked."""
    out = []
    for s in node.get("inputs") or []:
        tag = s["name"]
        if s.get("widget"):
            tag += "[w]"
        if s.get("link") is not None:
            tag += f"={s['link']}"
        out.append(tag)
    return out


def _messages(result: dict[str, Any]) -> list[str]:
    return [f"{e['level']}: {e['text'][:200]}" for e in result["console"]]


def _node_view(result: dict[str, Any], node_id: int) -> dict[str, Any]:
    # Live node ids are strings in 1.59.1 while serialize() writes numbers: compare as text.
    live = next(n for n in result["nodes"] if str(n["id"]) == str(node_id))
    saved = next(
        (n for n in result["serialized"]["nodes"] if str(n["id"]) == str(node_id)),
        {"missing_from_serialize": True},
    )
    return {
        "type": live["type"],
        "widgets": [[w["name"], w["type"], w["value"], w["serialize"], w["to_prompt"]] for w in live["widgets"]],
        "live_inputs": [
            f"{s['name']}:{s['type']}" + ("[w]" if s["widget"] else "") + (f"={s['link']}" if s["link"] is not None else "")
            + (f" shape={s['shape']}" if s["shape"] is not None else "") + (f" label={s['label']}" if s["label"] else "")
            for s in live["inputs"]
        ],
        "live_outputs": [f"{s['name']}:{s['type']}" for s in live["outputs"]],
        "saved_inputs": _slots(saved),
        "saved_widgets_values": saved.get("widgets_values"),
        "saved_widgets_values_named": saved.get("widgets_values_named"),
        "size": live["size"],
        "in_serialize": "missing_from_serialize" not in saved,
    }


def check_inputs(url: str) -> dict[str, Any]:
    """#1: full interleaved vs link-only ``inputs`` arrays."""
    names = [
        "inputs_spec_order.json",
        "inputs_frontend_order.json",
        "inputs_compressed.json",
        "inputs_linked_only.json",
        "inputs_linked_only_no_widget_key.json",
        "inputs_linked_only_stale_slots.json",
    ]
    report: dict[str, Any] = {}
    reference = None
    for name in names:
        r = probe(_load(name), url, vue=False)
        if reference is None:
            reference = r["prompt"]
        report[name] = {
            "load_error": r.get("load_error"),
            "console": _messages(r),
            "prompt_equals_spec_order": r["prompt"] == reference,
            "prompt_ksampler": r["prompt"]["3"]["inputs"],
            "serialized_ksampler_inputs": _slots(next(n for n in r["serialized"]["nodes"] if str(n["id"]) == "3")),
            "serialized_vaedecode_inputs": _slots(next(n for n in r["serialized"]["nodes"] if str(n["id"]) == "4")),
            "serialized_latent_inputs": _slots(next(n for n in r["serialized"]["nodes"] if str(n["id"]) == "2")),
            "serialized_links": sorted(r["serialized"]["links"]),
            "graphToPrompt_workflow_ksampler_inputs": _slots(
                next(n for n in r["prompt_workflow"]["nodes"] if str(n["id"]) == "3")
            ),
            "graphToPrompt_workflow_links": sorted(r["prompt_workflow"]["links"]),
        }
        if name == "inputs_spec_order.json":
            report["roundtrip"] = _roundtrip(url, r, reference)
    return report


def _node_differences(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    """Keys that differ per node as ``<id>:<type>:<key> <before> -> <after>``; empty when equal."""
    left = {str(n["id"]): n for n in before}
    right = {str(n["id"]): n for n in after}
    out = []
    for key in sorted(left.keys() | right.keys(), key=int):
        a, b = left.get(key, {}), right.get(key, {})
        for field in sorted(a.keys() | b.keys()):
            if a.get(field) != b.get(field):
                out.append(f"{key}:{(a or b).get('type')}:{field} {json.dumps(a.get(field))} -> {json.dumps(b.get(field))}")
    return out


def _roundtrip(url: str, first: dict[str, Any], reference_prompt: dict[str, Any]) -> dict[str, Any]:
    """Fixed point: load what ``serialize()`` wrote, twice, and compare with the first export.

    Two passes because a node's size can settle one load late.
    """
    one = probe(first["serialized"], url, vue=False)
    two = probe(one["serialized"], url, vue=False)
    return {
        "load_error": one.get("load_error") or two.get("load_error"),
        "pass1_node_differences": _node_differences(first["serialized"]["nodes"], one["serialized"]["nodes"]),
        "pass2_node_differences": _node_differences(one["serialized"]["nodes"], two["serialized"]["nodes"]),
        "serialized_links_equal": sorted(one["serialized"]["links"]) == sorted(first["serialized"]["links"])
        and sorted(two["serialized"]["links"]) == sorted(first["serialized"]["links"]),
        "prompt_equal": one["prompt"] == reference_prompt and two["prompt"] == reference_prompt,
    }


def check_widgets(url: str) -> dict[str, Any]:
    """#2: ``widgets_values_named`` vs ``widgets_values``, under both settings."""
    report: dict[str, Any] = {}
    for label, settings in (("named_restore_default", None), ("named_restore_on", NAMED_ON)):
        r = probe(_load("widgets_named_vs_positional.json"), url, vue=False, settings=settings)
        report[label] = {
            "named_values_restore": r["versions"]["named_values_restore"],
            "load_error": r.get("load_error"),
            "console": _messages(r),
            "nodes": {n["id"]: _node_view(r, n["id"]) for n in r["nodes"]},
            "prompt": r["prompt"],
        }
    return report


def check_dynamic(url: str) -> dict[str, Any]:
    """#3: V3 dynamic inputs, first as the frontend writes them, then hand-written."""
    report: dict[str, Any] = {}
    with open_session(url) as s:
        s.log.phase = "create"
        made = s.call(
            "createAndSerialize",
            [
                {"type": "EmptyImage", "connect": [[0, 0, 2, "images.image0"], [0, 0, 3, "input"]]},
                {"type": "EmptyImage", "connect": [[1, 0, 2, "images.image1"]]},
                {"type": "BatchImagesNode", "connect": [[2, 0, 4, "on_true"]]},
                {"type": "ResizeImageMaskNode", "set": {"resize_type": "scale by multiplier", "resize_type.multiplier": 0.5}},
                {"type": "ComfySwitchNode", "set": {"switch": True}},
            ],
        )
        report["frontend_written"] = {
            "nodes": {n["type"]: {k: n.get(k) for k in ("inputs", "outputs", "widgets_values", "widgets_values_named", "size")}
                      for n in made["serialized"]["nodes"]},
            "links": made["serialized"]["links"],
            "console": [f"{e['level']}: {e['text'][:200]}" for e in s.log.take("create")],
        }
        # Slot growth and type resolution, one fresh canvas per step.
        def step(specs: list[dict[str, Any]], index: int) -> dict[str, Any]:
            r = s.call("createAndSerialize", specs)
            view = _node_view(r, index)
            return {
                "live_inputs": view["live_inputs"],
                "live_outputs": view["live_outputs"],
                "widgets": [[w[0], w[1], w[2]] for w in view["widgets"]],
                "saved_inputs": view["saved_inputs"],
                "saved_widgets_values": view["saved_widgets_values"],
            }

        image = {"type": "EmptyImage"}
        report["autogrow_steps"] = {
            "alone": step([{"type": "BatchImagesNode"}], 1),
            "one_linked": step([image, {"type": "BatchImagesNode", "connect": [[0, 0, 1, "images.image0"]]}], 2),
            "two_linked": step(
                [image, image, {"type": "BatchImagesNode", "connect": [[0, 0, 2, "images.image0"], [1, 0, 2, "images.image1"]]}], 3
            ),
            "three_linked": step(
                [image, image, image, {"type": "BatchImagesNode", "connect": [[0, 0, 3, "images.image0"], [1, 0, 3, "images.image1"], [2, 0, 3, "images.image2"]]}],
                4,
            ),
        }
        report["matchtype_steps"] = {
            "alone": step([{"type": "ComfySwitchNode"}], 1),
            "on_true_linked": step([image, {"type": "ComfySwitchNode", "connect": [[0, 0, 1, "on_true"]]}], 2),
            "both_linked": step(
                [image, image, {"type": "ComfySwitchNode", "connect": [[0, 0, 2, "on_true"], [1, 0, 2, "on_false"]]}], 3
            ),
        }
        report["dynamic_combo_steps"] = {
            "alone": step([{"type": "ResizeImageMaskNode"}], 1),
            "scale_by_multiplier": step([{"type": "ResizeImageMaskNode", "set": {"resize_type": "scale by multiplier"}}], 1),
            "match_size": step([{"type": "ResizeImageMaskNode", "set": {"resize_type": "match size"}}], 1),
        }
    for name in ("dynamic_v3.json",):
        r = probe(_load(name), url, vue=False)
        report[name] = {
            "load_error": r.get("load_error"),
            "console": _messages(r),
            "nodes": {n["id"]: _node_view(r, n["id"]) for n in r["nodes"]},
            "prompt": r["prompt"],
            "serialized_links": sorted(r["serialized"]["links"]),
        }
        report[name]["roundtrip"] = _roundtrip(url, r, r["prompt"])
    return report


CHECKS: dict[str, Callable[[str], dict[str, Any]]] = {
    "inputs": check_inputs,
    "widgets": check_widgets,
    "dynamic": check_dynamic,
}
# Stage 2 (sizes): slower, so they run only when named in --only.
SIZE_CHECKS: dict[str, Callable[[str], dict[str, Any]]] = {
    "vue_sizes": check_vue_sizes,
    "markdown": check_markdown,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="m0", description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--only", default=",".join(CHECKS))
    parser.add_argument("-o", "--out", type=Path)
    args = parser.parse_args(argv)
    runnable = {**CHECKS, **SIZE_CHECKS}
    report = {name: runnable[name](args.url) for name in args.only.split(",")}
    text = json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
