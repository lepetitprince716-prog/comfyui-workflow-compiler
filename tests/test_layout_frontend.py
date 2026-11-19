"""Laid-out canvases through a real frontend, in both renderers (M2 acceptance).

Runs only with the oracle (``tests/oracle_support.py``): ``CWC_ORACLE_URL`` and ``CWC_ORACLE_PYTHON``.

Each canvas of :func:`cases` is compiled and laid out at test time with each preset in
``PRESETS`` (add a preset name to that list to run it as well), once as laid out and once laid
out and routed (``<name>+route``: ``cwc.route.route`` writes ``extra.reroutes`` and
``extra.linkExtensions``). Each is loaded in a classic page and in a Vue page, once as written
and once with every bypassed node switched on; one batch, so one page, per renderer. The checks,
per preset, canvas and renderer:

* ``console``: the load logs no error and no unlisted warning, and neither load nor export fails.
* ``envelope``: every live node box (``boundingRect``) lies inside ``cwc.canvas.envelope.envelope``.
* ``expanded_box``: the live box of an expanded node equals the model of that renderer
  (``classic_box`` or ``vue_box``).
* ``collapsed_box``: the same for a collapsed node, kept apart because the facts hold less for it.
* ``membership``: the innermost group of every node by the frontend's own rule
  (``recomputeInsideNodes``: the box centre in the group, groups nested by wholly containing)
  is the group ``LayoutResult.membership`` names; a node layout leaves ungrouped is in no group
  at all.
* ``serialize``: ``serialize()`` after the load gives back every node field including ``pos``,
  the bounding of every group, the links in order, and ``extra.reroutes`` and
  ``extra.linkExtensions`` exactly as written (``docs/m2-frontend-facts.md`` 6.6: the canonical
  form is a fixed point of load and serialize; both keys are absent when there are no reroutes).
* ``prompt``: the exported prompt equals the compile's, in the factory state and with every
  node enabled (``tests/test_publish_roundtrip.py`` checks the same on a real graph).

A failing check is a finding about layout or the envelope, not about this test. It goes into
``KNOWN_FAILURES`` with its cause: the key is ``(preset, canvas, renderer, check)``, the value a
tuple of ``(pattern, cause)`` where ``pattern`` is a regular expression and every failure line of
that check must match one of them. A listed check that fails with other lines fails the test, and
one that passes now fails it as well, so the table cannot go stale.
"""

from __future__ import annotations

import copy
import re
import sys
import unittest
from dataclasses import dataclass, replace
from typing import Any, Callable

from tests import support  # noqa: F401  (puts src/ on sys.path)
from tests import oracle_support
from tests.graphs import DEFS, compiled
from tests.layout_cases import OTHERS, SIMPLE, laid_out, routed, two_stage, with_lora
from tests.layout_generator import random_graph

from cwc.annotate import annotate, apply_notes
from cwc.canvas.envelope import box_facts, classic_box, envelope, vue_box
from cwc.canvas.geometry import Rect
from cwc.layout import layout
from cwc.layout.presets import get_preset
from cwc.prompt.model import prompt_diff
from cwc.route import route

PRESETS = ["bento", "swimlanes", "stepped", "cockpit-split", "tower-preview"]
SEEDS = (0, 3, 4, 11, 14)
RENDERERS = ("classic", "vue")
CHECKS = ("console", "envelope", "expanded_box", "collapsed_box", "membership", "serialize", "prompt")
TOLERANCE = 0.5

Cause = tuple[str, str]  # (pattern a failure line must match, cause)

# Vue, expanded node: x, y and width equal, the live box shorter than ``vue_box``. The model's body
# height is an upper bound (VUE_SLACK 24 on every node, 244 for an image preview a node reserves
# that the oracle never fills: SaveImage and PreviewImage are 256 to 264 px short, LoadImage 20).
VUE_UPPER_BOUND: Cause = (
    r"off by \[0\.0, 0\.0, 0\.0, -\d+\.\d+\]$",
    "vue_box height is an upper bound: the live Vue node is shorter than the model (VUE_SLACK, a reserved image preview)",
)
_CANVASES = (
    "txt2img", "optional_lora", "two_stage", "custom_sampling", "controlnet", "optional_upscale",
    "lora_controlnet_upscale", "long_chain_6", "required_lora", "seed_0", "seed_3", "seed_4", "seed_11", "seed_14",
    "nested_two_stage", "lora_chain_OROR", "lora_chain_ROROR",
)  # fmt: skip
ROUTED = "+route"  # suffix of the canvas name laid out and routed
KNOWN_FAILURES: dict[tuple[str, str, str, str], tuple[Cause, ...]] = {
    **{(p, c + s, "vue", "expanded_box"): (VUE_UPPER_BOUND,) for p in PRESETS for c in _CANVASES for s in ("", ROUTED)},
}


