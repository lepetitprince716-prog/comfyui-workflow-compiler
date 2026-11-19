"""The bundled data files as ``cwc.resources`` hands them out."""

import json
import unittest

from tests import support  # noqa: F401 - puts src/ on the path

from cwc import resources

DATA = support.ROOT / "src" / "cwc" / "data"


class BundledDataTest(unittest.TestCase):
    def test_every_json_file_of_the_data_package_is_served_as_it_is_on_disk(self):
        names = sorted(path.name for path in DATA.glob("*.json"))
        self.assertTrue(names)
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(resources.read_bytes(name), (DATA / name).read_bytes())
                self.assertEqual(resources.read_json(name), json.loads((DATA / name).read_bytes()))

    def test_a_missing_file_is_reported_by_name(self):
        with self.assertRaises(FileNotFoundError) as caught:
            resources.read_bytes("no-such-file.json")
        self.assertEqual(str(caught.exception), "bundled data file missing: no-such-file.json")

    def test_a_folder_is_not_a_data_file(self):
        with self.assertRaises(FileNotFoundError):
            resources.read_bytes("")


if __name__ == "__main__":
    unittest.main()
