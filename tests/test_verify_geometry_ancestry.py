"""G3 without a target (``check_ancestry``) reads a node's groups at each centre the frontend may use.

Measured centres of a node at ``(0, 0)``, 300 x 100, one unknown type ``T`` (see ``test_verify_geometry``):
expanded: classic ``(150, 35)``, Vue ``(150, 47)``; collapsed: classic ``(33.9, -15)``, Vue ``(112.5, -15)``,
expanded centre ``(150, 47)``.
"""

from __future__ import annotations

import unittest

from tests.test_verify_geometry import BEAUTIFY, canvas, found, group, node, run

from cwc.report import Code, Severity

WIDE = (-50, -100, 400, 300)  # holds every centre above


class AncestryCentres(unittest.TestCase):
    def check(self, rect: tuple[float, float, float, float], *, collapsed: bool) -> list:
        entry = node(1, 0, 0, collapsed=collapsed)
        before = canvas([entry], groups=[group(*WIDE)])
        after = canvas([entry], groups=[group(*rect)])
        return found(run(after, BEAUTIFY, before=before), Code.G3)

    def only(self, rect: tuple[float, float, float, float], centre: str, *, collapsed: bool) -> None:
        (finding,) = self.check(rect, collapsed=collapsed)
        self.assertEqual(finding.severity, Severity.ERROR)
        self.assertEqual(finding.nodes, ("1",))
        self.assertEqual(list(finding.data["centres"]), [centre])
        self.assertEqual(finding.data["centres"][centre], {"before": [0], "after": []})
        self.assertIn(f"under the {centre} box centre", finding.message)

    def test_all_centres_unchanged_reports_nothing(self) -> None:
        for collapsed in (False, True):
            with self.subTest(collapsed=collapsed):
                self.assertEqual(self.check((-60, -110, 420, 320), collapsed=collapsed), [])

    def test_only_the_classic_centre_changed(self) -> None:
        self.only((-50, 40, 400, 200), "classic", collapsed=False)  # top edge 40: classic y 35 out, Vue y 47 in
        self.only((50, -100, 300, 300), "classic", collapsed=True)  # left edge 50: classic x 33.9 out, Vue 112.5 and 150 in

    def test_only_the_vue_centre_changed(self) -> None:
        self.only((-50, -100, 400, 140), "vue", collapsed=False)  # bottom edge 40: classic y 35 in, Vue y 47 out

    def test_only_the_expanded_centre_of_a_collapsed_node_changed(self) -> None:
        self.only((-50, -100, 400, 100), "expanded", collapsed=True)  # bottom edge 0: both band centres (y -15) in, 47 out

    def test_the_reviewers_counterexample(self) -> None:
        entry = node(1, 100, 100, collapsed=True)
        entry["title"] = "P"
        before = canvas([entry], groups=[group(0, 0, 300, 200)])
        after = canvas([entry], groups=[group(0, 0, 180, 100)])
        report = run(after, BEAUTIFY, before=before)
        (finding,) = found(report, Code.G3)
        self.assertEqual(sorted(finding.data["centres"]), ["expanded", "vue"])  # the classic centre (133.9, 85) stays in
        self.assertIn("vue box centre", finding.message)
        self.assertIn("expanded box centre", finding.message)

    def test_a_node_in_no_group_at_any_centre_is_unchanged(self) -> None:
        entry = node(1, 0, 0)
        before = canvas([entry], groups=[group(900, 900, 10, 10)])
        after = canvas([entry], groups=[group(800, 800, 10, 10)])
        self.assertEqual(found(run(after, BEAUTIFY, before=before), Code.G3), [])

    def test_the_expanded_centre_is_read_from_the_node_as_written_now(self) -> None:
        # collapsed in the output only: the expanded centre of the input node is compared too
        before = canvas([node(1, 0, 0)], groups=[group(*WIDE)])
        after = canvas([node(1, 0, 0, collapsed=True)], groups=[group(-50, -100, 400, 100)])
        (finding,) = found(run(after, BEAUTIFY, before=before), Code.G3)
        self.assertEqual(finding.data["centres"]["expanded"], {"before": [0], "after": []})


if __name__ == "__main__":
    unittest.main()
