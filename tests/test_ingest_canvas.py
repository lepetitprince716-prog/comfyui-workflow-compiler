import copy
import json
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.canvas.model import Canvas
from cwc.ingest import limits, load_canvas
from cwc.report import CwcError, ExitCode
from tests.fixtures.ingest import pngs


def load(doc) -> Canvas:
    return load_canvas(json.dumps(doc).encode())


def rejected(doc, code: str = "I1") -> CwcError:
    with unittest.TestCase().assertRaises(CwcError) as caught:
        load(doc)
    error = caught.exception
    assert error.exit_code == ExitCode.INPUT_ERROR
    assert {f.code.value for f in error.findings} == {code}, [f.code for f in error.findings]
    return error


def with_node(**changes) -> dict:
    doc = pngs.canvas_doc()
    doc["nodes"][0].update(changes)
    return doc


def messages(error: CwcError) -> str:
    return "\n".join(f.message for f in error.findings)


class AcceptedDocumentTest(unittest.TestCase):
    def test_a_minimal_canvas_loads_unchanged(self):
        doc = pngs.canvas_doc()
        self.assertEqual(load(doc).raw, doc)

    def test_unknown_keys_and_object_forms_are_accepted(self):
        doc = self.rich_doc()
        self.assertEqual(load(doc).raw, doc)

    def test_every_canvas_view_reads_an_accepted_document(self):
        canvas = load(self.rich_doc())
        for node in canvas.nodes:
            for name in ("id", "key", "type", "pos", "size", "mode", "flags", "collapsed", "pinned", "title",
                         "properties", "inputs", "outputs", "widgets_values", "widgets_values_named"):
                getattr(node, name)
            node.frame()
            list(canvas.iter_input_links(node))
        for group in canvas.groups:
            for name in ("id", "title", "bounding", "pinned"):
                getattr(group, name)
        for name in ("links", "groups", "extra", "reroutes", "link_extensions", "floating_links"):
            getattr(canvas, name)
        canvas.node_map()
        canvas.link_map()
        canvas.set_reroutes([], [])

    @staticmethod
    def rich_doc() -> dict:
        doc = pngs.canvas_doc()
        doc["x-vendor"] = {"k": 1}
        doc["nodes"][0].update(
            {"pos": {"0": 1, "1": 2}, "size": [3.5, 4], "mode": 4, "title": "t", "x-ext": [1], "widgets_values": {"a": 1}}
        )
        doc["nodes"][0]["inputs"] = [{"name": "a", "type": "X", "link": None}, {"name": "b", "link": 3}]
        doc["nodes"][0]["outputs"] = [{"name": "o", "type": "X", "links": None}, {"name": "p", "links": [1, 2]}]
        doc["nodes"].append({"id": "abc", "type": "T", "pos": [0, 0], "size": [1, 1], "flags": None, "properties": None})
        doc["links"] = [None, [3, 1, 0, "abc", 0, "X"], [4, 1, "0", 1, "1", "*"]]
        doc["groups"] = [{"title": "g", "bounding": [0, 0, 10, 10.5]}]
        doc["extra"] = {"reroutes": [{"id": 1}], "linkExtensions": [{"id": 3, "parentId": 1}]}
        return doc

    def test_missing_optional_tables_are_accepted(self):
        doc = {"nodes": [], "version": 0.4}
        self.assertEqual(load(doc).raw, doc)

    def test_digit_strings_in_other_scripts_are_refused(self):
        # ``"²".isdigit()`` is true and ``int("²")`` raises, so a later ``id_order`` or link view would crash.
        for digits in ("²", "٣"):
            with self.subTest(where="node id", digits=digits):
                doc = with_node(id=digits)
                doc["links"] = []
                self.assertIn("ASCII", messages(rejected(doc)))
            for position in (1, 3):
                with self.subTest(where=f"link[{position}]", digits=digits):
                    doc = pngs.canvas_doc()
                    doc["links"] = [[1, 1, 0, 2, 0, "X"]]
                    doc["links"][0][position] = digits
                    rejected(doc)
            for position in (2, 4):
                with self.subTest(where=f"link[{position}]", digits=digits):
                    doc = pngs.canvas_doc()
                    doc["links"] = [[1, 1, 0, 2, 0, "X"]]
                    doc["links"][0][position] = digits
                    self.assertIn("ASCII", messages(rejected(doc)))
        doc = pngs.canvas_doc()
        doc["links"] = [[1, "1", "0", 2, "0", "X"]]
        load(doc)

    def test_a_bom_is_accepted(self):
        data = b"\xef\xbb\xbf" + json.dumps(pngs.canvas_doc()).encode()
        self.assertEqual(load_canvas(data).raw, pngs.canvas_doc())


