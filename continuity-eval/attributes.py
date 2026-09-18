# -*- coding: utf-8 -*-
"""Name-attribute contradiction channel: birth/death claims bound to people.

Why this exists as a separate channel
-------------------------------------
The value-contradiction detector compares values of the same *kind* within a
small numeric distance (MAX_VALUE_DISTANCE). That is the right tool for a
mistyped count, and exactly the wrong tool for the most damaging real-world
contradictions: a character recorded as dying in 1978 in one chapter and in 2024
in the canon is not a "near value" -- it is a 46-year gap on a singular
attribute. Singular attributes (birth, death) follow a different rule: any two
distinct values for the same person are a contradiction, however far apart.

What it does
------------
* Names come from the declared canon (the subjects of `canon-facts.jsonl`), so
  binding is anchored to the cast the author already maintains.
* Prose claims: for every birth/death word occurrence, the nearest date token
  within a small radius and the nearest name within a larger radius form a claim
  (name, attribute, year). Attr-centric binding (word first, then date, then
  name) handles both "张某1978年6月过世" and the registry form
  "死亡日期：1978年...".
* Fact claims: a declared fact is by construction about its subject, so its
  object text is scanned with the subject bound directly -- no radius game.
* Any two distinct values for one (name, attribute) are a finding.

Out of the benchmark, deliberately: the injector does not generate this
contradiction class, so there is nothing to score it against. This channel is
the second column of the evaluation -- validated against contradictions the
author already knows about, not against injections. Its findings go to the
author for adjudication, matching the advisory-only invariant.

Stdlib only. Never writes to the project it reads.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import re
import sys
from typing import Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import detect  # noqa: E402
import inject  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = 1
FINDING_KIND = "attribute_conflict"
THREADS_FILE = ("continuity", "threads.md")

# Multi-char attr words only: single 生/死 match 生活, 死账, 该死 and would
# bury the signal. 过世/去世/死亡/死于/殁/病故 are unambiguous in this corpus.
DEATH_WORDS = ("过世", "去世", "死亡", "死于", "殁", "病故")
BIRTH_WORDS = ("出生", "生于", "诞生")

DATE_RADIUS = 12     # attr word <-> date token
FALLBACK_RADIUS = 25  # attr word <-> declared-name fallback (left side only)

# Registry and dialogue phrasing put the subject directly before the date
# ("林某人1978年6月过世", ""李某，1924年出生""). Binding prefers that local
# subject and only falls back to the nearest declared name -- the previous
# lexicon-only binding attributed other people's dates to whoever was nearby
# (the listener 江某, the prop 账簿), which is worse than useless in a review
# list because it looks authoritative.
_SKIP_TEXT = (
    "，。：；！？、·「」《》（）—…"
    "\u201c\u201d\u2018\u2019"    # curly quotes, written escaped so the
    " \n\t\"'"                     # string quoting stays unambiguous
)
SKIP_CHARS = set(_SKIP_TEXT)
# 左边界字：收集局部主语时在这里停止（「确定是李某」停在「是」而不是作废）。
# 年月日也在内，避免把日期残片收进名字。
SUBJECT_STOP = set("说道问想看回的是在了他她我你个和与把被将又也都不没就还这那有很年月日号")
ROLE_SUFFIXES = ("持有人", "债务人", "账房", "老板", "警察", "医生", "律师", "老人",
                 "儿子", "母亲", "父亲", "女儿", "妻子", "丈夫", "孙女", "孙子",
                 "护士")
ATTR_WORDS = DEATH_WORDS + BIRTH_WORDS + ("日期",)


def local_subject(text: str, pos: int) -> str | None:
    """The 2-4 char name-like run immediately before `pos`, or None."""
    index = pos
    while index > 0 and text[index - 1] in SKIP_CHARS:
        index -= 1
    chars = []
    while index > 0 and len(chars) < 4:
        ch = text[index - 1]
        if not ("\u4e00" <= ch <= "\u9fff") or ch in SUBJECT_STOP:
            break
        chars.insert(0, ch)
        index -= 1
    name = "".join(chars)
    if not 2 <= len(name) <= 4:
        return None
    if any(name.endswith(suffix) for suffix in ROLE_SUFFIXES):
        return None
    if any(word in name for word in ATTR_WORDS):
        return None
    return name


def choose_date(text: str, attr_start: int, attr_end: int,
                dated: list[tuple[int, inject.Occ]]) -> tuple[int, inject.Occ] | None:
    """The date this attr word is actually about.

    Distance alone picks the wrong partner whenever a second date sits just
    across a comma ("1922年出生，2024年在世" -- 2024年 is *closer* to 出生 by
    start-offset). The ranking prefers the date with the smallest gap to the
    attr word, and a gap containing CJK text (other words intervening)
    disqualifies the pair before distance is even considered.
    """
    best: tuple[int, inject.Occ] | None = None
    best_key: tuple[bool, int, int] | None = None
    for year, occ in dated:
        if occ.end <= attr_start:
            gap = text[occ.end:attr_start]
        elif occ.start >= attr_end:
            gap = text[attr_end:occ.start]
        else:
            continue
        if len(gap) > DATE_RADIUS:
            continue
        # 「死亡日期：1978年」的属性词只匹配到「死亡」，尾巴「日期」留在间隙里；
        # 间隙里出现属性词残余不算正文阻隔，剥掉再判。
        cleaned = gap
        for word in ATTR_WORDS:
            cleaned = cleaned.replace(word, "")
        # 平局偏向属性词之后的日期：registry 句式里日期在词后，而前一个日期
        # 往往隔着句号贴得更近。
        key = (any("\u4e00" <= ch <= "\u9fff" for ch in cleaned), len(cleaned),
               0 if occ.start >= attr_end else 1)
        if best_key is None or key < best_key:
            best, best_key = (year, occ), key
    return best


def person_names(root: pathlib.Path) -> set[str]:
    """The declared cast, from two canon sources.

    Fact subjects that look like people, plus the 知情者 column of
    `continuity/threads.md` -- the author maintains that table per thread, and it
    carries names the facts never need as subjects (林韵清 appears only inside
    other facts' objects). Prop subjects (账簿, 孙家手账) are excluded: they are
    legitimate fact owners but can die in no sense, and keeping them manufactures
    findings where a sentence merely mentions the ledger near a death date.
    """
    names = set()
    for fact in detect.load_facts(root):
        subject = str(fact.get("subject", "")).strip()
        if not subject or _is_prop(subject):
            continue
        names.add(subject)

    threads = root.joinpath(*THREADS_FILE)
    if threads.is_file():
        for line in threads.read_text(encoding="utf-8").splitlines():
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) < 5:
                continue
            for candidate in cells[4].split("、"):     # the 知情者 column
                name = candidate.split("（")[0].strip()
                if 2 <= len(name) <= 6 and not _is_prop(name):
                    names.add(name)
    return names


def _is_prop(name: str) -> bool:
    return (any(ch in name for ch in "簿账册表单匣")
            or any(name.endswith(suffix) for suffix in ROLE_SUFFIXES))


def year_of(occ: inject.Occ) -> int | None:
    """The year a date-like occurrence asserts, for cross-claim comparison."""
    if occ.klass == "date" and occ.date_parts:
        return occ.date_parts[0]
    if occ.klass == "year":
        return occ.value
    return None


def claims_from_text(text: str, path: pathlib.Path, names: set[str],
                     subject: str | None = None) -> list[dict]:
    """Claims in one text. With `subject`, every claim binds to it (fact mode);
    otherwise the nearest lexicon name inside FALLBACK_RADIUS binds the claim."""
    occurrences = inject.scan_text(text, path.name.split("-")[0] if subject is None else "fact",
                                   path, keep_nested=True)
    dated = [(year_of(o), o) for o in occurrences if year_of(o) is not None]

    claims: list[dict] = []
    for word, kind in [(w, "death") for w in DEATH_WORDS] + \
                      [(w, "birth") for w in BIRTH_WORDS]:
        for m in re.finditer(re.escape(word), text):
            chosen = choose_date(text, m.start(), m.end(), dated)
            if chosen is None:
                continue
            year, occ = chosen

            if subject is not None:
                bound = subject
            else:
                # preferred: the name sitting right before the date; fallback:
                # the nearest declared name within FALLBACK_RADIUS
                bound = local_subject(text, occ.start)
                if bound is None:
                    # fallback: nearest declared name strictly to the LEFT. The
                    # right side of an attr word is dialogue attribution (the
                    # listener), and binding deaths to the listener is exactly
                    # the misattribution this channel must not produce.
                    left = text[max(0, m.start() - FALLBACK_RADIUS):m.start()]
                    candidates = [n for n in names if n in left]
                    if not candidates:
                        continue
                    bound = max(candidates, key=lambda n: left.rfind(n))
            claims.append({
                "name": bound, "attribute": kind, "value": year,
                "file": None,
                "span": [occ.start, occ.end], "token": occ.token, "attr_word": word,
                "quote": text[max(0, m.start() - 20):m.end() + 20],
            })
    return claims


def _unique(claims: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for claim in claims:
        key = (claim["name"], claim["attribute"], claim["value"],
               claim.get("file"), tuple(claim["span"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
    return out


def attribute_findings(root: pathlib.Path) -> list[dict]:
    names = person_names(root)
    if not names:
        return []

    prose_claims: list[dict] = []
    for _, path in inject.read_chapters(root):
        for claim in claims_from_text(path.read_text(encoding="utf-8"), path, names):
            claim["file"] = path.relative_to(root).as_posix()
            claim["source"] = "prose"
            prose_claims.append(claim)
    prose_claims = _unique(prose_claims)

    fact_claims: list[dict] = []
    for fact in detect.load_facts(root):
        subject = str(fact.get("subject", "")).strip()
        blob = " ".join(str(fact.get(k, "")) for k in ("predicate", "object"))
        for claim in claims_from_text(blob, root / "continuity" / "canon-facts.jsonl",
                                      names, subject=subject):
            claim["file"] = "continuity/canon-facts.jsonl"
            claim["source"] = "fact"
            claim["fact_id"] = fact.get("fact_id", "?")
            fact_claims.append(claim)
    fact_claims = _unique(fact_claims)

    grouped: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for claim in prose_claims + fact_claims:
        grouped[(claim["name"], claim["attribute"])].append(claim)

    findings = []
    for (name, attribute), claims in sorted(grouped.items()):
        values = sorted({c["value"] for c in claims})
        if len(values) < 2:
            continue
        findings.append({
            "schema_version": SCHEMA_VERSION,
            "finding_kind": FINDING_KIND,
            "project_id": root.name,
            "severity": "error",
            "name": name,
            "attribute": attribute,
            "values": values,
            "claims": sorted(claims, key=lambda c: (c.get("file") or "", c["span"][0])),
        })
    return findings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="attributes", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="Project root to scan (read-only).")
    p.add_argument("--output", help="Write findings JSONL here (default: stdout).")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = pathlib.Path(args.project)
    if not root.is_dir():
        print(json.dumps({"status": "error", "error": f"not a directory: {root}",
                          "error_type": "usage"}, ensure_ascii=False))
        return 2

    findings = attribute_findings(root)
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
        "names": [{ "name": f["name"], "attribute": f["attribute"], "values": f["values"]}
                  for f in findings],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())