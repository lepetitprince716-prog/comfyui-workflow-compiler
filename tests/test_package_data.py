"""The built wheel carries every bundled data file, and the installed commands read them.

A file left out of ``package-data`` still works from the source tree, so only a real build
shows it. The wheel is built from a copy of the project (the source tree stays clean), without
build isolation or a package index, and run from a folder outside the checkout with the wheel's
contents as the only ``cwc`` on the path.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests import support
from tests.graphs import txt2img

DATA = support.ROOT / "src" / "cwc" / "data"
PROJECT_FILES = ("pyproject.toml", "README.md", "LICENSE")


def _data_files() -> list[str]:
    return sorted(p.name for p in DATA.iterdir() if p.is_file() and p.suffix != ".pyc")


@unittest.skipUnless(importlib.util.find_spec("setuptools"), "setuptools is needed to build the wheel")
@unittest.skipUnless(importlib.util.find_spec("pip"), "pip is needed to build the wheel")
class BuiltWheelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        project = tmp / "project"
        project.mkdir()
        for name in PROJECT_FILES:
            shutil.copy2(support.ROOT / name, project / name)
        shutil.copytree(
            support.ROOT / "src",
            project / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "*.pyc"),
        )
        wheels = tmp / "wheels"
        build = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "-q", "-w", str(wheels), str(project)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            raise AssertionError(f"wheel build failed:\n{build.stdout}\n{build.stderr}")
        [cls.wheel] = wheels.glob("*.whl")
        cls.site = tmp / "site"
        with zipfile.ZipFile(cls.wheel) as archive:
            archive.extractall(cls.site)
        cls.work = tmp / "work"
        cls.work.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def cwc(self, *args):
        environment = dict(os.environ, PYTHONPATH=str(self.site), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-m", "cwc", *map(str, args)], cwd=self.work, env=environment, capture_output=True, text=True
        )

    def test_every_data_file_is_in_the_wheel(self):
        with zipfile.ZipFile(self.wheel) as archive:
            packed = {name.removeprefix("cwc/data/") for name in archive.namelist() if name.startswith("cwc/data/")}
        for name in _data_files():
            with self.subTest(name=name):
                self.assertIn(name, packed)

    def test_the_commands_run_from_the_wheel(self):
        environment = dict(os.environ, PYTHONPATH=str(self.site))
        where = subprocess.run(
            [sys.executable, "-c", "import cwc; print(cwc.__file__)"], cwd=self.work, env=environment, capture_output=True, text=True
        )
        self.assertTrue(where.stdout.strip().startswith(str(self.site)), where.stdout + where.stderr)

        guide = self.cwc("guide")
        self.assertEqual(guide.returncode, 0, guide.stderr)
        self.assertEqual(guide.stdout, (DATA / "guide.md").read_text(encoding="utf-8"))

        presets = self.cwc("presets")
        self.assertEqual(presets.returncode, 0, presets.stderr)
        self.assertIn("bento", presets.stdout)

        (self.work / "graph.json").write_text(json.dumps(txt2img()), encoding="utf-8")
        compiled = self.cwc("compile", "graph.json", "-o", "out")
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertTrue((self.work / "out" / "graph.ui.json").stat().st_size > 0)
        self.assertTrue((self.work / "out" / "graph.api.json").stat().st_size > 0)


class BuiltPyzTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.dist = tmp / "dist"
        build = subprocess.run(
            [sys.executable, str(support.ROOT / "tools" / "build_pyz.py"), "--portable", "-o", str(cls.dist)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            raise AssertionError(f"pyz build failed:\n{build.stdout}\n{build.stderr}")
        cls.pyz = cls.dist / "cwc.pyz"
        cls.portable = cls.dist / "cwc-portable.zip"
        cls.work = tmp / "work"
        cls.work.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_pyz(self, *args):
        environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(self.pyz), *map(str, args)],
            cwd=self.work,
            env=environment,
            capture_output=True,
            text=True,
        )

    def test_every_data_file_is_in_the_pyz_without_pycache(self):
        with zipfile.ZipFile(self.pyz) as archive:
            names = archive.namelist()
        self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))
        packed = {name.removeprefix("cwc/data/") for name in names if name.startswith("cwc/data/")}
        for name in _data_files():
            with self.subTest(name=name):
                self.assertIn(name, packed)

    def test_the_commands_run_from_the_standalone_pyz(self):
        guide = self.run_pyz("guide")
        self.assertEqual(guide.returncode, 0, guide.stderr)
        self.assertEqual(guide.stdout, (DATA / "guide.md").read_text(encoding="utf-8"))

        (self.work / "graph.json").write_text(json.dumps(txt2img()), encoding="utf-8")
        compiled = self.run_pyz("compile", "graph.json", "-o", "out")
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertTrue((self.work / "out" / "graph.ui.json").stat().st_size > 0)

        rendered = self.run_pyz("render", "out/graph.ui.json", "-o", "out/graph.svg")
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertTrue((self.work / "out" / "graph.svg").stat().st_size > 0)

    def test_portable_zip_contains_launchers_and_examples(self):
        with zipfile.ZipFile(self.portable) as archive:
            names = set(archive.namelist())
        self.assertIn("cwc-portable/cwc.pyz", names)
        self.assertIn("cwc-portable/compile-drop-here.bat", names)
        self.assertIn("cwc-portable/compile-drop-here.command", names)
        self.assertIn("cwc-portable/README-PORTABLE.md", names)
        self.assertIn("cwc-portable/examples/01-basic-t2i.json", names)
        self.assertIn("cwc-portable/examples/03-optional-style-lora.json", names)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
