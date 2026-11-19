"""An author's group id with no workflow title is turned into words: split at whitespace,
underscores and hyphens, the leading all-digit words dropped (the layout numbers the groups),
every other word kept with its digits. An id with nothing left falls back to the zone name."""

from __future__ import annotations

import unittest

from cwc.annotate import annotate

from tests.graphs import DEFS, compiled
from tests.test_annotate_author_title import author_group, two_pass
from tests.test_annotate_optional_groups import lora_chain, optional_groups


def sampler_group_name(group: str) -> str:
    """The name of the group the author keyed ``group`` on the sampler (no workflow title)."""
    found, _ = author_group(two_pass(group), group=group)
    return found.name


class GroupIdNameTest(unittest.TestCase):
    def test_a_trailing_number_is_kept(self):
        self.assertEqual(sampler_group_name("pass 1"), "Pass 1")
        self.assertEqual(sampler_group_name("pass 2"), "Pass 2")
        self.assertEqual(sampler_group_name("hires_2"), "Hires 2")

    def test_digits_inside_a_word_are_kept(self):
        self.assertEqual(sampler_group_name("sd15_base"), "Sd15 base")
        self.assertEqual(sampler_group_name("4x_upscale"), "4x upscale")

    def test_leading_number_words_are_dropped(self):
        self.assertEqual(sampler_group_name("2_hires"), "Hires")
        self.assertEqual(sampler_group_name("1_base"), "Base")
        self.assertEqual(sampler_group_name("01-02-hires"), "Hires")

    def test_only_the_leading_number_words_are_dropped(self):
        self.assertEqual(sampler_group_name("2_pass_2"), "Pass 2")
        self.assertEqual(sampler_group_name("pass_2_hires"), "Pass 2 hires")

    def test_the_words_are_joined_with_one_space_and_the_first_character_is_capitalised(self):
        self.assertEqual(sampler_group_name("hires__2--final"), "Hires 2 final")
        self.assertEqual(sampler_group_name("__hires 2"), "Hires 2")

    def test_an_id_with_nothing_left_falls_back_to_the_zone_name(self):
        for group in ("123", "01", "1_2", "--_", "_"):
            with self.subTest(group=group):
                self.assertEqual(sampler_group_name(group), "Sampling")

    def test_only_ascii_digits_count_as_a_number_word(self):
        self.assertEqual(sampler_group_name("٢_hires"), "٢ hires")

    def test_two_groups_that_differ_only_in_a_number_get_different_names(self):
        doc = two_pass("pass 1")
        doc["s2"].setdefault("_meta", {})["ui_group"] = "pass 2"
        canvas, authoring = compiled(doc)
        plan = annotate(canvas, DEFS, authoring)
        names = {g.key: g.name for g in plan.groups}
        self.assertEqual((names["group:pass 1"], names["group:pass 2"]), ("Pass 1", "Pass 2"))

    def test_a_workflow_title_still_wins_and_its_number_prefix_is_cleaned(self):
        doc = two_pass("sd15_base", title="02a My chain")
        found, _ = author_group(doc, group="sd15_base")
        self.assertEqual(found.name, "My chain")

    def test_an_optional_module_named_by_its_id_follows_the_same_rule(self):
        doc = lora_chain("lora_opt0", "lora_opt0")
        del doc["_workflow"]
        self.assertEqual([name for name, _ in optional_groups(doc)], ["Lora opt0"])

    def test_an_optional_module_with_a_leading_number_id(self):
        doc = lora_chain("1_style", "1_style")
        del doc["_workflow"]
        self.assertEqual([name for name, _ in optional_groups(doc)], ["Style"])


if __name__ == "__main__":
    unittest.main()
