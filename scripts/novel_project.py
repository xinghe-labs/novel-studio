#!/usr/bin/env python3
"""Create, validate, transition, and transactionally commit novel projects."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import novel_review
import novel_continuity
import novel_cli


_REAL_OS_REPLACE = os.replace


SCHEMA_VERSION = 1
WORK_TYPES = frozenset({"serial_novel", "short_story"})
FRAMEWORK_STAGES = frozenset(
    {
        "discovery",
        "direction",
        "core",
        "characters",
        "world",
        "voice",
        "outline",
        "confirmation",
        "complete",
    }
)
FRAMEWORK_CONFIRMATIONS = frozenset({"pending", "confirmed"})
FRAMEWORK_CANONICAL_FILES = (
    "story-bible/premise.md",
    "story-bible/cast.md",
    "story-bible/world.md",
    "story-bible/style-guide.md",
    "outlines/master-outline.md",
)
FRAMEWORK_MEMORY_FILES = (
    "memory/decisions.md",
    "memory/book-summary.md",
)
FRAMEWORK_SYNC_FILES = (
    "planning/framework-session.md",
    *FRAMEWORK_CANONICAL_FILES,
    *FRAMEWORK_MEMORY_FILES,
)
FRAMEWORK_SETTINGS_FILE = "project-settings.json"
FRAMEWORK_SYNC_SOURCE_FILES = (*FRAMEWORK_SYNC_FILES, FRAMEWORK_SETTINGS_FILE)
CANDIDATE_APPROVALS = frozenset({"pending", "approved", "revision_requested"})
DEEP_ANALYSIS_STAGES = frozenset({"not_started", "in_progress", "complete"})
REQUIRED_DIRS = (
    "planning",
    "research",
    "sources",
    "story-bible",
    "outlines",
    "manuscript",
    "manuscript/chapters",
    "memory",
    "memory/chapters",
    "continuity",
    "reviews",
    "revisions/snapshots",
    "staging/chapters",
)
REQUIRED_FILES = (
    "novel.json",
    "planning/framework-session.md",
    "research/source-index.md",
    "research/market-scan.md",
    "research/comparable-works.md",
    "research/originality-map.md",
    "research/platform.json",
    "research/source-manifest.jsonl",
    "research/originality-plan.json",
    "story-bible/premise.md",
    "story-bible/cast.md",
    "story-bible/world.md",
    "story-bible/style-guide.md",
    "outlines/master-outline.md",
    "manuscript/index.md",
    "memory/book-summary.md",
    "memory/decisions.md",
    "continuity/state.json",
    "continuity/timeline.md",
    "continuity/threads.md",
)
UPGRADE_FILES = (
    "planning/framework-session.md",
    "research/source-index.md",
    "research/market-scan.md",
    "research/comparable-works.md",
    "research/originality-map.md",
    "research/platform.json",
    "research/source-manifest.jsonl",
    "research/originality-plan.json",
    "manuscript/index.md",
    "memory/book-summary.md",
    "memory/decisions.md",
)
CHAPTER_NAME = re.compile(
    r"^(?P<number>\d{4})(?:-[^/\\]+)?\.md$", re.IGNORECASE
)
TRANSACTION_DIRNAME = ".novel-transaction"
TRANSACTION_SCHEMA_VERSION = 1
UPGRADE_TRANSACTION_DIRNAME = ".novel-upgrade-transaction"
UPGRADE_TRANSACTION_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MARKDOWN_LINK = re.compile(r"\]\((?P<target>[^)#]+\.md)(?:#[^)]+)?\)", re.IGNORECASE)
INDEX_TITLE = re.compile(
    r"^\|\s*(?P<number>\d{4})\s*\|\s*(?P<title>(?:\\\||[^|])*)\|"
)
SERIAL_CHAPTER_HEADING = re.compile(
    r"^#\s*第(?P<number>[0-9零〇一二三四五六七八九十百千两]+)章\s*(?P<title>.+?)\s*$"
)


class ProjectError(RuntimeError):
    pass


def project_write_context(root: Path, args: argparse.Namespace):
    """Return the shared function-level write authorization context."""

    try:
        import novel_workspace
    except (ImportError, OSError) as exc:
        raise ProjectError(f"Project write authorization module unavailable: {exc}") from exc
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
            raise ProjectError(str(exc)) from exc

    return _context()


def work_type_for_manifest(manifest: dict[str, Any]) -> str:
    """Return the project form while keeping pre-v2 manifests compatible."""
    work_type = manifest.get("work_type", "serial_novel")
    if work_type not in WORK_TYPES:
        raise ProjectError(
            "novel.json work_type must be serial_novel or short_story"
        )
    return str(work_type)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_root(raw_root: str) -> Path:
    raw = Path(raw_root).expanduser()
    if _contains_symlink(raw):
        raise ProjectError(
            f"Project path cannot traverse a symbolic link or reparse point: {raw}"
        )
    root = raw.resolve()
    anchor = Path(root.anchor).resolve()
    home = Path.home().resolve()
    if root == anchor:
        raise ProjectError("Project root cannot be a filesystem root.")
    if root == home:
        raise ProjectError("Project root cannot be the user home directory.")
    return root


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError(f"Expected a JSON object in {path}")
    return data


def write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_stable_bytes(path: Path, *, label: str = "file") -> bytes:
    """Read bytes without following a link or accepting a replacement race."""

    raw = Path(path).expanduser()
    if _contains_symlink(raw):
        raise ProjectError(
            f"{label} cannot traverse a symbolic link or reparse point: {raw}"
        )
    try:
        with raw.open("rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read()
            after = os.fstat(handle.fileno())
        path_stat = raw.stat()
    except OSError as exc:
        raise ProjectError(f"Unable to read {label}: {raw}: {exc}") from exc
    identity_before = (
        getattr(before, "st_dev", None),
        getattr(before, "st_ino", None),
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        getattr(after, "st_dev", None),
        getattr(after, "st_ino", None),
        after.st_size,
        after.st_mtime_ns,
    )
    path_identity = (
        getattr(path_stat, "st_dev", None),
        getattr(path_stat, "st_ino", None),
        path_stat.st_size,
        path_stat.st_mtime_ns,
    )
    if identity_before != identity_after or identity_after != path_identity:
        raise ProjectError(f"{label} changed while being read: {raw}")
    return content


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _link_like(path: Path) -> bool:
    """Detect symlinks, junctions and Windows reparse points without following them."""

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


def _contains_symlink(path: Path) -> bool:
    current = path
    while True:
        if _link_like(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _assert_tree_has_no_links(root: Path) -> None:
    """Refuse to copy or mutate a project tree containing link-like entries."""

    if _contains_symlink(root):
        raise ProjectError(f"Project path cannot traverse a symbolic link or junction: {root}")
    try:
        entries = list(root.rglob("*"))
    except OSError as exc:
        raise ProjectError(f"Unable to inspect project tree: {exc}") from exc
    for entry in entries:
        if _link_like(entry):
            raise ProjectError(f"Project tree contains a symbolic link or junction: {entry}")


def _validate_transaction_target(
    target: Path, root: Path | None = None, *, require_regular_file: bool = True
) -> Path:
    raw = Path(target).expanduser()
    if _contains_symlink(raw):
        raise ProjectError(f"Transactional target cannot use a symbolic link: {target}")
    resolved = raw.resolve()
    if root is not None and not is_within(resolved, root):
        raise ProjectError(f"Transactional target is outside journal root: {resolved}")
    if require_regular_file and resolved.exists() and not resolved.is_file():
        raise ProjectError(f"Transactional target is not a regular file: {resolved}")
    return resolved


def _atomic_write_bytes_unpatched(path: Path, content: bytes) -> None:
    """Write transaction metadata without sharing the target replace hook."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # Use the import-time function so journal bookkeeping stays
        # independent from the target-file replace hook used by tests.
        _REAL_OS_REPLACE(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    """Best-effort parent-directory durability for rename metadata.

    Windows does not expose a portable stdlib directory handle that can be
    fsynced.  POSIX filesystems generally do; failures there are deliberately
    ignored because the file data itself has already been flushed and a
    platform-specific directory fsync must not make the transaction unusable.
    """

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _transaction_root(root: Path) -> Path:
    return root / TRANSACTION_DIRNAME


def _remove_transaction_directory(path: Path) -> None:
    """Remove transaction metadata only after validating the complete tree."""

    files: list[Path] = []
    directories: list[Path] = []

    def inspect(directory: Path) -> None:
        if _link_like(directory):
            raise ProjectError(
                f"Refusing to remove link-like transaction artifact: {directory}"
            )
        if not directory.is_dir():
            raise ProjectError(f"Transaction artifact is not a directory: {directory}")
        for child in directory.iterdir():
            if _link_like(child):
                raise ProjectError(
                    f"Refusing to remove link-like transaction artifact: {child}"
                )
            if child.is_dir():
                inspect(child)
            elif child.is_file():
                files.append(child)
            else:
                raise ProjectError(f"Unexpected transaction artifact: {child}")
        directories.append(directory)

    if _link_like(path):
        raise ProjectError(
            f"Refusing to remove link-like transaction artifact: {path}"
        )
    if not path.exists():
        return
    inspect(path)
    for child in files:
        if _link_like(child) or not child.is_file():
            raise ProjectError(
                f"Transaction artifact changed during cleanup: {child}"
            )
        child.unlink()
    for directory in directories:
        if _link_like(directory) or not directory.is_dir():
            raise ProjectError(
                f"Transaction directory changed during cleanup: {directory}"
            )
        directory.rmdir()


def _read_transaction_journal(path: Path) -> dict[str, Any]:
    if _contains_symlink(path):
        raise ProjectError(f"Transaction journal cannot use a symbolic link: {path}")
    if not path.is_file():
        raise ProjectError(f"Transaction journal is missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Unreadable transaction journal: {path}: {exc}") from exc
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") != TRANSACTION_SCHEMA_VERSION
    ):
        raise ProjectError(f"Unsupported transaction journal: {path}")
    status = data.get("status")
    if not isinstance(status, str) or status not in {
        "prepared",
        "applying",
        "rolled_back",
        "committed",
    }:
        raise ProjectError(f"Invalid transaction journal status: {path}")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise ProjectError(f"Transaction journal has no files: {path}")
    preconditions = data.get("preconditions", [])
    if not isinstance(preconditions, list):
        raise ProjectError(f"Transaction journal has invalid preconditions: {path}")
    for item in preconditions:
        if not isinstance(item, dict):
            raise ProjectError(f"Transaction journal has an invalid precondition: {path}")
        target = item.get("target")
        expected = item.get("expected_sha256")
        if (
            not isinstance(target, str)
            or not target
            or Path(target).is_absolute()
            or "." in Path(target).parts
            or ".." in Path(target).parts
            or "\\" in target
            or Path(target).as_posix() != target
        ):
            raise ProjectError(f"Transaction journal has an invalid precondition path: {path}")
        if expected is not None and (
            not isinstance(expected, str) or not SHA256_RE.fullmatch(expected)
        ):
            raise ProjectError(f"Transaction journal has an invalid precondition hash: {path}")
    return data


def _validate_transaction_preconditions(
    root: Path,
    journal: dict[str, Any],
    transaction_targets: set[Path],
) -> None:
    """Verify files read but not replaced by a transaction are unchanged."""

    preconditions = journal.get("preconditions", [])
    if not isinstance(preconditions, list):  # defensive for in-memory callers
        raise ProjectError("Transaction journal has invalid preconditions")
    seen: set[str] = set()
    for item in preconditions:
        if not isinstance(item, dict):
            raise ProjectError("Transaction journal has an invalid precondition")
        relative = item.get("target")
        expected = item.get("expected_sha256")
        if not isinstance(relative, str):
            raise ProjectError("Transaction journal precondition target must be a string")
        target = _validate_transaction_target(root / Path(relative), root)
        key = os.path.normcase(str(target))
        if key in seen:
            raise ProjectError(f"Transaction journal contains duplicate precondition: {relative}")
        seen.add(key)
        if key in {os.path.normcase(str(item_target)) for item_target in transaction_targets}:
            raise ProjectError(
                f"Transaction precondition overlaps a replaced target: {relative}"
            )
        current = _current_transaction_hash(target)
        if current != expected:
            raise ProjectError(
                "Transactional precondition changed during recovery: " + relative
            )


def _validate_transaction_entries(
    root: Path,
    transaction_dir: Path,
    journal: dict[str, Any],
    *,
    inspect_current: bool,
    verify_backups: bool = True,
    verify_preconditions: bool = True,
) -> list[dict[str, Any]]:
    """Validate a journal completely before changing any project file."""

    raw_transaction_dir = transaction_dir.expanduser()
    if _contains_symlink(raw_transaction_dir):
        raise ProjectError(f"Transaction directory cannot use a symbolic link: {transaction_dir}")
    transaction_dir = raw_transaction_dir.resolve()
    transaction_root = transaction_dir.parent.resolve()
    if _contains_symlink(transaction_dir) or not transaction_dir.is_dir():
        raise ProjectError(f"Transaction directory is not a regular directory: {transaction_dir}")
    if not is_within(transaction_dir, transaction_root):
        raise ProjectError(f"Transaction directory escapes its transaction root: {transaction_dir}")
    recorded_root = journal.get("project_root")
    if not isinstance(recorded_root, str) or Path(recorded_root).expanduser().resolve() != root:
        raise ProjectError("Transaction journal belongs to a different project root")
    files = journal.get("files")
    if not isinstance(files, list) or not files:
        raise ProjectError("Transaction journal has no files")
    seen_targets: set[str] = set()
    seen_backups: set[str] = set()
    entries: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            raise ProjectError("Transaction journal contains an invalid file entry")
        relative = item.get("target")
        if not isinstance(relative, str) or not relative:
            raise ProjectError("Transaction journal target must be a relative path")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ProjectError(
                "Transaction journal target must stay relative to the project root"
            )
        target = _validate_transaction_target(
            root / relative_path, root, require_regular_file=inspect_current
        )
        if not is_within(target, root):
            raise ProjectError("Transaction journal target escapes the project root")
        if is_within(target, transaction_root):
            raise ProjectError("Transaction journal cannot target its transaction metadata")
        target_key = os.path.normcase(str(target))
        if target_key in seen_targets:
            raise ProjectError(f"Transaction journal contains duplicate target: {relative}")
        seen_targets.add(target_key)
        prior_exists = item.get("prior_exists")
        prior_hash = item.get("prior_sha256")
        new_hash = item.get("new_sha256")
        if not isinstance(prior_exists, bool) or not isinstance(new_hash, str) or not SHA256_RE.fullmatch(new_hash):
            raise ProjectError("Transaction journal has invalid file hashes")
        if prior_exists and (
            not isinstance(prior_hash, str) or not SHA256_RE.fullmatch(prior_hash)
        ):
            raise ProjectError("Transaction journal has an invalid prior file hash")
        if not prior_exists and prior_hash is not None:
            raise ProjectError("Transaction journal has an unexpected prior file hash")
        backup_name = item.get("backup")
        backup: Path | None = None
        prior_bytes: bytes | None = None
        if prior_exists:
            if not isinstance(backup_name, str) or Path(backup_name).name != backup_name:
                raise ProjectError(f"Transaction journal is missing backup: {relative}")
            backup_key = os.path.normcase(backup_name)
            if backup_key in seen_backups:
                raise ProjectError(f"Transaction journal contains duplicate backup: {backup_name}")
            seen_backups.add(backup_key)
            backup = (transaction_dir / backup_name).resolve()
            if not is_within(backup, transaction_dir) or _contains_symlink(
                transaction_dir / backup_name
            ):
                raise ProjectError(f"Transaction backup is outside its journal: {relative}")
            if verify_backups:
                if not backup.is_file():
                    raise ProjectError(f"Transaction backup is missing: {relative}")
                prior_bytes = backup.read_bytes()
                if sha256_bytes(prior_bytes) != prior_hash:
                    raise ProjectError(
                        f"Transaction backup hash does not match journal: {relative}"
                    )
        else:
            if backup_name is not None:
                raise ProjectError(f"Transaction journal has an unexpected backup: {relative}")

        current_hash: str | None = None
        action = "skip"
        if inspect_current:
            current = target.read_bytes() if target.is_file() else None
            current_hash = sha256_bytes(current) if current is not None else None
            expected_prior_hash = prior_hash if prior_exists else None
            if current_hash == expected_prior_hash:
                action = "skip"
            elif current_hash != new_hash:
                if prior_exists:
                    raise ProjectError(
                        f"Transaction recovery found an unexpected change: {relative}"
                    )
                raise ProjectError(
                    f"Transaction recovery found an unexpected new file change: {relative}"
                )
            action = "restore" if prior_exists else "delete"
        entries.append(
            {
                "relative": relative,
                "target": target,
                "prior_exists": prior_exists,
                "prior_hash": prior_hash,
                "prior_bytes": prior_bytes,
                "new_hash": new_hash,
                "action": action,
            }
        )
    if verify_preconditions:
        _validate_transaction_preconditions(
            root,
            journal,
            {entry["target"] for entry in entries},
        )
    return entries


