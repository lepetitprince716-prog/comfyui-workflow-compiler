---
name: comfyui-workflow-compiler
description: Compile a declarative ComfyUI API prompt into a clean, grouped, verified `.ui.json` canvas with `cwc`, plus an offline SVG preview (`render --values`). Use whenever a user asks for a ComfyUI workflow file, visual canvas layout, or shareable `.ui.json`.
---

# comfyui-workflow-compiler (`cwc`)

Never hand-write LiteGraph pixel coordinates, link tables, or positional `widgets_values` arrays. Author the declarative ComfyUI API JSON (with optional `_meta` and `_workflow` hints) and compile it with `cwc`.

## 0. Locate or Run `cwc` (Zero-Install Fallback)

Pick the first command that works in your shell:

1. Installed CLI: `cwc --help`
2. Single-file archive in repo/directory: `python3 dist/cwc.pyz --help` (zero external dependencies, Python 3.10+)
3. Zero-install via `uvx`: `uvx --from git+https://github.com/lepetitprince716-prog/comfyui-workflow-compiler cwc --help`

## 1. Four-Step Offline Authoring Loop

1. **Read the spec once**: run `cwc guide` to inspect the exact authoring format, every `_meta` / `_workflow` key, and a worked example.
2. **Verify node schemas**: run `cwc node TYPE` or `cwc node --search KW` (702 core nodes are bundled offline).
   - For custom node packs, pass `--nodedefs http://127.0.0.1:8188` (a live ComfyUI server you trust) or `--nodedefs path/to/object_info.json` to both `cwc node` and `cwc compile`.
3. **Compile & fix until `ok`**:
   ```bash
   cwc compile GRAPH.json -o out --preset square --portable --all-enabled
   ```
   - Fix every error and warning printed to stderr (each includes an actionable `hint`) and re-run until the summary line says `ok` with zero unintended warnings.
   - Always pass `--portable` when compiling shareable workflows against a live `/object_info` so machine-local model/LoRA filename combos do not trigger false warnings while enum and numeric range checks stay strict.
4. **Render & inspect offline**:
   ```bash
   cwc render out/GRAPH.ui.json -o out/GRAPH.svg --values
   ```
   - `--values` draws inline widget values, formatted Quick Guide cards, and dashed `IMAGE PREVIEW` / `INPUT IMAGE` viewports so you can visually verify the canvas without launching ComfyUI.

## 2. Preset & Ergonomics Cheat Sheet

- **Layout presets (`cwc presets`)**:
  - `square` — compact `~1.0–1.7:1` square / 4:3 stacked-row dashboard (best default for multi-stage workflows, laptops, and showcase screenshots without wide horizontal scrolling).
  - `gallery` — widescreen horizontal studio layout with intermediate step previews and optional A/B branches tucked cleanly under the main pipeline rail.
  - `tower-preview` — single-stage / small workflows (`<= 16` nodes) with a large right-hand preview box.
  - `bento` / `swimlanes` / `cockpit-split` / `stepped` / `auto` — classic single-row, lane-aligned, split-cockpit, tutorial, and automatic layouts.
- **In-canvas Quick Guide card**: set `_workflow.title` (plus optional `_workflow.groups.<id>.note` or per-node `_meta.ui_note`) to generate a verified top-left `MarkdownNote` guide lane (`C5` / `G8` checked).
- **Compact panels**: set `_meta.ui_collapsed: true` on zero-widget plumbing nodes (`VAEDecode`, `BasicGuider`, `DisableNoise`, `ModelSampling*`, etc.) inside named stages to save space without hiding tunable knobs.
- **Optional switches**: set `_meta.ui_optional: true` on LoRA / refiner / A/B compare branches so they compile into a clean bypassed (`mode: 4`) module users can toggle with one click.
