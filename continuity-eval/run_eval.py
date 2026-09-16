# -*- coding: utf-8 -*-
"""Score a detector run against injection labels.

The join is span-based and blind: a label counts as detected when a finding in
the same file overlaps its span. The detector never sees the labels, so this is
a genuine measurement, not a self-fulfilling comparison.

Reported per project and in aggregate:

* precision / recall / F1 over the injected corpora
* recall stratified by label class, by distance bucket, and by anchor
  (canon-declared vs prose-only) -- the aggregate number hides exactly the
  differences that matter
* false positives on the injected corpora, plus a separate false-positive run on
  the *original* corpus, where every finding is by construction a false positive

Two detector configurations are always compared:

* **full** -- all rules
* **baseline** -- `rare_near_common` only: the frequency fallback without the
  slot or canon channels. This is the "trivial" strategy the full detector has
  to beat for its extra machinery to be justified.

**The second column**: the name-attribute channel (`attributes.py`) runs on the
*original* corpus, not the injected one -- the injector deliberately does not
generate that contradiction class, so scoring it against injections would be
meaningless. Its findings (real or false) go into the report under
`projects.<id>.attributes` for author adjudication; they are never merged into
the injection scores above.

The chart is hand-rolled SVG (recall by distance bucket, both configurations) to
keep the harness dependency-free.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import sys
from typing import Iterable
from xml.sax.saxutils import escape

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import attributes  # noqa: E402
import corpus_snapshot  # noqa: E402
import detect  # noqa: E402
import inject  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = 1
REPORT_KIND = "continuity_eval_report"

FULL_RULES = "canon_fact,slot_match,rare_near_common"
BASELINE_RULES = "rare_near_common"


def spans_overlap(a: list[int], b: list[int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def score(labels: list[dict], findings: list[dict]) -> dict:
    """Join findings to labels by file + span overlap; return counts and rates."""
    detected = set()
    matched_findings = set()
    for index, label in enumerate(labels):
        for f_index, finding in enumerate(findings):
            if finding["file"] == label["file"] and spans_overlap(finding["span"], label["span"]):
                detected.add(index)
                matched_findings.add(f_index)
    tp = len(detected)
    fp = len(findings) - len(matched_findings)
    fn = len(labels) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4)}


def stratified(labels: list[dict], findings: list[dict], field: str) -> dict:
    """Recall per value of a label field (class / bucket / anchor)."""
    groups: dict[str, list[int]] = collections.defaultdict(list)
    for index, label in enumerate(labels):
        groups[str(label.get(field))].append(index)
    out = {}
    for value, indices in sorted(groups.items()):
        sub_labels = [labels[i] for i in indices]
        hit = 0
        for i in indices:
            label = labels[i]
            if any(finding["file"] == label["file"]
                   and spans_overlap(finding["span"], label["span"]) for finding in findings):
                hit += 1
        out[value] = {"labels": len(sub_labels), "detected": hit,
                      "recall": round(hit / len(sub_labels), 4) if sub_labels else 0.0}
    return out


def manuscript_chars(root: pathlib.Path) -> int:
    return sum(p.stat().st_size for _, p in inject.read_chapters(root))


def verify_snapshot(manifest_path: pathlib.Path, projects_root: pathlib.Path) -> dict:
    """Re-derive every project digest from the live corpus and compare.

    A pinned snapshot that no longer matches the bytes on disk means the
    evaluation would measure something other than what was pinned -- refuse to
    run rather than write a report nobody can trust.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_kind") != corpus_snapshot.MANIFEST_KIND:
        raise SystemExit(f"error: not a corpus snapshot manifest: {manifest_path}")
    pinned_root = manifest.get("projects_root")
    if pinned_root and pathlib.Path(pinned_root).resolve() != projects_root.resolve():
        raise SystemExit(
            f"error: snapshot projects_root {pinned_root} != --projects-root {projects_root}")
    verified = {}
    for pid, pinned in manifest.get("projects", {}).items():
        live = corpus_snapshot.snapshot_project(projects_root / pid)
        if live["digest"] != pinned.get("digest"):
            raise SystemExit(
                f"error: corpus drift for {pid}: pinned {pinned.get('digest', '?')[:12]} "
                f"!= live {live['digest'][:12]} -- re-run corpus_snapshot.py or restore the corpus")
        verified[pid] = live["digest"]
    return {"manifest": str(manifest_path),
            "corpus_digest": manifest.get("corpus_digest"),
            "projects": verified}