@dataclass(frozen=True)
class Spec:
    """How one canvas is made: the authoring graph, and optionally ``nest`` = ``(outer, inner)``,
    two group keys of the inferred plan; the inner group is moved under the outer one before
    layout, so the canvas has a nested group (annotate never nests inferred groups itself)."""

    build: Callable[[], dict[str, Any]]
    nest: tuple[str, str] | None = None


def cases() -> dict[str, Spec]:
    """Canvas name to how it is made: every builder of ``tests/layout_cases.py``, the generator
    seeds, and one canvas with a nested group."""
    out = {name: Spec(build) for name, build in {**SIMPLE, **OTHERS}.items()}
    out["required_lora"] = Spec(lambda: with_lora(optional=False))
    for seed in SEEDS:
        out[f"seed_{seed}"] = Spec(lambda seed=seed: random_graph(seed))
    # the second sampler's group inside the latent upscale's group: the outer group holds a node
    # of its own, so both "outer only" and "outer and inner" memberships occur
    out["nested_two_stage"] = Spec(two_stage, nest=("stage:3", "sampling:9"))
    return out


@dataclass
class Case:
    """One canvas laid out by one preset: the written canvas and what the compile says of it."""

    preset: str
    name: str
    canvas: Any  # cwc.canvas.model.Canvas
    result: Any  # cwc.layout.types.LayoutResult
    written: dict[str, Any]
    enabled: dict[str, Any]
    factory_prompt: dict[str, Any]
    full_prompt: dict[str, Any]


def nested(doc: dict[str, Any], preset: Any, nest: tuple[str, str], routing: bool) -> tuple[Any, Any]:
    """The compile pipeline with the plan's group ``nest[1]`` put under ``nest[0]``: the canvas and
    the layout result."""
    canvas, authoring = compiled(doc)
    plan = annotate(canvas, DEFS, authoring)
    apply_notes(canvas, plan)
    outer, inner = nest
    groups = [g for g in plan.groups if g.key != inner]
    at = next(i for i, g in enumerate(groups) if g.key == outer)
    groups.insert(at + 1, replace(plan.group(inner), parent=outer))
    result = layout(canvas, replace(plan, groups=tuple(groups)), preset, DEFS)
    if routing:
        route(canvas, result, DEFS)
    return canvas, result


def lay_out(preset: str, name: str, spec: Spec, routing: bool = False) -> Case:
    if spec.nest is not None:
        canvas, result = nested(spec.build(), get_preset(preset), spec.nest, routing)
    else:
        canvas, _plan, result, *_ = (routed if routing else laid_out)(spec.build(), get_preset(preset))
    authoring = compiled(copy.deepcopy(spec.build()))[1]
    written = canvas.to_json()
    label = name + ROUTED if routing else name
    return Case(preset, label, canvas, result, written, oracle_support.all_enabled(written), authoring.factory, authoring.full)


def observe(laid: list[Case]) -> dict[tuple[str, str, str], tuple[oracle_support.Live, oracle_support.Live]]:
    """``(preset, canvas, renderer)`` -> (the canvas as laid out, with every node enabled), one batch per renderer."""
    documents = [c.written for c in laid] + [c.enabled for c in laid]
    out = {}
    for renderer in RENDERERS:
        lives = oracle_support.live(documents, vue=renderer == "vue")
        for index, case in enumerate(laid):
            out[(case.preset, case.name, renderer)] = (lives[index], lives[len(laid) + index])
    return out


def _box(row: dict[str, Any]) -> Rect:
    return Rect(*row["bounding"])


def _fmt(rect: Rect) -> str:
    return "[" + ", ".join(f"{v:.2f}" for v in rect.as_list()) + "]"


def _off(a: Rect, b: Rect) -> list[float]:
    return [x - y for x, y in zip(a.as_list(), b.as_list())]


def _inside(inner: Rect, outer: Rect) -> bool:
    return (
        inner.x >= outer.x - TOLERANCE
        and inner.y >= outer.y - TOLERANCE
        and inner.right <= outer.right + TOLERANCE
        and inner.bottom <= outer.bottom + TOLERANCE
    )


def check_console(case: Case, renderer: str, live: oracle_support.Live, enabled: oracle_support.Live) -> list[str]:
    lines = []
    for state, item in (("factory", live), ("enabled", enabled)):
        if item.load_error:
            lines.append(f"{state}: load error {item.load_error.splitlines()[0]}")
        if item.prompt_error:
            lines.append(f"{state}: export error {item.prompt_error.splitlines()[0]}")
        lines.extend(f"{state}: {text}" for text in item.console)
    return lines


