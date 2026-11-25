<p align="right"><a href="README.zh-CN.md">简体中文</a> · <a href="showcase/README.md">Tutorial &amp; Showcase</a></p>

<h1 align="center">comfyui-workflow-compiler (<code>cwc</code>)</h1>

<p align="center">
  Compile a flat ComfyUI API graph—written by a coding agent, exported from ComfyUI, or crafted by hand—into a clean, grouped, human-readable <code>.ui.json</code> canvas with native reroutes and verified prompt equivalence.
</p>

<p align="center">
  <strong>Zero external dependencies</strong> · <strong>702 bundled core nodes</strong> · <strong>Single-file <code>cwc.pyz</code></strong> · <strong>7 layout presets</strong> · <strong>In-canvas Quick Guide</strong>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/hero-dark.png">
    <img src="docs/images/hero-light.png" width="830" alt="The left part of a canvas compiled by cwc: a Controls group with the positive and negative prompts and the canvas size, a Models group with the checkpoint loader, and a Sampling group with the base pass, joined by native reroutes">
  </picture>
</p>

The screenshot above shows the left half of the canvas `cwc compile` generates for the nine-node high-res pipeline in [`docs/images/src/hires.json`](docs/images/src/hires.json) (captured directly from ComfyUI frontend 1.59.1 with menus hidden). Full-canvas comparisons are shown under [Seven presets](#seven-presets), and ready-to-drop example canvases live in [`showcase/`](showcase/README.md).

---

## Why `cwc`?

ComfyUI speaks two different JSON dialects:
1. **The API format (`Save (API Format)`)**: A compact dictionary of `{class_type, inputs}` that scripts and coding agents (Claude Code, Cursor, Gemini, Codex) can author and diff cleanly—but **cannot be dragged onto the ComfyUI canvas** with a readable layout.
2. **The UI canvas format (`.ui.json`)**: A visual scene graph requiring pixel coordinates, renderer-specific node dimensions, positional `widgets_values` arrays (including hidden pseudo-widgets like `control_after_generate`), integer link tables, and group bounding boxes.

`cwc` bridges the two. You (or your coding agent) write the declarative API graph with optional `_meta` hints; `cwc` validates every socket and widget, infers stage groups, lays out the canvas without collisions across Classic and Vue renderers, routes long wires through native LiteGraph reroutes, and verifies that the compiled canvas exports the exact same API prompt.

---

## Quickstart (3 Ways to Run)

Requires **Python 3.10+**; the core compiler uses **nothing outside the Python standard library**.

### Option 1: Zero-Install Single File (`cwc.pyz`) or Drag-and-Drop Portable Pack
Download `cwc.pyz` (`~395 KB`) or `cwc-portable.zip` from Releases (or build them locally in one second with `python3 tools/build_pyz.py --portable -o dist`):

- **Windows Drag & Drop**: Unzip `cwc-portable.zip` and drag any ComfyUI API `.json` file onto **`compile-drop-here.bat`**. An `out/` folder appears right next to your file with `<name>.ui.json` (ready to drag into ComfyUI) and `<name>.svg` (vector preview).
- **Single-File CLI (Windows / macOS / Linux)**:
  ```bash
  python3 dist/cwc.pyz compile showcase/03-optional-style-lora.json -o out
  python3 dist/cwc.pyz render out/03-optional-style-lora.ui.json -o out/preview.svg
  ```

### Option 2: One-Line Run with `uvx` (No Clone Needed)
```bash
uvx --from git+https://github.com/lepetitprince716-prog/comfyui-workflow-compiler cwc compile graph.json -o out
```

### Option 3: Local Clone & `pip install`
```bash
git clone https://github.com/lepetitprince716-prog/comfyui-workflow-compiler
cd comfyui-workflow-compiler
python3 -m venv .venv && . .venv/bin/activate
pip install .
cwc compile docs/images/src/hires.json -o out
```

```text
G9 warning: the canvas extent is 2560 x 820, aspect ratio 3.12; it should be 1 to 2.8
cwc compile: ok; preset bento; 7 groups, 0 optional modules, 1 warning; preview: cwc render out/hires.ui.json -o hires.svg
wrote out/hires.ui.json
wrote out/hires.api.json
wrote out/hires.report.json
```

Drag `out/hires.ui.json` straight onto the ComfyUI browser canvas to open it. `hires.api.json` is the verified prompt the canvas exports (with any optional modules bypassed), and `hires.report.json` records all layout measurements and check findings.

> **New to `cwc`?** Read the **[3-Minute Step-by-Step Tutorial & Workflow Showcase (`showcase/README.md`)](showcase/README.md)**, or jump straight to **[1-Minute Foolproof Agent Setup](#1-minute-foolproof-agent-setup)** below to let Claude Code, Cursor, Codex, or Antigravity set it up for you.
>
> **Want Ready-to-Run Zero-Custom-Node Workflows + Sweep CLI?** Grab the companion **[Agent × ComfyUI Starter Pack (`agent-comfyui-starter-v0.1.0.zip`)](https://github.com/lepetitprince716-prog/agent-genops/releases/download/v0.1.0/agent-comfyui-starter-v0.1.0.zip)** from **[`agent-genops` Releases (`v0.1.0`)](https://github.com/lepetitprince716-prog/agent-genops/releases/tag/v0.1.0)**—includes `cwc`-compiled `.ui.json` / `.api.json` canvases (`Fast-12 OPD + 20s 1.5x HiRes` and `Optional Style LoRA A/B`), a stdlib-only `comfy_sweep.py` benchmark runner, and free SFW companion weights on **[Civitai (`lepetitprince716355`)](https://civitai.com/user/lepetitprince716355)**.

---

## Workflow for Coding Agents

### 1-Minute Foolproof Agent Setup

#### Option A: Zero-Config Copy-Paste Prompt (Fastest)
Copy and paste this prompt directly into **Claude Code, Antigravity, Codex, Cursor, or Gemini CLI**—your agent will install `cwc`, learn the syntax, and start compiling immediately:

```text
Install and configure comfyui-workflow-compiler (cwc) for this machine:
1. Make `cwc` runnable via `uv tool install git+https://github.com/lepetitprince716-prog/comfyui-workflow-compiler` (or `pip install git+https://github.com/lepetitprince716-prog/comfyui-workflow-compiler`, or zero-install `uvx --from git+https://github.com/lepetitprince716-prog/comfyui-workflow-compiler cwc`).
2. Install the bundled skill from `skills/comfyui-workflow-compiler/SKILL.md` in https://github.com/lepetitprince716-prog/comfyui-workflow-compiler into your active skills/rules directory.
3. Verify with `cwc presets` and read `cwc guide` once.
From now on, whenever I ask for a ComfyUI workflow, author the declarative API JSON first, compile it with `cwc compile --preset square --portable` (or `--preset gallery` for widescreen multi-step comparisons), and render a preview with `cwc render ... --values`.
```

#### Option B: One-Command Permanent CLI + Skill Install (macOS / Linux)
Install the `cwc` CLI globally once, then copy the bundled skill into whichever coding agent you use:

```bash
# 1. Install the cwc CLI (pick ONE: uv tool, pipx, or pip)
uv tool install git+https://github.com/lepetitprince716-prog/comfyui-workflow-compiler
# Alternative without uv: pip install --user git+https://github.com/lepetitprince716-prog/comfyui-workflow-compiler

# 2. Verify installation (should print all 7 layout presets)
cwc presets

# 3. Install the Agent Skill (clone once, then copy to your agent's folder)
git clone --depth 1 https://github.com/lepetitprince716-prog/comfyui-workflow-compiler /tmp/cwc-skill
```

| Coding Agent | Copy the skill folder to |
|---|---|
| **Claude Code** | `mkdir -p ~/.claude/skills && cp -R /tmp/cwc-skill/skills/comfyui-workflow-compiler ~/.claude/skills/` |
| **Codex CLI** | `mkdir -p ~/.codex/skills && cp -R /tmp/cwc-skill/skills/comfyui-workflow-compiler ~/.codex/skills/` |
| **Antigravity (AGY)** | `mkdir -p ~/.gemini/config/skills && cp -R /tmp/cwc-skill/skills/comfyui-workflow-compiler ~/.gemini/config/skills/` |
| **OpenCode / Pi / Shared Agents** | `mkdir -p ~/.agents/skills && cp -R /tmp/cwc-skill/skills/comfyui-workflow-compiler ~/.agents/skills/` |
| **Cursor** | Copy `skills/comfyui-workflow-compiler/SKILL.md` into your project's `.cursor/rules/cwc.mdc` (or reference it with `@SKILL.md`) |

> **Windows / No-Clone Tip:** You don't even need `git` or `pip`—drop `cwc.pyz` anywhere on disk and tell your agent to run `python dist/cwc.pyz guide` and `python dist/cwc.pyz compile ...`.

#### Option C: Ready-to-Use Everyday Prompt
Once set up, just ask your agent in plain English:

```text
Build a 2-stage ComfyUI workflow for <your model/task> with an optional LoRA toggle and an in-canvas Quick Guide.
Use cwc (`--preset square --portable`), fix all warnings until `ok`, and render a `--values` SVG preview for me.
(If using custom nodes, add: `--nodedefs http://127.0.0.1:8188` or `--nodedefs object_info.json`.)
```

### The 4-Step Offline Agent Loop

Point your coding agent at [`skills/comfyui-workflow-compiler/SKILL.md`](skills/comfyui-workflow-compiler/SKILL.md). The agent loop takes four fast offline commands:

1. `cwc guide` prints the complete one-page authoring specification: input syntax, every `_meta` and `_workflow` key with its effect, and a worked example.
2. `cwc node TYPE` shows a node's inputs, widgets with defaults, dropdown options, and outputs; `cwc node --search KW` searches across all 702 bundled core node types.
3. `cwc compile GRAPH.json -o out` validates and compiles the graph. Errors and warnings go to stderr with an actionable hint each; `--json` emits structured `finding_list` and `findings` counts.
4. Fix any findings and re-compile until the summary line says `ok`; run `cwc render` to inspect the canvas as an SVG without starting ComfyUI.

Misspelled socket or widget names get an immediate suggestion:

```text
E2: node 'sampler': KSampler has no input 'sampler_nme'; did you mean 'sampler_name'?
  hint: valid inputs: model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent_image, denoise
cwc compile: removed out/steps.ui.json, out/steps.api.json from an earlier run, which this run does not replace
cwc compile: 1 error(s); see out/steps.report.json
wrote out/steps.report.json
```

### Output Contract & Exit Codes

For an input file named `<name>.json`, `cwc compile` guarantees:
- `<name>.report.json` is always written. A successful run also writes `<name>.ui.json`, `<name>.api.json`, and (with `--all-enabled`) `<name>.full.api.json`.
- Stale artifacts from an earlier run that the current run does not replace are removed automatically and listed on stderr—so a failed compile never leaves an old `.ui.json` behind to be mistaken for a fresh one.
- A successful run finishes with one summary line on stderr naming the chosen preset (and the matching rule under `auto`), group count, optional module count, warning count, and the `cwc render` preview command.

Every command uses the same exit codes:

| Code | Meaning |
|---|---|
| 0 | Done |
| 1 | A verification check failed (only the report was written); also `cwc node --search` with no match |
| 2 | Command-line usage error |
| 3 | Input is unreadable, malformed, or fails authoring checks; also `cwc node TYPE` with an unknown type |
| 4 | Node definitions are unavailable, unreadable, or malformed |
| 5 | An output file could not be written (partial outputs from the run are cleaned up where possible) |
| 70 | Internal bug in `cwc`, reported on one stderr line |

---

## Authoring Format: What Goes In

The input is a standard ComfyUI API graph where each entry has a `class_type` and `inputs`, and links are written as `[node_id, output_name_or_index]`. You can use descriptive string IDs (`"sampler"` instead of `"3"`), omit inputs that have declared defaults, and optionally attach `_meta` hints. Without any `_meta` hints, `cwc` infers stage groups automatically from the graph topology.

Here is one node from [`docs/images/src/landscape.json`](docs/images/src/landscape.json):

```json
"lora": {
  "class_type": "LoraLoader",
  "inputs": {
    "model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"],
    "lora_name": "film_grain.safetensors",
    "strength_model": 0.6, "strength_clip": 0.6
  },
  "_meta": {"title": "Style LoRA", "ui_optional": true}
}
```

Setting `"ui_optional": true` isolates the node in an **Optional models** group that ships bypassed (`mode: 4` in ComfyUI—shown as the dimmed `Style LoRA` box in the preset screenshots below). Run `cwc guide` for the full list of `_meta` and `_workflow` keys.

---

## The `Controls` Cockpit Group

The **Controls** group on the far left holds the parameters a user edits on every run. Positive/negative prompts and `EmptyLatentImage` (canvas dimensions) enter `Controls` automatically.

You can promote any other node into `Controls` with `"ui_role": "cockpit"`, or keep a prompt/latent out of `Controls` with `"ui_role": "pipeline"`. For example, to expose step count as a cockpit knob, feed your sampler from a `PrimitiveInt` node:

```json
"steps": {"class_type": "PrimitiveInt", "inputs": {"value": 28},
          "_meta": {"title": "Steps", "ui_role": "cockpit"}},
"sampler": {"class_type": "KSampler", "inputs": {"steps": ["steps", 0], ...}}
```

The compile report confirms which `_meta` keys took effect on the canvas under `metrics.authoring_keys` (`nodes` lists where the compiled canvas reflects the key; `no_effect_on` flags where a key was overridden or had no effect):

```json
"authoring_keys": [
 {"key": "_meta.title", "nodes": ["steps"]},
 {"key": "_meta.ui_role", "nodes": ["steps"]}
]
```

Keys that are accepted for forward compatibility but not yet rendered on the canvas are reported once per run in the `E8` info finding.

---

## Seven Presets

Use `--preset <name>` (or `_workflow.layout.preset`) to pick a layout architecture, or run `cwc presets` to see when to use each. Here is the same twelve-node graph (`landscape.json`) compiled across the five classic presets, plus **`gallery`** (widescreen multi-step preview studio) and **`square`** (compact `~1.0–1.7:1` square / 4:3 dashboard):

### `bento`

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/bento-dark.png">
  <img src="docs/images/bento-light.png" width="830" alt="The bento preset">
</picture>

**The default.** Places `Controls` on the left, followed by models, prompts, sampling, post-processing, and output along one main row, with control branches and optional modules neatly aligned in a second row underneath. When the main row exceeds 2.4× its height, it wraps cleanly onto a new row.

### `swimlanes`

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/swimlanes-dark.png">
  <img src="docs/images/swimlanes-light.png" width="830" alt="The swimlanes preset">
</picture>

Places `Controls` first, then arranges every pipeline stage into strict left-to-right data-flow columns, docking each ControlNet or mask branch directly inside the column of the stage it steers.

### `cockpit-split`

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/cockpit-split-dark.png">
  <img src="docs/images/cockpit-split-light.png" width="830" alt="The cockpit-split preset">
</picture>

Like `swimlanes`, except when `Controls` contains both core controls (prompts, reference image, size, seed) and secondary tuning knobs, it splits them into an upper primary cockpit and a lower tuning box.

### `tower-preview`

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/tower-preview-dark.png">
  <img src="docs/images/tower-preview-light.png" width="830" alt="The tower-preview preset">
</picture>

Uses the `bento` arrangement with an enlarged right-hand gallery box: whenever the output group holds a preview/save node, the group expands to at least `880 × 680 px` so the generated image fills the right side of the screen.

### `stepped`

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/stepped-dark.png">
  <img src="docs/images/stepped-light.png" width="830" alt="The stepped preset">
</picture>

Titles stage groups `Step 1`, `Step 2`, `Step 3`, and stacks `Models` and `Controls` in the first column—ideal for teaching workflows and step-by-step tutorials.

### `gallery`

Designed for **multi-step pipelines with intermediate step previews or optional A/B comparisons**:
- Keeps `Controls` anchored on the top-left (automatically splitting tall 5+ control cockpits into two side-by-side columns) separated from the main pipeline rail by an 80 px vertical spine, with the final output preview flush on the top-right.
- Automatically docks intermediate dead-end preview groups (such as Stage 1 layout previews or ROI crop checks) and optional comparison branches tightly underneath the pipeline rail (`dock_steps` + spine-aware lower-band tucking) instead of wasting vertical dead space below a tall control sidebar.
- Enlarges preview viewports uniformly across output, optional, and intermediate preview groups (`>= 560 × 540 px`). Combine with `_meta.ui_collapsed: true` to fold zero-widget plumbing nodes (`VAEDecode`, `BasicGuider`, `DisableNoise`) inside named stages for compact, studio-grade panels.

### `square`

Designed for **compact square / 4:3 dashboards (`~1.0–1.7:1`)** without wide horizontal scrolling:
- Automatically folds wide multi-stage pipelines into balanced stacked rows once total canvas aspect ratio exceeds `1.85:1`, using clean horizontal bus-corridor carriage returns (`wrap_rows: true`) while keeping strict left-to-right flow within every row (`G2 = 0` crossings, `C3 = 0`, `G9` green).
- Keeps intermediate step previews and optional A/B comparison branches docked inside each row's width (`>= 560 × 540 px`) and reclaims the full width below the top-left Quick Guide lane on wrapped rows—ideal for laptops, 4:3 showcase screenshots, and dense multi-stage workflows.

### `auto`

`--preset auto` evaluates the graph topology and picks the first matching rule (recording both the choice and the reason in the stderr summary and `.report.json`):

| Graph topology | Selected preset |
|---|---|
| At most 16 nodes and no optional module | `tower-preview` |
| Three or more control or mask branches | `swimlanes` |
| 60 nodes or more | `cockpit-split` |
| Anything else | `bento` |

---

## CLI Reference

```text
cwc node     [TYPE | --search KW] [--nodedefs FILE|URL] [--json]
cwc compile  GRAPH.json -o DIR [--nodedefs FILE|URL] [--preset NAME]
             [--all-enabled] [--portable] [--allow-unknown] [--json]
cwc presets  [--json]
cwc guide
cwc learn    FILE [FILE ...] -o FILE [--nodedefs FILE|URL] [--json]
cwc render   GRAPH.ui.json -o OUT.svg [--nodedefs FILE|URL] [--values] [--json]
```

| Command | Description |
|---|---|
| `node` | Inspect one node definition (inputs, widgets with defaults, combo options, outputs), or search by class name, display name, or category. Unknown `TYPE` exits `3` with closest matches; an empty search exits `1` (`[]` under `--json`). |
| `compile` | Compile an API graph into `<name>.ui.json`, `<name>.api.json`, and `<name>.report.json`. Pass `--all-enabled` to also emit `<name>.full.api.json` with all optional modules switched on, and `--portable` to suppress machine-local file-picker combo warnings (checkpoints, LoRAs, VAEs, images) when compiling shareable workflows against a live `/object_info` while keeping enum and range checks strict. |
| `presets` | List all seven layout presets, their visual structure, when to choose each, and the `auto` selection rules. |
| `guide` | Print the one-page authoring reference. |
| `learn` | Learn widget templates for custom nodes from paired `.ui.json` + `.api.json` exports. |
| `render` | Render a `.ui.json` canvas to a standalone `.svg` diagram without a browser. Pass `--values` to display inline widget parameter values, formatted Quick Guide cards, and dashed `IMAGE PREVIEW` / `INPUT IMAGE` viewports. |

Definitions for all **702 core ComfyUI nodes** are bundled inside `cwc`. For custom node packs, pass `--nodedefs` with either a saved `/object_info` JSON file or the base URL of a running ComfyUI server (fetched from `<URL>/api/object_info`, following at most 3 `http`/`https` redirects without downgrading `https` to `http`; only point at servers you trust).

<details>
<summary>Finding codes of the authoring checks</summary>

The report's `title` field carries the same text; the other code families check the canvas.

| Code | Meaning |
|---|---|
| E1 | unknown node type |
| E2 | input name not defined for the node type |
| E3 | required input without a declared default is missing |
| E4 | link connects incompatible types |
| E5 | literal value has the wrong type, is out of range, or is not a known option |
| E6 | link points at a missing node or output, or names an ambiguous output |
| E7 | graph structure is invalid (cycle, bad id, bad _workflow, mixed optional group) |
| E8 | authoring key is accepted but has no effect yet |

E7 also covers `_meta` keys `cwc` does not know: an error for one that starts with `ui_`, which
names the closest key, and a warning for any other. A `_meta` key that another key of the same
node overrides is an E7 warning that names both keys and says what to remove; the exit code and
`ok` do not change. `ui_role: cockpit` beats `ui_group`, `ui_group` beats `ui_role: plumbing`,
and `ui_optional` beats `ui_role` `cockpit` and `plumbing`.

A key `cwc` accepts but does not use yet (`ui_locked`, `ui_expose`, `_workflow.locale`) is named in one info
finding, E8, per compile: one stderr line that lists the keys and the nodes that set them, and,
in the report of a successful compile, the keys one by one in `data.keys`. The exit code and
`ok` do not change. (`_workflow.title`, `_workflow.groups.<id>.note`, and `_meta.ui_note` now
render into the in-canvas `MarkdownNote` Quick Guide card placed in the reserved top-left guide lane.)

</details>

<details>
<summary>Behaviour that changed since the first version in this repository</summary>

- An unknown `cwc node TYPE` exits 3; it used to exit 4. A search with no match exits 1; it used
  to exit 0.
- A `_meta` key that starts with `ui_` but is not one of cwc's keys is an error (E7, exit 3). It
  used to be a warning, and the misspelt key did nothing.
- A key that has no effect yet is named in the E8 info finding. Before, nothing said so.
- A `_meta` key that another key of the same node overrides is an E7 warning. Before, the
  overridden key did nothing and nothing said so.

</details>

<details>
<summary>Input limits</summary>

Inputs are checked once at the edge, and one past a limit below is refused with a message that
names the limit. The limits are far above real workflows (large canvases are a few hundred nodes
and a few megabytes). A JSON file is read up to the file limit, then refused at the document
limit. The values are the constants in `src/cwc/ingest/limits.py`, `src/cwc/ingest/png.py`,
`src/cwc/nodedefs/derive.py`, `src/cwc/nodedefs/registry.py` and `src/cwc/nodedefs/learn.py`.

| Input | Limit | Past it |
|---|---|---|
| An input file (canvas, authoring file, PNG), read | 256 MiB, also for a pipe or a file that grows | I3, exit 3 |
| A JSON document, or the text of one PNG chunk | 64 MiB | I3, exit 3 |
| The compressed text chunks of one PNG, inflated, in all | 64 MiB | I3, exit 3 |
| Nesting of JSON arrays and objects | 64 levels | I3, exit 3 |
| Canvas nodes, or authoring entries | 5000 | I3, exit 3 |
| Canvas links | 20000 | I3, exit 3 |
| A canvas node, link, slot or reroute id (integer or digit string) | 32 digits | I1, exit 3 |
| An authoring key made of digits | 32 digits | E7, exit 3 |
| A canvas coordinate or size (nodes, groups, reroutes) | at most 1e9 in absolute value | I1, exit 3 |
| A node definition source (file, URL response, or gzip once inflated) | 64 MiB, 64 levels deep | D1, exit 4 |
| A URL source: redirects followed, only to http or https, never https to http | 3 | D1, exit 4 |
| An Autogrow `min` or `max` in a node definition | 1000 | D1, exit 4 |
| The slots of one Autogrow group (template inputs times `max`) | 10000 | D1, exit 4 |
| `learn`: widget values times candidate names of one node, aligned | 250000 cells | the node votes on its length only (`too_large`), no error |

An integer larger than a float holds is not an internal error either: in a widget it gets the
usual range finding, in a canvas coordinate it is past the limit above.

</details>

---

## Architecture & Project Status

- **Working today**: The complete compilation pipeline (`normalize` → `instantiate` → `annotate` → `layout` → `route` → `notes` → `verify`), all seven layout presets (including `gallery` for widescreen multi-step previews and `square` for compact square/4:3 dashboards), in-canvas `MarkdownNote` Quick Guide cards (`C5`/`G8` verified), `--portable` compilation, `_meta.ui_collapsed` per-node plumbing folding, `render --values` visual inspection, single-file `cwc.pyz` / portable zip packaging (`tools/build_pyz.py`), agent skill package (`skills/`), and the [`showcase/`](showcase/README.md) tutorial workflows.
- **Planned next (M3)**: Lossless geometry tidying of existing canvases (`cwc beautify`), second-round corridor widening, and PNG workflow embedding/extraction (`cwc embed` / `cwc extract`).

Deep-dive engineering docs:
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Module topology, layout math, and verification rules.
- [`docs/m0-findings.md`](docs/m0-findings.md) — Empirical measurements from live ComfyUI 0.45.0 / frontend 1.59.1.
- [`docs/m1-templates.md`](docs/m1-templates.md) — Positional widget templates and pseudo-widget handling.
- [`docs/m2-frontend-facts.md`](docs/m2-frontend-facts.md) — Exact group membership, node boxes, and reroute geometry in the real frontend.

Run the test suite with `python3 -m unittest discover -s tests`. Playwright is only required if you run the live browser oracle in `tools/oracle/` or regenerate README screenshots with [`tools/make_readme_images.py`](tools/make_readme_images.py).

<details>
<summary>Milestones</summary>

| Milestone | What it covers | Status |
|---|---|---|
| M0 | Real-frontend oracle; measures the constants the layout depends on (`tools/oracle/`, `docs/m0-findings.md`) | done |
| M1 | Instantiation and equivalence; an API graph becomes a canvas that exports the same prompt (`docs/m1-templates.md`) | done |
| M2 | The layout presets (`bento`, `swimlanes`, `cockpit-split`, `tower-preview`, `stepped`, `gallery`, `square`), dual-renderer layout and native link routing checks (`docs/ARCHITECTURE.md`, `docs/m2-frontend-facts.md`) | done |
| M3 | In-canvas `MarkdownNote` Quick Guide (`notes`, done); reroute second round, lossless tidy (`beautify`), PNG embed and extract | in progress |
| M4 | Agent skill package ([`skills/`](skills/comfyui-workflow-compiler/SKILL.md)), standalone `cwc.pyz` packaging (`tools/build_pyz.py`), and [`showcase/`](showcase/README.md) workflows | done |
| M5 | Optional extensions after the first release | planned |

</details>

## Licence

Apache License 2.0 ([`LICENSE`](LICENSE)). The files `cwc` writes for you (`.ui.json`, `.api.json`, `.svg`, and reports) belong to the author of the workflow and are not covered by it: see [`LICENSE-OUTPUTS.md`](LICENSE-OUTPUTS.md).

No code or copyrighted data from GPL or AGPL projects is copied into this repository. The bundled core node snapshot keeps only interoperability facts (names, types, order).
