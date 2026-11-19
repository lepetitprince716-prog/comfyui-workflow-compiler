"""render_svg and ``cwc render``: structure, escaping, determinism and the command."""

from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from tests.support import FIXTURES

from cwc.canvas.envelope import Renderer, box_facts, classic_box, slot_anchors, vue_box
from cwc.canvas.links import canvas_splines
from cwc.canvas.model import Canvas
from cwc.cli import main
from cwc.ingest import load_canvas
from cwc.nodedefs.registry import NodeDefs
from cwc.render import render_svg
from cwc.render.svg import render
from cwc.report import ExitCode

SVG = "{http://www.w3.org/2000/svg}"
LAYOUT = FIXTURES / "layout"


class NoDefs:
    def get(self, type: str) -> None:
        return None


def node(key: int, x: float, y: float, *, title: str | None = None, mode: int = 0, collapsed: bool = False) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": key,
        "type": "T",
        "pos": [x, y],
        "size": [300, 100],
        "flags": {"collapsed": True} if collapsed else {},
        "order": 0,
        "mode": mode,
        "inputs": [{"name": "a", "type": "X", "link": None}],
        "outputs": [{"name": "o", "type": "X", "links": []}],
        "properties": {},
    }
    if title is not None:
        raw["title"] = title
    return raw


def canvas(nodes: list[dict], links: list[list] | None = None, groups: list[dict] | None = None, extra: dict | None = None) -> Canvas:
    return Canvas(
        {
            "last_node_id": len(nodes),
            "last_link_id": len(links or []),
            "nodes": nodes,
            "links": links or [],
            "groups": groups or [],
            "config": {},
            "extra": extra or {},
            "version": 0.4,
        }
    )


def parse(svg: str) -> ET.Element:
    return ET.fromstring(svg.encode("utf-8"))


def by_class(root: ET.Element, tag: str, cls: str) -> list[ET.Element]:
    return [el for el in root.iter(SVG + tag) if cls in (el.get("class") or "").split()]


class Structure(unittest.TestCase):
    def test_one_element_per_group_node_and_link(self) -> None:
        cv = canvas(
            [node(1, 0, 0), node(2, 500, 0), node(3, 1000, 0, mode=4), node(4, 1500, 0, collapsed=True)],
            links=[[1, 1, 0, 2, 0, "X"], [2, 2, 0, 3, 0, "X"], [3, 1, 0, 9, 0, "X"], [4, 3, 5, 4, 0, "X"]],
            groups=[
                {"title": "A", "bounding": [-20, -60, 400, 300], "color": "#335"},
                {"title": "B", "bounding": [460, -60, 400, 300]},
            ],
        )
        root = parse(render_svg(cv, NoDefs()))
        self.assertEqual(len(by_class(root, "g", "group")), 2)
        self.assertEqual(len(by_class(root, "g", "node")), 4)
        links = by_class(root, "path", "link")
        self.assertEqual(sorted(el.get("id") for el in links), ["link-1", "link-2"])  # 3: no end node; 4: no slot 5
        self.assertEqual(len(by_class(root, "g", "bypassed")), 1)
        self.assertEqual(len(by_class(root, "g", "collapsed")), 1)
        self.assertEqual(len(by_class(root, "rect", "classic")), 4)
        self.assertEqual(len(by_class(root, "rect", "vue")), 4)
        self.assertIsNotNone(root.find(f".//{SVG}rect[@id='first-screen']"))

    def test_vue_box_is_dashed_and_classic_is_solid(self) -> None:
        root = parse(render_svg(canvas([node(1, 0, 0)]), NoDefs()))
        self.assertIsNone(by_class(root, "rect", "classic")[0].get("stroke-dasharray"))
        self.assertIsNotNone(by_class(root, "rect", "vue")[0].get("stroke-dasharray"))

    def test_view_box_holds_every_box_with_a_margin(self) -> None:
        cv = canvas([node(1, -500, -400), node(2, 3000, 2000)], groups=[{"title": "G", "bounding": [-600, -500, 100, 100]}])
        root = parse(render_svg(cv, NoDefs()))
        x, y, w, h = (float(v) for v in root.get("viewBox").split())
        self.assertLessEqual(x, -600 - 40)
        self.assertLessEqual(y, -500 - 40)
        self.assertGreaterEqual(x + w, 3300 + 40)
        self.assertGreaterEqual(y + h, 2000 + 24 + 40)

    def test_empty_canvas_shows_the_first_screen(self) -> None:
        root = parse(render_svg(Canvas.empty(), NoDefs()))
        self.assertEqual(root.get("viewBox"), "-40 -40 1680 980")
        self.assertEqual(by_class(root, "g", "node"), [])

    def test_group_without_a_usable_colour_gets_the_default(self) -> None:
        cv = canvas([], groups=[{"title": "G", "bounding": [0, 0, 10, 10], "color": '"/><script/>'}])
        svg = render_svg(cv, NoDefs())
        self.assertNotIn("script", svg)
        self.assertIn("#3f789e", svg)


