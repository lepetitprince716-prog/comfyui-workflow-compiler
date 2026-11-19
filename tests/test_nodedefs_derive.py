import json
import os
import sys
import unittest

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.nodedefs._frontend import frontend
from cwc.nodedefs.derive import from_object_info, slot_layout, widget_layout
from cwc.nodedefs.types import SlotSpec, Tier, WidgetKind
from cwc.resources import read_json

SAMPLE = json.loads((support.FIXTURES / "nodedefs" / "core_sample.json").read_text(encoding="utf-8"))["nodes"]
TRUTH = support.ROOT / "src" / "cwc" / "data" / "core_truth.json"
CORE = support.ROOT / "src" / "cwc" / "data" / "core_nodedefs.json"


def derive(type_name):
    return from_object_info(type_name, SAMPLE[type_name])


def names(widgets):
    return [w.name for w in widgets]


def slots(nodedef, values=None):
    return [(s.name, s.type, s.widget) for s in slot_layout(nodedef, values)]


def synthetic(required=None, optional=None, **extra):
    return {"input": {"required": required or {}, "optional": optional or {}}, "output": [], **extra}


def core_object_info():
    """All core /object_info entries: $CWC_OBJECT_INFO (a JSON export) or the bundled snapshot."""
    path = os.environ.get("CWC_OBJECT_INFO")
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if CORE.is_file():
        return json.loads(CORE.read_bytes())["nodes"]
    return None


class ValueControlTest(unittest.TestCase):
    def test_seed_is_followed_by_control_after_generate(self):
        nd = derive("KSampler")
        self.assertEqual(nd.tier, Tier.DERIVED)
        self.assertEqual(
            names(nd.widgets),
            ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
        )
        control = nd.widgets[1]
        self.assertEqual((control.kind, control.input_name, control.default, control.to_prompt),
                         (WidgetKind.CONTROL, "seed", "randomize", False))
        self.assertTrue(nd.layout_complete and nd.names_complete)

    def test_control_mode_from_spec_string(self):
        nd = derive("SeedNode")
        self.assertEqual([(w.name, w.default) for w in nd.widgets], [("seed", 0), ("control_after_generate", "fixed")])

    def test_combo_with_control_has_three_positions(self):
        info = synthetic({"mode": ["COMBO", {"options": ["a", "b"], "control_after_generate": True}]})
        nd = from_object_info("T", info)
        self.assertEqual([(w.name, w.kind, w.default, w.to_prompt) for w in nd.widgets], [
            ("mode", WidgetKind.VALUE, "a", True),
            ("control_after_generate", WidgetKind.CONTROL, "randomize", False),
            ("control_filter_list", WidgetKind.FILTER, "", False),
        ])

    def test_control_name_override_and_prefix(self):
        info = synthetic({
            "pick": [["x", "y"], {"control_after_generate": "pick mode", "control_filter_list": "pick filter"}],
            "n": ["INT", {"control_after_generate": True, "control_prefix": "n"}],
            "seed": ["INT", {"control_after_generate": False}],
        })
        nd = from_object_info("T", info)
        self.assertEqual([(w.name, w.default) for w in nd.widgets], [
            ("pick", "x"), ("pick mode", "randomize"), ("pick filter", ""),
            ("n", 0), ("n control_after_generate", "randomize"),
            ("seed", 0),
        ])

    def test_float_control_defaults_to_fixed(self):
        nd = from_object_info("T", synthetic({"f": ["FLOAT", {"control_after_generate": "randomize"}]}))
        self.assertEqual([(w.name, w.default) for w in nd.widgets], [("f", 0), ("control_after_generate", "fixed")])


class UploadTest(unittest.TestCase):
    def test_image_upload_comes_after_all_inputs(self):
        nd = derive("LoadImage")
        self.assertEqual(nd.inputs[-1].name, "upload")
        self.assertEqual([(w.name, w.kind, w.default, w.to_prompt) for w in nd.widgets[-1:]],
                         [("upload", WidgetKind.UPLOAD, "image", False)])
        self.assertEqual(slots(nd), [("image", "COMBO", "image"), ("upload", "IMAGEUPLOAD", "upload")])

    def test_upload_follows_optional_inputs_listed_in_input_order(self):
        info = synthetic(
            {"image": [["a.png"], {"image_upload": True}]},
            {"extra": ["STRING", {}]},
            input_order={"required": ["image"], "optional": ["extra"]},
        )
        self.assertEqual(names(from_object_info("T", info).widgets), ["image", "extra", "upload"])

    def test_audio_upload_value_is_empty(self):
        nd = derive("LoadAudio")
        self.assertEqual([(w.name, w.default) for w in nd.widgets], [("audio", None), ("upload", "")])
        self.assertFalse(nd.layout_complete)  # a frontend extension adds audioUI by node name

    def test_remote_combo_companions(self):
        nd = derive("LoadImageOutput")
        self.assertEqual([(w.name, w.default, w.to_prompt) for w in nd.widgets], [
            ("image", "Loading...", True),
            ("Auto-refresh after generation", False, False),
            ("refresh", "refresh", True),
            ("upload", "image", False),
        ])