def _restore_transaction(root: Path, transaction_dir: Path, journal: dict[str, Any]) -> None:
    entries = _validate_transaction_entries(
        root,
        transaction_dir,
        journal,
        inspect_current=True,
        verify_preconditions=False,
    )
    for entry in entries:
        target = entry["target"]
        relative = entry["relative"]
        prior_hash = entry["prior_hash"] if entry["prior_exists"] else None
        current = target.read_bytes() if target.is_file() else None
        current_hash = sha256_bytes(current) if current is not None else None
        # Recheck immediately before each mutation so an external edit between
        # prevalidation and the write is never silently overwritten.
        if current_hash == prior_hash:
            continue
        if current_hash != entry["new_hash"]:
            raise ProjectError(f"Transaction recovery found an unexpected change: {relative}")
        if entry["action"] == "restore":
            prior_bytes = entry["prior_bytes"]
            if not isinstance(prior_bytes, bytes):
                raise ProjectError(f"Transaction backup is missing: {relative}")
            _atomic_write_bytes_unpatched(target, prior_bytes)
            if not target.is_file() or sha256_file(target) != prior_hash:
                raise ProjectError(f"Transaction recovery failed to restore: {relative}")
        elif entry["action"] == "delete":
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            if target.exists():
                raise ProjectError(f"Transaction recovery failed to remove: {relative}")


def _current_transaction_hash(target: Path) -> str | None:
    """Return a target hash while rejecting a type or link change."""

    _validate_transaction_target(target)
    if not target.is_file():
        return None
    return sha256_file(target)


def _rollback_transaction_target(
    target: Path, prior: bytes | None, new_hash: str
) -> None:
    """Restore one target only when it still contains this transaction's bytes."""

    prior_hash = sha256_bytes(prior) if prior is not None else None
    current_hash = _current_transaction_hash(target)
    if current_hash == prior_hash:
        return
    if current_hash != new_hash:
        raise ProjectError(
            f"Transactional rollback found an unexpected change: {target}"
        )
    if prior is None:
        target.unlink(missing_ok=True)
        if target.exists():
            raise ProjectError(f"Transactional rollback failed to remove: {target}")
        return
    atomic_write_bytes(target, prior)
    if _current_transaction_hash(target) != prior_hash:
        raise ProjectError(f"Transactional rollback failed to restore: {target}")


def recover_pending_transactions(raw_root: str | Path) -> list[str]:
    """Recover incomplete project file transactions after interruption."""

    root = resolve_root(str(raw_root))
    transaction_root = _transaction_root(root)
    if not transaction_root.exists():
        return []
    if _link_like(transaction_root) or not transaction_root.is_dir():
        raise ProjectError(f"Transaction recovery path is not a directory: {transaction_root}")
    recovered: list[str] = []
    for transaction_dir in sorted(transaction_root.iterdir(), key=lambda path: path.name):
        if _link_like(transaction_dir) or not transaction_dir.is_dir():
            raise ProjectError(f"Unexpected transaction artifact: {transaction_dir}")
        if not is_within(transaction_dir.resolve(), transaction_root.resolve()):
            raise ProjectError(f"Transaction directory escapes the project root: {transaction_dir}")
        journal_path = transaction_dir / "journal.json"
        if not journal_path.exists():
            if any(transaction_dir.iterdir()):
                raise ProjectError(
                    "Transaction directory contains artifacts but no journal: "
                    f"{transaction_dir}"
                )
            transaction_dir.rmdir()
            _fsync_directory(transaction_root)
            recovered.append(f"cleaned empty transaction {transaction_dir.name}")
            continue
        journal = _read_transaction_journal(journal_path)
        if journal["status"] == "committed":
            # The committed marker is authoritative only while every target
            # still contains the bytes named by that marker.  An external
            # edit after commit must leave the journal in place and fail
            # closed instead of erasing the last durable transaction record.
            entries = _validate_transaction_entries(
                root,
                transaction_dir,
                journal,
                inspect_current=False,
                verify_backups=False,
                verify_preconditions=False,
            )
            for entry in entries:
                if _current_transaction_hash(entry["target"]) != entry["new_hash"]:
                    raise ProjectError(
                        "Committed transaction target changed before journal cleanup: "
                        f"{entry['relative']}"
                    )
            _remove_transaction_directory(transaction_dir)
            _fsync_directory(transaction_root)
            recovered.append(f"cleaned committed transaction {transaction_dir.name}")
            continue
        if journal["status"] == "prepared":
            entries = _validate_transaction_entries(
                root,
                transaction_dir,
                journal,
                inspect_current=False,
                verify_backups=False,
                verify_preconditions=False,
            )
            for entry in entries:
                expected = entry["prior_hash"] if entry["prior_exists"] else None
                if _current_transaction_hash(entry["target"]) != expected:
                    raise ProjectError(
                        "Prepared transaction target changed before cleanup: "
                        f"{entry['relative']}"
                    )
            journal["status"] = "rolled_back"
            _atomic_write_bytes_unpatched(
                journal_path, dump_json(journal).encode("utf-8")
            )
            _remove_transaction_directory(transaction_dir)
            _fsync_directory(transaction_root)
            recovered.append(f"discarded prepared transaction {transaction_dir.name}")
            continue
        if journal["status"] == "rolled_back":
            entries = _validate_transaction_entries(
                root,
                transaction_dir,
                journal,
                inspect_current=False,
                verify_backups=False,
                verify_preconditions=False,
            )
            for entry in entries:
                expected = entry["prior_hash"] if entry["prior_exists"] else None
                if _current_transaction_hash(entry["target"]) != expected:
                    raise ProjectError(
                        "Rolled-back transaction target changed before journal cleanup: "
                        f"{entry['relative']}"
                    )
            _remove_transaction_directory(transaction_dir)
            _fsync_directory(transaction_root)
            recovered.append(f"cleaned rolled-back transaction {transaction_dir.name}")
            continue
        _restore_transaction(root, transaction_dir, journal)
        journal["status"] = "rolled_back"
        _atomic_write_bytes_unpatched(
            journal_path, dump_json(journal).encode("utf-8")
        )
        _remove_transaction_directory(transaction_dir)
        _fsync_directory(transaction_root)
        recovered.append(f"rolled back transaction {transaction_dir.name}")
    try:
        transaction_root.rmdir()
    except OSError:
        pass
    return recovered


def assert_no_pending_transactions(raw_root: str | Path) -> None:
    """Fail closed for read/write checks while a journal needs recovery."""

    root = resolve_root(str(raw_root))
    transaction_root = _transaction_root(root)
    if not transaction_root.exists():
        return
    if (
        _link_like(transaction_root)
        or not transaction_root.is_dir()
        or any(transaction_root.iterdir())
    ):
        raise ProjectError(
            "An incomplete project transaction requires recovery before this operation"
        )


def replace_frontmatter(text: str, updates: dict[str, Any]) -> str:
    if not text.startswith("---\n"):
        raise ProjectError("Cannot update a Markdown file without YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ProjectError("Cannot update unterminated YAML frontmatter")
    lines = text[4:end].splitlines()
    pending = {key: str(value) for key, value in updates.items()}
    output: list[str] = []
    for line in lines:
        if ":" not in line:
            output.append(line)
            continue
        key = line.split(":", 1)[0].strip()
        if key in pending:
            output.append(f"{key}: {pending.pop(key)}")
        else:
            output.append(line)
    output.extend(f"{key}: {value}" for key, value in pending.items())
    return "---\n" + "\n".join(output) + text[end:]


def transactional_write(
    files: list[tuple[Path, bytes]],
    validator: Any | None = None,
    *,
    journal_root: str | Path | None = None,
    expected_existing: dict[Path | str, str | None] | None = None,
    expected_targets: dict[Path | str, str | None] | None = None,
) -> None:
    """Replace files atomically, with optional durable crash recovery metadata.

    ``expected_existing`` is a compare-and-swap receipt for files that are
    inspected but not necessarily replaced by this transaction.  Each value is
    the SHA-256 currently expected at the target (or ``None`` when the target
    must not exist).  The receipt is checked both before the first replacement
    and immediately before the commit marker, so provenance manifests cannot be
    committed for an existing source that changed during preparation.

    ``expected_targets`` is the corresponding receipt for files that this
    transaction will replace.  It closes the preparation-to-commit race for
    callers that build replacement bytes from an earlier read.  Unlike
    ``expected_existing``, target receipts intentionally overlap replaced
    targets and are checked before the transaction snapshot and again before
    each replacement.
    """
    if not files:
        return
    normalized_files: list[tuple[Path, bytes]] = []
    for raw_target, content in files:
        if not isinstance(content, bytes):
            raise ProjectError("Transactional file content must be bytes")
        normalized_files.append((Path(raw_target), content))
    targets = [_validate_transaction_target(target) for target, _ in normalized_files]
    if len(set(targets)) != len(targets):
        raise ProjectError("Transactional write cannot contain duplicate targets")

    normalized_preconditions: dict[Path, str | None] = {}
    if expected_existing:
        for raw_target, expected in expected_existing.items():
            target = _validate_transaction_target(Path(raw_target))
            if expected is not None and not SHA256_RE.fullmatch(str(expected)):
                raise ProjectError(
                    f"Transactional precondition has an invalid SHA-256: {target}"
                )
            normalized_preconditions[target] = expected

    normalized_target_preconditions: dict[Path, str | None] = {}
    if expected_targets:
        for raw_target, expected in expected_targets.items():
            target = _validate_transaction_target(Path(raw_target))
            if target not in targets:
                raise ProjectError(
                    "Transactional target precondition does not name a replaced target: "
                    f"{target}"
                )
            if target in normalized_preconditions:
                raise ProjectError(
                    f"Transactional target has both target and existing preconditions: {target}"
                )
            if expected is not None and not SHA256_RE.fullmatch(str(expected)):
                raise ProjectError(
                    f"Transactional target precondition has an invalid SHA-256: {target}"
                )
            normalized_target_preconditions[target] = expected

    def assert_preconditions() -> None:
        for target, expected in normalized_preconditions.items():
            current = _current_transaction_hash(target)
            if current != expected:
                raise ProjectError(
                    "Transactional compare-and-swap precondition failed: "
                    f"{target}"
                )

    def assert_target_preconditions() -> None:
        for target, expected in normalized_target_preconditions.items():
            current = _current_transaction_hash(target)
            if current != expected:
                raise ProjectError(
                    "Transactional target compare-and-swap precondition failed: "
                    f"{target}"
                )

    # Check receipts before creating any journal or temporary file.  This keeps
    # a stale source from producing even a transient manifest update.
    assert_preconditions()
    assert_target_preconditions()

    root: Path | None = None
    transaction_root: Path | None = None
    if journal_root is not None:
        root = resolve_root(str(journal_root))
        if not root.is_dir():
            raise ProjectError(f"Journal root is not a project directory: {root}")
        transaction_root = _transaction_root(root)
        if _link_like(transaction_root) or (
            transaction_root.exists() and not transaction_root.is_dir()
        ):
            raise ProjectError(
                f"Transaction recovery path is not a directory: {transaction_root}"
            )
        for target in targets:
            if not is_within(target, root):
                raise ProjectError(
                    f"Transactional target is outside journal root: {target}"
                )
            if is_within(target, transaction_root):
                raise ProjectError("Transactional target cannot be transaction metadata")
        for target in normalized_preconditions:
            # Read-only compare-and-swap receipts may intentionally refer to
            # an external source file.  They are checked in-process before
            # and after replacement, but are never written into the project
            # recovery journal because recovery cannot mutate external data.
            if is_within(target, transaction_root):
                raise ProjectError(
                    "Transactional precondition cannot reference transaction metadata"
                )
            if target in targets:
                raise ProjectError(
                    f"Transactional precondition overlaps a replaced target: {target}"
                )
        for target in normalized_target_preconditions:
            if not is_within(target, root):
                raise ProjectError(
                    f"Transactional target precondition is outside journal root: {target}"
                )
            if is_within(target, transaction_root):
                raise ProjectError(
                    "Transactional target precondition cannot reference transaction metadata"
                )
        # Recover any previous transaction before reading backups for this one.
        # Otherwise a crashed prior write could be mistaken for the new
        # transaction's original bytes and make a later rollback non-restorative.
        recover_pending_transactions(root)

    new_hashes = {
        target: sha256_bytes(content)
        for target, (_, content) in zip(targets, normalized_files)
    }
    backups: dict[Path, bytes | None] = {
        target: read_stable_bytes(target, label=f"transaction target {target}")
        if target.is_file()
        else None
        for target in targets
    }
    # A target may have been replaced between the first receipt check and the
    # byte snapshot.  Do not let that newer content become the rollback base.
    assert_target_preconditions()
    contents = {
        target: content for target, (_, content) in zip(targets, normalized_files)
    }
    temp_paths: dict[Path, Path] = {}
    attempted: list[Path] = []
    transaction_dir: Path | None = None
    journal_path: Path | None = None
    journal_committed = False

    if root is not None:
        if transaction_root is None:  # pragma: no cover - kept as an invariant guard
            raise ProjectError("Transactional journal root was not initialized")
        transaction_root.mkdir(parents=True, exist_ok=True)
        transaction_dir = transaction_root / uuid.uuid4().hex
        transaction_dir.mkdir()
        journal_files: list[dict[str, Any]] = []
        try:
            for index, target in enumerate(targets):
                prior = backups[target]
                backup_name: str | None = None
                if prior is not None:
                    backup_name = f"backup-{index:04d}.bin"
                    _atomic_write_bytes_unpatched(transaction_dir / backup_name, prior)
                journal_files.append(
                    {
                        "target": target.relative_to(root).as_posix(),
                        "prior_exists": prior is not None,
                        "prior_sha256": sha256_bytes(prior) if prior is not None else None,
                        "new_sha256": new_hashes[target],
                        "backup": backup_name,
                    }
                )
            journal_path = transaction_dir / "journal.json"
            journal = {
                "schema_version": TRANSACTION_SCHEMA_VERSION,
                "status": "prepared",
                "project_root": str(root),
                "created_at": utc_now(),
                "files": journal_files,
                "preconditions": [
                    {
                        "target": target.relative_to(root).as_posix(),
                        "expected_sha256": expected,
                    }
                    for target, expected in sorted(
                        (
                            item
                            for item in normalized_preconditions.items()
                            if is_within(item[0], root)
                        ),
                        key=lambda item: item[0].relative_to(root).as_posix(),
                    )
                ],
            }
            _atomic_write_bytes_unpatched(journal_path, dump_json(journal).encode("utf-8"))
            journal["status"] = "applying"
            _atomic_write_bytes_unpatched(journal_path, dump_json(journal).encode("utf-8"))
        except BaseException:
            if transaction_dir is not None:
                try:
                    _remove_transaction_directory(transaction_dir)
                except OSError:
                    pass
            raise
    try:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "wb",
                delete=False,
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".transaction.tmp",
            )
            temp_path = Path(handle.name)
            with handle:
                handle.write(contents[target])
                handle.flush()
                os.fsync(handle.fileno())
            temp_paths[target] = temp_path
        for target in targets:
            # Record the attempt before calling the OS.  This covers both a
            # signal after replace but before normal bookkeeping and a signal
            # immediately before replace; rollback checks the target hash and
            # therefore only touches bytes owned by this transaction.
            prior = backups[target]
            expected_hash = sha256_bytes(prior) if prior is not None else None
            if _current_transaction_hash(target) != expected_hash:
                raise ProjectError(
                    "Transactional compare-and-swap detected a concurrent change: "
                    f"{target}"
                )
            if target in normalized_target_preconditions:
                expected_target = normalized_target_preconditions[target]
                if _current_transaction_hash(target) != expected_target:
                    raise ProjectError(
                        "Transactional target compare-and-swap detected a concurrent change: "
                        f"{target}"
                    )
            attempted.append(target)
            os.replace(temp_paths[target], target)
            _fsync_directory(target.parent)
        if validator is not None:
            validator()
        assert_preconditions()
        for target in targets:
            if _current_transaction_hash(target) != new_hashes[target]:
                raise ProjectError(
                    "Transactional target changed before commit marker: "
                    f"{target}"
                )
        if journal_path is not None:
            journal = _read_transaction_journal(journal_path)
            journal["status"] = "committed"
            _atomic_write_bytes_unpatched(
                journal_path, dump_json(journal).encode("utf-8")
            )
            journal_committed = True
    except BaseException as exc:
        # A signal can arrive immediately after the durable committed marker
        # is replaced, before the assignment above or before the try block
        # exits.  Once that marker exists, rolling bytes back would turn a
        # committed transaction into a false rollback.
        if journal_path is not None and not journal_committed:
            try:
                marker = _read_transaction_journal(journal_path)
                journal_committed = marker.get("status") == "committed"
            except BaseException:
                pass
        if journal_committed:
            # Preserve the committed journal after an interrupted control
            # path.  The next controlled operation validates every target and
            # removes it.  Returning here lets the surrounding write guard
            # commit the already-refreshed SQLite base hash.
            return
        rollback_errors: list[str] = []
        for target in reversed(attempted):
            try:
                _rollback_transaction_target(
                    target, backups[target], new_hashes[target]
                )
            except BaseException as rollback_exc:  # pragma: no cover - catastrophic I/O
                rollback_errors.append(f"{target}: {rollback_exc}")
        if not rollback_errors and transaction_dir is not None:
            try:
                if journal_path is None:
                    raise ProjectError("Transaction journal path is unavailable")
                rollback_journal = _read_transaction_journal(journal_path)
                rollback_journal["status"] = "rolled_back"
                _atomic_write_bytes_unpatched(
                    journal_path, dump_json(rollback_journal).encode("utf-8")
                )
                _remove_transaction_directory(transaction_dir)
                transaction_root = transaction_dir.parent
                transaction_root.rmdir()
            except BaseException as cleanup_exc:
                rollback_errors.append(f"transaction journal cleanup: {cleanup_exc}")
        suffix = (
            "; rollback errors: " + "; ".join(rollback_errors)
            if rollback_errors
            else ""
        )
        raise ProjectError(
            f"Transactional write failed and was rolled back: {exc}{suffix}"
        ) from exc
    else:
        if transaction_dir is not None:
            # A committed journal is deliberately cleaned after the commit
            # marker is durable.  If cleanup is interrupted, the next startup
            # removes it without rolling back already committed bytes.
            try:
                _remove_transaction_directory(transaction_dir)
                transaction_dir.parent.rmdir()
            except BaseException:
                # The durable committed marker is enough to make cleanup
                # idempotent; a later command will remove the leftovers.
                pass
    finally:
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)


