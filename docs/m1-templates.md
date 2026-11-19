# M1: learning widget templates

`/object_info` does not describe every value a node saves in `widgets_values`: frontend
extensions add widgets, and some packages change their layout between versions. A template
lists the widget names of one node type in saved order. `cwc learn` writes them from canvases
that come with the API prompt of the same run. Module: `src/cwc/nodedefs/learn.py`; file
format and registry lookup: `docs/ARCHITECTURE.md`.

## Method

Input is a sample: a canvas and the prompt it was queued as, sharing node ids. Two file
forms give one: a PNG with `workflow` and `prompt` text chunks, and `x_ui.json` beside
`x_api.json` (or `x.ui.json` beside `x.api.json`). A file that gives none is skipped and
counted by reason (`no_chunks`, `no_pair`, `invalid`).

**Alignment of one node.** The saved `widgets_values` are aligned, in order, with the node's
prompt inputs. The candidates are the prompt inputs in prompt order (link-only sockets left
out, widget inputs fed by a link kept) plus the widgets the definition keeps out of the
prompt (value control, filter, upload button, companions), placed after the prompt widget
they follow. An order-preserving alignment maximises the evidence: a value equal to the
prompt value (JS number equality, `__value__` unwrapped, and fitting the declared input
type) scores 2; a plausible out-of-prompt widget, or a linked widget whose value fits its
type, scores 1. A position left unexplained is `null`. Because both sequences keep their
order, a value repeated in another input cannot pull a name out of place.

**Grouping and voting.** Instances are grouped by `(type, properties.ver)`. A group's
template has the most common `widgets_values` length; each position takes the name most
aligned instances gave it, and `null` does not vote. A name that would sit at two positions
stays at the one with more votes. A position with no vote takes the derived name when the
definition without templates fits every instance of the chosen length. Nodes absent from the
prompt (muted, bypassed) vote on the length only.

**What gets a template.** Only types whose definition without templates gets the length wrong
for some instance, has incomplete names, or is unknown. Types from truth or the frontend
builtins keep those (truth first), DynamicCombo types replay their layout from
`/object_info`, and nodes of subgraph types are skipped. Nodes saving `widgets_values` as an
object are counted, not learned.

**Evidence.** Each entry carries `instances`, `aligned`, `lengths` (instance count per saved
length), `conflicts` (instances of another length plus aligned instances disagreeing with a
chosen name) and `conflict_ratio`.

**Output.** Entries are ordered by type, then newest version first: dotted numeric versions
by value, then other versions (commit hashes) by instance count, then the entry without a
version. The same samples in any order give the same bytes. `NodeDefs.get(type, ver)` returns
the template recorded for a node's version, otherwise the first entry. A run that finds no
sample exits 3 and leaves an existing output file untouched.

## Evaluation

`tools/eval_templates.py` measures the templates on files it did not learn from.

- Split by workflow, not by node. The structure key (`--split structure`) hashes the node
  types and the links with node ids, link ids, order, `widgets_values`, positions, sizes,
  titles, modes and link types left out (colour refinement over the link graph), so every file
  with the same structure, from either source, lands on the same side.
- The M1 number comes from grouped k-fold cross-validation (`--split structure --folds K`).
  A structure goes to fold `int(sha256("<seed>:<structure key>")[:8], 16) % K`. For each
  fold, templates are learned from the other folds and the fold is measured, so every node of
  every sample is measured exactly once. The files are read once. The report pools the raw
  counts and lists each fold and each mismatch with its fold. Seed `cwc-m1`, K = 5.
- A single split (`--holdout`, default 20%) stays as a comparison: `--split structure`
  holds out a share of the structures, `--split file` (default) hashes `<seed>:<source>/<path>`,
  which lets renders of one workflow fall on both sides. All splits are the same on every machine.
- Learning reads the training samples. For each root node of a held-out sample the definition
  comes from `NodeDefs.load(object_info=..., templates=<learned>)` with
  `get(type, properties.ver)`. The node counts as matched when
  `len(widget_layout(definition, values)) == len(values)`.
- Every row of the report carries raw counts (`nodes`, `match`). The rate is rounded for
  reading; a threshold is compared as `match * 100 >= nodes * 99`.
- Nodes of subgraph types and nodes saving an object are not measured. A held-out canvas
  identical to a training canvas is counted and also reported apart. `--holdout` takes 0 to
  100; PNG files macOS reports as not downloaded (iCloud) are skipped unread.
