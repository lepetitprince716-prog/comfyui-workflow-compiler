"""Node ids and coordinates have a size limit, so none of them reaches ``int()`` or a float conversion
in a form that raises an uncaught ``ValueError`` or ``OverflowError``.

The callers that can pass them are the file arguments of ``compile`` (author ids, widget values)
and ``render`` (canvas ids, slots, positions, group boxes).
A digit string longer than 4300 characters made ``int()`` raise, and an integer beyond what a
float holds made ``math.isfinite`` raise; two finite coordinates near 1e308 made a box width
overflow while drawing. Each ended in exit code 70 instead of an input error.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.cli import main
from cwc.ingest.limits import MAX_COORDINATE, MAX_ID_DIGITS
from cwc.report import ExitCode

HUGE_DIGITS = "9" * 4500
HUGE_INT = 10**400  # more than a float holds, fewer digits than int() refuses to print


def canvas(*, node_id=1, pos=(0, 0), links=(), groups=()):
    node = {"id": node_id, "type": "EmptyLatentImage", "pos": list(pos), "size": [100, 100]}
    return {"version": 0.4, "nodes": [node], "links": list(links), "groups": list(groups), "extra": {}}


def run(command, document, *extra, raw=None):
    """Exit code and the text printed, for ``cwc <command>`` on one document written to a file."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.json"
        path.write_text(raw if raw is not None else json.dumps(document), encoding="utf-8")
        target = str(Path(tmp) / ("out.svg" if command == "render" else "out"))
        out, err = io.StringIO(), io.StringIO()
        code = main([command, str(path), "-o", target, *extra], out, err)
    return code, out.getvalue() + err.getvalue()


def authoring(**nodes):
    return {key: {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}} for key in nodes}


class CanvasIdsTest(unittest.TestCase):
    def assert_input_error(self, result):
        code, text = result
        self.assertEqual(code, int(ExitCode.INPUT_ERROR), text)
        self.assertNotIn("internal error", text)

    def test_a_digit_string_id_longer_than_the_limit_is_an_input_error(self):
        self.assert_input_error(run("render", canvas(node_id=HUGE_DIGITS)))

    def test_a_digit_string_slot_longer_than_the_limit_is_an_input_error(self):
        link = [1, 1, "0", 1, HUGE_DIGITS, "LATENT"]
        self.assert_input_error(run("render", canvas(links=[link])))

    def test_an_integer_id_past_the_limit_is_an_input_error(self):
        self.assert_input_error(run("render", None, raw=json.dumps(canvas()).replace('"id": 1', f'"id": {HUGE_INT}')))

    def test_an_id_of_the_longest_allowed_length_still_renders(self):
        for node_id in ("9" * MAX_ID_DIGITS, int("9" * MAX_ID_DIGITS)):
            with self.subTest(node_id=node_id):
                self.assertEqual(run("render", canvas(node_id=node_id))[0], 0)

    def test_an_id_one_digit_past_the_limit_is_refused(self):
        self.assert_input_error(run("render", canvas(node_id="9" * (MAX_ID_DIGITS + 1))))


class CoordinateTest(unittest.TestCase):
    def assert_input_error(self, result):
        code, text = result
        self.assertEqual(code, int(ExitCode.INPUT_ERROR), text)
        self.assertNotIn("internal error", text)

    def test_two_finite_coordinates_that_overflow_a_width_are_an_input_error(self):
        document = canvas(pos=(-1e308, 0))
        document["nodes"].append({"id": 2, "type": "EmptyLatentImage", "pos": [1e308, 0], "size": [100, 100]})
        self.assert_input_error(run("render", document))

    def test_an_integer_coordinate_beyond_a_float_is_an_input_error(self):
        raw = json.dumps(canvas()).replace('"pos": [0, 0]', f'"pos": [{HUGE_INT}, 0]')
        self.assert_input_error(run("render", None, raw=raw))

    def test_a_group_box_past_the_limit_is_an_input_error(self):
        self.assert_input_error(run("render", canvas(groups=[{"title": "g", "bounding": [0, 0, MAX_COORDINATE * 2, 10]}])))

    def test_a_coordinate_just_inside_the_limit_still_renders(self):
        self.assertEqual(run("render", canvas(pos=(MAX_COORDINATE, -MAX_COORDINATE)))[0], 0)

    def test_the_limit_is_far_above_a_real_canvas(self):
        positions = []
        for path in sorted((support.ROOT / "tests" / "fixtures").rglob("*.json")):
            try:
                doc = json.loads(path.read_bytes())
            except ValueError:
                continue
            stack = [doc]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    if isinstance(item.get("pos"), list):
                        positions.extend(v for v in item["pos"] if isinstance(v, (int, float)))
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
        self.assertGreater(len(positions), 100)
        self.assertGreaterEqual(MAX_COORDINATE, 1000 * max(abs(v) for v in positions if abs(v) < 1e6))


class AuthoringTest(unittest.TestCase):
    def test_a_node_key_of_digits_longer_than_the_limit_is_an_input_error(self):
        code, text = run("compile", authoring(**{HUGE_DIGITS: 1}))
        self.assertEqual(code, int(ExitCode.INPUT_ERROR), text)
        self.assertNotIn("internal error", text)
        self.assertIn("E7", text)

    def test_a_node_key_at_the_limit_compiles(self):
        code, text = run("compile", authoring(**{"9" * MAX_ID_DIGITS: 1}))
        self.assertEqual(code, int(ExitCode.CHECK_FAILED), text)  # N2: the graph has no output node
        self.assertIn("N2", text)
        self.assertNotIn("E7", text)

    def test_an_integer_widget_value_beyond_a_float_is_a_finding_not_a_crash(self):
        raw = json.dumps(authoring(**{"1": 1})).replace('"width": 512', f'"width": {HUGE_INT}')
        code, text = run("compile", None, raw=raw)
        self.assertEqual(code, int(ExitCode.INPUT_ERROR), text)
        self.assertNotIn("internal error", text)

    def test_a_float_widget_value_beyond_a_float_is_a_finding_not_a_crash(self):
        # ``KSampler.cfg`` is a FLOAT widget
        doc = {"1": {"class_type": "KSampler", "inputs": {"cfg": HUGE_INT}}}
        code, text = run("compile", doc, "--allow-unknown")
        self.assertNotEqual(code, int(ExitCode.INTERNAL), text)
        self.assertNotIn("internal error", text)


if __name__ == "__main__":
    unittest.main()