UPGRADE_IGNORED_PARTS = frozenset(
    {
        "exports",
        "staging",
        ".novel-cache",
        ".novel-transaction",
        ".novel-upgrade-transaction",
        ".novel-export.lock",
        ".novel-export-journal.json",
        "__pycache__",
    }
)


def _upgrade_transaction_root(root: Path) -> Path:
    return root / UPGRADE_TRANSACTION_DIRNAME


def _upgrade_transaction_path(root: Path) -> Path:
    return _upgrade_transaction_root(root) / "journal.json"


def _upgrade_relpath(value: Any, *, label: str = "upgrade path") -> str:
    if not isinstance(value, str) or not value:
        raise ProjectError(f"{label} must be a non-empty relative path")
    if "\\" in value:
        raise ProjectError(f"{label} must use POSIX separators")
    pure = Path(value)
    if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise ProjectError(f"{label} must be a normalized relative path")
    normalized = pure.as_posix()
    if normalized != value or not normalized:
        raise ProjectError(f"{label} must be a normalized relative path")
    return normalized


def _upgrade_should_skip(relative: Path) -> bool:
    return any(part in UPGRADE_IGNORED_PARTS for part in relative.parts)


def _upgrade_file_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Capture regular project files without following link-like entries."""

    _assert_tree_has_no_links(root)
    snapshot: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        raise ProjectError(f"Project directory does not exist: {root}")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _upgrade_should_skip(relative):
            continue
        if _link_like(path):
            raise ProjectError(f"Project tree contains a link-like entry: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ProjectError(f"Project tree contains a non-regular entry: {path}")
        key = relative.as_posix()
        content = path.read_bytes()
        snapshot[key] = {
            "sha256": sha256_bytes(content),
            "size_bytes": len(content),
        }
    return snapshot


def _upgrade_dir_snapshot(root: Path) -> set[str]:
    _assert_tree_has_no_links(root)
    directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _upgrade_should_skip(relative) or not path.is_dir():
            continue
        if _link_like(path):
            raise ProjectError(f"Project tree contains a link-like directory: {path}")
        directories.add(relative.as_posix())
    return directories


def _upgrade_assert_existing_snapshot(
    root: Path, snapshot: dict[str, dict[str, Any]]
) -> None:
    for relative, expected in snapshot.items():
        path = root / relative
        if _contains_symlink(path) or not path.is_file():
            raise ProjectError(f"Existing project file changed during upgrade: {relative}")
        content = path.read_bytes()
        if (
            len(content) != expected.get("size_bytes")
            or sha256_bytes(content) != expected.get("sha256")
        ):
            raise ProjectError(f"Existing project file changed during upgrade: {relative}")


def _upgrade_assert_expected_snapshot(
    root: Path,
    existing: dict[str, dict[str, Any]],
    created: list[dict[str, Any]],
    *,
    require_all_created: bool,
) -> None:
    expected_created = {item["path"]: item for item in created}
    current = _upgrade_file_snapshot(root)
    unexpected = sorted(set(current) - set(existing) - set(expected_created))
    if unexpected:
        raise ProjectError(
            "Unexpected project files appeared during upgrade: "
            + ", ".join(unexpected[:8])
        )
    _upgrade_assert_existing_snapshot(root, existing)
    for relative, item in expected_created.items():
        actual = current.get(relative)
        if actual is None:
            if require_all_created:
                raise ProjectError(f"Upgrade-created file is missing: {relative}")
            continue
        if (
            actual.get("sha256") != item.get("sha256")
            or actual.get("size_bytes") != item.get("size_bytes")
        ):
            raise ProjectError(f"Upgrade-created file changed unexpectedly: {relative}")


def _write_upgrade_journal(root: Path, journal: dict[str, Any]) -> None:
    directory = _upgrade_transaction_root(root)
    if _contains_symlink(directory) or (directory.exists() and not directory.is_dir()):
        raise ProjectError(f"Upgrade transaction path is not a directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes_unpatched(
        directory / "journal.json",
        dump_json(journal).encode("utf-8"),
    )


def _read_upgrade_journal(root: Path) -> dict[str, Any]:
    directory = _upgrade_transaction_root(root)
    journal_path = _upgrade_transaction_path(root)
    if _contains_symlink(directory) or not directory.is_dir():
        raise ProjectError(f"Upgrade transaction path is not a directory: {directory}")
    if _contains_symlink(journal_path) or not journal_path.is_file():
        raise ProjectError(f"Upgrade transaction journal is missing: {journal_path}")
    try:
        value = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Unreadable upgrade transaction journal: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != UPGRADE_TRANSACTION_SCHEMA_VERSION:
        raise ProjectError("Unsupported upgrade transaction journal schema")
    recorded_root = value.get("project_root")
    if not isinstance(recorded_root, str) or Path(recorded_root).expanduser().resolve() != root:
        raise ProjectError("Upgrade transaction journal belongs to another project")
    if value.get("status") not in {"prepared", "applying", "committed"}:
        raise ProjectError("Invalid upgrade transaction journal status")
    created_dirs = value.get("created_dirs")
    files = value.get("files")
    existing_files = value.get("existing_files")
    if not isinstance(created_dirs, list) or not isinstance(files, list):
        raise ProjectError("Upgrade transaction journal has invalid created entries")
    if not isinstance(existing_files, list):
        raise ProjectError("Upgrade transaction journal has no existing file snapshot")
    seen_dirs: set[str] = set()
    for item in created_dirs:
        relative = _upgrade_relpath(item, label="created directory")
        if relative in seen_dirs:
            raise ProjectError("Upgrade transaction journal contains duplicate directories")
        seen_dirs.add(relative)
    seen_files: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ProjectError("Upgrade transaction journal contains an invalid file")
        relative = _upgrade_relpath(item.get("path"), label="created file")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if relative in seen_files or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ProjectError("Upgrade transaction journal contains invalid file hashes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ProjectError("Upgrade transaction journal contains invalid file sizes")
        seen_files.add(relative)
    seen_existing: set[str] = set()
    for item in existing_files:
        if not isinstance(item, dict):
            raise ProjectError("Upgrade transaction journal contains an invalid existing snapshot")
        relative = _upgrade_relpath(item.get("path"), label="existing file")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if relative in seen_existing or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ProjectError("Upgrade transaction journal contains invalid existing hashes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ProjectError("Upgrade transaction journal contains invalid existing sizes")
        seen_existing.add(relative)
    return value


def _remove_upgrade_transaction(root: Path) -> None:
    directory = _upgrade_transaction_root(root)
    if not directory.exists():
        return
    if _contains_symlink(directory) or not directory.is_dir():
        raise ProjectError(f"Upgrade transaction path is not a regular directory: {directory}")
    _assert_tree_has_no_links(directory)
    _remove_transaction_directory(directory)


def recover_pending_upgrade(raw_root: str | Path) -> list[str]:
    """Recover an interrupted add-only project upgrade fail-closed."""

    root = resolve_root(str(raw_root))
    directory = _upgrade_transaction_root(root)
    if not directory.exists():
        return []
    if _contains_symlink(directory) or not directory.is_dir():
        raise ProjectError(f"Upgrade transaction path is not a directory: {directory}")
    # A nested canonical transaction may have been interrupted before the
    # upgrade journal was advanced. Restore it first, then inspect the upgrade
    # additions against their durable hashes.
    recover_pending_transactions(root)
    journal = _read_upgrade_journal(root)
    existing = {
        item["path"]: item
        for item in journal["existing_files"]
        if isinstance(item, dict)
    }
    created = [item for item in journal["files"] if isinstance(item, dict)]
    _upgrade_assert_expected_snapshot(
        root, existing, created, require_all_created=False
    )
    present = 0
    for item in created:
        relative = item["path"]
        path = root / relative
        if _contains_symlink(path):
            raise ProjectError(f"Upgrade-created file became a link: {relative}")
        if not path.exists():
            continue
        if not path.is_file():
            raise ProjectError(f"Upgrade-created path is not a file: {relative}")
        content = path.read_bytes()
        if len(content) != item["size_bytes"] or sha256_bytes(content) != item["sha256"]:
            raise ProjectError(f"Upgrade-created file changed unexpectedly: {relative}")
        present += 1

    if journal["status"] == "committed" or present == len(created):
        # All additions are present. A committed marker (or a complete set
        # after a crash before marker bookkeeping) is safe to retain; validate
        # the resulting project before deleting the only recovery record.
        errors, _ = collect_validation(root, check_transactions=False)
        if errors:
            raise ProjectError(
                "Committed upgrade cannot be validated; recovery record retained: "
                + "; ".join(errors[:8])
            )
        _remove_upgrade_transaction(root)
        return [f"cleaned completed upgrade for {root}"]

    # Partial application: only remove files whose exact bytes are still the
    # bytes named by this journal. Anything changed or replaced above fails
    # closed and remains available for manual recovery.
    for item in reversed(created):
        path = root / item["path"]
        if not path.exists():
            continue
        content = path.read_bytes()
        if len(content) != item["size_bytes"] or sha256_bytes(content) != item["sha256"]:
            raise ProjectError(f"Cannot safely roll back upgrade file: {item['path']}")
        path.unlink()
    for relative in sorted(
        (_upgrade_relpath(item, label="created directory") for item in journal["created_dirs"]),
        key=lambda value: (value.count("/"), value),
        reverse=True,
    ):
        path = root / relative
        if not path.exists():
            continue
        if _contains_symlink(path) or not path.is_dir():
            raise ProjectError(f"Cannot safely roll back upgrade directory: {relative}")
        try:
            path.rmdir()
        except OSError as exc:
            raise ProjectError(
                f"Upgrade directory is no longer empty; recovery retained: {relative}"
            ) from exc
    _remove_upgrade_transaction(root)
    return [f"rolled back incomplete upgrade for {root}"]


def markdown_templates(
    title: str, work_type: str = "serial_novel"
) -> dict[str, str]:
    if work_type not in WORK_TYPES:
        raise ProjectError(f"Unsupported work_type: {work_type}")
    short_story = work_type == "short_story"
    work_label = "短故事" if short_story else "小说"
    outline_label = "全篇结构" if short_story else "总纲"
    index_title = "短故事主稿索引" if short_story else "章节索引"
    index_note = (
        "短故事只提交一个完整正文单元；该行链接是唯一正文主稿。"
        if short_story
        else "每个已提交章节恰好一行；摘要只写正文已经发生的事实。"
    )
    index_unit = "单元" if short_story else "章号"
    memory_label = "全篇长期记忆" if short_story else "全书长期记忆"
    current_label = "当前全篇状态" if short_story else "当前阶段与不可逆转折"
    fanqie_adapter = {
        "status": "implemented",
        "market_window_months": [3, 6],
        "publication_profile": "fanqie_public",
        "rules_last_verified_at": None,
        "rule_source_ids": [],
    }
    publication_profiles = {
        "author_draft": {
            "boundary": "author_confirmed_content_scope"
        },
        "fanqie_public": {
            "boundary": "verify_current_official_rules_before_publication"
        },
    }
    if short_story:
        fanqie_adapter.update(
            {
                "publication_profile": "fanqie_short_story_public",
                "market_scope": "serial_novel_library_only",
                "short_story_market_data": "not_implemented",
            }
        )
        publication_profiles["fanqie_short_story_public"] = {
            "boundary": (
                "verify_current_short_story_rules_fields_and_limits_"
                "before_publication"
            )
        }
    return {
        "planning/framework-session.md": (
            "---\n"
            f"schema_version: {SCHEMA_VERSION}\n"
            "stage: discovery\n"
            "confirmation: pending\n"
            "requirements_confidence: 0\n"
            "story_confidence: 0\n"
            f"updated_at: {utc_now()}\n"
            "---\n\n"
            f"# {title}：{work_label}互动框架会话\n\n"
            "> 本文件保存策划恢复点，不是已确认正典。模型补全先进入“暂定”。\n\n"
            "## 用户原始材料\n\n[待补充]\n\n"
            "## 理解状态\n\n"
            "### 需求层\n\n"
            "- 已确认：[待补充]\n"
            "- 已委托：[待补充]\n"
            "- 阻断项：[待补充]\n\n"
            "### 故事层\n\n"
            "- 已确认：[待补充]\n"
            "- 已委托：[待补充]\n"
            "- 阻断项：[待补充]\n\n"
            "## 已确认\n\n"
            "| ID | 类别 | 作者决定 | 影响范围 |\n"
            "|---|---|---|---|\n\n"
            "## 暂定\n\n"
            "| ID | 工作假设 | 暂定原因 | 确认条件 |\n"
            "|---|---|---|---|\n\n"
            "## 未决定\n\n"
            "| ID | 开放问题 | 为什么影响后续 | 最晚决定时间 |\n"
            "|---|---|---|---|\n\n"
            "## 已排除或被替代\n\n"
            "| ID | 方向或旧决定 | 排除或替代原因 | 替代项 |\n"
            "|---|---|---|---|\n\n"
            "## 下一轮\n\n"
            "- 当前主题：故事素材与创作边界\n"
            "- 待讨论：一句话灵感、主要题材、必须保留、明确避免\n\n"
            "## 框架确认单\n\n"
            "- 暂定书名：[待确认]\n"
            "- 题材与目标读者：[待确认]\n"
            "- 预计规模：[待确认]\n"
            "- 一句话故事：[待确认]\n"
            "- 读者承诺：[待确认]\n"
            "- 核心冲突与失败代价：[待确认]\n"
            "- 主题问题：[待确认]\n"
            "- 主角驱动力与人物弧：[待确认]\n"
            "- 主要对抗力量与关键关系：[待确认]\n"
            "- 世界硬规则：[待确认]\n"
            "- 叙事声音与内容边界：[待确认]\n"
            f"- {'全篇推进与结局落点' if short_story else '全书阶段与结局方向'}：[待确认]\n"
            "- 必须保留与明确避免：[待确认]\n"
            "- 仍未决定：[待确认]\n"
        ),
        "research/source-index.md": (
            "# 研究来源索引\n\n"
            "> 所有当前性结论记录绝对日期；观察、解释和建议分开。\n\n"
            "| ID | 查询或页面 | 来源与 URL | 页面日期 | 观察时间 | 可见指标及定义 | 访问限制 | 可信度 |\n"
            "|---|---|---|---|---|---|---|---|\n"
        ),
        "research/market-scan.md": (
            "# 市场扫描\n\n"
            "## 研究问题与时间窗口\n\n[待确认]\n\n"
            "## 近期公开信号\n\n[尚未研究]\n\n"
            "## 目标受众与阅读入口\n\n[尚未研究]\n\n"
            "## 类型拥挤度、机会与不确定性\n\n[尚未研究]\n\n"
            "## 三个候选原创方向\n\n[候选作品获批并完成研究后生成]\n"
        ),
        "research/comparable-works.md": (
            "---\n"
            f"schema_version: {SCHEMA_VERSION}\n"
            "candidate_approval: pending\n"
            "deep_analysis: not_started\n"
            f"updated_at: {utc_now()}\n"
            "---\n\n"
            "# 参考作品候选与机制分析\n\n"
            "## 候选清单\n\n"
            "| ID | 层级 | 作品与作者 | 来源 | 公开时间或指标 | 相关性 | 拟研究机制 | 相似风险 |\n"
            "|---|---|---|---|---|---|---|---|\n\n"
            "## 用户审批记录\n\n"
            "- 状态：待审批\n"
            "- 批准或退回时间：\n"
            "- 用户意见：\n\n"
            "## 获批作品深度拆解\n\n"
            "[候选未获批准前不得开始]\n"
        ),
        "research/originality-map.md": (
            "# 原创性地图\n\n"
            "## 机制来源与抽象用途\n\n"
            "| ID | 来源作品 | 可迁移机制 | 禁止复用的独特元素 |\n"
            "|---|---|---|---|\n\n"
            "## 因果改造与创新点\n\n[待研究与作者确认]\n\n"
            "## 相似风险检查\n\n"
            "- 一句话故事映射风险：[待检查]\n"
            "- 主要人物关系映射风险：[待检查]\n"
            "- 世界规则映射风险：[待检查]\n"
            "- 前三个关键节点映射风险：[待检查]\n"
            "- 核心反转映射风险：[待检查]\n"
        ),
        "research/platform.json": dump_json(
            {
                "schema_version": SCHEMA_VERSION,
                "architecture": "core_plus_platform_adapters",
                "primary_platform": None,
                "adapters": {"fanqie": fanqie_adapter},
                "publication_profiles": publication_profiles,
            }
        ),
        "research/source-manifest.jsonl": "",
        "research/originality-plan.json": dump_json(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "draft",
                "candidate": {
                    "logline": {
                        "text": "",
                        "influences": [],
                        "causal_transformation": "",
                    },
                    "relationships": [],
                    "world_rules": [],
                    "first_three_nodes": [],
                    "core_twist": {
                        "text": "",
                        "influences": [],
                        "causal_transformation": "",
                    },
                },
                "references": [],
                "thresholds": {"structural_similarity_review": 0.58},
            }
        ),
        "story-bible/premise.md": (
            f"# {title}：故事核心\n\n"
            "## 一句话故事\n\n[待作者确认]\n\n"
            "## 读者承诺\n\n[待作者确认]\n\n"
            "## 核心冲突与代价\n\n[待作者确认]\n\n"
            "## 主题问题\n\n[待作者确认]\n"
        ),
        "story-bible/cast.md": (
            "# 人物档案\n\n"
            "记录主要人物的欲望、缺陷、策略、知识边界、关系和声音标记。\n"
        ),
        "story-bible/world.md": (
            "# 世界规则\n\n"
            "记录会影响选择和因果的规则、成本、限制、控制者与例外。\n"
        ),
        "story-bible/style-guide.md": (
            "# 叙事声音\n\n"
            "## POV 与叙述距离\n\n[待作者确认]\n\n"
            "## 时态、语气与节奏\n\n[待作者确认]\n\n"
            "## 人物声音与避免项\n\n[待作者确认]\n"
        ),
        "outlines/master-outline.md": (
            f"# {title}：{outline_label}\n\n"
            + (
                "## 开篇承诺与触发\n\n[待作者确认]\n\n"
                "## 场景推进、升级与关键反转\n\n[待作者确认]\n\n"
                "## 高潮选择、情绪兑现与结尾余味\n\n[待作者确认]\n"
                if short_story
                else
                "## 起始失衡\n\n[待作者确认]\n\n"
                "## 主要升级与不可逆转折\n\n[待作者确认]\n\n"
                "## 高潮选择与结局余波\n\n[待作者确认]\n"
            )
        ),
        "manuscript/index.md": (
            f"# {index_title}\n\n"
            f"> {index_note}\n\n"
            f"| {index_unit} | 标题 | POV | 故事时间 | 地点 | 事实摘要 | 关键变化 | 线索 ID | 正文 |\n"
            "|---:|---|---|---|---|---|---|---|---|\n"
        ),
        "memory/book-summary.md": (
            f"# {title}：{memory_label}\n\n"
            "## 故事承诺与核心冲突\n\n[待作者确认]\n\n"
            f"## {current_label}\n\n[尚未开始正文]\n\n"
            "## 主要人物、关系与长期后果\n\n[待作者确认]\n\n"
            "## 已确认规则、真相与活跃主线\n\n[待作者确认]\n"
        ),
        "memory/decisions.md": (
            "# 作者决策与追溯修改\n\n"
            "只记录作者明确确认的裁决，不记录模型推断。\n\n"
            "| ID | 记录时间 | 生效范围 | 决策或被替代事实 | 需要同步的文件 |\n"
            "|---|---|---|---|---|\n"
        ),
        "continuity/timeline.md": (
            "# 时间线\n\n"
            "| 故事时间 | 章节 | 事件 | 直接后果 |\n"
            "|---|---:|---|---|\n"
        ),
        "continuity/threads.md": (
            "# 线索账本\n\n"
            "| ID | 状态 | 首次出现 | 知情者 | 回收窗口 | 说明 |\n"
            "|---|---|---:|---|---|---|\n"
        ),
    }


def chapter_files(root: Path) -> list[Path]:
    """Return canonical and legacy per-chapter manuscript files."""
    candidates: list[Path] = []
    chapter_dir = root / "manuscript/chapters"
    if chapter_dir.is_dir():
        candidates.extend(chapter_dir.glob("*.md"))

    legacy_dir = root / "manuscript"
    if legacy_dir.is_dir():
        candidates.extend(
            path
            for path in legacy_dir.glob("*.md")
            if path.name.lower() != "index.md"
        )

    return sorted(
        {path for path in candidates if CHAPTER_NAME.fullmatch(path.name)},
        key=lambda path: (path.name[:4], path.as_posix()),
    )


def markdown_link_targets(text: str) -> list[str]:
    return [
        match.group("target").strip().replace("\\", "/").removeprefix("./")
        for match in MARKDOWN_LINK.finditer(text)
    ]


def index_titles(text: str) -> dict[str, str]:
    titles: dict[str, str] = {}
    for line in text.splitlines():
        match = INDEX_TITLE.match(line.strip())
        if match is None:
            continue
        titles[match.group("number")] = match.group("title").replace("\\|", "|").strip()
    return titles


def chapter_number_value(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    total = 0
    current = 0
    for character in raw:
        if character in digits:
            current = digits[character]
        elif character in units:
            total += (current or 1) * units[character]
            current = 0
        else:
            return None
    return total + current


def chapter_heading(path: Path, *, short_story: bool) -> tuple[int | None, str] | None:
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0].lstrip("\ufeff")
    except (IndexError, OSError, UnicodeError):
        return None
    if short_story:
        match = re.fullmatch(r"#\s+(.+?)\s*", first_line)
        return (1, match.group(1).strip()) if match else None
    match = SERIAL_CHAPTER_HEADING.fullmatch(first_line)
    if match is None:
        return None
    return chapter_number_value(match.group("number")), match.group("title").strip()


def parse_frontmatter_metadata(text: str, *, label: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ProjectError(f"Missing YAML frontmatter in {label}")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ProjectError(f"Unterminated YAML frontmatter in {label}")

    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def frontmatter_metadata(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProjectError(f"Missing file: {path}") from exc
    return parse_frontmatter_metadata(text, label=str(path))


def init_project(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    title = args.title.strip()
    if not title:
        raise ProjectError("Title cannot be empty.")
    work_type = getattr(args, "work_type", "serial_novel")
    if work_type not in WORK_TYPES:
        raise ProjectError("work_type must be serial_novel or short_story")
    target_words = getattr(args, "target_words", None)
    if target_words is not None and (
        not isinstance(target_words, int)
        or isinstance(target_words, bool)
        or target_words < 1
    ):
        raise ProjectError("target_words must be a positive integer when provided")

    manifest_path = root / "novel.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if manifest.get("title") != title:
            raise ProjectError(
                "An initialized project already exists with a different title."
            )
        existing_work_type = work_type_for_manifest(manifest)
        if existing_work_type != work_type:
            raise ProjectError(
                "An initialized project already exists with a different work_type."
            )
        # A matching manifest is not enough to claim idempotent success.  An
        # interrupted/hand-edited project must be routed to upgrade or repair,
        # never silently treated as a complete initialization.
        errors, _ = collect_validation(root)
        if errors:
            raise ProjectError(
                "Project manifest exists but the project is incomplete or invalid; "
                "run upgrade/repair instead of re-initializing: "
                + "; ".join(errors[:8])
            )
        result = {
            "status": "already_initialized",
            "project_root": str(root),
            "title": title,
        }
        if existing_work_type == "short_story":
            result["work_type"] = existing_work_type
        return result

    if root.exists() and any(root.iterdir()):
        raise ProjectError(
            "Refusing to initialize a non-empty directory without novel.json."
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "title": title,
        "language": args.language,
        "genre": args.genre.strip(),
        "status": "planning",
        "current_volume": 1,
        "current_chapter": 0,
        "pov": "",
        "tense": "",
        "target_words": target_words,
        "periodic_review": {
            "enabled": True,
            "interval_chapters": (
                1 if work_type == "short_story" else novel_review.DEFAULT_INTERVAL
            ),
            "block_next_commit": True,
        },
        "updated_at": utc_now(),
    }
    # Absence of work_type is the legacy serial-novel contract. Only the new
    # mode writes a discriminator, so ordinary long-form project generation
    # remains byte-for-byte compatible apart from timestamps.
    if work_type == "short_story":
        manifest["work_type"] = work_type
    continuity = {
        "schema_version": SCHEMA_VERSION,
        "through_chapter": 0,
        "story_time": "",
        "characters": {},
        "open_threads": [],
    }

    # Build the complete scaffold in a sibling directory and publish it with
    # one directory rename.  This keeps an injected failure, Ctrl+C, or a
    # process crash from exposing a half-initialized project at the requested
    # path.  A pre-existing empty directory is removed only immediately before
    # the final rename; a non-empty directory is never touched.
    parent = root.parent
    if root.is_symlink():
        raise ProjectError("Cannot initialize through a symbolic-link project path")
    parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.init-", dir=str(parent))
    )
    published = False
    try:
        for relative_dir in REQUIRED_DIRS:
            (temp_root / relative_dir).mkdir(parents=True, exist_ok=True)

        created: list[str] = []
        write_new(temp_root / "novel.json", dump_json(manifest))
        created.append("novel.json")
        write_new(temp_root / "continuity/state.json", dump_json(continuity))
        created.append("continuity/state.json")
        for relative_path, content in markdown_templates(title, work_type).items():
            write_new(temp_root / relative_path, content)
            created.append(relative_path)

        # Use the internal bootstrap builder so no registry identity is
        # needed for a directory that has not been published yet.
        continuity_install = novel_continuity._install_project(temp_root)
        # The scaffold is built under a private sibling directory.  Do not
        # expose that implementation path in the result after the directory
        # is published at the caller-requested root.
        continuity_install["project_root"] = str(root)
        created.extend(continuity_install["created_files"])
        validation_errors, _ = collect_validation(temp_root)
        if validation_errors:
            raise ProjectError(
                "Generated project failed pre-publish validation: "
                + "; ".join(validation_errors[:8])
            )

        if root.exists():
            if not root.is_dir() or any(root.iterdir()):
                raise ProjectError(
                    "Project path became non-empty during initialization; refusing to replace it"
                )
            root.rmdir()
        os.replace(temp_root, root)
        _fsync_directory(parent)
        published = True
    except BaseException:
        raise
    finally:
        if not published and temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)

    result = {
        "status": "created",
        "project_root": str(root),
        "title": title,
        "created_files": sorted(created),
        "continuity": continuity_install,
    }
    if work_type == "short_story":
        result["work_type"] = work_type
    return result


def upgrade_project(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    with project_write_context(root, args) as context:
        result = _upgrade_project(args, root)
        context.assert_live()
        context.refresh_base_after_write("project upgrade")
    # The committed marker remains until the SQLite base hash has committed.
    # A crash before this point leaves enough evidence for write_guard to
    # reconcile the completed upgrade on the next controlled operation.
    if _upgrade_transaction_root(root).exists():
        recover_pending_upgrade(root)
    return result


def _copy_project_for_upgrade(root: Path) -> Path:
    _assert_tree_has_no_links(root)
    temp_root = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.upgrade-", dir=str(root.parent))
    )

    def ignore(directory: str, names: list[str]) -> set[str]:
        current = Path(directory)
        relative = current.relative_to(root) if current != root else Path()
        ignored: set[str] = set()
        for name in names:
            candidate = relative / name
            if _upgrade_should_skip(candidate):
                ignored.add(name)
        return ignored

    try:
        shutil.copytree(
            root,
            temp_root,
            dirs_exist_ok=True,
            symlinks=False,
            ignore=ignore,
        )
    except BaseException:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    return temp_root


def _upgrade_project(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    recovery = recover_pending_upgrade(root)
    recover_pending_transactions(root)
    _assert_tree_has_no_links(root)
    manifest = read_json(root / "novel.json")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ProjectError("novel.json title must be a non-empty string")
    work_type = work_type_for_manifest(manifest)
    existing_files = _upgrade_file_snapshot(root)
    existing_dirs = _upgrade_dir_snapshot(root)
    temp_root = _copy_project_for_upgrade(root)
    try:
        templates = markdown_templates(title, work_type)
        for relative_dir in REQUIRED_DIRS:
            path = temp_root / relative_dir
            if not path.exists():
                path.mkdir(parents=True, exist_ok=False)
            elif _link_like(path) or not path.is_dir():
                raise ProjectError(f"Expected a regular directory: {path}")
        for relative_file, content in templates.items():
            path = temp_root / relative_file
            if not path.exists():
                write_new(path, content)
            elif _link_like(path) or not path.is_file():
                raise ProjectError(f"Expected a regular file: {path}")

        try:
            continuity_install = novel_continuity._install_project(temp_root)
        except novel_continuity.ContinuityError as exc:
            raise ProjectError(str(exc)) from exc

        validation_errors, _ = collect_validation(temp_root)
        if validation_errors:
            raise ProjectError(
                "Upgraded project failed pre-install validation: "
                + "; ".join(validation_errors[:8])
            )
        candidate_files = _upgrade_file_snapshot(temp_root)
        candidate_dirs = _upgrade_dir_snapshot(temp_root)
        changed_existing = [
            relative
            for relative, expected in existing_files.items()
            if candidate_files.get(relative) != expected
        ]
        if changed_existing:
            raise ProjectError(
                "Upgrade attempted to modify existing files: "
                + ", ".join(changed_existing[:8])
            )
        created_files = sorted(set(candidate_files) - set(existing_files))
        created_dirs = sorted(
            set(candidate_dirs) - set(existing_dirs),
            key=lambda value: (value.count("/"), value),
        )
        # ``staging/`` is intentionally excluded from the state-file snapshot,
        # but its required directory shape is still part of the project
        # contract and must be restored by an upgrade.
        required_missing_dirs = {
            relative_dir
            for relative_dir in REQUIRED_DIRS
            if (temp_root / relative_dir).is_dir()
            and not (root / relative_dir).exists()
        }
        expanded_missing_dirs = set(required_missing_dirs)
        for relative_dir in tuple(required_missing_dirs):
            parts = relative_dir.split("/")
            for index in range(1, len(parts)):
                parent_relative = "/".join(parts[:index])
                if (
                    (temp_root / parent_relative).is_dir()
                    and not (root / parent_relative).exists()
                ):
                    expanded_missing_dirs.add(parent_relative)
        created_dirs = sorted(
            set(created_dirs) | expanded_missing_dirs,
            key=lambda value: (value.count("/"), value),
        )
        writes = [
            (root / relative, (temp_root / relative).read_bytes())
            for relative in created_files
        ]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    continuity_install["project_root"] = str(root)

    if not created_dirs and not created_files:
        chapters = chapter_files(root)
        index_text = (root / "manuscript/index.md").read_text(encoding="utf-8")
        index_targets = markdown_link_targets(index_text)
        backfill = []
        for chapter_path in chapters:
            match = CHAPTER_NAME.fullmatch(chapter_path.name)
            if match is None:
                continue
            relative = chapter_path.relative_to(root / "manuscript").as_posix()
            card_path = root / "memory/chapters" / f"{match.group('number')}.md"
            card_valid = (
                card_path.is_file()
                and chapter_path.name in card_path.read_text(encoding="utf-8")
            )
            if relative not in index_targets or not card_valid:
                backfill.append(chapter_path.name)
        return {
            "status": "already_current",
            "project_root": str(root),
            "created_directories": [],
            "created_files": [],
            "chapters_requiring_memory_backfill": backfill,
            "continuity": continuity_install,
            "recovery": recovery,
        }

    _upgrade_assert_existing_snapshot(root, existing_files)
    for relative in created_files:
        target = root / relative
        if target.exists() or _contains_symlink(target):
            raise ProjectError(f"Upgrade target appeared concurrently: {relative}")
    journal = {
        "schema_version": UPGRADE_TRANSACTION_SCHEMA_VERSION,
        "project_root": str(root),
        "status": "prepared",
        "created_at": utc_now(),
        "created_dirs": created_dirs,
        "files": [
            {
                "path": relative,
                "sha256": candidate_files[relative]["sha256"],
                "size_bytes": candidate_files[relative]["size_bytes"],
            }
            for relative in created_files
        ],
        "existing_files": [
            {"path": relative, **metadata}
            for relative, metadata in sorted(existing_files.items())
        ],
    }
    _write_upgrade_journal(root, journal)
    journal["status"] = "applying"
    _write_upgrade_journal(root, journal)

    for relative in created_dirs:
        path = root / relative
        if path.exists():
            if _contains_symlink(path) or not path.is_dir():
                raise ProjectError(f"Upgrade directory appeared with the wrong type: {relative}")
            continue
        path.mkdir(exist_ok=False)

    def validate_upgrade() -> None:
        _upgrade_assert_expected_snapshot(
            root,
            existing_files,
            journal["files"],
            require_all_created=True,
        )
        errors, _ = collect_validation(root, check_transactions=False)
        if errors:
            raise ProjectError("Post-upgrade validation failed: " + "; ".join(errors[:8]))

    if writes:
        transactional_write(writes, validator=validate_upgrade, journal_root=root)
    else:
        validate_upgrade()
    journal["status"] = "committed"
    journal["committed_at"] = utc_now()
    _write_upgrade_journal(root, journal)

    chapters = chapter_files(root)
    index_text = (root / "manuscript/index.md").read_text(encoding="utf-8")
    index_targets = markdown_link_targets(index_text)
    backfill: list[str] = []
    for chapter_path in chapters:
        match = CHAPTER_NAME.fullmatch(chapter_path.name)
        if match is None:
            continue
        relative = chapter_path.relative_to(root / "manuscript").as_posix()
        card_path = root / "memory/chapters" / f"{match.group('number')}.md"
        card_valid = (
            card_path.is_file()
            and chapter_path.name in card_path.read_text(encoding="utf-8")
        )
        if relative not in index_targets or not card_valid:
            backfill.append(chapter_path.name)

    return {
        "status": "upgraded",
        "project_root": str(root),
        "created_directories": sorted(created_dirs),
        "created_files": sorted(created_files),
        "chapters_requiring_memory_backfill": backfill,
        "continuity": continuity_install,
        "recovery": recovery,
    }


def source_manifest_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        import novel_research

        return novel_research.read_manifest(path.parent.parent)
    except (ImportError, OSError, UnicodeError) as exc:
        raise ProjectError(f"Unable to read source manifest: {exc}") from exc
    except Exception as exc:
        # Keep the project validator's public error type stable while using
        # the single strict source-manifest parser.
        if exc.__class__.__name__ == "ResearchError":
            raise ProjectError(str(exc)) from exc
        raise


def collect_v2_research_validation(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    platform_path = root / "research/platform.json"
    if platform_path.is_file():
        try:
            platform = read_json(platform_path)
        except ProjectError as exc:
            errors.append(str(exc))
        else:
            if platform.get("schema_version") != SCHEMA_VERSION:
                errors.append("research/platform.json has an unsupported schema_version")
            if not isinstance(platform.get("adapters"), dict):
                errors.append("research/platform.json adapters must be an object")

    originality_path = root / "research/originality-plan.json"
    if originality_path.is_file():
        try:
            originality = read_json(originality_path)
        except ProjectError as exc:
            errors.append(str(exc))
        else:
            if originality.get("schema_version") != SCHEMA_VERSION:
                errors.append(
                    "research/originality-plan.json has an unsupported schema_version"
                )
            if not isinstance(originality.get("candidate"), dict):
                errors.append("research/originality-plan.json candidate must be an object")
            if not isinstance(originality.get("references"), list):
                errors.append("research/originality-plan.json references must be an array")

    manifest_path = root / "research/source-manifest.jsonl"
    try:
        records = source_manifest_records(manifest_path)
    except ProjectError as exc:
        errors.append(str(exc))
        records = []
    try:
        import novel_research

        source_errors, source_warnings = novel_research.validate_manifest_records(
            root, records
        )
        errors.extend(source_errors)
        warnings.extend(source_warnings)
    except (ImportError, OSError) as exc:
        errors.append(f"Unable to load source-manifest validator: {exc}")
    return errors, warnings


def collect_validation(
    root: Path, *, check_transactions: bool = True
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not root.is_dir():
        return [f"Project directory does not exist: {root}"], warnings

    if check_transactions:
        try:
            assert_no_pending_transactions(root)
        except ProjectError as exc:
            errors.append(str(exc))

    for relative_dir in REQUIRED_DIRS:
        if not (root / relative_dir).is_dir():
            errors.append(f"Missing directory: {relative_dir}")
    for relative_file in REQUIRED_FILES:
        if not (root / relative_file).is_file():
            errors.append(f"Missing file: {relative_file}")

    v2_errors, v2_warnings = collect_v2_research_validation(root)
    errors.extend(v2_errors)
    warnings.extend(v2_warnings)

    manifest: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    try:
        manifest = read_json(root / "novel.json")
    except ProjectError as exc:
        errors.append(str(exc))
    try:
        state = read_json(root / "continuity/state.json")
    except ProjectError as exc:
        errors.append(str(exc))

    if manifest is not None:
        if manifest.get("schema_version") != SCHEMA_VERSION:
            errors.append("novel.json has an unsupported schema_version")
        if not isinstance(manifest.get("title"), str) or not manifest["title"].strip():
            errors.append("novel.json title must be a non-empty string")
        try:
            work_type_for_manifest(manifest)
        except ProjectError as exc:
            errors.append(str(exc))
        target_words = manifest.get("target_words")
        if target_words is not None and (
            not isinstance(target_words, int)
            or isinstance(target_words, bool)
            or target_words < 1
        ):
            errors.append("novel.json target_words must be null or a positive integer")
        chapter = manifest.get("current_chapter")
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 0:
            errors.append("novel.json current_chapter must be a non-negative integer")

    if state is not None:
        if state.get("schema_version") != SCHEMA_VERSION:
            errors.append("continuity/state.json has an unsupported schema_version")
        through = state.get("through_chapter")
        if not isinstance(through, int) or isinstance(through, bool) or through < 0:
            errors.append(
                "continuity/state.json through_chapter must be a non-negative integer"
            )
        if not isinstance(state.get("characters"), dict):
            errors.append("continuity/state.json characters must be an object")
        if not isinstance(state.get("open_threads"), list):
            errors.append("continuity/state.json open_threads must be an array")

    framework_path = root / "planning/framework-session.md"
    if framework_path.is_file():
        try:
            framework = frontmatter_metadata(framework_path)
        except ProjectError as exc:
            errors.append(str(exc))
        else:
            if framework.get("schema_version") != str(SCHEMA_VERSION):
                errors.append(
                    "planning/framework-session.md has an unsupported schema_version"
                )
            stage = framework.get("stage")
            confirmation = framework.get("confirmation")
            if stage not in FRAMEWORK_STAGES:
                errors.append(
                    "planning/framework-session.md stage must be one of: "
                    + ", ".join(sorted(FRAMEWORK_STAGES))
                )
            if confirmation not in FRAMEWORK_CONFIRMATIONS:
                errors.append(
                    "planning/framework-session.md confirmation must be pending "
                    "or confirmed"
                )
            if (stage == "complete") != (confirmation == "confirmed"):
                errors.append(
                    "planning/framework-session.md must pair stage=complete with "
                    "confirmation=confirmed"
                )
            if not framework.get("updated_at"):
                errors.append(
                    "planning/framework-session.md updated_at must not be empty"
                )

            confidence_values: dict[str, int] = {}
            for key in ("requirements_confidence", "story_confidence"):
                raw_value = framework.get(key)
                try:
                    value = int(raw_value) if raw_value is not None else -1
                except ValueError:
                    value = -1
                if value < 0 or value > 100:
                    errors.append(
                        f"planning/framework-session.md {key} must be an "
                        "integer from 0 to 100"
                    )
                else:
                    confidence_values[key] = value

            if confirmation == "confirmed" and any(
                confidence_values.get(key, 0) < 95
                for key in ("requirements_confidence", "story_confidence")
            ):
                errors.append(
                    "planning/framework-session.md cannot be confirmed until "
                    "both confidence values are at least 95"
                )

    comparables_path = root / "research/comparable-works.md"
    if comparables_path.is_file():
        try:
            comparables = frontmatter_metadata(comparables_path)
        except ProjectError as exc:
            errors.append(str(exc))
        else:
            if comparables.get("schema_version") != str(SCHEMA_VERSION):
                errors.append(
                    "research/comparable-works.md has an unsupported schema_version"
                )
            approval = comparables.get("candidate_approval")
            deep_analysis = comparables.get("deep_analysis")
            if approval not in CANDIDATE_APPROVALS:
                errors.append(
                    "research/comparable-works.md candidate_approval must be "
                    "pending, approved, or revision_requested"
                )
            if deep_analysis not in DEEP_ANALYSIS_STAGES:
                errors.append(
                    "research/comparable-works.md deep_analysis must be "
                    "not_started, in_progress, or complete"
                )
            if deep_analysis in {"in_progress", "complete"} and approval != "approved":
                errors.append(
                    "research/comparable-works.md cannot start deep analysis "
                    "before candidate approval"
                )
            if not comparables.get("updated_at"):
                errors.append(
                    "research/comparable-works.md updated_at must not be empty"
                )

    chapters = chapter_files(root)
    chapter_numbers: dict[str, Path] = {}
    index_path = root / "manuscript/index.md"
    index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    index_targets = markdown_link_targets(index_text)
    indexed_titles = index_titles(index_text)
    expected_cards: set[str] = set()
    expected_index_targets: set[str] = set()
    short_story = bool(
        manifest is not None and manifest.get("work_type", "serial_novel") == "short_story"
    )

    for chapter_path in chapters:
        match = CHAPTER_NAME.fullmatch(chapter_path.name)
        if match is None:
            continue
        number = match.group("number")
        if number in chapter_numbers:
            errors.append(
                "Duplicate chapter number "
                f"{number}: {chapter_numbers[number]} and {chapter_path}"
            )
        else:
            chapter_numbers[number] = chapter_path

        heading = chapter_heading(chapter_path, short_story=short_story)
        if heading is None:
            errors.append(
                f"Chapter must start with a valid level-one title: {chapter_path.relative_to(root).as_posix()}"
            )
        else:
            heading_number, heading_title = heading
            if heading_number != int(number):
                errors.append(
                    f"Chapter heading number does not match filename {number}: "
                    f"{chapter_path.relative_to(root).as_posix()}"
                )
            indexed_title = indexed_titles.get(number)
            if indexed_title is not None and heading_title != indexed_title:
                errors.append(
                    f"Chapter heading title does not match manuscript/index.md for {number}: "
                    f"heading={heading_title!r}, index={indexed_title!r}"
                )

        relative = chapter_path.relative_to(root / "manuscript").as_posix()
        expected_index_targets.add(relative)
        link_count = index_targets.count(relative)
        if link_count == 0:
            errors.append(
                f"Chapter is missing from manuscript/index.md: {relative}"
            )
        elif link_count > 1:
            errors.append(
                f"Chapter has duplicate links in manuscript/index.md: {relative}"
            )

        card_name = f"{number}.md"
        expected_cards.add(card_name.lower())
        card_path = root / "memory/chapters" / card_name
        if not card_path.is_file():
            errors.append(f"Missing chapter memory card: memory/chapters/{card_name}")
        elif chapter_path.name not in card_path.read_text(encoding="utf-8"):
            errors.append(
                f"Chapter memory card does not link to its manuscript: "
                f"memory/chapters/{card_name}"
            )

    for target in index_targets:
        if target not in expected_index_targets:
            errors.append(f"Index points to a missing or non-chapter file: {target}")

    committed_chapter = max((int(number) for number in chapter_numbers), default=0)
    if (
        manifest is not None
        and manifest.get("work_type", "serial_novel") == "short_story"
        and committed_chapter > 1
    ):
        errors.append(
            "short_story projects must keep the complete canonical manuscript "
            "in exactly one indexed Markdown unit"
        )
    if manifest is not None and manifest.get("current_chapter") != committed_chapter:
        errors.append(
            "novel.json current_chapter does not match the highest chapter file: "
            f"expected {committed_chapter}"
        )
    if state is not None and state.get("through_chapter") != committed_chapter:
        errors.append(
            "continuity/state.json through_chapter does not match the highest "
            f"chapter file: expected {committed_chapter}"
        )

    memory_dir = root / "memory/chapters"
    if memory_dir.is_dir():
        for card in memory_dir.glob("*.md"):
            if card.name.lower() not in expected_cards:
                warnings.append(f"Orphan chapter memory card: memory/chapters/{card.name}")

    if (root / "novel.json").is_file():
        review_errors, review_warnings = novel_review.collect_review_validation(root)
        errors.extend(review_errors)
        warnings.extend(review_warnings)
        try:
            continuity_errors, continuity_warnings = novel_continuity.collect_validation(root)
        except novel_continuity.ContinuityError as exc:
            continuity_errors, continuity_warnings = [str(exc)], []
        errors.extend(continuity_errors)
        warnings.extend(continuity_warnings)

    return errors, warnings


def clean_authorization_reference(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ProjectError(
            "A short --authorization-reference is required for controlled state changes"
        )
    if len(cleaned) > 240:
        raise ProjectError("--authorization-reference must not exceed 240 characters")
    return cleaned


def _research_state(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    path = root / "research/comparable-works.md"
    current = frontmatter_metadata(path)
    approval = args.candidate_approval or current.get("candidate_approval")
    deep_analysis = args.deep_analysis or current.get("deep_analysis")
    if approval not in CANDIDATE_APPROVALS:
        raise ProjectError("Invalid candidate approval state")
    if deep_analysis not in DEEP_ANALYSIS_STAGES:
        raise ProjectError("Invalid deep-analysis state")
    if deep_analysis in {"in_progress", "complete"} and approval != "approved":
        raise ProjectError("Deep analysis cannot start before candidate approval")
    if args.candidate_approval is None and args.deep_analysis is None:
        raise ProjectError("Specify at least one research state field")
    authorization = clean_authorization_reference(args.authorization_reference)
    updates: dict[str, Any] = {
        "candidate_approval": approval,
        "deep_analysis": deep_analysis,
        "updated_at": utc_now(),
    }
    if args.candidate_approval is not None:
        updates["candidate_authorization"] = authorization
    if args.deep_analysis is not None:
        updates["analysis_authorization"] = authorization
    original = path.read_text(encoding="utf-8")
    updated = replace_frontmatter(original, updates)
    continuity_result: dict[str, Any] | None = None
    try:
        if read_json(root / "novel.json").get("current_chapter") == 0:
            continuity_result = novel_continuity.reseal_zero_baseline(
                root, authorization, extra_writes=[(path, updated.encode("utf-8"))]
            )
        else:
            transactional_write([(path, updated.encode("utf-8"))], journal_root=root)
    except novel_continuity.ContinuityError as exc:
        raise ProjectError(str(exc)) from exc
    return {
        "status": "updated" if updated != original else "already_current",
        "project_root": str(root),
        "before": {
            "candidate_approval": current.get("candidate_approval"),
            "deep_analysis": current.get("deep_analysis"),
        },
        "after": {
            "candidate_approval": approval,
            "deep_analysis": deep_analysis,
        },
        "authorization_reference": authorization,
        "continuity": continuity_result,
    }


def research_state(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    try:
        with project_write_context(root, args) as context:
            result = _research_state(args, root)
            context.assert_live()
            context.refresh_base_after_write("research state update")
            return result
    except (OSError, sqlite3.Error) as exc:
        raise ProjectError(f"Project write authorization failed: {exc}") from exc


def _framework_values(
    args: argparse.Namespace,
    current: dict[str, str],
    *,
    require_explicit_state: bool = True,
) -> tuple[str | None, str | None, int, int]:
    """Resolve a framework transition from CLI values and current metadata."""

    stage_arg = getattr(args, "stage", None)
    confirmation_arg = getattr(args, "confirmation", None)
    requirements_arg = getattr(args, "requirements_confidence", None)
    story_arg = getattr(args, "story_confidence", None)
    if require_explicit_state and all(
        value is None
        for value in (stage_arg, confirmation_arg, requirements_arg, story_arg)
    ):
        raise ProjectError("Specify at least one framework state field")
    stage = stage_arg or current.get("stage")
    confirmation = confirmation_arg or current.get("confirmation")
    try:
        requirements_confidence = (
            requirements_arg
            if requirements_arg is not None
            else int(current.get("requirements_confidence", "-1"))
        )
        story_confidence = (
            story_arg
            if story_arg is not None
            else int(current.get("story_confidence", "-1"))
        )
    except (TypeError, ValueError) as exc:
        raise ProjectError("Current framework confidence is not an integer") from exc
    return stage, confirmation, requirements_confidence, story_confidence


def _validate_framework_transition(
    root: Path,
    *,
    stage: str | None,
    confirmation: str | None,
    requirements_confidence: int,
    story_confidence: int,
    canonical_texts: dict[str, str] | None = None,
) -> None:
    if stage not in FRAMEWORK_STAGES:
        raise ProjectError("Invalid framework stage")
    if confirmation not in FRAMEWORK_CONFIRMATIONS:
        raise ProjectError("Invalid framework confirmation")
    if not 0 <= requirements_confidence <= 100 or not 0 <= story_confidence <= 100:
        raise ProjectError("Framework confidence values must be from 0 to 100")
    if (stage == "complete") != (confirmation == "confirmed"):
        raise ProjectError("stage=complete must be paired with confirmation=confirmed")
    if confirmation == "confirmed":
        if requirements_confidence < 95 or story_confidence < 95:
            raise ProjectError("Both confidence values must be at least 95 to confirm")
        if canonical_texts is None:
            canonical_texts = {
                relative: (root / relative).read_text(encoding="utf-8")
                for relative in FRAMEWORK_CANONICAL_FILES
            }
        unresolved: list[str] = []
        for relative, text in canonical_texts.items():
            if not text.strip() or any(
                marker in text for marker in ("[待作者确认]", "[待确认]", "[待补充]")
            ):
                unresolved.append(relative)
        if unresolved:
            raise ProjectError(
                "Cannot confirm before canonical files are synchronized: "
                + ", ".join(unresolved)
            )


def _framework_updates(
    args: argparse.Namespace,
    *,
    stage: str,
    confirmation: str,
    requirements_confidence: int,
    story_confidence: int,
) -> tuple[dict[str, Any], str]:
    authorization = clean_authorization_reference(args.authorization_reference)
    return (
        {
            "stage": stage,
            "confirmation": confirmation,
            "requirements_confidence": requirements_confidence,
            "story_confidence": story_confidence,
            "updated_at": utc_now(),
            "state_authorization": authorization,
        },
        authorization,
    )


def _framework_state(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    path = root / "planning/framework-session.md"
    current = frontmatter_metadata(path)
    stage, confirmation, requirements_confidence, story_confidence = _framework_values(
        args, current
    )
    _validate_framework_transition(
        root,
        stage=stage,
        confirmation=confirmation,
        requirements_confidence=requirements_confidence,
        story_confidence=story_confidence,
    )
    updates, authorization = _framework_updates(
        args,
        stage=stage,
        confirmation=confirmation,
        requirements_confidence=requirements_confidence,
        story_confidence=story_confidence,
    )
    original = path.read_text(encoding="utf-8")
    updated = replace_frontmatter(original, updates)
    continuity_result: dict[str, Any] | None = None
    try:
        if read_json(root / "novel.json").get("current_chapter") == 0:
            continuity_result = novel_continuity.reseal_zero_baseline(
                root, authorization, extra_writes=[(path, updated.encode("utf-8"))]
            )
        else:
            transactional_write([(path, updated.encode("utf-8"))], journal_root=root)
    except novel_continuity.ContinuityError as exc:
        raise ProjectError(str(exc)) from exc
    return {
        "status": "updated" if updated != original else "already_current",
        "project_root": str(root),
        "before": {
            "stage": current.get("stage"),
            "confirmation": current.get("confirmation"),
            "requirements_confidence": current.get("requirements_confidence"),
            "story_confidence": current.get("story_confidence"),
        },
        "after": {
            "stage": stage,
            "confirmation": confirmation,
            "requirements_confidence": requirements_confidence,
            "story_confidence": story_confidence,
        },
        "authorization_reference": authorization,
        "continuity": continuity_result,
    }


def _resolve_framework_sync_root(
    raw_source: str | Path,
    project_root: Path,
    work_root: Path,
) -> Path:
    raw = Path(raw_source).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    if _contains_symlink(raw):
        raise ProjectError(
            "Framework sync source cannot traverse a symbolic link or reparse point"
        )
    source = raw.resolve()
    if is_within(source, project_root):
        raise ProjectError(
            "Framework sync source must be outside the project tree; keep it in work-root"
        )
    if not is_within(source, work_root):
        raise ProjectError(
            "Framework sync source must be inside the active work directory"
        )
    if not source.is_dir():
        raise ProjectError(f"Framework sync source is not a directory: {source}")
    _assert_tree_has_no_links(source)
    return source


def _read_framework_sync_files(
    source: Path,
) -> tuple[dict[str, bytes], dict[Path, str]]:
    contents: dict[str, bytes] = {}
    preconditions: dict[Path, str] = {}
    for relative in FRAMEWORK_SYNC_SOURCE_FILES:
        path = source / relative
        if _contains_symlink(path) or not path.is_file():
            raise ProjectError(
                f"Framework sync source is missing a regular file: {relative}"
            )
        content = read_stable_bytes(path, label=f"framework sync source {relative}")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectError(
                f"Framework sync source is not UTF-8: {relative}"
            ) from exc
        resolved = path.resolve()
        contents[relative] = content
        preconditions[resolved] = sha256_bytes(content)
    return contents, preconditions


def _framework_manifest_bytes(
    raw_settings: bytes,
    current_manifest: dict[str, Any],
    *,
    confirmation: str,
) -> bytes:
    try:
        settings = json.loads(raw_settings.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(
            f"Framework sync {FRAMEWORK_SETTINGS_FILE} must be valid UTF-8 JSON"
        ) from exc
    if not isinstance(settings, dict):
        raise ProjectError(
            f"Framework sync {FRAMEWORK_SETTINGS_FILE} must be a JSON object"
        )
    required = {"schema_version", "pov", "tense", "target_words"}
    missing = sorted(required - set(settings))
    unknown = sorted(set(settings) - required)
    if missing:
        raise ProjectError(
            f"Framework sync {FRAMEWORK_SETTINGS_FILE} is missing: "
            + ", ".join(missing)
        )
    if unknown:
        raise ProjectError(
            f"Framework sync {FRAMEWORK_SETTINGS_FILE} contains protected or unknown "
            "fields: "
            + ", ".join(unknown)
        )
    if type(settings.get("schema_version")) is not int or settings[
        "schema_version"
    ] != SCHEMA_VERSION:
        raise ProjectError(
            f"Framework sync {FRAMEWORK_SETTINGS_FILE} has an unsupported schema_version"
        )
    for key in ("pov", "tense"):
        value = settings.get(key)
        if (
            not isinstance(value, str)
            or value != value.strip()
            or "\x00" in value
            or "\r" in value
            or "\n" in value
            or len(value) > 200
        ):
            raise ProjectError(
                f"Framework sync {FRAMEWORK_SETTINGS_FILE} {key} must be a concise string"
            )
        if confirmation == "confirmed" and not value:
            raise ProjectError(
                f"Confirmed framework requires a non-empty {key} in "
                f"{FRAMEWORK_SETTINGS_FILE}"
            )
    target_words = settings.get("target_words")
    if target_words is not None and (
        type(target_words) is not int or target_words < 1
    ):
        raise ProjectError(
            f"Framework sync {FRAMEWORK_SETTINGS_FILE} target_words must be null "
            "or a positive integer"
        )

    updated = dict(current_manifest)
    for key in ("pov", "tense", "target_words"):
        updated[key] = settings[key]
    if confirmation == "confirmed" and updated.get("status") == "planning":
        updated["status"] = "drafting"
    if updated != current_manifest:
        updated["updated_at"] = utc_now()
    return dump_json(updated).encode("utf-8")


def _framework_sync(
    args: argparse.Namespace,
    root: Path,
    work_root: Path,
) -> dict[str, Any]:
    source = _resolve_framework_sync_root(args.source_root, root, work_root)
    source_contents, source_preconditions = _read_framework_sync_files(source)
    source_session = source_contents["planning/framework-session.md"].decode("utf-8")
    source_metadata = parse_frontmatter_metadata(
        source_session, label=str(source / "planning/framework-session.md")
    )
    current_session_path = root / "planning/framework-session.md"
    current_session = frontmatter_metadata(current_session_path)
    # The package is the source of the complete session body; CLI values are
    # optional overrides for the controlled state transition.
    state_args = argparse.Namespace(
        stage=getattr(args, "stage", None),
        confirmation=getattr(args, "confirmation", None),
        requirements_confidence=getattr(args, "requirements_confidence", None),
        story_confidence=getattr(args, "story_confidence", None),
    )
    package_stage, package_confirmation, package_requirements, package_story = (
        _framework_values(state_args, source_metadata, require_explicit_state=False)
    )
    # A package with no state metadata may inherit the currently recorded
    # state, but it must still carry a valid frontmatter version and transition.
    if source_metadata.get("schema_version") != str(SCHEMA_VERSION):
        raise ProjectError(
            "Framework sync source session has an unsupported schema_version"
        )
    if package_stage is None:
        package_stage = current_session.get("stage")
    if package_confirmation is None:
        package_confirmation = current_session.get("confirmation")
    if source_metadata.get("requirements_confidence") is None and getattr(
        args, "requirements_confidence", None
    ) is None:
        package_requirements = int(current_session.get("requirements_confidence", "-1"))
    if source_metadata.get("story_confidence") is None and getattr(
        args, "story_confidence", None
    ) is None:
        package_story = int(current_session.get("story_confidence", "-1"))
    _validate_framework_transition(
        root,
        stage=package_stage,
        confirmation=package_confirmation,
        requirements_confidence=package_requirements,
        story_confidence=package_story,
        canonical_texts={
            relative: source_contents[relative].decode("utf-8")
            for relative in FRAMEWORK_SYNC_FILES
        },
    )
    updates, authorization = _framework_updates(
        args,
        stage=package_stage,
        confirmation=package_confirmation,
        requirements_confidence=package_requirements,
        story_confidence=package_story,
    )
    updated_session = replace_frontmatter(source_session, updates).encode("utf-8")

    writes: list[tuple[Path, bytes]] = []
    target_preconditions: dict[Path, str | None] = {}
    changed_targets = False
    changed_semantic_paths: set[str] = set()
    for relative in (*FRAMEWORK_CANONICAL_FILES, *FRAMEWORK_MEMORY_FILES):
        target = root / relative
        if _contains_symlink(target) or not target.is_file():
            raise ProjectError(f"Project framework target is missing: {relative}")
        previous = read_stable_bytes(target, label=f"framework target {relative}")
        target_preconditions[target.resolve()] = sha256_bytes(previous)
        if previous != source_contents[relative]:
            changed_targets = True
            changed_semantic_paths.add(relative)
        writes.append((target, source_contents[relative]))
    previous_session = read_stable_bytes(
        current_session_path, label="current framework session"
    )
    comparison_updates = dict(updates)
    comparison_updates["updated_at"] = current_session.get(
        "updated_at", updates["updated_at"]
    )
    comparison_updates["state_authorization"] = current_session.get(
        "state_authorization", authorization
    )
    comparison_session = replace_frontmatter(
        source_session, comparison_updates
    ).encode("utf-8")
    if comparison_session == previous_session:
        updated_session = previous_session
    else:
        changed_semantic_paths.add("planning/framework-session.md")
    target_preconditions[current_session_path.resolve()] = sha256_bytes(previous_session)
    changed_targets = changed_targets or previous_session != updated_session
    writes.append((current_session_path, updated_session))

    manifest_path = root / "novel.json"
    previous_manifest = read_stable_bytes(manifest_path, label="current novel.json")
    try:
        current_manifest = json.loads(previous_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError("Current novel.json must be valid UTF-8 JSON") from exc
    if not isinstance(current_manifest, dict):
        raise ProjectError("Current novel.json must be a JSON object")
    current_chapter = current_manifest.get("current_chapter")
    if (
        type(current_chapter) is not int
        or current_chapter < 0
    ):
        raise ProjectError("novel.json current_chapter must be a non-negative integer")
    updated_manifest = _framework_manifest_bytes(
        source_contents[FRAMEWORK_SETTINGS_FILE],
        current_manifest,
        confirmation=package_confirmation,
    )
    updated_manifest_data = json.loads(updated_manifest.decode("utf-8"))
    manifest_semantic_changed = updated_manifest_data != current_manifest
    if not manifest_semantic_changed:
        updated_manifest = previous_manifest
    else:
        changed_semantic_paths.add("novel.json")
    target_preconditions[manifest_path.resolve()] = sha256_bytes(previous_manifest)
    changed_targets = changed_targets or previous_manifest != updated_manifest
    writes.append((manifest_path, updated_manifest))
    if current_chapter > 0 and changed_targets:
        try:
            open_invalidations = novel_continuity.unresolved_invalidations(root)
        except novel_continuity.ContinuityError as exc:
            raise ProjectError(str(exc)) from exc
        covered_paths = {
            str(path)
            for item in open_invalidations
            for path in item.get("changed_paths", [])
            if isinstance(path, str)
        }
        uncovered_paths = sorted(changed_semantic_paths - covered_paths)
        if not open_invalidations or uncovered_paths:
            detail = (
                "; missing changed_paths coverage: " + ", ".join(uncovered_paths)
                if uncovered_paths
                else ""
            )
            raise ProjectError(
                "Framework content changes after the first chapter require an open "
                "continuity invalidation that covers every changed canonical path; "
                "run impact, then invalidate, before sync"
                + detail
            )

    current_before = {
        "stage": current_session.get("stage"),
        "confirmation": current_session.get("confirmation"),
        "requirements_confidence": current_session.get("requirements_confidence"),
        "story_confidence": current_session.get("story_confidence"),
    }
    if not changed_targets:
        return {
            "status": "already_current",
            "project_root": str(root),
            "source_root": str(source),
            "source_files": list(FRAMEWORK_SYNC_SOURCE_FILES),
            "synced_files": [],
            "before": current_before,
            "after": dict(current_before),
            "authorization_reference": authorization,
            "continuity": None,
        }

    try:
        if current_chapter == 0:
            continuity_result = novel_continuity.reseal_zero_baseline(
                root,
                authorization,
                extra_writes=writes,
                expected_targets=target_preconditions,
                expected_existing=source_preconditions,
            )
        else:
            transactional_write(
                writes,
                journal_root=root,
                expected_existing=source_preconditions,
                expected_targets=target_preconditions,
            )
            continuity_result = None
    except novel_continuity.ContinuityError as exc:
        raise ProjectError(str(exc)) from exc
    return {
        "status": "synchronized",
        "project_root": str(root),
        "source_root": str(source),
        "source_files": list(FRAMEWORK_SYNC_SOURCE_FILES),
        "synced_files": [*FRAMEWORK_SYNC_FILES, "novel.json"],
        "before": current_before,
        "after": {
            "stage": package_stage,
            "confirmation": package_confirmation,
            "requirements_confidence": package_requirements,
            "story_confidence": package_story,
        },
        "authorization_reference": authorization,
        "continuity": continuity_result,
    }


def framework_sync(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    try:
        with project_write_context(root, args) as context:
            if context.workspace_root is None or context.guard is None:
                raise ProjectError(
                    "framework-sync requires an active workspace work directory"
                )
            work_root = (
                context.workspace_root / "workspaces" / context.guard.work_id
            ).resolve()
            result = _framework_sync(args, root, work_root)
            context.assert_live()
            context.refresh_base_after_write("framework content and state synchronization")
            return result
    except (OSError, sqlite3.Error) as exc:
        raise ProjectError(f"Project write authorization failed: {exc}") from exc


def framework_state(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    try:
        with project_write_context(root, args) as context:
            result = _framework_state(args, root)
            context.assert_live()
            context.refresh_base_after_write("framework state update")
            return result
    except (OSError, sqlite3.Error) as exc:
        raise ProjectError(f"Project write authorization failed: {exc}") from exc


def markdown_cell(value: Any) -> str:
    if isinstance(value, list):
        text = ",".join(str(item) for item in value)
    else:
        text = str(value or "")
    return " ".join(text.replace("|", "\\|").split())


def resolve_package_file(package: Path, relative: str) -> Path:
    raw_package = Path(package).expanduser()
    if _contains_symlink(raw_package):
        raise ProjectError(f"Staging package cannot traverse a symbolic link: {package}")
    raw_path = raw_package / relative
    if _contains_symlink(raw_path):
        raise ProjectError(f"Staged file cannot traverse a symbolic link: {relative}")
    path = raw_path.resolve()
    if not is_within(path, package):
        raise ProjectError(f"Staging path escapes its package: {relative}")
    if not path.is_file():
        raise ProjectError(f"Missing staged file: {relative}")
    return path


def _package_entries(package: Path) -> tuple[tuple[str, bytes], ...]:
    """Read a complete package tree as stable bytes, rejecting links/special files."""

    if _link_like(package) or not package.is_dir():
        raise ProjectError(f"Chapter package must be a regular directory: {package}")
    entries: list[tuple[str, bytes]] = []
    pending = [package]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ProjectError(f"Unable to enumerate chapter package: {directory}: {exc}") from exc
        for child in children:
            if _link_like(child):
                raise ProjectError(f"Chapter package cannot contain a symbolic link: {child}")
            if child.is_dir():
                pending.append(child)
                continue
            if not child.is_file():
                raise ProjectError(f"Chapter package contains a non-regular file: {child}")
            relative = child.relative_to(package).as_posix()
            try:
                before = child.stat()
                content = child.read_bytes()
                after = child.stat()
            except OSError as exc:
                raise ProjectError(f"Unable to read chapter package file: {child}: {exc}") from exc
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise ProjectError(f"Chapter package file changed while being read: {relative}")
            entries.append((relative, content))
    if not entries:
        raise ProjectError("Chapter package cannot be empty")
    return tuple(sorted(entries, key=lambda item: item[0]))


def _package_fingerprint(package: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (relative, sha256_bytes(content), len(content))
        for relative, content in _package_entries(package)
    )


def _materialize_package_snapshot(
    package: Path,
) -> tuple[Path, tuple[tuple[str, str, int], ...], tuple[tuple[str, str, int], ...]]:
    """Copy a package to a private sibling directory and return both fingerprints."""

    entries = _package_entries(package)
    original_fingerprint = tuple(
        (relative, sha256_bytes(content), len(content))
        for relative, content in entries
    )
    snapshot = Path(
        tempfile.mkdtemp(prefix=".novel-package-snapshot-", dir=str(package.parent))
    )
    try:
        for relative, content in entries:
            target = snapshot / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        snapshot_fingerprint = _package_fingerprint(snapshot)
    except BaseException:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise
    return snapshot, original_fingerprint, snapshot_fingerprint


def _assert_package_snapshot_unchanged(
    package: Path,
    expected: tuple[tuple[str, str, int], ...],
    snapshot: Path,
    snapshot_expected: tuple[tuple[str, str, int], ...],
) -> None:
    if _package_fingerprint(package) != expected:
        raise ProjectError(
            "Staged chapter package changed during validation; refusing to commit"
        )
    if _package_fingerprint(snapshot) != snapshot_expected:
        raise ProjectError(
            "Private chapter package snapshot changed during validation; refusing to commit"
        )


def validate_originality_report(
    root: Path, report_relative: str, chapter_path: Path
) -> tuple[dict[str, Any], Path, Path, bytes]:
    if not isinstance(report_relative, str) or not report_relative:
        raise ProjectError("Originality report path in commit.json must not be empty")
    if "\\" in report_relative:
        raise ProjectError("Originality report path must use POSIX separators")
    relative_path = Path(report_relative)
    if (
        relative_path.is_absolute()
        or "." in relative_path.parts
        or ".." in relative_path.parts
        or relative_path.as_posix() != report_relative
    ):
        raise ProjectError(
            "Originality report path in commit.json must be a normalized project-relative path"
        )
    raw_report_path = root / relative_path
    if _contains_symlink(raw_report_path):
        raise ProjectError(
            "Originality report path cannot traverse a symbolic link or reparse point"
        )
    report_path = raw_report_path.resolve()
    reviews_root = (root / "reviews").resolve()
    staging_root = (root / "staging").resolve()
    in_reviews = is_within(report_path, reviews_root)
    if not in_reviews and not is_within(report_path, staging_root):
        raise ProjectError(
            "Originality report must be under the project's staging or reviews directory"
        )
    report_bytes = read_stable_bytes(report_path, label="Originality report")
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Originality report is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise ProjectError("Originality report must be a JSON object")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ProjectError("Originality report has an unsupported schema_version")
    if report.get("decision") != "pass":
        raise ProjectError(
            "Chapter commit requires an originality report with decision=pass"
        )
    wording = report.get("wording")
    structure = report.get("structure")
    if not isinstance(wording, dict) or wording.get("status") != "pass":
        raise ProjectError("Originality report wording layer is not pass")
    if not isinstance(structure, dict) or structure.get("status") != "pass":
        raise ProjectError("Originality report structure layer is not pass")
    chapter_hash = sha256_file(chapter_path)
    candidates = report.get("candidate_files")
    if not isinstance(candidates, list) or not any(
        isinstance(item, dict) and item.get("sha256") == chapter_hash
        for item in candidates
    ):
        raise ProjectError("Originality report does not cover the staged chapter hash")
    plan_path = root / "research/originality-plan.json"
    report_plan_hash = report.get("originality_plan_sha256")
    if report_plan_hash != sha256_bytes(
        read_stable_bytes(plan_path, label="Originality plan")
    ):
        raise ProjectError("Originality plan changed after the report was generated")
    references = report.get("reference_files")
    if not isinstance(references, list):
        raise ProjectError("Originality report reference_files must be an array")
    for reference in references:
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
            raise ProjectError("Originality report contains an invalid reference entry")
        reference_relative = reference["path"]
        if "\\" in reference_relative:
            raise ProjectError("Originality report reference path must use POSIX separators")
        pure_reference = Path(reference_relative)
        if (
            pure_reference.is_absolute()
            or "." in pure_reference.parts
            or ".." in pure_reference.parts
            or pure_reference.as_posix() != reference_relative
        ):
            raise ProjectError("Originality report reference path is not normalized")
        raw_reference = root / pure_reference
        if _contains_symlink(raw_reference):
            raise ProjectError(
                "Originality report reference cannot traverse a link or reparse point"
            )
        reference_path = raw_reference.resolve()
        if not is_within(reference_path, root) or not reference_path.is_file():
            raise ProjectError("Originality report reference is missing or out of scope")
        if sha256_bytes(
            read_stable_bytes(reference_path, label="Originality reference")
        ) != reference.get("sha256"):
            raise ProjectError("An originality reference changed after audit")
    if in_reviews:
        archive_path = report_path
    else:
        generated_at = report.get("generated_at")
        if not isinstance(generated_at, str) or not generated_at.strip():
            raise ProjectError("Originality report generated_at must not be empty")
        try:
            generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProjectError("Originality report generated_at must be ISO-8601") from exc
        if generated.tzinfo is None:
            raise ProjectError("Originality report generated_at must include a timezone")
        timestamp = generated.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fingerprint = sha256_bytes(report_bytes)[:10]
        archive_path = reviews_root / f"originality-audit-{timestamp}-{fingerprint}.json"
        if _contains_symlink(archive_path):
            raise ProjectError(
                "Originality report archive path cannot traverse a link or reparse point"
            )
        if archive_path.exists() and read_stable_bytes(
            archive_path, label="Originality report archive"
        ) != report_bytes:
            raise ProjectError(
                f"Originality report archive target already exists with different bytes: {archive_path}"
            )
    return report, report_path, archive_path, report_bytes


def validate_humanization_review(
    package: Path,
    package_manifest: dict[str, Any],
    chapter_path: Path,
    chapter_number: int,
) -> dict[str, Any]:
    review_relative = package_manifest.get("humanization_review_file")
    if not isinstance(review_relative, str) or not review_relative.strip():
        raise ProjectError(
            "commit.json must name a complete humanization_review_file"
        )
    review_path = resolve_package_file(package, review_relative)
    review = read_json(review_path)
    if review.get("schema_version") != SCHEMA_VERSION:
        raise ProjectError("Humanization review has an unsupported schema_version")
    if review.get("status") != "complete":
        raise ProjectError("Humanization review status must be complete")
    if review.get("skill") != "humanizer-zh":
        raise ProjectError("Humanization review must declare skill=humanizer-zh")
    review_chapter = review.get("chapter_number")
    if (
        not isinstance(review_chapter, int)
        or isinstance(review_chapter, bool)
        or review_chapter != chapter_number
    ):
        raise ProjectError("Humanization review chapter_number does not match commit.json")

    reviewed_at = review.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        raise ProjectError("Humanization review reviewed_at must be a non-empty ISO-8601 time")
    try:
        parsed_reviewed_at = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectError("Humanization review reviewed_at must be ISO-8601") from exc
    if parsed_reviewed_at.tzinfo is None:
        raise ProjectError("Humanization review reviewed_at must include a timezone")

    outcome = review.get("outcome")
    if outcome not in {"revised", "unchanged"}:
        raise ProjectError("Humanization review outcome must be revised or unchanged")
    summary = review.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ProjectError("Humanization review summary must not be empty")
    protected = review.get("protected_elements")
    if (
        not isinstance(protected, list)
        or not protected
        or not all(isinstance(item, str) and item.strip() for item in protected)
    ):
        raise ProjectError(
            "Humanization review protected_elements must contain non-empty strings"
        )

    files: dict[str, tuple[Path, str]] = {}
    for role in ("source", "result"):
        record = review.get(role)
        if not isinstance(record, dict):
            raise ProjectError(f"Humanization review {role} must be an object")
        relative = record.get("path")
        expected_hash = record.get("sha256")
        if not isinstance(relative, str) or not relative.strip():
            raise ProjectError(f"Humanization review {role}.path must not be empty")
        path = resolve_package_file(package, relative)
        if path.suffix.lower() != ".md":
            raise ProjectError(f"Humanization review {role} must be a Markdown file")
        if not isinstance(expected_hash, str) or re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ) is None:
            raise ProjectError(
                f"Humanization review {role}.sha256 must be a lowercase SHA-256"
            )
        if sha256_file(path) != expected_hash:
            raise ProjectError(
                f"Humanization review {role} hash does not match its staged file"
            )
        files[role] = (path, expected_hash)

    source_path, source_hash = files["source"]
    result_path, result_hash = files["result"]
    if source_path == result_path:
        raise ProjectError(
            "Humanization review source and result must be separate Markdown files"
        )
    if result_path != chapter_path.resolve():
        raise ProjectError(
            "Humanization review result.path must match commit.json chapter_file"
        )
    if outcome == "revised" and source_hash == result_hash:
        raise ProjectError("A revised humanization review requires different hashes")
    if outcome == "unchanged" and source_hash != result_hash:
        raise ProjectError("An unchanged humanization review requires matching hashes")

    selection = review.get("selection")
    if not isinstance(selection, dict):
        raise ProjectError("Humanization review selection must be an object")
    if selection.get("selected") != "result":
        raise ProjectError("Humanization review selection.selected must be result")
    authorization = selection.get("authorization_reference")
    if not isinstance(authorization, str) or not authorization.strip():
        raise ProjectError(
            "Humanization review selection.authorization_reference must not be empty"
        )
    return review


def _commit_chapter_impl(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    workspace_raw = getattr(args, "workspace", None)
    work_id = getattr(args, "work_id", None)
    if not workspace_raw or not work_id:
        raise ProjectError(
            "commit-chapter requires --workspace and --work-id so the project "
            "write lease cannot be bypassed"
        )
    # Imported lazily because novel_workspace imports this module while
    # registering and creating projects.
    try:
        import novel_workspace
    except (ImportError, OSError) as exc:
        raise ProjectError(f"Project write authorization module unavailable: {exc}") from exc
    try:
        write_authority = novel_workspace.write_check(workspace_raw, work_id)
    except (OSError, sqlite3.Error, novel_workspace.WorkspaceError) as exc:
        raise ProjectError(f"Project write authorization failed: {exc}") from exc
    authorized_root = Path(write_authority["project_root"]).resolve()
    if authorized_root != root:
        raise ProjectError(
            "The work lease belongs to a different project; refusing to commit"
        )
    package = Path(args.package).expanduser()
    if not package.is_absolute():
        package = root / package
    package = package.resolve()
    staging_root = (root / "staging/chapters").resolve()
    if not package.is_dir() or not is_within(package, staging_root):
        raise ProjectError(
            "Chapter package must be a directory under staging/chapters"
        )
    package_manifest = read_json(package / "commit.json")
    if package_manifest.get("schema_version") != SCHEMA_VERSION:
        raise ProjectError("Staged commit.json has an unsupported schema_version")
    chapter_number = package_manifest.get("chapter_number")
    if (
        not isinstance(chapter_number, int)
        or isinstance(chapter_number, bool)
        or chapter_number < 1
        or chapter_number > 9999
    ):
        raise ProjectError("commit.json chapter_number must be an integer from 1 to 9999")
    manuscript_filename = str(package_manifest.get("manuscript_filename", ""))
    match = CHAPTER_NAME.fullmatch(manuscript_filename)
    if match is None or int(match.group("number")) != chapter_number:
        raise ProjectError(
            "commit.json manuscript_filename must match its four-digit chapter number"
        )

    manifest = read_json(root / "novel.json")
    work_type = work_type_for_manifest(manifest)
    if work_type == "short_story" and chapter_number != 1:
        raise ProjectError(
            "A short_story project has one complete canonical Markdown unit; "
            "revise unit 0001 through the revision workflow instead of adding another"
        )
    state = read_json(root / "continuity/state.json")
    if manifest.get("current_chapter") != chapter_number - 1:
        raise ProjectError("Chapter commits must be contiguous and exactly one chapter ahead")
    if state.get("through_chapter") != chapter_number - 1:
        raise ProjectError("Continuity state is not synchronized with current_chapter")
    try:
        review_before = novel_review.ensure_commit_allowed(root, chapter_number)
    except novel_review.ReviewError as exc:
        raise ProjectError(str(exc)) from exc
    framework = frontmatter_metadata(root / "planning/framework-session.md")
    if framework.get("stage") != "complete" or framework.get("confirmation") != "confirmed":
        raise ProjectError("Formal chapter commit requires a confirmed story framework")
    for key in ("requirements_confidence", "story_confidence"):
        try:
            if int(framework.get(key, "0")) < 95:
                raise ProjectError("Formal chapter commit requires both 95% gates")
        except ValueError as exc:
            raise ProjectError(f"Framework {key} is not an integer") from exc

    chapter_source = resolve_package_file(
        package, str(package_manifest.get("chapter_file", "chapter.md"))
    )
    memory_source = resolve_package_file(
        package, str(package_manifest.get("memory_file", "memory.md"))
    )
    continuity_source = resolve_package_file(
        package,
        str(package_manifest.get("continuity_state_file", "continuity-state.json")),
    )
    chapter_bytes = chapter_source.read_bytes()
    memory_bytes = memory_source.read_bytes()
    if not chapter_bytes.strip():
        raise ProjectError("Staged chapter is empty")
    try:
        memory_text = memory_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectError("Staged memory card must be UTF-8") from exc
    if manuscript_filename not in memory_text:
        raise ProjectError("Staged memory card does not link to manuscript_filename")
    staged_state = read_json(continuity_source)
    if staged_state.get("schema_version") != SCHEMA_VERSION:
        raise ProjectError("Staged continuity state has an unsupported schema_version")
    if staged_state.get("through_chapter") != chapter_number:
        raise ProjectError("Staged continuity state must advance through_chapter")
    if not isinstance(staged_state.get("characters"), dict) or not isinstance(
        staged_state.get("open_threads"), list
    ):
        raise ProjectError("Staged continuity state has invalid characters/open_threads")

    humanization_review = validate_humanization_review(
        package, package_manifest, chapter_source, chapter_number
    )

    report_relative = str(package_manifest.get("originality_report", ""))
    if not report_relative:
        raise ProjectError("commit.json must name a passing originality_report")
    (
        originality_report,
        originality_source,
        originality_archive,
        originality_bytes,
    ) = validate_originality_report(
        root, report_relative, chapter_source
    )

    try:
        continuity_result = novel_continuity.validate_chapter_package(
            root, package, package_manifest, chapter_source, staged_state
        )
    except novel_continuity.ContinuityError as exc:
        raise ProjectError(str(exc)) from exc

    index_fields = package_manifest.get("index")
    if not isinstance(index_fields, dict):
        raise ProjectError("commit.json index must be an object")
    required_index = (
        "title",
        "pov",
        "story_time",
        "location",
        "fact_summary",
        "key_change",
        "thread_ids",
    )
    missing_index = [key for key in required_index if key not in index_fields]
    if missing_index:
        raise ProjectError("commit.json index is missing: " + ", ".join(missing_index))

    chapter_target = root / "manuscript/chapters" / manuscript_filename
    memory_target = root / "memory/chapters" / f"{chapter_number:04d}.md"
    if chapter_target.exists() or memory_target.exists():
        raise ProjectError("Refusing to overwrite an existing chapter or memory card")
    index_path = root / "manuscript/index.md"
    index_text = index_path.read_text(encoding="utf-8")
    relative_target = f"chapters/{manuscript_filename}"
    if relative_target in markdown_link_targets(index_text):
        raise ProjectError("Chapter is already linked in manuscript/index.md")
    row = (
        f"| {chapter_number:04d} | {markdown_cell(index_fields['title'])} | "
        f"{markdown_cell(index_fields['pov'])} | "
        f"{markdown_cell(index_fields['story_time'])} | "
        f"{markdown_cell(index_fields['location'])} | "
        f"{markdown_cell(index_fields['fact_summary'])} | "
        f"{markdown_cell(index_fields['key_change'])} | "
        f"{markdown_cell(index_fields['thread_ids'])} | "
        f"[正文]({relative_target}) |\n"
    )
    updated_index = index_text.rstrip() + "\n" + row
    updated_manifest = dict(manifest)
    updated_manifest.update(
        {
            "current_chapter": chapter_number,
            "status": "reviewing" if work_type == "short_story" else "drafting",
            "updated_at": utc_now(),
        }
    )

    writes: list[tuple[Path, bytes]] = [
        (chapter_target, chapter_bytes),
        (memory_target, memory_bytes),
        (index_path, updated_index.encode("utf-8")),
        (continuity_result["audit_target"], continuity_result["audit_bytes"]),
        (root / novel_continuity.FACTS_PATH, continuity_result["facts_bytes"]),
        (
            root / novel_continuity.EXCEPTIONS_PATH,
            continuity_result["exceptions_bytes"],
        ),
        (
            root / novel_continuity.DEPENDENCIES_PATH,
            continuity_result["dependencies_bytes"],
        ),
    ]
    if originality_archive != originality_source:
        writes.append((originality_archive, originality_bytes))
    optional_replacements = (
        ("timeline_file", root / "continuity/timeline.md"),
        ("threads_file", root / "continuity/threads.md"),
        ("book_summary_file", root / "memory/book-summary.md"),
    )
    for manifest_key, target in optional_replacements:
        relative = package_manifest.get(manifest_key)
        if relative:
            writes.append((target, resolve_package_file(package, str(relative)).read_bytes()))
    writes.extend(
        [
            (root / "continuity/state.json", dump_json(staged_state).encode("utf-8")),
            (root / "novel.json", dump_json(updated_manifest).encode("utf-8")),
        ]
    )
    try:
        head_target, head_bytes, committed_canon_hash = (
            novel_continuity.finalize_head_write(
                root,
                writes,
                chapter_number,
                continuity_result["audit_target"].relative_to(root).as_posix(),
            )
        )
    except novel_continuity.ContinuityError as exc:
        raise ProjectError(str(exc)) from exc
    writes.append((head_target, head_bytes))

    def validate_committed_state() -> None:
        assert_staging_snapshot()
        errors, _ = collect_validation(root, check_transactions=False)
        if errors:
            raise ProjectError("Post-commit validation failed: " + "; ".join(errors))
        try:
            guard.assert_live()
            guard.refresh_base_after_write("commit-chapter transaction")
        except (OSError, sqlite3.Error, novel_workspace.WorkspaceError) as exc:
            raise ProjectError(
                f"Project write authorization expired during commit: {exc}"
            ) from exc

    # The guard acquires BEGIN IMMEDIATE and remains open for the complete
    # atomic file transaction.  A second writer therefore cannot reclaim the
    # lease between the final authorization check and rollback/replace.
    package_snapshot_info = getattr(args, "_package_snapshot_info", None)

    def assert_staging_snapshot() -> None:
        if not package_snapshot_info:
            return
        _assert_package_snapshot_unchanged(
            package_snapshot_info["original_package"],
            package_snapshot_info["original_fingerprint"],
            package_snapshot_info["snapshot_package"],
            package_snapshot_info["snapshot_fingerprint"],
        )
        for item in package_snapshot_info.get("external_files", ()):
            path, expected_hash, expected_size = item
            if _link_like(path) or not path.is_file():
                raise ProjectError(f"Commit evidence file disappeared or became a link: {path}")
            content = path.read_bytes()
            if (
                len(content) != expected_size
                or sha256_bytes(content) != expected_hash
            ):
                raise ProjectError(
                    f"Commit evidence file changed during validation: {path}"
                )

    try:
        with novel_workspace.write_guard(
            workspace_raw,
            work_id,
            expected_project_root=root,
        ) as guard:
            # Recheck the immutable package and external evidence immediately
            # before replacing any canonical bytes.  If a writer changed them
            # during validation, transactional_write rolls the canonical files
            # back instead of committing a mixed evidence set.
            assert_staging_snapshot()
            transactional_write(
                writes,
                validator=validate_committed_state,
                journal_root=root,
                expected_targets={chapter_target: None, memory_target: None},
            )
    except (OSError, sqlite3.Error, novel_workspace.WorkspaceError) as exc:
        raise ProjectError(f"Project write authorization expired: {exc}") from exc
    cache_result: dict[str, Any] | None = None
    cache_warning: str | None = None
    if (root / ".novel-cache/novel-memory.sqlite3").is_file():
        try:
            import novel_memory

            cache_result = novel_memory.update_index(root)
        except Exception as exc:  # Derived cache never invalidates canonical commit.
            cache_warning = f"Derived SQLite index update failed; rebuild it: {exc}"
    review_after = novel_review.review_status(root)
    commit_warnings = [cache_warning] if cache_warning else []
    if review_after["review_due"]:
        if work_type == "short_story":
            commit_warnings.append(
                "Short-story completion review is due for the full manuscript"
            )
        else:
            commit_warnings.append(
                "Periodic review is due for chapters "
                f"{review_after['review_from']:04d}-{review_after['review_through']:04d}"
            )
    result = {
        "status": "committed",
        "project_root": str(root),
        "chapter_number": chapter_number,
        "manuscript": chapter_target.relative_to(root).as_posix(),
        "memory_card": memory_target.relative_to(root).as_posix(),
        "humanization_review": str(package_manifest["humanization_review_file"]),
        "humanization_outcome": humanization_review.get("outcome"),
        "originality_report": originality_archive.relative_to(root).as_posix(),
        "originality_decision": originality_report.get("decision"),
        "continuity_audit": continuity_result["audit_target"]
        .relative_to(root)
        .as_posix(),
        "continuity_decision": continuity_result["audit"].get("decision"),
        "continuity_reviewer_mode": continuity_result["audit"]
        .get("reviewer", {})
        .get("mode"),
        "canon_sha256": committed_canon_hash,
        "memory_index": cache_result,
        "periodic_review_before_commit": review_before,
        "periodic_review": review_after,
        "review_required_before_next_commit": review_after["commit_blocked"],
        "warnings": commit_warnings,
    }
    if work_type == "short_story":
        result["work_type"] = work_type
    return result


def commit_chapter(args: argparse.Namespace) -> dict[str, Any]:
    """Commit from one immutable staging snapshot.

    The public entry point keeps the caller's package untouched while the
    implementation validates a private byte-for-byte copy.  This closes the
    check/use race where an editor could replace one evidence file between two
    reads of the same package.
    """

    if not getattr(args, "workspace", None) or not getattr(args, "work_id", None):
        raise ProjectError("commit-chapter requires --workspace and --work-id")
    root = resolve_root(args.root)
    raw_package = Path(args.package).expanduser()
    if not raw_package.is_absolute():
        raw_package = root / raw_package
    if _contains_symlink(raw_package):
        raise ProjectError("Chapter package cannot traverse a symbolic link")
    original_package = raw_package.resolve()
    staging_root = (root / "staging/chapters").resolve()
    if (
        not original_package.is_dir()
        or not is_within(original_package, staging_root)
    ):
        raise ProjectError(
            "Chapter package must be a directory under staging/chapters"
        )

    snapshot_package, original_fingerprint, snapshot_fingerprint = (
        _materialize_package_snapshot(original_package)
    )
    external_files: list[tuple[Path, str, int]] = []
    try:
        # The originality report normally lives under staging/originality or
        # reviews, outside the chapter package.  Capture it now so its bytes
        # are tied to the package snapshot as well.
        try:
            staged_commit = read_json(snapshot_package / "commit.json")
        except ProjectError:
            staged_commit = {}
        report_relative = staged_commit.get("originality_report")
        if isinstance(report_relative, str) and report_relative.strip():
            report_path = (root / report_relative).resolve()
            if not is_within(report_path, original_package):
                if _link_like(report_path) or not report_path.is_file():
                    raise ProjectError(
                        "Originality report must be a regular file before commit"
                    )
                report_bytes = report_path.read_bytes()
                external_files.append(
                    (report_path, sha256_bytes(report_bytes), len(report_bytes))
                )

        snapshot_args = argparse.Namespace(**vars(args))
        snapshot_args.package = str(snapshot_package)
        snapshot_args._package_snapshot_info = {
            "original_package": original_package,
            "original_fingerprint": original_fingerprint,
            "snapshot_package": snapshot_package,
            "snapshot_fingerprint": snapshot_fingerprint,
            "external_files": tuple(external_files),
        }
        return _commit_chapter_impl(snapshot_args)
    finally:
        shutil.rmtree(snapshot_package, ignore_errors=True)


def validate_project(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = resolve_root(args.root)
    errors, warnings = collect_validation(root)
    result = {
        "status": "valid" if not errors else "invalid",
        "project_root": str(root),
        "errors": errors,
        "warnings": warnings,
    }
    return result, 0 if not errors else 1


def project_status(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = resolve_root(args.root)
    errors, warnings = collect_validation(root)
    if errors:
        return {
            "status": "invalid",
            "project_root": str(root),
            "errors": errors,
            "warnings": warnings,
        }, 1

    manifest = read_json(root / "novel.json")
    state = read_json(root / "continuity/state.json")
    framework = frontmatter_metadata(root / "planning/framework-session.md")
    comparables = frontmatter_metadata(root / "research/comparable-works.md")
    chapters = chapter_files(root)
    index_path = root / "manuscript/index.md"
    index_text = index_path.read_text(encoding="utf-8")
    index_targets = markdown_link_targets(index_text)
    memory_cards = list((root / "memory/chapters").glob("*.md"))
    source_records = source_manifest_records(root / "research/source-manifest.jsonl")
    periodic_review = novel_review.review_status(root)
    continuity = novel_continuity.continuity_status(root)
    indexed_chapters = sum(
        1
        for path in chapters
        if path.relative_to(root / "manuscript").as_posix() in index_targets
    )
    result = {
        "status": "ok",
        "project_root": str(root),
        "title": manifest["title"],
        "project_stage": manifest.get("status", ""),
        "framework_stage": framework.get("stage", ""),
        "framework_confirmation": framework.get("confirmation", ""),
        "requirements_confidence": int(
            framework.get("requirements_confidence", "0")
        ),
        "story_confidence": int(framework.get("story_confidence", "0")),
        "candidate_approval": comparables.get("candidate_approval", ""),
        "deep_analysis": comparables.get("deep_analysis", ""),
        "current_chapter": manifest.get("current_chapter", 0),
        "manuscript_files": len(chapters),
        "indexed_chapters": indexed_chapters,
        "chapter_memory_cards": len(memory_cards),
        "latest_manuscript": (
            chapters[-1].relative_to(root).as_posix() if chapters else None
        ),
        "tracked_characters": len(state.get("characters", {})),
        "open_threads": len(state.get("open_threads", [])),
        "registered_sources": len(source_records),
        "periodic_review": periodic_review,
        "continuity": continuity,
        "memory_index": (
            "present"
            if (root / ".novel-cache/novel-memory.sqlite3").is_file()
            else "missing"
        ),
        "warnings": warnings,
    }
    work_type = work_type_for_manifest(manifest)
    if work_type == "short_story":
        result["work_type"] = work_type
    return result, 0


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(
        description=(
            "Create, upgrade, validate, transition, and transactionally commit an "
            "interactive, research-backed, stateful Chinese fiction project."
        )
    )
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )

    init_parser = subparsers.add_parser("init", help="Initialize a new project.")
    init_parser.add_argument("root", help="Dedicated project directory.")
    init_parser.add_argument("--title", required=True, help="Work title.")
    init_parser.add_argument("--language", default="zh-CN", help="BCP-47 language.")
    init_parser.add_argument("--genre", default="", help="Genre or genre blend.")
    init_parser.add_argument(
        "--work-type",
        choices=sorted(WORK_TYPES),
        default="serial_novel",
        help="serial_novel for long-form chapters or short_story for one complete text.",
    )
    init_parser.add_argument(
        "--target-words",
        type=int,
        help="Optional target length; verify platform limits before publication.",
    )

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help=(
            "Add missing interactive planning, research, index, and memory "
            "scaffolding."
        ),
    )
    upgrade_parser.add_argument("root", help="Initialized project directory.")
    upgrade_parser.add_argument(
        "--workspace", help="Workspace root that owns the write lease."
    )
    upgrade_parser.add_argument("--work-id", help="Active work context that owns the lease.")
    upgrade_parser.add_argument(
        "--allow-bootstrap",
        action="store_true",
        help="Explicitly allow upgrading an unregistered standalone project.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate a project.")
    validate_parser.add_argument("root", help="Project directory.")

    status_parser = subparsers.add_parser("status", help="Summarize project state.")
    status_parser.add_argument("root", help="Project directory.")

    research_parser = subparsers.add_parser(
        "research-state",
        help="Apply an authorized candidate-approval or deep-analysis transition.",
    )
    research_parser.add_argument("root", help="Project directory.")
    research_parser.add_argument(
        "--candidate-approval", choices=sorted(CANDIDATE_APPROVALS)
    )
    research_parser.add_argument(
        "--deep-analysis", choices=sorted(DEEP_ANALYSIS_STAGES)
    )
    research_parser.add_argument("--authorization-reference", required=True)
    research_parser.add_argument("--workspace")
    research_parser.add_argument("--work-id")
    research_parser.add_argument("--allow-bootstrap", action="store_true")

    framework_parser = subparsers.add_parser(
        "framework-state",
        help="Apply an authorized framework/confidence transition after content sync.",
    )
    framework_parser.add_argument("root", help="Project directory.")
    framework_parser.add_argument("--stage", choices=sorted(FRAMEWORK_STAGES))
    framework_parser.add_argument(
        "--confirmation", choices=sorted(FRAMEWORK_CONFIRMATIONS)
    )
    framework_parser.add_argument("--requirements-confidence", type=int)
    framework_parser.add_argument("--story-confidence", type=int)
    framework_parser.add_argument("--authorization-reference", required=True)
    framework_parser.add_argument("--workspace")
    framework_parser.add_argument("--work-id")
    framework_parser.add_argument("--allow-bootstrap", action="store_true")

    framework_sync_parser = subparsers.add_parser(
        "framework-sync",
        help=(
            "Atomically synchronize framework, memory, and allowlisted project "
            "settings from an external work-root package, then record state."
        ),
    )
    framework_sync_parser.add_argument("root", help="Project directory.")
    framework_sync_parser.add_argument(
        "source_root",
        help=(
            "External work-root directory containing planning/framework-session.md, "
            "the four story-bible files, outlines/master-outline.md, two memory "
            "files, and project-settings.json."
        ),
    )
    framework_sync_parser.add_argument("--stage", choices=sorted(FRAMEWORK_STAGES))
    framework_sync_parser.add_argument(
        "--confirmation", choices=sorted(FRAMEWORK_CONFIRMATIONS)
    )
    framework_sync_parser.add_argument("--requirements-confidence", type=int)
    framework_sync_parser.add_argument("--story-confidence", type=int)
    framework_sync_parser.add_argument("--authorization-reference", required=True)
    framework_sync_parser.add_argument(
        "--workspace", required=True, help="Workspace root that owns the write lease."
    )
    framework_sync_parser.add_argument(
        "--work-id", required=True, help="Active work context that owns the write lease."
    )

    commit_parser = subparsers.add_parser(
        "commit-chapter",
        help=(
            "Atomically commit a staged chapter package, or the sole complete "
            "short-story unit, after originality approval."
        ),
    )
    commit_parser.add_argument("root", help="Project directory.")
    commit_parser.add_argument(
        "package", help="Directory under <project>/staging/chapters containing commit.json."
    )
    commit_parser.add_argument(
        "--workspace",
        required=True,
        help="Workspace root that owns the write lease.",
    )
    commit_parser.add_argument(
        "--work-id",
        required=True,
        help="Active work context that owns the write lease.",
    )
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        if args.command == "init":
            return init_project(args)
        if args.command == "upgrade":
            return upgrade_project(args)
        if args.command == "validate":
            return validate_project(args)
        if args.command == "status":
            return project_status(args)
        if args.command == "research-state":
            return research_state(args)
        if args.command == "framework-state":
            return framework_state(args)
        if args.command == "framework-sync":
            return framework_sync(args)
        if args.command == "commit-chapter":
            return commit_chapter(args)
        raise ProjectError(f"Unsupported command: {args.command}")

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_project",
        domain_errors=(ProjectError, OSError),
    )


if __name__ == "__main__":
    sys.exit(main())
