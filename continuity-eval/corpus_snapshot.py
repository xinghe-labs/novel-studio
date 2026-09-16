# -*- coding: utf-8 -*-
"""Pin a corpus snapshot: hash every canonical file of each novel project.

The evaluation harness must record which exact bytes it measured. This mirrors
novel-studio's canonical_snapshot idea but is deliberately standalone: it has to
work on injected *scratch* copies too, which are not registered projects.

Read-only. Writes only the manifest it is asked to write.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import stat
import sys
from typing import Iterable

REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = 1
MANIFEST_KIND = "corpus_snapshot"

# Directories whose *.md files form the canonical corpus we care about.
CHAPTER_DIR = ("manuscript", "chapters")
MEMORY_CHAPTER_DIR = ("memory", "chapters")

# Individual canonical files, mapped to a kind label for reporting.
SINGLE_FILES = {
    ("novel.json",): "novel_meta",
    ("continuity", "canon-facts.jsonl"): "canon_facts",
    ("continuity", "intentional-exceptions.jsonl"): "canon_exceptions",
    ("continuity", "state.json"): "continuity_state",
    ("continuity", "threads.md"): "threads",
    ("continuity", "timeline.md"): "timeline",
    ("continuity", "head.json"): "continuity_head",
    ("memory", "book-summary.md"): "book_summary",
    ("memory", "decisions.md"): "decisions",
    ("manuscript", "index.md"): "manuscript_index",
}

SKIP_DIRS = {"staging", "exports", "research", "sources", "revisions", "reviews", ".novel-cache"}


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())


def _is_linklike(path: pathlib.Path) -> bool:
    """Refuse symlinks and Windows reparse points (junctions).

    novel-studio's canonical snapshot refuses these too; a pinned snapshot that
    silently follows a link is not pinning what we think it is.
    """
    try:
        if path.is_symlink():
            return True
        st = path.lstat()
        return bool(getattr(st, "st_file_attributes", 0) & REPARSE_POINT)
    except OSError:
        return False


def project_files(root: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Return (absolute path, kind) for every canonical file present."""
    found: list[tuple[pathlib.Path, str]] = []
    for parts, kind in SINGLE_FILES.items():
        p = root.joinpath(*parts)
        if p.is_file():
            found.append((p, kind))
    for parts, kind in ((CHAPTER_DIR, "chapter"), (MEMORY_CHAPTER_DIR, "memory_card")):
        d = root.joinpath(*parts)
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                found.append((p, kind))
    return found


def _jsonl_record_count(path: pathlib.Path) -> int:
    """Count non-empty records. Zero on a missing or unreadable file."""
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def snapshot_project(root: pathlib.Path) -> dict:
    entries = []
    counts: dict[str, int] = {}
    for path, kind in project_files(root):
        rel = path.relative_to(root).as_posix()
        if _is_linklike(path):
            raise SystemExit(f"error: canonical file is a link or reparse point: {rel}")
        raw = path.read_bytes()
        entries.append({"path": rel, "kind": kind, "sha256": sha256_bytes(raw), "bytes": len(raw)})
        counts[kind] = counts.get(kind, 0) + 1
    entries.sort(key=lambda e: e["path"])

    digest = sha256_bytes(
        "\n".join(f"{e['sha256']}  {e['path']}" for e in entries).encode("utf-8")
    )

    title = None
    meta = root / "novel.json"
    if meta.is_file():
        try:
            title = json.loads(meta.read_text(encoding="utf-8")).get("title")
        except (OSError, ValueError):
            title = None

    # `counts` counts *files* by kind. The record counts below are the numbers
    # that actually matter for the evaluation, so keep them explicit.
    return {
        "project_id": root.name,
        "title": title,
        "files": entries,
        "counts": counts,
        "fact_records": _jsonl_record_count(root / "continuity" / "canon-facts.jsonl"),
        "exception_records": _jsonl_record_count(
            root / "continuity" / "intentional-exceptions.jsonl"
        ),
        "total_bytes": sum(e["bytes"] for e in entries),
        "digest": digest,
    }


def snapshot_corpus(projects_root: pathlib.Path) -> dict:
    projects = {}
    for child in sorted(projects_root.iterdir()):
        if not child.is_dir() or child.name in SKIP_DIRS:
            continue
        if not child.joinpath("novel.json").is_file():
            continue
        projects[child.name] = snapshot_project(child)

    corpus_digest = sha256_bytes(
        "\n".join(f"{pid}  {p['digest']}" for pid, p in sorted(projects.items())).encode("utf-8")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": MANIFEST_KIND,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "projects_root": str(projects_root),
        "projects": projects,
        "corpus_digest": corpus_digest,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corpus_snapshot", description=__doc__)
    p.add_argument("--projects-root", required=True, help="Directory containing novel project dirs.")
    p.add_argument("--output", help="Write the manifest JSON here (default: stdout).")
    p.add_argument("--quiet", action="store_true", help="Suppress the human-readable summary.")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = pathlib.Path(args.projects_root)
    if not root.is_dir():
        print(json.dumps({"status": "error", "error": f"not a directory: {root}", "error_type": "usage"}))
        return 2

    manifest = snapshot_corpus(root)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)

    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")

    if not args.quiet:
        for pid, proj in manifest["projects"].items():
            c = proj["counts"]
            print(
                f"{pid:<28} {str(proj['title']):<18} "
                f"ch={c.get('chapter', 0):<4} cards={c.get('memory_card', 0):<4} "
                f"facts={proj['fact_records']:<3} ex={proj['exception_records']:<3} "
                f"bytes={proj['total_bytes']:<8} digest={proj['digest'][:12]}"
            )
        total_ch = sum(p["counts"].get("chapter", 0) for p in manifest["projects"].values())
        total_bytes = sum(p["total_bytes"] for p in manifest["projects"].values())
        print(f"-- {len(manifest['projects'])} projects, {total_ch} chapters, {total_bytes} bytes")
        print(f"corpus_digest={manifest['corpus_digest']}")

    if not args.output:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
