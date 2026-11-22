# Architecture and module contracts

This is the contract every module is written against. When code and this file disagree,
fix one of them in the same change.

## Pipeline

```
authoring JSON -> normalize -> instantiate --+
                                             +-> annotate -> layout -> route -> notes -> verify -> emit
UI JSON / PNG --------------> ingest --------+
```

- One internal representation: `cwc.canvas.Canvas`, a view over a 0.4 workflow document.
  The raw document is the source of truth; views read typed values and write only through
  named mutators. Unknown keys are never touched.
- `instantiate` runs only for authoring input. After it, both inputs follow the same path.
- `verify` runs before `emit`. When it fails, only `<name>.report.json` is written.

## Exit codes

`cwc.report.ExitCode`, the same for every command:

| Code | Name | Meaning |
|---|---|---|
| 0 | `OK` | done |
| 1 | `CHECK_FAILED` | verification ran and raised a blocking finding; only the report is written. `cwc node --search` found no type (like `grep`) |
| 2 | `USAGE` | bad command line |
| 3 | `INPUT_ERROR` | the input is unreadable, malformed or fails authoring checks; `cwc node TYPE` names no known type |
| 4 | `NODEDEFS_MISSING` | node definitions are unavailable, unreadable or malformed (D1) |
| 5 | `WRITE_FAILED` | an output could not be written; the run's artefacts were removed where possible, so the folder never mixes two runs |
| 70 | `INTERNAL` | a bug in cwc: `cwc: internal error: <type>: <message>` on one stderr line, nothing written |

## Shared types

| Module | Holds |
|---|---|
| `cwc.canvas.model` | `Canvas`, `Node`, `Link`, `Group`, `NodeMode`, `id_order` (the one sort key for node ids), `is_ascii_decimal` (the one test for numeric id and slot strings: `str.isdigit` also accepts `"²"`, which `int()` refuses) |
| `cwc.canvas.geometry` | `Rect`, `GRID = 20`, `TITLE_HEIGHT = 30`, snapping |
| `cwc.nodedefs.types` | `NodeDef`, `InputSpec`, `WidgetSpec`, `SlotSpec`, `OutputSpec`, `Tier`, `WidgetKind`, `Behavior` |
| `cwc.prompt.model` | `Prompt`, link test, `__value__` wrapping, JS-number equality, `prompt_diff` |
| `cwc.normalize.types` | `Authoring`, `NodeIntent`, `WorkflowInfo`, `Role` |
| `cwc.report` | `Code` (every finding code), `Finding`, `Report`, `ExitCode`, `CwcError` |
| `cwc.resources` | `read_bytes`, `read_json` for files in `src/cwc/data/` |

## Module contracts

Each module is one folder under `src/cwc/`. Public functions are listed here; anything
else is private to the module.

