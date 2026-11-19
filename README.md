# comfyui-workflow-compiler

`cwc` turns a ComfyUI API graph, written by a coding agent or by hand, into a native
canvas workflow: grouped, laid out by a chosen preset, routed with native link reroutes,
and checked to export the same prompt it was compiled from. Planned next: an in-canvas
guide, and tidying an existing canvas without changing anything but geometry.

Status: pre-alpha. `cwc compile` runs the whole pipeline (normalize, instantiate,
annotate, layout, route, verify) and writes the canvas, the API prompt and a report;
`cwc node`, `cwc learn` and `cwc render` are available too. Tidying an existing canvas,
in-canvas notes and the guide, PNG embedding and extraction, and live checks against a
running frontend are not available as commands yet. Details: `docs/ARCHITECTURE.md`
(modules and layout rules), `docs/m0-findings.md` (what the real frontend does),
`docs/m1-templates.md` (widget templates and their evaluation) and
`docs/m2-frontend-facts.md` (groups, node boxes and links as the frontend stores them).

## Commands

```
cwc node     [TYPE | --search KW] [--nodedefs FILE|URL] [--json]
cwc compile  GRAPH.json -o DIR [--nodedefs FILE|URL] [--preset NAME] [--all-enabled]
             [--allow-unknown] [--json]
cwc learn    FILE [FILE ...] -o FILE [--nodedefs FILE|URL] [--json]
cwc render   GRAPH.ui.json -o OUT.svg [--nodedefs FILE|URL] [--json]
```

- `node` shows one node definition or searches the definitions.
- `compile` turns an authoring graph into a canvas and its API prompts.
  `--preset` takes one of `bento`, `swimlanes`, `cockpit-split`, `tower-preview`,
  `stepped`, or `auto`, which chooses by the size and shape of the graph.
- `learn` learns widget templates from canvases paired with their API prompts.
- `render` draws a canvas as an SVG, without a browser.

## Planned

These commands are not in `cwc --help` yet.

```
cwc beautify messy.ui.json [--preset ...] [--regroup] -o out/
cwc check    x.ui.json [--api x.api.json] [--live URL] [--publish]
cwc embed    x.ui.json --image cover.png -o x.workflow.png
cwc extract  image.png -o out/
cwc snapshot --from URL [--via-frontend] -o nodedefs.json
```

The core needs Python 3.10 or newer and nothing outside the standard library.
Playwright is needed only for the real-frontend checks in `tools/oracle/`.

## Milestones

| Milestone | What it covers | Status |
|---|---|---|
| M0 | Real-frontend oracle; measures the constants the layout depends on (`tools/oracle/`, `docs/m0-findings.md`) | done |
| M1 | Instantiation and equivalence; an API graph becomes a canvas that exports the same prompt (`docs/m1-templates.md`) | done |
| M2 | The five presets, layout and native link routing checks (`docs/ARCHITECTURE.md`, `docs/m2-frontend-facts.md`) | done |
| M3 | Reroute routing second round, in-canvas notes and guide, lossless tidy of an existing canvas, embed and extract | planned |
| M4 | A skill package for coding agents and a showcase of workflows | planned |
| M5 | Optional extensions after the first release | planned |

## Licence

The tool is licensed under the Apache License 2.0 (`LICENSE`).

Files that `cwc` produces for you (`.ui.json`, `.api.json`, `.guide.md`, `.svg`, `.png`
and reports) belong to the author of the workflow and are not covered by this licence.
See `LICENSE-OUTPUTS.md`.

No code or copyrighted data from GPL or AGPL projects is copied into this repository.
The bundled core node snapshot keeps only interoperability facts (names, types, order).
