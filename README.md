<p align="right"><a href="README.zh-CN.md">中文</a></p>

<h1 align="center">comfyui-workflow-compiler</h1>

<p align="center">
  <code>cwc</code> turns a ComfyUI API graph, written by a coding agent or by hand, into a canvas
  a person can read: grouped, laid out, linked with native reroutes, and checked to export the
  same prompt it was compiled from.
</p>

<p align="center"><code>pip install .</code> then <code>cwc compile graph.json -o out</code></p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/hero-dark.png">
    <img src="docs/images/hero-light.png" width="830" alt="The left part of a canvas compiled by cwc: a Controls group with the positive and negative prompts and the canvas size, a Models group with the checkpoint loader, and a Sampling group with the base pass, joined by native reroutes">
  </picture>
</p>

The left part of the canvas `cwc compile` writes for the nine-node graph in
[`docs/images/src/hires.json`](docs/images/src/hires.json), a text-to-image graph with a
high-res pass. It is a screenshot of the ComfyUI frontend with its menus hidden. Whole canvases
are under [Five presets](#five-presets).

## Try it

Python 3.10 or newer; the core uses nothing outside the standard library.

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

Drop `out/hires.ui.json` onto the ComfyUI canvas to open it. `hires.api.json` is the prompt
the canvas exports, with any optional modules bypassed; `hires.report.json` holds the findings
and measurements of the checks. The first two lines go to stderr: a layout warning (this
canvas is wide and flat) and the summary.

## For agents

1. `cwc guide` prints the authoring reference: the input format, every `_meta` and `_workflow`
   key with its effect, and an example with an optional module.
2. `cwc node TYPE` shows a node's inputs, its widgets with their defaults, the values of each
   dropdown, and its outputs; `cwc node --search KW` finds a type.
3. Write the API prompt and run `cwc compile GRAPH.json -o out`. Errors and warnings go to
   stderr with a hint each; `--json` gives the same list as `finding_list`, next to the
   `findings` counts.
4. Fix and compile again until the summary line says `ok`; `cwc render` draws the canvas as an
   SVG to look at without ComfyUI.

A typo gets a suggestion:

```text
E2: node 'sampler': KSampler has no input 'sampler_nme'; did you mean 'sampler_name'?
  hint: valid inputs: model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent_image, denoise
cwc compile: removed out/steps.ui.json, out/steps.api.json from an earlier run, which this run does not replace
cwc compile: 1 error(s); see out/steps.report.json
wrote out/steps.report.json
```

What `compile` leaves in the output folder, for a graph file named `<name>.json`:

- Always `<name>.report.json`. A successful run also writes `<name>.ui.json`, `<name>.api.json`
  and, with `--all-enabled`, `<name>.full.api.json`.
- Files of that name that an earlier run left and this run does not write are removed, and one
  stderr line names them: a failed run leaves only its report, so an old canvas is never taken
  for the new one.
- A successful run ends with one summary line on stderr: the preset (and why, under `auto`), the
  number of groups, optional modules and warnings, and the `cwc render` command that previews
  the canvas.

Every command uses the same exit codes:

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | a check failed and only the report was written; also `cwc node --search` with no match |
| 2 | bad command line |
| 3 | the input is unreadable, malformed or fails the authoring checks; also `cwc node TYPE` with an unknown type |
| 4 | node definitions are unavailable, unreadable or malformed |
| 5 | an output could not be written; the run's files were removed where possible |
| 70 | a bug in cwc, reported on one stderr line |

## What goes in

An API graph: each node is a `class_type` and its `inputs`, and a link is `[node, output]`.
Ids can be names, and an input with a declared default may be left out. The `_meta` hints are
optional; without them `cwc` infers the groups from the graph, as it did for `hires.json`. One
node from [`docs/images/src/landscape.json`](docs/images/src/landscape.json):

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

`ui_optional` puts a node in an optional module that the canvas ships bypassed (the dimmed
Style LoRA under Optional models in the pictures below). `cwc guide` lists every key.

## The Controls group

The Controls group is the part a user edits on every run. The positive and negative prompts and
the empty latent (the image size) go into it by themselves. Any other node goes in with
`"ui_role": "cockpit"` in its `_meta`, and `"ui_role": "pipeline"` keeps a node out. For
example, to make the step count a control, feed the sampler from a primitive node
(`cwc node PrimitiveInt` shows its widget, `value`):

```json
"steps": {"class_type": "PrimitiveInt", "inputs": {"value": 28},
          "_meta": {"title": "Steps", "ui_role": "cockpit"}},
"sampler": {"class_type": "KSampler", "inputs": {"steps": ["steps", 0], ...}}
```

On a plain text-to-image graph, the Controls group then holds the two prompts, the empty latent
and a node titled Steps. Without the `ui_role` line the same node sits in the Sampling group.

The report of a successful compile says which `_meta` keys show in the canvas. Under
`metrics.authoring_keys`, each key lists in `nodes` the nodes where the compiled canvas matches
it, and in `no_effect_on` the nodes where the key was set and the canvas does not show it. A key
that repeats what `cwc` does anyway, such as `"ui_role": "cockpit"` on a prompt, is listed in
`nodes` too. The report of the graph above has:

```json
"authoring_keys": [
 {"key": "_meta.title", "nodes": ["steps"]},
 {"key": "_meta.ui_role", "nodes": ["steps"]}
]
```

The keys that have no effect yet are not listed there; they are in the E8 finding.

## Five presets

`--preset` picks the arrangement, and `cwc presets` describes each and says when to pick it.
The same twelve-node graph, `landscape.json`, in each:

### bento

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/bento-dark.png">
  <img src="docs/images/bento-light.png" width="830" alt="The bento preset">
</picture>

The default. Controls first, then models and prompts side by side, sampling, post-processing
and output in one main row; control branches and optional modules in a second row below it.
The main row wraps onto a new row when it gets wider than 2.4 times its height.

### swimlanes

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/swimlanes-dark.png">
  <img src="docs/images/swimlanes-light.png" width="830" alt="The swimlanes preset">
</picture>

Controls first, then every other group in one row of columns in data-flow order, with a
control branch in the column of the stage it steers.

### cockpit-split

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/cockpit-split-dark.png">
  <img src="docs/images/cockpit-split-light.png" width="830" alt="The cockpit-split preset">
</picture>

Like swimlanes, but when the Controls group holds more than the core controls (prompts,
reference image, size, seed), the rest go in a second box under them.

### tower-preview

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/tower-preview-dark.png">
  <img src="docs/images/tower-preview-light.png" width="830" alt="The tower-preview preset">
</picture>

Bento, with a large image preview: when the output group holds a node that shows a preview,
the group is at least 880 by 680 pixels and the preview fills it.

### stepped

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/stepped-dark.png">
  <img src="docs/images/stepped-light.png" width="830" alt="The stepped preset">
</picture>

Groups titled Step 1, Step 2 and so on, with the models and Controls groups in the first
column. For a graph meant to be followed in order, like a tutorial.

### auto

`--preset auto` takes the first rule that holds, and the summary line and the report say which:

| Graph | Preset |
|---|---|
| at most 16 nodes and no optional module | `tower-preview` |
| three or more control or mask branches | `swimlanes` |
| 60 nodes or more | `cockpit-split` |
| anything else | `bento` |

Without `--preset`, `cwc` uses the graph's `_workflow.layout.preset`, else `bento`.

## Commands

```text
cwc node     [TYPE | --search KW] [--nodedefs FILE|URL] [--json]
cwc compile  GRAPH.json -o DIR [--nodedefs FILE|URL] [--preset NAME]
             [--all-enabled] [--allow-unknown] [--json]
cwc presets  [--json]
cwc guide
cwc learn    FILE [FILE ...] -o FILE [--nodedefs FILE|URL] [--json]
cwc render   GRAPH.ui.json -o OUT.svg [--nodedefs FILE|URL] [--json]
```

| Command | Does |
|---|---|
| `node` | Shows one node definition (inputs, widgets with defaults, the values of each dropdown, outputs), or searches them by name, display name or category. An unknown TYPE exits 3 and names the closest types on stderr; a search with no match exits 1 with nothing on stdout, or `[]` under `--json`. |
| `compile` | Turns an API graph into a canvas and its API prompt. `--all-enabled` also writes `<name>.full.api.json`, the prompt with every optional module switched on. |
| `presets` | Lists the presets, what each looks like, when to pick it, and the rules of `auto`. |
| `guide` | Prints the authoring guide. |
| `learn` | Learns widget templates from canvases paired with their API prompts. |
| `render` | Draws a canvas as an SVG, without a browser. |

Node definitions for the core nodes are bundled. For custom nodes, pass `--nodedefs` with an
`/object_info` export or the URL of a running ComfyUI server. A URL is read at
`<URL>/api/object_info`, following at most three redirects, only to `http` or `https`, and never
from `https` to `http`. `cwc` does not judge where the server or a redirect points: a server on
this machine or the local network is the usual source, so name only a server you trust.

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

A key `cwc` accepts but does not use yet (`ui_locked`, `ui_expose`, `ui_note`,
`_workflow.title`, `_workflow.locale`, `_workflow.groups.<id>.note`) is named in one info
finding, E8, per compile: one stderr line that lists the keys and the nodes that set them, and,
in the report of a successful compile, the keys one by one in `data.keys`. The exit code and
`ok` do not change.

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

## Status

Pre-alpha. Working now: the whole compile pipeline (normalize, instantiate, annotate, layout,
route, verify), the five presets, and the six commands above.

Planned, not in `cwc --help` yet: tidying an existing canvas without changing anything but
geometry, in-canvas notes and an in-canvas guide, PNG embedding and extraction, and live checks
against a running frontend.

```text
cwc beautify messy.ui.json [--preset ...] [--regroup] -o out/
cwc check    x.ui.json [--api x.api.json] [--live URL] [--publish]
cwc embed    x.ui.json --image cover.png -o x.workflow.png
cwc extract  image.png -o out/
cwc snapshot --from URL [--via-frontend] -o nodedefs.json
```

How it works: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (modules, layout rules, exit
codes), [`docs/m0-findings.md`](docs/m0-findings.md) (what the real frontend does),
[`docs/m1-templates.md`](docs/m1-templates.md) (widget templates and their evaluation) and
[`docs/m2-frontend-facts.md`](docs/m2-frontend-facts.md) (groups, node boxes and links as the
frontend stores them).

Paths on this page and in `docs/` (`src/`, `tests/`, `tools/`) are relative to the repository
root, and the commands in them run from there. `python3 -m unittest discover -s tests` runs the
tests. Playwright is needed only for the real-frontend checks in `tools/oracle/` and for
[`tools/make_readme_images.py`](tools/make_readme_images.py), which makes the pictures on this
page from a running ComfyUI server.

<details>
<summary>Milestones</summary>

| Milestone | What it covers | Status |
|---|---|---|
| M0 | Real-frontend oracle; measures the constants the layout depends on (`tools/oracle/`, `docs/m0-findings.md`) | done |
| M1 | Instantiation and equivalence; an API graph becomes a canvas that exports the same prompt (`docs/m1-templates.md`) | done |
| M2 | The five presets, layout and native link routing checks (`docs/ARCHITECTURE.md`, `docs/m2-frontend-facts.md`) | done |
| M3 | Reroute routing second round, in-canvas notes and guide, lossless tidy of an existing canvas, embed and extract | planned |
| M4 | A skill package for coding agents (done, in [`skills/`](skills/comfyui-workflow-compiler/SKILL.md)) and a showcase of workflows (planned) | partly done |
| M5 | Optional extensions after the first release | planned |

</details>

## Licence

Apache License 2.0 ([`LICENSE`](LICENSE)). The files `cwc` writes for you (`.ui.json`,
`.api.json`, `.svg` and reports today; `.guide.md` and `.png` with an embedded workflow once
those commands exist) belong to the author of the workflow and are not covered by it: see
[`LICENSE-OUTPUTS.md`](LICENSE-OUTPUTS.md).

No code or copyrighted data from GPL or AGPL projects is copied into this repository. The bundled
core node snapshot keeps only interoperability facts (names, types, order).