| Module | Public functions | Raises / reports |
|---|---|---|
| `ingest` | `read_file(path, what) -> bytes` (size check, unreadable file is I1); `load_authoring(data: bytes) -> dict`; `load_canvas(data: bytes) -> Canvas` (UI JSON or PNG); `parse_png_chunks(data: bytes) -> list[Chunk]` (strict: signature, IHDR first, CRCs, IEND); `read_png_text(data: bytes) -> dict[str, str]` (the text the frontend's `getFromPngBuffer` reads: `tEXt`, `comf`, `iTXt`, also after `IEND`; divergences in the module docstring) | `CwcError(INPUT_ERROR, [I1..I4])` |
| `nodedefs` | `registry.NodeDefs.load(object_info=None, truth=None, templates=None, include_core=True) -> NodeDefs`; `NodeDefs.get(type, ver=None) -> NodeDef \| None` (`ver` is a canvas node's `properties.ver`: a template recorded for that package version wins, otherwise the type's default definition),  `.tier_of(type) -> Tier \| None`, `.closest(type, n=3) -> list[str]`, `.search(keyword) -> list[NodeDef]`, `in`, `iter`, `len`; `registry.read_source(file_or_url) -> object_info document`; `derive.from_object_info(type, info) -> NodeDef`; `derive.widget_layout(nodedef, values=None) -> tuple[WidgetSpec, ...]`; `derive.slot_layout(nodedef, values=None, linked=()) -> tuple[SlotSpec, ...]` (the only source of input slot order; `linked` grows Autogrow groups); `sizing.node_size(nodedef, values=None, *, slots=None) -> (w, h)` (classic size: truth size when the layout is the fresh one, else the larger of truth and formula); `SlotSpec.match` and `OutputSpec.match` carry match-type template ids; `derive.match_widget(nodedef, name) -> WidgetSpec`; `NodeDef.size` (classic size after creation, from truth) and `NodeDef.behavior` (`Behavior` of a builtin node) are the only places these facts live; `truth.parse_truth(doc)`, `truth.merge(derived, truth_node) -> NodeDef`; `templates.parse_templates(doc) -> dict[type, tuple[Template, ...]]` (every template of a type, curated before learned, then file order; the first is the default), `templates.by_version(options) -> dict[ver, Template]` (the first template for each package version), `templates.apply(template, derived \| None) -> NodeDef`; `learn.Learner(defs).add(sample)` / `.document() -> dict` (the templates document; the same samples in any order give the same bytes), `learn.align(values, inputs, slots=(), base=None) -> tuple[str \| None, ...]`, `learn.read_sample(path, skipped) -> Sample \| None`, `learn.iter_samples(paths, skipped) -> Iterator[Sample]`, `learn.pair_path(path) -> Path \| None`, `learn.command(args, out, err) -> ExitCode` (`cwc learn`), `learn.NO_TEMPLATES` (the empty table learning starts from); method and measured results: `docs/m1-templates.md` | `CwcError(NODEDEFS_MISSING, ...)` when a source cannot be read or is malformed; `derive`, `truth`, `templates` raise `ValueError`, which the registry converts, and so is any other failure of `derive` on a malformed entry (D1 names the type); a source holding `NaN` or `Infinity` is refused as a whole (D1) |
| `normalize` | `normalize(doc: dict, defs: NodeDefs, *, allow_unknown: bool, portable: bool = False) -> tuple[Authoring, list[Finding]]` (`portable=True` suppresses file-picker combo E5 warnings while keeping enum/range checks strict; `_meta.ui_collapsed` folds boilerplate nodes inside named groups) | `CwcError(INPUT_ERROR, [E1..E7])` on any blocking authoring finding; warnings returned |
| `prompt` | `from_canvas.canvas_to_prompt(canvas, defs, *, widget_layout=static_layout, named_restore=False) -> Prompt` (port of the frontend's graphToPrompt, bypass and mute included); `bypass.factory_prompt(full, defs, optional_ids) -> Prompt` | `bypass.ResolveError` (cycle, missing slot); `ValueError` (subgraph instances, unknown types the bypass needs) |
| `instantiate` | `instantiate(authoring: Authoring) -> Canvas` (ports, links, widget values, titles, modes, `order` as the frontend recomputes it, sizes from `nodedefs.sizing`; every `pos` is `[0, 0]`; shape per `tests/fixtures/instantiate/`) | none |
| `annotate` | `annotate(canvas, defs, authoring, *, regroup=False) -> Annotations` (the plan, types in `cwc.annotate.types`: groups, zones, nesting, member order, node notes, no coordinates; `authoring` is `None` in tidy); `apply_notes(canvas, annotations) -> None` (compile only, before layout: `flags.collapsed`, node colour, cockpit title) | `CwcError(INPUT_ERROR, [G4...])` in tidy without `regroup` when two groups overlap without nesting and share a node centre; `Annotations` raises `ValueError` when built with a broken invariant |
| `annotate.palette` | `zone_class(zone) -> str`, `group_colour(palette, colour_class) -> str`, `node_colours(node_class) -> NodeColours`, `palette_names() -> frozenset[str]`, `parse_palettes(doc) -> Palettes` (`src/cwc/data/palettes.json`) | `ValueError` on a malformed document |
| `layout` | `layout(canvas, annotations, preset, defs, *, widen=None) -> LayoutResult` (`defs` are the node definitions the checks get, for the box model; types in `cwc.layout.types`; writes `pos`, `size` (width only grows; height grows only for a preview node in a preset's preview slot) and `groups`, nothing else; `widen` is the M3 second round; a preset that uses a feature placement does not read yet (an axis other than x, `under_guide`, `tray`, a corridor other than the bus, the skybridge and the spine) is refused, not laid out without it) | `NotImplementedError` for `widen`, kept groups and such a preset |
| `layout.presets` | `load_presets() -> dict[str, Preset]`, `get_preset(name) -> Preset`, `parse_presets(doc) -> dict[str, Preset]` (`src/cwc/data/presets.json`, document shape and rules in the module docstring), `DEFAULT = "bento"` | `ValueError` on a malformed document; `KeyError` for an unknown name |
| `canvas.envelope` | `box_facts(node, nodedef) -> BoxFacts`, `classic_box`, `vue_box`, `envelope` (their union), `expanded_centre`, `live_size`, `shows_preview(nodedef)` (an output node that takes images, or a node with an upload button), `slot_anchors(node, facts, renderer)`, `title_text`, `title_width`; all take `(node, facts)` unless shown | none |
| `canvas.textwidth` | `text_width(text, size) -> float` (upper-bound width from the measured table `src/cwc/data/char_widths.json`: exact at 14 px and 12 px, linear scaling otherwise), `roles() -> Mapping[str, FontTable]`; a character's advance is, in order, measured ASCII, a `listed` measured wide glyph or joiner, the `emoji` advance in U+1F000-U+1FAFF, else the one-em `default`; non-ASCII numbers hold for the recording machine's fonts | `FileNotFoundError` if the table is missing |
| `canvas.links` | `spline(start, end) -> Spline`, `link_spline(link, nodes, facts, renderer, anchors=None) -> Spline \| None` (`anchors`: an `Anchors` cache of `slot_anchors` for one renderer, filled as nodes come up), `canvas_splines(canvas, facts, renderer) -> dict[int, Spline]`, `crosses(path, rect) -> bool` | none |
| `canvas.reroutes` | `link_paths(canvas, facts, renderer, cap=REROUTE_CAP) -> dict[int, LinkPath]` (every drawable link as the frontend draws it through its reroutes), `paths_between(canvas, ends, cap=REROUTE_CAP, table=None) -> dict[int, LinkPath]` (`ends`: link id to output and input anchor; `table`: `Reroutes.read(canvas)` when the caller has it), `Reroutes.read(canvas)`, `segment_controls(a, b, leaving, entering, cap) -> (Point, Point)` (the control points of one segment; a direction is `None` at an anchor), `own_directions(points, first=1) -> list[Direction]` (the direction of each reroute of a link that shares none), `REROUTE_CAP` | none |
| `canvas.box_index` | `BoxIndex(boxes)` over `(key, Rect)` pairs in a fixed order; `.crossed(path, exclude=frozenset()) -> tuple[key, ...]`: the boxes whose interior the polyline crosses, in that order, the same answer as `crosses` box by box (buckets and per-segment bounds only skip boxes that cannot be hit); the G2 check uses it | none |
| `route` | `route(canvas, layout_result, defs) -> RouteResult` (clears and writes `extra.reroutes`, `extra.linkExtensions` in the canonical form of `docs/m2-frontend-facts.md` 6.6; `RouteResult`: `rerouted`, `dummy`, `unresolved`, `channels`, `shared_edges`, `shortfalls`; channel ids `bus:<i>`, `h:<above>\|<below>`, `v:<left>\|<right>`, each side a node key, `edge` or `open`; a channel's tracks are the most outputs overlapping at one point along it). Every routed link is checked at reroute cap 20 and unlimited in both renderers; links of one output share reroutes as a bus tree, never across outputs | none |
| `notes` | `summary(authoring, plan, laid, canvas, defs) -> GuideSummary \| None`, `guide_markdown(info) -> str`, `place_guide(canvas, authoring, plan, laid, preset, defs) -> tuple[str \| None, LayoutResult]` (emits a `Behavior.NOTE` Quick Guide card in the reserved left guide lane, `C5`/`G8` verified, when title/notes exist) | none |
| `verify` | `verify_compile(canvas, authoring, defs) -> Report` (S1-S6, N1-N3, B1-B3, Q1-Q2; a finding about a node whose author id differs from its canvas id also names the author id, in the message and in `data["author_ids"]`); `verify_canvas(canvas, defs, *, mode) -> Report` (S1-S6, N1-N6 with the severities of `mode`); `verify_beautify(before, after, ...) -> Report` (M3); `geometry.check_geometry(canvas, defs, *, mode, membership=None, before=None, regroup=False, preset=None, cockpit=()) -> Report` (G1-G10, C1-C6 on the written canvas; `preset` gives C3 its axis and G8 its numbering, `cockpit` is the indices in `canvas.groups` of the cockpit's groups, as `LayoutResult.cockpit` gives them (one index, or one per part when C1 split the cockpit), for C4 and for what G5 and C3 leave out; without them C3 and C4 are skipped and listed in `metrics["skipped"]`, as are C5 without a guide node and C6 in beautify without `regroup`) | findings only |
| `render` | `render_svg(canvas, defs, *, show_values: bool = False) -> str` (`show_values=True` prints unlinked widget values and dashed `IMAGE PREVIEW` / `INPUT IMAGE` viewports; `Behavior.NOTE` cards render wrapped Markdown lines) | none |
| `emit` | `json_bytes(obj, *, ascii=False) -> bytes` (canonical); `write_atomic(path, data)`; `png_chunk(kind: bytes, data: bytes) -> bytes`; `embed_png(png, texts) -> bytes` (strips old workflow chunks first; `ValueError` for a keyword or text PNG cannot hold) | `CwcError(INPUT_ERROR, [I1])` on a bad PNG |
| `pipeline` | `compile_document(doc, defs, *, allow_unknown=False, preset=None) -> Compiled` (`authoring`, `canvas`, `report`, `layout`, `routes`): normalize, instantiate, annotate, apply_notes, layout, route, then `verify_compile` and `check_geometry` in compile mode merged into one report (`metrics["preset"]`, `metrics["geometry"]`, `metrics["route"]`, `metrics["authoring_keys"]` from `effects.py`: the nodes each `_meta` key changed or did not, and an E7 warning for a key that another key of the same node overrides; a link the router could not resolve is a G2 error with `data["unresolved"]`); the preset is `preset` (the command line's `--preset`), else the document's `_workflow.layout.preset`, else `bento`; the name `auto` picks by `choose_auto(annotations.counts)`, first rule that holds: at most 16 nodes and no optional module `tower-preview`, three or more parallel control branches `swimlanes`, 60 nodes or more `cockpit-split`, else `bento` (`metrics["preset_auto"]` records the rule and the counts; a choice layout refuses is an error, never a silent fall back); `PlanCounts.control_branches` counts top-level groups only, optional control branches included, and a chain of control steps merged into one group counts once; `cwc compile` writes `<name>.ui.json`, `<name>.api.json`, `--all-enabled` `<name>.full.api.json` and always `<name>.report.json`, and removes the other artefacts of the same name, so a failed run leaves only its report | `CwcError(INPUT_ERROR)` for E1-E7, an unknown preset name and a preset layout refuses (E7); verification and geometry failures are report errors |

## M2 contract

How compile and tidy place nodes and groups, and how the checks read the result.

- One engine. Compile and tidy run the same `annotate` and `layout`; they differ only in where
  the groups come from.
- `annotate` returns a plan without coordinates: which groups exist, how they nest, which
  zone of the preset each one goes to, which nodes each holds and in what order, and how each
  node should look. `layout` turns the plan into positions and numbers the groups ("01",
  "02a", "Step 3") from the final order, so numbering stays continuous when modules come and go.
- One box model. Layout and every check use the envelope of a node, the union of its classic
  and Vue boxes (`cwc.canvas.envelope`), and for a collapsed node also the centre of its
  expanded box.
- One link model. A link without reroutes is the frontend's default spline; only its anchors
  depend on the renderer (`cwc.canvas.links`). A link through native reroutes is drawn segment by
  segment as frontend 1.59.1 draws it (`cwc.canvas.reroutes.link_paths`, facts in
  `docs/m2-frontend-facts.md` section 6): G2, G10 and render read that path, at the default reroute
  spline cap of 20 (a user setting); G5 and layout's direction count judge a link by its end
  anchors (`canvas_splines`). A segment is keyed by the reroute it ends at, or by its link for the
  last one: a reader that draws skips keys already drawn, a reader that measures takes the whole
  path. A reroute's direction takes its upstream point from the first link through it in canvas
  link order.
- G2 runs once per renderer, each link against that renderer's boxes. G3 holds only when a
  node's group is the target under both renderers' box centres. G3 without a target (tidy)
  compares each node's ancestor groups before and after at the classic box centre, the Vue box
  centre and, for a collapsed node, the expanded centre; a node whose ancestors differ at any of
  them is one finding naming those centres.
- Under Vue, a widget-backed input's anchor depends on the node's live height and on which
  widget inputs are linked: multi-line text boxes share the height above the natural body
  equally, and a linked widget takes a 24 px row (`docs/m2-frontend-facts.md` 5.1). The node's
  envelope does not depend on either.
- G8's group part, run in compile and in tidy with `--regroup`, is an error when the canvas has
  nodes and no group title is the first interactive group's (`01 ` or `01a `, or `Step 1`, by the
  preset's numbering).
- `Reroutes.read` keeps only the reroutes some existing link or floating link passes through
  (the chain of the link's `linkExtensions` entry or of the floating link's `parentId`), and
  drops the `linkExtensions` entries naming a missing link, as a frontend load does. It never
  writes the canvas.
- Annotate rule 3: an intermediate preview (an output node a later sampler follows) joins its
  sampler's group together with every free, non-plumbing node on the paths from that sampler to
  it, also when such a node feeds a final output; a node on those paths that reaches a later
  sampler stays out of the group. An intermediate preview the author put in a named group
  stays in that group, and the nodes on its paths are handled the same way; free previews are
  handled first, so a path shared with an author-grouped preview ends as it would without it.
  An author-grouped output that rule 3 does not keep beside a sampler is left in its group as
  a final output: it has no sampler upstream, no later sampler follows its sampler, or that
  sampler's group is not known at rule 3 (it sits in an optional module). Its own zone is the output zone, whatever the
  classification without optional nodes and intents gives it. A side branch feeding such a group or path
  (a free, non-plumbing node with a path into the preview or its chain that passes no sampler,
  and that is not downstream of the preview's sampler) joins the group of its own nearest
  upstream sampler; a node with no upstream sampler, or whose sampler's group is not known at
  rule 3, is left to the later rules.
- Cockpit titles: in compile, every node whose group lies in the cockpit zone (the cockpit
  group, and an explicit group whose members' majority zone is the cockpit) is drawn under a
  title that is not its type name and that no other such node shares, unless its author wrote a
  title. The base title is `Positive prompt` or `Negative prompt` for a conditioning node of one
  polarity, else the definition's display name, else (no display name, or one equal to the
  type) the type split into words at case and digit boundaries. A repeated generated title, or
  one equal to an authored title, gives way: a non-prompt node adds the sorted names of the
  inputs it feeds in parentheses, and what still repeats is numbered in display order. A title
  is written only when it differs from what the frontend draws anyway. Authored titles are
  never changed; two alike are reported by C4.
- Cockpit frames: every top-level group of the cockpit zone is a cockpit frame, the cockpit
  group and any explicit group whose members' majority zone is the cockpit. G5 and C3 leave
  out the links into every frame when the frames' groups are named: every part of each frame
  (a cockpit that C1 split has several), and any group nested in them. Layout's wrap search,
  the wrap's crossing count and the sub-column rule leave out the same links, so the counts
  agree. C4 reads all the frames as one panel. The other frames follow the cockpit group in
  placement order, keeping their own order, so the cockpit slot stacks them under it; nothing
  else moves. Without a cockpit group the frames keep their ranked order. The main
  prompts are lifted into the cockpit, left of the model stack that feeds them, so in a plain
  text-to-image graph about a fifth of the links run backward by design; counting them would
  make the 5 % limit unreachable. This is a departure from the original definition of G5,
  which exempts feedback links only.
- C3 reads advance in reading order: a downstream group passes when it starts at or after its
  upstream group along the preset axis, or lies wholly beyond it across the axis (below it for
  `x`). Without the second clause a wrapped band or a docked lower band could never pass.
  The first clause also passes a downstream group wholly above its upstream group: a docked
  lower band feeds the row above it that way (a control branch under the sampler it feeds).
  Checks cannot see where a section starts, so band order across a wrap is the layout's to keep.
- Zero-hole is judged by column. Within a section (a main row and the lower bands under it; a
  wrap starts a new section below), the projections of all groups on the main axis leave no gap
  over 80 px. A main-row gap wider than the slot gap holds a bridge, a lower-band group fed from
  and feeding the main row, and is the least width that keeps the bridge's links left to right.
  A per-band rule would leave a bridge no room without a backward link.
- Inside a group, layers follow reachability through the whole graph, not only the group's own
  links (ordering only, no dummy slots), so a model chain broken by an optional group keeps one row.
- After a wrap, a lower-band group with producers (main-row groups or other lower-band groups,
  the latter placed first) goes to the section of its latest producer, so it is never laid out
  before a group that feeds it; a lower-band group with no producer goes to the first section
  it links to. Every lower-band section is a max or min of main-row sections fixed by the
  links, so a trial cut moves no lower-band group into or out of a section already closed. A
  consumer of a lower-band group can sit in an earlier section only where the main row already
  runs backward: a main-row group feeding it, directly or through lower-band groups, sits
  after that consumer.
- A lower-band group docks under its own column. A collision takes the nearest free spot in the same
  layer, left or right, then moves down within the columns it spans; taller groups go first at the
  same dock. A placed group keeps the direction its dock gave its links and never widens the section.
  When a dock falls left of the section start, the main row moves right until it fits, and the moved
  column is filled by that group. When a bridge's two docks tie on backward links, it docks left of
  its consumer so C3 passes in reading order. A lower-band group with no producer ends left of
  each of its consumers: at the left edge of its main-row consumers and `GAP` left of the dock of
  its lower-band consumers, whichever lies further left; with no consumer placed it docks at the
  section start.
- A group is cut along the main axis into consecutive parts wherever a path leaves it: when a
  member reaches another along a path through at least one node outside the group, and the
  other does not reach it back, the second is in a later part than the first, whether or not
  another path joins them inside the group; a link inside the group stays within one part or
  runs forward. So no part reaches itself through a node outside it, and an outside group, an
  optional LoRA in the middle of the model chain for example, bridges two parts instead of
  closing a loop. Each such part is then cut again where it must be, by cut level. A steering
  member's cut level is the latest of its own zone's level and the cut levels of the members
  of the part that feed it, directly or through other members; a member that does not steer
  has no level of its own and passes on its feeders'. A steering member of the part's latest
  cut level is in the last sub-part. One of an earlier cut level moves there too when every
  link it has goes to a steering member of the part of the latest cut level or to a member
  that can move (an output node, with no link out, always can; a member that does not steer
  can move on the same condition, which decides only for the members feeding it: one with a
  link leaving the part holds back the earlier members that feed it); the steering members
  that cannot move, because a link of theirs leaves the part or feeds a member that cannot
  move, form one sub-part per cut level, earliest first, ahead of the last. A member that
  does not steer is placed by a rule of its own: it joins the earliest
  sub-part among the steering members it reaches within the part through members that do not
  steer; reaching none, the latest among the members feeding it directly; else the first. The
  cut level never falls along a link inside the part, so no link between two members of a
  group runs from a later part to an earlier one. Nothing is cut again when every member can
  move. Parts are numbered as split
  parts are (`02a`, `02b`), each is a written group, and G3 membership follows the parts.
- After the cut, layout ranks the placed groups, each part a group of its own, with
  `group_ranks` over the same links and execution order annotate uses, and places them in that
  order, plan order breaking ties. With no group cut the placed groups are the plan's top-level
  groups and the ranks are `GroupPlan.stage`. C3 does not read the ranks.
- An author's group (annotate rule 2) is a cockpit frame, and is not cut, when the majority
  zone of its members on their own, plumbing left out unless it is all the group holds, is the
  cockpit; a tie with the cockpit counts. Any other author's group takes the latest zone of
  its steering members in data-flow order: model, prompt, control, sampling and the generic
  stage (one level; the majority decides between the two, sampling on a tie),
  post-processing, output. The
  steering members are those whose own zone is not the cockpit, and of these those that are
  not plumbing; each step is skipped when it would leave none. A member's own zone is the one
  the classification without optional nodes and intents gives it, whether the author put it
  in the group or rule 3 added it (an intermediate preview kept beside its sampler, the decode
  feeding it, a side branch), and the output zone for an output rule 3 leaves as a final
  output. No member takes its own zone from its group: the group's zone follows from the own
  zones by this rule alone, and layout cuts and places the group by them. A node's own zone is
  usually not earlier than the zone of a node feeding it, but it can be: a first-pass decode
  whose image feeds a second pass's ControlNet is of the control zone though the sampler feeds
  it. The cut does not rely on that order: its second step parts a group's members by cut
  level, which never falls along a link inside the group.
- Each part of a cut group takes the latest zone of its own steering members, by the same
  rule; the part goes to that zone's slot (or a later one, by the rule on main-row slots
  below), its preview area included, and its title, number
  and colour stay the group's. A part of cockpit members only is a cockpit frame. So an
  authored group holding nodes from both ends of the data flow is drawn as parts, each where
  its members belong, and a group around the outputs of several stages is one box in the
  output slot.
- Main-row slots follow the data flow. A placed group of the main row (the first preset band),
  a part of a cut group included, goes to its zone's slot unless a main-row group strictly
  upstream of it has its own zone's slot later in the row; it then goes to the latest such
  slot. A group is strictly upstream of another when the links between top-level groups lead
  from it to the other, through groups of any band and never through a link into the cockpit,
  and do not lead back. Upstream groups count with their own zone's slots, not the slots they
  move to: strict upstream carries over, so the result is that of moving groups until nothing
  moves. In a repeated slot a moved group opens the instance of its own rank; in any other
  slot it follows that slot's stack rule. Cockpit frames do not move, lower-band groups keep
  their band, and two groups that reach each other do not move one another. The main row is
  laid out left to right in slot order, so without this a prompt group fed through a first
  pass's sampler (a combine after a tile ControlNet on the first pass's image) would sit left
  of the groups feeding it. Presets that hold every zone but the cockpit in one repeated slot
  (swimlanes, cockpit-split) have nothing to move.
- Repeat-slot instances (sampling, generic stages) are placed in data-flow order. The order is
  that of `group_ranks`: a group reached from another, and not reaching it back, ranks after
  it; groups in one loop are ranked as a unit from the groups upstream of the loop, and inside
  it follow their members' earliest place in the execution order. A repeated slot opens one
  instance per distinct rank of its placed groups, those the rule on main-row slots moved in
  included, in ascending order, so a part and the groups
  bridging it to the part before it take instances of their own in data-flow order.
- A repeated slot may name `attach` zones (control in swimlanes and cockpit-split). A group of
  an attach zone opens no instance of its own: it joins the instance of the lowest-ranked
  opening group downstream of it, and the last instance when it feeds none, so a control
  branch stands in the column of the stage it steers. When a repeated slot holds attach groups
  and no opening group, the attach groups open one instance together, at their lowest rank.
- Groups stacked in one column (a `stack y` slot, an instance of a repeated slot) form
  sub-columns: taken in placement order, a group goes one sub-column right of the rightmost
  earlier group of the stack that links to it, or that reaches it one way along links between top-level
  groups (through groups outside the stack too, never through a link into the cockpit), and of
  the part of its own group before it, and otherwise stays in the first; a link into the cockpit
  moves nothing. Sub-columns are top-aligned and one group gap apart, so a link between two
  groups of a stack still runs left to right, and a group outside the stack that joins two of
  them has an x between them to sit at. A stack with no such link or path is one column.
- A preset slot repeated along y is refused as not implemented: its instances would stack at one
  x, and a later stage's links would run back left.
- Annotate rule 1 forms one optional module per connected set of optional nodes, where a link
  joins two optional nodes only when they name the same `ui_group` (none counts as the same);
  linked optional nodes that name different groups are separate modules, each titled from its
  own group, and unlinked optional nodes that name the same group stay separate modules with
  the same title.
- A group keyed `group:<id>` (annotate rule 2, from the author's `ui_group`) is named by the
  author alone: its name is the cleaned `title` of `workflow.groups[<id>]` when it has one, else
  `<id>` turned into words (split at whitespace, underscores and hyphens; the leading words
  that are only ASCII digits are dropped, since layout numbers the groups; every other word
  keeps its digits, so `pass 1` and `pass 2` stay apart; the first character is capitalised),
  else the zone name. Nodes that later rules add to it (rule 3's
  preview and its decode) do not change the name. An optional module takes the author's name
  only when all its members name the same `ui_group`.
- Annotate rule 4 lifts a latent source into the cockpit's size slot when it feeds a sampler
  directly, or reaches a sampler's latent input along links that carry only a latent and
  through latent-only steps: nodes that are not samplers, whose every output is a latent and
  whose declared inputs are latents and values only. A path through pixels does not count.
- A route starts in the first grid column right of the output anchors that the clearance leaves
  free of the source box, and ends in the last such column left of the input anchors whose left
  neighbour is free too (a route enters its input heading right). Its first and last grid points
  are left out, when the run beside them has a flank, only if they lie on the grid line nearest
  their anchors' mean under the two renderers.
- A lower-band group fed by one sub-column of a main-row item and feeding a later sub-column of
  the same item bridges them: the item is cut into one item per sub-column, so the gap for the
  bridge opens between the sub-columns. An item with no such bridge stays whole.
- A preset with a skybridge corridor gets a strip one band gap high above the main row of every
  section, written to `LayoutResult.corridors` as `skybridge:<section>` with four tracks; the
  section rectangle leaves the strip out, so wrapping does not see it. The router does not use
  corridors yet (it searches the free grid of the whole canvas); they reserve the space.
- A preset with `cockpit_split` writes the cockpit group (no other frame) as two boxes when it
  has at least `min_nodes` (default 1) members and both sides are non-empty: the members whose
  cockpit slot is in the preset's core set first, the rest in a second box along `axis` (`y`
  stacked by default, or `x` side by side); with one side empty or fewer than `min_nodes`
  members it stays one box.
  `LayoutResult.cockpit` lists every box of every cockpit frame, ascending.
- A preset with a `preview` gives the slot of `preview.zone` (plus any `extra_zones`) room for
  large images: when a group of those zones holds an expanded node that shows a preview
  (`shows_preview`), the group is at least `share` of the first screen's width (880 at 0.55) by
  `min_height`, and every preview node grows uniformly to fill it (raising classic heights at
  least to Vue's preview body even when stacked previews already satisfy `min_height`). When
  `dock_steps` is true and a second band exists, dead-end intermediate preview groups fed by
  earlier stages dock in band 1 directly under the stages following their producer while the
  final output stays at the end of band 0. No non-preview node's height changes.
- A preset with a spine corridor leaves one band gap, not one group gap, between the band-0 slot
  the rule names (`after_slot`) and the slot after it. That gap, as high as the section with
  its lower bands, is written to `LayoutResult.corridors` as `spine:<section>` with four tracks,
  in each section that holds both sides. When band 1 sits entirely right of the spine, it tucks
  `BAND_GAP` below the right-of-spine main-row items it spans rather than waiting below a taller
  left sidebar. A wrap never cuts at the spine, so the cockpit and the
  first stage it steers share the first section; a section that could only be cut there stays
  whole with a G9 warning.
- A lower band may use the `00` guide's column, which the guide reserves in the main row only.
  Moving the first section's main row right never takes the first interactive group off what G8
  requires.
- One count of backward links (`cwc.canvas.direction`) serves G5 and layout. A section wraps
  when it is wider than `wrap_ratio` times its height, and only by a cut after which G5 stays
  within its limit; otherwise it stays one section with a G9 warning. Small graphs rarely wrap
  for this reason: measured with the limit lifted, wrapping put 6 of the 15 links of a
  two-stage graph backward. A cut is taken only when the part it moves is at least a quarter of
  the section's width (`WRAP_MIN_MOVED`): moving a short tail, an output group alone, saves
  little width and costs a link back across the canvas. Among the cuts left, layout prefers one
  whose head is no longer too wide, then the fewest links between sections, then the latest
  cut, and takes the first that keeps G5.
- The `00` guide is a reserved rectangle in column 0 until M3 writes a guide node; a check
  about the guide does not apply to a canvas without one.
- Checks read the written canvas only, never layout internals. Their G3 target is passed in:
  `LayoutResult.membership` for compile and tidy with `--regroup`, the input canvas for tidy
  without it.
- Tidy without `--regroup` keeps the canvas's groups. When two groups overlap without nesting
  and the centre of a node lies in both, no layout can keep them and clear G4, so tidy stops
  with `INPUT_ERROR` and one G4 finding per such pair, asking for `--regroup`.
- In a kept plan, a node in no group is placed in the slot of its own zone, without a group box.
- Tidy with `--regroup` infers groups as compile does, and reads optional from a node's bypass
  mode, since a canvas carries no authoring intents.
- Presets (`src/cwc/data/presets.json`) and palettes (`src/cwc/data/palettes.json`) are data. Adding a preset or
  a palette changes no code.

## Rules for all code

- Python 3.10+, standard library only. Playwright is used only in `tools/oracle/`.
- No node class name appears as a string literal in `src/cwc/` (`tools/lint_no_node_names.py`).
  Node structure, roles, presets and palettes live in `src/cwc/data/`.
- Deterministic output: no randomness, no timestamps, no host or environment data, every
  sort has a total order. Same input, same version and same data give identical bytes.
- Validate at the boundary (`ingest`, `normalize`, `nodedefs` loaders) and trust the shape
  inside. Make illegal states unrepresentable in the types where practical.
- Iterations are capped (barycentre sweeps 4, layout and corridor passes 2).
- Organise by feature: one module per folder, no layer folders, no god files, no pass-through
  wrappers, no duplicated logic across modules.
- Tests use `unittest` and run with `python3 -m unittest discover -s tests`. Fixtures are
  hand-written or derived from public sources; nothing private enters `tests/`.

## Data files

| File | Content |
|---|---|
| `src/cwc/data/core_nodedefs.json` | `{"format": 1, "comfyui": "<version>", "nodes": {type: object_info entry}, "withheld": [[type, input], ...]}` for core nodes only (`python_module` `nodes`, `comfy_extras.*`, `comfy_api_nodes.*`); descriptions, tooltips, search aliases, help, price badges and file lists removed. Key order inside an entry is kept (it is input order) |
| `src/cwc/data/core_truth.json` | Frontend truth for core nodes, format below, plus `"withheld": [[type, widget], ...]` |
| `src/cwc/data/frontend_builtins.json` | `{"format": 1, "nodes": {type: {...}}}` for frontend-only nodes (notes, legacy reroute, primitive) |
| `src/cwc/data/templates.json` | `{"format": 1, "templates": [{"type", "pack", "ver", "source": "learned"\|"curated", "widgets": [name \| null, ...], "evidence"}]}`; curated before learned, then file order. `cwc learn` writes the entries of one type newest version first: dotted numeric versions by value, then other versions (commit hashes) by instance count, then the entry without a version, so the first entry is the default for a node that saves no version |
| `src/cwc/data/presets.json`, `src/cwc/data/palettes.json`, `src/cwc/data/char_widths.json` | layout presets, palettes, text width table |

Both core files are built by `tools/build_core_data.py OBJECT_INFO TRUTH --out src/cwc/data/` from a
raw `/object_info` export and a raw truth snapshot; the same inputs give the same bytes.

`withheld` lists every string default (object_info `default`, truth widget `value`) longer than
64 characters. Its value is replaced by null: long upstream prose and code (shader sources,
prompt templates) stay out of this repository. Nested inputs are named `<combo>.<sub>`. The
registry treats a withheld default as unknown: `has_default` is false, and a widget whose truth
value is withheld falls back to the `/object_info` default when that one is known, else null.

### Definition tiers

`NodeDefs.load` gives each type one tier: `BUILTIN` for frontend-only nodes; `TRUTH` when a truth
entry and an `/object_info` entry exist (slot order, outputs, widget order and widget values from
truth; inputs, `required`, `has_default`, combo options and other metadata from `/object_info`;
widget kinds from derive by name, unknown names `EXTRA`); `TEMPLATES` when a template exists
(widget names from the template, placed on the `/object_info` entry when there is one); `DERIVED`
otherwise. A user `/object_info` source (`--nodedefs FILE|URL`, a URL is read from
`<url>/api/object_info`) replaces bundled core entries of the same type.

### Truth snapshot format

Produced by `tools/oracle` (and later `cwc snapshot --via-frontend`) by instantiating every
node type in a real frontend with `LiteGraph.createNode`.

```json
{
  "format": 1,
  "frontend_version": "1.59.1",
  "comfyui_version": "0.45.0",
  "nodes": {
    "<type>": {
      "inputs": [{"name": "model", "type": "MODEL", "widget": null, "shape": null}],
      "outputs": [{"name": "LATENT", "type": "LATENT", "is_list": false}],
      "widgets": [{"name": "seed", "type": "number", "value": 0, "to_prompt": true}],
      "size": {"classic": [315, 262], "vue": null}
    }
  }
}
```

- `inputs` / `outputs`: the node's slots in order; `widget` is the widget name for a
  widget-backed input slot.
- `widgets`: the widgets with `serialize !== false`, in order (exactly the
  `widgets_values` order); `to_prompt` is `options.serialize !== false`.
- `size.classic`: `node.size` right after creation; `size.vue` when measured.
