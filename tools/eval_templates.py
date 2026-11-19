"""Measure learned widget templates on held-out files.

Files are split, not nodes. ``--split file`` (default): a source file goes to the held-out
set when the SHA-256 of ``<seed>:<source>/<path relative to its root>`` falls in the
held-out share. ``--split structure``: the key is the workflow's structure instead (see
:func:`structure_key`), so every file whose canvas has the same structure, whatever its
source, lands on the same side; renders of one workflow cannot sit on both sides. Either
way the split is the same on every run and every machine. ``--folds K`` (with the structure
split) is grouped k-fold cross-validation: a structure goes to fold
``int(sha256(<seed>:<structure key>)[:8], 16) % K``; for each fold, templates are learned
from the other folds and the fold is measured, so every node is measured once. The report
pools the raw counts and lists each fold. Templates are learned from the training files
(``cwc.nodedefs.learn``); every root node of a held-out file then gets its definition from
``NodeDefs.load(object_info=<--nodedefs>, templates=<learned>)`` with
``get(type, properties.ver)``, and the length of ``widget_layout(definition, values)`` is
compared with the saved ``widgets_values`` (absent counts as empty). Nodes saving
``widgets_values`` as an object and nodes of subgraph types are counted, not measured.

Held-out files are the ones that give a sample (a canvas with its API prompt), the same
population learning reads. A held-out canvas identical to a training canvas (same
workflow JSON) is counted in the totals and also reported separately, since it tests
nothing new.

PNG files that macOS reports as not downloaded (iCloud ``dataless``) are skipped unread
and counted, so the run never triggers a download.

    python3 tools/eval_templates.py --pairs DIR --pngs DIR --nodedefs FILE [--holdout 20]
        [--seed cwc-m1] [--split file|structure] [--folds K] [--templates-out FILE] [--json]

Every row of the report carries raw counts (``nodes``, ``match``); ``rate`` is rounded for
reading only and is never to be compared with a threshold: use ``match * 100 >= nodes * 99``.

Type names appear in the output; it is meant for the machine that owns the files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cwc.emit.canonical import json_bytes, write_atomic  # noqa: E402
from cwc.nodedefs.derive import widget_layout  # noqa: E402
from cwc.canvas.model import Canvas  # noqa: E402
from cwc.nodedefs.learn import NO_TEMPLATES, Learner, Sample, read_sample  # noqa: E402
from cwc.nodedefs.registry import NodeDefs, read_source  # noqa: E402
from cwc.nodedefs.types import Tier  # noqa: E402

SF_DATALESS = 0x40000000  # macOS st_flags: content not on this disk (iCloud)
CANVAS_GLOBS = ("*_ui.json", "*.ui.json")
SPLITS = ("file", "structure")


def _sources(pairs: Path | None, pngs: Path | None, skipped: Counter[str]) -> list[tuple[str, str, Path]]:
    """``(source, key, path)`` for every candidate file, in a fixed order."""
    found: list[tuple[str, str, Path]] = []
    if pairs is not None:
        seen: set[Path] = set()
        for pattern in CANVAS_GLOBS:
            seen.update(pairs.rglob(pattern))
        found += [("pairs", p.relative_to(pairs).as_posix(), p) for p in sorted(seen)]
    if pngs is not None:
        for directory, dirs, files in os.walk(pngs):
            dirs.sort()
            for name in sorted(files):
                if not name.lower().endswith(".png"):
                    continue
                path = Path(directory) / name
                if getattr(os.lstat(path), "st_flags", 0) & SF_DATALESS:
                    skipped["png_not_downloaded"] += 1
                    continue
                found.append(("png", path.relative_to(pngs).as_posix(), path))
    return found


def held_out(seed: str, source: str, key: str, percent: int) -> bool:
    digest = hashlib.sha256(f"{seed}:{source}/{key}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100 < percent


def structure_key(canvas: Canvas) -> str:
    """Identifies the workflow structure: node types and links, ids normalised away.

    Colour refinement: a node starts with its type and takes in, round by round, the sorted
    colours of its neighbours with the slots they are joined at. The key hashes the sorted
    node colours and the sorted links as colour pairs, so it does not depend on node ids,
    link ids, array order, ``widgets_values``, positions, sizes, titles, modes or link types.
    Different structures can collide (refinement does not tell every pair of graphs apart);
    a collision only puts two structures on the same side.
    """

    def digest(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    colour = {node.key: digest(node.type) for node in canvas.nodes}
    edges = [(str(link.origin_id), link.origin_slot, str(link.target_id), link.target_slot) for link in canvas.links]
    classes = len(frozenset(colour.values()))
    for _ in range(len(colour)):
        incoming: dict[str, list[Any]] = defaultdict(list)
        outgoing: dict[str, list[Any]] = defaultdict(list)
        for origin, o_slot, target, t_slot in edges:
            incoming[target].append((t_slot, o_slot, colour.get(origin, "")))
            outgoing[origin].append((o_slot, t_slot, colour.get(target, "")))
        colour = {k: digest(c, sorted(incoming[k]), sorted(outgoing[k])) for k, c in colour.items()}
        refined = len(frozenset(colour.values()))
        if refined == classes:
            break
        classes = refined
    links = sorted((colour.get(o, ""), o_slot, t_slot, colour.get(t, "")) for o, o_slot, t, t_slot in edges)
    return digest(sorted(colour.values()), links)


def _digest(sample: Sample) -> str:
    return hashlib.sha256(json.dumps(sample.canvas.raw, sort_keys=True).encode("utf-8")).hexdigest()


class _Tally:
    def __init__(self) -> None:
        self.total: Counter[str] = Counter()
        self.ok: Counter[str] = Counter()

    def add(self, key: str, ok: bool) -> None:
        self.total[key] += 1
        self.ok[key] += ok

    def merge(self, other: "_Tally") -> None:
        self.total.update(other.total)
        self.ok.update(other.ok)

    def rows(self) -> dict[str, dict[str, Any]]:
        return {
            k: {"nodes": self.total[k], "match": self.ok[k], "rate": round(self.ok[k] / self.total[k], 4)}
            for k in sorted(self.total)
        }


class _Measure:
    """What the held-out samples of one fit (a split, or one fold) showed."""

    def __init__(self) -> None:
        self.overall, self.by_tier, self.fresh = _Tally(), _Tally(), _Tally()
        self.excluded: Counter[str] = Counter()
        self.misses: dict[str, Counter[str]] = defaultdict(Counter)  # source -> reason
        self.by_type: Counter[tuple[str, str, str, str]] = Counter()
        self.test_samples: Counter[str] = Counter()
        self.shared_structure: Counter[str] = Counter()

    def merge(self, other: "_Measure") -> None:
        self.overall.merge(other.overall)
        self.by_tier.merge(other.by_tier)
        self.fresh.merge(other.fresh)
        self.excluded.update(other.excluded)
        for source, counter in other.misses.items():
            self.misses[source].update(counter)
        self.by_type.update(other.by_type)
        self.test_samples.update(other.test_samples)
        self.shared_structure.update(other.shared_structure)


def _reason(nd: Any, type_name: str, ver: str | None, trained: dict[str, Any], seen_types: set[str]) -> str:
    """Why a held-out node's layout length is wrong."""
    if nd is None:
        return "no definition"
    if nd.tier in (Tier.TRUTH, Tier.BUILTIN):
        return f"{nd.tier.value} layout differs"
    if nd.tier is Tier.DERIVED:
        if type_name not in seen_types:
            return "type not in training files"
        return "derived layout differs; training instances fitted it"
    entry = trained.get((type_name, ver))
    if entry is None:
        return "version not in training files"
    if len(entry["evidence"]["lengths"]) > 1:
        return "length varies within one (type, version)"
    return "template length differs"