def _boxes(case: Case, renderer: str, live: oracle_support.Live):
    for node in case.canvas.nodes:
        row = live.nodes.get(node.key)
        facts = box_facts(node, DEFS.get(node.type))
        yield node, row, facts


def check_envelope(case: Case, renderer: str, live: oracle_support.Live, enabled: oracle_support.Live) -> list[str]:
    lines = []
    for node, row, facts in _boxes(case, renderer, live):
        if row is None:
            lines.append(f"node {node.key} {node.type}: not in the live graph")
            continue
        box, env = _box(row), envelope(node, facts)
        if not _inside(box, env):
            lines.append(f"node {node.key} {node.type}: live box {_fmt(box)} is outside the envelope {_fmt(env)}; off by {[round(v, 2) for v in _off(box, env)]}")
    return lines


def _match(case: Case, renderer: str, live: oracle_support.Live, collapsed: bool) -> list[str]:
    lines = []
    model = classic_box if renderer == "classic" else vue_box
    for node, row, facts in _boxes(case, renderer, live):
        if node.collapsed != collapsed or row is None:
            continue
        box, want = _box(row), model(node, facts)
        if any(abs(d) > TOLERANCE for d in _off(box, want)):
            lines.append(
                f"node {node.key} {node.type}: live box {_fmt(box)} != {model.__name__} {_fmt(want)}; off by {[round(v, 2) for v in _off(box, want)]}"
            )
    return lines


def check_expanded_box(case: Case, renderer: str, live: oracle_support.Live, enabled: oracle_support.Live) -> list[str]:
    return _match(case, renderer, live, collapsed=False)


def check_collapsed_box(case: Case, renderer: str, live: oracle_support.Live, enabled: oracle_support.Live) -> list[str]:
    return _match(case, renderer, live, collapsed=True)


def frontend_innermost(live: oracle_support.Live, key: str) -> tuple[list[int], int | None]:
    """Group ids holding node ``key`` by the frontend's rule, and the innermost of them: the one
    no other holding group is nested in (``None`` when there is not exactly one, as when two
    overlapping sibling groups both hold the node). Reads only ``live.groups``."""
    holding = [gid for gid, row in live.groups.items() if key in row["member_nodes"]]
    innermost = [g for g in holding if not any(h in live.groups[g]["child_groups"] for h in holding if h != g)]
    return holding, innermost[0] if len(innermost) == 1 else None


def check_membership(case: Case, renderer: str, live: oracle_support.Live, enabled: oracle_support.Live) -> list[str]:
    lines = []
    ids = [group["id"] for group in case.written["groups"]]
    for node in case.canvas.nodes:
        want_index = case.result.membership[node.key]
        want = None if want_index is None else ids[want_index]
        holding, got = frontend_innermost(live, node.key)
        # an ungrouped node must be in no group: ``got`` is also None when two groups hold it
        if (holding != []) if want is None else (got != want):
            lines.append(f"node {node.key} {node.type}: innermost group in the frontend {got}, layout says {want}; groups holding it {holding}")
    return lines


def without_pos(node: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in node.items() if key != "pos"}


def check_serialize(case: Case, renderer: str, live: oracle_support.Live, enabled: oracle_support.Live) -> list[str]:
    lines = []
    written, got = case.written, live.serialized
    if got is None:
        return ["no serialize() result"]
    ours = {node["id"]: node for node in written["nodes"]}
    theirs = {node["id"]: node for node in got["nodes"]}
    if list(ours) != list(theirs):
        lines.append(f"node ids {list(ours)} come back as {list(theirs)}")
    for node_id, node in ours.items():
        back = theirs.get(node_id)
        if back is None:
            continue
        for field in sorted(node.keys() | back.keys()):
            if node.get(field) != back.get(field):
                lines.append(f"node {node_id} {node['type']} field {field}: written {node.get(field)!r}, serialized {back.get(field)!r}")
    groups = got.get("groups") or []
    if [g["id"] for g in written["groups"]] != [g["id"] for g in groups]:
        lines.append(f"group ids {[g['id'] for g in written['groups']]} come back as {[g['id'] for g in groups]}")
    for ours_group, back in zip(written["groups"], groups):
        if ours_group["bounding"] != back["bounding"]:
            lines.append(f"group {ours_group['id']} {ours_group['title']!r} bounding: written {ours_group['bounding']}, serialized {back['bounding']}")
    for key in ("links", "last_node_id", "last_link_id"):
        if written[key] != got.get(key):
            lines.append(f"{key}: written {written[key]!r}, serialized {got.get(key)!r}")
    return lines + reroute_lines(written, got)


REROUTE_KEYS = ("reroutes", "linkExtensions")