class DocumentShapeTest(unittest.TestCase):
    def test_not_an_object(self):
        for doc in ([], "x", 3, None):
            with self.subTest(doc=doc):
                rejected(doc)

    def test_an_api_prompt_is_refused_with_a_hint(self):
        error = rejected({"1": {"class_type": "A", "inputs": {}}})
        self.assertIn("API prompt", error.findings[0].message)
        self.assertIn("cwc compile", error.findings[0].hint)

    def test_no_nodes_list(self):
        for doc in ({}, {"version": 0.4}, {"nodes": {}, "version": 0.4}, {"nodes": None, "version": 0.4}):
            with self.subTest(doc=doc):
                rejected(doc)

    def test_version_must_be_0_4(self):
        for version in (None, 1, 2, 0.5, "0.4", True, [0.4]):
            with self.subTest(version=version):
                doc = pngs.canvas_doc()
                doc["version"] = version
                rejected(doc, "I2")
        doc = pngs.canvas_doc()
        del doc["version"]
        rejected(doc, "I2")

    def test_version_1_explains_the_subgraph_schema(self):
        doc = pngs.canvas_doc()
        doc["version"] = 1
        error = rejected(doc, "I2")
        self.assertIn("subgraph", error.findings[0].message)
        self.assertIn("0.4", error.findings[0].message)

    def test_node_and_link_limits_are_i3(self):
        doc = pngs.canvas_doc()
        doc["nodes"] = [{}] * (limits.MAX_NODES + 1)
        rejected(doc, "I3")
        doc = pngs.canvas_doc()
        doc["links"] = [None] * (limits.MAX_LINKS + 1)
        rejected(doc, "I3")

    def test_counts_at_the_limits_pass_the_count_check(self):
        doc = pngs.canvas_doc()
        doc["links"] = [None] * limits.MAX_LINKS
        self.assertEqual(len(load(doc).raw["links"]), limits.MAX_LINKS)

    def test_deep_nesting_is_i3(self):
        doc = pngs.canvas_doc()
        deep: list = []
        for _ in range(limits.MAX_DEPTH + 1):
            deep = [deep]
        doc["extra"] = {"deep": deep}
        rejected(doc, "I3")


class NodeShapeTest(unittest.TestCase):
    def assertBad(self, doc, fragment: str):
        self.assertIn(fragment, messages(rejected(doc)))

    def test_node_must_be_an_object(self):
        doc = pngs.canvas_doc()
        doc["nodes"].append(5)
        self.assertBad(doc, "nodes[1] is not an object")

    def test_id_and_type(self):
        for bad in (None, True, "", 1.5, [1]):
            with self.subTest(id=bad):
                self.assertBad(with_node(id=bad), "`id` must be")
        for bad in (None, "", 3):
            with self.subTest(type=bad):
                self.assertBad(with_node(type=bad), "`type` must be")

    def test_pos_and_size(self):
        for bad in (None, [1], [1, 2, 3], ["1", 2], [1, None], {"0": 1}, {"0": 1, "1": "x"}, "xy", [True, 1]):
            for key in ("pos", "size"):
                with self.subTest(key=key, value=bad):
                    self.assertBad(with_node(**{key: bad}), f"`{key}` must be")

    def test_optional_scalar_fields(self):
        cases = {"mode": ["4", 1.5, True, [], None], "title": [1, []], "flags": [[], "x"], "properties": [[]],
                 "widgets_values_named": [[1]], "widgets_values": ["x", 3]}
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertBad(with_node(**{key: value}), f"`{key}` must be")

    def test_slot_lists(self):
        self.assertBad(with_node(inputs={}), "`inputs` must be an array")
        self.assertBad(with_node(outputs="x"), "`outputs` must be an array")
        self.assertBad(with_node(inputs=[5]), "inputs[0] must be an object")
        self.assertBad(with_node(inputs=[{"name": 3}]), "inputs[0] must be an object")
        self.assertBad(with_node(outputs=[{"name": None}]), "outputs[0] must be an object")

    def test_slot_links(self):
        for bad in ("1", 1.5, True, [1]):
            self.assertBad(with_node(inputs=[{"name": "a", "link": bad}]), "inputs[0].link")
        for bad in ("x", [1.5], [True], 3):
            self.assertBad(with_node(outputs=[{"name": "a", "links": bad}]), "outputs[0].links")

    def test_problems_name_their_node(self):
        error = rejected(with_node(pos=None))
        self.assertEqual(error.findings[0].nodes, ("1",))
        self.assertIn("node 1", error.findings[0].message)

    def test_a_node_without_a_usable_id_is_named_by_position(self):
        error = rejected(with_node(id=None))
        self.assertIn("nodes[0]", error.findings[0].message)
        self.assertEqual(error.findings[0].nodes, ())