class Links(unittest.TestCase):
    def test_reroutes_turn_a_link_into_cubic_segments(self) -> None:
        extra = {
            "reroutes": [{"id": 1, "pos": [400, 300], "linkIds": [1]}, {"id": 2, "pos": [450, 350], "parentId": 1, "linkIds": [1]}],
            "linkExtensions": [{"id": 1, "parentId": 2}],
        }
        cv = canvas([node(1, 0, 0), node(2, 800, 0)], links=[[1, 1, 0, 2, 0, "X"], [2, 1, 0, 2, 0, "X"]], extra=extra)
        root = parse(render_svg(cv, NoDefs()))
        paths = {el.get("id"): el for el in by_class(root, "path", "link")}
        self.assertEqual(paths["link-1"].get("class"), "link rerouted")
        d = paths["link-1"].get("d")
        self.assertNotIn("L", d)
        self.assertEqual((d.count("M"), d.count("C")), (1, 3))  # output -> reroute 1 -> reroute 2 -> input
        self.assertLess(d.index("400 300"), d.index("450 350"))  # output end first, parentId chain in order
        self.assertEqual(paths["link-2"].get("class"), "link")
        self.assertEqual(paths["link-2"].get("d").count("C"), 1)
        self.assertEqual(len(root.findall(f".//{SVG}circle[@class='reroute']")), 2)

    def test_a_segment_two_links_share_is_drawn_once(self) -> None:
        extra = {
            "reroutes": [{"id": 1, "pos": [400, 300], "linkIds": [1, 2]}, {"id": 2, "pos": [500, 350], "parentId": 1, "linkIds": [1]}],
            "linkExtensions": [{"id": 1, "parentId": 2}, {"id": 2, "parentId": 1}],
        }
        cv = canvas([node(1, 0, 0), node(2, 800, 0), node(3, 800, 400)], links=[[1, 1, 0, 2, 0, "X"], [2, 1, 0, 3, 0, "X"]], extra=extra)
        paths = {el.get("id"): el.get("d") for el in by_class(parse(render_svg(cv, NoDefs())), "path", "link")}
        self.assertEqual(paths["link-1"].count("C"), 3)  # output -> 1 -> 2 -> input
        self.assertEqual((paths["link-2"].count("M"), paths["link-2"].count("C")), (1, 1))  # only reroute 1 -> input

    def test_the_recorded_canvas_draws_the_recorded_number_of_segments(self) -> None:
        raw = json.loads((LAYOUT / "reroutes.canvases.json").read_text(encoding="utf-8"))["main"]
        root = parse(render_svg(Canvas(raw), NodeDefs.load()))
        paths = by_class(root, "path", "link")
        self.assertEqual(sum(el.get("d").count("C") for el in paths), 12)  # the frame's drawLink calls
        self.assertEqual(len(root.findall(f".//{SVG}circle[@class='reroute']")), 7)

    def test_reroute_cycle_and_missing_point_do_not_loop(self) -> None:
        extra = {
            "reroutes": [{"id": 1, "pos": [400, 300], "parentId": 2}, {"id": 2, "pos": [450, 350], "parentId": 1}, {"id": 3}],
            "linkExtensions": [{"id": 1, "parentId": 1}, {"id": 2, "parentId": 3}],
        }
        cv = canvas([node(1, 0, 0), node(2, 800, 0)], links=[[1, 1, 0, 2, 0, "X"], [2, 1, 0, 2, 0, "X"]], extra=extra)
        root = parse(render_svg(cv, NoDefs()))
        self.assertEqual(len(by_class(root, "path", "link")), 2)


    def test_a_link_listed_twice_is_drawn_once(self) -> None:
        cv = canvas([node(1, 0, 0), node(2, 800, 0)], links=[[1, 1, 0, 2, 0, "X"], [1, 1, 0, 2, 0, "X"]])
        rendering = render(cv, NoDefs())
        ids = [el.get("id") for el in by_class(parse(rendering.svg), "path", "link")]
        self.assertEqual(ids, ["link-1"])
        self.assertEqual((rendering.links, rendering.links_unresolved), (1, 0))


