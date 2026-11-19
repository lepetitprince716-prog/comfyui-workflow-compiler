"""tower-preview: the bento skeleton with a preview area in the output slot.

The slot holding ``preview.zone`` is at least ``snap_up(share * SCREEN_W)`` wide and
``min_height`` high when it holds a node that shows a preview, and those nodes grow, width and
height, to fill their group; nothing else changes from ``bento``.
"""

import unittest
from dataclasses import replace

from tests import layout_cases as C
from tests.graphs import DEFS, node, txt2img

from cwc.annotate.types import Zone
from cwc.canvas.envelope import box_facts, envelope, shows_preview
from cwc.canvas.geometry import GRID
from cwc.layout.inner import PAD
from cwc.layout.presets import get_preset

TOWER = get_preset("tower-preview")
BENTO = get_preset("bento")
WIDTH, HEIGHT = 880, 680


def laid_out(doc, preset=TOWER):
    return C.laid_out(doc, preset)


def output_previews(canvas, plan):
    """The expanded preview nodes in output-zone groups: the ones the preview area grows."""
    zone = {key: group.zone for group in plan.groups for key in group.members}
    return {
        key: n
        for key, n in canvas.node_map().items()
        if zone.get(key) is Zone.OUTPUT and (d := DEFS.get(n.type)) is not None and shows_preview(d) and not n.collapsed
    }


def box_of(n):
    return envelope(n, box_facts(n, DEFS.get(n.type)))


def two_previews():
    """txt2img with a second preview node on the decode: two previews in one layer of the output group."""
    doc = txt2img()
    doc["peek"] = node("PreviewImage", {"images": ["decode", 0]})
    return doc


def previews_in_two_layers():
    """An output group (made by ``ui_group``) whose layers both hold a preview node: the
    image preview shares layer 0 with the invert node, the save node follows the invert node in layer 1."""
    doc = txt2img()
    view = {"ui_group": "view"}
    doc["invert"] = node("ImageInvert", {"image": ["decode", 0]}, **view)
    doc["peek"] = node("PreviewImage", {"images": ["decode", 0]}, **view)
    doc["save"] = node("SaveImage", {"images": ["invert", 0], "filename_prefix": "out"}, **view)
    return doc


def no_preview():
    """txt2img saving the latent: the output slot holds no node that shows a preview."""
    doc = txt2img()
    del doc["decode"]
    doc["save"] = node("SaveLatent", {"samples": ["sampler", 0], "filename_prefix": "out"})
    return doc


CASES = {**C.SIMPLE, **C.OTHERS, "two_previews": two_previews}