class TableShapeTest(unittest.TestCase):
    def assertBad(self, doc, fragment: str):
        self.assertIn(fragment, messages(rejected(doc)))

    def doc(self, **changes) -> dict:
        doc = pngs.canvas_doc()
        doc.update(changes)
        return doc

    def test_links_table(self):
        self.assertBad(self.doc(links={}), "`links` must be an array")
        self.assertBad(self.doc(links=None), "`links` must be an array")
        bad_links = [
            [1, 1, 0, 1],
            [1, 1, 0, 1, 0, "X", "extra"],
            ["1", 1, 0, 1, 0, "X"],
            [True, 1, 0, 1, 0, "X"],
            [1, None, 0, 1, 0, "X"],
            [1, 1, 0.5, 1, 0, "X"],
            [1, 1, 0, 1, "a", "X"],
            [1, 1, -1.5, 1, 0, "X"],
            [1, 1, 0, "", 0, "X"],
            "link",
            {},
        ]
        for link in bad_links:
            with self.subTest(link=link):
                self.assertBad(self.doc(links=[link]), "links[0] must be")

    def test_groups_table(self):
        self.assertBad(self.doc(groups={}), "`groups` must be an array")
        self.assertBad(self.doc(groups=None), "`groups` must be an array")
        for group in ({}, "g", {"bounding": [0, 0, 1]}, {"bounding": [0, 0, 1, "x"]}, {"bounding": None}):
            with self.subTest(group=group):
                self.assertBad(self.doc(groups=[group]), "groups[0]")

    def test_extra_table(self):
        self.assertBad(self.doc(extra=[]), "`extra` must be an object")
        self.assertBad(self.doc(extra={"reroutes": {}}), "`extra.reroutes`")
        self.assertBad(self.doc(extra={"reroutes": [1]}), "`extra.reroutes`")
        # 1e999 is legal JSON and reads as inf; a non-integer id would be truncated by the views.
        text = json.dumps(self.doc(extra={"reroutes": [{"id": 1, "pos": [12345.5, 0]}]})).replace("12345.5", "1e999")
        with self.assertRaises(CwcError) as caught:
            load_canvas(text.encode())
        self.assertIn("`extra.reroutes`", messages(caught.exception))
        for reroute in (
            {"id": 1, "pos": [0]},
            {"id": 1, "pos": [0, "1"]},
            {"id": 3.7, "pos": [0, 0]},
            {"pos": [0, 0]},
            {"id": 1, "parentId": "2"},
            {"id": 1, "linkIds": [1, None]},
        ):
            with self.subTest(reroute=reroute):
                self.assertBad(self.doc(extra={"reroutes": [reroute]}), "`extra.reroutes`")
        good = {"id": 2, "pos": [10.5, -4], "parentId": None, "linkIds": [3], "floating": {"slotType": "output"}}
        self.assertEqual(load(self.doc(extra={"reroutes": [good]})).reroutes, [good])
        for ext in ({"id": 1}, {"id": "1", "parentId": 2}, {"id": 1, "parentId": None}, 7):
            with self.subTest(ext=ext):
                self.assertBad(self.doc(extra={"linkExtensions": [ext]}), "`extra.linkExtensions`")

    def test_a_null_extra_would_break_the_canvas_views_and_is_refused(self):
        self.assertBad(self.doc(extra=None), "`extra` must be an object")

    def test_config_table(self):
        self.assertBad(self.doc(config=[]), "`config` is not an object")

    def test_every_problem_is_listed_in_one_error(self):
        doc = self.doc(links=[[1]], groups=[{}])
        doc["nodes"][0]["pos"] = None
        error = rejected(doc)
        self.assertEqual(len(error.findings), 3)

    def test_long_problem_lists_are_cut_and_summarised(self):
        doc = pngs.canvas_doc()
        doc["nodes"] = [{"id": i, "type": "T", "pos": None, "size": [1, 1]} for i in range(limits.MAX_REPORTED + 5)]
        error = rejected(doc)
        self.assertEqual(len(error.findings), limits.MAX_REPORTED + 1)
        self.assertEqual(error.findings[-1].message, "and 5 more shape problems")

    def test_exactly_the_cap_is_not_summarised(self):
        doc = pngs.canvas_doc()
        doc["nodes"] = [{"id": i, "type": "T", "pos": None, "size": [1, 1]} for i in range(limits.MAX_REPORTED)]
        error = rejected(doc)
        self.assertEqual(len(error.findings), limits.MAX_REPORTED)

    def test_the_document_is_not_modified(self):
        doc = pngs.canvas_doc()
        before = copy.deepcopy(doc)
        load(doc)
        self.assertEqual(doc, before)


if __name__ == "__main__":
    unittest.main()
