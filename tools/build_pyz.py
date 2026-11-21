#!/usr/bin/env python3
"""Build the standalone single-file ``cwc.pyz`` executable and portable lazy bundle.

Because ``cwc`` uses only the Python standard library and reads bundled node
definitions via ``importlib.resources.files("cwc.data")``, the entire compiler
runs directly from a compressed ``.pyz`` zipapp archive without ``pip install``
or a virtual environment.

Usage::

    python3 tools/build_pyz.py -o dist
    python3 tools/build_pyz.py --portable -o dist
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
import tempfile
import zipapp
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

WINDOWS_BAT = """@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYZ=%SCRIPT_DIR%cwc.pyz"

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        set "PY=py -3"
    ) else (
        echo [cwc] Python 3.10+ was not found on PATH.
        pause
        exit /b 1
    )
)

if "%~1"=="" (
    echo ============================================================
    echo   cwc - ComfyUI Workflow Compiler (Portable)
    echo ============================================================
    echo.
    echo Drag and drop one or more ComfyUI API .json files onto this
    echo compile-drop-here.bat icon, or run from a terminal:
    echo.
    echo   python cwc.pyz compile my_graph.json -o out
    echo   python cwc.pyz guide
    echo.
    pause
    exit /b 0
)

:loop
if "%~1"=="" goto done
set "INFILE=%~f1"
set "OUTDIR=%~dp1out"
echo [cwc] Compiling "%~nx1" -^> "%OUTDIR%" ...
%PY% "%PYZ%" compile "%INFILE%" -o "%OUTDIR%"
if %ERRORLEVEL% EQU 0 (
    %PY% "%PYZ%" render "%OUTDIR%\\%~n1.ui.json" -o "%OUTDIR%\\%~n1.svg"
)
echo.
shift
goto loop

:done
echo [cwc] Done. Drag the generated .ui.json file from the "out" folder onto ComfyUI.
pause
"""

MACOS_COMMAND = """#!/bin/sh
set -eu
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYZ="$SCRIPT_DIR/cwc.pyz"

