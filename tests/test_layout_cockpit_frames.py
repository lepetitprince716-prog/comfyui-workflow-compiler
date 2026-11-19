"""Every top-level group of the cockpit zone is a cockpit frame: the cockpit group, and an
explicit group whose members' majority zone is the cockpit. The links into any frame (and the
groups nested in it) are left out of the wrap search, the sub-column rule, G5 and C3;
``LayoutResult.cockpit`` lists the boxes of every frame, ascending; C4 reads all the frames as
one panel. ``cockpit_split`` tiers the cockpit group only, and the other frames follow it in
the cockpit slot."""

import copy
import unittest
from dataclasses import replace
from types import SimpleNamespace

from tests import support  # noqa: F401
from tests.graphs import DEFS, node, txt2img
from tests.layout_cases import compiled, gap_faults
from tests.test_layout_tiers import with_advanced

from cwc.annotate import annotate, apply_notes
from cwc.annotate.types import CockpitSlot, Zone
from cwc.layout import layout
from cwc.layout.presets import get_preset
from cwc.pipeline import compile_document
from cwc.report import Code

PRESETS = ("bento", "swimlanes", "stepped", "cockpit-split", "tower-preview")


def prompts_frame(doc=None):
    """Both prompts in an explicit group, which lands in the cockpit zone."""
    doc = doc or txt2img()
    doc["pos"]["_meta"] = {"ui_group": "prompts"}
    doc["neg"]["_meta"] = {"ui_group": "prompts"}
    return doc


def size_frame(doc=None):
    """The empty latent in an explicit group, which lands in the cockpit zone."""
    doc = doc or txt2img()
    doc["latent"]["_meta"] = {"ui_group": "size"}
    return doc


def three_frames():
    """The cockpit group (the exposed steps) and two explicit frames."""
    doc = size_frame(prompts_frame())
    doc["steps"] = node("PrimitiveInt", {"value": 20}, ui_role="cockpit")
    doc["sampler"]["inputs"]["steps"] = ["steps", 0]
    return doc


def errors(report):
    return [f for f in report.findings if f.severity.value == "error"]


def run(doc, preset):
    out = compile_document(copy.deepcopy(doc), DEFS, preset=preset)
    titles = [g.title for g in out.canvas.groups]
    return out, [titles[i] for i in out.layout.cockpit]


def frames(doc):
    canvas, authoring = compiled(copy.deepcopy(doc))
    plan = annotate(canvas, DEFS, authoring)
    return [g.key for g in plan.children(None) if g.zone is Zone.COCKPIT]


class FramesTest(unittest.TestCase):
    def check(self, doc, keys, names):
        """``doc`` has the cockpit frames ``keys`` (plan keys) and compiles clean under every
        preset, its written cockpit boxes titled ``names`` (number prefix left out), in order."""
        self.assertEqual(frames(doc), keys)
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out, cockpit = run(doc, preset)
                self.assertEqual(errors(out.report), [])
                self.assertEqual(list(out.layout.cockpit), sorted(out.layout.cockpit))
                self.assertEqual([t.split(" ", 1 if preset != "stepped" else 2)[-1] for t in cockpit], names)

    def test_explicit_prompts_group_is_a_second_frame(self):
        self.check(prompts_frame(), ["cockpit", "group:prompts"], ["Controls", "Prompts"])

    def test_explicit_size_group_is_a_second_frame(self):
        # The size group ranks before the cockpit group (it has no producer), yet the panel
        # opens with the cockpit group.
        self.check(size_frame(), ["group:size", "cockpit"], ["Controls", "Size"])

    def test_frames_without_a_cockpit_group(self):
        self.check(size_frame(prompts_frame()), ["group:size", "group:prompts"], ["Size", "Prompts"])

    def test_three_frames(self):
        self.check(three_frames(), ["group:size", "cockpit", "group:prompts"], ["Controls", "Size", "Prompts"])


class GapHelperTest(unittest.TestCase):
    def test_gap_faults_leaves_out_the_links_into_every_frame(self):
        # Two frames, B and C, each fed by a lower column under the gap before it. Leaving out
        # the links into B alone still leaves the bridge under the gap before C.
        def column(x, right, *groups):
            return SimpleNamespace(rect=SimpleNamespace(x=x, right=right), groups=list(groups))

        section = {"main": [column(0, 100, "A"), column(200, 300, "B"), column(400, 500, "C")], "lower": [column(100, 180, "L1"), column(300, 380, "L2")]}
        links = {("A", "L1"): 1, ("L1", "B"): 1, ("B", "L2"): 1, ("L2", "C"): 1}
        self.assertEqual(gap_faults(section, links), [])
        self.assertEqual(gap_faults(section, links, frozenset({"B"})), [((100, 200), "no-bridge")])
        self.assertEqual(gap_faults(section, links, frozenset({"B", "C"})), [((100, 200), "no-bridge"), ((300, 400), "no-bridge")])


class OnePanelTest(unittest.TestCase):
    def test_c4_compares_titles_across_frames(self):
        # One authored title in the cockpit group, the same one in an explicit frame: C4 reads
        # the frames as one panel and reports the pair.
        doc = prompts_frame()
        doc["pos"]["_meta"]["title"] = "Knob"
        doc["latent"]["_meta"] = {"title": "Knob"}
        for preset in PRESETS:
            with self.subTest(preset=preset):
                out, cockpit = run(doc, preset)
                self.assertEqual(len(cockpit), 2)
                c4 = [f for f in errors(out.report) if f.code is Code.C4]
                self.assertEqual(len(c4), 1)
                self.assertIn("'Knob'", c4[0].message)
                self.assertEqual([f for f in errors(out.report) if f.code is not Code.C4], [])


class SplitTest(unittest.TestCase):
    def test_cockpit_split_tiers_the_cockpit_group_only(self):
        # The explicit frame's members are given cockpit slots on both sides of the preset's
        # core set; a tiered frame would be written as two boxes.
        doc = prompts_frame(with_advanced())
        canvas, authoring = compiled(doc)
        plan = annotate(canvas, DEFS, authoring)
        group = next(g for g in plan.groups if g.key == "group:prompts")
        pos, neg = sorted(group.members)
        notes = dict(plan.nodes)
        notes[pos] = replace(notes[pos], cockpit=CockpitSlot.POSITIVE)
        notes[neg] = replace(notes[neg], cockpit=CockpitSlot.OTHER)
        plan = replace(plan, nodes=notes)
        apply_notes(canvas, plan)
        result = layout(canvas, plan, get_preset("cockpit-split"), DEFS)
        titles = [canvas.raw["groups"][i]["title"] for i in result.cockpit]
        self.assertEqual(titles, ["01a Controls", "01b Controls", "02 Prompts"])
        upper, lower, prompts = (canvas.raw["groups"][i]["bounding"] for i in result.cockpit)
        self.assertEqual(lower[0], upper[0])
        self.assertEqual(prompts[0], upper[0])
        self.assertGreater(prompts[1], lower[1] + lower[3])


if __name__ == "__main__":
    unittest.main()
