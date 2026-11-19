import unittest
import unittest.mock

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.nodedefs.derive import widget_layout
from cwc.nodedefs.learn import Learner
from cwc.nodedefs.registry import NodeDefs
from cwc.nodedefs.types import Tier
from tests.fixtures.learn.samples import (
    OBJECT_INFO,
    base_defs,
    canvas,
    node,
    prompt,
    sample,
    stack_inputs,
    STACK_META,
    STACK_LIST,
    stack_node,
)


def learner(*samples):
    learn = Learner(base_defs())
    for s in samples:
        learn.add(s)
    return learn


def entries(learn, type_name):
    return [e for e in learn.document()["templates"] if e["type"] == type_name]


def stack_sample(*, ver="1.2.0", text="hello", api_text=None):
    return sample(canvas(stack_node(3, ver=ver, text=text)), prompt(n3=("Xi Stack", stack_inputs(api_text or text))))


class LearnerTest(unittest.TestCase):
    def test_type_whose_derived_layout_fits_gets_no_template(self):
        doc = sample(canvas(node(1, "Mu Sampler", [1, "fixed", 20])), prompt(n1=("Mu Sampler", {"seed": 1, "steps": 20})))
        self.assertEqual(learner(doc).document(), {"format": 1, "templates": []})

    def test_extension_widgets_learned(self):
        (entry,) = entries(learner(stack_sample(), stack_sample()), "Xi Stack")
        self.assertEqual(entry["widgets"], ["meta", "text", "strength", "stack"])
        self.assertEqual((entry["pack"], entry["ver"], entry["source"]), ("pack-xi", "1.2.0", "learned"))
        self.assertEqual(entry["evidence"], {"instances": 2, "aligned": 2, "lengths": {"4": 2}, "conflicts": 0, "conflict_ratio": 0.0})

    def test_unknown_name_is_null(self):
        doc = sample(canvas(node(2, "Pi Viewer", [[{"name": "A"}]])), prompt(n2=("Pi Viewer", {"viewer": {"images": []}, "image": ["1", 0]})))
        (entry,) = entries(learner(doc), "Pi Viewer")
        self.assertEqual(entry["widgets"], [None])

    def test_majority_wins_and_conflicts_are_counted(self):
        # In one run the canvas text differs from the queued text: that instance cannot name
        # the slot (null does not vote); a mislabelled instance would count as a conflict.
        runs = [stack_sample(), stack_sample(), stack_sample(), stack_sample(text="saved", api_text="queued")]
        (entry,) = entries(learner(*runs), "Xi Stack")
        self.assertEqual(entry["widgets"], ["meta", "text", "strength", "stack"])
        self.assertEqual(entry["evidence"]["conflicts"], 0)

    def test_disagreeing_names_are_conflicts(self):
        swapped = sample(canvas(stack_node(3)), prompt(n3=("Xi Stack", {"meta": {"version": 1}, "label": "hello", "strength": 0.5, "stack": {"__value__": [{"name": "x", "on": True}]}})))
        (entry,) = entries(learner(stack_sample(), stack_sample(), stack_sample(), swapped), "Xi Stack")
        self.assertEqual(entry["widgets"][1], "text")
        self.assertEqual((entry["evidence"]["conflicts"], entry["evidence"]["conflict_ratio"]), (1, 0.25))

    def test_most_common_length_wins(self):
        short = sample(canvas(node(3, "Xi Stack", ["hello", 0.5], ver="1.2.0")), prompt(n3=("Xi Stack", {"text": "hello", "strength": 0.5})))
        (entry,) = entries(learner(stack_sample(), stack_sample(), short), "Xi Stack")
        self.assertEqual(len(entry["widgets"]), 4)
        self.assertEqual(entry["evidence"]["lengths"], {"2": 1, "4": 2})
        self.assertEqual(entry["evidence"]["conflicts"], 1)

    def test_nodes_outside_the_prompt_vote_on_length_only(self):
        muted = sample(canvas(stack_node(3, mode=4)), {})
        learn = learner(muted, muted, stack_sample())
        (entry,) = entries(learn, "Xi Stack")
        self.assertEqual(entry["widgets"], ["meta", "text", "strength", "stack"])
        self.assertEqual((entry["evidence"]["instances"], entry["evidence"]["aligned"]), (3, 1))
        self.assertEqual(learn.counts["without_prompt"], 2)

    def test_one_template_per_version_newest_first(self):
        old = sample(canvas(node(3, "Xi Stack", ["hello", 0.5], ver="1.10.0")), prompt(n3=("Xi Stack", {"text": "hello", "strength": 0.5})))
        unversioned = stack_sample(ver=None)
        found = entries(learner(stack_sample(ver="1.9.0"), old, unversioned, stack_sample(ver="abc123")), "Xi Stack")
        self.assertEqual([e["ver"] for e in found], ["1.10.0", "1.9.0", "abc123", None])
        # The 1.10.0 instance fits the derived layout: its names come from /object_info.
        self.assertEqual(found[0]["widgets"], ["text", "strength"])

    def test_truth_types_and_subgraph_nodes_are_skipped(self):
        doc = canvas(
            node(1, "KSampler", [1, "fixed", 20, 8, "euler", "normal", 1, "extra"]),
            node(2, "1f0e6c9a-subgraph", [1, 2, 3]),
            subgraphs=[{"id": "1f0e6c9a-subgraph"}],
        )
        learn = learner(sample(doc, {}))
        self.assertEqual(learn.document()["templates"], [])
        self.assertEqual((learn.counts["fixed"], learn.counts["subgraph"]), (1, 1))

    def test_same_samples_any_order_give_the_same_document(self):
        runs = [stack_sample(), stack_sample(ver="2.0.0"), stack_sample(ver=None)]
        self.assertEqual(learner(*runs).document(), learner(*reversed(runs)).document())

    def test_pack_is_the_most_common_one_whatever_the_order(self):
        def packed(key, pack):
            doc = sample(canvas(stack_node(3)), prompt(n3=("Xi Stack", stack_inputs())))
            doc.canvas.raw["nodes"][0]["properties"] = {key: pack, "ver": "1.2.0"}
            return doc

        runs = [packed("cnr_id", "pack-b"), packed("aux_id", "pack-a"), packed("cnr_id", "pack-b")]
        for order in (runs, runs[::-1], [runs[1], runs[0], runs[2]]):
            self.assertEqual(entries(learner(*order), "Xi Stack")[0]["pack"], "pack-b")
        tie = [runs[0], runs[1]]
        for order in (tie, tie[::-1]):
            self.assertEqual(entries(learner(*order), "Xi Stack")[0]["pack"], "pack-a")

    def test_node_too_large_to_align_votes_on_length_only(self):
        values = [STACK_META, "hello", 0.5, STACK_LIST]
        learn = learner(stack_sample())
        self.assertEqual(learn.counts["aligned"], 1)
        with unittest.mock.patch("cwc.nodedefs.learn.MAX_ALIGN_CELLS", 3):
            learn = learner(stack_sample())
        self.assertEqual((learn.counts["aligned"], learn.counts["too_large"]), (0, 1))
        entry = entries(learn, "Xi Stack")[0]
        self.assertEqual(entry["evidence"]["lengths"], {str(len(values)): 1})
        self.assertEqual(entry["evidence"]["aligned"], 0)


