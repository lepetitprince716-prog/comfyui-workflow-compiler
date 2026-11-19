import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.nodedefs.learn import align
from tests.fixtures.learn.samples import STACK_LIST, STACK_META, base_defs

DEFS = base_defs()


class AlignTest(unittest.TestCase):
    def test_equal_values_take_prompt_names_in_order(self):
        names = align(["hi", 0.5], {"text": "hi", "strength": 0.5, "clip": ["4", 0]}, base=DEFS.get("Xi Stack"))
        self.assertEqual(names, ("text", "strength"))

    def test_extension_widgets_named_from_the_prompt(self):
        values = [STACK_META, "hello", 0.5, STACK_LIST]
        inputs = {"meta": STACK_META, "text": "hello", "strength": 0.5, "stack": {"__value__": STACK_LIST}, "clip": ["9", 0]}
        self.assertEqual(align(values, inputs, base=DEFS.get("Xi Stack")), ("meta", "text", "strength", "stack"))

    def test_value_control_named_by_the_pseudo_widget_rule(self):
        names = align([7, "fixed", 30], {"model": ["1", 0], "seed": 7, "steps": 30}, base=DEFS.get("Mu Sampler"))
        self.assertEqual(names, ("seed", "control_after_generate", "steps"))

    def test_value_control_needs_a_mode(self):
        names = align([7, "", 30], {"seed": 7, "steps": 30}, base=DEFS.get("Mu Sampler"))
        self.assertEqual(names, ("seed", None, "steps"))

    def test_upload_button_named_by_the_pseudo_widget_rule(self):
        self.assertEqual(align(["a.png", "image"], {"image": "a.png"}, base=DEFS.get("Nu Loader")), ("image", "upload"))

    def test_unexplained_value_is_null(self):
        names = align([[{"name": "A"}]], {"viewer": {"images": [{"name": "A"}]}, "image": ["2", 0]}, base=DEFS.get("Pi Viewer"))
        self.assertEqual(names, (None,))

    def test_unequal_value_is_null_even_at_the_right_position(self):
        self.assertEqual(align(["saved", 0.5], {"text": "queued", "strength": 0.5}, base=DEFS.get("Xi Stack")), (None, "strength"))

    def test_type_must_fit_the_declared_input(self):
        # 1 == 1.0 under JS numbers, but a STRING input never holds a number.
        names = align([1], {"text": 1}, base=DEFS.get("Xi Stack"))
        self.assertEqual(names, (None,))

    def test_linked_widget_matches_a_fitting_value(self):
        slots = [{"name": "seed", "type": "INT", "widget": {"name": "seed"}, "link": 3}]
        names = align([5, "randomize", 30], {"model": ["1", 0], "seed": ["8", 0], "steps": 30}, slots, DEFS.get("Mu Sampler"))
        self.assertEqual(names, ("seed", "control_after_generate", "steps"))

    def test_link_only_socket_is_not_a_widget(self):
        self.assertEqual(align(["x"], {"clip": ["4", 0], "text": "x"}, base=DEFS.get("Xi Stack")), ("text",))

    def test_repeated_value_keeps_order(self):
        names = align([0.5, 0.5], {"a": 0.5, "b": 0.5})
        self.assertEqual(names, ("a", "b"))

    def test_without_definition_values_align_with_prompt_order(self):
        self.assertEqual(align([1, True, "z"], {"n": 1, "flag": True, "other": "q"}), ("n", "flag", None))

    def test_node_larger_than_the_cell_limit_is_not_aligned(self):
        values, inputs = [1, 2, 3], {"a": 1, "b": 2, "c": 3}
        self.assertEqual(align(values, inputs, max_cells=9), ("a", "b", "c"))
        self.assertIsNone(align(values, inputs, max_cells=8))

    def test_extra_saved_value_between_prompt_values(self):
        self.assertEqual(align([1, "ui-only", 2], {"a": 1, "b": 2}), ("a", None, "b"))


if __name__ == "__main__":
    unittest.main()