def evaluate_project(pid: str, original_root: pathlib.Path, injected_root: pathlib.Path,
                     labels: list[dict], rules: str,
                     skip_attributes: bool = False) -> dict:
    rule_set = set(rules.split(","))

    findings = detect.detect_project(injected_root / pid, rule_set)
    result = {"labels": len(labels), "findings": len(findings), "full": score(labels, findings)}
    result["full"]["by_class"] = stratified(labels, findings, "class")
    result["full"]["by_bucket"] = stratified(labels, findings, "distance_bucket")
    result["full"]["by_anchor"] = stratified(labels, findings, "anchor")
    result["full"]["by_rule"] = dict(collections.Counter(f["rule"] for f in findings))

    # every finding on the untouched corpus is a false positive by construction
    clean_findings = detect.detect_project(original_root / pid, rule_set)
    chars = manuscript_chars(original_root / pid)
    result["clean_text"] = {
        "findings": len(clean_findings),
        "per_10k_chars": round(len(clean_findings) / (chars / 10000), 2) if chars else None,
        "by_rule": dict(collections.Counter(f["rule"] for f in clean_findings)),
    }
    if not skip_attributes:
        # the second column runs on the untouched corpus by design; its findings
        # are the author's adjudication input, never injection scores
        attr_findings = attributes.attribute_findings(original_root / pid)
        result["attributes"] = {
            "corpus": "original",
            "count": len(attr_findings),
            "findings": attr_findings,
        }
    return result