class TemplateByVersionTest(unittest.TestCase):
    TEMPLATES = {
        "format": 1,
        "templates": [
            {"type": "Xi Stack", "pack": "pack-xi", "ver": "2.0.0", "source": "learned", "widgets": ["meta", "text", "strength", "stack"]},
            {"type": "Xi Stack", "pack": "pack-xi", "ver": "1.0.0", "source": "learned", "widgets": ["text", "strength", None]},
            {"type": "Omicron", "pack": "pack-o", "ver": "1.0.0", "source": "learned", "widgets": ["a"]},
            {"type": "Omicron", "pack": "pack-o", "ver": "1.0.0", "source": "learned", "widgets": ["b", "c"]},
            {"type": "Omicron", "source": "curated", "widgets": ["x", "y", "z"]},
            {"type": "KSampler", "ver": "1.0.0", "source": "curated", "widgets": ["only"]},
        ],
    }

    @classmethod
    def setUpClass(cls):
        cls.defs = NodeDefs.load(object_info=OBJECT_INFO, templates=cls.TEMPLATES)  # read-only: shared by the tests

    def names(self, type_name, ver=None):
        return [w.name for w in widget_layout(self.defs.get(type_name, ver))]

    def test_without_version_the_first_template_is_used(self):
        self.assertEqual(self.names("Xi Stack"), ["meta", "text", "strength", "stack"])
        self.assertEqual(self.defs.get("Xi Stack").tier, Tier.TEMPLATES)

    def test_matching_version_picks_its_template(self):
        self.assertEqual(self.names("Xi Stack", "1.0.0"), ["text", "strength", None])
        self.assertEqual(self.defs.get("Xi Stack", "1.0.0").pack.ver, "1.0.0")

    def test_unknown_version_falls_back_to_the_default(self):
        self.assertEqual(self.names("Xi Stack", "9.9.9"), ["meta", "text", "strength", "stack"])

    def test_curated_is_the_default_and_first_entry_per_version_wins(self):
        self.assertEqual(self.names("Omicron"), ["x", "y", "z"])
        self.assertEqual(self.names("Omicron", "1.0.0"), ["a"])

    def test_truth_ignores_versions(self):
        self.assertEqual(self.defs.get("KSampler", "1.0.0").tier, Tier.TRUTH)
        self.assertIs(self.defs.get("KSampler", "1.0.0"), self.defs.get("KSampler"))

    def test_types_without_templates_ignore_versions(self):
        self.assertIs(self.defs.get("Mu Sampler", "1.0.0"), self.defs.get("Mu Sampler"))


if __name__ == "__main__":
    unittest.main()
