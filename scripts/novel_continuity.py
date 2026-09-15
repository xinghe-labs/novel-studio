#!/usr/bin/env python3
"""Build and enforce hash-bound continuity gates for fiction projects."""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import novel_review
import novel_cli


SCHEMA_VERSION = 1
POLICY_PATH = "continuity/policy.json"
HEAD_PATH = "continuity/head.json"
FACTS_PATH = "continuity/canon-facts.jsonl"
EXCEPTIONS_PATH = "continuity/intentional-exceptions.jsonl"
DEPENDENCIES_PATH = "continuity/dependencies.json"
INVALIDATIONS_PATH = "continuity/invalidations.json"
BASELINES_DIR = "continuity/baselines"
AUDITS_DIR = "reviews/continuity"

REVIEW_DIMENSIONS = (
    "causality",
    "timeline",
    "location",
    "character_state",
    "knowledge_boundaries",
    "relationships_and_names",
    "items_and_resources",
    "world_rules",
    "threads_and_payoffs",
)
FACT_CATEGORIES = frozenset(
    {
        "canon_truth",
        "character_knows",
        "character_believes",
        "character_claims",
        "reader_knows",
        "author_plan",
        "intentional_exception",
    }
)
FACT_STATUSES = frozenset({"active", "resolved", "superseded", "retired"})
SIGNIFICANCE_LEVELS = frozenset({"local", "normal", "major", "core"})
CHANGE_OPERATIONS = frozenset({"add", "update", "retire"})
FINDING_SEVERITIES = frozenset({"blocker", "error", "warning", "note"})
FINDING_CERTAINTIES = frozenset(
    {"confirmed", "suspected", "intentional_exception"}
)
AUDIT_DECISIONS = frozenset({"pass", "needs_author", "block"})
CHECK_STATUSES = frozenset({"pass", "warning", "not_applicable"})
CHAPTER_CLASSES = frozenset({"normal", "key"})
OUTLINE_STATUSES = frozenset({"locked", "planned", "optional", "abandoned"})
RISK_TRIGGERS = frozenset(
    {
        "core_world_rule",
        "complex_timeline",
        "callback_10_plus",
        "secret_boundary",
        "major_relationship",
        "key_item",
        "numeric_ledger",
        "suspected_conflict",
        "volume_open",
        "volume_close",
        "major_reveal",
        "character_fate",
        "locked_outline",
    }
)
INDEPENDENT_TRIGGERS = RISK_TRIGGERS
AUTHOR_CONFIRMATION_TRIGGERS = frozenset(
    {
        "core_world_rule",
        "major_relationship",
        "volume_open",
        "volume_close",
        "major_reveal",
        "character_fate",
        "locked_outline",
    }
)
CORE_REVISION_TYPES = frozenset(
    {"world_rule", "character_fate", "core_relationship", "core_truth"}
)
REVISION_TYPES = frozenset(
    {
        "formatting",
        "local_fact",
        "character_state",
        "knowledge_boundary",
        "timeline",
        "thread",
        "world_rule",
        "character_fate",
        "core_relationship",
        "core_truth",
        "unknown",
    }
)
FACT_ID = re.compile(r"^F-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
EXCEPTION_ID = re.compile(r"^EX-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
CHAPTER_FILE = re.compile(r"^(?P<number>\d{4})(?:-[^/\\]+)?\.md$", re.IGNORECASE)


class ContinuityError(RuntimeError):
    pass


def project_write_context(
    root: Path,
    args: argparse.Namespace | None = None,
    *,
    allow_bootstrap: bool = False,
):
    """Return the shared project-write authorization context.

    ``allow_bootstrap`` is intentionally explicit and is only used while a
    project is being initialized before it has a registry/lease row.  Keeping
    it on this adapter (rather than silently inferring it from ``args``)
    ensures callers cannot accidentally turn a registered-project mutation
    into an unauthenticated write.
    """
    try:
        import novel_workspace

        allow_bootstrap = bool(
            allow_bootstrap
            or (args is not None and getattr(args, "allow_bootstrap", False))
        )

        @contextlib.contextmanager
        def _context():
            try:
                with novel_workspace.project_write_context(
                    root,
                    workspace=getattr(args, "workspace", None) if args is not None else None,
                    work_id=getattr(args, "work_id", None) if args is not None else None,
                    allow_bootstrap=allow_bootstrap,
                ) as context:
                    yield context
            except novel_workspace.WorkspaceError as exc:
                raise ContinuityError(str(exc)) from exc

        return _context()
    except (ImportError, OSError) as exc:
        raise ContinuityError(
            f"Project write authorization module unavailable: {exc}"
        ) from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compact_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_root(raw_root: str | Path) -> Path:
    raw = Path(raw_root).expanduser()
    if _path_chain_has_link(raw):
        raise ContinuityError(
            f"Project path cannot traverse a symbolic link or reparse point: {raw}"
        )
    root = raw.resolve()
    if root == Path(root.anchor).resolve() or root == Path.home().resolve():
        raise ContinuityError("Project root cannot be a filesystem root or user home")
    if not (root / "novel.json").is_file():
        raise ContinuityError(f"Not an initialized novel project: {root}")
    return root


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _link_like(path: Path) -> bool:
    """Detect symbolic links, junctions, and Windows reparse points."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _path_chain_has_link(path: Path) -> bool:
    current = Path(path).expanduser()
    if not current.is_absolute():
        current = Path.cwd() / current
    while True:
        if _link_like(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_stable_bytes(path, label="JSON input").decode("utf-8"))
    except ContinuityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContinuityError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuityError(f"Expected a JSON object in {path}")
    return value


def dump_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(_read_stable_bytes(path, label="file"))


def _read_stable_bytes(path: Path, *, label: str) -> bytes:
    """Read one ordinary file without accepting a link or replacement race."""

    raw = Path(path).expanduser()
    if _path_chain_has_link(raw):
        raise ContinuityError(
            f"{label} cannot traverse a symbolic link or reparse point: {raw}"
        )
    try:
        with raw.open("rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read()
            after = os.fstat(handle.fileno())
        current = os.stat(raw, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ContinuityError(f"Missing {label}: {raw}") from exc
    except OSError as exc:
        raise ContinuityError(f"Unable to read {label}: {raw}: {exc}") from exc
    before_identity = (
        getattr(before, "st_dev", None),
        getattr(before, "st_ino", None),
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        getattr(after, "st_dev", None),
        getattr(after, "st_ino", None),
        after.st_size,
        after.st_mtime_ns,
    )
    current_identity = (
        getattr(current, "st_dev", None),
        getattr(current, "st_ino", None),
        current.st_size,
        current.st_mtime_ns,
    )
    if before_identity != after_identity or after_identity != current_identity:
        raise ContinuityError(f"{label} changed while being read: {raw}")
    if not stat.S_ISREG(current.st_mode):
        raise ContinuityError(f"{label} is not a regular file: {raw}")
    return content


def _json_object_from_bytes(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContinuityError(f"{label} must be valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuityError(f"{label} must be a JSON object")
    return value


def _external_baseline_input(
    raw_path: str | Path, *, project_root: Path, label: str
) -> tuple[Path, bytes]:
    raw = Path(raw_path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    if _path_chain_has_link(raw):
        raise ContinuityError(
            f"{label} cannot traverse a symbolic link or reparse point: {raw}"
        )
    path = raw.resolve()
    if path == project_root or project_root in path.parents:
        raise ContinuityError(f"{label} must be stored outside the project tree")
    if not path.is_file():
        raise ContinuityError(f"{label} is not a regular file: {path}")
    return path, _read_stable_bytes(path, label=label)


def write_new(path: Path, content: bytes) -> None:
    try:
        novel_cli.atomic_create_bytes(path, content)
    except FileExistsError as exc:
        raise ContinuityError(f"Refusing to overwrite an existing output: {path}") from exc


def transactional_write(
    files: list[tuple[Path, bytes]],
    *,
    journal_root: str | Path | None = None,
    expected_existing: dict[Path | str, str | None] | None = None,
    expected_targets: dict[Path | str, str | None] | None = None,
) -> None:
    """Use the canonical persistent transaction implementation."""

    if not files:
        return
    try:
        import novel_project

        root = journal_root
        if root is None:
            first = Path(files[0][0]).resolve()
            for parent in (first.parent, *first.parents):
                if (parent / "novel.json").is_file():
                    root = parent
                    break
        novel_project.transactional_write(
            files,
            journal_root=root,
            expected_existing=expected_existing,
            expected_targets=expected_targets,
        )
    except Exception as exc:
        if isinstance(exc, ContinuityError):
            raise
        raise ContinuityError(str(exc)) from exc


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": True,
        "enforcement": "strict",
        "source_of_truth": "local_markdown",
        "baseline_required": True,
        "ordinary_errors": "auto_fix_in_staging_then_reaudit",
        "major_conflicts": "ask_author_in_related_batches",
        "key_chapters_require_author_confirmation": True,
        "quality_review_separate": True,
        "online_revision_requires_explicit_confirmation": True,
        "independent_review": {
            "mode": "risk_based",
            "periodic_interval_chapters": 5,
            "callback_span_threshold": 10,
            "risk_triggers": sorted(INDEPENDENT_TRIGGERS),
        },
    }


def empty_dependencies() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "chapters": {}}


def empty_invalidations() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "items": []}


def scaffold_contents() -> dict[str, bytes]:
    return {
        POLICY_PATH: dump_json(default_policy()).encode("utf-8"),
        FACTS_PATH: b"",
        EXCEPTIONS_PATH: b"",
        DEPENDENCIES_PATH: dump_json(empty_dependencies()).encode("utf-8"),
        INVALIDATIONS_PATH: dump_json(empty_invalidations()).encode("utf-8"),
    }


def canonical_relative_paths(
    root: Path, overrides: dict[str, bytes] | None = None
) -> list[str]:
    paths: set[str] = set()
    exact = (
        "novel.json",
        "planning/framework-session.md",
        "manuscript/index.md",
        "memory/book-summary.md",
        "memory/decisions.md",
        "continuity/state.json",
        "continuity/timeline.md",
        "continuity/threads.md",
        POLICY_PATH,
        FACTS_PATH,
        EXCEPTIONS_PATH,
        DEPENDENCIES_PATH,
        INVALIDATIONS_PATH,
    )
    for relative in exact:
        if (root / relative).is_file():
            paths.add(relative)
    for directory, pattern in (
        ("story-bible", "*.md"),
        ("outlines", "*.md"),
        ("manuscript/chapters", "*.md"),
        ("memory/chapters", "*.md"),
    ):
        folder = root / directory
        if folder.is_dir():
            for path in folder.rglob(pattern):
                if path.is_file():
                    paths.add(path.relative_to(root).as_posix())
    manuscript = root / "manuscript"
    if manuscript.is_dir():
        for path in manuscript.glob("*.md"):
            if path.name.lower() != "index.md" and CHAPTER_FILE.fullmatch(path.name):
                paths.add(path.relative_to(root).as_posix())
    if overrides:
        exact_set = set(exact)
        for relative in overrides:
            normalized = relative.replace("\\", "/")
            in_markdown_tree = any(
                normalized.startswith(prefix) and normalized.lower().endswith(".md")
                for prefix in (
                    "story-bible/",
                    "outlines/",
                    "manuscript/chapters/",
                    "memory/chapters/",
                )
            )
            direct_manuscript_chapter = (
                normalized.startswith("manuscript/")
                and normalized.count("/") == 1
                and normalized != "manuscript/index.md"
                and CHAPTER_FILE.fullmatch(Path(normalized).name) is not None
            )
            if normalized in exact_set or in_markdown_tree or direct_manuscript_chapter:
                paths.add(normalized)
    paths.discard(HEAD_PATH)
    return sorted(paths)


def snapshot_digest(entries: Iterable[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item["path"]):
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def canonical_snapshot(
    root: Path, overrides: dict[str, bytes] | None = None
) -> tuple[list[dict[str, str]], str]:
    normalized = {key.replace("\\", "/"): value for key, value in (overrides or {}).items()}
    # Scope has to be judged between canonical forms.  Path.resolve() rewrites
    # 8.3 short names and other non-canonical components, so comparing a
    # resolved child against the root argument as given reports existing
    # sources as missing whenever the two forms differ.
    resolved_root = root.resolve()
    entries: list[dict[str, str]] = []
    for relative in canonical_relative_paths(root, normalized):
        content = normalized.get(relative)
        if content is None:
            raw_path = root / relative
            if _path_chain_has_link(raw_path):
                raise ContinuityError(
                    f"Canonical source traverses a symbolic link or reparse point: {relative}"
                )
            path = raw_path.resolve()
            if not is_within(path, resolved_root) or not path.is_file():
                raise ContinuityError(f"Canonical source is missing or out of scope: {relative}")
            content = _read_stable_bytes(raw_path, label=f"canonical source {relative}")
        entries.append({"path": relative, "sha256": sha256_bytes(content)})
    return entries, snapshot_digest(entries)


def validate_snapshot_current(root: Path, snapshot: Any) -> list[str]:
    if not isinstance(snapshot, list):
        return ["source_snapshot must be an array"]
    seen: set[str] = set()
    expected: list[dict[str, str]] = []
    errors: list[str] = []
    for index, entry in enumerate(snapshot):
        if not isinstance(entry, dict):
            errors.append(f"source_snapshot[{index}] must be an object")
            continue
        relative = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(relative, str) or not relative or relative in seen:
            errors.append(f"source_snapshot[{index}] has an invalid or duplicate path")
            continue
        if not isinstance(digest, str) or len(digest) != 64:
            errors.append(f"source_snapshot[{index}] has an invalid sha256")
            continue
        seen.add(relative)
        expected.append({"path": relative, "sha256": digest})
    if errors:
        return errors
    current, current_hash = canonical_snapshot(root)
    expected_hash = snapshot_digest(expected)
    if expected_hash != current_hash or expected != current:
        return ["canonical sources changed after the continuity context was prepared"]
    return []


def chapter_paths(root: Path, through: int | None = None) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for relative in canonical_relative_paths(root):
        if not relative.startswith("manuscript/") or relative == "manuscript/index.md":
            continue
        match = CHAPTER_FILE.fullmatch(Path(relative).name)
        if match is None:
            continue
        number = int(match.group("number"))
        if through is None or number <= through:
            result.append((number, relative))
    return sorted(result)


def current_chapter(root: Path) -> int:
    value = read_json(root / "novel.json").get("current_chapter")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContinuityError("novel.json current_chapter must be a non-negative integer")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ContinuityError(f"Missing file: {path}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContinuityError(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ContinuityError(f"Expected an object in {path} line {line_number}")
        records.append(value)
    return records


def dump_jsonl(records: Iterable[dict[str, Any]], key: str) -> bytes:
    ordered = sorted(records, key=lambda item: str(item.get(key, "")))
    text = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in ordered)
    return text.encode("utf-8")


def baseline_path_for(
    root: Path,
    through: int,
    canon_hash: str,
    discriminator: str | None = None,
) -> Path:
    suffix = f"-{discriminator[:10]}" if discriminator else ""
    return root / BASELINES_DIR / (
        f"canon-baseline-{through:04d}-{canon_hash[:12]}{suffix}.json"
    )


def build_baseline_record(
    *,
    root: Path,
    through: int,
    snapshot: list[dict[str, str]],
    canon_hash: str,
    authorization_reference: str,
    reviewer: dict[str, Any],
    findings: list[dict[str, Any]],
    residual_risks: list[Any],
    packet_sha256: str | None,
    report_sha256: str | None,
) -> dict[str, Any]:
    chapter_set = {path for _, path in chapter_paths(root, through)}
    sealed_sources = [entry for entry in snapshot if entry["path"] in chapter_set or entry["path"].startswith("memory/chapters/")]
    return {
        "schema_version": SCHEMA_VERSION,
        "record_kind": "canon_continuity_baseline",
        "status": "complete",
        "decision": "pass",
        "through_chapter": through,
        "canon_sha256": canon_hash,
        "source_snapshot": snapshot,
        "source_snapshot_sha256": snapshot_digest(snapshot),
        "sealed_sources": sealed_sources,
        "reviewed_dimensions": list(REVIEW_DIMENSIONS),
        "reviewer": reviewer,
        "findings": findings,
        "residual_risks": residual_risks,
        "packet_sha256": packet_sha256,
        "report_sha256": report_sha256,
        "authorization_reference": authorization_reference,
        "recorded_at": utc_now(),
    }


def reseal_zero_baseline(
    root: Path,
    authorization_reference: str,
    *,
    extra_writes: list[tuple[Path, bytes]] | None = None,
    expected_existing: dict[Path | str, str | None] | None = None,
    expected_targets: dict[Path | str, str | None] | None = None,
) -> dict[str, Any]:
    if current_chapter(root) != 0:
        raise ContinuityError("Only an empty project can use the zero-chapter baseline")
    overrides: dict[str, bytes] = {}
    resolved_root = root.resolve()
    for path, content in extra_writes or []:
        resolved = path.resolve()
        if not is_within(resolved, resolved_root):
            raise ContinuityError("Zero-baseline write escapes the project root")
        overrides[resolved.relative_to(resolved_root).as_posix()] = content
    snapshot, canon_hash = canonical_snapshot(root, overrides)
    baseline = build_baseline_record(
        root=root,
        through=0,
        snapshot=snapshot,
        canon_hash=canon_hash,
        authorization_reference=authorization_reference,
        reviewer={"mode": "deterministic", "reviewer_id": "project-initializer", "independent_context": True},
        findings=[],
        residual_risks=[],
        packet_sha256=None,
        report_sha256=None,
    )
    baseline_path = baseline_path_for(root, 0, canon_hash)
    baseline_relative = baseline_path.relative_to(root).as_posix()
    baseline_bytes = dump_json(baseline).encode("utf-8")
    if baseline_path.is_file():
        baseline_bytes = baseline_path.read_bytes()
    head = {
        "schema_version": SCHEMA_VERSION,
        "status": "current",
        "through_chapter": 0,
        "canon_sha256": canon_hash,
        "parent_canon_sha256": None,
        "baseline_file": baseline_relative,
        "baseline_sha256": sha256_bytes(baseline_bytes),
        "latest_audit": None,
        "authorization_reference": authorization_reference,
        "updated_at": utc_now(),
    }
    writes: list[tuple[Path, bytes]] = list(extra_writes or [])
    if not baseline_path.exists():
        writes.append((baseline_path, baseline_bytes))
    writes.append((root / HEAD_PATH, dump_json(head).encode("utf-8")))
    target_receipts = {
        Path(target).resolve(): expected
        for target, expected in (expected_targets or {}).items()
    }
    for target, _ in writes:
        resolved = target.resolve()
        if resolved not in target_receipts:
            target_receipts[resolved] = sha256_file(resolved) if resolved.is_file() else None
    transactional_write(
        writes,
        journal_root=root,
        expected_existing=expected_existing,
        expected_targets=target_receipts,
    )
    return {"status": "sealed", "through_chapter": 0, "canon_sha256": canon_hash, "baseline_file": baseline_relative}


def _install_project(raw_root: str | Path) -> dict[str, Any]:
    root = resolve_root(raw_root)
    created_directories: list[str] = []
    for relative in (BASELINES_DIR, AUDITS_DIR):
        path = root / relative
        if not path.exists():
            path.mkdir(parents=True)
            created_directories.append(relative)
        elif not path.is_dir():
            raise ContinuityError(f"Expected a directory: {path}")
    created_files: list[str] = []
    scaffold_writes: list[tuple[Path, bytes]] = []
    for relative, content in scaffold_contents().items():
        path = root / relative
        if not path.exists():
            scaffold_writes.append((path, content))
            created_files.append(relative)
        elif not path.is_file():
            raise ContinuityError(f"Expected a file: {path}")
    baseline_result: dict[str, Any] | None = None
    if not (root / HEAD_PATH).is_file() and current_chapter(root) == 0:
        baseline_result = reseal_zero_baseline(
            root,
            "Initialized continuity hard gate before the first canonical unit",
            extra_writes=scaffold_writes,
            expected_targets={path: None for path, _ in scaffold_writes},
        )
        created_files.extend([baseline_result["baseline_file"], HEAD_PATH])
    elif scaffold_writes:
        transactional_write(
            scaffold_writes,
            journal_root=root,
            expected_targets={path: None for path, _ in scaffold_writes},
        )
    return {
        "status": "installed" if created_directories or created_files else "already_current",
        "project_root": str(root),
        "created_directories": sorted(created_directories),
        "created_files": sorted(created_files),
        "baseline": baseline_result,
        "baseline_required": not (root / HEAD_PATH).is_file(),
    }


def install_project(
    raw_root: str | Path,
    *,
    workspace: str | Path | None = None,
    work_id: str | None = None,
    allow_bootstrap: bool = False,
) -> dict[str, Any]:
    root = resolve_root(raw_root)
    args = argparse.Namespace(workspace=workspace, work_id=work_id)
    with project_write_context(
        root,
        args,
        allow_bootstrap=allow_bootstrap,
    ) as context:
        result = _install_project(root)
        context.assert_live()
        context.refresh_base_after_write("continuity project installation")
        return result


def unresolved_invalidations(root: Path) -> list[dict[str, Any]]:
    data = read_json(root / INVALIDATIONS_PATH)
    items = data.get("items")
    if not isinstance(items, list):
        raise ContinuityError("continuity/invalidations.json items must be an array")
    return [item for item in items if isinstance(item, dict) and item.get("status") == "open"]


def validate_baseline_anchor(root: Path, head: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    relative = head.get("baseline_file")
    if not isinstance(relative, str) or not relative:
        return ["continuity/head.json does not name a baseline_file"]
    path = (root / relative).resolve()
    if not is_within(path, (root / BASELINES_DIR).resolve()) or not path.is_file():
        return ["continuity baseline file is missing or out of scope"]
    if sha256_file(path) != head.get("baseline_sha256"):
        errors.append("continuity baseline file changed after it was sealed")
        return errors
    baseline = read_json(path)
    if baseline.get("decision") != "pass" or baseline.get("status") != "complete":
        errors.append("continuity baseline is not a completed pass")
    sealed = baseline.get("sealed_sources")
    if not isinstance(sealed, list):
        errors.append("continuity baseline sealed_sources must be an array")
        return errors
    for entry in sealed:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("continuity baseline contains an invalid sealed source")
            continue
        source = (root / entry["path"]).resolve()
        if not is_within(source, root.resolve()) or not source.is_file():
            errors.append(f"sealed continuity source is missing: {entry['path']}")
        elif sha256_file(source) != entry.get("sha256"):
            errors.append(f"sealed continuity source changed: {entry['path']}")
    return errors


def validate_dependency_chain(root: Path, head: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    dependencies = read_json(root / DEPENDENCIES_PATH)
    chapters = dependencies.get("chapters")
    if dependencies.get("schema_version") != SCHEMA_VERSION or not isinstance(chapters, dict):
        return ["continuity/dependencies.json has an invalid schema"]
    baseline_relative = head.get("baseline_file")
    if not isinstance(baseline_relative, str) or not baseline_relative:
        return errors
    baseline_path = (root / baseline_relative).resolve()
    if not is_within(baseline_path, (root / BASELINES_DIR).resolve()) or not baseline_path.is_file():
        return errors
    baseline = read_json(baseline_path)
    baseline_through = baseline.get("through_chapter", -1)
    current = current_chapter(root)
    for number in range(1, current + 1):
        key = f"{number:04d}"
        entry = chapters.get(key)
        if number <= baseline_through:
            if entry is not None and not isinstance(entry, dict):
                errors.append(f"continuity dependency {key} must be an object")
            continue
        if not isinstance(entry, dict):
            errors.append(f"missing continuity dependency for chapter {key}")
            continue
        audit_relative = entry.get("audit_path")
        if not isinstance(audit_relative, str) or not audit_relative:
            errors.append(f"chapter {key} does not name its continuity audit")
            continue
        audit_path = (root / audit_relative).resolve()
        if not is_within(audit_path, (root / AUDITS_DIR).resolve()) or not audit_path.is_file():
            errors.append(f"chapter {key} continuity audit is missing")
        elif sha256_file(audit_path) != entry.get("audit_sha256"):
            errors.append(f"chapter {key} continuity audit changed after commit")
    return errors


def continuity_status(raw_root: str | Path) -> dict[str, Any]:
    root = resolve_root(raw_root)
    missing = [relative for relative in scaffold_contents() if not (root / relative).is_file()]
    if missing:
        return {"status": "not_installed", "project_root": str(root), "missing": missing, "commit_blocked": True, "delivery_blocked": True, "errors": [], "warnings": ["Continuity hard gate is not installed; run novel_project.py upgrade"]}
    head_path = root / HEAD_PATH
    if not head_path.is_file():
        return {"status": "baseline_required", "project_root": str(root), "through_chapter": current_chapter(root), "commit_blocked": True, "delivery_blocked": True, "errors": ["A full continuity baseline is required before the next canonical commit or delivery"], "warnings": []}
    errors: list[str] = []
    warnings: list[str] = []
    review_due = False
    review_due_through: int | None = None
    baseline_through: int | None = None
    try:
        head = read_json(head_path)
        if head.get("schema_version") != SCHEMA_VERSION:
            errors.append("continuity/head.json has an unsupported schema_version")
        if head.get("status") != "current":
            errors.append("continuity head is explicitly blocked")
        if head.get("through_chapter") != current_chapter(root):
            errors.append("continuity head does not match novel.json current_chapter")
        _, current_hash = canonical_snapshot(root)
        if head.get("canon_sha256") != current_hash:
            errors.append("canonical files changed outside the current continuity seal")
        errors.extend(validate_baseline_anchor(root, head))
        errors.extend(validate_dependency_chain(root, head))
        open_items = unresolved_invalidations(root)
        if open_items:
            errors.append(f"{len(open_items)} continuity invalidation(s) remain unresolved")
        baseline_relative = head.get("baseline_file")
        if isinstance(baseline_relative, str) and baseline_relative:
            baseline_path = (root / baseline_relative).resolve()
            if is_within(baseline_path, (root / BASELINES_DIR).resolve()) and baseline_path.is_file():
                baseline = read_json(baseline_path)
                raw_baseline_through = baseline.get("through_chapter")
                if isinstance(raw_baseline_through, int) and not isinstance(raw_baseline_through, bool):
                    baseline_through = raw_baseline_through
        policy = read_json(root / POLICY_PATH)
        review_policy = policy.get("independent_review")
        interval = review_policy.get("periodic_interval_chapters") if isinstance(review_policy, dict) else None
        work_type = read_json(root / "novel.json").get("work_type", "serial_novel")
        if work_type == "short_story":
            interval = 1
        if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
            errors.append("continuity policy periodic_interval_chapters must be a positive integer")
        elif baseline_through is not None:
            current = current_chapter(root)
            review_due_through = (current // interval) * interval
            review_due = review_due_through > baseline_through
            if review_due:
                warnings.append(
                    "Independent global continuity review is due through chapter "
                    f"{review_due_through:04d}"
                )
    except (ContinuityError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"Invalid continuity metadata: {exc}")
        current_hash = None
        open_items = []
        head = {}
    status = "stale" if errors else ("review_due" if review_due else "current")
    return {
        "status": status,
        "project_root": str(root),
        "through_chapter": current_chapter(root),
        "canon_sha256": current_hash,
        "sealed_canon_sha256": head.get("canon_sha256"),
        "baseline_file": head.get("baseline_file"),
        "baseline_through_chapter": baseline_through,
        "latest_audit": head.get("latest_audit"),
        "global_review_due": review_due,
        "global_review_due_through": review_due_through if review_due else None,
        "open_invalidations": len(open_items),
        "commit_blocked": bool(errors) or review_due,
        "delivery_blocked": bool(errors) or review_due,
        "errors": errors,
        "warnings": warnings,
    }


def collect_validation(raw_root: str | Path) -> tuple[list[str], list[str]]:
    status = continuity_status(raw_root)
    if status["status"] == "not_installed":
        return [], list(status["warnings"])
    return list(status["errors"]), list(status["warnings"])


def ensure_ready(root: Path, action: str) -> dict[str, Any]:
    status = continuity_status(root)
    if status["status"] != "current":
        details = "; ".join(status["errors"] or status["warnings"])
        raise ContinuityError(f"Continuity hard gate blocks {action}: {details}")
    return status


def ensure_delivery_allowed(raw_root: str | Path) -> dict[str, Any]:
    root = resolve_root(raw_root)
    return ensure_ready(root, "derived export or platform delivery")


def safe_output(raw: str | Path, *, project_root: Path | None = None) -> Path:
    raw_path = Path(raw).expanduser()
    if not raw_path.is_absolute():
        raw_path = Path.cwd() / raw_path
    if _path_chain_has_link(raw_path):
        raise ContinuityError(
            f"Output path cannot traverse a symbolic link or reparse point: {raw_path}"
        )
    path = raw_path.resolve()
    if project_root is not None and is_within(path, Path(project_root).resolve()):
        raise ContinuityError(
            "Preparation output must be outside the project tree; use the current "
            "work-root and copy completed input into staging only under a lease"
        )
    if path.exists():
        raise ContinuityError(f"Refusing to overwrite an existing output: {path}")
    return path


def write_external_pair(
    first: Path, first_content: bytes, second: Path, second_content: bytes
) -> None:
    """Create two external preparation files and remove this attempt on failure."""

    if first.resolve() == second.resolve():
        raise ContinuityError("Preparation packet and report outputs must be different files")
    items = ((first, first_content), (second, second_content))
    for path, _ in items:
        if _path_chain_has_link(path):
            raise ContinuityError(
                f"Output path cannot traverse a symbolic link or reparse point: {path}"
            )
        if path.exists():
            raise ContinuityError(f"Refusing to overwrite an existing output: {path}")

    created: list[tuple[Path, tuple[int, int]]] = []
    try:
        for path, content in items:
            try:
                identity = novel_cli.atomic_create_bytes(path, content)
            except FileExistsError as exc:
                raise ContinuityError(
                    f"Refusing to overwrite an existing output: {path}"
                ) from exc
            created.append((path, identity))
    except BaseException as exc:
        cleanup_errors: list[str] = []
        for path, identity in reversed(created):
            try:
                current = path.stat(follow_symlinks=False)
                if _link_like(path) or not path.is_file():
                    cleanup_errors.append(f"owned output changed type: {path}")
                elif (current.st_dev, current.st_ino) != identity:
                    cleanup_errors.append(f"owned output was replaced concurrently: {path}")
                else:
                    path.unlink()
            except FileNotFoundError:
                continue
            except BaseException as cleanup_exc:
                cleanup_errors.append(f"{path}: {cleanup_exc}")
        if cleanup_errors:
            raise ContinuityError(
                "Preparation failed and partial-output cleanup requires manual "
                "reconciliation: " + "; ".join(cleanup_errors)
            ) from exc
        raise


def resolve_package_directory(root: Path, raw_package: str | Path) -> Path:
    package = Path(raw_package).expanduser()
    if not package.is_absolute():
        package = root / package
    package = package.resolve()
    if not package.is_dir() or not is_within(
        package, (root / "staging/chapters").resolve()
    ):
        raise ContinuityError("Chapter package must be under staging/chapters")
    return package


def package_member(
    package: Path,
    raw_relative: Any,
    default: str,
    *,
    must_exist: bool = True,
) -> Path:
    relative = raw_relative if isinstance(raw_relative, str) and raw_relative else default
    path = (package / relative).resolve()
    if not is_within(path, package):
        raise ContinuityError(f"Chapter package path escapes staging: {relative}")
    if must_exist and not path.is_file():
        raise ContinuityError(f"Missing chapter package file: {relative}")
    return path


def source_entry_map(snapshot: list[dict[str, str]]) -> dict[str, str]:
    return {entry["path"]: entry["sha256"] for entry in snapshot}


def automatic_reading(root: Path, chapter_number: int, snapshot: list[dict[str, str]]) -> list[dict[str, Any]]:
    paths = source_entry_map(snapshot)
    selected: list[str] = []
    for relative in (
        "memory/book-summary.md",
        "memory/decisions.md",
        "manuscript/index.md",
        "continuity/state.json",
        "continuity/timeline.md",
        "continuity/threads.md",
        POLICY_PATH,
        FACTS_PATH,
        EXCEPTIONS_PATH,
        DEPENDENCIES_PATH,
        INVALIDATIONS_PATH,
        "story-bible/premise.md",
        "story-bible/cast.md",
        "story-bible/world.md",
        "outlines/master-outline.md",
    ):
        if relative in paths:
            selected.append(relative)
    for number, chapter_path in chapter_paths(root):
        if chapter_number - 3 <= number < chapter_number:
            selected.append(chapter_path)
            card = f"memory/chapters/{number:04d}.md"
            if card in paths:
                selected.append(card)
    return [{"path": path, "sha256": paths[path]} for path in dict.fromkeys(selected)]


def build_context(root: Path, chapter_number: int) -> dict[str, Any]:
    status = ensure_ready(root, "continuity context preparation")
    quality_status = novel_review.review_status(root)
    if quality_status["commit_blocked"]:
        raise ContinuityError(
            "Quality review hard gate blocks continuity context preparation: "
            f"review chapters {quality_status['review_from']:04d}-"
            f"{quality_status['review_through']:04d} and record a passing "
            "independent quality report before drafting the next chapter"
        )
    current = current_chapter(root)
    if chapter_number != current + 1:
        raise ContinuityError(f"Continuity context must target chapter {current + 1:04d}")
    snapshot, canon_hash = canonical_snapshot(root)
    state_hash = sha256_file(root / "continuity/state.json")
    work_type = read_json(root / "novel.json").get("work_type", "serial_novel")
    return {
        "schema_version": SCHEMA_VERSION,
        "packet_kind": "short_story_continuity_context" if work_type == "short_story" else "chapter_continuity_context",
        "status": "draft",
        "project_root": str(root),
        "work_type": work_type,
        "chapter_number": chapter_number,
        "base_through_chapter": current,
        "base_canon_sha256": canon_hash,
        "base_state_sha256": state_hash,
        "source_snapshot": snapshot,
        "source_snapshot_sha256": snapshot_digest(snapshot),
        "automatic_reading": automatic_reading(root, chapter_number, snapshot),
        "required_reading": [],
        "touched_entities": [],
        "touched_fact_ids": [],
        "chapter_contract": {
            "delivery": "",
            "pov": "",
            "story_time": "",
            "location": "",
            "purpose": "",
            "entry_state": "",
            "allowed_changes": [],
            "forbidden_changes": [],
            "exit_direction": "",
        },
        "invariants": [],
        "risk_assessment": {
            "chapter_class": "normal",
            "triggers": [],
            "max_callback_span": 0,
            "requires_independent_review": False,
            "rationale": "",
        },
        "author_confirmation_reference": "",
        "prepared_from_head": status["sealed_canon_sha256"],
        "prepared_at": utc_now(),
    }


def prepare_context(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    output = safe_output(args.output, project_root=root)
    context = build_context(root, args.chapter)
    write_new(output, dump_json(context).encode("utf-8"))
    return {"status": "prepared", "chapter_number": args.chapter, "output": str(output), "base_canon_sha256": context["base_canon_sha256"]}


def evidence_text(root: Path, context: dict[str, Any], candidate: Path, relative: str) -> str:
    if relative == "candidate":
        return candidate.read_text(encoding="utf-8")
    snapshot_paths = source_entry_map(context["source_snapshot"])
    if relative not in snapshot_paths:
        raise ContinuityError(f"Evidence path is not in the bound context snapshot: {relative}")
    path = (root / relative).resolve()
    if not is_within(path, root.resolve()) or not path.is_file():
        raise ContinuityError(f"Evidence path is missing or out of scope: {relative}")
    return path.read_text(encoding="utf-8")


def validate_evidence(
    root: Path,
    context: dict[str, Any],
    candidate: Path,
    evidence: Any,
    label: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, list):
        return [f"{label} must be an array"]
    if not evidence and not allow_empty:
        return [f"{label} must contain source evidence"]
    for index, item in enumerate(evidence):
        prefix = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        relative = item.get("path")
        quote = item.get("quote")
        location = item.get("location")
        if not isinstance(relative, str) or not relative:
            errors.append(f"{prefix}.path must not be empty")
            continue
        if not isinstance(location, str) or not location.strip():
            errors.append(f"{prefix}.location must not be empty")
        if not isinstance(quote, str) or not quote.strip():
            errors.append(f"{prefix}.quote must not be empty")
            continue
        try:
            source_text = evidence_text(root, context, candidate, relative)
        except (ContinuityError, UnicodeDecodeError) as exc:
            errors.append(str(exc))
            continue
        if quote.strip() not in source_text:
            errors.append(f"{prefix}.quote was not found verbatim in {relative}")
    return errors


def derived_risk(context: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    assessment = context.get("risk_assessment")
    if not isinstance(assessment, dict):
        raise ContinuityError("risk_assessment must be an object")
    chapter_class = assessment.get("chapter_class")
    if chapter_class not in CHAPTER_CLASSES:
        raise ContinuityError("risk_assessment.chapter_class must be normal or key")
    triggers = assessment.get("triggers")
    if not isinstance(triggers, list) or any(item not in RISK_TRIGGERS for item in triggers):
        raise ContinuityError("risk_assessment.triggers contains an unsupported value")
    callback_span = assessment.get("max_callback_span")
    if not isinstance(callback_span, int) or isinstance(callback_span, bool) or callback_span < 0:
        raise ContinuityError("risk_assessment.max_callback_span must be non-negative")
    reasons = sorted(set(str(item) for item in triggers))
    independent = bool(set(triggers) & INDEPENDENT_TRIGGERS) or callback_span >= 10 or chapter_class == "key"
    confirmation = chapter_class == "key" or bool(set(triggers) & AUTHOR_CONFIRMATION_TRIGGERS)
    return independent, confirmation, reasons


def validate_context(root: Path, context_path: Path, chapter_number: int) -> dict[str, Any]:
    context = read_json(context_path)
    errors: list[str] = []
    if context.get("schema_version") != SCHEMA_VERSION:
        errors.append("continuity context has an unsupported schema_version")
    if context.get("status") != "complete":
        errors.append("continuity context status must be complete")
    if context.get("chapter_number") != chapter_number:
        errors.append("continuity context chapter_number does not match the package")
    current = ensure_ready(root, "chapter continuity validation")
    if context.get("base_canon_sha256") != current["canon_sha256"]:
        errors.append("continuity context does not bind the current canonical head")
    if context.get("base_state_sha256") != sha256_file(root / "continuity/state.json"):
        errors.append("continuity context does not bind the current state.json")
    errors.extend(validate_snapshot_current(root, context.get("source_snapshot")))
    if context.get("source_snapshot_sha256") != snapshot_digest(context.get("source_snapshot", [])):
        errors.append("continuity context source_snapshot_sha256 is invalid")
    contract = context.get("chapter_contract")
    required_contract = ("delivery", "pov", "story_time", "location", "purpose", "entry_state", "exit_direction")
    if not isinstance(contract, dict):
        errors.append("chapter_contract must be an object")
    else:
        for key in required_contract:
            if not isinstance(contract.get(key), str) or not contract[key].strip():
                errors.append(f"chapter_contract.{key} must not be empty")
        for key in ("allowed_changes", "forbidden_changes"):
            if not isinstance(contract.get(key), list):
                errors.append(f"chapter_contract.{key} must be an array")
    touched = context.get("touched_entities")
    if not isinstance(touched, list) or not touched:
        errors.append("touched_entities must identify at least one entity")
    else:
        for index, item in enumerate(touched):
            if not isinstance(item, dict) or not str(item.get("type", "")).strip() or not str(item.get("name", "")).strip():
                errors.append(f"touched_entities[{index}] must name an entity type and name")
    touched_fact_ids = context.get("touched_fact_ids")
    if not isinstance(touched_fact_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in touched_fact_ids
    ):
        errors.append("touched_fact_ids must be an array of non-empty fact IDs")
        touched_fact_ids = []
    if not isinstance(context.get("invariants"), list) or not context.get("invariants"):
        errors.append("invariants must contain the facts that the chapter cannot silently violate")
    candidate_placeholder = context_path
    reading = context.get("required_reading")
    if not isinstance(reading, list) or not reading:
        errors.append("required_reading must contain verified source readings")
    else:
        snapshot_paths = source_entry_map(context.get("source_snapshot", []))
        read_paths: set[str] = set()
        for index, item in enumerate(reading):
            if not isinstance(item, dict):
                errors.append(f"required_reading[{index}] must be an object")
                continue
            relative = item.get("path")
            if not isinstance(relative, str) or relative not in snapshot_paths:
                errors.append(f"required_reading[{index}].path is not in the context snapshot")
                continue
            read_paths.add(relative)
            if item.get("sha256") != snapshot_paths[relative]:
                errors.append(f"required_reading[{index}] has a stale sha256")
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                errors.append(f"required_reading[{index}].reason must not be empty")
            source_is_empty = not (root / relative).read_text(encoding="utf-8").strip()
            errors.extend(
                validate_evidence(
                    root,
                    context,
                    candidate_placeholder,
                    item.get("evidence"),
                    f"required_reading[{index}].evidence",
                    allow_empty=source_is_empty,
                )
            )
        if current_chapter(root) > 0:
            previous = dict(chapter_paths(root)).get(current_chapter(root))
            if previous and previous not in read_paths:
                errors.append("required_reading must include the previous chapter original text")
        automatic_paths = {
            item.get("path")
            for item in context.get("automatic_reading", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        missing_automatic = sorted(automatic_paths - read_paths)
        if missing_automatic:
            errors.append(
                "required_reading is missing automatic continuity sources: "
                + ", ".join(missing_automatic)
            )
        try:
            facts_by_id = {
                str(item.get("fact_id")): item
                for item in read_jsonl(root / FACTS_PATH)
            }
        except ContinuityError as exc:
            errors.append(str(exc))
            facts_by_id = {}
        for fact_id in touched_fact_ids:
            record = facts_by_id.get(fact_id)
            if record is None:
                errors.append(f"touched_fact_ids references an unknown fact: {fact_id}")
                continue
            source = record.get("source")
            source_path = source.get("path") if isinstance(source, dict) else None
            if isinstance(source_path, str) and source_path not in read_paths:
                errors.append(
                    "required_reading must include the source for touched fact "
                    f"{fact_id}: {source_path}"
                )
    try:
        independent, confirmation, reasons = derived_risk(context)
    except ContinuityError as exc:
        errors.append(str(exc))
        independent = False
        confirmation = False
        reasons = []
    assessment = context.get("risk_assessment", {})
    if isinstance(assessment, dict):
        if assessment.get("requires_independent_review") is not independent:
            errors.append("risk_assessment.requires_independent_review does not match the declared risk")
        if not isinstance(assessment.get("rationale"), str) or not assessment.get("rationale", "").strip():
            errors.append("risk_assessment.rationale must not be empty")
    if confirmation and not str(context.get("author_confirmation_reference", "")).strip():
        errors.append("key or canon-changing chapters require an author confirmation reference")
    if errors:
        raise ContinuityError("; ".join(errors))
    context["derived_independent_review"] = independent
    context["derived_author_confirmation"] = confirmation
    context["derived_risk_reasons"] = reasons
    return context


def json_pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def json_diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if type(before) is not type(after):
        return [{"path": path or "/", "before_present": True, "before": before, "after_present": True, "after": after}]
    if isinstance(before, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{json_pointer_escape(str(key))}"
            if key not in before:
                changes.append({"path": child, "before_present": False, "after_present": True, "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "before_present": True, "before": before[key], "after_present": False})
            else:
                changes.extend(json_diff(before[key], after[key], child))
        return changes
    if isinstance(before, list):
        if before != after:
            return [{"path": path or "/", "before_present": True, "before": before, "after_present": True, "after": after}]
        return []
    if before != after:
        return [{"path": path or "/", "before_present": True, "before": before, "after_present": True, "after": after}]
    return []


def pointer_tokens(pointer: str) -> list[str]:
    if pointer == "/":
        return []
    if not pointer.startswith("/"):
        raise ContinuityError(f"Invalid JSON pointer: {pointer}")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def apply_change(document: Any, change: dict[str, Any]) -> Any:
    result = copy.deepcopy(document)
    tokens = pointer_tokens(str(change["path"]))
    if not tokens:
        return copy.deepcopy(change.get("after")) if change.get("after_present") else None
    target = result
    for token in tokens[:-1]:
        if not isinstance(target, dict) or token not in target:
            raise ContinuityError(f"State delta path does not exist: {change['path']}")
        target = target[token]
    if not isinstance(target, dict):
        raise ContinuityError(f"State delta parent is not an object: {change['path']}")
    key = tokens[-1]
    before_present = bool(change.get("before_present"))
    if (key in target) != before_present:
        raise ContinuityError(f"State delta before presence mismatch: {change['path']}")
    if before_present and target[key] != change.get("before"):
        raise ContinuityError(f"State delta before value mismatch: {change['path']}")
    if change.get("after_present"):
        target[key] = copy.deepcopy(change.get("after"))
    else:
        target.pop(key)
    return result


def _prepare_audit(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    package = resolve_package_directory(root, args.package)
    commit = read_json(package / "commit.json")
    chapter_number = commit.get("chapter_number")
    if not isinstance(chapter_number, int) or isinstance(chapter_number, bool):
        raise ContinuityError("commit.json chapter_number must be an integer")
    chapter = package_member(package, commit.get("chapter_file"), "chapter.md")
    state_path = package_member(
        package, commit.get("continuity_state_file"), "continuity-state.json"
    )
    context_path = package_member(
        package, commit.get("continuity_context_file"), "continuity-context.json"
    )
    context = validate_context(root, context_path, chapter_number)
    base_state = read_json(root / "continuity/state.json")
    result_state = read_json(state_path)
    changes = json_diff(base_state, result_state)
    for change in changes:
        if change["path"] == "/through_chapter":
            change.update({"reason": "Advance the committed continuity state to this chapter", "evidence": []})
        else:
            change.update({"reason": "", "evidence": []})
    chapter_hash = sha256_file(chapter)
    state_delta = {
        "schema_version": SCHEMA_VERSION,
        "record_kind": "chapter_state_delta",
        "status": "draft",
        "chapter_number": chapter_number,
        "chapter_sha256": chapter_hash,
        "context_sha256": sha256_file(context_path),
        "base_canon_sha256": context["base_canon_sha256"],
        "base_state_sha256": sha256_bytes(dump_json(base_state).encode("utf-8")),
        "result_state_sha256": sha256_bytes(dump_json(result_state).encode("utf-8")),
        "entry_state": {},
        "state_changes": [],
        "exit_state": {},
        "changes": changes,
        "fact_changes": [],
        "exception_changes": [],
    }
    checks = {dimension: {"status": "pass", "rationale": "", "evidence": []} for dimension in REVIEW_DIMENSIONS}
    audit = {
        "schema_version": SCHEMA_VERSION,
        "report_kind": "short_story_continuity_audit" if context["work_type"] == "short_story" else "chapter_continuity_audit",
        "status": "draft",
        "chapter_number": chapter_number,
        "chapter_sha256": chapter_hash,
        "context_sha256": sha256_file(context_path),
        "state_delta_sha256": None,
        "binding_status": "pending_state_delta",
        "base_canon_sha256": context["base_canon_sha256"],
        "reviewed_dimensions": list(REVIEW_DIMENSIONS),
        "checks": checks,
        "reviewer": {"mode": "independent" if context["derived_independent_review"] else "self", "reviewer_id": "", "independent_context": bool(context["derived_independent_review"])},
        "decision": "pass",
        "findings": [],
        "residual_risks": [],
        "quality_review_separate": True,
        "reviewed_at": "",
    }
    delta_path = package_member(
        package, commit.get("state_delta_file"), "state-delta.json", must_exist=False
    )
    audit_path = package_member(
        package,
        commit.get("continuity_audit_file"),
        "continuity-audit.json",
        must_exist=False,
    )
    if delta_path.exists() or audit_path.exists():
        raise ContinuityError("Refusing to overwrite an existing state delta or continuity audit")
    transactional_write(
        [
            (delta_path, dump_json(state_delta).encode("utf-8")),
            (audit_path, dump_json(audit).encode("utf-8")),
        ],
        journal_root=root,
    )
    return {"status": "prepared", "chapter_number": chapter_number, "state_delta": str(delta_path), "continuity_audit": str(audit_path), "requires_independent_review": context["derived_independent_review"]}


def prepare_audit(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    with project_write_context(root, args) as context:
        result = _prepare_audit(args, root)
        context.assert_live()
        context.refresh_base_after_write("continuity audit preparation")
        return result


def _bind_audit(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    package = resolve_package_directory(root, args.package)
    commit = read_json(package / "commit.json")
    chapter_number = commit.get("chapter_number")
    if not isinstance(chapter_number, int) or isinstance(chapter_number, bool):
        raise ContinuityError("commit.json chapter_number must be an integer")
    candidate = package_member(package, commit.get("chapter_file"), "chapter.md")
    state_path = package_member(
        package, commit.get("continuity_state_file"), "continuity-state.json"
    )
    context_path = package_member(
        package, commit.get("continuity_context_file"), "continuity-context.json"
    )
    delta_path = package_member(
        package, commit.get("state_delta_file"), "state-delta.json"
    )
    audit_path = package_member(
        package, commit.get("continuity_audit_file"), "continuity-audit.json"
    )
    context = validate_context(root, context_path, chapter_number)
    staged_state = read_json(state_path)
    validate_state_delta(root, package, commit, context, candidate, staged_state)
    audit = read_json(audit_path)
    if audit.get("status") != "draft":
        raise ContinuityError("Only a draft continuity audit can be rebound")
    audit.update(
        {
            "chapter_number": chapter_number,
            "chapter_sha256": sha256_file(candidate),
            "context_sha256": sha256_file(context_path),
            "state_delta_sha256": sha256_file(delta_path),
            "base_canon_sha256": context["base_canon_sha256"],
            "binding_status": "current",
        }
    )
    transactional_write(
        [(audit_path, dump_json(audit).encode("utf-8"))], journal_root=root
    )
    return {
        "status": "bound",
        "chapter_number": chapter_number,
        "continuity_audit": str(audit_path),
        "state_delta_sha256": audit["state_delta_sha256"],
        "requires_independent_review": context["derived_independent_review"],
    }


def bind_audit(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    with project_write_context(root, args) as context:
        result = _bind_audit(args, root)
        context.assert_live()
        context.refresh_base_after_write("continuity audit binding")
        return result


def fact_record_errors(record: Any, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return [f"{label} must be an object"]
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label}.schema_version is unsupported")
    if not isinstance(record.get("fact_id"), str) or not FACT_ID.fullmatch(record["fact_id"]):
        errors.append(f"{label}.fact_id is invalid")
    if record.get("category") not in FACT_CATEGORIES:
        errors.append(f"{label}.category is invalid")
    for key in ("subject", "predicate"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            errors.append(f"{label}.{key} must not be empty")
    if "object" not in record or record.get("object") is None:
        errors.append(f"{label}.object must be present")
    if record.get("status") not in FACT_STATUSES:
        errors.append(f"{label}.status is invalid")
    if record.get("significance") not in SIGNIFICANCE_LEVELS:
        errors.append(f"{label}.significance is invalid")
    chapter = record.get("valid_from_chapter")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 0:
        errors.append(f"{label}.valid_from_chapter must be non-negative")
    source = record.get("source")
    if not isinstance(source, dict):
        errors.append(f"{label}.source must be an object")
    else:
        for key in ("path", "location", "quote"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                errors.append(f"{label}.source.{key} must not be empty")
    if record.get("category") == "author_plan" and record.get("plan_status") not in OUTLINE_STATUSES:
        errors.append(
            f"{label}.plan_status must be locked, planned, optional, or abandoned"
        )
    valid_to = record.get("valid_to_chapter")
    if valid_to is not None and (
        not isinstance(valid_to, int)
        or isinstance(valid_to, bool)
        or not isinstance(chapter, int)
        or valid_to < chapter
    ):
        errors.append(f"{label}.valid_to_chapter must not precede valid_from_chapter")
    return errors


def exception_record_errors(record: Any, label: str) -> list[str]:
    if not isinstance(record, dict):
        return [f"{label} must be an object"]
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label}.schema_version is unsupported")
    if not isinstance(record.get("exception_id"), str) or not EXCEPTION_ID.fullmatch(record["exception_id"]):
        errors.append(f"{label}.exception_id is invalid")
    for key in ("exception_type", "description", "narrative_purpose", "status"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            errors.append(f"{label}.{key} must not be empty")
    return errors


def record_sha(record: dict[str, Any]) -> str:
    return sha256_bytes(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def apply_record_changes(
    current: list[dict[str, Any]],
    changes: Any,
    *,
    id_key: str,
    validator: Any,
) -> list[dict[str, Any]]:
    if not isinstance(changes, list):
        raise ContinuityError(f"{id_key} changes must be an array")
    records = {str(item.get(id_key)): item for item in current}
    for index, change in enumerate(changes):
        label = f"{id_key}_changes[{index}]"
        if not isinstance(change, dict) or change.get("operation") not in CHANGE_OPERATIONS:
            raise ContinuityError(f"{label}.operation is invalid")
        record = change.get("record")
        errors = validator(record, f"{label}.record")
        if errors:
            raise ContinuityError("; ".join(errors))
        record_id = str(record[id_key])
        existing = records.get(record_id)
        operation = change["operation"]
        if operation == "add" and existing is not None:
            raise ContinuityError(f"{label} cannot add an existing {id_key}: {record_id}")
        if operation in {"update", "retire"}:
            if existing is None:
                raise ContinuityError(f"{label} cannot change a missing {id_key}: {record_id}")
            if change.get("before_sha256") != record_sha(existing):
                raise ContinuityError(f"{label}.before_sha256 does not bind the current record")
        if not isinstance(change.get("reason"), str) or not change["reason"].strip():
            raise ContinuityError(f"{label}.reason must not be empty")
        records[record_id] = record
    return list(records.values())


def validate_state_delta(
    root: Path,
    package: Path,
    commit: dict[str, Any],
    context: dict[str, Any],
    candidate: Path,
    staged_state: dict[str, Any],
) -> tuple[dict[str, Any], bytes, bytes]:
    path = package_member(package, commit.get("state_delta_file"), "state-delta.json")
    delta = read_json(path)
    chapter_number = int(commit["chapter_number"])
    errors: list[str] = []
    if delta.get("schema_version") != SCHEMA_VERSION or delta.get("status") != "complete":
        errors.append("state-delta.json must use the current schema and status=complete")
    bindings = {
        "chapter_number": chapter_number,
        "chapter_sha256": sha256_file(candidate),
        "context_sha256": sha256_file(
            package_member(
                package,
                commit.get("continuity_context_file"),
                "continuity-context.json",
            )
        ),
        "base_canon_sha256": context["base_canon_sha256"],
    }
    for key, value in bindings.items():
        if delta.get(key) != value:
            errors.append(f"state delta binding mismatch: {key}")
    base_state = read_json(root / "continuity/state.json")
    if delta.get("base_state_sha256") != sha256_bytes(dump_json(base_state).encode("utf-8")):
        errors.append("state delta does not bind the current base state")
    if delta.get("result_state_sha256") != sha256_bytes(dump_json(staged_state).encode("utf-8")):
        errors.append("state delta does not bind the staged result state")
    for key, expected_type in (("entry_state", dict), ("state_changes", list), ("exit_state", dict)):
        if not isinstance(delta.get(key), expected_type):
            errors.append(f"state delta {key} has an invalid type")
    changes = delta.get("changes")
    if not isinstance(changes, list):
        errors.append("state delta changes must be an array")
        changes = []
    actual = copy.deepcopy(base_state)
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            errors.append(f"state delta changes[{index}] must be an object")
            continue
        if not isinstance(change.get("reason"), str) or not change["reason"].strip():
            errors.append(f"state delta changes[{index}].reason must not be empty")
        if change.get("path") != "/through_chapter":
            errors.extend(validate_evidence(root, context, candidate, change.get("evidence"), f"state delta changes[{index}].evidence"))
        try:
            actual = apply_change(actual, change)
        except ContinuityError as exc:
            errors.append(str(exc))
    if actual != staged_state:
        errors.append("state delta changes do not reproduce continuity-state.json exactly")
    expected_changes = [{key: value for key, value in item.items() if key not in {"reason", "evidence"}} for item in json_diff(base_state, staged_state)]
    supplied_changes = [{key: value for key, value in item.items() if key not in {"reason", "evidence"}} for item in changes if isinstance(item, dict)]
    if supplied_changes != expected_changes:
        errors.append("state delta must enumerate every state change exactly once")
    try:
        facts = apply_record_changes(read_jsonl(root / FACTS_PATH), delta.get("fact_changes"), id_key="fact_id", validator=fact_record_errors)
        exceptions = apply_record_changes(read_jsonl(root / EXCEPTIONS_PATH), delta.get("exception_changes"), id_key="exception_id", validator=exception_record_errors)
    except ContinuityError as exc:
        errors.append(str(exc))
        facts = []
        exceptions = []
    for group_name, group in (("fact_changes", delta.get("fact_changes", [])), ("exception_changes", delta.get("exception_changes", []))):
        if isinstance(group, list):
            for index, change in enumerate(group):
                if isinstance(change, dict):
                    errors.extend(validate_evidence(root, context, candidate, change.get("evidence"), f"{group_name}[{index}].evidence"))
                    if group_name == "fact_changes":
                        record = change.get("record")
                        source = record.get("source") if isinstance(record, dict) else None
                        errors.extend(
                            validate_evidence(
                                root,
                                context,
                                candidate,
                                [source],
                                f"{group_name}[{index}].record.source",
                            )
                        )
    if errors:
        raise ContinuityError("; ".join(errors))
    return delta, dump_jsonl(facts, "fact_id"), dump_jsonl(exceptions, "exception_id")


def expected_audit_decision(findings: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "blocker" for item in findings):
        return "block"
    if any(item.get("severity") == "error" and item.get("author_judgment") for item in findings):
        return "needs_author"
    if any(item.get("severity") == "error" for item in findings):
        return "block"
    return "pass"


def validate_audit(
    root: Path,
    package: Path,
    commit: dict[str, Any],
    context: dict[str, Any],
    delta: dict[str, Any],
    candidate: Path,
) -> tuple[dict[str, Any], Path, bytes]:
    source = package_member(
        package, commit.get("continuity_audit_file"), "continuity-audit.json"
    )
    audit = read_json(source)
    errors: list[str] = []
    expected_kind = "short_story_continuity_audit" if context["work_type"] == "short_story" else "chapter_continuity_audit"
    if audit.get("schema_version") != SCHEMA_VERSION or audit.get("report_kind") != expected_kind:
        errors.append("continuity audit has an unsupported schema or report_kind")
    if audit.get("status") != "complete":
        errors.append("continuity audit status must be complete")
    if audit.get("binding_status") != "current":
        errors.append("continuity audit must be rebound after state-delta completion")
    bindings = {
        "chapter_number": commit["chapter_number"],
        "chapter_sha256": sha256_file(candidate),
        "context_sha256": sha256_file(
            package_member(
                package,
                commit.get("continuity_context_file"),
                "continuity-context.json",
            )
        ),
        "state_delta_sha256": sha256_file(
            package_member(package, commit.get("state_delta_file"), "state-delta.json")
        ),
        "base_canon_sha256": context["base_canon_sha256"],
    }
    for key, value in bindings.items():
        if audit.get(key) != value:
            errors.append(f"continuity audit binding mismatch: {key}")
    if audit.get("reviewed_dimensions") != list(REVIEW_DIMENSIONS):
        errors.append("continuity audit must list every continuity dimension")
    checks = audit.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(REVIEW_DIMENSIONS):
        errors.append("continuity audit checks must cover every continuity dimension")
    else:
        for dimension in REVIEW_DIMENSIONS:
            check = checks[dimension]
            if not isinstance(check, dict) or check.get("status") not in CHECK_STATUSES:
                errors.append(f"continuity check {dimension} has an invalid status")
                continue
            if not isinstance(check.get("rationale"), str) or not check["rationale"].strip():
                errors.append(f"continuity check {dimension} requires a rationale")
            errors.extend(validate_evidence(root, context, candidate, check.get("evidence"), f"checks.{dimension}.evidence", allow_empty=check.get("status") == "not_applicable"))
    findings = audit.get("findings")
    if not isinstance(findings, list):
        errors.append("continuity audit findings must be an array")
        findings = []
    else:
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                errors.append(f"findings[{index}] must be an object")
                continue
            if finding.get("severity") not in FINDING_SEVERITIES:
                errors.append(f"findings[{index}].severity is invalid")
            if finding.get("dimension") not in REVIEW_DIMENSIONS:
                errors.append(f"findings[{index}].dimension is invalid")
            certainty = finding.get("certainty")
            if certainty not in FINDING_CERTAINTIES:
                errors.append(f"findings[{index}].certainty is invalid")
            severity = finding.get("severity")
            if severity in {"blocker", "error"} and certainty != "confirmed":
                errors.append(
                    f"findings[{index}] blocking severities require certainty=confirmed"
                )
            if certainty in {"suspected", "intentional_exception"} and severity not in {"warning", "note"}:
                errors.append(
                    f"findings[{index}] suspected or intentional items must be warnings or notes"
                )
            for key in ("id", "problem", "impact", "suggested_fix"):
                if not isinstance(finding.get(key), str) or not finding[key].strip():
                    errors.append(f"findings[{index}].{key} must not be empty")
            if not isinstance(finding.get("author_judgment"), bool):
                errors.append(f"findings[{index}].author_judgment must be boolean")
            errors.extend(validate_evidence(root, context, candidate, finding.get("evidence"), f"findings[{index}].evidence"))
    decision = audit.get("decision")
    if decision not in AUDIT_DECISIONS:
        errors.append("continuity audit decision is invalid")
    elif decision != expected_audit_decision(findings):
        errors.append(f"continuity audit decision must be {expected_audit_decision(findings)} for its findings")
    if decision != "pass":
        errors.append("chapter commit requires a passing continuity audit")
    reviewer = audit.get("reviewer")
    if not isinstance(reviewer, dict):
        errors.append("continuity audit reviewer must be an object")
    else:
        mode = reviewer.get("mode")
        if mode not in {"self", "independent"}:
            errors.append("continuity audit reviewer.mode must be self or independent")
        if not isinstance(reviewer.get("reviewer_id"), str) or not reviewer["reviewer_id"].strip():
            errors.append("continuity audit reviewer_id must not be empty")
        if context["derived_independent_review"] and (mode != "independent" or reviewer.get("independent_context") is not True):
            errors.append("declared chapter risk requires an independent-context reviewer")
    if audit.get("quality_review_separate") is not True:
        errors.append("continuity and quality review must remain separate")
    if not isinstance(audit.get("reviewed_at"), str) or not audit["reviewed_at"].strip():
        errors.append("continuity audit reviewed_at must not be empty")
    if errors:
        raise ContinuityError("; ".join(errors))
    audit_bytes = source.read_bytes()
    target_name = f"chapter-continuity-{int(commit['chapter_number']):04d}-{bindings['chapter_sha256'][:10]}-{sha256_bytes(audit_bytes)[:10]}.json"
    return audit, root / AUDITS_DIR / target_name, audit_bytes


def dependency_chapter_from_path(relative: str) -> int | None:
    name = Path(relative).name
    match = CHAPTER_FILE.fullmatch(name)
    if match and (relative.startswith("manuscript/") or relative.startswith("memory/chapters/")):
        return int(match.group("number"))
    return None


def validate_chapter_package(
    raw_root: str | Path,
    package: Path,
    commit: dict[str, Any],
    candidate: Path,
    staged_state: dict[str, Any],
) -> dict[str, Any]:
    root = resolve_root(raw_root)
    package = resolve_package_directory(root, package)
    ensure_ready(root, "chapter commit")
    chapter_number = int(commit["chapter_number"])
    context_path = package_member(
        package, commit.get("continuity_context_file"), "continuity-context.json"
    )
    expected_candidate = package_member(
        package, commit.get("chapter_file"), "chapter.md"
    )
    if candidate.resolve() != expected_candidate:
        raise ContinuityError("Candidate chapter path does not match commit.json")
    candidate = expected_candidate
    context = validate_context(root, context_path, chapter_number)
    delta, facts_bytes, exceptions_bytes = validate_state_delta(root, package, commit, context, candidate, staged_state)
    audit, audit_target, audit_bytes = validate_audit(root, package, commit, context, delta, candidate)
    dependencies = read_json(root / DEPENDENCIES_PATH)
    chapters = dependencies.setdefault("chapters", {})
    reading_paths = [item["path"] for item in context["required_reading"]]
    depends_on = sorted({number for path in reading_paths if (number := dependency_chapter_from_path(path)) is not None and number < chapter_number})
    fact_ids = sorted({str(item.get("record", {}).get("fact_id")) for item in delta.get("fact_changes", []) if item.get("record", {}).get("fact_id")} | {str(value) for value in context.get("touched_fact_ids", [])})
    audit_relative = audit_target.relative_to(root).as_posix()
    chapters[f"{chapter_number:04d}"] = {
        "chapter_sha256": sha256_file(candidate),
        "audit_path": audit_relative,
        "audit_sha256": sha256_bytes(audit_bytes),
        "context_sha256": sha256_file(context_path),
        "state_delta_sha256": sha256_file(
            package_member(package, commit.get("state_delta_file"), "state-delta.json")
        ),
        "fact_ids": fact_ids,
        "depends_on_chapters": depends_on,
        "evidence_paths": sorted(set(reading_paths + ["candidate"])),
        "risk_triggers": sorted(context["risk_assessment"]["triggers"]),
        "reviewer_mode": audit["reviewer"]["mode"],
        "covered_by_baseline": False,
    }
    return {
        "context": context,
        "state_delta": delta,
        "audit": audit,
        "audit_target": audit_target,
        "audit_bytes": audit_bytes,
        "facts_bytes": facts_bytes,
        "exceptions_bytes": exceptions_bytes,
        "dependencies_bytes": dump_json(dependencies).encode("utf-8"),
    }


def finalize_head_write(
    raw_root: str | Path,
    writes: list[tuple[Path, bytes]],
    chapter_number: int,
    audit_relative: str,
) -> tuple[Path, bytes, str]:
    root = resolve_root(raw_root)
    old_head = read_json(root / HEAD_PATH)
    overrides: dict[str, bytes] = {}
    for path, content in writes:
        resolved = path.resolve()
        if is_within(resolved, root):
            overrides[resolved.relative_to(root).as_posix()] = content
    _, canon_hash = canonical_snapshot(root, overrides)
    head = {
        "schema_version": SCHEMA_VERSION,
        "status": "current",
        "through_chapter": chapter_number,
        "canon_sha256": canon_hash,
        "parent_canon_sha256": old_head.get("canon_sha256"),
        "baseline_file": old_head.get("baseline_file"),
        "baseline_sha256": old_head.get("baseline_sha256"),
        "latest_audit": audit_relative,
        "authorization_reference": "Hash-bound chapter continuity audit passed",
        "updated_at": utc_now(),
    }
    return root / HEAD_PATH, dump_json(head).encode("utf-8"), canon_hash


def validate_baseline_fact_source(root: Path, snapshot: list[dict[str, str]], record: dict[str, Any], label: str) -> list[str]:
    errors = fact_record_errors(record, label)
    if errors:
        return errors
    source = record.get("source", {})
    relative = source.get("path")
    quote = source.get("quote")
    if not isinstance(relative, str) or relative not in source_entry_map(snapshot):
        errors.append(f"{label}.source.path is not in the baseline snapshot")
    elif not isinstance(quote, str) or not quote.strip():
        errors.append(f"{label}.source.quote must not be empty")
    else:
        try:
            text = _read_stable_bytes(
                root / relative, label=f"{label}.source"
            ).decode("utf-8")
            if quote.strip() not in text:
                errors.append(f"{label}.source.quote was not found verbatim")
        except (ContinuityError, UnicodeError) as exc:
            errors.append(f"{label}.source cannot be read: {exc}")
    return errors


def build_baseline_packet(root: Path) -> dict[str, Any]:
    missing_files = [relative for relative in scaffold_contents() if not (root / relative).is_file()]
    missing_directories = [relative for relative in (BASELINES_DIR, AUDITS_DIR) if not (root / relative).is_dir()]
    if missing_files or missing_directories:
        missing = ", ".join([*missing_directories, *missing_files])
        raise ContinuityError(
            "Continuity hard gate is not installed; run novel_project.py upgrade "
            f"before preparing a baseline. Missing: {missing}"
        )
    current = current_chapter(root)
    snapshot, canon_hash = canonical_snapshot(root)
    chapters = [path for _, path in chapter_paths(root, current)]
    return {
        "schema_version": SCHEMA_VERSION,
        "packet_kind": "canon_continuity_baseline_packet",
        "status": "prepared",
        "project_root": str(root),
        "chapter_from": 1 if current else 0,
        "chapter_through": current,
        "source_snapshot": snapshot,
        "source_snapshot_sha256": canon_hash,
        "required_full_text_chapters": chapters,
        "required_dimensions": list(REVIEW_DIMENSIONS),
        "requires_independent_review": current > 0,
        "scaffold_status": "installed",
        "prepared_at": utc_now(),
    }


def baseline_report_template(packet: dict[str, Any], packet_hash: str) -> dict[str, Any]:
    through = int(packet["chapter_through"])
    return {
        "schema_version": SCHEMA_VERSION,
        "report_kind": "canon_continuity_baseline_review",
        "status": "draft",
        "project_root": packet["project_root"],
        "chapter_from": packet["chapter_from"],
        "chapter_through": through,
        "packet_sha256": packet_hash,
        "reviewed_dimensions": list(REVIEW_DIMENSIONS),
        "reviewed_chapters": [],
        "reviewer": {"mode": "independent" if through else "deterministic", "reviewer_id": "", "independent_context": bool(through)},
        "decision": "pass",
        "summary": "",
        "findings": [],
        "residual_risks": [],
        "facts": [],
        "intentional_exceptions": [],
        "chapter_dependencies": {},
        "reviewed_at": "",
    }


def prepare_baseline(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    output = safe_output(args.output, project_root=root)
    report_raw = args.report_output or output.with_name(output.stem + "-report.json")
    report_output = safe_output(report_raw, project_root=root)
    packet = build_baseline_packet(root)
    packet_bytes = dump_json(packet).encode("utf-8")
    report = baseline_report_template(packet, sha256_bytes(packet_bytes))
    write_external_pair(
        output,
        packet_bytes,
        report_output,
        dump_json(report).encode("utf-8"),
    )
    return {"status": "prepared", "packet": str(output), "packet_sha256": sha256_bytes(packet_bytes), "report_template": str(report_output), "chapter_through": packet["chapter_through"], "required_full_text_chapters": packet["required_full_text_chapters"]}


def validate_baseline_finding(
    root: Path,
    snapshot: list[dict[str, str]],
    finding: Any,
    index: int,
) -> list[str]:
    label = f"findings[{index}]"
    if not isinstance(finding, dict):
        return [f"{label} must be an object"]
    errors: list[str] = []
    if finding.get("severity") not in FINDING_SEVERITIES:
        errors.append(f"{label}.severity is invalid")
    for key in ("id", "location", "problem", "impact", "suggested_fix"):
        if not isinstance(finding.get(key), str) or not finding[key].strip():
            errors.append(f"{label}.{key} must not be empty")
    if finding.get("dimension") not in REVIEW_DIMENSIONS:
        errors.append(f"{label}.dimension is invalid")
    certainty = finding.get("certainty")
    if certainty not in FINDING_CERTAINTIES:
        errors.append(f"{label}.certainty is invalid")
    severity = finding.get("severity")
    if severity in {"blocker", "error"} and certainty != "confirmed":
        errors.append(f"{label} blocking severities require certainty=confirmed")
    if certainty in {"suspected", "intentional_exception"} and severity not in {"warning", "note"}:
        errors.append(
            f"{label} suspected or intentional items must be warnings or notes"
        )
    context = {"source_snapshot": snapshot}
    errors.extend(
        validate_evidence(
            root,
            context,
            root / "novel.json",
            finding.get("evidence"),
            f"{label}.evidence",
        )
    )
    if not isinstance(finding.get("author_judgment"), bool):
        errors.append(f"{label}.author_judgment must be boolean")
    return errors


def validate_baseline_report(root: Path, packet_path: Path, report_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    packet_path, packet_bytes = _external_baseline_input(
        packet_path, project_root=root, label="Baseline packet"
    )
    report_path, report_bytes = _external_baseline_input(
        report_path, project_root=root, label="Baseline report"
    )
    if packet_path == report_path:
        raise ContinuityError("Baseline packet and report must be different files")
    packet = _json_object_from_bytes(packet_bytes, label="Baseline packet")
    report = _json_object_from_bytes(report_bytes, label="Baseline report")
    return _validate_baseline_report_data(
        root,
        packet,
        report,
        packet_bytes=packet_bytes,
    )


def _validate_baseline_report_data(
    root: Path,
    packet: dict[str, Any],
    report: dict[str, Any],
    *,
    packet_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    if packet.get("schema_version") != SCHEMA_VERSION or packet.get("packet_kind") != "canon_continuity_baseline_packet":
        errors.append("baseline packet has an unsupported schema or kind")
    if Path(str(packet.get("project_root", ""))).resolve() != root:
        errors.append("baseline packet belongs to another project")
    raw_snapshot = packet.get("source_snapshot")
    snapshot = raw_snapshot if isinstance(raw_snapshot, list) else []
    errors.extend(validate_snapshot_current(root, raw_snapshot))
    try:
        snapshot_hash = snapshot_digest(snapshot)
    except (KeyError, TypeError, UnicodeEncodeError):
        snapshot_hash = None
    if packet.get("source_snapshot_sha256") != snapshot_hash:
        errors.append("baseline packet source snapshot hash is invalid")
    try:
        expected_packet = build_baseline_packet(root)
    except ContinuityError as exc:
        errors.append(str(exc))
    else:
        protected_packet_fields = (
            "status",
            "chapter_from",
            "chapter_through",
            "source_snapshot",
            "source_snapshot_sha256",
            "required_full_text_chapters",
            "required_dimensions",
            "requires_independent_review",
            "scaffold_status",
        )
        mismatched = [
            key
            for key in protected_packet_fields
            if packet.get(key) != expected_packet.get(key)
        ]
        if mismatched:
            errors.append(
                "baseline packet does not exactly match the current review scope: "
                + ", ".join(mismatched)
            )
    if report.get("schema_version") != SCHEMA_VERSION or report.get("report_kind") != "canon_continuity_baseline_review":
        errors.append("baseline report has an unsupported schema or kind")
    if report.get("packet_sha256") != sha256_bytes(packet_bytes):
        errors.append("baseline report does not bind the current packet")
    if report.get("status") != "complete":
        errors.append("baseline report status must be complete")
    if report.get("reviewed_dimensions") != list(REVIEW_DIMENSIONS):
        errors.append("baseline report must cover every continuity dimension")
    raw_through = packet.get("chapter_through")
    if not isinstance(raw_through, int) or isinstance(raw_through, bool) or raw_through < 0:
        errors.append("baseline packet chapter_through must be a non-negative integer")
        through = -1
    else:
        through = raw_through
    for key in ("project_root", "chapter_from", "chapter_through"):
        if report.get(key) != packet.get(key):
            errors.append(f"baseline report {key} does not match the packet")
    expected_chapters = list(range(1, through + 1))
    if report.get("reviewed_chapters") != expected_chapters:
        errors.append("baseline report reviewed_chapters must list every chapter in order")
    reviewer = report.get("reviewer")
    if through and (not isinstance(reviewer, dict) or reviewer.get("mode") != "independent" or reviewer.get("independent_context") is not True or not str(reviewer.get("reviewer_id", "")).strip()):
        errors.append("a non-empty baseline requires an identified independent-context reviewer")
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        errors.append("baseline report summary must not be empty")
    if not isinstance(report.get("reviewed_at"), str) or not report["reviewed_at"].strip():
        errors.append("baseline report reviewed_at must not be empty")
    findings = report.get("findings")
    if not isinstance(findings, list):
        errors.append("baseline findings must be an array")
        findings = []
    else:
        for index, finding in enumerate(findings):
            errors.extend(
                validate_baseline_finding(
                    root, snapshot, finding, index
                )
            )
    decision = report.get("decision")
    if decision not in AUDIT_DECISIONS:
        errors.append("baseline decision is invalid")
    elif decision != expected_audit_decision(findings):
        errors.append(f"baseline decision must be {expected_audit_decision(findings)} for its findings")
    facts = report.get("facts")
    if not isinstance(facts, list):
        errors.append("baseline facts must be an array")
        facts = []
    seen_facts: set[str] = set()
    for index, fact in enumerate(facts):
        errors.extend(validate_baseline_fact_source(root, snapshot, fact, f"facts[{index}]"))
        if isinstance(fact, dict):
            fact_id = fact.get("fact_id")
            if fact_id in seen_facts:
                errors.append(f"duplicate baseline fact_id: {fact_id}")
            seen_facts.add(str(fact_id))
    exceptions = report.get("intentional_exceptions")
    if not isinstance(exceptions, list):
        errors.append("intentional_exceptions must be an array")
        exceptions = []
    seen_exceptions: set[str] = set()
    for index, item in enumerate(exceptions):
        errors.extend(exception_record_errors(item, f"intentional_exceptions[{index}]"))
        if isinstance(item, dict):
            exception_id = str(item.get("exception_id"))
            if exception_id in seen_exceptions:
                errors.append(f"duplicate baseline exception_id: {exception_id}")
            seen_exceptions.add(exception_id)
    dependencies = report.get("chapter_dependencies")
    if not isinstance(dependencies, dict):
        errors.append("chapter_dependencies must be an object")
        dependencies = {}
    elif set(dependencies) != {f"{number:04d}" for number in expected_chapters}:
        errors.append("chapter_dependencies must exactly cover every reviewed chapter")
    chapter_map = dict(chapter_paths(root, through))
    for number in expected_chapters:
        key = f"{number:04d}"
        entry = dependencies.get(key)
        if not isinstance(entry, dict):
            errors.append(f"chapter_dependencies is missing {key}")
            continue
        fact_ids = entry.get("fact_ids")
        if not isinstance(fact_ids, list) or not fact_ids or any(str(value) not in seen_facts for value in fact_ids):
            errors.append(f"chapter_dependencies[{key}].fact_ids must reference baseline facts")
        depends_on = entry.get("depends_on_chapters")
        if not isinstance(depends_on, list) or any(not isinstance(value, int) or value < 1 or value >= number for value in depends_on):
            errors.append(f"chapter_dependencies[{key}].depends_on_chapters is invalid")
        evidence_paths = entry.get("evidence_paths")
        own_path = chapter_map.get(number)
        if not isinstance(evidence_paths, list) or own_path not in evidence_paths:
            errors.append(f"chapter_dependencies[{key}] must include its own original chapter path")
        triggers = entry.get("risk_triggers")
        if not isinstance(triggers, list) or any(value not in RISK_TRIGGERS for value in triggers):
            errors.append(f"chapter_dependencies[{key}].risk_triggers is invalid")
    if through and not facts:
        errors.append("a non-empty baseline must extract stable canonical facts")
    if errors:
        raise ContinuityError("; ".join(errors))
    return packet, report


def _record_baseline(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    packet_path, packet_bytes = _external_baseline_input(
        args.packet, project_root=root, label="Baseline packet"
    )
    report_path, report_bytes = _external_baseline_input(
        args.report, project_root=root, label="Baseline report"
    )
    if packet_path == report_path:
        raise ContinuityError("Baseline packet and report must be different files")
    authorization = str(args.authorization_reference or "").strip()
    if not authorization:
        raise ContinuityError("authorization_reference must not be empty")
    packet = _json_object_from_bytes(packet_bytes, label="Baseline packet")
    report = _json_object_from_bytes(report_bytes, label="Baseline report")
    packet, report = _validate_baseline_report_data(
        root,
        packet,
        report,
        packet_bytes=packet_bytes,
    )
    source_preconditions = {
        packet_path: sha256_bytes(packet_bytes),
        report_path: sha256_bytes(report_bytes),
    }
    snapshot_preconditions = {
        root / entry["path"]: entry["sha256"]
        for entry in packet["source_snapshot"]
    }
    if report["decision"] != "pass":
        source_preconditions.update(snapshot_preconditions)
        target = root / AUDITS_DIR / f"baseline-review-{int(packet['chapter_through']):04d}-{compact_stamp()}-{sha256_bytes(report_bytes)[:10]}.json"
        transactional_write(
            [(target, report_bytes)],
            journal_root=root,
            expected_existing=source_preconditions,
            expected_targets={target: None},
        )
        return {"status": "blocked", "decision": report["decision"], "report_path": target.relative_to(root).as_posix(), "findings": len(report["findings"])}
    facts_bytes = dump_jsonl(report["facts"], "fact_id")
    exceptions_bytes = dump_jsonl(report["intentional_exceptions"], "exception_id")
    chapters: dict[str, Any] = {}
    chapter_map = dict(chapter_paths(root, int(packet["chapter_through"])))
    for key, entry in report["chapter_dependencies"].items():
        number = int(key)
        chapters[key] = {
            **entry,
            "chapter_sha256": snapshot_preconditions[root / chapter_map[number]],
            "audit_path": None,
            "audit_sha256": None,
            "context_sha256": None,
            "state_delta_sha256": None,
            "reviewer_mode": "independent",
            "covered_by_baseline": True,
        }
    dependencies_bytes = dump_json({"schema_version": SCHEMA_VERSION, "chapters": chapters}).encode("utf-8")
    invalidations_source = _read_stable_bytes(
        root / INVALIDATIONS_PATH, label="continuity invalidations"
    )
    if sha256_bytes(invalidations_source) != snapshot_preconditions[root / INVALIDATIONS_PATH]:
        raise ContinuityError("continuity invalidations changed after baseline validation")
    invalidations = _json_object_from_bytes(
        invalidations_source, label="continuity invalidations"
    )
    for item in invalidations.get("items", []):
        if isinstance(item, dict) and item.get("status") == "open":
            item["status"] = "resolved"
            item["resolved_at"] = utc_now()
            item["resolution"] = "Covered by a new full continuity baseline"
    invalidations_bytes = dump_json(invalidations).encode("utf-8")
    overrides = {
        FACTS_PATH: facts_bytes,
        EXCEPTIONS_PATH: exceptions_bytes,
        DEPENDENCIES_PATH: dependencies_bytes,
        INVALIDATIONS_PATH: invalidations_bytes,
    }
    replaced_sources = {root / relative for relative in overrides}
    source_preconditions.update(
        {
            path: digest
            for path, digest in snapshot_preconditions.items()
            if path not in replaced_sources
        }
    )
    snapshot, canon_hash = canonical_snapshot(root, overrides)
    report_hash = sha256_bytes(report_bytes)
    baseline = build_baseline_record(
        root=root,
        through=int(packet["chapter_through"]),
        snapshot=snapshot,
        canon_hash=canon_hash,
        authorization_reference=authorization,
        reviewer=report["reviewer"],
        findings=report["findings"],
        residual_risks=report.get("residual_risks", []),
        packet_sha256=sha256_bytes(packet_bytes),
        report_sha256=report_hash,
    )
    baseline_path = baseline_path_for(
        root, int(packet["chapter_through"]), canon_hash, report_hash
    )
    baseline_bytes = dump_json(baseline).encode("utf-8")
    head_path = root / HEAD_PATH
    if head_path.is_file():
        previous_head_bytes = _read_stable_bytes(head_path, label="continuity head")
        previous_head = _json_object_from_bytes(
            previous_head_bytes, label="continuity head"
        )
        previous_head_hash = sha256_bytes(previous_head_bytes)
    else:
        previous_head = {}
        previous_head_hash = None
    head = {
        "schema_version": SCHEMA_VERSION,
        "status": "current",
        "through_chapter": int(packet["chapter_through"]),
        "canon_sha256": canon_hash,
        "parent_canon_sha256": previous_head.get("canon_sha256"),
        "baseline_file": baseline_path.relative_to(root).as_posix(),
        "baseline_sha256": sha256_bytes(baseline_bytes),
        "latest_audit": None,
        "authorization_reference": authorization,
        "updated_at": utc_now(),
    }
    writes = [
        (root / FACTS_PATH, facts_bytes),
        (root / EXCEPTIONS_PATH, exceptions_bytes),
        (root / DEPENDENCIES_PATH, dependencies_bytes),
        (root / INVALIDATIONS_PATH, invalidations_bytes),
        (baseline_path, baseline_bytes),
        (head_path, dump_json(head).encode("utf-8")),
    ]
    target_preconditions = {
        root / relative: snapshot_preconditions[root / relative]
        for relative in overrides
    }
    target_preconditions.update(
        {
            baseline_path: None,
            head_path: previous_head_hash,
        }
    )
    transactional_write(
        writes,
        journal_root=root,
        expected_existing=source_preconditions,
        expected_targets=target_preconditions,
    )
    return {"status": "sealed", "decision": "pass", "through_chapter": packet["chapter_through"], "canon_sha256": canon_hash, "baseline_file": baseline_path.relative_to(root).as_posix(), "facts": len(report["facts"]), "intentional_exceptions": len(report["intentional_exceptions"]), "dependencies": len(chapters)}


def record_baseline(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    with project_write_context(root, args) as context:
        result = _record_baseline(args, root)
        context.assert_live()
        context.refresh_base_after_write("continuity baseline record")
        return result


def dependency_impact(root: Path, changed_paths: list[str], change_type: str) -> dict[str, Any]:
    if change_type not in REVISION_TYPES:
        raise ContinuityError(f"Unsupported change_type: {change_type}")
    current = current_chapter(root)
    normalized = sorted({Path(value).as_posix().lstrip("./") for value in changed_paths})
    changed_chapters = sorted({number for path in normalized if (number := dependency_chapter_from_path(path)) is not None})
    dependencies = read_json(root / DEPENDENCIES_PATH).get("chapters", {})
    affected: set[int] = set(changed_chapters)
    if change_type in CORE_REVISION_TYPES or change_type == "unknown":
        start = min(changed_chapters, default=1)
        affected.update(range(start, current + 1))
        strategy = "all_following_chapters"
    elif change_type == "formatting":
        strategy = "changed_units_only"
    else:
        changed_fact_ids: set[str] = set()
        for key, entry in dependencies.items():
            if isinstance(entry, dict) and set(entry.get("evidence_paths", [])) & set(normalized):
                changed_fact_ids.update(str(value) for value in entry.get("fact_ids", []))
                affected.add(int(key))
        expanded = True
        while expanded:
            expanded = False
            for key, entry in dependencies.items():
                if not isinstance(entry, dict):
                    continue
                number = int(key)
                if number in affected:
                    continue
                if set(entry.get("depends_on_chapters", [])) & affected or set(str(value) for value in entry.get("fact_ids", [])) & changed_fact_ids:
                    affected.add(number)
                    changed_fact_ids.update(str(value) for value in entry.get("fact_ids", []))
                    expanded = True
        strategy = "fact_dependency_closure"
    return {"status": "calculated", "change_type": change_type, "changed_paths": normalized, "changed_chapters": changed_chapters, "affected_chapters": sorted(number for number in affected if 1 <= number <= current), "strategy": strategy, "current_chapter": current, "requires_full_following_review": change_type in CORE_REVISION_TYPES or change_type == "unknown"}


def impact_command(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    return dependency_impact(root, args.changed_path, args.change_type)


def _invalidate_command(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    authorization = str(args.authorization_reference or "").strip()
    reason = str(args.reason or "").strip()
    if not authorization or not reason:
        raise ContinuityError("reason and authorization_reference must not be empty")
    impact = dependency_impact(root, args.changed_path, args.change_type)
    invalidations = read_json(root / INVALIDATIONS_PATH)
    item = {
        "id": f"INV-{compact_stamp()}-{hashlib.sha256(json.dumps(impact, sort_keys=True).encode()).hexdigest()[:8].upper()}",
        "status": "open",
        "change_type": args.change_type,
        "changed_paths": impact["changed_paths"],
        "affected_chapters": impact["affected_chapters"],
        "strategy": impact["strategy"],
        "reason": reason,
        "authorization_reference": authorization,
        "created_at": utc_now(),
    }
    invalidations.setdefault("items", []).append(item)
    invalidations_bytes = dump_json(invalidations).encode("utf-8")
    head = read_json(root / HEAD_PATH)
    _, canon_hash = canonical_snapshot(root, {INVALIDATIONS_PATH: invalidations_bytes})
    head.update({"status": "blocked", "canon_sha256": canon_hash, "authorization_reference": authorization, "updated_at": utc_now()})
    transactional_write(
        [
            (root / INVALIDATIONS_PATH, invalidations_bytes),
            (root / HEAD_PATH, dump_json(head).encode("utf-8")),
        ],
        journal_root=root,
    )
    return {"status": "invalidated", "invalidation": item, "commit_blocked": True, "delivery_blocked": True}


def invalidate_command(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    with project_write_context(root, args) as context:
        result = _invalidate_command(args, root)
        context.assert_live()
        context.refresh_base_after_write("continuity invalidation record")
        return result


def check_package_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = resolve_root(args.root)
    try:
        package = resolve_package_directory(root, args.package)
        commit = read_json(package / "commit.json")
        candidate = package_member(package, commit.get("chapter_file"), "chapter.md")
        staged_state = read_json(
            package_member(
                package,
                commit.get("continuity_state_file"),
                "continuity-state.json",
            )
        )
        result = validate_chapter_package(root, package, commit, candidate, staged_state)
    except ContinuityError as exc:
        return {"status": "blocked", "error": str(exc)}, 1
    return {"status": "pass", "chapter_number": commit["chapter_number"], "requires_independent_review": result["context"]["derived_independent_review"], "audit_decision": result["audit"]["decision"]}, 0


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(description="Prepare and enforce hash-bound fiction continuity gates.")
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )
    install = subparsers.add_parser("install", help="Add continuity hard-gate scaffolding without changing prose.")
    install.add_argument("root")
    install.add_argument("--workspace")
    install.add_argument("--work-id")
    install.add_argument("--allow-bootstrap", action="store_true")
    status = subparsers.add_parser("status", help="Check the current continuity seal and invalidations.")
    status.add_argument("root")
    context = subparsers.add_parser("prepare-context", help="Create the next chapter's pre-writing continuity context.")
    context.add_argument("root")
    context.add_argument("--chapter", type=int, required=True)
    context.add_argument("--output", required=True)
    audit = subparsers.add_parser("prepare-audit", help="Create state-delta and continuity-audit templates for a staged chapter.")
    audit.add_argument("root")
    audit.add_argument("package")
    audit.add_argument("--workspace")
    audit.add_argument("--work-id")
    audit.add_argument("--allow-bootstrap", action="store_true")
    bind = subparsers.add_parser("bind-audit", help="Validate a completed state delta and bind the draft continuity audit to its final hash.")
    bind.add_argument("root")
    bind.add_argument("package")
    bind.add_argument("--workspace")
    bind.add_argument("--work-id")
    bind.add_argument("--allow-bootstrap", action="store_true")
    check = subparsers.add_parser("check-package", help="Validate a staged chapter's complete continuity evidence.")
    check.add_argument("root")
    check.add_argument("package")
    prepare = subparsers.add_parser("prepare-baseline", help="Prepare a full current-manuscript baseline packet and report template.")
    prepare.add_argument("root")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--report-output")
    record = subparsers.add_parser("record-baseline", help="Record an independently completed baseline review and seal canon.")
    record.add_argument("root")
    record.add_argument("--packet", required=True)
    record.add_argument("--report", required=True)
    record.add_argument("--authorization-reference", required=True)
    record.add_argument("--workspace")
    record.add_argument("--work-id")
    record.add_argument("--allow-bootstrap", action="store_true")
    impact = subparsers.add_parser("impact", help="Calculate downstream chapters affected by proposed canonical changes.")
    impact.add_argument("root")
    impact.add_argument("--changed-path", action="append", required=True)
    impact.add_argument("--change-type", choices=sorted(REVISION_TYPES), default="unknown")
    invalidate = subparsers.add_parser("invalidate", help="Record an authorized revision impact and block commit/delivery until rebaseline.")
    invalidate.add_argument("root")
    invalidate.add_argument("--changed-path", action="append", required=True)
    invalidate.add_argument("--change-type", choices=sorted(REVISION_TYPES), default="unknown")
    invalidate.add_argument("--reason", required=True)
    invalidate.add_argument("--authorization-reference", required=True)
    invalidate.add_argument("--workspace")
    invalidate.add_argument("--work-id")
    invalidate.add_argument("--allow-bootstrap", action="store_true")
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        if args.command == "install":
            return install_project(
                args.root,
                workspace=getattr(args, "workspace", None),
                work_id=getattr(args, "work_id", None),
                allow_bootstrap=bool(getattr(args, "allow_bootstrap", False)),
            )
        if args.command == "status":
            result = continuity_status(args.root)
            return result, 0 if result["status"] == "current" else 1
        if args.command == "prepare-context":
            return prepare_context(args)
        if args.command == "prepare-audit":
            return prepare_audit(args)
        if args.command == "bind-audit":
            return bind_audit(args)
        if args.command == "check-package":
            return check_package_command(args)
        if args.command == "prepare-baseline":
            return prepare_baseline(args)
        if args.command == "record-baseline":
            result = record_baseline(args)
            return result, 0 if result["status"] == "sealed" else 1
        if args.command == "impact":
            return impact_command(args)
        if args.command == "invalidate":
            return invalidate_command(args)
        raise ContinuityError(f"Unsupported command: {args.command}")

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_continuity",
        domain_errors=(ContinuityError, OSError),
    )


if __name__ == "__main__":
    sys.exit(main())
