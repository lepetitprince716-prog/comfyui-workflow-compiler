"""Slots and links instantiate writes for Autogrow groups and match-type nodes."""

import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.instantiate import instantiate
from tests.fixtures.instantiate import graphs


def written(name, author_id):
    authoring = graphs.build(getattr(graphs, name)())
    canvas = instantiate(authoring)
    return authoring, canvas, canvas.node_map()[authoring.ids[author_id]]


class AutogrowSlotsTest(unittest.TestCase):
    def test_slots_grow_past_the_last_linked_one_and_stay_before_later_inputs(self):
        _, _, encode = written("autogrow", "encode")
        names = [slot["name"] for slot in encode.inputs]
        self.assertEqual(
            names,
            ["clip", "images.image_0", "images.image_1", "images.image_2", "images.image_3", "vae", "prompt", "edit_format", "image_strength"],
        )

    def test_linked_slots_carry_their_link_and_the_new_slot_is_empty(self):
        _, canvas, encode = written("autogrow", "encode")
        links = {slot["name"]: slot["link"] for slot in encode.inputs}
        self.assertTrue(all(links[n] is not None for n in ("clip", "images.image_0", "images.image_1", "images.image_2", "vae")))
        self.assertIsNone(links["images.image_3"])
        for link_id in (v for v in links.values() if v is not None):
            self.assertEqual(canvas.link_map()[link_id].target_slot, [s["link"] for s in encode.inputs].index(link_id))

    def test_slots_after_the_first_are_optional_and_the_first_is_not(self):
        _, _, encode = written("autogrow", "encode")
        shapes = {slot["name"]: slot.get("shape") for slot in encode.inputs}
        self.assertIsNone(shapes["images.image_0"])
        self.assertEqual({shapes[f"images.image_{n}"] for n in (1, 2, 3)}, {7})

    def test_a_group_with_a_gap_keeps_the_empty_slot_in_it(self):
        doc = graphs.autogrow()
        del doc["encode"]["inputs"]["images.image_1"]
        authoring = graphs.build(doc)
        encode = instantiate(authoring).node_map()[authoring.ids["encode"]]
        links = {slot["name"]: slot["link"] for slot in encode.inputs}
        self.assertIsNone(links["images.image_1"])
        self.assertIsNotNone(links["images.image_2"])
        self.assertIn("images.image_3", links)

    def test_a_slot_label_is_the_part_after_the_group_name(self):
        _, _, encode = written("autogrow", "encode")
        labels = {slot["localized_name"] for slot in encode.inputs if slot["name"].startswith("images.")}
        self.assertEqual(labels, {"image_0", "image_1", "image_2", "image_3"})


class MatchTypeTest(unittest.TestCase):
    def test_a_match_type_output_takes_the_type_of_its_first_linked_input(self):
        _, _, resize = written("dynamic_match", "resize")
        _, _, pick = written("dynamic_match", "pick")
        self.assertEqual([slot["type"] for slot in resize.outputs], ["IMAGE"])
        self.assertEqual([slot["type"] for slot in pick.outputs], ["IMAGE"])

    def test_links_leaving_a_match_type_output_carry_the_resolved_type(self):
        authoring, canvas, _ = written("dynamic_match", "resize")
        resize = int(authoring.ids["resize"])
        types = {link.type for link in canvas.links if link.origin_id == resize}
        self.assertEqual(types, {"IMAGE"})

    def test_an_unlinked_match_type_output_stays_a_wildcard(self):
        doc = graphs.dynamic_match()
        del doc["out"]
        del doc["pick"]["inputs"]["on_true"]
        del doc["pick"]["inputs"]["on_false"]
        authoring = graphs.build(doc)
        pick = instantiate(authoring).node_map()[authoring.ids["pick"]]
        self.assertEqual([slot["type"] for slot in pick.outputs], ["*"])

    def test_the_chosen_option_decides_which_widget_is_linked(self):
        authoring, canvas, resize = written("dynamic_match", "resize")
        slots = [slot["name"] for slot in resize.inputs]
        self.assertEqual(slots, ["input", "resize_type", "resize_type.multiplier", "scale_method"])
        link = next(s["link"] for s in resize.inputs if s["name"] == "resize_type.multiplier")
        self.assertEqual(canvas.link_map()[link].origin_id, int(authoring.ids["factor"]))
        self.assertEqual(resize.raw["widgets_values"], ["scale by multiplier", 1.0, "area"])

    def input_types(self, name, author_id, slot_names):
        _, _, node = written(name, author_id)
        by_name = {slot["name"]: slot["type"] for slot in node.inputs}
        return [by_name[slot] for slot in slot_names]

    def test_both_linked_slots_of_a_template_take_the_linked_type(self):
        self.assertEqual(self.input_types("dynamic_match", "pick", ["on_false", "on_true"]), ["IMAGE", "IMAGE"])

    def test_one_linked_slot_keeps_the_declared_type_and_the_empty_one_takes_the_linked_type(self):
        # frontend 1.59.1: a slot is typed by the other linked slots of its template, never by its own link
        self.assertEqual(self.input_types("match_partial", "pick", ["on_false", "on_true"]), ["IMAGE", "*"])
        doc = graphs.match_partial()
        doc["pick"]["inputs"] = {"on_false": ["src", 0], "switch": True}
        authoring = graphs.build(doc)
        pick = instantiate(authoring).node_map()[authoring.ids["pick"]]
        self.assertEqual([slot["type"] for slot in pick.inputs[:2]], ["*", "IMAGE"])

    def test_a_group_of_match_type_slots_types_every_slot_when_two_are_linked(self):
        names = ["autogrow.branch0", "autogrow.branch1", "autogrow.branch2"]
        self.assertEqual(self.input_types("match_partial", "branch", names), ["IMAGE", "IMAGE", "IMAGE"])

    def test_a_union_template_keeps_its_union(self):
        self.assertEqual(self.input_types("match_partial", "resize", ["input"]), ["IMAGE,MASK"])
        _, _, resize = written("match_partial", "resize")
        self.assertEqual([slot["type"] for slot in resize.outputs], ["MASK"])

    def test_nothing_linked_leaves_every_slot_a_wildcard(self):
        doc = graphs.dynamic_match()
        del doc["out"]
        del doc["pick"]["inputs"]["on_true"]
        del doc["pick"]["inputs"]["on_false"]
        authoring = graphs.build(doc)
        pick = instantiate(authoring).node_map()[authoring.ids["pick"]]
        self.assertEqual([slot["type"] for slot in pick.inputs[:2]], ["*", "*"])


if __name__ == "__main__":
    unittest.main()