class Geometry(unittest.TestCase):
    """The drawn boxes, slot dots and link ends are the envelope and link models' numbers."""

    def test_boxes_slots_and_link_ends_match_the_models(self) -> None:
        defs = NodeDefs.load()
        cv = load_canvas((LAYOUT / "boxes.canvas.json").read_bytes())
        root = parse(render_svg(cv, defs))
        facts = {n.key: box_facts(n, defs.get(n.type)) for n in cv.nodes}
        for n in cv.nodes:
            g = root.find(f".//{SVG}g[@id='node-{n.key}']")
            self.assertIsNotNone(g, n.key)
            for cls, box in (("classic", classic_box(n, facts[n.key])), ("vue", vue_box(n, facts[n.key]))):
                rect = g.find(f"{SVG}rect[@class='{cls}']")
                drawn = [float(rect.get(k)) for k in ("x", "y", "width", "height")]
                self.assertEqual(drawn, [round(v, 2) for v in (box.x, box.y, box.w, box.h)], (n.key, cls))
            inputs, outputs = slot_anchors(n, facts[n.key], Renderer.CLASSIC)
            for kind, points in (("in", inputs), ("out", outputs)):
                dots = {(float(c.get("cx")), float(c.get("cy"))) for c in g.iter(SVG + "circle") if c.get("class") == f"slot {kind}"}
                self.assertEqual(dots, {(round(x, 2), round(y, 2)) for x, y in points}, (n.key, kind))
        splines = canvas_splines(cv, facts, Renderer.CLASSIC)
        paths = {el.get("id"): el.get("d") for el in by_class(root, "path", "link")}
        self.assertEqual(len(paths), len(splines))
        for link_id, curve in splines.items():
            numbers = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", paths[f"link-{link_id}"])]
            ends = [round(v, 2) for v in (curve.p0[0], curve.p0[1], curve.p3[0], curve.p3[1])]
            self.assertEqual(numbers[:2] + numbers[-2:], ends, link_id)


class Escaping(unittest.TestCase):
    def test_markup_in_titles_is_text(self) -> None:
        nasty = "a<b>&\"c'd</title><script>"
        cv = canvas([node(1, 0, 0, title=nasty)], groups=[{"title": nasty, "bounding": [0, 0, 500, 500]}])
        svg = render_svg(cv, NoDefs())
        root = parse(svg)
        texts = [el.text for el in root.iter(SVG + "text")]
        self.assertEqual(texts.count(nasty), 2)
        self.assertNotIn("<script>", svg)
        self.assertNotIn("<b>", svg)

    def test_characters_xml_cannot_hold_are_dropped(self) -> None:
        cv = canvas([node(1, 0, 0, title="a\x00b\x0bc\ud800d")])
        root = parse(render_svg(cv, NoDefs()))
        self.assertEqual([el.text for el in by_class(root, "text", "title")], ["abcd"])

    def test_node_id_with_markup_stays_inside_the_attribute(self) -> None:
        raw = node(1, 0, 0)
        raw["id"] = 'x"><g id="evil'
        root = parse(render_svg(canvas([raw]), NoDefs()))
        self.assertEqual(len(by_class(root, "g", "node")), 1)
        self.assertIsNone(root.find(f".//{SVG}g[@id='evil']"))