class DynamicInputTest(unittest.TestCase):
    def test_dynamic_combo_children_follow_their_combo(self):
        nd = derive("ResizeImageMaskNode")
        self.assertEqual(names(nd.widgets), ["resize_type", "scale_method"])
        self.assertEqual(
            names(widget_layout(nd)),
            ["resize_type", "resize_type.width", "resize_type.height", "resize_type.crop", "scale_method"],
        )
        positional = widget_layout(nd, ["scale by multiplier", 1.5, "area"])
        self.assertEqual(names(positional), ["resize_type", "resize_type.multiplier", "scale_method"])
        self.assertEqual(names(widget_layout(nd, {"resize_type": "not an option"})), ["resize_type", "scale_method"])
        self.assertEqual(nd.widgets[0].default, "scale dimensions")

    def test_dynamic_combo_slot_order(self):
        nd = derive("ResizeImageMaskNode")
        fresh = [s[0] for s in slots(nd)]
        self.assertEqual(fresh, ["input", "resize_type.width", "resize_type.height", "resize_type.crop",
                                 "resize_type", "scale_method"])
        assigned = [s[0] for s in slots(nd, {"resize_type": "scale by multiplier"})]
        self.assertEqual(assigned, ["input", "resize_type", "resize_type.multiplier", "scale_method"])

    def test_match_type(self):
        nd = derive("ResizeImageMaskNode")
        self.assertEqual(slots(nd)[0], ("input", "IMAGE,MASK", None))
        self.assertEqual(nd.outputs[0].type, "*")

    def test_autogrow_prefix_slots(self):
        nd = derive("BatchImagesNode")
        self.assertEqual(nd.widgets, ())
        self.assertEqual(slot_layout(nd), (
            SlotSpec("images.image0", "IMAGE", None, False),
            SlotSpec("images.image1", "IMAGE", None, True),
        ))

    def test_autogrow_names_slots(self):
        nd = derive("StringFormat")
        grow = nd.input("values").autogrow
        self.assertEqual((grow.min, grow.max, grow.names[:2]), (0, 26, ("a", "b")))
        self.assertEqual(slot_layout(nd)[0], SlotSpec("values.a", "*", None, True))
        self.assertEqual(names(nd.widgets), ["f_string"])

    def test_autogrow_without_prefix_uses_empty_prefix(self):
        info = synthetic({"g": ["COMFY_AUTOGROW_V3", {"template": {"input": {"required": {"x": ["IMAGE", {}]}}}}]})
        self.assertEqual([s.name for s in slot_layout(from_object_info("T", info))], ["g.0", "g.1"])

    def test_dynamic_group_is_not_modelled(self):
        nd = derive("LoadLoraModel")
        self.assertTrue(nd.input("loras").socketless)
        self.assertEqual(names(nd.widgets)[0], "loras")
        self.assertFalse(nd.layout_complete)
        self.assertFalse(nd.names_complete)


class WidgetDecisionTest(unittest.TestCase):
    def test_force_input_makes_a_socket(self):
        nd = derive("SDPoseKeypointExtractor")
        spec = nd.input("bboxes")
        self.assertFalse(spec.widget)
        self.assertNotIn("bboxes", names(nd.widgets))
        self.assertIn(("bboxes", "BOUNDING_BOX", None), slots(nd))

    def test_socketless_option_keeps_the_classic_slot(self):
        nd = derive("ImageCropV2")
        self.assertFalse(nd.input("crop_region").socketless)
        self.assertIn(("crop_region", "BOUNDING_BOX", "crop_region"), slots(nd))
        self.assertEqual([(w.name, w.kind, w.to_prompt) for w in nd.widgets], [
            ("crop_region", WidgetKind.VALUE, True),
            ("x", WidgetKind.COMPANION, False),
            ("y", WidgetKind.COMPANION, False),
            ("width", WidgetKind.COMPANION, False),
            ("height", WidgetKind.COMPANION, False),
        ])
        self.assertEqual(nd.widgets[3].default, 512)

    def test_widget_that_is_not_persisted_still_has_a_slot(self):
        nd = derive("ImageCompare")
        self.assertEqual(nd.widgets, ())
        self.assertEqual(slots(nd)[-1], ("compare_view", "IMAGECOMPARE", "compare_view"))

    def test_patched_type_is_incomplete(self):
        nd = derive("CustomCombo")
        self.assertFalse(nd.layout_complete)
        self.assertFalse(nd.names_complete)

    def test_has_default_only_for_an_explicit_key(self):
        nd = from_object_info("T", synthetic({"a": ["INT", {}], "b": ["INT", {"default": None}]}))
        self.assertEqual([(s.has_default, s.default) for s in nd.inputs], [(False, None), (True, None)])
        self.assertEqual([w.default for w in nd.widgets], [0, 0])

    def test_combo_default_uses_js_truthiness(self):
        info = synthetic({"a": [["x", "y"], {"default": ""}], "b": [["x", "y"], {"default": "y"}], "c": [[], {}]})
        self.assertEqual([w.default for w in from_object_info("T", info).widgets], ["x", "y", None])

    def test_widget_type_override(self):
        info = synthetic({"rate": ["FLOAT,INT", {"widgetType": "FLOAT", "default": 25.0}]})
        nd = from_object_info("T", info)
        self.assertEqual(slots(nd), [("rate", "FLOAT,INT", "rate")])
        self.assertEqual(nd.widgets[0].default, 25.0)


