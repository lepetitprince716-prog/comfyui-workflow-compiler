# cwc authoring guide

`cwc compile GRAPH.json -o DIR` reads a ComfyUI API prompt (the JSON that "Export (API)"
writes) and writes `DIR/GRAPH.ui.json` (the canvas), `DIR/GRAPH.api.json` (the prompt as it
runs with optional modules off) and `DIR/GRAPH.report.json`. A plain API prompt compiles as it
is. This guide lists what cwc accepts beyond that.

## Steps

1. Write the graph. Check input names, outputs and the values of each dropdown with `cwc node TYPE`.
2. `cwc compile GRAPH.json -o out` (add `--portable` for shareable workflows whose model filenames may not exist on the compiling machine) and read stderr: every error and warning comes with a hint,
   one `E8 info` line names the keys that have no effect yet, and a successful run ends with one
   summary line.
3. `cwc render out/GRAPH.ui.json -o GRAPH.svg --values` to look at the canvas (with inline widget values and preview viewports) without ComfyUI.

## Relaxations of the API format

- Node ids may be any string (`"sampler"`); cwc numbers them.
- A link names the output by type or name, `["ckpt", "MODEL"]`, or by index, `["ckpt", 0]`.
- An input with a declared default may be left out; cwc writes the default.
- Each node may carry a `_meta` object with the keys below.
- The document may carry a top-level `_workflow` object with the keys below.

## Node `_meta` keys

| Key | Value | Effect on the canvas |
|---|---|---|
| `title` | string | The node's title, in the canvas and in `_meta.title` of the API prompt. |
| `ui_group` | non-empty string, a group id | The node goes into a group of its own with that id. The group's title is `_workflow.groups.<id>.title`, else the id as words (`finish` becomes `Finish`). |
| `ui_role` | `cockpit`, `pipeline` or `plumbing` | `cockpit` moves the node into the Controls group. `pipeline` keeps it out of Controls (prompts and the empty latent go there by themselves otherwise). `plumbing` collapses the node, and it no longer gets a stage group of its own. |
| `ui_optional` | `true` or `false` | `true` makes the node part of an optional module: bypassed in the canvas (mode 4), in its own group, and left out of `GRAPH.api.json` with links passed around it. `--all-enabled` also writes `GRAPH.full.api.json` with it on. Linked optional nodes form one module; give them the same `ui_group` to name it. |
| `ui_collapsed` | `true` or `false` | `true` collapses the node (`flags.collapsed: true`) while keeping it inside its named `ui_group`. Useful for compacting zero-widget plumbing (`VAEDecode`, `BasicGuider`, `DisableNoise`, `ModelSamplingAuraFlow`) without losing stage grouping. |
| `ui_seed` | `fixed` or `randomize` | Sets the node's `control_after_generate` widget. The default is `randomize`. Only for nodes that have that widget. |
| `ui_order` | integer | Orders the Controls group's members of one kind, lower first, members without it last. The kinds keep their own order (positive prompt, negative prompt, reference image, size and batch, seed, other), and a member fed by another member stays after it, so `ui_order` decides only among, say, two `other` controls side by side. Elsewhere it matters only when more than 12 nodes of one group sit at the same step of the data flow and are cut into chunks. |
| `ui_locked` | `true` or a list of widget names | Checked (the names must be widgets of the node), no effect on the canvas yet. |
| `ui_expose` | list of widget names | Checked (the names must be widgets of the node), no effect on the canvas yet. |
| `ui_note` | string | Included in the in-canvas Quick Guide note card placed on the first screen. |

The keys marked "no effect yet" are listed, with the nodes that set them, in one `E8 info` line
on stderr and in the report when any is set. It does not change the exit code or `ok`.

A key starting with `ui_` that is not in this table is an error that names the closest key. A
key from the table written without `ui_` (`optional`, `group`, `role`) is ignored with a warning
that names the right key. Any other key is ignored with a warning, so prompts written by other
tools still compile.

Two keys on one node can disagree. `ui_role: "cockpit"` beats `ui_group` (the node stays in
Controls), `ui_group` beats `ui_role: "plumbing"` (the node is not collapsed), and `ui_optional`
beats `ui_role` `cockpit` and `plumbing` (the node stays in its optional module). The losing key
does nothing, and cwc says so in an `E7 warning` that names both keys. The warning does not
change the exit code or `ok`. Write one of the two keys.

Without any `_meta`, cwc groups the graph by itself: positive and negative prompts and the empty
latent (the image size) go into the Controls group, the rest into Models, Sampling,
Post-processing and Output. Use `ui_role: "cockpit"` to add another node to Controls, for
example the sampler, and `ui_role: "pipeline"` to keep a prompt out of it.

## Top-level `_workflow` keys

| Key | Value | Effect |
|---|---|---|
| `format` | `1` | Required when `_workflow` is present. |
| `layout` | `{"preset": NAME}` | The layout preset; `--preset` overrides it. `cwc presets` lists the names. |
| `groups` | `{GROUP_ID: {"title": string, "note": string}}` | `title` names the group made by `ui_group`. `note` is written into the in-canvas Quick Guide note card. |
| `models` | list of `{"name", "url", "directory"}`, all strings | Written to the canvas's `models` list, which ComfyUI reads to offer missing model downloads. |
| `title` | string | Heading of the in-canvas Quick Guide note card placed in the top-left guide lane. |
| `locale` | string | Accepted, no effect yet. |

Any other key in `_workflow`, `_workflow.layout` or a `groups` entry is an error.

## Example: text to image with an optional LoRA

```json
{
  "_workflow": {"format": 1, "layout": {"preset": "auto"}},
  "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
  "lora": {
    "class_type": "LoraLoader",
    "inputs": {"model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"], "lora_name": "style.safetensors",
               "strength_model": 0.8, "strength_clip": 0.8},
    "_meta": {"title": "Style LoRA", "ui_optional": true}
  },
  "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["lora", "CLIP"], "text": "a cat"},
          "_meta": {"title": "Positive prompt"}},
  "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["lora", "CLIP"], "text": "blurry"},
          "_meta": {"title": "Negative prompt"}},
  "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
             "_meta": {"title": "Image size"}},
  "sampler": {
    "class_type": "KSampler",
    "inputs": {"model": ["lora", "MODEL"], "positive": ["pos", 0], "negative": ["neg", 0],
               "latent_image": ["latent", 0], "seed": 1, "steps": 28, "cfg": 5.0,
               "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0},
    "_meta": {"title": "Sampler", "ui_role": "cockpit", "ui_seed": "fixed"}
  },
  "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", "LATENT"], "vae": ["ckpt", "VAE"]}},
  "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", "IMAGE"], "filename_prefix": "cat"}}
}
```

The canvas gets a Controls group with the two prompts, the image size and the sampler, an
optional module with the LoRA bypassed, and the stage groups. `GRAPH.api.json` runs without the
LoRA (the sampler and prompts take the checkpoint directly); turn the module on in ComfyUI, or
compile with `--all-enabled` for a prompt with it on.

## Exit codes

0 success, 1 a check failed, 2 usage error, 3 the input is wrong, 4 node definitions missing,
5 an output could not be written, 70 a bug in cwc. A failed compile writes only the report and
removes the canvas and prompts an earlier run left under the same name.
