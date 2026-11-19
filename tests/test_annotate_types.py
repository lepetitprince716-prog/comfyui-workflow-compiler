import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.annotate.types import Annotations, CockpitSlot, GroupPlan, NodeNote, Source, Zone
from cwc.normalize.types import Role


def notes(*keys, optional=()):
    return {key: NodeNote(zone=Zone.STAGE, optional=key in optional) for key in keys}


class ValidPlanTest(unittest.TestCase):
    def test_inferred_plan(self):
        nodes = {
            "1": NodeNote(zone=Zone.COCKPIT, role=Role.COCKPIT, cockpit=CockpitSlot.POSITIVE, colour="positive"),
            "2": NodeNote(zone=Zone.SAMPLING),
            "3": NodeNote(zone=Zone.OPTIONAL, optional=True),
            "4": NodeNote(zone=Zone.OPTIONAL, optional=True),
            "5": NodeNote(zone=Zone.STAGE, role=Role.PLUMBING, collapsed=True),
        }
        plan = Annotations(
            Source.INFERRED,
            (
                GroupPlan("cockpit", Zone.COCKPIT, "Cockpit", ("1",)),
                GroupPlan("sampling", Zone.SAMPLING, "Sampling", ("2",), stage=1),
                GroupPlan("refine", Zone.OPTIONAL, "Refine", ("3",), optional=True, stage=2),
                GroupPlan("refine-inner", Zone.OPTIONAL, "Detail", ("4",), parent="refine", optional=True, stage=2),
            ),
            nodes,
            ungrouped=("5",),
            reserve_guide=True,
        )
        self.assertEqual([g.key for g in plan.children(None)], ["cockpit", "sampling", "refine"])
        self.assertEqual([g.key for g in plan.children("refine")], ["refine-inner"])
        self.assertEqual(plan.group("sampling").stage, 1)

    def test_kept_plan_reports_impure_user_groups(self):
        plan = Annotations(
            Source.KEPT,
            (
                GroupPlan("g0", Zone.STAGE, "My group", ("1", "2"), source_index=0),
                GroupPlan("g1", Zone.STAGE, "Inside", ("3",), parent="g0", optional=True, source_index=1),
            ),
            notes("1", "2", "3", "4", optional=("2",)),
            ungrouped=("4",),
        )
        self.assertEqual(plan.group("g1").source_index, 1)


class InvariantTest(unittest.TestCase):
    def assertRejected(self, fragment, source, groups, nodes, ungrouped=()):
        with self.assertRaises(ValueError) as caught:
            Annotations(source, groups, nodes, ungrouped=ungrouped)
        self.assertIn(fragment, str(caught.exception))

    def test_group_keys_are_unique(self):
        self.assertRejected(
            "not unique",
            Source.INFERRED,
            (GroupPlan("a", Zone.STAGE, "A", ("1",)), GroupPlan("a", Zone.STAGE, "A", ("2",))),
            notes("1", "2"),
        )

    def test_parent_is_listed_before_its_children(self):
        self.assertRejected(
            "not listed before",
            Source.INFERRED,
            (GroupPlan("child", Zone.STAGE, "C", ("1",), parent="top"), GroupPlan("top", Zone.STAGE, "T", ("2",))),
            notes("1", "2"),
        )

    def test_unknown_parent(self):
        self.assertRejected(
            "not listed before",
            Source.INFERRED,
            (GroupPlan("child", Zone.STAGE, "C", ("1",), parent="nowhere"),),
            notes("1"),
        )

    def test_node_listed_twice_in_ungrouped(self):
        self.assertRejected("twice in ungrouped", Source.INFERRED, (), notes("1"), ungrouped=("1", "1"))

    def test_node_in_two_groups(self):
        self.assertRejected(
            "placed twice",
            Source.INFERRED,
            (GroupPlan("a", Zone.STAGE, "A", ("1",)), GroupPlan("b", Zone.STAGE, "B", ("1",))),
            notes("1"),
        )

    def test_node_in_a_group_and_ungrouped(self):
        self.assertRejected(
            "placed twice",
            Source.INFERRED,
            (GroupPlan("a", Zone.STAGE, "A", ("1",)),),
            notes("1"),
            ungrouped=("1",),
        )

    def test_member_without_a_note(self):
        self.assertRejected(
            "has no note", Source.INFERRED, (GroupPlan("a", Zone.STAGE, "A", ("1", "9")),), notes("1")
        )

    def test_ungrouped_without_a_note(self):
        self.assertRejected("placed without a note", Source.INFERRED, (), notes("1"), ungrouped=("1", "9"))

    def test_node_not_placed(self):
        self.assertRejected("nodes not placed", Source.INFERRED, (GroupPlan("a", Zone.STAGE, "A", ("1",)),), notes("1", "2"))

    def test_optional_group_inside_non_optional(self):
        self.assertRejected(
            "sits inside non-optional",
            Source.INFERRED,
            (
                GroupPlan("top", Zone.STAGE, "T", ("1",)),
                GroupPlan("opt", Zone.OPTIONAL, "O", ("2",), parent="top", optional=True),
            ),
            notes("1", "2", optional=("2",)),
        )

    def test_non_optional_group_inside_optional(self):
        self.assertRejected(
            "must be optional",
            Source.INFERRED,
            (
                GroupPlan("opt", Zone.OPTIONAL, "O", ("1",), optional=True),
                GroupPlan("inner", Zone.STAGE, "I", ("2",), parent="opt"),
            ),
            notes("1", "2", optional=("1",)),
        )

    def test_member_and_group_disagree_on_optional(self):
        self.assertRejected(
            "disagree on optional",
            Source.INFERRED,
            (GroupPlan("opt", Zone.OPTIONAL, "O", ("1", "2"), optional=True),),
            notes("1", "2", optional=("1",)),
        )
        self.assertRejected(
            "disagree on optional",
            Source.INFERRED,
            (GroupPlan("plain", Zone.STAGE, "P", ("1",)),),
            notes("1", optional=("1",)),
        )

    def test_kept_group_needs_a_source_index(self):
        self.assertRejected(
            "source_index", Source.KEPT, (GroupPlan("g0", Zone.STAGE, "G", ("1",)),), notes("1")
        )

    def test_inferred_group_has_no_source_index(self):
        self.assertRejected(
            "source_index", Source.INFERRED, (GroupPlan("a", Zone.STAGE, "A", ("1",), source_index=0),), notes("1")
        )


if __name__ == "__main__":
    unittest.main()