class PreviewAreaTest(unittest.TestCase):
    def test_the_area_is_wide_and_high_enough(self):
        self.assertEqual(round(TOWER.preview.share * 1600), WIDTH)
        for name, build in CASES.items():
            with self.subTest(case=name):
                canvas, plan, result, _, _ = laid_out(build())
                output = {g.key for g in plan.groups if g.zone is Zone.OUTPUT}
                columns = [c for c in result.columns if output & set(c.groups)]
                self.assertTrue(columns)
                for column in columns:
                    self.assertGreaterEqual(column.rect.w, WIDTH)
                    self.assertGreaterEqual(column.rect.h, HEIGHT)

    def test_preview_nodes_fill_their_group(self):
        # each preview node spans its group's inner width and none shrinks from bento; the lowest
        # one of each layer reaches the group's inner bottom, within a grid step
        for name, build in CASES.items():
            with self.subTest(case=name):
                canvas, plan, result, _, _ = laid_out(build())
                b_canvas, _, _, _, _ = C.laid_out(build(), BENTO)
                grown = output_previews(canvas, plan)
                self.assertTrue(grown)
                before = b_canvas.node_map()
                lowest: dict[tuple[int, float], float] = {}
                for key, n in grown.items():
                    x, y, w, h = canvas.raw["groups"][result.membership[key]]["bounding"]
                    box = box_of(n)
                    self.assertEqual(box.right, x + w - PAD)
                    self.assertLessEqual(box.bottom, y + h - PAD)
                    self.assertGreater(n.size[0], before[key].size[0])
                    self.assertGreaterEqual(box.h, box_of(before[key]).h)
                    at = (result.membership[key], n.pos[0])
                    lowest[at] = max(lowest.get(at, 0.0), box.bottom)
                for (index, _), bottom in lowest.items():
                    _, y, _, h = canvas.raw["groups"][index]["bounding"]
                    self.assertGreater(bottom, y + h - PAD - GRID)

    def test_a_short_output_group_grows_its_preview_node(self):
        canvas, plan, result, _, _ = laid_out(txt2img())
        b_canvas, _, _, _, _ = C.laid_out(txt2img(), BENTO)
        [(key, n)] = output_previews(canvas, plan).items()
        b = b_canvas.node_map()[key]
        self.assertGreater(n.size[0], b.size[0])
        self.assertGreater(box_of(n).h, box_of(b).h)
        _, _, w, h = canvas.raw["groups"][result.membership[key]]["bounding"]
        self.assertEqual((w, h), (WIDTH, HEIGHT))

    def test_previews_in_one_layer_share_the_spare_height(self):
        # two Vue preview bodies already fill 680, so the area is made taller to leave spare height
        tall = replace(TOWER, preview=replace(TOWER.preview, min_height=1200))
        canvas, plan, result, _, _ = laid_out(two_previews(), tall)
        b_canvas, _, _, _, _ = C.laid_out(two_previews(), BENTO)
        grown = output_previews(canvas, plan)
        by_layer: dict[tuple[int, float], list[str]] = {}
        for key, n in grown.items():
            by_layer.setdefault((result.membership[key], n.pos[0]), []).append(key)
        [keys] = [keys for keys in by_layer.values() if len(keys) > 1]
        before = b_canvas.node_map()
        growth = [box_of(grown[k]).h - box_of(before[k]).h for k in keys]
        self.assertGreater(min(growth), 2 * GRID)
        self.assertLessEqual(max(growth) - min(growth), GRID)
        _, y, _, h = canvas.raw["groups"][result.membership[keys[0]]]["bounding"]
        self.assertEqual(h, 1200)
        self.assertEqual(max(box_of(grown[k]).bottom for k in keys), y + h - PAD)

    def test_the_missing_width_is_shared_by_the_layers_holding_previews(self):
        # two layers hold previews: each takes half of what the area lacks, so the group ends within
        # one grid step per layer of the area's width, not twice as wide
        canvas, plan, result, _, _ = laid_out(previews_in_two_layers())
        grown = output_previews(canvas, plan)
        self.assertEqual(len(grown), 2)
        self.assertEqual(len({n.pos[0] for n in grown.values()}), 2)
        [index] = {result.membership[key] for key in grown}
        _, _, w, _ = canvas.raw["groups"][index]["bounding"]
        self.assertGreaterEqual(w, WIDTH)
        self.assertLess(w, WIDTH + 2 * GRID)

    def test_other_nodes_keep_their_bento_heights(self):
        for name, build in CASES.items():
            with self.subTest(case=name):
                canvas, plan, _, _, _ = laid_out(build())
                b_canvas, _, _, _, _ = C.laid_out(build(), BENTO)
                grown = output_previews(canvas, plan)
                before = b_canvas.node_map()
                for key, n in canvas.node_map().items():
                    if key not in grown:
                        self.assertEqual(n.size[1], before[key].size[1], key)
                        self.assertEqual(n.collapsed, before[key].collapsed, key)

    def test_no_preview_node_no_area(self):
        canvas, _, result, _, _ = laid_out(no_preview())
        b_canvas, _, b_result, _, _ = C.laid_out(no_preview(), BENTO)
        self.assertEqual(canvas.raw["groups"], b_canvas.raw["groups"])
        self.assertEqual(
            {k: (n.pos, n.size) for k, n in canvas.node_map().items()},
            {k: (n.pos, n.size) for k, n in b_canvas.node_map().items()},
        )
        self.assertEqual([c.rect for c in result.columns], [c.rect for c in b_result.columns])

    def test_other_presets_grow_no_height(self):
        for preset in ("bento", "swimlanes", "stepped", "cockpit-split"):
            for name in ("txt2img", "two_stage", "long_chain_6", "two_previews"):
                with self.subTest(preset=preset, case=name):
                    canvas, _, _, _, before = C.laid_out(CASES[name](), get_preset(preset))
                    heights = {str(n["id"]): n["size"][1] for n in before["nodes"]}
                    self.assertEqual({k: n.size[1] for k, n in canvas.node_map().items()}, heights)


if __name__ == "__main__":
    unittest.main()
