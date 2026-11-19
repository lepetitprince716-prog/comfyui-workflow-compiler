"""The generator entries ``wide_graph_outputs``, ``wide_graph_scatter`` and ``wide_graph_fed`` give valid graphs.

For seeds 0 to ``SEEDS - 1`` of each entry, under ``bento``: the compile does not raise, and the
report holds no error finding that is not a layout finding (code G or C). Whether the layout
findings are zero is for the layout property tests, not here: this file only asserts that the
graphs these entries generate compile and can be measured.

The features' own contracts: the ``outputs`` group has at least two members; when
``optional_stage`` is on exactly one sampler is optional; the ``scatter`` group has two to four
members; the ``fed`` group has two to four required members, one of them linked to every other,
and the entry always draws ``stage_control`` or ``cond_mix``; the entries never change what
``wide_graph`` draws; one seed gives one graph.
"""

import unittest
from functools import lru_cache

from tests import support  # noqa: F401
from tests.graphs import DEFS
from tests.layout_generator_wide import (
    EXTRA,
    wide_fed_features,
    wide_features,
    wide_graph_fed,
    wide_graph_outputs,
    wide_graph_scatter,
    wide_outputs_features,
    wide_scatter_features,
)

from cwc.pipeline import compile_document

SEEDS = range(60)
SAMPLERS = ("KSampler", "SamplerCustomAdvanced")


@lru_cache(maxsize=None)
def compiled(entry: str, seed: int):
    graph = {"outputs": wide_graph_outputs, "scatter": wide_graph_scatter, "fed": wide_graph_fed}[entry]
    return compile_document(graph(seed), DEFS, preset="bento")


def members(doc: dict, group: str) -> list[str]:
    return [key for key, value in doc.items() if value.get("_meta", {}).get("ui_group") == group]


class EntriesCompile(unittest.TestCase):
    def check(self, entry: str) -> None:
        for seed in SEEDS:
            with self.subTest(entry=entry, seed=seed):
                out = compiled(entry, seed)
                other = [f.code.value for f in out.report.findings if f.severity.value == "error" and f.code.value[:1] not in ("G", "C")]
                self.assertEqual(other, [])

    def test_outputs(self):
        self.check("outputs")

    def test_scatter(self):
        self.check("scatter")

    def test_fed(self):
        self.check("fed")


class OutputsEntry(unittest.TestCase):
    def test_group_has_two_members(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertIn("group_outputs", wide_outputs_features(seed))
                self.assertGreaterEqual(len(members(wide_graph_outputs(seed), "outputs")), 2)

    def test_group_members_are_required(self):
        for seed in SEEDS:
            doc = wide_graph_outputs(seed)
            for key in members(doc, "outputs"):
                with self.subTest(seed=seed, node=key):
                    self.assertFalse(doc[key]["_meta"].get("ui_optional"))

    def test_optional_stage_makes_one_sampler_optional(self):
        opened = 0
        for seed in SEEDS:
            doc = wide_graph_outputs(seed)
            optional = [key for key, value in doc.items() if value["class_type"] in SAMPLERS and value.get("_meta", {}).get("ui_optional")]
            with self.subTest(seed=seed):
                if "optional_stage" in wide_outputs_features(seed):
                    opened += 1
                    self.assertEqual(len(optional), 1)
                    self.assertNotEqual(optional[0], "sampler")  # a sampler after the first
                else:
                    self.assertEqual(optional, [])
        self.assertGreater(opened, 0)  # the draw reaches the feature in these seeds


class ScatterEntry(unittest.TestCase):
    def test_group_has_two_to_four_members(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertIn("group_scatter", wide_scatter_features(seed))
                self.assertIn(len(members(wide_graph_scatter(seed), "scatter")), (2, 3, 4))

    def test_group_members_are_required(self):
        for seed in SEEDS:
            doc = wide_graph_scatter(seed)
            for key in members(doc, "scatter"):
                with self.subTest(seed=seed, node=key):
                    self.assertFalse(doc[key]["_meta"].get("ui_optional"))


def feeders(doc: dict, key: str) -> list[str]:
    """The nodes that feed ``key`` directly."""
    return [v[0] for v in doc[key]["inputs"].values() if isinstance(v, list)]


def linked(doc: dict, key: str) -> list[str]:
    """The nodes that feed ``key`` directly or that it feeds directly."""
    return feeders(doc, key) + [k for k in doc if key in feeders(doc, k)]


class FedEntry(unittest.TestCase):
    def test_group_has_two_to_four_members(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertIn("group_fed", wide_fed_features(seed))
                self.assertIn(len(members(wide_graph_fed(seed), "fed")), (2, 3, 4))

    def test_group_members_are_required(self):
        for seed in SEEDS:
            doc = wide_graph_fed(seed)
            for key in members(doc, "fed"):
                with self.subTest(seed=seed, node=key):
                    self.assertFalse(doc[key]["_meta"].get("ui_optional"))

    def test_one_member_is_linked_to_every_other(self):
        # The group is a seed node and nodes directly feeding it or fed by it.
        for seed in SEEDS:
            doc = wide_graph_fed(seed)
            group = members(doc, "fed")
            with self.subTest(seed=seed):
                self.assertTrue(any(all(other in linked(doc, key) for other in group if other != key) for key in group))

    def test_entry_draws_a_steering_feature(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertTrue({"stage_control", "cond_mix"} & {*wide_fed_features(seed)})

    def test_stage_control_conditions_a_later_stage(self):
        # An early decode feeds a ControlNet apply, which conditions a sampler (or the guider of
        # one) of a later stage.
        opened = 0
        for seed in SEEDS:
            doc = wide_graph_fed(seed)
            if "stage_control" not in wide_fed_features(seed):
                self.assertNotIn("tile_cn", doc)
                continue
            opened += 1
            with self.subTest(seed=seed):
                self.assertEqual(doc["tile_cn"]["class_type"], "ControlNetApplyAdvanced")
                source = doc["tile_cn"]["inputs"]["image"][0]
                self.assertTrue(source.startswith("decode_s"))
                consumers = [doc[k]["class_type"] for k in doc if "tile_cn" in feeders(doc, k)]
                self.assertTrue({*consumers} & {*SAMPLERS, "CFGGuider"})
        self.assertGreater(opened, 0)

    def test_cond_mix_combines_a_controlnet_and_a_prompt(self):
        opened = 0
        for seed in SEEDS:
            doc = wide_graph_fed(seed)
            if "cond_mix" not in wide_fed_features(seed):
                self.assertNotIn("mix_comb", doc)
                continue
            opened += 1
            with self.subTest(seed=seed):
                inputs = doc["mix_comb"]["inputs"]
                self.assertEqual(doc[inputs["conditioning_1"][0]]["class_type"], "ControlNetApplyAdvanced")
                self.assertEqual(doc[inputs["conditioning_2"][0]]["class_type"], "CLIPTextEncode")
        self.assertGreater(opened, 0)


class Determinism(unittest.TestCase):
    def test_same_seed_same_graph(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertEqual(wide_graph_outputs(seed), wide_graph_outputs(seed))
                self.assertEqual(wide_graph_scatter(seed), wide_graph_scatter(seed))
                self.assertEqual(wide_graph_fed(seed), wide_graph_fed(seed))

    def test_the_draw_never_switches_on_an_extra_feature(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                self.assertFalse(set(wide_features(seed)) & set(EXTRA))


if __name__ == "__main__":
    unittest.main()