def svg_grouped_bars(series: dict[str, dict[str, float]], height: int = 180) -> str:
    """Hand-rolled grouped bar chart: series -> group -> value in [0,1]."""
    groups = sorted({g for values in series.values() for g in values})
    palette = ["#2563eb", "#94a3b8", "#f59e0b", "#10b981"]
    bar_w, gap, group_gap, left, top = 18, 4, 26, 42, 14
    width = left + len(groups) * (len(series) * (bar_w + gap) + group_gap) + 60
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'font-family="sans-serif" font-size="10">']
    parts.append(f'<line x1="{left}" y1="{height-24}" x2="{width-10}" y2="{height-24}" stroke="#333"/>')
    for s_index, (name, values) in enumerate(sorted(series.items())):
        color = palette[s_index % len(palette)]
        parts.append(f'<rect x="{width-150+s_index*70}" y="4" width="10" height="10" fill="{color}"/>')
        parts.append(f'<text x="{width-136+s_index*70}" y="13" fill="#333">{escape(name)}</text>')
        for g_index, group in enumerate(groups):
            value = values.get(group, 0.0)
            x = left + g_index * (len(series) * (bar_w + gap) + group_gap) + s_index * (bar_w + gap)
            bar_h = round((height - 44) * value)
            parts.append(f'<rect x="{x}" y="{height-24-bar_h}" width="{bar_w}" height="{bar_h}" '
                         f'fill="{color}"><title>{escape(group)}: {value}</title></rect>')
            parts.append(f'<text x="{x + bar_w/2}" y="{height-28-bar_h}" text-anchor="middle" '
                         f'fill="#555">{value:.2f}</text>' if value else "")
        for g_index, group in enumerate(groups):
            x = left + g_index * (len(series) * (bar_w + gap) + group_gap) + len(series) * (bar_w + gap) / 2
            parts.append(f'<text x="{x}" y="{height-10}" text-anchor="middle" fill="#333">'
                         f'{escape(group)}</text>')
    parts.append("</svg>")
    return "\n".join(p for p in parts if p)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_eval", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--projects-root", required=True, help="Original corpus (untouched).")
    p.add_argument("--injected-root", required=True,
                   help="Directory holding scratch copies and labels-*.jsonl.")
    p.add_argument("--projects", help="Comma-separated project ids (default: every labels-*.jsonl).")
    p.add_argument("--rules", default=FULL_RULES, help=f"Full detector rules (default: {FULL_RULES})")
    p.add_argument("--baseline-rules", default=BASELINE_RULES,
                   help=f"Baseline detector rules (default: {BASELINE_RULES})")
    p.add_argument("--output", required=True, help="Report JSON path.")
    p.add_argument("--chart", help="Optional SVG chart path (recall by distance bucket).")
    p.add_argument("--no-attributes", action="store_true",
                   help="Skip the name-attribute second column (default: run it "
                        "on the original corpus and include it in the report).")
    p.add_argument("--snapshot", help="Corpus snapshot manifest to verify against "
                                      "(refuses to run if any project's live digest drifted).")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    projects_root = pathlib.Path(args.projects_root)
    injected_root = pathlib.Path(args.injected_root)

    pids = (args.projects.split(",") if args.projects
            else sorted(p.stem.removeprefix("labels-") for p in injected_root.glob("labels-*.jsonl")))
    if not pids:
        print(json.dumps({"status": "error", "error": "no labels-*.jsonl found",
                          "error_type": "usage"}, ensure_ascii=False))
        return 2

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "rules": args.rules,
        "baseline_rules": args.baseline_rules,
        "projects": {},
    }
    if args.snapshot:
        report["corpus_snapshot"] = verify_snapshot(pathlib.Path(args.snapshot), projects_root)
    totals = {"labels": 0, "full": {"tp": 0, "fp": 0, "fn": 0},
              "baseline": {"tp": 0, "fp": 0, "fn": 0}}
    bucket_recall = {"full": collections.Counter(), "baseline": collections.Counter()}
    bucket_totals: collections.Counter = collections.Counter()

    for pid in pids:
        labels_path = injected_root / f"labels-{pid}.jsonl"
        if not labels_path.is_file():
            continue
        labels = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        entry = evaluate_project(pid, projects_root, injected_root, labels, args.rules,
                                 skip_attributes=args.no_attributes)

        baseline_findings = detect.detect_project(injected_root / pid,
                                                  set(args.baseline_rules.split(",")))
        entry["baseline"] = score(labels, baseline_findings)

        report["projects"][pid] = entry
        totals["labels"] += entry["labels"]
        for key in ("full", "baseline"):
            for field in ("tp", "fp", "fn"):
                totals[key][field] += entry[key][field]
        for bucket, stats in entry["full"]["by_bucket"].items():
            bucket_recall["full"][bucket] += stats["detected"]
            bucket_totals[bucket] += stats["labels"]
        for bucket, stats in stratified(labels, baseline_findings, "distance_bucket").items():
            bucket_recall["baseline"][bucket] += stats["detected"]

    for key in ("full", "baseline"):
        tp, fp, fn = totals[key]["tp"], totals[key]["fp"], totals[key]["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        totals[key]["precision"] = round(precision, 4)
        totals[key]["recall"] = round(recall, 4)
        totals[key]["f1"] = round(2 * precision * recall / (precision + recall), 4) \
            if precision + recall else 0.0
    totals["by_bucket_recall"] = {
        key: {bucket: round(bucket_recall[key][bucket] / bucket_totals[bucket], 4)
              for bucket in sorted(bucket_totals)}
        for key in ("full", "baseline")
    }
    totals["by_bucket_labels"] = dict(sorted(bucket_totals.items()))
    report["aggregate"] = totals

    if not args.no_attributes:
        report["attribute_channel"] = {
            "corpus": "original",
            "total_findings": sum(e.get("attributes", {}).get("count", 0)
                                  for e in report["projects"].values()),
            "note": "second column: real-corpus name-attribute conflicts, "
                    "out of the injection benchmark by design",
        }

    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")

    if args.chart:
        pathlib.Path(args.chart).write_text(
            svg_grouped_bars(totals["by_bucket_recall"]), encoding="utf-8")

    summary = {"status": "ok", "report": str(out),
               "aggregate": {k: totals[k] for k in ("labels", "full", "baseline",
                                                    "by_bucket_recall")}}
    if "attribute_channel" in report:
        summary["attribute_findings"] = report["attribute_channel"]["total_findings"]
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())