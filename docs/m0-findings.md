# M0 findings: stages 1 and 2

Measurements against a real frontend. Stage 1 covers checklist items 1, 2, 3 and 7. Stage 2 so
far covers items 4 (MarkdownNote line heights) and 6 (Vue node size); item 5 is still open.

Environment of every number below: ComfyUI 0.45.0, frontend 1.59.1, CPU only, headless Chrome
(`channel="chrome"`, the machine's installed Chrome) at 1920x1080, device pixel ratio 1, served
at `https://<name>.localhost`. Stage 1 numbers (#1, #2, #3, #7) were taken on the classic
renderer (`Comfy.VueNodes.Enabled` false); #4 and #6 state the renderer per number. Nothing
here was measured on another frontend version or with custom node packs installed. Pixel
numbers (#4, #6) depend on the fonts of the machine: they were taken with the browser's default
fonts on one macOS machine, and another machine may differ (unverified).

Method: `tools/oracle/harness.py` opens the page in Playwright, injects settings by rewriting
the `GET /api/settings` response, and answers every write to `/api/settings` and
`/api/userdata` locally, so the server under test is never changed. `tools/oracle/m0.py`
loads the canvases in `tests/fixtures/oracle/` and prints the facts cited here as JSON
(`python tools/oracle/m0.py --url https://<name>.localhost [--only inputs,widgets,dynamic]`).
Every fixture is hand-written.

## #1 Full `inputs` array or links only

Canvases (`tests/fixtures/oracle/`), each with a sampler fed by an integer node on `steps` and
a latent node on `latent_image`, plus a decode node:

| File | `inputs` of the sampler as written |
|---|---|
| `inputs_spec_order.json` | all slots, widget sockets interleaved in the node definition's declaration order; link slot numbers in `links` match |
| `inputs_frontend_order.json` | all slots, link-carrying sockets first, widget sockets after (the order the frontend itself writes) |
| `inputs_compressed.json` | the four connection sockets plus the one linked widget socket; unlinked widget sockets omitted |
| `inputs_linked_only.json` | only the two linked slots, slot numbers in `links` renumbered to match |
| `inputs_linked_only_no_widget_key.json` | as above, the linked widget slot without its `widget` key |
| `inputs_linked_only_stale_slots.json` | only the two linked slots, but `links` still carries the slot numbers of the interleaved form (2 and 8) |

Observations (1.59.1):

- All six load with no load error and no console error. The only console message is the
  `output.links is deprecated` warning, present in every run.
- `graphToPrompt().output` is identical for all six. Compared to the interleaved fixture:
  `{cfg, denoise, latent_image: ["2", 0], sampler_name, scheduler, seed, steps: ["1", 0]}` with the same values.
- `app.graph.serialize()` is identical for all six: connection slots first, then widget
  slots, each group in definition order (`model, positive, negative, latent_image, seed,
  steps, cfg, sampler_name, scheduler, denoise`). It restores omitted slots, restores the
  `widget` key on the linked widget socket, and recomputes the target slot number in
  `links` by input name (stale 2 and 8 became 5 and 3).
- `graphToPrompt().workflow` is not the same as `serialize()`: for the sampler it lists
  only the four connection sockets and the linked widget socket, without the unlinked
  widget sockets.
- Round trip: loading the `serialize()` output of `inputs_spec_order.json` twice and
  exporting again gives identical nodes, links and prompt (`roundtrip` in the m0 report;
  no differing node keys in either pass). Fixed point reached.
- Live node ids are strings (`"3"`); `serialize()` writes numbers (`3`); prompt keys are strings.
  The order of entries in `serialize().links` is not stable between runs; compare sorted.

Conclusion: any of the six forms is accepted; the frontend repairs missing slots, missing
`widget` keys and wrong slot numbers on load.
The writer should emit the form the frontend writes itself (second row of the table) so the
file is already a fixed point of load and save.

Design impact: the interleaved-by-declaration order assumed before M0 is not what the
frontend writes or creates. Slot positions are never trusted by the frontend (it matches by
name), so the interleaved form works, but it is not the canonical one.

Unverified: oldest supported frontend (1.41). Not run.

## #2 `widgets_values_named` against `widgets_values`

Canvas: `tests/fixtures/oracle/widgets_named_vs_positional.json`, loaded twice, once with the
default settings and once with `Comfy.Workflow.NamedValuesRestore` true. Nodes: a sampler with
conflicting named and positional values; named table with missing keys; named table only;
short positional array; long positional array; an extra unknown key in the named table; a
loader with an upload button; two primitive nodes on a combo and on an integer input; a
sampler fed by those primitives.

Observations (1.59.1), runtime `LiteGraph.namedValuesRestore`:

| Case | Setting default (false) | Setting true |
|---|---|---|
| conflict (named and positional differ) | positional values used, named table ignored | named values used |
| named table missing keys | positional values used | missing keys take the widget default; they do not fall back to positional |
| named table only, no positional array | all widgets take defaults | named values used |
| positional array short | missing trailing widgets take defaults | same |
| positional array long | extra values ignored | same |
| unknown key in named table | ignored | dropped |

- The setting is false by default in 1.59.1. The bundle's setting definition has default false;
  at runtime `namedValuesRestore` is false.
- `serialize()` writes `widgets_values_named` in both modes, filled from the live widgets
  and including the pseudo widgets.
- Pseudo widgets seen on the live node, all part of `widgets_values` in position order:
  - `control_after_generate`: combo with values `fixed`, `increment`, `decrement`,
    `randomize`; follows a number widget; not exported to the prompt (`options.serialize`
    false). 20 of 702 core types have one, always right after a number widget.
  - `upload` on the image loader: button, value `"image"`, occupies the second position of
    `widgets_values` (`["<file>", "image"]`), not exported to the prompt.
  - `control_filter_list`: only on a primitive node connected to a combo input, a string
    widget (value `"dpm"` in the fixture) after `control_after_generate`. It occurs on none of
    the 702 core types.
- A primitive node connected to a target writes its value into the target on export. In the
  fixture the target's own widget still showed `euler` after load while the prompt and
  `serialize()` carried the primitive's `heun`. The primitive itself is not in the prompt.

Conclusion: with default settings positional values decide and the named table is ignored.
With the setting on, the named table decides completely. A file is loaded correctly under
both settings only if the two agree on every widget, pseudo widgets included.

Design impact: see the discrepancy table. The writer must produce both from one source and
verify they match; it cannot rely on the named table alone.

Unverified: the version that introduced `widgets_values_named`; the older frontends' default;
behaviour on the Vue renderer.

## #3 V3 dynamic inputs

Canvases: `tests/fixtures/oracle/dynamic_v3.json` (hand-written, one node per case) and a
graph created from scratch by the frontend inside `check_dynamic` (`m0.py`); the node types
are an auto-growing image batch, a resize node with a dynamic combo, and a match-type
switch.

Auto-growing inputs (`COMFY_AUTOGROW_V3`):

- Slot names are `<input>.<prefix><n>`: `images.image0`, `images.image1`. The label is
  `image0` (the `label`, and `localized_name` in the saved file). Declaration for the batch node: prefix `image`, min 1, max 50.
- A fresh node has two slots: the minimum plus one empty trailing slot. Linking N slots gives
  N+1 slots: 1 linked gives 2, 2 gives 3, 3 gives 4. The first slot has no shape; later slots
  have shape 7. Whether growth stops at the declared max (50) was not tried.
- No entry in `widgets_values`; the node has no widgets. The empty trailing slot is not in
  the prompt, linked slots are: `images.image0: ["1", 0]`.
- Round trip: the file with two linked slots gets a third empty slot on load. The node's
  size was kept (`[140, 66]`) on the first load and became `[140, 86]` on the second load,
  and then stayed. Everything else is a fixed point after one load.

Dynamic combo (`COMFY_DYNAMICCOMBO_V3`):

- Sub inputs are named `<combo>.<key>`: `resize_type.multiplier`. Sub widgets sit directly after
  the parent widget in `widgets_values` (`["scale by multiplier", 0.5, "area"]`); the prompt
  key is dotted as well.
- An option can contribute a socket instead of a widget: `resize_type.match` is a
  connection socket (not in `widgets_values`; prompt key `resize_type.match: ["2", 0]`) beside
  the widget `resize_type.crop`.
- A file carrying only the positional array and only the plain input slot loads
  correctly: the frontend rebuilds the widget slots (`resize_type.longer_size`,
  `scale_method`) from the stored selection.
- Slot order differs between a fresh node and a node after the selection changes. Fresh: the
  default option's sub inputs come before the parent slot
  (`input, resize_type.width, resize_type.height, resize_type.crop, resize_type, scale_method`).
  After the selection is set: parent first (`input, resize_type, resize_type.multiplier, scale_method`).
  The widget order, and so `widgets_values`, is parent first in both.
- After selecting an option on a fresh node, its sub widgets hold their declared defaults
  (`multiplier` 1, `crop` `center`).

Match-type slots (`COMFY_MATCHTYPE_V3`):

- Declared and saved as `*`; at runtime the slot type is rewritten to the resolved type.
  Fresh node: `on_false:*`, `on_true:*`, output `*`. With one linked slot, that slot stays
  `*` and the other slots and the output become `IMAGE`. With all linked: all `IMAGE`.
  A file saved with `*` on linked slots loads with the resolved type.
- The saved type therefore depends on the connections: `serialize()` writes the resolved
  type for an unlinked match slot and `*` for the linked slot of the one-link case above.

`dynamic_v3.json` loads with no error and the prompt is as expected. Its round trip is a
fixed point after the second load, apart from the size change noted above. The graph
created by the frontend was not round-tripped.

Design impact: the writer must emit dotted names and the dynamic slots as the frontend
rebuilds them; the prompt side needs dotted keys and ignores the empty trailing slot.

Unverified: more than one dynamic combo on a node; nested dynamic inputs; other autogrow
templates (only the image batch node was tried); the 1.41 frontend; the Vue renderer.

## #4 MarkdownNote body height

Method: `python tools/oracle/m0.py --url https://<name>.localhost --only markdown` (code:
`tools/oracle/sizes.py`, page side `markdownBlocks` in `tools/oracle/js/lib.js`). The 29 notes of
`tests/fixtures/oracle/markdown_cases.json` (headings h1 to h3, single-line paragraphs, wrapped
paragraphs, tables of 1 to 6 rows, bullet and numbered lists, Chinese and English mixed lines,
three mixed documents) are loaded one per canvas at saved width 400 and 480 and saved height 60
(too small), under each renderer. The script reads every rendered block's height, margins and
line count from the DOM, and the body's content extent (first block top to last block bottom
margin; equals `scrollHeight` where the body overflows).

Both renderers, 58 loads each:

- Classic (Tiptap, `.tiptap` inside the DOM widget) never grows the node. The live size is
  `max(saved, computeSize())`, 88 high for a bare note, so a saved height of 60 became 88. A
  body taller than the box scrolls (`overflow-y: auto`). The height that shows the whole body is
  `extent + 22`: the widget element is always 22 shorter than the node. Checked on the 24 cases
  whose needed height is at least 88: a node of that height has no overflow, a node 3 lower
  overflows by exactly 3.
- Vue (`marked` html in `.comfy-markdown-content`) grows the node. The DOM box is
  `max(live size, extent + 18)` plus the 30 high title bar; 28 cases grew past the minimum and
  read 17.99 to 18.00 for the extra. The body area has `min-height: 60px`. Neither renderer
  writes anything back: `node.size` and `serialize()` stay at the classic value.
- Width 400 against 480 changes the wrapped line counts, not the height of a line, row or item
  (units agree to 0.1 for every kind).

Height of one block (graph px; text size is the `Comfy.TextareaWidget.FontSize` setting, 10 by
default, read as `--comfy-textarea-font-size` = `10px` in the page; whether the server's stored
setting was the default was not checked):

| Block | Classic | Vue |
|---|---|---|
| h1 line | 24 (28 with CJK text) | 18.86 |
| h2 line | 19 | 15.42 |
| h3 line | 14 | 13.70 |
| paragraph line | 12 (14 with CJK text) | 16 (CJK the same) |
| table | 23 per row + 1 | 33 per row - 1 |
| list item | 12 (14 with CJK text) | 24 per item - 8 per list |

Margins (collapsing between neighbours, so the gap is the larger of the upper block's bottom and
the lower block's top margin): classic h1 13.4, h2 10.05, h3 7.84, paragraph 10, list 10, table
0; none above the first block or below the last one. Vue h1 10.53, h2 8.61, h3 7.66, list 8, table
0, paragraph 5 below (none above); the bottom margin of the last block stays, except for a
paragraph.

Predicting the body height from block kinds and counts only (per-kind constants read from the
same data, margins collapsing as above): residual against the measured extent within 0.04 px on
all 116 loads. So the structure above is exact for these cases; it was not tested on bold or
inline code, code blocks, quotes, images, nested lists, wrapped table cells or list items.

Font size 14 (width 400, 29 loads each): the extra stays 22 (classic) and 18 (Vue), the
prediction stays within 0.06. Vue scales as 1.6 x font size per line (22.39), table row 1.6 x
size + 17 (39.39), list item 1.6 x size + 8 (30.39); classic paragraph line 17, h1 34, h2 25,
h3 20, table row 32, list item 17 (CJK 20 to 40). The constants above are for size 10 only.

Constants a compiler can use for both renderers (`envelope` in the report): every block is
charged its height plus its largest margin, so a sum never falls below either renderer. Over all
116 loads the sum plus the 22 extra is at least 4.5 px above what a node must be (smallest
margin of safety: a single h3), and up to 106 px above (a classic list of 8). It is an upper
bound, not a fit.

| Constant | Value |
|---|---|
| Heading line, h1 / h2 / h3, gap included | 41.4 / 29.05 / 21.84 |
| Paragraph line, plus 10 per paragraph | 16 |
| Table row, plus 1 per table | 33 |
| List item, plus 10 per list | 24 |
| Added to the sum | 22 |

Against the provisional values assumed before measuring (24 paragraph line, 34 table row, 36
heading line, 40 padding): the table row (33 against 34) is close; the heading line is not
one value but three, 41.4 / 29.05 / 21.84 for h1 / h2 / h3 (41.4 is the classic CJK h1; an
ASCII h1 is 37.4), against the single 36 assumed; a paragraph line is 12 to 16, not 24; the fixed padding is 22,
not 40; the assumed values have no per-block gap, which costs 5 to 13.4 px per block here.

Unverified: other fonts or machines, wrapped lines in a table cell or list item, nested
structures, and the frontend's `Comfy.TextareaWidget.FontSize` at values other than 10 and 14.

## #5

Stage 2.

## #6 Vue node size

Method: `python tools/oracle/m0.py --url https://<name>.localhost --only vue_sizes` (code:
`tools/oracle/sizes.py`). Nine core nodes (`tests/fixtures/oracle/sizes_small.json`, saved size
100 x 40; `sizes_large.json`, 640 x 720) are each loaded alone on a canvas, once per renderer.
The Vue box is the `[data-node-id]` element, title bar included (the box is the saved height
plus 30). Images that the server does not have are answered by the harness with a solid
512 x 768 PNG; the two image-saving nodes get a faked `executed` event.

- Width: Vue shows `max(saved width, 225)`. A saved width of 100 gave 225 on every node, saved
  640 gave 640. A saved width above 225 is kept, also when the node's own minimum is lower (140
  for the decoder).
- Height: Vue shows `max(live height, natural DOM height)`, where the live height is the
  classic one (`max(saved, computeSize())`, applied on load before Vue draws). From 720 saved,
  every node stayed 720: the saved height is a floor, nothing shrinks and nothing is added.
- The Vue growth is DOM only. `node.size` and `serialize()` stay at the classic value in every
  load, and `boundingRect` follows the DOM for some nodes but not all (it did not for the
  preview nodes). Read the DOM, not `boundingRect`.
- Natural body height under Vue (saved 100 x 40), against the classic height after load:

| Node | Classic after load | Vue natural body |
|---|---|---|
| Checkpoint loader | 210 x 98 | 225 x 126 |
| CLIP text encode (multi-line box) | 218.7 x 88 | 225 x 102 |
| KSampler | 210 x 262 | 225 x 262 |
| Empty latent image | 210 x 106 | 225 x 118 |
| VAE decode | 140 x 46 | 225 x 54 |
| Save image (preview showing) | 210 x 58 | 225 x 302 |
| Load image (file chosen) | 222.8 x 314 | 225 x 346 |
| Preview image (preview showing) | 140 x 40 | 225 x 266 |
| Note (multi-line box) | 210 x 88 | 225 x 88 |

  The Vue height is the classic one or taller in all nine; the difference is 0 (KSampler, Note)
  to 28 (checkpoint loader), and 244 and 226 for the preview nodes. Save image and preview image
  have no preview in the classic render here: the faked execution result did not create an image
  on the classic node (`node.imgs` stayed empty), so the classic height after a real run is
  unverified.
- Multi-line text box under Vue at the natural size: 64 high in the text-encode node, 70 in the
  note, 205 wide. Forty lines of text did not change either box or the node. Under classic the
  same boxes were 42 and 66 high at the classic minimum.
- Image area under Vue at the natural size: 209 x 196, for images of 512 x 768, 768 x 512 and
  64 x 64 alike, so the area does not follow the image. In a saved 640 x 720 node it filled the
  rest (624 x 570 to 650).

Unverified: nodes with many widgets or sockets (only nine types were measured), other widths
for wrapped slot labels, the preview height after a real execution in the classic renderer, the
Vue title bar height (28 for the header element, 30 for the box), and other machines.

## #7 Batch capture of node truth

`python tools/oracle/oracle.py truth --url https://<name>.localhost -o <scratch>/truth.raw.json.gz`
creates every registered type once with `LiteGraph.createNode`, adds it to the graph
(`graph.add`), reads it, and removes it. Only types listed by the server's `/object_info` are kept.
The raw snapshot is not bundled as is: `python tools/build_core_data.py <object_info> <scratch>/truth.raw.json.gz --out src/cwc/data/`
writes the plain JSON files in `src/cwc/data/` (see docs/ARCHITECTURE.md).

- 706 registered types, 702 in `/object_info`, 702 captured, 0 failures, 0 types missing from
  the frontend. The four extra types are frontend-only and are not in the snapshot: note,
  markdown note, primitive and reroute.
- Time: page start 0.7 to 0.9 s, create loop 0.55 to 0.71 s (about 1 ms per type), whole run
  4.2 to 5.2 s (two runs). Only core types were available; a larger set with custom node
  packs was not measured.
- Four types print the warning `LiteGraph addWidget(...) without a callback or property
  assigned` on creation, and still produce their widgets: the build-prompt node for
  Ideogram, the bounding-box creator, the image compositor, the painter.
- Determinism: two consecutive runs produce byte-identical files (sha256 `0ad7e97c...a4d`).
  The gzip has no name and mtime 0 and the JSON is canonical (sorted keys, compact).
- `graph.add` matters. A first run created nodes without adding them: two types differed:
  the custom combo node's `choice` default is `null` without it and `""` with it, and the audio
  recorder's second widget is named `audio` without it and `audio#1` with it (a name
  deduplication that runs on add). The snapshot uses the added form because a canvas
  loaded in the frontend has its nodes added.
- Coverage of the snapshot: 3563 input slots, 976 output slots, 2400 serializable widgets,
  34 of them not exported to the prompt (`control_after_generate` 20, `upload` 5, and 9
  others: crop-region values, lora lists, a camera view, a refresh toggle, an audio recorder).
- 9 input slots name a widget that is not in the serializable list (audio player, comparison
  view, preview and camera-state widgets). `inputs[].widget` is therefore not guaranteed to
  appear in `widgets`.
- 63 core types have an input name containing a dot on a fresh node: dynamic combo and
  auto-growing slots, and plain names such as `time_embed.` on the model-merge nodes. A dot
  alone does not mean a dynamic input. 29 types carry 36 slots of type `COMFY_DYNAMICCOMBO_V3`.
- 308 of 702 types have a fractional `size.classic` width. Widths of 270 (310 types),
  400 (59) and 140 (20) are the common values; the rest look like text measurements.
  Hypothesis, not checked: those widths depend on the fonts of the machine that took the
  snapshot, so a snapshot from another host may differ in width. Heights are integers.
- Combo widget values in the snapshot are the current value only (`null` when the server has
  no file for it); option lists are not recorded, so no file or model name from the host
  enters the snapshot. `tests/test_core_truth.py` asserts that.
- Console and HTTP noise: in the fixture runs the only errors are 404s for the example image
  that the loader fixture names. A single 502 seen once during start-up setup did not recur
  (unverified, probably a start-up race).

Tolerance to a failing extension script: not tested, because the server has no extensions
installed. Unverified.

## #8 Dynamic prompts: the exported text is not the widget value

Frontend 1.59.1 gives every widget whose input spec has `dynamicPrompts: true` a different
`serializeValue`, and `graphToPrompt` exports through it. `app.graph.serialize()` keeps the raw
value, so the canvas and the exported prompt disagree for some texts. Sources, read from the
v1.59.1 tag of the frontend:

- `src/renderer/extensions/vueNodes/widgets/composables/useStringWidget.ts:115`: a STRING
  input spec with a boolean `dynamicPrompts` sets `widget.dynamicPrompts`, multiline or not.
  54 inputs of 37 core types carry `dynamicPrompts: true` in `/object_info`
  (`CLIPTextEncode.text`, the SDXL and SD3 encoders, the text generators, music tags and lyrics).
- `src/extensions/core/dynamicPrompts.ts`: for each widget with the flag, `serializeValue`
  returns `processDynamicPrompt(widget.value)` when the value is a string.
- `packages/shared-frontend-utils/src/formatUtil.ts:231-293`, `processDynamicPrompt`:
  1. `/\/\*[\s\S]*?\*\/|\/\/.*/g` strips comments first. A JavaScript `.` stops at `\n`, `\r`,
     U+2028 and U+2029. `https://x` becomes `https:`.
  2. A backslash pairs with the next character and both stay; at the very end the next
     character is `undefined`, so `trailing\` exports as `trailing\undefined`.
  3. `{` opens a choice block up to the matching `}` (nesting counted, escapes skipped, an
     unterminated block still keeps its option); one option is picked at random and processed
     recursively with the same function.
  4. The result loses a backslash before `{`, `}` or `|`.
  A lone `|` or `}` outside a block is text.

Measured on 1.59.1 through `tools/oracle/record_dynamic_prompts.py`: 49 texts were loaded as the
`text` of a text encoder in a hand-built canvas and exported (`tests/fixtures/prompt/dynamic_prompts.json`).
Covered: line comments ended by `\n`, `\r`, U+2028, U+2029 and `\r\n`, block comments over several
lines, `/*/`, an unterminated `/*`, every escape, a backslash before ordinary characters, an astral
character after a backslash, nested, one-option, empty and unterminated blocks, `{/}`, and blocks
whose options are equal (no random text was recorded). `cwc.prompt.dynamic.process` reproduces all 49.

The rule the compiler follows:

- `cwc.prompt.from_canvas` exports `process(value).text` for a string in such a widget, a value a
  primitive pushed included. A random block exports its first option there; `process(...).random`
  tells a caller that the frontend would pick differently.
- `cwc.instantiate` writes `escape(value)`: `{`, `}` and `|` get a backslash, a `/` that starts
  `//` or `/*` is written `{/}` (a one-option block yields `/` without forming a comment). A run of
  backslashes before a brace or pipe is written one longer. The candidate is checked with `process`.
- An odd number of backslashes at the end of the text has no spelling: normalize reports E5, also for
  the declared default of an input the author left out or linked, which instantiate writes too.
- Before the fix, `cwc compile` wrote such text unchanged and reported `ok: true`, and the frontend
  exported a different prompt (`photo of {red|red} car, see https://example.com end` became
  `photo of red car, see https:`).

Unverified: a frontend extension or a custom node that sets `dynamicPrompts` outside the input spec,
and custom node inputs that the offline definitions do not carry (a type without a definition has
no flag; its text is passed as written).

## #9 Unknown node types under `--allow-unknown`

Question: a node whose type the compiler has no definition for is written as a placeholder with
its literal inputs, by name, as `widgets_values` in the order the author wrote them
(`cwc.instantiate.node.widget_values`). What does a frontend that does know the type export?

Method: `tools/oracle/probe_unknown_types.py`, frontend 1.59.1. One core type is removed from the
compiler's definitions, a text to image graph using it (inputs in API-export order) is compiled
with `allow_unknown=True` (report ok, one E1 warning), and the canvas is loaded in the oracle,
which has the type. Exports are compared with the input prompt.

| Case | Export against the input prompt |
|---|---|
| nothing removed (control) | identical |
| `ImageScale` removed, inputs in the node's widget order | identical |
| `ImageScale` removed, inputs written in another order | `upscale_method`, `width`, `height` and `crop` take each other's values |
| `KSampler` removed, inputs in API-export order | `steps`, `cfg`, `sampler_name`, `scheduler`, `denoise` are shifted by one position (`cfg` exports `"euler"`, `scheduler` exports `0.75`, `denoise` falls back to its default) |

Load error, prompt error and console were empty in all four cases: the frontend loads the canvas
and exports it without a sign that the values are wrong.

Reading: the frontend takes an unknown-to-the-compiler node's values by position (named values are
ignored at the default setting, #2), so the result is right only when the author's input order is
the type's widget order and the type has no widget that the API prompt leaves out. The second
condition fails for every type with `control_after_generate` (20 core types), whose combo sits
between `seed` and `steps` in the saved list. The compiler cannot tell, so E1 under
`--allow-unknown` is a warning that the node's exported values are not verified and may be shifted
(`cwc.normalize.build`).

Not measured: a custom node with a widget the API prompt leaves out other than
`control_after_generate`; a type with a dynamic or Autogrow group.

## Constants

| Constant | Value | Source |
|---|---|---|
| Frontend / server version | 1.59.1 / 0.45.0 | `window.__COMFYUI_FRONTEND_VERSION__`, `/api/system_stats` |
| Classic node width, default | 270 | 310 of 702 types |
| Classic node width, other common | 400 (59 types), 140 (20 types) | truth |
| Classic node height, n widgets, one output, no sockets | 34 + 24 n | n = 1..5 and 10, all groups sampled agree |
| Classic node height, sockets only | 6 + 20 * max(inputs, outputs, 1) | samples of 1 to 4 rows; three types with larger custom sizes do not fit |
| Autogrow slots on a fresh node | min + 1 | image batch: min 1, 2 slots |
| Autogrow slots after k linked | max(min, k) + 1 | k = 0..3 on the image batch; the max cap was not tried |
| Optional-slot shape value | 7 | later autogrow slots, match-type slots |
| `control_after_generate` | 20 core types; combo; not in prompt; follows a number widget | truth |
| `Comfy.Workflow.NamedValuesRestore` default | false | runtime flag and setting definition |
| Truth snapshot, one type per call | about 1 ms | `create_loop_seconds` |
| Vue node minimum width | 225 | nine core nodes, saved 100 |
| Vue node height | max(classic live height, DOM natural height) | nine core nodes; DOM only, `serialize()` unchanged |
| Vue title bar in the box | 30 | box = saved height + 30 on 640 x 720 |
| Vue multi-line box, natural | 64 (text encode), 70 (note) | independent of text length |
| Vue image area, natural | 209 x 196 at width 225 | independent of image size |
| MarkdownNote classic, node minus body | 22 | 58 cases, all equal |
| MarkdownNote Vue, node minus body | 18 | 28 cases that grew |
| MarkdownNote classic line / row / item | 12 (CJK 14) / 23 / 12 (CJK 14) | font size 10 |
| MarkdownNote Vue line / row / item | 16 / 33 / 24 | font size 10 |
| MarkdownNote constants for both renderers | heading 41.4 / 29.05 / 21.84, line 16 (+10 per paragraph), row 33, item 24 (+10 per list), 22 added | upper bound, 116 loads |

The height rows of the classic node are fits to the sampled types in the truth data and were not
checked on every type; the widths of 270 and 400 are exact for the types that use them. The
MarkdownNote and Vue rows were measured at font size 10 on one machine (see #4, #6).

## Differences from the initial assumptions

"None recorded" means no assumption was stated; the row is a new fact.

| Item | Assumption before M0 | Measured (1.59.1, classic) |
|---|---|---|
| Named restore | the named table is the one the frontend reads when present; keys missing from it get defaults | `NamedValuesRestore` is off by default: the named table is ignored, positional values decide. When the setting is on, the named table decides and missing keys get defaults |
| `inputs` order from `serialize()` | the node definition's declaration order, widget sockets interleaved | connection sockets first, then widget sockets, each in declaration order; a node created by the frontend has the same order |
| Omitted or stale `inputs` | none recorded | repaired on load; slot numbers in `links` are recomputed by input name |
| `control_filter_list` | a third pseudo widget after every combo that has the `control_after_generate` option | appears only on a primitive node connected to a combo; none of the 702 core types has it |
| Node id type | none recorded | the live node id is a string; `serialize()` writes a number; prompt keys are strings |
| Autogrow initial slots | none recorded | min + 1; N linked give N + 1 |
| Match-type slot type | none recorded | rewritten at runtime to the resolved type; unlinked slots on a node with a linked partner already show the resolved type |
| Dynamic combo slot order | none recorded | parent first only after the selection changes; a fresh node lists the default option's sub inputs before the parent slot |
| Node size on reload | none recorded | an auto-grown node can change size on the second load (66 to 86 high), then is stable |
| Truth capture | `createNode` only | `graph.add` changes two types' defaults; the snapshot uses the added form |
| Vue width | saved value, not below 225 | confirmed: `max(saved, 225)` |
| Vue height | saved value is the floor, content grows it | confirmed, DOM only; the size written to the file stays the classic one. Natural bodies are 0 to 28 higher than classic for ordinary nodes |
| Multi-line box and preview minimum (assumed: 50 and 220) | classic computeSize minima | Vue natural: box 64 to 70, image area 196 high; neither depends on content |
| MarkdownNote line heights (assumed: 24 / 34 / 36, pad 40) | provisional | classic paragraph 12, row 23, h1 24; Vue paragraph 16, row 33, h1 18.9; node extra 22 / 18; block gaps 5 to 13.4 are not in the assumed formula |