class Fixtures(unittest.TestCase):
    def test_layout_fixtures_render_with_real_definitions(self) -> None:
        defs = NodeDefs.load()
        for name, groups, nodes, links in (("boxes", 5, 8, 10), ("links", 0, 4, 3)):
            cv = load_canvas((LAYOUT / f"{name}.canvas.json").read_bytes())
            svg = render_svg(cv, defs)
            root = parse(svg)
            self.assertEqual(len(by_class(root, "g", "group")), groups, name)
            self.assertEqual(len(by_class(root, "g", "node")), nodes, name)
            self.assertEqual(len(by_class(root, "path", "link")), links, name)
            self.assertEqual(svg, render_svg(cv, defs), name)

    def test_widget_labels_come_from_the_definition(self) -> None:
        cv = load_canvas((LAYOUT / "boxes.canvas.json").read_bytes())
        root = parse(render_svg(cv, NodeDefs.load()))
        labels = {el.text for el in by_class(root, "text", "widget")}
        self.assertTrue({"ckpt_name", "seed", "steps", "cfg"} <= labels)

    def test_same_canvas_gives_identical_bytes(self) -> None:
        defs = NodeDefs.load()
        data = (LAYOUT / "boxes.canvas.json").read_bytes()
        self.assertEqual(render_svg(load_canvas(data), defs).encode(), render_svg(load_canvas(data), defs).encode())


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out, err)
    return code, out.getvalue(), err.getvalue()


class Command(unittest.TestCase):
    def test_writes_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.svg"
            code, out, err = run("render", str(LAYOUT / "boxes.canvas.json"), "-o", str(target))
            self.assertEqual((code, err), (ExitCode.OK, ""))
            self.assertEqual(out, f"wrote {target}\n")
            self.assertEqual(len(by_class(ET.fromstring(target.read_bytes()), "g", "node")), 8)

    def test_json_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.svg"
            code, out, _ = run("render", str(LAYOUT / "links.canvas.json"), "-o", str(target), "--json")
            self.assertEqual(code, ExitCode.OK)
            doc = json.loads(out)
            self.assertEqual(doc["output"], str(target))
            self.assertEqual(doc["drawn"], {"groups": 0, "nodes": 4, "links": 3, "links_unresolved": 0, "reroutes": 0})
            self.assertEqual(len(doc["view_box"]), 4)

    def test_missing_input_exits_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.svg"
            code, out, err = run("render", str(Path(tmp) / "nope.json"), "-o", str(target))
            self.assertEqual((code, out), (ExitCode.INPUT_ERROR, ""))
            self.assertIn("cannot read", err)
            self.assertFalse(target.exists())
            code, out, _ = run("render", str(Path(tmp) / "nope.json"), "-o", str(target), "--json")
            self.assertEqual(code, ExitCode.INPUT_ERROR)
            self.assertEqual(json.loads(out)["ok"], False)

    def test_unwritable_output_exits_5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run("render", str(LAYOUT / "links.canvas.json"), "-o", str(Path(tmp) / "no" / "dir" / "out.svg"))
            self.assertEqual(code, ExitCode.WRITE_FAILED)
            self.assertIn("cannot write", err)

    def test_missing_nodedefs_exits_4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = run("render", str(LAYOUT / "links.canvas.json"), "-o", str(Path(tmp) / "o.svg"), "--nodedefs", str(Path(tmp) / "none.json"))
            self.assertEqual(code, ExitCode.NODEDEFS_MISSING)

    def test_output_is_required(self) -> None:
        self.assertEqual(run("render", str(LAYOUT / "links.canvas.json"))[0], ExitCode.USAGE)


if __name__ == "__main__":
    unittest.main()
