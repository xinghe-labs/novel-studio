#!/usr/bin/env python3
"""Plan, record, and enforce novel reviews and short-story completion reviews."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import novel_cli


SCHEMA_VERSION = 1
DEFAULT_INTERVAL = 5
MIN_INTERVAL = 1
MAX_INTERVAL = 100
DECISIONS = frozenset({"pass", "needs_revision", "block"})
SEVERITIES = frozenset({"blocker", "important", "minor", "note"})
SCOPES = frozenset({"local", "next_chapters", "whole_book"})
REQUIRED_DIMENSIONS = (
    "hook_and_reader_promise",
    "pacing_and_scene_function",
    "tension_and_information_release",
    "character_arc_and_emotional_force",
    "prose_voice_and_readability",
    "originality_and_cliche_control",
    "platform_fit_and_retention",
    "humanization_and_repetition",
    "language_and_format",
)
CONTEXT_FILES = (
    "novel.json",
    "manuscript/index.md",
    "memory/book-summary.md",
    "memory/decisions.md",
    "continuity/state.json",
    "continuity/timeline.md",
    "continuity/threads.md",
    "story-bible/premise.md",
    "story-bible/cast.md",
    "story-bible/world.md",
    "story-bible/style-guide.md",
    "outlines/master-outline.md",
)
CHAPTER_NAME = re.compile(r"^(?P<number>\d{4})(?:-[^/\\]+)?\.md$", re.IGNORECASE)


class ReviewError(RuntimeError):
    pass


def project_write_context(root: Path, args: argparse.Namespace):
    try:
        import novel_workspace

        @contextlib.contextmanager
        def _context():
            try:
                with novel_workspace.project_write_context(
                    root,
                    workspace=getattr(args, "workspace", None),
                    work_id=getattr(args, "work_id", None),
                    allow_bootstrap=bool(getattr(args, "allow_bootstrap", False)),
                ) as context:
                    yield context
            except novel_workspace.WorkspaceError as exc:
                raise ReviewError(str(exc)) from exc

        return _context()
    except (ImportError, OSError) as exc:
        raise ReviewError(f"Project write authorization module unavailable: {exc}") from exc


def work_type_for_manifest(manifest: dict[str, Any]) -> str:
    work_type = manifest.get("work_type", "serial_novel")
    if work_type not in {"serial_novel", "short_story"}:
        raise ReviewError(
            "novel.json work_type must be serial_novel or short_story"
        )
    return str(work_type)


def review_identity(manifest: dict[str, Any]) -> dict[str, str]:
    if work_type_for_manifest(manifest) == "short_story":
        return {
            "mode": "completion",
            "packet_kind": "short_story_completion_review_packet",
            "report_kind": "short_story_completion_review",
            "directory": "completion",
            "filename_prefix": "completion-review",
        }
    return {
        "mode": "periodic",
        "packet_kind": "periodic_novel_review_packet",
        "report_kind": "periodic_novel_review",
        "directory": "periodic",
        "filename_prefix": "periodic-review",
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_root(raw_root: str | Path) -> Path:
    raw = Path(raw_root).expanduser()
    if _path_chain_has_link(raw):
        raise ReviewError(
            f"Project path cannot traverse a symbolic link or reparse point: {raw}"
        )
    root = raw.resolve()
    anchor = Path(root.anchor).resolve()
    home = Path.home().resolve()
    if root == anchor:
        raise ReviewError("Project root cannot be a filesystem root")
    if root == home:
        raise ReviewError("Project root cannot be the user home directory")
    if not (root / "novel.json").is_file():
        raise ReviewError(f"Not an initialized novel project: {root}")
    return root


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read_stable_bytes(path, label="JSON input").decode("utf-8"))
    except FileNotFoundError as exc:
        raise ReviewError(f"Missing file: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewError(f"Expected a JSON object in {path}")
    return data


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(_read_stable_bytes(path, label="review source")).hexdigest()


def write_new_json(path: Path, data: dict[str, Any]) -> None:
    try:
        novel_cli.atomic_create_text(path, dump_json(data))
    except FileExistsError as exc:
        raise ReviewError(f"Refusing to overwrite an existing file: {path}") from exc


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


def _read_stable_bytes(path: Path, *, label: str) -> bytes:
    raw = Path(path).expanduser()
    if _path_chain_has_link(raw):
        raise ReviewError(
            f"{label} cannot traverse a symbolic link or reparse point: {raw}"
        )
    try:
        with raw.open("rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read()
            after = os.fstat(handle.fileno())
        current = raw.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReviewError(f"Unable to read {label}: {raw}: {exc}") from exc
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    current_identity = (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )
    if before_identity != after_identity or after_identity != current_identity:
        raise ReviewError(f"{label} changed while being read: {raw}")
    return content


def _external_review_input(
    raw_path: str | Path,
    *,
    project_root: Path,
    label: str,
) -> tuple[Path, bytes]:
    raw = Path(raw_path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    if _path_chain_has_link(raw):
        raise ReviewError(
            f"{label} cannot traverse a symbolic link or reparse point: {raw}"
        )
    path = raw.resolve()
    if path == project_root or project_root in path.parents:
        raise ReviewError(f"{label} must be stored outside the project tree")
    if not path.is_file():
        raise ReviewError(f"{label} is not a regular file: {path}")
    return path, _read_stable_bytes(path, label=label)


def _json_object_from_bytes(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"{label} must be valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must be a JSON object")
    return value


def safe_external_output(raw: str | Path, project_root: Path) -> Path:
    """Resolve a new preparation output outside the canonical project tree."""

    raw_path = Path(raw).expanduser()
    if not raw_path.is_absolute():
        raw_path = Path.cwd() / raw_path
    if _path_chain_has_link(raw_path):
        raise ReviewError(
            f"Preparation output cannot traverse a symbolic link or reparse point: {raw_path}"
        )
    path = raw_path.resolve()
    if path == project_root or project_root in path.parents:
        raise ReviewError(
            "Review preparation output must be outside the project tree; use the current work-root"
        )
    if path.exists():
        raise ReviewError(f"Refusing to overwrite an existing file: {path}")
    return path


def write_external_pair(
    first: Path, first_data: dict[str, Any], second: Path, second_data: dict[str, Any]
) -> None:
    """Create two external JSON files and clean up only files created here on failure."""

    if first.resolve() == second.resolve():
        raise ReviewError("Review packet and report outputs must be different files")
    items = (
        (first, dump_json(first_data).encode("utf-8")),
        (second, dump_json(second_data).encode("utf-8")),
    )
    for path, _ in items:
        if _path_chain_has_link(path):
            raise ReviewError(
                f"Preparation output cannot traverse a symbolic link or reparse point: {path}"
            )
        if path.exists():
            raise ReviewError(f"Refusing to overwrite an existing file: {path}")

    created: list[tuple[Path, tuple[int, int]]] = []
    try:
        for path, content in items:
            try:
                identity = novel_cli.atomic_create_bytes(path, content)
            except FileExistsError as exc:
                raise ReviewError(
                    f"Refusing to overwrite an existing file: {path}"
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
            raise ReviewError(
                "Review preparation failed and partial-output cleanup requires "
                "manual reconciliation: " + "; ".join(cleanup_errors)
            ) from exc
        raise


def clean_reference(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ReviewError("A non-empty --authorization-reference is required")
    if len(cleaned) > 240:
        raise ReviewError("--authorization-reference must not exceed 240 characters")
    return cleaned


def parse_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ReviewError(f"{label} must be true or false")


def policy_for_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    work_type = work_type_for_manifest(manifest)
    default_interval = 1 if work_type == "short_story" else DEFAULT_INTERVAL
    raw = manifest.get("periodic_review")
    source = "novel.json" if raw is not None else "default"
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ReviewError("novel.json periodic_review must be an object")

    enabled = parse_bool(raw.get("enabled", True), "periodic_review.enabled")
    block_next_commit = parse_bool(
        raw.get("block_next_commit", True),
        "periodic_review.block_next_commit",
    )
    interval = raw.get("interval_chapters", default_interval)
    if (
        not isinstance(interval, int)
        or isinstance(interval, bool)
        or interval < MIN_INTERVAL
        or interval > MAX_INTERVAL
    ):
        raise ReviewError(
            f"periodic_review.interval_chapters must be an integer from "
            f"{MIN_INTERVAL} to {MAX_INTERVAL}"
        )
    if work_type == "short_story" and interval != 1:
        raise ReviewError(
            "short_story completion review requires interval_chapters=1"
        )
    return {
        "enabled": enabled,
        "interval_chapters": interval,
        "block_next_commit": block_next_commit,
        "source": source,
    }


def project_policy(root: str | Path) -> dict[str, Any]:
    project_root = resolve_root(root)
    return policy_for_manifest(read_json(project_root / "novel.json"))


def chapter_map(root: Path, through: int | None = None) -> dict[int, Path]:
    chapters: dict[int, Path] = {}
    chapter_dir = root / "manuscript/chapters"
    for path in sorted(chapter_dir.glob("*.md")):
        match = CHAPTER_NAME.fullmatch(path.name)
        if match is None:
            continue
        number = int(match.group("number"))
        if through is not None and number > through:
            continue
        if number in chapters:
            raise ReviewError(
                f"Duplicate chapter number {number:04d}: "
                f"{chapters[number].name} and {path.name}"
            )
        chapters[number] = path
    return chapters


def source_entry(root: Path, path: Path, kind: str) -> dict[str, str]:
    content = _read_stable_bytes(path, label="review source")
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def build_source_snapshot(root: Path, through: int) -> list[dict[str, str]]:
    if through < 1:
        raise ReviewError("A periodic review must cover at least one chapter")
    chapters = chapter_map(root, through=through)
    expected = set(range(1, through + 1))
    missing = sorted(expected - set(chapters))
    if missing:
        raise ReviewError(
            "Cannot prepare review because committed chapters are missing: "
            + ", ".join(f"{number:04d}" for number in missing)
        )

    snapshot: list[dict[str, str]] = []
    for relative in CONTEXT_FILES:
        snapshot.append(source_entry(root, root / relative, "context"))
    for number in range(1, through + 1):
        snapshot.append(source_entry(root, chapters[number], "chapter"))
        snapshot.append(
            source_entry(
                root,
                root / "memory/chapters" / f"{number:04d}.md",
                "chapter_memory",
            )
        )
    return sorted(snapshot, key=lambda item: (item["kind"], item["path"]))


def _expected_reading_scope(
    snapshot: list[dict[str, str]], chapter_from: int, through: int
) -> dict[str, Any]:
    """Build the non-editable reading scope represented by a review packet."""

    return {
        "full_text_primary": [
            entry["path"]
            for entry in snapshot
            if entry["kind"] == "chapter"
            and chapter_from <= int(Path(entry["path"]).name[:4]) <= through
        ],
        "chapter_memory_global": [
            entry["path"] for entry in snapshot if entry["kind"] == "chapter_memory"
        ],
        "stable_context": [
            entry["path"] for entry in snapshot if entry["kind"] == "context"
        ],
        "targeted_older_full_text": (
            "Read any older chapter whose facts are implicated by a possible "
            "conflict; memory cards alone cannot prove a finding."
        ),
    }


def _validate_review_packet_scope(
    root: Path,
    packet: dict[str, Any],
    identity: dict[str, str],
    manifest: dict[str, Any],
) -> list[str]:
    """Reject packets that silently narrow the files or dimensions being reviewed."""

    errors: list[str] = []
    through = packet.get("chapter_through")
    chapter_from = packet.get("chapter_from")
    if (
        not isinstance(through, int)
        or isinstance(through, bool)
        or not isinstance(chapter_from, int)
        or isinstance(chapter_from, bool)
        or chapter_from < 1
        or through < chapter_from
    ):
        return ["Review packet has an invalid chapter range"]
    status = review_status(root)
    if through > status["current_chapter"]:
        return ["Review packet chapter_through exceeds current_chapter"]
    expected_from = (
        1 if identity["mode"] == "completion" else status["last_passed_through"] + 1
    )
    if expected_from > through and identity["mode"] != "completion":
        expected_from = max(1, through - status["policy"]["interval_chapters"] + 1)
    if chapter_from != expected_from:
        errors.append("Review packet chapter_from does not match the current review scope")
    if packet.get("project_title") != status["title"]:
        errors.append("Review packet project_title does not match the current project")
    try:
        expected_snapshot = build_source_snapshot(root, through)
    except ReviewError as exc:
        return [str(exc)]
    if packet.get("source_snapshot") != expected_snapshot:
        errors.append(
            "Review packet source_snapshot must exactly cover the current review sources"
        )
    if packet.get("review_dimensions") != list(REQUIRED_DIMENSIONS):
        errors.append("Review packet must cover exactly the independent quality dimensions")
    if packet.get("review_domain") != "quality":
        errors.append("Review packet review_domain must be quality")
    if packet.get("continuity_review_separate") is not True:
        errors.append("Review packet must declare continuity_review_separate=true")
    if packet.get("global_context_through") != through:
        errors.append("Review packet global_context_through must equal chapter_through")
    if packet.get("policy") != policy_for_manifest(manifest):
        errors.append("Review packet policy is stale; prepare a new packet")
    expected_scope = _expected_reading_scope(expected_snapshot, chapter_from, through)
    if packet.get("reading_scope") != expected_scope:
        errors.append("Review packet reading_scope must exactly cover its source snapshot")
    if identity["mode"] == "completion" and packet.get("review_mode") != identity["mode"]:
        errors.append("Review packet mode does not match the project work type")
    if identity["mode"] == "periodic" and "review_mode" in packet:
        errors.append("Periodic review packet must not contain review_mode")
    return errors


def validate_snapshot_current(root: Path, snapshot: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(snapshot, list) or not snapshot:
        return ["source_snapshot must be a non-empty array"]
    for entry in snapshot:
        if not isinstance(entry, dict):
            errors.append("source_snapshot contains a non-object entry")
            continue
        relative = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(relative, str) or not relative:
            errors.append("source_snapshot entry has an invalid path")
            continue
        raw_path = root / relative
        if _path_chain_has_link(raw_path):
            errors.append(f"source_snapshot path traverses a link: {relative}")
            continue
        path = raw_path.resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"source_snapshot path escapes the project: {relative}")
            continue
        if not path.is_file():
            errors.append(f"review source is missing: {relative}")
            continue
        try:
            actual_hash = sha256_file(path)
        except ReviewError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            errors.append(f"review source changed after preparation: {relative}")
    return errors


def validate_recorded_report(root: Path, path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        report = read_json(path)
    except ReviewError as exc:
        return None, str(exc)
    if report.get("schema_version") != SCHEMA_VERSION:
        return None, "unsupported schema_version"
    identity = review_identity(read_json(root / "novel.json"))
    if report.get("report_kind") != identity["report_kind"]:
        return None, "unexpected report_kind"
    if report.get("status") != "complete":
        return None, "report status is not complete"
    if report.get("decision") not in DECISIONS:
        return None, "invalid report decision"
    if (
        report.get("review_domain") != "quality"
        or report.get("continuity_review_separate") is not True
    ):
        return None, "report does not keep quality and continuity review separate"
    reviewer = report.get("reviewer")
    if (
        not isinstance(reviewer, dict)
        or reviewer.get("mode") != "independent"
        or reviewer.get("independent_context") is not True
        or not isinstance(reviewer.get("reviewer_id"), str)
        or not reviewer["reviewer_id"].strip()
    ):
        return None, "report does not identify an independent-context reviewer"
    if (
        identity["mode"] == "completion"
        and report.get("review_mode") != identity["mode"]
    ):
        return None, "report mode does not match the project work type"
    chapter_from = report.get("chapter_from")
    chapter_through = report.get("chapter_through")
    if (
        not isinstance(chapter_from, int)
        or isinstance(chapter_from, bool)
        or not isinstance(chapter_through, int)
        or isinstance(chapter_through, bool)
        or chapter_from < 1
        or chapter_through < chapter_from
    ):
        return None, "invalid chapter range"

    if report.get("reviewed_dimensions") != list(REQUIRED_DIMENSIONS):
        return None, "report does not cover exactly the independent quality dimensions"
    if not isinstance(report.get("reviewed_at"), str) or not report["reviewed_at"].strip():
        return None, "missing reviewed_at"
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        return None, "missing summary"
    residual = report.get("residual_risks")
    if not isinstance(residual, list) or any(not isinstance(item, str) for item in residual):
        return None, "invalid residual_risks"
    findings = report.get("findings")
    if not isinstance(findings, list):
        return None, "findings must be an array"
    finding_errors: list[str] = []
    for index, finding in enumerate(findings):
        finding_errors.extend(validate_finding(finding, chapter_through, index))
    if finding_errors:
        return None, "; ".join(finding_errors)
    if report.get("decision") != expected_decision(findings):
        return None, "report decision does not match its finding severities"

    snapshot = report.get("source_snapshot")
    if not isinstance(snapshot, list):
        return None, "missing source_snapshot"
    chapter_entries = [
        entry
        for entry in snapshot
        if isinstance(entry, dict) and entry.get("kind") == "chapter"
    ]
    current_chapters = chapter_map(root, through=chapter_through)
    if set(current_chapters) != set(range(1, chapter_through + 1)):
        return None, "one or more reviewed chapters are now missing"
    expected_chapter_paths = {
        chapter.relative_to(root).as_posix()
        for chapter in current_chapters.values()
    }
    recorded_chapter_paths = {
        str(entry.get("path")) for entry in chapter_entries
    }
    if (
        len(chapter_entries) != chapter_through
        or recorded_chapter_paths != expected_chapter_paths
    ):
        return None, "source_snapshot does not exactly cover all reviewed chapters"
    chapter_errors = validate_snapshot_current(root, chapter_entries)
    if chapter_errors:
        return None, "; ".join(chapter_errors)
    return report, None


def recorded_reports(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    valid: list[dict[str, Any]] = []
    warnings: list[str] = []
    manifest = read_json(root / "novel.json")
    identity = review_identity(manifest)
    report_dir = root / "reviews" / identity["directory"]
    if not report_dir.is_dir():
        return valid, warnings
    for path in sorted(report_dir.glob("*.json")):
        report, error = validate_recorded_report(root, path)
        if error:
            warnings.append(
                f"Ignored invalid {identity['mode']} review {path.name}: {error}"
            )
            continue
        assert report is not None
        report = dict(report)
        report["report_path"] = path.relative_to(root).as_posix()
        valid.append(report)
    return valid, warnings


def passed_coverage(reports: list[dict[str, Any]]) -> int:
    coverage = 0
    passes = sorted(
        (report for report in reports if report.get("decision") == "pass"),
        key=lambda report: (report["chapter_through"], report["chapter_from"]),
    )
    for report in passes:
        if report["chapter_from"] <= coverage + 1 and report["chapter_through"] > coverage:
            coverage = report["chapter_through"]
    return coverage


def review_status(root: str | Path) -> dict[str, Any]:
    project_root = resolve_root(root)
    manifest = read_json(project_root / "novel.json")
    work_type = work_type_for_manifest(manifest)
    identity = review_identity(manifest)
    current = manifest.get("current_chapter")
    if not isinstance(current, int) or isinstance(current, bool) or current < 0:
        raise ReviewError("novel.json current_chapter must be a non-negative integer")
    policy = policy_for_manifest(manifest)
    reports, warnings = recorded_reports(project_root)
    passed = min(passed_coverage(reports), current)
    reviewed = max(
        (min(int(report["chapter_through"]), current) for report in reports),
        default=0,
    )

    interval = policy["interval_chapters"]
    if work_type == "short_story":
        due_through = 1 if policy["enabled"] and current >= 1 else 0
        due = bool(due_through and passed < 1)
        next_due = 1 if policy["enabled"] and passed < 1 else None
    else:
        due_through = (current // interval) * interval if policy["enabled"] else 0
        due = bool(policy["enabled"] and due_through > passed)
        next_due = (
            ((passed // interval) + 1) * interval if policy["enabled"] else None
        )
    latest_nonpassing = None
    for report in sorted(reports, key=lambda item: item["chapter_through"], reverse=True):
        if report["decision"] != "pass" and report["chapter_through"] >= due_through:
            latest_nonpassing = {
                "decision": report["decision"],
                "report_path": report["report_path"],
                "chapter_through": report["chapter_through"],
            }
            break

    result = {
        "status": "due" if due else ("disabled" if not policy["enabled"] else "current"),
        "project_root": str(project_root),
        "title": manifest.get("title", ""),
        "current_chapter": current,
        "policy": policy,
        "last_reviewed_through": reviewed,
        "last_passed_through": passed,
        "next_due_chapter": next_due,
        "chapters_until_due": (
            max(0, int(next_due) - current) if next_due is not None and not due else 0
        ),
        "review_due": due,
        "review_from": (1 if work_type == "short_story" else passed + 1) if due else None,
        "review_through": due_through if due else None,
        "global_context_through": due_through if due else None,
        "commit_blocked": bool(due and policy["block_next_commit"]),
        "latest_nonpassing_review": latest_nonpassing,
        "valid_report_count": len(reports),
        "warnings": warnings,
    }
    if work_type == "short_story":
        result.update({"work_type": work_type, "review_mode": identity["mode"]})
    return result


def ensure_commit_allowed(root: str | Path, next_chapter: int) -> dict[str, Any]:
    status = review_status(root)
    if status["commit_blocked"]:
        manifest = read_json(resolve_root(root) / "novel.json")
        if work_type_for_manifest(manifest) == "short_story":
            raise ReviewError(
                "The complete short story must pass its full-manuscript review "
                "before another canonical change"
            )
        raise ReviewError(
            "Periodic review is overdue before committing chapter "
            f"{next_chapter:04d}: review chapters "
            f"{status['review_from']:04d}-{status['review_through']:04d}, "
            "record a passing report, then retry"
        )
    return status


def prepare_review(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    status = review_status(root)
    manifest = read_json(root / "novel.json")
    identity = review_identity(manifest)
    current = status["current_chapter"]
    through = args.through
    if through is None:
        if status["review_due"]:
            through = status["review_through"]
        elif args.force:
            through = current
        else:
            label = "Completion" if identity["mode"] == "completion" else "Periodic"
            raise ReviewError(
                f"{label} review is not due; use --force for an explicit ad-hoc review"
            )
    if (
        not isinstance(through, int)
        or isinstance(through, bool)
        or through < 1
        or through > current
    ):
        raise ReviewError("--through must be between 1 and current_chapter")

    chapter_from = (
        1
        if identity["mode"] == "completion"
        else status["last_passed_through"] + 1
    )
    if chapter_from > through and identity["mode"] != "completion":
        chapter_from = max(1, through - status["policy"]["interval_chapters"] + 1)
    snapshot = build_source_snapshot(root, through)
    packet = {
        "schema_version": SCHEMA_VERSION,
        "packet_kind": identity["packet_kind"],
        "project_root": str(root),
        "project_title": status["title"],
        "created_at": utc_now(),
        "chapter_from": chapter_from,
        "chapter_through": through,
        "global_context_through": through,
        "policy": status["policy"],
        "review_dimensions": list(REQUIRED_DIMENSIONS),
        "review_domain": "quality",
        "continuity_review_separate": True,
        "reading_scope": {
            "full_text_primary": [
                entry["path"]
                for entry in snapshot
                if entry["kind"] == "chapter"
                and chapter_from <= int(Path(entry["path"]).name[:4]) <= through
            ],
            "chapter_memory_global": [
                entry["path"]
                for entry in snapshot
                if entry["kind"] == "chapter_memory"
            ],
            "stable_context": [
                entry["path"] for entry in snapshot if entry["kind"] == "context"
            ],
            "targeted_older_full_text": (
                "Read any older chapter whose facts are implicated by a possible "
                "conflict; memory cards alone cannot prove a finding."
            ),
        },
        "source_snapshot": snapshot,
    }
    if identity["mode"] == "completion":
        packet["review_mode"] = identity["mode"]
    output = safe_external_output(args.output, root)

    report_output = (
        safe_external_output(args.report_output, root)
        if args.report_output
        else safe_external_output(output.with_name(output.stem + "-report.json"), root)
    )
    packet_bytes = dump_json(packet).encode("utf-8")
    packet_hash = hashlib.sha256(packet_bytes).hexdigest()
    report_template = {
        "schema_version": SCHEMA_VERSION,
        "report_kind": identity["report_kind"],
        "status": "draft",
        "project_title": status["title"],
        "chapter_from": chapter_from,
        "chapter_through": through,
        "global_context_through": through,
        "packet_sha256": packet_hash,
        "reviewed_at": "",
        "reviewed_dimensions": list(REQUIRED_DIMENSIONS),
        "review_domain": "quality",
        "continuity_review_separate": True,
        "reviewer": {
            "mode": "independent",
            "reviewer_id": "",
            "independent_context": True,
        },
        "decision": "pass",
        "summary": "",
        "findings": [],
        "residual_risks": [],
    }
    if identity["mode"] == "completion":
        report_template["review_mode"] = identity["mode"]
    write_external_pair(output, packet, report_output, report_template)
    result = {
        "status": "prepared",
        "project_root": str(root),
        "chapter_from": chapter_from,
        "chapter_through": through,
        "packet_path": str(output),
        "packet_sha256": packet_hash,
        "report_template_path": str(report_output),
        "required_full_text_chapters": packet["reading_scope"]["full_text_primary"],
    }
    if identity["mode"] == "completion":
        result["review_mode"] = identity["mode"]
    return result


def validate_finding(finding: Any, through: int, index: int) -> list[str]:
    prefix = f"findings[{index}]"
    errors: list[str] = []
    if not isinstance(finding, dict):
        return [f"{prefix} must be an object"]
    for key in ("id", "location", "evidence", "problem", "impact", "suggested_fix"):
        value = finding.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{prefix}.{key} must be a non-empty string")
    if finding.get("severity") not in SEVERITIES:
        errors.append(f"{prefix}.severity must be one of: {', '.join(sorted(SEVERITIES))}")
    if finding.get("scope") not in SCOPES:
        errors.append(f"{prefix}.scope must be one of: {', '.join(sorted(SCOPES))}")
    if not isinstance(finding.get("author_judgment"), bool):
        errors.append(f"{prefix}.author_judgment must be true or false")
    chapters = finding.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        errors.append(f"{prefix}.chapters must be a non-empty array")
    else:
        for chapter in chapters:
            if (
                not isinstance(chapter, int)
                or isinstance(chapter, bool)
                or chapter < 1
                or chapter > through
            ):
                errors.append(f"{prefix}.chapters contains an out-of-range chapter")
                break
    return errors


def expected_decision(findings: list[dict[str, Any]]) -> str:
    severities = {finding.get("severity") for finding in findings}
    if "blocker" in severities:
        return "block"
    if "important" in severities:
        return "needs_revision"
    return "pass"


def _record_review(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    manifest = read_json(root / "novel.json")
    identity = review_identity(manifest)
    packet_path, packet_bytes = _external_review_input(
        args.packet, project_root=root, label="Review packet"
    )
    report_path, report_bytes = _external_review_input(
        args.report, project_root=root, label="Review report"
    )
    if packet_path == report_path:
        raise ReviewError("Review packet and report must be different files")
    packet = _json_object_from_bytes(packet_bytes, label="Review packet")
    report = _json_object_from_bytes(report_bytes, label="Review report")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise ReviewError("Review packet has an unsupported schema_version")
    if packet.get("packet_kind") != identity["packet_kind"]:
        raise ReviewError("Unexpected review packet kind")
    if Path(str(packet.get("project_root", ""))).resolve() != root:
        raise ReviewError("Review packet belongs to a different project")
    packet_scope_errors = _validate_review_packet_scope(
        root, packet, identity, manifest
    )
    if packet_scope_errors:
        raise ReviewError("; ".join(packet_scope_errors))
    packet_hash = hashlib.sha256(packet_bytes).hexdigest()
    if report.get("packet_sha256") != packet_hash:
        raise ReviewError("Report does not reference the current review packet hash")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ReviewError("Review report has an unsupported schema_version")
    if report.get("report_kind") != identity["report_kind"]:
        raise ReviewError("Unexpected review report kind")
    if identity["mode"] == "completion":
        if packet.get("review_mode") != identity["mode"]:
            raise ReviewError("Review packet mode does not match the project work type")
        if report.get("review_mode") != identity["mode"]:
            raise ReviewError("Review report mode does not match the project work type")
    if report.get("status") != "complete":
        raise ReviewError("Review report status must be complete")
    if packet.get("review_domain") != "quality" or packet.get("continuity_review_separate") is not True:
        raise ReviewError("Review packet must keep quality and continuity review separate")
    if report.get("review_domain") != "quality" or report.get("continuity_review_separate") is not True:
        raise ReviewError("Review report must keep quality and continuity review separate")
    reviewer = report.get("reviewer")
    if (
        not isinstance(reviewer, dict)
        or reviewer.get("mode") != "independent"
        or reviewer.get("independent_context") is not True
        or not isinstance(reviewer.get("reviewer_id"), str)
        or not reviewer["reviewer_id"].strip()
    ):
        raise ReviewError(
            "Periodic and completion quality reviews require an identified independent-context reviewer"
        )
    if report.get("project_title") != packet.get("project_title"):
        raise ReviewError("Review report project_title does not match the packet")
    for key in ("chapter_from", "chapter_through", "global_context_through"):
        if report.get(key) != packet.get(key):
            raise ReviewError(f"Review report {key} does not match the packet")
    reviewed_at = report.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        raise ReviewError("Review report reviewed_at must be non-empty")
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ReviewError("Review report summary must be non-empty")
    residual = report.get("residual_risks")
    if not isinstance(residual, list) or any(not isinstance(item, str) for item in residual):
        raise ReviewError("Review report residual_risks must be an array of strings")

    dimensions = report.get("reviewed_dimensions")
    if dimensions != list(REQUIRED_DIMENSIONS):
        raise ReviewError(
            "Review report must cover exactly the independent quality dimensions"
        )

    findings = report.get("findings")
    if not isinstance(findings, list):
        raise ReviewError("Review report findings must be an array")
    finding_errors: list[str] = []
    for index, finding in enumerate(findings):
        finding_errors.extend(
            validate_finding(finding, int(packet["chapter_through"]), index)
        )
    if finding_errors:
        raise ReviewError("; ".join(finding_errors))
    decision = report.get("decision")
    if decision not in DECISIONS:
        raise ReviewError("Review report has an invalid decision")
    expected = expected_decision(findings)
    if decision != expected:
        raise ReviewError(
            f"Review decision must be {expected} for the recorded finding severities"
        )

    snapshot_errors = validate_snapshot_current(root, packet.get("source_snapshot"))
    if snapshot_errors:
        raise ReviewError("; ".join(snapshot_errors))
    authorization = clean_reference(args.authorization_reference)
    recorded = dict(report)
    recorded.update(
        {
            "status": "complete",
            "recorded_at": utc_now(),
            "authorization_reference": authorization,
            "source_snapshot": packet["source_snapshot"],
        }
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(dump_json(recorded).encode("utf-8")).hexdigest()[:10]
    target = (
        root
        / "reviews"
        / identity["directory"]
        / (
            f"{identity['filename_prefix']}-{int(report['chapter_from']):04d}-"
            f"{int(report['chapter_through']):04d}-{stamp}-{digest}.json"
        )
    )
    try:
        import novel_project

        novel_project.transactional_write(
            [(target, dump_json(recorded).encode("utf-8"))],
            journal_root=root,
            expected_existing={
                packet_path: hashlib.sha256(packet_bytes).hexdigest(),
                report_path: hashlib.sha256(report_bytes).hexdigest(),
                **{
                    root / entry["path"]: entry["sha256"]
                    for entry in packet["source_snapshot"]
                },
            },
            expected_targets={target: None},
        )
    except Exception as exc:
        raise ReviewError(str(exc)) from exc
    status = review_status(root)
    result = {
        "status": "recorded",
        "project_root": str(root),
        "decision": decision,
        "report_path": target.relative_to(root).as_posix(),
        "periodic_review": status,
    }
    if identity["mode"] == "completion":
        result["review_mode"] = identity["mode"]
    return result


def record_review(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    with project_write_context(root, args) as context:
        result = _record_review(args, root)
        context.assert_live()
        context.refresh_base_after_write("quality review record")
        return result


def _configure_policy(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    manifest_path = root / "novel.json"
    manifest = read_json(manifest_path)
    work_type = work_type_for_manifest(manifest)
    before = policy_for_manifest(manifest)
    interval = args.interval if args.interval is not None else before["interval_chapters"]
    if (
        not isinstance(interval, int)
        or isinstance(interval, bool)
        or interval < MIN_INTERVAL
        or interval > MAX_INTERVAL
    ):
        raise ReviewError(f"--interval must be from {MIN_INTERVAL} to {MAX_INTERVAL}")
    if work_type == "short_story" and interval != 1:
        raise ReviewError(
            "short_story completion review uses interval 1 because the project "
            "contains one complete manuscript unit"
        )
    enabled = before["enabled"] if args.enabled is None else parse_bool(args.enabled, "--enabled")
    block = (
        before["block_next_commit"]
        if args.block_next_commit is None
        else parse_bool(args.block_next_commit, "--block-next-commit")
    )
    authorization = clean_reference(args.authorization_reference)
    updated = dict(manifest)
    updated["periodic_review"] = {
        "enabled": enabled,
        "interval_chapters": interval,
        "block_next_commit": block,
    }
    updated["updated_at"] = utc_now()
    try:
        import novel_project

        novel_project.transactional_write(
            [(manifest_path, dump_json(updated).encode("utf-8"))], journal_root=root
        )
    except Exception as exc:
        raise ReviewError(str(exc)) from exc
    return {
        "status": "configured",
        "project_root": str(root),
        "before": before,
        "after": policy_for_manifest(updated),
        "authorization_reference": authorization,
    }


def configure_policy(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    with project_write_context(root, args) as context:
        result = _configure_policy(args, root)
        context.assert_live()
        context.refresh_base_after_write("quality review policy update")
        return result


def collect_review_validation(root: str | Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        status = review_status(root)
    except ReviewError as exc:
        return [str(exc)], warnings
    warnings.extend(status["warnings"])
    if status["review_due"]:
        manifest = read_json(resolve_root(root) / "novel.json")
        if work_type_for_manifest(manifest) == "short_story":
            warnings.append(
                "Short-story completion review is due for the full manuscript"
            )
        else:
            warnings.append(
                "Periodic review is due for chapters "
                f"{status['review_from']:04d}-{status['review_through']:04d}"
            )
    return errors, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(
        description=(
            "Plan, record, and enforce periodic novel reviews or a short-story "
            "full-manuscript completion review."
        )
    )
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )

    status_parser = subparsers.add_parser("status", help="Show project review state.")
    status_parser.add_argument("root", help="Project directory.")

    prepare_parser = subparsers.add_parser(
        "prepare", help="Create a snapshot-bound review packet and report template."
    )
    prepare_parser.add_argument("root", help="Project directory.")
    prepare_parser.add_argument("--output", required=True, help="Packet JSON path.")
    prepare_parser.add_argument("--report-output", help="Report template JSON path.")
    prepare_parser.add_argument("--through", type=int, help="Last chapter to review.")
    prepare_parser.add_argument(
        "--force", action="store_true", help="Prepare even when no periodic review is due."
    )

    record_parser = subparsers.add_parser(
        "record", help="Validate and record a completed project review report."
    )
    record_parser.add_argument("root", help="Project directory.")
    record_parser.add_argument("--packet", required=True, help="Prepared packet JSON.")
    record_parser.add_argument("--report", required=True, help="Completed report JSON.")
    record_parser.add_argument("--authorization-reference", required=True)
    record_parser.add_argument("--workspace")
    record_parser.add_argument("--work-id")
    record_parser.add_argument("--allow-bootstrap", action="store_true")

    configure_parser = subparsers.add_parser(
        "configure", help="Configure the per-project review interval and gate."
    )
    configure_parser.add_argument("root", help="Project directory.")
    configure_parser.add_argument("--interval", type=int)
    configure_parser.add_argument("--enabled", choices=("true", "false"))
    configure_parser.add_argument(
        "--block-next-commit", choices=("true", "false")
    )
    configure_parser.add_argument("--authorization-reference", required=True)
    configure_parser.add_argument("--workspace")
    configure_parser.add_argument("--work-id")
    configure_parser.add_argument("--allow-bootstrap", action="store_true")
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        if args.command == "status":
            return review_status(args.root)
        if args.command == "prepare":
            return prepare_review(args)
        if args.command == "record":
            return record_review(args)
        if args.command == "configure":
            return configure_policy(args)
        raise ReviewError(f"Unsupported command: {args.command}")

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_review",
        domain_errors=(ReviewError, OSError),
    )


if __name__ == "__main__":
    sys.exit(main())