def reroute_lines(written: dict[str, Any], serialized: dict[str, Any]) -> list[str]:
    """``extra.reroutes`` and ``extra.linkExtensions`` of ``serialized`` against ``written``, entry
    by entry in order; a key absent on one side differs from a present one."""
    lines = []
    for key in REROUTE_KEYS:
        ours, theirs = (written.get("extra") or {}).get(key), (serialized.get("extra") or {}).get(key)
        if ours == theirs:
            continue
        if ours is None or theirs is None:
            lines.append(f"extra.{key}: written {ours!r}, serialized {theirs!r}")
            continue
        for index in range(max(len(ours), len(theirs))):
            a = ours[index] if index < len(ours) else None
            b = theirs[index] if index < len(theirs) else None
            if a != b:
                lines.append(f"extra.{key}[{index}]: written {a!r}, serialized {b!r}")
    return lines


def check_prompt(case: Case, renderer: str, live: oracle_support.Live, enabled: oracle_support.Live) -> list[str]:
    lines = []
    for state, item, want in (("factory", live, case.factory_prompt), ("enabled", enabled, case.full_prompt)):
        if item.prompt is None:
            lines.append(f"{state}: no exported prompt")
            continue
        lines.extend(f"{state}: {line}" for line in prompt_diff(want, item.prompt))
    return lines


CHECK_FUNCTIONS: dict[str, Callable[..., list[str]]] = {
    "console": check_console,
    "envelope": check_envelope,
    "expanded_box": check_expanded_box,
    "collapsed_box": check_collapsed_box,
    "membership": check_membership,
    "serialize": check_serialize,
    "prompt": check_prompt,
}
assert tuple(CHECK_FUNCTIONS) == CHECKS


def evaluate(laid: list[Case], observed: dict) -> dict[tuple[str, str, str, str], list[str]]:
    """Every check on every canvas, preset and renderer: key ``(preset, canvas, renderer, check)`` to its failure lines."""
    out = {}
    for case in laid:
        for renderer in RENDERERS:
            live, enabled = observed[(case.preset, case.name, renderer)]
            for check, function in CHECK_FUNCTIONS.items():
                out[(case.preset, case.name, renderer, check)] = function(case, renderer, live, enabled)
    return out


def matrix(results: dict[tuple[str, str, str, str], list[str]]) -> str:
    """One row per preset, canvas and renderer, one column per check: ``ok``, or the number of failure lines
    (``k`` after it when the check is in ``KNOWN_FAILURES``)."""
    rows = [f"{'preset canvas renderer':<46}" + "".join(f"{c:>14}" for c in CHECKS)]
    for preset, name, renderer in dict.fromkeys(key[:3] for key in results):
        cells = []
        for check in CHECKS:
            found = results[(preset, name, renderer, check)]
            cells.append("ok" if not found else f"{len(found)}" + ("k" if (preset, name, renderer, check) in KNOWN_FAILURES else ""))
        rows.append(f"{preset + ' ' + name + ' ' + renderer:<46}" + "".join(f"{cell:>14}" for cell in cells))
    return "\n".join(rows)


class LayoutFrontendTest(unittest.TestCase):
    results: dict[tuple[str, str, str, str], list[str]]

    @classmethod
    def setUpClass(cls) -> None:
        oracle_support.require()
        laid = [lay_out(preset, name, spec, routing) for preset in PRESETS for name, spec in cases().items() for routing in (False, True)]
        cls.results = evaluate(laid, observe(laid))
        print("\n" + matrix(cls.results), file=sys.stderr)

    def run_check(self, check: str) -> None:
        for key, lines in self.results.items():
            if key[3] != check:
                continue
            preset, name, renderer, _ = key
            with self.subTest(preset=preset, canvas=name, renderer=renderer):
                known = KNOWN_FAILURES.get(key)
                if known is None:
                    self.assertEqual(lines, [])
                    continue
                causes = "; ".join(cause for _, cause in known)
                if not lines:
                    self.fail(f"listed in KNOWN_FAILURES but it passes now ({causes})")
                other = [line for line in lines if not any(re.search(pattern, line) for pattern, _ in known)]
                self.assertEqual(other, [], f"fails with a cause other than the listed one ({causes})")
                self.skipTest(f"known failure: {causes}")

    def test_the_load_logs_no_error(self):
        self.run_check("console")

    def test_every_live_box_lies_in_the_envelope(self):
        self.run_check("envelope")

    def test_an_expanded_node_has_the_modelled_box(self):
        self.run_check("expanded_box")

    def test_a_collapsed_node_has_the_modelled_box(self):
        self.run_check("collapsed_box")

    def test_the_frontend_puts_every_node_in_its_intended_group(self):
        self.run_check("membership")

    def test_serialize_gives_back_the_written_canvas(self):
        self.run_check("serialize")

    def test_the_frontend_exports_the_compiled_prompt(self):
        self.run_check("prompt")


if __name__ == "__main__":
    unittest.main()
