# -*- coding: utf-8 -*-
"""Deterministic contradiction detector for long-form fiction.

Blind by construction
---------------------
The detector sees exactly what a consistency checker for a finished manuscript
would see: the book's own chapters, its declared canon facts, and its memory
cards. It never sees the injection labels and never sees the pre-injection
corpus, so a detection cannot be a diff against ground truth.

Three rules, all deterministic
------------------------------
* **canon_fact** (strongest) -- a value in the prose conflicts with a value
  declared in `continuity/canon-facts.jsonl` (same class and unit, value within
  MAX_VALUE_DISTANCE). This is the "declared facts vs manuscript" channel.
* **slot_match** -- two occurrences whose context windows are identical once the
  numeral is blanked out ("负债§秒" == "负债§秒"): the same slot filled with two
  different numbers. High precision, low recall -- exact context equality is a
  strict test.
* **rare_near_common** -- a rare value sitting next to a common one of the same
  kind; the frequency fallback that catches contradictions whose contexts never
  repeat.

There is deliberately **no same-chapter rule**: on clean text nearly every
same-chapter near pair is legitimate discourse -- 第一/第二/第三 enumerations,
different events with different durations (三分钟 vs 五分钟). Without semantics,
a same-chapter pair is indistinguishable from an enumeration, so the rule would
be noise; intra-chapter contradictions are still caught by the other two rules
when the mutated value is rare.

The rarity guard is load-bearing, not decoration: in the counter-ledger book the debt *legitimately*
progresses (3秒 -> 6秒 -> 9秒 ...), so near values coexist on every page. A rule
that flagged any near pair would drown in true positives of the wrong kind. Both
values being frequent means the variation is established usage, not an error.

Tokenisation is shared with the injector (`inject.scan_text`). That is honest
and worth stating: the benchmark measures the *comparison* layer, not the
tokenizer, and the trivial-baseline comparison in the evaluation quantifies what
the comparison rules add over frequency alone.

Stdlib only. Never writes to the project it reads.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import json
import pathlib
import sys
from typing import Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import inject  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = 1
FINDING_KIND = "value_contradiction"
CONTEXT_WINDOW = 12
MAX_VALUE_DISTANCE = 3
RARE = 2              # cnt <= RARE counts as "rare" for the frequency rule
BOTH_RARE_LIMIT = 3   # min(cnt_a, cnt_b) <= this means the pair is not established usage

RULE_PRIORITY = {"canon_fact": 0, "slot_match": 1, "rare_near_common": 2}
DEFAULT_RULES = {"canon_fact", "slot_match", "rare_near_common"}


def occurrence_key(occ: inject.Occ) -> tuple:
    """Grouping key: the kind of slot a value can contradict another in."""
    if occ.klass == "date" and occ.date_parts:
        return ("date", occ.date_parts[0], occ.date_parts[1])
    _, _, unit = inject.split_token(occ.token)
    return (occ.klass, unit, occ.script)


def blanked_context(text: str, start: int, end: int, window: int = CONTEXT_WINDOW) -> str:
    """The context around a span with the numeral itself blanked out."""
    return text[max(0, start - window):start] + "§" + text[end:end + window]


def load_facts(root: pathlib.Path) -> list[dict]:
    path = root.joinpath(*inject.FACTS_FILE)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def fact_occurrences(facts: list[dict], source_root: pathlib.Path) -> list[tuple[str, inject.Occ]]:
    """Value tokens asserted by declared facts, nested ones included."""
    out: list[tuple[str, inject.Occ]] = []
    for index, fact in enumerate(facts):
        blob = " ".join(str(fact.get(k, "")) for k in ("subject", "predicate", "object"))
        occs = inject.scan_text(blob, f"fact-{index:04d}", source_root / "continuity" / "canon-facts.jsonl",
                                keep_nested=True)
        for occ in occs:
            out.append((fact.get("fact_id", "?"), occ))
    return out


@dataclasses.dataclass
class Params:
    """Detector thresholds. Swept on the dev novels; the defaults are the
    winning configuration, not guesses (see run_eval --sweep history)."""

    rare: int = 2            # cnt <= rare counts as a rare value
    partner_mult: int = 2    # rare_near_common partner bar: >= max(min_partner, partner_mult * cnt)
    min_partner: int = 2
    both_rare_limit: int = 3  # slot_match: min(cnt_a, cnt_b) <= this
    max_value_distance: int = 3
    context_window: int = 12
    seq_min_run: int = 4      # strict mode: a run of >= this many values ...
    seq_max_gap: int = 3      # ... chained by gaps <= this counts as a progression
    seq_guard: bool = False   # strict mode suppresses near pairs inside a progression


def sequence_values(counts: collections.Counter, min_run: int, max_gap: int) -> set[int]:
    """Values that sit inside a dense numeric run of the same key.

    Counters and ordinals legitimately walk: 第N笔报备 goes 7,8,9,10... across
    chapters, the debt goes 3,6,9,12. When a key's distinct values chain into a
    run of MIN_RUN or more with gaps of MAX_GAP or less, those values are
    established progression, and a near pair inside the run is expected
    variation, not a contradiction.

    Honesty note: on the injected benchmark this guard trades recall for
    precision and is roughly F1-neutral, because the injections themselves
    cluster around the truth (each ±1..3) and several mutations of one token
    family *manufacture* the very run this looks for. Real contradictory values
    are sparse and do not cluster, so real-world precision gain is larger than
    the benchmark shows -- but that is a claim about the design, not a
    measurement, and the benchmark number is the one to quote.
    """
    distinct = sorted(counts)
    out: set[int] = set()
    run: list[int] = []
    for value in distinct:
        if run and value - run[-1] > max_gap:
            if len(run) >= min_run:
                out.update(run)
            run = []
        run.append(value)
    if len(run) >= min_run:
        out.update(run)
    return out


def detect_project(root: pathlib.Path, rules: set[str] | None = None,
                   params: Params | None = None) -> list[dict]:
    """Return findings for one project. Read-only; the project is never modified."""
    rules = rules or DEFAULT_RULES
    params = params or Params()

    texts: dict[pathlib.Path, str] = {}
    occurrences: list[inject.Occ] = []
    for _, path in inject.read_chapters(root):
        text = path.read_text(encoding="utf-8")
        texts[path] = text
        occurrences.extend(inject.scan_text(text, path.name.split("-")[0], path))

    by_key: dict[tuple, list[inject.Occ]] = collections.defaultdict(list)
    for occ in occurrences:
        by_key[occurrence_key(occ)].append(occ)
    counts: dict[tuple, collections.Counter] = {
        key: collections.Counter(o.value for o in group) for key, group in by_key.items()
    }
    # values inside an established progression, per key. Always computed: the
    # sequence guard (seq_guard) uses it to filter prose partners, and the
    # canon_fact rule uses it to downgrade findings whose prose side sits inside
    # a progressing series (第十四次 vs the declared 第十二次 is a ledger walking
    # forward, not an error).
    sequences: dict[tuple, set[int]] = {
        key: sequence_values(counter, params.seq_min_run, params.seq_max_gap)
        for key, counter in counts.items()
    }

    # canon-fact assertions, grouped the same way as prose values
    fact_assertions: dict[tuple, list[tuple[str, inject.Occ]]] = collections.defaultdict(list)
    for fact_id, occ in fact_occurrences(load_facts(root), root):
        fact_assertions[occurrence_key(occ)].append((fact_id, occ))

    findings: dict[tuple[int, str], dict] = {}
    for key, group in by_key.items():
        seq = sequences.get(key, set())
        for occ in group:
            text = texts[occ.path]
            ctx = blanked_context(text, occ.start, occ.end, params.context_window)
            cnt_v = counts[key][occ.value]

            partners = [
                other for other in group
                if other is not occ
                and 1 <= abs(other.value - occ.value) <= params.max_value_distance
                and not (params.seq_guard and occ.value in seq and other.value in seq)
            ]

            slot = [p for p in partners
                    if blanked_context(texts[p.path], p.start, p.end, params.context_window) == ctx]
            best_rule = None
            conflicts: list[dict] = []
            fact_values: list[int] = []

            # canon_fact is evaluated INDEPENDENTLY of prose partners: on a
            # fact-anchored contradiction the prose truth is gone by design, so
            # there is no partner to find -- gating this rule behind a non-empty
            # partner list made the declared-canon channel structurally blind to
            # exactly the cell it exists for. The rarity guard keeps protecting
            # established prose variation, but a value with no prose neighbour
            # within reach is isolated, has no variation to protect, and the
            # declared fact is the only context that can judge it.
            if "canon_fact" in rules and (cnt_v <= params.rare or not partners):
                for fact_id, fact_occ in fact_assertions.get(key, []):
                    # a conflict requires the values to DIFFER: agreement with a
                    # declared fact is corroboration, not a finding
                    if 1 <= abs(fact_occ.value - occ.value) <= params.max_value_distance:
                        best_rule = "canon_fact"
                        fact_values.append(fact_occ.value)
                        conflicts.append({
                            "source": "canon_fact", "fact_id": fact_id,
                            "token": fact_occ.token, "value": fact_occ.value,
                        })
            if best_rule is None and "slot_match" in rules and slot:
                partner_counts = [counts[key][p.value] for p in slot]
                if min([cnt_v] + partner_counts) <= params.both_rare_limit:
                    best_rule = "slot_match"
            if best_rule is None and "rare_near_common" in rules and partners and cnt_v <= params.rare:
                bar = max(params.min_partner, params.partner_mult * cnt_v)
                if max(counts[key][p.value] for p in partners) >= bar:
                    best_rule = "rare_near_common"
            if best_rule is None:
                continue

            # evidence: the strongest conflicting occurrence, quoted verbatim; on
            # a fact-only conflict the declared canon is the counter-side
            pool = slot or partners
            if pool:
                witness = max(pool, key=lambda p: counts[key][p.value])
                witness_text = texts[witness.path]
                witness_rel = witness.path.relative_to(root).as_posix()
                conflicts.append({
                    "source": "manuscript",
                    "file": witness_rel,
                    "chapter": witness.chapter,
                    "span": [witness.start, witness.end],
                    "token": witness.token,
                    "value": witness.value,
                })
                evidence = [{"path": witness_rel, "location": f"offset {witness.start}",
                             "quote": witness_text[max(0, witness.start - 20):witness.end + 20]}]
            else:
                evidence = [{"path": "continuity/canon-facts.jsonl",
                             "location": conflicts[0]["fact_id"],
                             "quote": conflicts[0]["token"]}]
            # A canon-fact conflict whose prose side sits inside an established
            # progression is usually the ledger walking forward (第十四次 vs the
            # declared 第十二次), not an error: downgrade and tag it so the
            # author's triage list leads with the real suspects.
            progression_suspect = best_rule == "canon_fact" and (
                occ.value in seq and any(v in seq for v in fact_values)
            )
            findings[(occ.start, occ.path.as_posix())] = {
                "schema_version": SCHEMA_VERSION,
                "finding_kind": FINDING_KIND,
                "project_id": root.name,
                "rule": best_rule,
                "severity": ("warning" if progression_suspect else
                             "error" if best_rule in ("canon_fact", "slot_match")
                             else "warning"),
                "file": occ.path.relative_to(root).as_posix(),
                "chapter": occ.chapter,
                "span": [occ.start, occ.end],
                "token": occ.token,
                "token_class": occ.klass,
                "script": occ.script,
                "value": occ.value,
                "context": text[max(0, occ.start - 24):occ.end + 24],
                "conflicts": conflicts,
                "evidence": evidence,
            }
            if progression_suspect:
                findings[(occ.start, occ.path.as_posix())]["progression_suspect"] = True

    ordered = [findings[k] for k in sorted(findings, key=lambda k: (k[1], k[0]))]
    for finding in ordered:
        finding["conflicts"].sort(key=lambda c: (c.get("file", ""), c.get("span", [0, 0])[0]))
    return ordered


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="detect", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="Project root to scan (read-only).")
    p.add_argument("--output", help="Write findings JSONL here (default: stdout).")
    p.add_argument("--rules", help="Comma-separated subset: canon_fact,slot_match,rare_near_common")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = pathlib.Path(args.project)
    if not root.is_dir():
        print(json.dumps({"status": "error", "error": f"not a directory: {root}",
                          "error_type": "usage"}, ensure_ascii=False))
        return 2

    rules = set(args.rules.split(",")) if args.rules else None
    findings = detect_project(root, rules)

    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for finding in findings:
                fh.write(json.dumps(finding, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "status": "ok",
        "project_id": root.name,
        "findings": len(findings),
        "by_rule": dict(collections.Counter(f["rule"] for f in findings)),
        "by_severity": dict(collections.Counter(f["severity"] for f in findings)),
        "by_class": dict(collections.Counter(f["token_class"] for f in findings)),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())