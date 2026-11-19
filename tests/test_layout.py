"""Layout inside groups: the C1 split, dummy slots for long links, widths and the contract."""

import unittest

from tests import support  # noqa: F401
from tests.graphs import DEFS, compiled, node, txt2img
from tests.layout_cases import BENTO, COMPILE_ERRORS, laid_out

from cwc.annotate import annotate, apply_notes
from cwc.canvas.envelope import box_facts, readable_width
from cwc.layout import layout
from cwc.layout.types import Widening


def post_chain(length: int, blend: bool = False):
    """Text to image with ``length`` image scalers after the decode; ``blend`` mixes the last
    scaler with the decode, a link that spans every scaler's layer."""
    doc = txt2img()
    prev = "decode"
    for i in range(length):
        doc[f"scale{i}"] = node("ImageScaleBy", {"image": [prev, 0], "upscale_method": "lanczos", "scale_by": 1.1})
        prev = f"scale{i}"
    if blend:
        doc["blend"] = node("ImageBlend", {"image1": [prev, 0], "image2": ["decode", 0], "blend_factor": 0.5, "blend_mode": "normal"})
        prev = "blend"
    doc["save"]["inputs"]["images"] = [prev, 0]
    return doc


def errors(report):
    return [f.message for f in report.findings if f.severity.value == "error" and f.code.value in COMPILE_ERRORS]


class SplitTest(unittest.TestCase):
    def test_more_than_five_layers_splits_into_lettered_sub_groups(self):
        canvas, plan, result, report, _ = laid_out(post_chain(8))
        self.assertEqual(errors(report), [])
        post = next(g for g in plan.groups if g.zone.value == "postprocess")
        parts = sorted(key for key in result.group_index if key.startswith(post.key + "/"))
        self.assertEqual(len(parts), 2)
        number = result.labels[parts[0]][:2]
        self.assertEqual([result.labels[key] for key in parts], [number + "a", number + "b"])
        titles = [canvas.raw["groups"][result.group_index[key]]["title"] for key in parts]
        self.assertTrue(all(title.endswith(" " + post.name) for title in titles), titles)
        for key in post.members:
            self.assertIn(result.membership[key], {result.group_index[p] for p in parts})
        self.assertLessEqual(report.metrics["C1"]["most_expanded"], 12)
        a, b = (canvas.raw["groups"][result.group_index[key]]["bounding"] for key in parts)
        self.assertLess(a[0] + a[2], b[0])  # the second half follows the first along x


class WaypointTest(unittest.TestCase):
    def test_a_link_spanning_layers_gets_a_slot_per_intermediate_layer(self):
        canvas, plan, result, report, _ = laid_out(post_chain(2, blend=True))
        self.assertEqual(errors(report), [])
        nodes = canvas.node_map()
        decode = next(k for k, n in nodes.items() if n.type == "VAEDecode")
        blend = next(k for k, n in nodes.items() if n.type == "ImageBlend")
        link = next(x for x in canvas.links if str(x.origin_id) == decode and str(x.target_id) == blend)
        points = result.waypoints[link.id]
        self.assertEqual(len(points), 2)
        xs = [p[0] for p in points]
        self.assertEqual(xs, sorted(xs))
        self.assertLess(nodes[decode].pos[0], xs[0])
        self.assertLess(xs[-1], nodes[blend].pos[0])


class WidthTest(unittest.TestCase):
    def test_expanded_nodes_of_a_layer_share_the_readable_width(self):
        canvas, plan, result, report, before = laid_out(txt2img())
        cockpit = next(g for g in plan.groups if g.key == "cockpit")
        nodes = canvas.node_map()
        expanded = [k for k in cockpit.members if not nodes[k].collapsed]
        widths = {nodes[k].size[0] for k in expanded}
        self.assertEqual(len(widths), 1)
        width = widths.pop()
        self.assertEqual(width % 20, 0)
        for key in expanded:
            self.assertGreaterEqual(width, readable_width(box_facts(nodes[key], DEFS.get(nodes[key].type))))
            self.assertGreaterEqual(width, 225)


class ContractTest(unittest.TestCase):
    def test_round_two_is_m3(self):
        canvas, authoring = compiled(txt2img())
        plan = annotate(canvas, DEFS, authoring)
        apply_notes(canvas, plan)
        first = layout(canvas, plan, BENTO, DEFS)
        with self.assertRaises(NotImplementedError):
            layout(canvas, plan, BENTO, DEFS, widen=Widening(first, {}))

    def test_result_shape(self):
        canvas, plan, result, report, _ = laid_out(txt2img())
        self.assertEqual(result.preset, "bento")
        self.assertEqual(set(result.membership), set(plan.nodes))
        self.assertEqual(sorted(result.group_index.values()), list(range(len(canvas.raw["groups"]))))
        self.assertEqual(len(result.bands), 1)
        self.assertEqual([c.slot for c in result.columns], [0, 1, 2, 3, 4])
        self.assertEqual(result.columns[2].stage, next(g.stage for g in plan.groups if g.zone.value == "sampling"))


if __name__ == "__main__":
    unittest.main()