Item = tuple[str, str, Sample]  # source, key, sample


def _check_split(args: argparse.Namespace) -> None:
    mode = getattr(args, "split", "file")
    if mode not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}, not {mode!r}")
    if not 0 <= args.holdout <= 100:
        raise ValueError(f"holdout must be between 0 and 100, not {args.holdout}")
    folds = getattr(args, "folds", None)
    if folds is not None:
        if mode != "structure":
            raise ValueError("folds need the structure split")
        if folds < 2:
            raise ValueError(f"folds must be at least 2, not {folds}")
        if getattr(args, "templates_out", None):
            raise ValueError("templates_out is for a single split, not folds")


def _split(items: list[Item], args: argparse.Namespace) -> tuple[list[Item], list[Item]]:
    _check_split(args)
    mode = getattr(args, "split", "file")
    train: list[Item] = []
    test: list[Item] = []
    for item in items:
        source, key, sample = item
        if mode == "structure":
            out = held_out(args.seed, "structure", structure_key(sample.canvas), args.holdout)
        else:
            out = held_out(args.seed, source, key, args.holdout)
        (test if out else train).append(item)
    return train, test


def fold_of(seed: str, structure: str, folds: int) -> int:
    digest = hashlib.sha256(f"{seed}:{structure}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % folds


def _folds(items: list[Item], args: argparse.Namespace) -> list[tuple[list[Item], list[Item]]]:
    """``(train, test)`` per fold: every structure is in the test side of exactly one fold."""
    _check_split(args)
    keyed = [(fold_of(args.seed, structure_key(item[2].canvas), args.folds), item) for item in items]
    return [
        ([item for k, item in keyed if k != fold], [item for k, item in keyed if k == fold])
        for fold in range(args.folds)
    ]


def _fit_and_measure(train: list[Item], test: list[Item], object_info: Any, args: argparse.Namespace) -> tuple[_Measure, dict[str, Any], Counter[str]]:
    """Learn from ``train``, then measure every node of ``test``."""
    learner = Learner(NodeDefs.load(object_info=object_info, templates=NO_TEMPLATES))
    train_samples: Counter[str] = Counter()
    seen_types: set[str] = set()
    train_digests: set[str] = set()
    train_structures = {structure_key(sample.canvas) for _, _, sample in train}
    for source, _, sample in train:
        train_samples[source] += 1
        learner.add(sample)
        seen_types.update(node.type for node in sample.canvas.nodes)
        train_digests.add(_digest(sample))
    doc = learner.document()
    if args.templates_out:
        write_atomic(args.templates_out, json_bytes(doc))
    trained = {(e["type"], e["ver"]): e for e in doc["templates"]}
    defs = NodeDefs.load(object_info=object_info, templates=doc)

    m = _Measure()
    for source, _, sample in test:
        m.test_samples[source] += 1
        if structure_key(sample.canvas) in train_structures:
            m.shared_structure[source] += 1
        duplicate = _digest(sample) in train_digests
        subgraphs = (sample.canvas.raw.get("definitions") or {}).get("subgraphs") or []
        subgraph_ids = {s.get("id") for s in subgraphs if isinstance(s, dict)}
        for node in sample.canvas.nodes:
            values = node.widgets_values if node.widgets_values is not None else []
            if node.type in subgraph_ids:
                m.excluded["subgraph type"] += 1
                continue
            if not isinstance(values, list):
                m.excluded["widgets_values is an object"] += 1
                continue
            ver = node.properties.get("ver") if isinstance(node.properties.get("ver"), str) else None
            nd = defs.get(node.type, ver or None)
            ok = nd is not None and len(widget_layout(nd, values)) == len(values)
            tier = nd.tier.value if nd is not None else "none"
            for key in (source, "all"):
                m.overall.add(key, ok)
                m.by_tier.add(f"{key}/{tier}", ok)
                if not duplicate:
                    m.fresh.add(key, ok)
            if not ok:
                reason = _reason(nd, node.type, ver or None, trained, seen_types)
                m.misses[source][reason] += 1
                m.by_type[(source, tier, node.type, reason)] += 1
    learned = {"templates": len(doc["templates"]), "types": len({e["type"] for e in doc["templates"]}), "nodes": dict(sorted(learner.counts.items()))}
    return m, learned, train_samples


def _mismatch_rows(m: _Measure, fold: int | None = None) -> list[dict[str, Any]]:
    rows = []
    for (src, tier, t, r), n in sorted(m.by_type.items(), key=lambda kv: (-kv[1], kv[0])):
        row: dict[str, Any] = {"source": src, "tier": tier, "type": t, "reason": r, "nodes": n}
        if fold is not None:
            row = {"fold": fold, **row}
        rows.append(row)
    return rows


def _fold_summary(fold: int, train_samples: Counter[str], m: _Measure, learned: dict[str, Any]) -> dict[str, Any]:
    rows = m.by_tier.rows()
    return {
        "fold": fold,
        "samples": {"train": dict(sorted(train_samples.items())), "test": dict(sorted(m.test_samples.items()))},
        "learned": learned,
        "length_match": m.overall.rows(),
        "templates_tier": {k.split("/")[0]: row for k, row in rows.items() if k.endswith("/templates")},
        "mismatches": sum(m.by_type.values()),
    }


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    _check_split(args)  # before the files are read
    skipped: Counter[str] = Counter()
    files = _sources(args.pairs, args.pngs, skipped)
    items: list[Item] = []
    for source, key, path in files:
        per: Counter[str] = Counter()
        sample = read_sample(path, per)
        for reason, count in per.items():
            skipped[f"{source}_{reason}"] += count
        if sample is not None:
            items.append((source, key, sample))
    object_info = read_source(args.nodedefs)
    folds = getattr(args, "folds", None)

    pooled = _Measure()
    fold_rows: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    if folds is None:
        train, test = _split(items, args)
        m, learned, train_samples = _fit_and_measure(train, test, object_info, args)
        pooled.merge(m)
        mismatches = _mismatch_rows(m)
        split: dict[str, Any] = {
            "mode": getattr(args, "split", "file"),
            "seed": args.seed,
            "holdout_percent": args.holdout,
            "files": len(files),
            "train_samples": len(train),
            "test_samples": len(test),
        }
        samples: dict[str, Any] = {"train": dict(sorted(train_samples.items())), "test": dict(sorted(m.test_samples.items()))}
    else:
        for fold, (train, test) in enumerate(_folds(items, args)):
            m, learned, train_samples = _fit_and_measure(train, test, object_info, args)
            pooled.merge(m)
            mismatches += _mismatch_rows(m, fold)
            fold_rows.append(_fold_summary(fold, train_samples, m, learned))
        split = {"mode": "structure", "seed": args.seed, "folds": folds, "files": len(files), "test_samples": len(items)}
        samples = {"test": dict(sorted(pooled.test_samples.items()))}

    report: dict[str, Any] = {
        "split": split,
        "samples": samples,
        "test_samples_sharing_a_training_structure": dict(sorted(pooled.shared_structure.items())),
        "skipped": dict(sorted(skipped.items())),
    }
    if folds is None:
        report["learned"] = learned
    else:
        report["structures"] = len({structure_key(sample.canvas) for _, _, sample in items})
        report["folds"] = fold_rows
    report.update(
        {
            "length_match": pooled.overall.rows(),
            "length_match_by_tier": pooled.by_tier.rows(),
            "length_match_without_training_duplicates": pooled.fresh.rows(),
            "excluded": dict(sorted(pooled.excluded.items())),
            "mismatch_reasons": {s: dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) for s, c in sorted(pooled.misses.items())},
            "mismatches": mismatches,
        }
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pairs", type=Path, help="folder searched for x_ui.json / x.ui.json beside their API files")
    parser.add_argument("--pngs", type=Path, help="folder searched for PNG files with workflow and prompt chunks")
    parser.add_argument("--nodedefs", required=True, help="/object_info export of the machine that made the files")
    parser.add_argument("--holdout", type=int, default=20, help="held-out share in percent, 0 to 100 (default 20)")
    parser.add_argument("--seed", default="cwc-m1", help="split seed (default cwc-m1)")
    parser.add_argument("--split", choices=SPLITS, default="file", help="what a split keeps together (default file)")
    parser.add_argument("--folds", type=int, help="grouped k-fold cross-validation with --split structure: every structure is held out once")
    parser.add_argument("--templates-out", help="also write the templates learned from the training files")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 0 <= args.holdout <= 100:
        parser.error("--holdout must be between 0 and 100")
    if args.folds is not None:
        if args.split != "structure":
            parser.error("--folds needs --split structure")
        if args.folds < 2:
            parser.error("--folds must be at least 2")
        if args.templates_out:
            parser.error("--templates-out is for a single split, not --folds")
    if args.pairs is None and args.pngs is None:
        print("eval_templates: give --pairs, --pngs or both", file=sys.stderr)
        return 2
    report = evaluate(args)
    if args.json:
        sys.stdout.write(json_bytes(report).decode("utf-8"))
        return 0
    for section, value in report.items():
        print(f"{section}:")
        rows = value if isinstance(value, list) else [f"{k}: {v}" for k, v in value.items()]
        for row in rows:
            print(f"  {row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
