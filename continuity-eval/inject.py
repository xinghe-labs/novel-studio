# -*- coding: utf-8 -*-
"""Deterministic contradiction injector for long-form fiction.

Mechanism
---------
A value that recurs across chapters is the interesting case: mutate one
occurrence, leave the others intact, and the mutated chapter now contradicts the
rest of the book at a *known* distance. That distance is the knob that separates
an easy test (a chapter contradicting itself) from the real one (a chapter
contradicting something sixty chapters back), so every label carries it.

The three invariants that make a label trustworthy
--------------------------------------------------
1. **Surviving truth is occurrence-level, not chapter-level.** The contradiction
   is the occurrences left unmutated. For an intra-chapter injection the
   surviving truth lives in the *same* chapter (the neighbouring occurrence), so
   counting by chapter would wrongly discard it; for a cross-chapter injection
   at least one chapter must be left untouched, and selection refuses a plan that
   would consume the token's last chapter.
2. **Spans are applied in ascending offset order per file.** A replacement at a
   higher offset never moves a lower one, so a span recorded after its own
   replacement stays valid in the final artifact. Applying mutations in an
   arbitrary order silently invalidates the spans of earlier injections, because
   a later edit *below* them shifts them -- which turns every span-based check
   into a lie.
3. **A label is dropped when it has no surviving truth.** Recording a
   contradiction that does not exist would inflate every downstream metric.

Other design choices worth stating
----------------------------------
* **Subtle, not arbitrary.** Replacements are neighbouring values (47 -> 46)
  rather than a value pulled from elsewhere, because a neighbouring number is
  what a real typo produces, and it is the case a naive string diff cannot
  separate from legitimate variation. Every Chinese-numeral replacement is
  round-tripped through the codec before use.
* **Ordinals are filtered.** A naive numeral scan reads 第二天 ("the next day")
  as the count 二天. The scanner checks the preceding character and routes 第X
  into its own class instead of polluting the count dataset.
* **Both numeral scripts.** The corpus writes Chinese numerals most of the time
  but switches to arabic for ordinals and timestamps (第18轮, 第9位, 3 秒), so
  scanning only one script would silently drop a large part of the inventory.
* **Two anchoring modes.** A mutated value corroborated by a declared canon fact
  is checkable against `continuity/canon-facts.jsonl`; one that only recurs in
  prose is checkable only by manuscript retrieval. These are different claims,
  so they are labelled separately rather than merged.
* **Writes only into a scratch copy**, never into the source project.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import random
import re
import shutil
import sys
from typing import Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import numeral  # noqa: E402

inject_module = sys.modules[__name__]  # the except-clause below needs a stable name

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = 1
LABEL_KIND = "contradiction_injection"

CHAPTER_DIR = ("manuscript", "chapters")
FACTS_FILE = ("continuity", "canon-facts.jsonl")
COPY_SKIP = {"staging", "exports", "research", "sources", "revisions", ".novel-cache"}

AMOUNT_UNITS = ("万", "亿", "元")
DURATION_UNITS = ("个月", "小时", "分钟", "年", "天", "秒", "分", "周", "日")
DATE_PART_UNITS = ("月", "号")
COUNT_UNITS = (
    "个", "户", "次", "条", "张", "份", "笔", "位", "人", "轮", "层",
    "名", "页", "封", "件", "盏", "座", "招", "道", "把", "根", "只", "本", "枚",
)
# Longest-first so 个月 is matched before 月 and 分钟 before 分.
ALL_UNITS = tuple(sorted(set(AMOUNT_UNITS + DURATION_UNITS + DATE_PART_UNITS + COUNT_UNITS),
                        key=len, reverse=True))

GENERIC = {
    "一个", "一次", "一位", "一张", "一条", "一天", "两个", "三个", "一层", "一份",
    "一笔", "一户", "一人", "几个", "某个", "每个", "整个", "半个", "一些", "一样",
    "一定", "一起", "一直", "一般", "一切", "一面", "一边", "一道", "一段", "一刻",
    "一眼", "一声", "一会儿", "一瞬", "一年", "两天",
}

FULL_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
CN_TOKEN_RE = re.compile(
    rf"([{re.escape(numeral.NUMERAL_CHARS)}]{{1,10}})({'|'.join(re.escape(u) for u in ALL_UNITS)})"
)
AR_TOKEN_RE = re.compile(
    rf"(?<!\d)(\d{{1,6}})\s*({'|'.join(re.escape(u) for u in ALL_UNITS)})"
)

BUCKETS = ((0, 0, "0"), (1, 5, "1-5"), (6, 20, "6-20"), (21, 10 ** 9, "21+"))
CROSS_CHAPTER = "chapter_consistent"
INTRA_CHAPTER = "single_occurrence"
FACT_ANCHOR = "fact_anchor"


def bucket_for(distance: int) -> str:
    for low, high, name in BUCKETS:
        if low <= distance <= high:
            return name
    raise AssertionError("unreachable")


def split_token(token: str) -> tuple[str, str, str]:
    """Split a surface token into (marker, numeral_text, unit)."""
    marker = ""
    rest = token
    for candidate in ("民国", "第"):
        if rest.startswith(candidate):
            marker, rest = candidate, rest[len(candidate):]
            break
    for unit in ALL_UNITS:
        if rest.endswith(unit):
            # rstrip: a corpus that writes "3 秒" would otherwise leave the
            # separating space inside numeral_text and lose it on rebuild.
            return marker, rest[: -len(unit)].rstrip(), unit
    return marker, rest, ""


@dataclasses.dataclass
class Occ:
    chapter: str
    path: pathlib.Path
    start: int
    end: int
    token: str
    klass: str
    script: str          # "cn" | "ar"
    value: int           # number of the span (day-of-month for a full date)
    date_parts: tuple[int, int, int] | None = None


def classify(marker: str, numeral_text: str, unit: str, script: str) -> str | None:
    token = marker + numeral_text + unit
    if token in GENERIC:
        return None
    if marker == "第":
        return "ordinal"
    if marker == "民国":
        return "era_time"
    if unit == "年" and script == "ar":
        return "year"
    if unit == "月" and script == "ar":
        return None
    if unit in AMOUNT_UNITS:
        return "amount"
    if script == "cn" and len(numeral_text) == 1 and numeral_text in ("一", "两", "几", "半", "某", "每"):
        return None
    if unit in DURATION_UNITS:
        return "duration"
    if unit in DATE_PART_UNITS:
        return "date_part"
    if unit in COUNT_UNITS:
        return "count"
    return None


def _spanned(match_start: int, match_end: int, text: str, numeral_text: str, unit: str,
             script: str) -> tuple[int, int, str, str]:
    """Extend a span to include its 第/民国 marker. Returns (start, end, token, marker)."""
    start, marker = match_start, ""
    if script == "cn" and start > 0 and text[start - 1] == "第":
        start, marker = start - 1, "第"
    elif script == "cn" and text[max(0, start - 2):start] == "民国":
        start, marker = start - 2, "民国"
    elif script == "ar" and start > 0 and text[start - 1] == "第":
        start, marker = start - 1, "第"
    return start, match_end, marker + numeral_text + unit, marker


def read_chapters(root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    d = root.joinpath(*CHAPTER_DIR)
    return [(p.name.split("-")[0], p) for p in sorted(d.glob("*.md"))]


def scan_text(text: str, chapter: str, path: pathlib.Path,
              keep_nested: bool = False) -> list[Occ]:
    """Scan one text into de-overlapped value occurrences.

    Matches are collected from three patterns and then de-overlapped with a
    longest-match-wins pass. Without that, "四万元" yields both 四万元 and the
    nested 万元, and "1946年3月15日" yields the date plus a nested year token;
    two plans would then mutate overlapping spans and each would corrupt the
    other's artifact.

    keep_nested=True skips that suppression: on *declared fact* text a nested
    token is an independent claim (二千一百四十七户 and the 四十七户 inside it
    are two different assertions), so the detector wants both.
    """
    candidates: list[tuple[int, int, str, str, str, int, tuple[int, int, int] | None]] = []

    for m in FULL_DATE_RE.finditer(text):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        candidates.append((m.start(), m.end(), "date", "ar", m.group(0), day, (year, month, day)))

    for m in CN_TOKEN_RE.finditer(text):
        numeral_text, unit = m.group(1), m.group(2)
        start, end, token, marker = _spanned(m.start(), m.end(), text, numeral_text, unit, "cn")
        klass = classify(marker, numeral_text, unit, "cn")
        value = numeral.parse_cn(numeral_text)
        if klass is not None and value is not None:
            candidates.append((start, end, klass, "cn", token, value, None))

    for m in AR_TOKEN_RE.finditer(text):
        number_text, unit = m.group(1), m.group(2)
        # A space between numeral and unit is common in this corpus ("3 秒"),
        # so preserve it rather than normalising it away.
        gap = text[m.start(1) + len(number_text):m.start(2)]
        start, end, _, marker = _spanned(m.start(1), m.end(), text, number_text, unit, "ar")
        token = f"{marker}{number_text}{gap}{unit}"
        klass = classify(marker, number_text, unit, "ar")
        if klass is not None:
            candidates.append((start, end, klass, "ar", token, int(number_text), None))

    if not keep_nested:
        candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))
        accepted: list[tuple] = []
        last_end = -1
        for cand in candidates:
            if cand[0] < last_end:      # overlaps a match already kept
                continue
            accepted.append(cand)
            last_end = cand[1]
        candidates = accepted

    return [
        Occ(chapter, path, start, end, token, klass, script, value, date_parts)
        for start, end, klass, script, token, value, date_parts in candidates
    ]


def scan_occurrences(root: pathlib.Path) -> dict[str, list[Occ]]:
    """token -> every occurrence in the manuscript."""
    by_token: dict[str, list[Occ]] = {}
    for number, path in read_chapters(root):
        for occ in scan_text(path.read_text(encoding="utf-8"), number, path):
            by_token.setdefault(occ.token, []).append(occ)
    return by_token


def load_canon_fact_text(root: pathlib.Path) -> dict[str, str]:
    path = root.joinpath(*FACTS_FILE)
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        out[rec.get("fact_id", "?")] = " ".join(
            str(rec.get(k, "")) for k in ("subject", "predicate", "object")
        )
    return out


@dataclasses.dataclass
class Plan:
    token: str
    klass: str
    mode: str
    chapter: str
    occurrences: list[Occ]
    distance: int      # optimistic: distance assuming every other chapter survives

    @property
    def bucket(self) -> str:
        if self.mode == FACT_ANCHOR:
            return "fact-only"
        return bucket_for(self.distance)


def build_plans(by_token: dict[str, list[Occ]], fact_texts: list[str]) -> list[Plan]:
    plans: list[Plan] = []
    for token, occs in by_token.items():
        klass = occs[0].klass
        per_chapter: dict[str, list[Occ]] = {}
        for o in occs:
            per_chapter.setdefault(o.chapter, []).append(o)
        chapters = sorted(per_chapter)

        # (a) intra-chapter: a chapter repeats the token; mutate one occurrence
        #     and leave its neighbours, so the chapter contradicts itself at
        #     distance 0.
        for chapter in chapters:
            group = sorted(per_chapter[chapter], key=lambda o: o.start)
            if len(group) >= 2:
                plans.append(Plan(token, klass, INTRA_CHAPTER, chapter, [group[0]], 0))

        # (b) cross-chapter: rewrite every occurrence inside one chapter, leaving
        #     the other chapters as the surviving truth.
        if len(chapters) >= 2:
            for chapter in chapters:
                others = [c for c in chapters if c != chapter]
                plans.append(Plan(token, klass, CROSS_CHAPTER, chapter,
                                  sorted(per_chapter[chapter], key=lambda o: o.start),
                                  min(abs(int(chapter) - int(c)) for c in others)))

        # (c) fact-anchored: the token's prose presence is confined to a single
        #     chapter and a declared fact asserts the same value. Mutating it
        #     leaves the fact store as the ONLY surviving truth -- the one
        #     contradiction class a prose-only checker cannot see by
        #     construction, and therefore the cell that measures what the
        #     declared-canon channel uniquely buys.
        if len(chapters) == 1 and any(token in blob for blob in fact_texts):
            chapter = chapters[0]
            plans.append(Plan(token, klass, FACT_ANCHOR, chapter,
                              sorted(per_chapter[chapter], key=lambda o: o.start), 0))
    return plans


def nearby(value: int, rng: random.Random, low: int = 1, high: int | None = None) -> int | None:
    deltas = [1, -1, 2, -2, 3, -3]
    rng.shuffle(deltas)
    for delta in deltas:
        candidate = value + delta
        if candidate < low:
            continue
        if high is not None and candidate > high:
            continue
        return candidate
    return None


def make_replacement(plan: Plan, rng: random.Random) -> str | None:
    """Build the replacement surface token, or None when this plan cannot mutate."""
    first = plan.occurrences[0]
    marker, numeral_text, unit = split_token(first.token)

    if plan.klass == "date":
        year, month, day = first.date_parts or (0, 0, 0)
        chosen = nearby(day, rng, low=1, high=28)
        if chosen is None or chosen == day:
            return None
        return f"{year}年{month}月{chosen}日"

    new_value = nearby(first.value, rng, low=1, high=12 if plan.klass == "date_part" else None)
    if new_value is None or new_value == first.value:
        return None

    if first.script == "ar":
        gap = first.token[len(marker) + len(numeral_text): len(first.token) - len(unit)]
        return f"{marker}{new_value}{gap}{unit}"

    rendered = numeral.render_cn(new_value, use_liang="两" in numeral_text)
    if rendered is None or numeral.parse_cn(rendered) != new_value:
        return None
    return f"{marker}{rendered}{unit}"


class OverlappingReplacement(ValueError):
    """Raised when rewrite_file is asked to apply spans that overlap."""


def rewrite_file(text: str, replacements: list[tuple[int, int, str]]) -> tuple[str, list[int]]:
    """Apply non-overlapping replacements in one left-to-right pass.

    Returns the new text and, for each replacement in input order, its start
    offset in the NEW text. Working from original coordinates in a single pass --
    instead of find-and-replacing incrementally -- is what keeps every recorded
    span valid no matter how the other replacements change the file's length.
    """
    replacements = sorted(replacements, key=lambda r: r[0])
    for (_, prev_end, _), (next_start, _, _) in zip(replacements, replacements[1:]):
        if prev_end > next_start:
            raise OverlappingReplacement(
                f"spans overlap: ...{prev_end}] and [{next_start}...")
    parts: list[str] = []
    final_starts: list[int] = []
    last = 0
    shift = 0
    for start, end, new_token in replacements:
        parts.append(text[last:start])
        final_starts.append(start + shift)
        parts.append(new_token)
        shift += len(new_token) - (end - start)
        last = end
    parts.append(text[last:])
    return "".join(parts), final_starts


def _select_plans(plans: list[Plan], per_cell: int,
                  chapter_totals: dict[str, int]) -> list[Plan]:
    """Pick a stratified, deterministic sample of mutation plans.

    Caps per (class, distance-bucket) cell so the dataset is not dominated by
    whichever token happens to be most frequent, and refuses any cross-chapter
    plan that would consume a token's last chapter.
    """
    plans.sort(key=lambda p: (p.klass, p.bucket, p.token, p.chapter, p.mode))
    chosen: list[Plan] = []
    per_cell_used: dict[tuple[str, str], int] = {}
    seen_sites: set[tuple[str, str]] = set()
    cross_used: dict[str, set[str]] = {}

    for plan in plans:
        cell = (plan.klass, plan.bucket)
        if per_cell_used.get(cell, 0) >= per_cell:
            continue
        site = (plan.token, plan.chapter)
        if site in seen_sites:          # one mutation site per token per chapter
            continue
        if plan.mode == CROSS_CHAPTER:
            # Never consume a token's last chapter: that would delete the
            # surviving truth and leave nothing to contradict.
            used = cross_used.setdefault(plan.token, set())
            if len(used) + 1 >= chapter_totals.get(plan.token, 1):
                continue
            used.add(plan.chapter)
        seen_sites.add(site)
        per_cell_used[cell] = per_cell_used.get(cell, 0) + 1
        chosen.append(plan)
    return chosen


def inject_project(source: pathlib.Path, out_root: pathlib.Path, seed: int, per_cell: int,
                   classes: set[str] | None, buckets: set[str] | None) -> tuple[pathlib.Path, list[dict]]:
    scratch = out_root / source.name
    if scratch.exists():
        shutil.rmtree(scratch)
    shutil.copytree(source, scratch, ignore=shutil.ignore_patterns(*COPY_SKIP))

    rng = random.Random(seed)
    by_token = scan_occurrences(source)
    chapter_totals = {token: len({o.chapter for o in occs}) for token, occs in by_token.items()}
    fact_text = load_canon_fact_text(source)

    plans = build_plans(by_token, list(fact_text.values()))
    if classes:
        plans = [p for p in plans if p.klass in classes]
    if buckets:
        plans = [p for p in plans if p.bucket in buckets]
    chosen = _select_plans(plans, per_cell, chapter_totals)

    # One pass per file, from original coordinates. Grouping by file is what
    # makes the single-pass rewrite possible; an incremental find-and-replace
    # invalidates the spans of earlier injections whenever a later one changes
    # the file's length, and can re-match inside text an earlier injection wrote.
    by_file: dict[pathlib.Path, list[Plan]] = {}
    for plan in chosen:
        by_file.setdefault(plan.occurrences[0].path, []).append(plan)

    pending: dict[pathlib.Path, str] = {}
    records: list[dict] = []
    for path, file_plans in by_file.items():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(source).as_posix()
        target = scratch.joinpath(*pathlib.PurePosixPath(rel).parts)

        replacements: list[tuple[int, int, str, Plan]] = []
        for plan in sorted(file_plans, key=lambda p: (p.occurrences[0].start, p.token)):
            new_token = make_replacement(plan, rng)
            if new_token is None or new_token == plan.occurrences[0].token:
                continue
            for occ in plan.occurrences:      # original coordinates, no re-resolution
                replacements.append((occ.start, occ.end, new_token, plan))
        if not replacements:
            continue
        # Sort into the same order rewrite_file reports offsets in; the plan-wise
        # append order interleaves differently once a plan owns several spans.
        replacements.sort(key=lambda r: r[0])

        try:
            new_text, final_starts = rewrite_file(
                text, [(s, e, t) for s, e, t, _ in replacements])
        except inject_module.OverlappingReplacement:
            continue      # defensive: overlapping plans are skipped, never corrupted

        pending[target] = new_text
        for (start, end, new_token, plan), final_start in zip(replacements, final_starts):
            records.append({
                "plan": plan,
                "rel": rel,
                "injected": new_token,
                "start": final_start,
                "mutated": 1,
            })

    for path, text in pending.items():
        path.write_text(text, encoding="utf-8")

    # Invariant 1 + 3: surviving truth is counted per occurrence, and a label with
    # no surviving truth is dropped rather than recorded with a fictional one.
    mutated_per_chapter: dict[str, dict[str, int]] = {}
    for rec in records:
        token, chapter = rec["plan"].token, rec["plan"].chapter
        mutated_per_chapter.setdefault(token, {})
        mutated_per_chapter[token][chapter] = (
            mutated_per_chapter[token].get(chapter, 0) + rec["mutated"]
        )

    labels: list[dict] = []
    for rec in records:
        plan = rec["plan"]
        token = plan.token
        fact_ids = sorted(fid for fid, blob in fact_text.items() if token in blob)

        if plan.mode == FACT_ANCHOR:
            # The prose truth was deliberately wiped out: the declared fact store
            # is the surviving counter-side. distance and prose counts do not
            # apply to this cell.
            truth = {"source": "fact", "chapters": [], "distance_chapters": None,
                     "occurrences_remaining": 0}
            bucket = "fact-only"
            detectable = ["canon_store"]
        else:
            total_per_chapter: dict[str, int] = {}
            for occ in by_token[token]:
                total_per_chapter[occ.chapter] = total_per_chapter.get(occ.chapter, 0) + 1
            surviving = {
                chapter: count - mutated_per_chapter.get(token, {}).get(chapter, 0)
                for chapter, count in total_per_chapter.items()
            }
            truth_chapters = sorted(c for c, left in surviving.items() if left > 0)
            if not truth_chapters:
                continue
            distance = min(abs(int(plan.chapter) - int(c)) for c in truth_chapters)
            truth = {"source": "prose", "chapters": truth_chapters,
                     "distance_chapters": distance,
                     "occurrences_remaining": sum(surviving[c] for c in truth_chapters)}
            bucket = bucket_for(distance)
            detectable = ["intra_chapter"] if distance == 0 else ["cross_chapter_retrieval"]
            if fact_ids:
                detectable.append("canon_store")

        text = pending[scratch.joinpath(*pathlib.PurePosixPath(rec["rel"]).parts)]
        start = rec["start"]
        labels.append({
            "schema_version": SCHEMA_VERSION,
            "label_kind": LABEL_KIND,
            "label_id": f"L-{len(labels) + 1:06d}",
            "project_id": source.name,
            "class": plan.klass,
            "mode": plan.mode,
            "script": plan.occurrences[0].script,
            "file": rec["rel"],
            "chapter": plan.chapter,
            "original": token,
            "injected": rec["injected"],
            "occurrences_mutated": rec["mutated"],
            # span locates the injected token in the injected file, so a reviewer
            # can jump straight from a label to the bytes it describes.
            "span": [start, start + len(rec["injected"])],
            "context": text[max(0, start - 24): start + len(rec["injected"]) + 24],
            "anchor": "canon" if fact_ids else "prose",
            "canon_fact_ids": fact_ids,
            "truth": truth,
            "distance_bucket": bucket,
            "detectable_by": detectable,
            "seed": seed,
        })
    return scratch, labels


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="inject", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="Source novel project (read-only).")
    p.add_argument("--out", required=True, help="Scratch output directory.")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--per-cell", type=int, default=3,
                   help="Max injections per (class, distance bucket) cell.")
    p.add_argument("--classes", help="Comma-separated class allowlist.")
    p.add_argument("--buckets", help="Comma-separated distance-bucket allowlist.")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = pathlib.Path(args.project)
    if not source.is_dir():
        print(json.dumps({"status": "error", "error": f"not a directory: {source}",
                          "error_type": "usage"}, ensure_ascii=False))
        return 2

    out_root = pathlib.Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    scratch, labels = inject_project(
        source, out_root, args.seed, args.per_cell,
        set(args.classes.split(",")) if args.classes else None,
        set(args.buckets.split(",")) if args.buckets else None,
    )

    labels_path = out_root / f"labels-{source.name}.jsonl"
    with labels_path.open("w", encoding="utf-8") as fh:
        for label in labels:
            fh.write(json.dumps(label, ensure_ascii=False, sort_keys=True) + "\n")

    summary: dict = {
        "status": "ok",
        "project_id": source.name,
        "scratch": str(scratch),
        "labels_file": str(labels_path),
        "injections": len(labels),
        "seed": args.seed,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "by_class": {}, "by_bucket": {}, "by_anchor": {},
    }
    for label in labels:
        for key, field in (("by_class", "class"), ("by_bucket", "distance_bucket"),
                           ("by_anchor", "anchor")):
            summary[key][label[field]] = summary[key].get(label[field], 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())