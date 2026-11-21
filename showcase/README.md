<p align="right"><a href="README.zh-CN.md">简体中文</a> · <a href="../README.md">Back to Main README</a></p>

# Step-by-Step Tutorial & Workflow Showcase

This folder contains ready-to-compile authoring graphs (`.json`) alongside their compiled ComfyUI canvases (`.ui.json`). You can drag any `.ui.json` file here directly onto the ComfyUI browser window to inspect the grouped layout and native reroute wiring.

---

## Included Showcase Workflows

| Workflow | Preset | Nodes / Groups | What It Demonstrates |
|---|---|---|---|
| [`01-basic-t2i.json`](01-basic-t2i.json) → [`01-basic-t2i.ui.json`](01-basic-t2i.ui.json) | `tower-preview` | 7 nodes · 4 groups | Clean text-to-image setup with the `KSampler` promoted into the left-hand **Controls** cockpit (`"ui_role": "cockpit"`) and a large right-hand output box. |
| [`02-hires-fix.json`](02-hires-fix.json) → [`02-hires-fix.ui.json`](02-hires-fix.ui.json) | `bento` | 9 nodes · 7 groups | Two-stage latent high-res upscale (`Base pass` → `Upscale Latent By` → `High-res pass`) with zero `_meta` group hints—100% inferred from graph topology. |
| [`03-optional-style-lora.json`](03-optional-style-lora.json) → [`03-optional-style-lora.ui.json`](03-optional-style-lora.ui.json) | `bento` | 9 nodes · 6 groups | A `PrimitiveInt` step control in the **Controls** group plus an optional **Style LoRA** module (`"ui_optional": true`) that ships bypassed (`mode: 4`). |

---

## Tutorial 1: For Everyday ComfyUI Users (Zero Command Line / One-Click Drag & Drop)

Have a messy ComfyUI workflow—or an API JSON exported via **Save (API Format)**—that you want arranged into a clean, grouped canvas?

### Step 1: Export or Write the API JSON
In ComfyUI, turn on **Dev Mode Options** in Settings and click **Save (API Format)** (or use any `.json` file in this `showcase/` folder).

### Step 2: Compile in One Click
- **Windows (Drag & Drop)**: Download and unzip `cwc-portable.zip`, then drag your `.json` file onto **`compile-drop-here.bat`**.
- **macOS / Linux (Single File)**: Download `cwc.pyz` (`~377 KB`, no `pip install` needed) and run:
  ```bash
  python3 cwc.pyz compile 03-optional-style-lora.json -o out
  ```

### Step 3: Drag `.ui.json` Back into ComfyUI
Open the generated `out/` folder and drag `03-optional-style-lora.ui.json` onto your ComfyUI browser tab. All prompts and image dimensions sit neatly in `01 Controls` on the left, while models, samplers, and decoders sit in aligned stage groups connected by orthogonal native reroutes.

---

## Tutorial 2: Customizing Your Cockpit & Optional Modules in 30 Seconds

A standard ComfyUI API JSON works with `cwc` out of the box with zero edits. When you want finer control over the layout, add a `_meta` block to any node:

### 1. Put Frequently Tweaked Knobs in the Left-Hand `Controls` Box
By default, positive/negative prompts and `EmptyLatentImage` (canvas size) automatically enter `01 Controls`. To pull another node (like your sampler or a `PrimitiveInt` step counter) into `01 Controls`, add `"ui_role": "cockpit"`:

```json
"steps": {
  "class_type": "PrimitiveInt",
  "inputs": {"value": 28},
  "_meta": {"title": "Steps", "ui_role": "cockpit"}
}
```

### 2. Ship Optional LoRAs or Upscalers Bypassed by Default
When sharing workflows on Civitai, users often don't have every optional style LoRA installed. Add `"ui_optional": true` to isolate the node in an **Optional models** group that ships bypassed (`Ctrl+B` / `mode: 4` in ComfyUI):

```json
"lora": {
  "class_type": "LoraLoader",
  "inputs": {
    "model": ["ckpt", "MODEL"],
    "clip": ["ckpt", "CLIP"],
    "lora_name": "sabikaze_style.safetensors",
    "strength_model": 0.85,
    "strength_clip": 0.85
  },
  "_meta": {"title": "Style LoRA (Optional)", "ui_optional": true}
}
```

When you run `cwc compile --all-enabled`, `cwc` verifies **both** the default prompt (with the LoRA bypassed) and `<name>.full.api.json` (with the LoRA enabled).

### 3. Pick the Best Layout Preset for Your Workflow
Pass `--preset <name>` on the command line or set `"_workflow": {"format": 1, "layout": {"preset": "<name>"}}` at the top of the JSON:
- `bento` (default): Compact two-row grid for everyday pipelines.
- `tower-preview`: Expands the final output group (`>= 880×680 px`) so the generated image preview dominates the right side.
- `swimlanes`: Aligns multiple ControlNet / IPAdapter / mask branches directly below the pipeline stage they steer.
- `cockpit-split`: Splits primary prompts/seeds above secondary tuning controls for large 60+ node graphs.
- `stepped`: Labels columns `Step 1`, `Step 2`, `Step 3` for tutorial workflows.

---

## Tutorial 3: Letting Coding Agents (Claude Code, Cursor, Gemini, Codex) Build Workflows for You

Language models are great at writing flat JSON dictionaries (`class_type` + `inputs`), but terrible at guessing pixel coordinates and LiteGraph `widgets_values` indices.

Point your coding agent at [`../skills/comfyui-workflow-compiler/SKILL.md`](../skills/comfyui-workflow-compiler/SKILL.md) or give it this prompt:

> *"Use `cwc` (`python3 cwc.pyz`) to build a ComfyUI workflow for [your task]. Run `python3 cwc.pyz guide` first, look up node sockets with `python3 cwc.pyz node --search <keyword>`, compile with `python3 cwc.pyz compile graph.json -o out`, and fix any findings until it exits 0."*
