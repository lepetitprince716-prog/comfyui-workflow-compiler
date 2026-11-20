"""An Autogrow ``min`` or ``max`` far beyond any real node is refused before slots are built.

The caller that can pass one is ``--nodedefs`` (``cwc node``, ``compile``, ``render``,
``learn``): the file is read by ``read_source`` and every entry reaches ``from_object_info``.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.cli import main
from cwc.nodedefs.derive import MAX_AUTOGROW, MAX_AUTOGROW_SLOTS, from_object_info, slot_layout
from cwc.report import ExitCode


def entry(**template):
    template.setdefault("input", {"required": {"x": ["IMAGE"]}})
    return {
        "input": {"required": {"items": ["COMFY_AUTOGROW_V3", {"template": template}]}},
        "output": [],
        "output_name": [],
        "output_is_list": [],
    }


def run_node(info):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "defs.json"
        path.write_text(json.dumps({"EvilNode": info}), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        code = main(["node", "EvilNode", "--nodedefs", str(path)], out, err)
    return code, out.getvalue(), err.getvalue()


class AutogrowLimitTest(unittest.TestCase):
    def test_a_count_past_the_limit_is_a_bad_entry_not_a_loop(self):
        for key in ("min", "max"):
            other = "max" if key == "min" else "min"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, rf"autogrow 'items': {key} is 1000000"):
                from_object_info("EvilNode", entry(**{key: 1_000_000, other: 5}))

    def test_a_huge_float_is_refused_too(self):
        for key in ("min", "max"):
            other = "max" if key == "min" else "min"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, f"autogrow 'items': {key} is"):
                from_object_info("EvilNode", entry(**{key: 1e308, other: 5}))

    def test_a_names_list_past_the_limit_is_refused(self):
        with self.assertRaisesRegex(ValueError, "autogrow 'items': max is"):
            from_object_info("EvilNode", entry(names=[f"n{i}" for i in range(MAX_AUTOGROW + 1)]))

    def test_many_template_inputs_times_many_ordinals_is_refused(self):
        wide = {"required": {f"x{i}": ["IMAGE"] for i in range(MAX_AUTOGROW_SLOTS // MAX_AUTOGROW + 2)}}
        with self.assertRaisesRegex(ValueError, "slots"):
            from_object_info("EvilNode", entry(input=wide, min=0, max=MAX_AUTOGROW))

    def test_the_limits_are_far_above_the_bundled_core_nodes(self):
        core = json.loads((support.ROOT / "src" / "cwc" / "data" / "core_nodedefs.json").read_bytes())["nodes"]
        widest = 0
        for name, info in core.items():
            nd = from_object_info(name, info)
            for spec in nd.inputs:
                if spec.autogrow is not None:
                    widest = max(widest, spec.autogrow.max, spec.autogrow.min)
        self.assertGreaterEqual(widest, 50)
        self.assertGreaterEqual(MAX_AUTOGROW, 10 * widest)

    def test_a_count_at_the_limit_still_builds(self):
        nd = from_object_info("T", entry(min=0, max=MAX_AUTOGROW))
        self.assertEqual(len(slot_layout(nd)), 1)

    def test_cli_exits_4_with_a_message(self):
        code, out, err = run_node(entry(min=1_000_000, max=1_000_000))
        self.assertEqual(code, ExitCode.NODEDEFS_MISSING)
        self.assertEqual(out, "")
        self.assertIn("autogrow 'items'", err)


if __name__ == "__main__":
    unittest.main()
