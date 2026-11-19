# M2 frontend facts: groups, node boxes, load changes, links, reroutes

Frontend 1.59.1, both renderers. Source lines are `path:line` in the `ComfyUI_frontend` tag
`v1.59.1`. Measurements come from `tools/oracle/geometry.py record`, which loads
`tests/fixtures/layout/{boxes,links}.canvas.json` in three runs (classic, Vue, classic with
`pysssss.SnapToGrid` on) and writes `tests/fixtures/layout/<canvas>.<run>.json`.
`tests/test_layout_facts.py` restates every rule below as arithmetic and checks it against
those files. Node sizes under Vue and MarkdownNote heights are in `docs/m0-findings.md` (#4,
#6) and are not repeated here.

## 1. Group membership

Rule: a node belongs to a group when the centre of its `boundingRect` (the visual box, title
band included) lies in the group's `bounding`, half-open: `x <= cx < x + w`,
`y <= cy < y + h`. A group is a child of another when the other wholly contains its bounding
(edges may touch; identical rectangles do not count). Membership is not exclusive: a node in
a nested group is a member of the inner and of every outer group. "Innermost group" (G3) is
therefore the checker's own choice, the smallest group whose membership holds.

- Source: `src/lib/litegraph/src/LGraphGroup.ts:416-463` (`recomputeInsideNodes`: nodes by
  `containsCentre(this._bounding, node.boundingRect)`, reroutes by point, groups by
  `containsRect`, recursion into contained groups); `src/lib/litegraph/src/measure.ts:77-83`
  (`isInRect`, half-open), `:144-148` (`containsCentre`), `:156-170` (`containsRect`).
- When it runs: not at load. Callers are a drag of the group title
  (`src/lib/litegraph/src/LGraphCanvas.ts:3003`), a selection rectangle that wholly contains
  the group (`LGraphCanvas.ts:4722`), group menu commands
  (`src/composables/graph/useGroupMenuOptions.ts:30,89`, `src/extensions/core/groupOptions.ts:47,56,84`,
  `src/composables/useCoreCommands.ts:638,796`) and convert-to-subgraph (`LGraph.ts:2261`).
  Membership is never saved; it is recomputed from geometry each time.
- Collapsed node: its `boundingRect` is the title band only (section 3), so only the band's
  centre counts.
- Measured (`boxes.*.json`, all three runs, identical): Outer `[60,100,1400,650]` holds nodes
  1, 3, 4, 5, 8 and child group 2. Node 8 sticks out 50 px below Outer and is a member (centre
  inside). Collapsed node 2 (`pos` y 100, band y 70..100) is not a member: the band centre
  y 85 is above the group. Node 7's centre x 1950 equals Overlap's right edge 1950: not a
  member. Nodes 1 and 4 are members of Inner and of Outer. Overlapping groups (Outer and
  Overlap) both keep their members independently.

## 2. Group geometry

Rule: `bounding` is `[x, y, w, h]` of the whole group, the title band included at its top. The
title band height is `LiteGraph.NODE_TITLE_HEIGHT` = 30; the title font is 20 px `Inter`
(`GROUP_TEXT_SIZE`, `GROUP_FONT`). `LGraphGroup.minWidth` 140 and `minHeight` 80 apply only
through the `size` setter (interactive resize), not on load. A load keeps the saved bounding
exactly unless snap to grid is on, which rounds `x, y` to the grid and keeps `w, h`.

- Source: `LGraphGroup.ts:53-54` (minimums), `:221-226` (`size` setter clamps),
  `:266-268` (`titleHeight`), `:293-300` (`configure` calls `setBounds` without clamping),
  `:302-311` (`serialize` writes the bounding back), `:321,360` (title font);
  `src/lib/litegraph/src/LiteGraphGlobal.ts:80,107,108`; snap on add: `LGraph.ts:1407-1411`
  and `:3408-3410`, `LGraphGroup.ts:399-407` (position only, `round`).
- Measured: every saved bounding, including `[1500,800,100,50]` (below the minimum) and
  `[1503.3,900.7,401.1,203.9]` (off grid), comes back unchanged from `serialize()` and from
  the live group in the classic and the Vue run. With snap on the off-grid group became
  `[1500,900,401.1,203.9]`. `font_size` 20 and `titleHeight` 30 read from the live groups.

## 3. Node boxes

Rule, classic renderer: expanded `boundingRect` = `[x, y - 30, size.w, size.h + 30]`.
Collapsed = `[x, y - 30, cw, 30]` with `cw = min(size.w, titleTextWidth + 60)`, the text
measured in the node's sub-text font (`innerFontStyle`, `normal 12px Inter`, `LGraphNode.ts:2532`; the
title itself is drawn in the 14 px title font); `cw` falls back to 80 (`NODE_COLLAPSED_WIDTH`) only when the
measurement gives 0 (no canvas context yet). Vue renderer: `boundingRect` is the DOM box. An
expanded node is `[x, y - 30, max(size.w, 225), max(live h, natural DOM h) + 30]` (m0 #6). A
collapsed node's width comes from its DOM header (short titles 225, longer titles grow), its
height is 30 in `boundingRect` while the DOM header measures 28.

- Source: `src/lib/litegraph/src/LGraphNode.ts:2518-2542` (`measure`: title offset, classic
  collapsed width, Vue path through `renderingSize`), `:930-944` (`renderingSize`, collapsed
  under Vue from the layout store's content size), `LiteGraphGlobal.ts:80,87`.
- Measured, classic: title "P" collapsed to width 67.66, title
  "A synthetic long title for the collapsed width" to 308.71, "Below" 94.55, "VAE Decode"
  130.38 (all with `size.w` 400 or 210, so the text term won). Vue: "P", "Below" and
  "VAE Decode" collapsed to 225, the long title to 284.7; DOM height 28. Expanded under Vue:
  Load Checkpoint 315 x 156 (classic 315 x 128), VAE Decode 225 x 84 (classic 210 x 76).
- Not measured: a collapsed node whose saved `size.w` is smaller than its title (the rule
  says the box is then `size.w` wide in classic).

## 4. What a load changes

Rule: without snap to grid a load keeps `pos`, `size`, `flags` and `groups[].bounding` as
saved, apart from the classic minimum size (`max(saved, computeSize())`, m0 #6) which grows an
undersized node. Vue growth is DOM only and never reaches `serialize()` (m0 #6). With
`pysssss.SnapToGrid` on (default off), every node's `pos` is rounded and its `size` rounded up
(`ceil`) to `Comfy.SnapToGrid.GridSize` (default 10, `CANVAS_GRID_SIZE`), and every group's
position is rounded. Collapsed flags are kept.

- Source: `src/platform/settings/constants/coreSettings.ts:833,843` (grid size default),
  `:848,854` (`pysssss.SnapToGrid` default false); `LGraph.ts:3358-3366` (node snap on load:
  `snapToGrid`, then `snapPoint(size, snapTo, 'ceil')`); `measure.ts:354-364` (`snapPoint`).
- Measured (`boxes.*.json`): classic and Vue kept `pos [103.7,457.3]`, oversized KSampler
  `size [400,500]`, `flags.collapsed`, all group boundings. Snap on: `[103.7,457.3]` became
  `[100,460]`, sizes `315x98` → `320x100`, `315x106` → `320x110`, `210x46` → `210x50`;
  KSampler 400 x 500 and the collapsed nodes' 400 x 200 unchanged.
- Not measured: undersized nodes in these canvases (covered by m0 #6), other settings that
  move nodes on load (none found besides snap).

## 5. Links

Rule: the default link render mode is spline (`Comfy.LinkRenderMode` default 2 =
`LinkRenderType.SPLINE_LINK`). A link without reroutes is a cubic Bezier from the output anchor
`p0` to the input anchor `p3` with `d = 0.25 * |p3 - p0|`, `c1 = p0 + (d, 0)`,
`c2 = p3 - (d, 0)` (output slots point right, input slots left). The Vue renderer draws links
on the same canvas with the same adapter; only the anchors differ.

Anchors, classic (`size` is the live node size):

- Input, a plain slot at row `r` (counting only inputs without a widget):
  `(x + 10, y + (r + 0.7) * 20)`. Output at row `r`: `(x + size.w + 1 - 10, y + (r + 0.7) * 20)`.
- Widget-backed input: placed on its widget's row, not by the formula (Load Checkpoint
  `ckpt_name` at `y + 76`, EmptyLatentImage `width/height/batch_size` at `y + 36/60/84`).
- Collapsed: every input at `(x, y - 15)`, every output at `(x + cw, y - 15)`.

Anchors, Vue (read from the DOM slot elements through the layout store): input row `r` at
`(x, y + 12 + 20 r)`, output row `r` at `(x + box.w, y + 12 + 20 r)`; widget-backed inputs at
their widget rows (section 5.1); collapsed at `(x, y - 16)` and `(x + box.w, y - 16)`.

- Source: `coreSettings.ts:702-714`; `src/lib/litegraph/src/types/globalEnums.ts:63`;
  `LGraphCanvas.ts:1394,6675-6685` (render call with the slot directions);
  `src/renderer/core/canvas/litegraph/litegraphLinkAdapter.ts:173-193` (`applySplineOffset`),
  `:235-283` (control points, factor 0.25); `src/renderer/core/canvas/litegraph/slotCalculations.ts:74-146`
  (classic anchors), `:155-236` (Vue anchors from `layoutStore.getSlotOffset`, fallbacks).
- Measured: per link, 21 points of the predicted curve tested with `isPointInStroke` against
  the `Path2D` the canvas stroked (3 px test stroke). Classic: 13 of 13 links, 21/21 points.
  Vue: 12 links 21/21, link 4 of `boxes` 20/21 (not investigated; a sub-pixel miss is the
  likely cause, unverified). Anchors: the classic formulas hold exactly in every classic node;
  the Vue row formula holds for every expanded node in `boxes.vue.json`.
- Measured oddity: in the Vue run, collapsed VAE Decode put its second input (`vae`) at
  `x + 13.4`, not at `x`. Not investigated.
- Not measured: link modes other than spline, slot `dir` overrides. Reroutes: section 6.

### 5.1 Vue widget rows and text boxes

Rule, Vue renderer, expanded node with `rows = max(link-only inputs, outputs, 1)`: the widget
rows start at `y + 20 * rows + 4` and follow each other without gaps, in widget order. Per row,
its height and where a widget-backed input's anchor sits in it:

| Row | Height | Anchor from the row top |
|---|---|---|
| plain widget (number, combo, toggle; a value-control combo shares its number's row) | 28 | 14 |
| single-line text | 36 | 18 |
| any widget whose input is fed by a link (the widget gives way to its slot) | 24 | 12 |
| multi-line text box, not fed by a link | 68 + share | 34 + share / 2 |
| advanced widget (`advanced` in the input options), not fed by a link | 0 (hidden) | see below |

The natural body (title band excluded) ends 10 below the last row, plus 24 for the
advanced-inputs button when a widget is hidden. When the body is taller than natural (the
node's live height, `max(saved h, classic minimum)`), the extra height is split equally among
the multi-line boxes not fed by a link (`share`); each box stays centred on its anchor. A box
fed by a link does not grow. The text area inside a 68 row is 64 high (2 above and below).

- Source: `src/renderer/extensions/vueNodes/components/WidgetGrid.vue:5-8,130-153,230-231` (one grid,
  4 px row gap; a row whose widget expands is `auto` and the grid takes the free height, a row
  whose widget gave way to a link is a standalone slot), `widgets/registry/widgetRegistry.ts:393`
  (`shouldExpand`), `components/LGraphNode.vue:666-683` (the advanced button shows when a widget
  is advanced and `Comfy.Node.AlwaysShowAdvancedWidgets` is off, the default),
  `composables/useProcessedWidgets.test.ts:325-331` (advanced widgets hidden unless linked).
- Measured (`tests/fixtures/layout/multiline.vue.json`, recorded with
  `tools/oracle/geometry.py record --canvas multiline`, all nodes 400 wide): the text-encode node
  (one box below one slot row) at saved heights 60, 102, 200, 300 and 450, anchor at
  `y + 7 + body / 2` for the bodies 102 (60 and 102 both load at the natural 102), 200, 300, 450,
  text area 64, 64, 162, 262, 412 high; a trim node (box above a combo) at 60, 200, 400; a
  concatenate node (two boxes above a single-line text) at 60, 200, 400; a Flux text-encode node
  (slot row, two boxes, a number) at 60, 250, 450; an SDXL text-encode node (six number widgets,
  two of them advanced, above two boxes) at 100 and 500; the same with one advanced widget fed by
  a link; a text-encode node and a concatenate node with one box fed by a link; an empty-latent
  node with all three numbers fed by links (anchors `y + 36/60/84`); a KSampler with its seed fed
  by a link (seed `y + 96`, the value-control combo gone with it, steps `y + 122`); a collapsed
  copy of the first four kinds. Every one of the 95 widget-backed inputs in the three Vue fixtures
  (`boxes`, `links`, `multiline`) is within 0.01 px of the rule apart from the hidden ones below
  (`tests/test_envelope_vue_widgets.py`). The single-line text row is measured twice (the image
  saver's prefix, the concatenate delimiter).
- Measured, hidden widgets: an advanced widget not fed by a link has no slot element; its anchor
  falls back to the widget's classic `y` + 10 (`slotCalculations.ts:261-271`), here `y + 82` and
  `y + 106` for the two crop widgets, which the rule does not give. A link to it shows the
  widget, so no link ends at a hidden anchor.
- Measured, collapsed: widget-backed inputs sit at `(x, y - 15)` (the fallback
  `NODE_TITLE_HEIGHT / 2`, `slotCalculations.ts:225-231`), the plain inputs at `y - 16`; cwc
  uses `y - 16` for both. Collapsed VAE Decode's second input at `x + 13.38` (section 5) is still
  not investigated.
- Not measured: a node that has both a text box and an image preview or another expanding
  widget (how the free height is split), a node with an error footer (the checkpoint loader with
  a model the server lacks is 24 taller than its rows; with a text box below it the share would be
  12 too large), a collapsed node whose widget input is fed by a link, widths other than 400, and
  a node with no slot row at all (a plain note; it has no inputs).

## 6. Native reroutes

Frontend 1.59.1, both renderers. Measurements come from `tools/oracle/reroutes.py record`, which loads the
synthetic canvases in `tests/fixtures/layout/reroutes.canvases.json` (core nodes, reroutes placed by hand) in
four runs and writes `tests/fixtures/layout/reroutes.<run>.json`: `classic`, `vue`, `classic_snap` (snap to
grid on) and `classic_offset80` (reroute spline offset setting 80, canvases `main` and `short` only). Per
canvas the record holds `serialize()` right after the load, the live reroutes and links, a serialize, load,
serialize round trip, every `drawLink` call of one frame (the control points the canvas really used), the
predicted segments tested with `isPointInStroke` against the stroked `Path2D`, and the arcs drawn at each
reroute. `tests/test_layout_reroutes.py` restates the rules below against these files. Legacy `Reroute`
nodes are out of scope.

### 6.1 Stored form

Rule: a native reroute is one entry of `extra.reroutes` with the keys `id`, `pos` (`[x, y]`), `parentId`
(omitted for the first reroute of a chain, counted from the output), `linkIds` (sorted ascending) and
`floating` (only `{slotType}` on floating reroutes, otherwise omitted). Which link passes through which
reroute is stored only on the link: `extra.linkExtensions` is a list of `{id, parentId}`, one per link that
has reroutes, where `parentId` is the last reroute of the link's chain (the one nearest the input). The chain
is read upstream through the reroutes' own `parentId`. `linkIds` is written on save and ignored on load: it
is derived from the links' chains and never stored. `serialize()` writes the 0.4 form (`version` 0.4, `links`
as arrays, `extra.reroutes`, `extra.linkExtensions`); the 1.0 schema (`LGraph.asSerialisable`, `version` 1,
top-level `reroutes`, link objects carrying `parentId`) loads and comes back as 0.4. `serialize()` also
adds `extra.ds` (the live viewport, `{offset, scale}`), `id` (a new UUID) and `revision`; none is topology.

- Source: `src/lib/litegraph/src/Reroute.ts:696-706` (`asSerialisable`), `:221-227` (`linkIds` derived),
  `src/stores/rerouteStore.ts:57-89` (membership index built from the links' chains); `LGraph.ts:2952-3004`
  (`serialize`: `:2967-2972` writes `linkExtensions` only when reroutes exist, `:2988` `extra.reroutes`),
  `:3162-3179` (0.4 load: links, then `linkExtensions` onto the links, then `extra.reroutes`), `:3221-3234`
  (1.0 load), `:3085-3090` (a load deletes `extra.linkExtensions` and `extra.linkPresentation` from the kept
  `extra`), `:2025-2045` (`setReroute` reads `id`, `parentId`, `pos`, `floating`; `linkIds` is not read),
  `src/lib/litegraph/src/types/serialisation.ts:220-226`.
- Measured: `main` (seven reroutes: a chain of three, a bus, a single) comes back with the same `links`,
  `extra.reroutes` and `extra.linkExtensions` as written, in all runs. Every canvas is a fixed point of
  serialize, load, serialize (`extra.ds` aside) in all four runs. A load adds `extra.ds`, `id` and `revision`
  and nothing in the topology. `schema_v1` loads and serializes as 0.4.

### 6.2 What a load keeps

Rule, in load order (`LGraph.configure`): links first; then `linkExtensions` give `parentId` to the links
that exist; then each `reroutes` entry configures a reroute; then nodes; then floating links; then every
reroute that no link or floating link passes through is dropped. Consequences, all measured:

- `linkIds` that disagree with `linkExtensions` are ignored (`linkids_disagree`: the written `[2]` and `[]`
  came back as `[1]` and `[2]`, from the extensions).
- A canvas with `linkIds` but no `linkExtensions` loses every reroute (`linkids_only`: no reroutes, links
  without `parentId`). A reroute with no link through it is dropped, whatever its `linkIds` say (`orphan`).
- A `linkExtensions` entry naming a link that does not exist is dropped (`extension_link_missing`). One naming
  a reroute that does not exist stays on the link and is written back (`extension_reroute_missing`:
  `{id: 2, parentId: 77}` survives load and serialize); the link draws as a plain spline.
- A reroute whose `parentId` names no reroute keeps it until the first draw, which clears it silently
  (`parent_missing`: serialize right after the load writes `parentId: 99`; after one draw the live `parentId`
  is undefined). `Reroute.getReroutes` does the clearing (`Reroute.ts:334-351`).
- A parent cycle is refused at the assignment that would close it, so the outcome depends on entry order
  (`cycle`: entry 1 `parentId 2` kept, entry 2 `parentId 1` refused; link 1 then has the chain `[2, 1]`).
  `Reroute.ts:97-120`.
- Two entries with one id merge: the later `pos` and `parentId` win (`duplicate_id`). A missing `pos` becomes
  `[0, 0]` (`pos_missing`). A missing or empty `linkExtensions` is not an error.
- A bus tree is kept as written: one output, a shared root (`4`), branches (`5`, `6`), a link ending at the
  root itself (`main`, links 2 to 4). A chain of three reroutes is kept (`main`, link 1).
- One reroute shared by links of different outputs is kept on load (`cross_output`) but draws wrongly, see 6.3.
- Nothing throws and nothing is logged in the cases above (`console_errors` and the page console are empty
  in every record).

### 6.3 How a link through reroutes is drawn

Rule: per link, the segments are output anchor to reroute 1, reroute j to reroute j+1, last reroute to input
anchor, each a cubic Bezier in spline mode, on the same canvas in both renderers (the anchors differ as in
section 5; identical anchors give identical control points). Let `cap` be `LiteGraph.Reroute.maxSplineOffset`,
the setting `LiteGraph.Reroute.SplineOffset`, **default 20** (range 0 to 400; a user setting, so the router
must not rely on it). For a reroute `R` with upstream point `U` (the previous reroute, or the output anchor):

- Direction of `R`: `o = atan2(R - U)`; `m` = the arithmetic mean of `atan2(N - R)` over every link and
  floating link through `R`, where `N` is the next reroute of that link after `R`, or its input anchor;
  `diff = (o - m) / 2`, plus `pi` when `|diff| > pi/2`; `phi = o - diff`; `(cos, sin) = (cos phi, sin phi)`.
- A segment ending at `R` has the end control point `R - e * (cos, sin)`, `e = min(cap, 0.25 * |R - U|)`. A
  segment leaving `R` has the start control point `R + f * (cos, sin)`, `f = min(cap, 0.25 * |R - N|)`, with
  `N` the next point of the link being drawn.
- First segment (output anchor `a`, reroute 1 at `b`): start control `a + (0.25 |b - a|, 0)`. Last segment
  (last reroute `a`, input anchor `b`): end control `b - (0.25 |b - a|, 0)`. So the section 5 factor
  `d = 0.25 |p3 - p0|` applies to the first and last segment only, and is not capped by `cap`.
- A reroute segment is drawn once, by the first link that reaches it in draw order; later links through the
  same reroute skip it (`main`: 12 `drawLink` calls for 5 links and 7 reroutes, that is 7 reroute segments
  plus 5 final segments). The segment starts at that first link's start point, so a reroute shared by links of
  different outputs shows only the first output's leg (`cross_output`: 3 calls; the second output's leg to
  the shared reroute is never drawn). A reroute with several downstream links has one direction, the mean of
  all of them (`main` reroute 4, three links).
- The drawn reroute is a circle of radius 10 (`Reroute.radius`) with an inner circle of radius 8 (`0.8 *
  radius`); the record shows arcs of 10 and 8 at every reroute centre.
- Source: `LGraphCanvas.ts:6556-6707` (`_renderAllLinkSegments`; `:6603-6604` draw once; `:6614-6616` upstream
  point; `:6640-6651` start control; `:6658-6672` last segment; `:6297-6329` links drawn per input, node by
  node); `Reroute.ts:505-571` (`calculateAngle`), `:66-68` (radius 10, cap field), `:579-620` (`draw`);
  `litegraphLinkAdapter.ts:235-283` (control point cases), `:294-299` (the stroked path of a segment is kept on
  the reroute that ends it, or on the link for the last one); `coreSettings.ts:1043-1052`,
  `useLitegraphSettings.ts:157-160` (the setting).
- Measured (`main`, `short`, `groups`, `parent_missing`, `extension_reroute_missing`, `cycle`, all runs): every
  segment the rule predicts equals a recorded `drawLink` call (start, end and both control points, tolerance
  1e-3 on controls); 14 of 14 in `main`. `short` (every segment under 80 px, so `0.25 d < cap`) and
  `classic_offset80` (cap 80) confirm the `min`. `isPointInStroke` with a 3 px stroke: `main` 294/294 samples
  (classic), 292/294 (Vue), 293/294 (snap); `short` 147/147. The missed samples belong to segments whose
  control points match the calls, so a stroke-edge effect is the likely cause (unverified).
- Not measured: link modes other than spline, slot `dir` overrides, the hidden-link badge mode, reroutes
  inside subgraphs.

### 6.4 Reroutes and groups

Rule: a reroute is a member of a group when its `pos` lies in the group's `bounding`, half open
(`x <= px < x + w`, `y <= py < y + h`), the title band included. Membership is not saved; it is recomputed
when a group is dragged (section 1). Dragging a group moves every member reroute with it, each once, also
under a nested group. Nothing moves a reroute on load, snap to grid included; a reroute is snapped only when
it is dragged (`Reroute.snapToGrid`, `y` rounded with an offset of 14).

- Source: `LGraphGroup.ts:416-463` (`recomputeInsideNodes`, `:438-441` reroutes by `isPointInRect`), `:387-396`
  (`move`), `measure.ts:60-68`; `utils/collections.ts:20-56` (the drag set holds nested items once);
  `LGraphCanvas.ts:3936-3938` (items moved with `skipChildren`), `:4055` (snap on drag end); `Reroute.ts:475-486`;
  `LGraph.ts:3360-3367` (load snap touches nodes only).
- Measured (`groups`, all runs): Outer `[300,100,800,600]` holds the reroutes at x 300 (left edge), y 100 (top
  edge), y 120 (title band) and inside; not x 1100 (right edge), y 700 (bottom edge) or x 1200. Inner
  `[400,200,300,300]` holds the reroute on its left edge (x 400) and one inside, and Outer holds both too.
  A drag of Outer moved its six reroutes and the one node inside by exactly `(37, 53)`; a drag of Inner moved
  its two reroutes and the node once. With snap on, `main` reroutes such as `[453.7, 447.3]` came back
  unchanged (`classic_snap`).

### 6.5 Floating reroutes

Rule: a reroute is floating when it ends a chain that has a floating link: an entry of `floatingLinks` (top
level, also in 0.4 canvases) with one unassigned end (`origin_id` or `target_id` -1) whose `parentId` names
the reroute; the reroute then carries `floating: {slotType}` (`output`: the output side is connected and the
chain dangles; `input`: the input side is connected). Floating links count as links passing through a
reroute, so a floating reroute with a floating link survives the unused-reroute drop and one without is
dropped. Floating links draw at a third of the alpha, the dangling end with a `[0, 0]` control offset.

- Source: `LLink.ts:525-539` (`toFloating`), `:547-589` (`disconnect` keeping reroutes), `Reroute.ts:126-133`,
  `:197-200` (`totalLinks`), `LGraph.ts:3371-3391` (floating links load), `:3395-3400` (unused dropped),
  `LGraphCanvas.ts:6443-6511` (drawing), `src/types/linkTopology.ts:28-33`.
- Measured (`floating`, classic and Vue): both floating links and their reroutes came back (`floatingLinks`
  ids 3 and 4, `floating` markers kept, `linkIds: []`); the flagged reroute with no link was dropped; both
  floating segments were drawn (`disabled` true). The router has no use for floating links and should not
  write them.

### 6.6 Canonical write form for the router

Write exactly this and a load followed by `serialize()` returns it unchanged (`main`, `short`, `groups`):

- `links` in the 0.4 array form; `extra.reroutes` as a list of `{id, pos, linkIds}` plus `parentId` when the
  reroute is not first in its chain; `extra.linkExtensions` as `{id, parentId}` for every link that has
  reroutes, `parentId` the reroute nearest the input.
- `linkIds` sorted ascending, equal to the links whose chain passes through the reroute (ignored on load;
  written so the file equals what `serialize()` writes).
- Reroute ids unique and positive; `parentId` naming an existing reroute and never closing a cycle; `pos`
  always present; no reroute without a link; no `floating`, no `floatingLinks`.
- Reroutes are shared only by links of the same output (a bus tree: a shared root, branches, a link may end
  at an inner reroute); never one reroute for links of different outputs.
- No `extra.ds`: the frontend adds it. Plan segments for `cap = 20` but treat it as a user setting: control
  offsets are `min(cap, 0.25 d)`.
- `extra.reroutes` and `linkExtensions` came back in the order written in every record.
