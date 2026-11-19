# Load probes, frontend 1.59.1 / ComfyUI 0.45.0 (classic renderer)

Run with `tests/oracle_runner.py batch`: each canvas loaded with `loadGraphData`, then
`app.graph.serialize()` read back. Inputs were `frontend_serialize.json` and variants of it.

- `localized_name` is rewritten on load: missing ones are added, wrong ones ("X" + name)
  are replaced. Inputs get the slot label (`image0` for `images.image0`, `multiplier` for
  `resize_type.multiplier`, `choose file to upload` for the upload slot); outputs get the name.
- `properties`: an empty object gets `{"Node name for S&R": type}` on load, except on
  SaveImage, which stays `{}`. A written value (also a wrong one, plus `cnr_id`, `ver`) is kept.
- `outputs[].links`: `[]` stays `[]`; `null` stays `null`. A new node writes `null`.
- `size`: a size smaller than the node's computed minimum is enlarged to it ([10, 10] became
  [210, 98] on CheckpointLoaderSimple, [210, 88] on CLIPTextEncode, [140, 46] on VAEDecode);
  a larger size is kept. BatchImagesNode with 3 slots (2 linked) grew from [140, 66] to
  [140, 86] on load; round 2 below shows this happens once, not on every load.
- `order` is recomputed on load: Kahn's algorithm, start nodes (no linked input) in node-list
  order, a FIFO queue, targets visited through each output's `links` in order. Checked on the
  capture graph, on the same graph with ids reversed, and with the node list reversed.
- Root: the frontend adds `id` (uuid) and `revision`; it keeps `last_node_id`, `last_link_id`.
- Autogrow: connecting the last slot of a group appends one empty slot right after the
  group's last slot, before the inputs that follow (TextEncodeMOSSImageEdit: `images.image_2`
  lands between `images.image_1` and `vae`). Linking only `images.image1` of BatchImagesNode
  leaves `image0` empty and adds `image2`. Linking image0..image2 live gives 4 slots, size
  [140, 86] before any load.
- Console during load: only 404s for `/api/view?...example.png` (missing input image) and a
  deprecation warning `output.links is deprecated`; both are noise for the round trip.

## Round 2 probes (instantiate fixes), frontend 1.59.1

- Match-type input slots: a slot takes the type that reaches the first other linked slot
  of its template; its own link does not count. ComfySwitchNode with only `on_true` linked
  loads as `on_false` IMAGE, `on_true` `*`; with only `on_false` linked, the reverse; both
  linked: both IMAGE; none: both `*`. BranchNode (Autogrow group of match-type slots) with
  two or more linked: every slot, the empty trailing one too, IMAGE; with one linked: that
  slot `*`, the next IMAGE. A lone slot of its template (EndLoop `output_value`, StartLoop
  `initial_iteration_value`) stays `*` when linked. ResizeImageMaskNode `input` stays
  `IMAGE,MASK` whether it gets an image or a mask. Output types follow the first linked slot.
- Autogrow height: loading adds one input row to a node with a linked slot in an Autogrow
  group of plain slots, once (a second and third load change nothing). All 26 core Autogrow
  nodes that could be fed (built live with a link into the first group slot, then loaded
  twice) match `node_size(..., linked=[slot])`; table in `tests/test_nodedefs_sizing.py`.
  BatchImagesNode is a fixed point after this one load ([140, 46] built, [140, 66] loaded,
  66 again after two more loads); the earlier note that it grows on every load was wrong.
  The row is added before the 200 minimum of a multiline node (TextEncodeJoyImageEdit
  stays 200). BranchNode (match-type group) gets no extra row. Links outside the group
  (MOSS `clip`, `vae`) add nothing.
- Links order after load: links whose target node has an Autogrow group or a DynamicCombo
  move behind all other links, grouped by target node in id order, each keeping its own
  order (autogrow, capture, match_partial graphs). Link ids and the set are unchanged.
  Not written into instantiate yet.
- Not matched, not fixed: Autogrow groups with a gap. BranchNode compacts the link into the
  lower slot on load. MOSS with image_0 and image_2 linked loads with `vae` before
  `image_2`.
- Not matched, not fixed, no gap: MOSS with image_0, image_1 and `vae` linked gains an
  empty `image_3` on load and grows another 20 on the second load, so that canvas is not a
  fixed point. MOSS with only image_0 linked is (the 26-node table); image_0 with `vae` was
  not measured.
- A page that never starts hung 600 s; the
  runner now gives a page 4 minutes and opens a new one, up to 4 times.
