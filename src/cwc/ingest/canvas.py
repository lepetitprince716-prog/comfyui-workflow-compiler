"""Reading a canvas workflow (UI JSON, or the ``workflow`` chunk of a PNG) into a ``Canvas``.

This is the boundary for canvases: everything the ``Canvas`` views index without a
check (node ``id``/``type``/``pos``/``size``, input and output slot entries, link tuples,
group boxes, reroute extensions) is checked here once. Semantic checks (unique ids,
link endpoints, consistent tables) belong to ``verify``.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from cwc.canvas.model import ROOT_VERSION, Canvas, is_ascii_decimal
from cwc.ingest.limits import MAX_LINKS, MAX_NODES, MAX_REPORTED, input_error, input_errors, parse_json
from cwc.ingest.png import SIGNATURE, read_png_text
from cwc.report import Code, Finding, Severity

WORKFLOW_KEY = "workflow"
PROMPT_KEY = "prompt"


def load_canvas(data: bytes) -> Canvas:
    """A 0.4 canvas from UI JSON bytes or a PNG carrying a ``workflow`` text chunk."""
    if data.startswith(SIGNATURE):
        texts = read_png_text(data)
        if WORKFLOW_KEY not in texts:
            if PROMPT_KEY in texts:
                raise input_error(
                    Code.I4,
                    "PNG carries only an API prompt, no canvas workflow",
                    hint="run `cwc extract` to save the prompt, then compile it",
                )
            raise input_error(Code.I4, "PNG has no workflow or prompt text chunk")
        doc = parse_json(texts[WORKFLOW_KEY].encode("utf-8"), "PNG workflow chunk")
    else:
        doc = parse_json(data, "workflow JSON")
    _check_document(doc)
    return Canvas(doc)


def _check_document(doc: Any) -> None:
    if not isinstance(doc, dict):
        raise input_error(Code.I1, "workflow is not a JSON object")
    if doc and all(isinstance(v, dict) and "class_type" in v for v in doc.values()):
        raise input_error(Code.I1, "input is an API prompt, not a canvas", hint="use `cwc compile` for API prompts")
    if not isinstance(doc.get("nodes"), list):
        raise input_error(Code.I1, "workflow has no `nodes` list")
    version = doc.get("version")
    if not _is_number(version) or version != ROOT_VERSION:
        detail = " (the object-link schema used inside subgraph definitions)" if version == 1 else ""
        raise input_error(
            Code.I2,
            f"root version is {version!r}{detail}; only {ROOT_VERSION} is supported",
            hint="open and save the workflow in the ComfyUI frontend to get a 0.4 file",
        )
    links = doc.get("links", [])
    if len(doc["nodes"]) > MAX_NODES:
        raise input_error(Code.I3, f"workflow has {len(doc['nodes'])} nodes; the limit is {MAX_NODES}")
    if isinstance(links, list) and len(links) > MAX_LINKS:
        raise input_error(Code.I3, f"workflow has {len(links)} links; the limit is {MAX_LINKS}")
    problems = _Problems()
    for index, node in enumerate(doc["nodes"]):
        _check_node(node, index, problems)
    _check_links(links, problems)
    _check_groups(doc.get("groups", []), problems)
    _check_extra(doc.get("extra", {}), problems)
    if doc.get("config") is not None and not isinstance(doc.get("config"), dict):
        problems.add("`config` is not an object")
    problems.raise_if_any()


class _Problems:
    def __init__(self) -> None:
        self.items: list[tuple[str, tuple[str, ...]]] = []

    def add(self, message: str, node: Any = None) -> None:
        self.items.append((message, () if node is None else (str(node),)))

    def raise_if_any(self) -> None:
        if not self.items:
            return
        findings = [Finding(Code.I1, Severity.ERROR, message, nodes=nodes) for message, nodes in self.items]
        if len(findings) > MAX_REPORTED:
            rest = len(findings) - MAX_REPORTED
            findings = findings[:MAX_REPORTED]
            findings.append(Finding(Code.I1, Severity.ERROR, f"and {rest} more shape problems"))
        raise input_errors(findings)


def _check_node(node: Any, index: int, problems: _Problems) -> None:
    if not isinstance(node, dict):
        problems.add(f"nodes[{index}] is not an object")
        return
    node_id = node.get("id")
    label = node_id if _is_node_id(node_id) else None
    where = f"node {node_id!r}" if label is not None else f"nodes[{index}]"
    if label is None:
        problems.add(f"{where}: `id` must be an integer or a non-empty string (digits must be ASCII)")
    if not isinstance(node.get("type"), str) or not node.get("type"):
        problems.add(f"{where}: `type` must be a non-empty string", label)
    for key in ("pos", "size"):
        if not _is_vec2(node.get(key)):
            problems.add(f"{where}: `{key}` must be [x, y] or {{\"0\": x, \"1\": y}} with finite numbers", label)
    if "mode" in node and not _is_int(node["mode"]):  # ``Node.mode`` calls ``int()`` on it, so null is refused too
        problems.add(f"{where}: `mode` must be an integer", label)
    _check_optional(node, "title", lambda v: isinstance(v, str), "a string", where, label, problems)
    for key in ("flags", "properties", "widgets_values_named"):
        _check_optional(node, key, lambda v: isinstance(v, dict), "an object", where, label, problems)
    _check_optional(node, "widgets_values", lambda v: isinstance(v, (list, dict)), "an array or object", where, label, problems)
    for key in ("inputs", "outputs"):
        slots = node.get(key)
        if slots is None:
            continue
        if not isinstance(slots, list):
            problems.add(f"{where}: `{key}` must be an array", label)
            continue
        for slot_index, slot in enumerate(slots):
            if not isinstance(slot, dict) or not isinstance(slot.get("name", ""), str):
                problems.add(f"{where}: {key}[{slot_index}] must be an object with a string `name`", label)
            elif key == "inputs" and not _is_optional_int(slot.get("link")):
                problems.add(f"{where}: inputs[{slot_index}].link must be an integer or null", label)
            elif key == "outputs" and slot.get("links") is not None and not (
                isinstance(slot["links"], list) and all(_is_int(v) for v in slot["links"])
            ):
                problems.add(f"{where}: outputs[{slot_index}].links must be an array of integers or null", label)


def _check_optional(
    node: dict, key: str, ok: Callable[[Any], bool], expected: str, where: str, label: Any, problems: _Problems
) -> None:
    if node.get(key) is not None and not ok(node[key]):
        problems.add(f"{where}: `{key}` must be {expected}", label)


def _check_links(links: Any, problems: _Problems) -> None:
    if not isinstance(links, list):
        problems.add("`links` must be an array")
        return
    for index, link in enumerate(links):
        if link is None:
            continue
        if not (
            isinstance(link, list)
            and len(link) == 6
            and _is_int(link[0])
            and _is_node_id(link[1])
            and _is_slot(link[2])
            and _is_node_id(link[3])
            and _is_slot(link[4])
        ):
            problems.add(
                f"links[{index}] must be [id, origin_id, origin_slot, target_id, target_slot, type]"
                " with an integer id, node ids and integer slots (digit strings must be ASCII)"
            )


def _check_groups(groups: Any, problems: _Problems) -> None:
    if not isinstance(groups, list):
        problems.add("`groups` must be an array")
        return
    for index, group in enumerate(groups):
        bounding = group.get("bounding") if isinstance(group, dict) else None
        if not (isinstance(bounding, list) and len(bounding) == 4 and all(_is_number(v) for v in bounding)):
            problems.add(f"groups[{index}] must be an object with `bounding` [x, y, w, h]")


def _check_extra(extra: Any, problems: _Problems) -> None:
    # An explicit null is refused: the Canvas views read ``extra`` as an object.
    if not isinstance(extra, dict):
        problems.add("`extra` must be an object")
        return
    reroutes = extra.get("reroutes")
    if reroutes is not None and not (isinstance(reroutes, list) and all(_is_reroute(r) for r in reroutes)):
        problems.add(
            "`extra.reroutes` must be an array of objects with an integer `id`, an optional `pos` of two finite"
            " numbers, an optional integer `parentId` and optional integer `linkIds`"
        )
    extensions = extra.get("linkExtensions")
    if extensions is not None and not (
        isinstance(extensions, list)
        and all(isinstance(e, dict) and _is_int(e.get("id")) and _is_int(e.get("parentId")) for e in extensions)
    ):
        problems.add("`extra.linkExtensions` must be an array of {id, parentId} integers")


def _is_reroute(value: Any) -> bool:
    if not (isinstance(value, dict) and _is_int(value.get("id"))):
        return False
    pos = value.get("pos")
    if pos is not None and not (isinstance(pos, list) and len(pos) == 2 and all(_is_number(v) for v in pos)):
        return False
    link_ids = value.get("linkIds")
    if link_ids is not None and not (isinstance(link_ids, list) and all(_is_int(v) for v in link_ids)):
        return False
    return _is_optional_int(value.get("parentId"))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_optional_int(value: Any) -> bool:
    return value is None or _is_int(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_node_id(value: Any) -> bool:
    """An integer, or a non-empty string that is not digits in another script: ``id_order`` and
    the link views convert digit strings with ``int()``."""
    if _is_int(value):
        return True
    return isinstance(value, str) and value != "" and (is_ascii_decimal(value) or not value.isdigit())


def _is_slot(value: Any) -> bool:
    return _is_int(value) or (isinstance(value, str) and is_ascii_decimal(value))


def _is_vec2(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) == 2 and all(_is_number(v) for v in value)
    if isinstance(value, dict):
        return all(_is_number(value.get(k)) for k in ("0", "1"))
    return False