- Corpus: files from one ComfyUI installation, in two kinds of source (canvas/API pairs and
  PNGs), and that installation's `/object_info` export. The corpus stays outside the
  repository. Most PNGs carry no workflow and prompt pair and are skipped; some are invalid
  (next section) and a canvas file can lack its API partner.

`tests/test_learn_corpus.py` asserts the M1 number, the pooled 5-fold templates tier over both sources:

    CWC_LEARN_CORPUS=<folder with pairs> CWC_LEARN_PNGS=<folder with PNGs> \
        CWC_LEARN_NODEDEFS=<object_info export> python3 -m unittest tests.test_learn_corpus

## Results

The done-condition is the pooled 5-fold templates tier, both sources: learned templates
match the saved `widgets_values` length on held-out files at 99% or more.

**M1 number: 99.68% of templates-tier nodes, over the 5-fold run** (seed `cwc-m1`).

| Tier | Pairs | PNG | Both |
|---|---|---|---|
| Templates (M1 number) | 97.92% | 99.91% | 99.68% |
| Truth | 99.94% | 98.73% | 99.16% |
| Derived | 100% | 99.66% | 99.79% |
| Builtin | 100% | 100% | 100% |
| All nodes | 99.81% | 99.46% | 99.57% |

Per fold (templates tier, then all nodes):

| Fold | Templates tier | All nodes |
|---|---|---|
| 0 | 99.19% | 99.19% |
| 1 | 100% | 99.92% |
| 2 | 99.86% | 99.58% |
| 3 | 98.97% | 99.42% |
| 4 | 99.50% | 99.04% |

The templates-tier mismatches have two causes, both in custom node types: the same
`(type, version)` saved instances of different lengths, so one template cannot fit all (the
bulk of the mismatches: one type in the pairs, another in the PNGs), and a template whose length differs
from the instance (a single node, in a PNG). The pairs rate alone is under 99% because most
of its mismatches sit in one type in one fold; the criterion is the pooled number.

The other mismatches are outside the templates tier: truth-tier core nodes whose layout
changed between versions (truth comes first, so a template cannot correct them), and
derived-tier nodes of a few custom types that no training sample had.

How the choice of method came about: a single structure split holds out a different number of
samples depending on the seed. Over 20 seeds the templates tier ran from 96.1% to 100%, and 4
of the 20 seeds were under 99%. A single seed is not a measurement, so the M1 number is the
cross-validation above, which measures every node once.

Comparison rows, same corpus (templates tier, then all nodes):

| Run | Templates, both | All nodes, both |
|---|---|---|
| 5-fold, pooled (M1 number) | 99.68% | 99.57% |
| Structure split, seed `cwc-m1` (a small held-out set) | 100% | 99.39% |
| File split, seed `cwc-m1` (a larger held-out set, most of it sharing a structure with a training sample) | 99.26% | 99.67% |

The file split leaks: renders of one workflow sit on both sides, so its numbers are not
held-out numbers for new workflows. Without its held-out canvases identical to a training
canvas the rate is 99.60%.

## Known limits

- Some PNGs in the corpus are counted `invalid`. The causes seen are a prompt chunk
  containing `NaN`, which `ingest.limits.parse_json` rejects, and an API prompt stored in the
  `workflow` chunk. Accepting them is an `ingest` change.
- Truth wins over templates, so a core node whose layout changed between versions cannot be
  corrected by learning.
- Layouts that vary in length inside one `(type, version)` get the most common length.
- A node whose `len(widgets_values) * candidates` exceeds `MAX_ALIGN_CELLS` (250000) is not
  aligned: it is counted as `too_large` and votes on the length only. The alignment is
  quadratic; nodes of real workflows have far fewer cells, so none reaches the limit in
  practice.
- The package id of an entry (`pack`) is the most common `cnr_id` or `aux_id` of the group's
  instances, ties broken by text, so file order does not change the output.
- Input files are read through `cwc.ingest.read_file`, which refuses a file above the size
  limit.
- A `null` name stays where no prompt value or out-of-prompt widget explains the position.
  The length is right, but `widgets_values_named` is not written for such a node.
- Learning needs the `/object_info` of the machine that made the files; the bundled core
  data is the fallback for core nodes only.
- No consumer other than the evaluation tool passes `ver` to `NodeDefs.get` yet.
- `src/cwc/data/templates.json` is an empty table. Learned templates stay with the files they come
  from until a curated, reviewed set is published.