class OrderTest(unittest.TestCase):
    def test_integer_keys_first_and_optional_overrides_in_place(self):
        info = synthetic(
            {"b": ["INT", {}], "10": ["INT", {}], "a": ["INT", {}], "2": ["INT", {}]},
            {"a": ["INT", {}], "1": ["INT", {}]},
        )
        nd = from_object_info("T", info)
        self.assertEqual([(s.name, s.required) for s in nd.inputs],
                         [("1", False), ("2", True), ("10", True), ("b", True), ("a", False)])

    def test_input_order_then_object_order(self):
        info = synthetic(
            {"x": ["INT", {}], "y": ["INT", {}], "both": ["INT", {}]},
            {"both": ["INT", {}], "z": ["INT", {}]},
            input_order={"required": ["y", "both"], "optional": ["z"]},
        )
        # "both" became optional, so the required pass skips it; it lands with the rest.
        self.assertEqual([s.name for s in from_object_info("T", info).inputs], ["y", "z", "x", "both"])

    def test_sockets_before_widgets(self):
        info = synthetic({"w": ["INT", {}], "m": ["MODEL", {}]}, {"o": ["LATENT", {}]})
        self.assertEqual(slots(from_object_info("T", info)), [("m", "MODEL", None), ("o", "LATENT", None), ("w", "INT", "w")])

    def test_output_names(self):
        info = synthetic(output=["IMAGE", ["a", "b"], "COMFY_MATCHTYPE_V3"], output_name=["", "pick"],
                         output_is_list=[False, True])
        nd = from_object_info("T", info)
        self.assertEqual([(o.name, o.type, o.is_list) for o in nd.outputs],
                         [("output_0", "IMAGE", False), ("pick", "COMBO", True), ("output_2", "*", False)])

    def test_malformed_entry_raises(self):
        with self.assertRaises(ValueError):
            from_object_info("T", synthetic({"a": "INT"}))


class BuiltinsDataTest(unittest.TestCase):
    def test_builtin_nodes(self):
        doc = read_json("frontend_builtins.json")
        self.assertEqual(doc["format"], 1)
        nodes = doc["nodes"]
        self.assertEqual({n["behavior"] for n in nodes.values()}, {"note", "passthrough", "primitive"})
        for node in nodes.values():
            self.assertTrue(node["virtual"])
            self.assertIsInstance(node["inputs"], list)
            self.assertIsInstance(node["widgets"], list)
        self.assertEqual(sorted(n for n, v in nodes.items() if not v["widgets_known"]),
                         [n for n, v in nodes.items() if v["behavior"] == "primitive"])

    def test_widget_types_parse(self):
        fe = frontend()
        for basic in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"):
            self.assertIn(basic, fe.widget_types)
        self.assertEqual(len(fe.widget_types["BOUNDING_BOX"].followers), 4)
        self.assertTrue(fe.patched_types)


class CoreTest(unittest.TestCase):
    def test_every_core_type_derives(self):
        info = core_object_info()
        if info is None:
            self.skipTest("no core /object_info export (set CWC_OBJECT_INFO or add src/cwc/data/core_nodedefs.json)")
        for type_name, entry in info.items():
            with self.subTest(type=type_name):
                nd = from_object_info(type_name, entry)
                widget_layout(nd)
                slot_layout(nd)

    def test_widget_names_against_truth(self):
        """Measurement, not a gate: share of types whose widget names match the truth snapshot."""
        info = core_object_info()
        if info is None or not TRUTH.is_file():
            self.skipTest("needs a core /object_info export and src/cwc/data/core_truth.json")
        truth = json.loads(TRUTH.read_bytes())["nodes"]
        counts = {True: [0, 0], False: [0, 0]}
        mismatched = []
        for type_name in sorted(set(info) & set(truth)):
            nd = from_object_info(type_name, info[type_name])
            same = names(widget_layout(nd)) == [w["name"] for w in truth[type_name]["widgets"]]
            counts[nd.layout_complete][0] += same
            counts[nd.layout_complete][1] += 1
            if not same:
                mismatched.append(f"{type_name}{'' if nd.layout_complete else ' (layout_complete=False)'}")
        total = sum(c[1] for c in counts.values())
        agree = sum(c[0] for c in counts.values())
        print(f"\nderive vs truth widget names: {agree}/{total} agree; "
              f"layout_complete {counts[True][0]}/{counts[True][1]}, "
              f"incomplete {counts[False][0]}/{counts[False][1]}", file=sys.stderr)
        for line in mismatched:
            print(f"  mismatch: {line}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
