# -*- coding: utf-8 -*-
"""Verify an injected dataset against its source corpus and snapshot.

Unit tests run against a synthetic fixture; this is the same integrity contract
checked against the real corpus, where a silent failure would poison every
downstream metric. Three things are checked:

A. **The source corpus is untouched.** Every project re-hashes to exactly what
   the pinned snapshot recorded. This is what makes "the injector only writes to
   a scratch copy" a measured fact rather than a promise.
B. **Every label describes the artifact it claims to describe** -- the span in
   the injected file really holds the injected token.
C. **Every label still has a contradiction** -- the surviving truth occurrences
   really do still carry the original value, at the recorded distance.

Exit code 0 when everything holds, 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Iterable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BUCKETS = ((0, 0, "0"), (1, 5, "1-5"), (6, 20, "6-20"), (21, 10 ** 9, "21+"))


def bucket_for(distance: int) -> str:
    for low, high, name in BUCKETS:
        if low <= distance <= high:
            return name
    raise AssertionError("unreachable")


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_source_integrity(projects_root: pathlib.Path, snapshot: dict) -> list[str]:
    problems: list[str] = []
    for pid, recorded in snapshot["projects"].items():
        root = projects_root / pid
        for entry in recorded["files"]:
            path = root.joinpath(*pathlib.PurePosixPath(entry["path"]).parts)
            if not path.is_file():
                problems.append(f"{pid}: canonical file missing: {entry['path']}")
                continue
            actual = sha256_file(path)
            if actual != entry["sha256"]:
                problems.append(
                    f"{pid}: CANONICAL FILE CHANGED since snapshot: {entry['path']}"
                )
    return problems


def check_labels(injected_root: pathlib.Path, labels_path: pathlib.Path) -> tuple[list[str], int]:
    problems: list[str] = []
    count = 0
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        count += 1
        label = json.loads(line)
        lid = label.get("label_id", "?")

        injected = label.get("injected", "")
        original = label.get("original", "")
        if injected == original:
            problems.append(f"{lid}: injected equals original")
        start, end = label.get("span", [-1, -1])
        if end - start != len(injected):
            problems.append(f"{lid}: span length {end-start} != len(injected) {len(injected)}")

        path = injected_root.joinpath(*pathlib.PurePosixPath(label["file"]).parts)
        if not path.is_file():
            problems.append(f"{lid}: injected file missing: {label['file']}")
            continue
        text = path.read_text(encoding="utf-8")
        if not (0 <= start <= end <= len(text)):
            problems.append(f"{lid}: span {label['span']} out of range")
        elif text[start:end] != injected:
            problems.append(f"{lid}: span does not hold injected token: {text[start:end]!r}")

        truth = label.get("truth", {})
        if truth.get("source") == "fact":
            # fact-anchored cell: the prose truth was wiped out by design and the
            # declared canon store is the surviving counter-side, so verify the
            # fact file still asserts the original value and move on. Note
            # injected_root here IS the project directory (see caller).
            facts_path = injected_root / "continuity" / "canon-facts.jsonl"
            if not facts_path.is_file() or original not in facts_path.read_text(encoding="utf-8"):
                problems.append(f"{lid}: fact truth does not assert the original token")
            continue

        chapters = truth.get("chapters", [])
        remaining = truth.get("occurrences_remaining", 0)
        if remaining <= 0 or not chapters:
            problems.append(f"{lid}: no surviving truth recorded")
            continue
        chapter_dir = path.parent
        found = 0
        for chapter in chapters:
            for f in chapter_dir.glob(f"{chapter}-*.md"):
                found += f.read_text(encoding="utf-8").count(original)
        if found < remaining:
            problems.append(
                f"{lid}: surviving truth count {found} < recorded {remaining}"
            )

        distance = min(abs(int(label["chapter"]) - int(c)) for c in chapters)
        if truth.get("distance_chapters") != distance:
            problems.append(f"{lid}: distance {truth.get('distance_chapters')} != true min {distance}")
        if label.get("distance_bucket") != bucket_for(distance):
            problems.append(f"{lid}: bucket {label.get('distance_bucket')} != {bucket_for(distance)}")
    return problems, count


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="verify_dataset", description=__doc__)
    p.add_argument("--projects-root", required=True)
    p.add_argument("--snapshot", required=True, help="Pinned corpus snapshot JSON.")
    p.add_argument("--injected-root", required=True,
                   help="Directory holding labels-*.jsonl and the scratch copies.")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    projects_root = pathlib.Path(args.projects_root)
    snapshot = json.loads(pathlib.Path(args.snapshot).read_text(encoding="utf-8"))
    injected_root = pathlib.Path(args.injected_root)

    report: dict = {"status": "ok", "source_integrity": [], "datasets": {}, "problems": 0}

    problems = check_source_integrity(projects_root, snapshot)
    report["source_integrity"] = problems
    report["problems"] += len(problems)

    for labels_path in sorted(injected_root.glob("labels-*.jsonl")):
        pid = labels_path.stem.removeprefix("labels-")
        problems, count = check_labels(injected_root / pid, labels_path)
        report["datasets"][pid] = {"labels": count, "problems": problems}
        report["problems"] += len(problems)

    if report["problems"]:
        report["status"] = "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())