if [ "$#" -eq 0 ]; then
    echo "============================================================"
    echo "  cwc - ComfyUI Workflow Compiler (Portable)"
    echo "============================================================"
    echo ""
    echo "Usage:"
    echo "  python3 \"$PYZ\" compile my_graph.json -o out"
    echo "  python3 \"$PYZ\" render out/my_graph.ui.json -o out/my_graph.svg"
    echo "  python3 \"$PYZ\" guide"
    echo ""
    echo "Compiling bundled examples into $SCRIPT_DIR/out ..."
    for f in "$SCRIPT_DIR"/examples/*.json; do
        [ -f "$f" ] || continue
        stem="$(basename "$f" .json)"
        python3 "$PYZ" compile "$f" -o "$SCRIPT_DIR/out"
        python3 "$PYZ" render "$SCRIPT_DIR/out/$stem.ui.json" -o "$SCRIPT_DIR/out/$stem.svg"
    done
    echo ""
    echo "Done! Open $SCRIPT_DIR/out and drag any .ui.json file onto ComfyUI."
    exit 0
fi

for f in "$@"; do
    dir="$(cd "$(dirname "$f")" && pwd)"
    stem="$(basename "$f" .json)"
    out="$dir/out"
    echo "[cwc] Compiling $f -> $out ..."
    python3 "$PYZ" compile "$f" -o "$out"
    python3 "$PYZ" render "$out/$stem.ui.json" -o "$out/$stem.svg"
done
"""

PORTABLE_README = """# cwc Portable — Zero-Install Quickstart / 免安装懒人包说明

`cwc` (ComfyUI Workflow Compiler) turns a ComfyUI API prompt JSON into a clean,
grouped, readable ComfyUI `.ui.json` canvas—with zero third-party Python
dependencies.

`cwc` 把任意 ComfyUI API 格式的 JSON（手写的、从 ComfyUI `Export (API)` 导出的、
或由 Claude / Cursor / Gemini 生成的）一键编译成按模块分组、原生拐点走线、
可直接拖进浏览器打开的 `.ui.json` 画布。无需 `pip install`，只要有 Python 3.10+ 即可运行。

---

## 1. Windows Drag-and-Drop (最省事的拖拽用法)

1. Drag any ComfyUI API `.json` file (for example, from `examples/`) and drop
   it onto **`compile-drop-here.bat`**.
   把任意 API 格式的 `.json` 文件（比如 `examples/` 里的示例）鼠标拖到 **`compile-drop-here.bat`** 图标上松手。
2. An `out/` folder is created right next to your file with:
   同目录下会自动生成 `out/` 文件夹，里面包含：
   - `<name>.ui.json` — Drag this straight onto the ComfyUI browser canvas!（直接拖进 ComfyUI 网页画布即可！）
   - `<name>.api.json` — Verified API prompt exported by the canvas.（等价校验通过的 API prompt）
   - `<name>.svg` — Vector preview you can open in any browser without starting ComfyUI.（无需启动 ComfyUI 即可在浏览器预览的矢量画布图）
   - `<name>.report.json` — Verification and layout metrics.（编译检查报告）

---

## 2. Single-File CLI (`cwc.pyz` 单文件命令行用法)

Works on Windows, macOS, and Linux with stock Python 3.10+:

```bash
# Compile an API graph into a ComfyUI canvas (编译 API 图为画布)
python3 cwc.pyz compile examples/03-optional-style-lora.json -o out

# Choose a layout preset: bento, tower-preview, swimlanes, cockpit-split, stepped, auto
python3 cwc.pyz compile examples/03-optional-style-lora.json --preset tower-preview -o out

# Render an SVG preview without starting ComfyUI (不启动 ComfyUI 直接生成 SVG 预览)
python3 cwc.pyz render out/03-optional-style-lora.ui.json -o out/preview.svg

# Print the 1-page authoring guide & inspect node sockets (查看编写指南与节点定义)
python3 cwc.pyz guide
python3 cwc.pyz node KSampler
```
"""

EXAMPLE_BASIC_T2I = {
    "_workflow": {"format": 1, "layout": {"preset": "tower-preview"}},
    "ckpt": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sdxl_base_1.0.safetensors"},
    },
    "pos": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["ckpt", "CLIP"],
            "text": "an anime illustration of a quiet mountain station at dusk, warm platform lamp",
        },
        "_meta": {"title": "Positive prompt"},
    },
    "neg": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["ckpt", "CLIP"], "text": "blurry, lowres, watermark, text"},
        "_meta": {"title": "Negative prompt"},
    },
    "latent": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        "_meta": {"title": "Canvas size"},
    },
    "sampler": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["ckpt", "MODEL"],
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "latent_image": ["latent", 0],
            "seed": 42,
            "steps": 28,
            "cfg": 5.5,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
        },
        "_meta": {"title": "Sampler", "ui_role": "cockpit", "ui_seed": "fixed"},
    },
    "decode": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", "LATENT"], "vae": ["ckpt", "VAE"]},
    },
    "save": {
        "class_type": "SaveImage",
        "inputs": {"images": ["decode", "IMAGE"], "filename_prefix": "station_dusk"},
    },
}

EXAMPLE_OPTIONAL_LORA = {
    "_workflow": {"format": 1, "layout": {"preset": "bento"}},
    "ckpt": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sdxl_base_1.0.safetensors"},
    },
    "lora": {
        "class_type": "LoraLoader",
        "inputs": {
            "model": ["ckpt", "MODEL"],
            "clip": ["ckpt", "CLIP"],
            "lora_name": "sabikaze_style.safetensors",
            "strength_model": 0.85,
            "strength_clip": 0.85,
        },
        "_meta": {"title": "Style LoRA (Optional)", "ui_optional": True},
    },
    "pos": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["lora", "CLIP"],
            "text": "a coastal lighthouse in heavy mist, dry-brush ink and grey-teal palette",
        },
        "_meta": {"title": "Positive prompt"},
    },
    "neg": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["lora", "CLIP"], "text": "blurry, lowres, watermark"},
        "_meta": {"title": "Negative prompt"},
    },
    "latent": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1216, "height": 832, "batch_size": 1},
        "_meta": {"title": "Canvas size"},
    },
    "steps": {
        "class_type": "PrimitiveInt",
        "inputs": {"value": 28},
        "_meta": {"title": "Steps", "ui_role": "cockpit"},
    },
    "sampler": {
        "class_type": "KSampler",
        "inputs": {
            "model": ["lora", "MODEL"],
            "positive": ["pos", 0],
            "negative": ["neg", 0],
            "latent_image": ["latent", 0],
            "seed": 1024,
            "steps": ["steps", 0],
            "cfg": 5.0,
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
            "denoise": 1.0,
        },
        "_meta": {"title": "Base sampler", "ui_seed": "fixed"},
    },
    "decode": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sampler", "LATENT"], "vae": ["ckpt", "VAE"]},
    },
    "save": {
        "class_type": "SaveImage",
        "inputs": {"images": ["decode", "IMAGE"], "filename_prefix": "lighthouse"},
    },
}


def _include_in_pyz(rel: Path) -> bool:
    if not rel.parts or rel.parts[0] != "cwc":
        return False
    if "__pycache__" in rel.parts:
        return False
    return not rel.name.endswith((".pyc", ".pyo", ".DS_Store"))


def build_pyz(dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    pyz_path = dest_dir / "cwc.pyz"
    zipapp.create_archive(
        SRC,
        pyz_path,
        interpreter="/usr/bin/env python3",
        main="cwc.cli:main",
        compressed=True,
        filter=_include_in_pyz,
    )
    pyz_path.chmod(pyz_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return pyz_path


def build_portable_zip(dest_dir: Path, pyz_path: Path | None = None) -> Path:
    if pyz_path is None:
        pyz_path = build_pyz(dest_dir)
    zip_path = dest_dir / "cwc-portable.zip"
    hires_src = ROOT / "docs" / "images" / "src" / "hires.json"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(pyz_path, "cwc-portable/cwc.pyz")
        zf.writestr("cwc-portable/compile-drop-here.bat", WINDOWS_BAT.replace("\n", "\r\n"))

        cmd_info = zipfile.ZipInfo("cwc-portable/compile-drop-here.command")
        cmd_info.create_system = 3  # Unix permissions
        cmd_info.external_attr = (stat.S_IFREG | 0o755) << 16
        zf.writestr(cmd_info, MACOS_COMMAND)

        zf.writestr("cwc-portable/README-PORTABLE.md", PORTABLE_README)
        zf.writestr(
            "cwc-portable/examples/01-basic-t2i.json",
            json.dumps(EXAMPLE_BASIC_T2I, indent=2, ensure_ascii=False) + "\n",
        )
        if hires_src.is_file():
            zf.write(hires_src, "cwc-portable/examples/02-hires-fix.json")
        zf.writestr(
            "cwc-portable/examples/03-optional-style-lora.json",
            json.dumps(EXAMPLE_OPTIONAL_LORA, indent=2, ensure_ascii=False) + "\n",
        )
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build standalone cwc.pyz and portable zip bundle.")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("dist"), help="Output directory (default: dist)")
    parser.add_argument("--portable", action="store_true", help="Also build cwc-portable.zip with drag-and-drop launchers and examples")
    args = parser.parse_args(argv)

    pyz = build_pyz(args.output_dir)
    print(f"wrote {pyz} ({pyz.stat().st_size:,} bytes)", file=sys.stderr)
    if args.portable:
        portable = build_portable_zip(args.output_dir, pyz)
        print(f"wrote {portable} ({portable.stat().st_size:,} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
