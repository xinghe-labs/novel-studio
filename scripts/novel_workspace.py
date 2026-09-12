#!/usr/bin/env python3
"""Manage isolated novel projects and platform-independent work contexts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import novel_project
import novel_cli


SCHEMA_VERSION = 1
WORKSPACE_KIND = "chinese-novel-workspace"
DEFAULT_LEASE_SECONDS = 1800
MAX_LEASE_SECONDS = 86400
# A live holder must refresh its heartbeat at least every five minutes (or
# within its shorter lease).  This makes heartbeat_at an active recovery signal
# instead of a field that is merely copied into status output.
HEARTBEAT_GRACE_SECONDS = 300
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SHORT_STORY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,2}$")
PROJECT_DATE = re.compile(r"^\d{8}$")
WORK_SUBDIRECTORIES = ("drafts", "research", "reports", "temp")
HASH_EXCLUDED_PARTS = frozenset({".git", ".novel-cache", "__pycache__"})
HASH_EXCLUDED_ROOTS = frozenset({"exports", "staging"})
LEGACY_HASH_EXCLUDED_ROOTS = frozenset({"exports"})
STATE_HASH_VERSION = 2
LEGACY_STATE_HASH_VERSION = 1
REGISTRY_LEGACY_SCHEMA_VERSIONS = frozenset({0})
REQUIRED_REGISTRY_COLUMNS = {
    "metadata": frozenset({"key", "value"}),
    "projects": frozenset(
        {"project_id", "title", "project_root", "status", "created_at", "updated_at"}
    ),
    "works": frozenset(
        {
            "work_id",
            "work_root",
            "project_id",
            "purpose",
            "client",
            "status",
            "base_state_hash",
            "created_at",
            "updated_at",
        }
    ),
    "leases": frozenset(
        {
            "project_id",
            "work_id",
            "acquired_at",
            "heartbeat_at",
            "expires_at",
            "lease_seconds",
            "heartbeat_enforced",
        }
    ),
    "lease_events": frozenset(
        {
            "event_id",
            "project_id",
            "previous_work_id",
            "event",
            "reason",
            "actor_work_id",
            "occurred_at",
            "previous_expires_at",
            "current_state_hash",
            "state_hash_error",
        }
    ),
}


class WorkspaceError(RuntimeError):
    pass


class WriteGuard:
    """A live project-write authorization held for one SQLite transaction."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        work_id: str,
        project_id: str,
        project_root: str,
        state_hash: str,
        hash_version: int,
    ) -> None:
        self.connection = connection
        self.work_id = work_id
        self.project_id = project_id
        self.project_root = project_root
        self.state_hash = state_hash
        self.hash_version = hash_version

    def assert_live(self) -> sqlite3.Row:
        """Recheck ownership while the guard's write transaction is held."""

        work = work_row(self.connection, self.work_id)
        if work["project_id"] != self.project_id:
            raise WorkspaceError("The guarded work changed project ownership")
        owner = require_live_owned_lease(self.connection, work)
        project = project_row(self.connection, self.project_id)
        if Path(project["project_root"]).resolve() != Path(self.project_root).resolve():
            raise WorkspaceError("The guarded project root changed")
        return owner


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_datetime().replace(microsecond=0).isoformat()


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise WorkspaceError(f"Invalid timestamp in workspace registry: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_root(raw_root: str | Path) -> Path:
    root = Path(raw_root).expanduser().resolve()
    anchor = Path(root.anchor).resolve()
    home = Path.home().resolve()
    if root == anchor:
        raise WorkspaceError("Workspace root cannot be a filesystem root.")
    if root == home:
        raise WorkspaceError("Workspace root cannot be the user home directory.")
    return root


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_identifier(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not IDENTIFIER.fullmatch(normalized):
        raise WorkspaceError(
            f"{label} must be 3-64 lowercase letters, digits, or hyphens and "
            "cannot contain path separators."
        )
    return normalized


def generated_project_id() -> str:
    return f"novel-{uuid.uuid4().hex[:8]}"


def local_project_date() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d")


def validate_short_story_slug(value: str | None) -> str:
    if value is None:
        raise WorkspaceError(
            "short_story_slug is required when a short-story project_id is generated."
        )
    normalized = value.strip().lower()
    if not SHORT_STORY_SLUG.fullmatch(normalized) or not any(
        character.isalpha() for character in normalized
    ):
        raise WorkspaceError(
            "short_story_slug must contain one to three lowercase English words "
            "separated by hyphens, using only ASCII letters and digits."
        )
    return normalized


def validate_project_date(value: str | None) -> str:
    normalized = value or local_project_date()
    if not PROJECT_DATE.fullmatch(normalized):
        raise WorkspaceError("project_date must use YYYYMMDD format.")
    try:
        parsed = datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise WorkspaceError("project_date must be a valid calendar date.") from exc
    if parsed.strftime("%Y%m%d") != normalized:
        raise WorkspaceError("project_date must use YYYYMMDD format.")
    return normalized


def reserve_short_story_project_root(
    root: Path, slug: str, project_date: str | None = None
) -> tuple[str, Path]:
    normalized_slug = validate_short_story_slug(slug)
    date_stamp = validate_project_date(project_date)
    projects_root = (root / "projects").resolve()
    connection = open_registry(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        registered = {
            row[0] for row in connection.execute("SELECT project_id FROM projects")
        }
        suffix = 1
        while True:
            suffix_text = "" if suffix == 1 else f"-{suffix}"
            candidate = f"shortstory-{normalized_slug}-{date_stamp}{suffix_text}"
            normalized_id = validate_identifier(candidate, "project_id")
            project_root = (projects_root / normalized_id).resolve()
            if project_root.parent != projects_root:
                raise WorkspaceError("Generated short-story project path escaped projects/.")
            if normalized_id in registered or project_root.exists():
                suffix += 1
                continue
            try:
                project_root.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                suffix += 1
                continue
            connection.commit()
            return normalized_id, project_root
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def generated_work_id() -> str:
    timestamp = utc_datetime().strftime("%Y%m%dT%H%M%SZ")
    return f"work-{timestamp}-{uuid.uuid4().hex[:8]}"


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError(f"Expected a JSON object in {path}")
    return data


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(dump_json(data))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def workspace_config_path(root: Path) -> Path:
    return root / "workspace.json"


def validate_workspace_config(root: Path) -> dict[str, Any]:
    config = read_json(workspace_config_path(root))
    if config.get("schema_version") != SCHEMA_VERSION:
        raise WorkspaceError("workspace.json has an unsupported schema_version")
    if config.get("kind") != WORKSPACE_KIND:
        raise WorkspaceError("workspace.json is not a Chinese novel workspace")
    return config


def initialize_workspace(
    raw_root: str | Path, *, adopt_existing: bool = False
) -> dict[str, Any]:
    root = resolve_root(raw_root)
    config_path = workspace_config_path(root)
    if config_path.is_file():
        validate_workspace_config(root)
        (root / "projects").mkdir(parents=True, exist_ok=True)
        (root / "workspaces").mkdir(parents=True, exist_ok=True)
        connection = open_registry(root)
        connection.close()
        return {
            "status": "already_initialized",
            "workspace_root": str(root),
            "registry": str(root / "registry.sqlite3"),
        }

    if root.exists():
        unexpected = sorted(
            item.name for item in root.iterdir() if item.name not in {".git"}
        )
        if unexpected and not adopt_existing:
            raise WorkspaceError(
                "Refusing to initialize a non-empty workspace without "
                f"--adopt-existing. Existing entries: {', '.join(unexpected[:8])}"
            )

    root.mkdir(parents=True, exist_ok=True)
    (root / "projects").mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(parents=True, exist_ok=True)
    config = {
        "schema_version": SCHEMA_VERSION,
        "kind": WORKSPACE_KIND,
        "projects_directory": "projects",
        "workspaces_directory": "workspaces",
        "registry": "registry.sqlite3",
        "created_at": utc_now(),
    }
    atomic_write_json(config_path, config)
    connection = open_registry(root)
    connection.close()
    return {
        "status": "created",
        "workspace_root": str(root),
        "registry": str(root / "registry.sqlite3"),
    }


def require_workspace(raw_root: str | Path, *, auto_initialize: bool = False) -> Path:
    root = resolve_root(raw_root)
    if not workspace_config_path(root).is_file():
        if not auto_initialize:
            raise WorkspaceError(f"Workspace is not initialized: {root}")
        initialize_workspace(root)
    validate_workspace_config(root)
    return root


def _parse_registry_schema_value(raw: Any) -> int:
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise WorkspaceError(
            f"Registry schema_version is not an integer: {raw!r}"
        ) from exc
    if version != SCHEMA_VERSION and version not in REGISTRY_LEGACY_SCHEMA_VERSIONS:
        raise WorkspaceError(
            f"Registry schema_version {version} is unsupported "
            f"(expected {SCHEMA_VERSION})"
        )
    return version


def _registry_schema_version(connection: sqlite3.Connection) -> int | None:
    """Read the registry schema marker without changing the database."""

    if "metadata" not in _registry_table_names(connection):
        return None
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        return None
    return _parse_registry_schema_value(row[0])


def _registry_table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def create_schema(connection: sqlite3.Connection) -> None:
    # Check the marker before any CREATE/ALTER/metadata write.  A future
    # registry must fail closed instead of being silently rewritten by an old
    # tool.
    prior_tables = _registry_table_names(connection)
    prior_schema_version = _registry_schema_version(connection)
    if prior_schema_version is None and prior_tables - {"sqlite_sequence"}:
        # A non-empty registry without a marker is not distinguishable from a
        # partially upgraded or foreign database.  Do not silently adopt it.
        raise WorkspaceError("Registry metadata is missing schema_version")
    prior_lease_columns = _table_columns(connection, "leases")
    # A registry that has only part of the additive lease migration cannot
    # prove that existing rows were created under the heartbeat contract.
    # Keep those rows on the old expires_at-only semantics until an explicit
    # acquire/renew operation writes the new marker.
    legacy_lease_layout = bool(prior_lease_columns) and not {
        "lease_seconds",
        "heartbeat_enforced",
    }.issubset(prior_lease_columns)
    legacy_schema = prior_schema_version in REGISTRY_LEGACY_SCHEMA_VERSIONS
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            project_root TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS works (
            work_id TEXT PRIMARY KEY,
            work_root TEXT NOT NULL UNIQUE,
            project_id TEXT REFERENCES projects(project_id),
            purpose TEXT NOT NULL,
            client TEXT NOT NULL,
            status TEXT NOT NULL,
            base_state_hash TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leases (
            project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
            work_id TEXT NOT NULL REFERENCES works(work_id),
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            lease_seconds INTEGER NOT NULL DEFAULT 1800,
            heartbeat_enforced INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS lease_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            previous_work_id TEXT,
            event TEXT NOT NULL,
            reason TEXT NOT NULL,
            actor_work_id TEXT,
            occurred_at TEXT NOT NULL,
            previous_expires_at TEXT,
            current_state_hash TEXT,
            state_hash_error TEXT
        );
        """
    )
    # Additive migration for registries created by schema revision 1.  The
    # workspace JSON schema intentionally remains compatible: no canonical
    # project files are rewritten by opening a registry.
    lease_columns = _table_columns(connection, "leases")
    if "lease_seconds" not in lease_columns:
        connection.execute(
            "ALTER TABLE leases ADD COLUMN lease_seconds INTEGER NOT NULL DEFAULT 1800"
        )
    if "heartbeat_enforced" not in lease_columns:
        connection.execute(
            "ALTER TABLE leases ADD COLUMN heartbeat_enforced INTEGER NOT NULL DEFAULT 1"
        )
    event_columns = _table_columns(connection, "lease_events")
    if "state_hash_error" not in event_columns:
        connection.execute(
            "ALTER TABLE lease_events ADD COLUMN state_hash_error TEXT"
        )
    if legacy_lease_layout or legacy_schema:
        # Older implementations had no verified heartbeat contract.  Keep
        # their original expires_at-only semantics until each lease is renewed
        # or reacquired by the new implementation.  This is intentionally
        # conservative for partially migrated/intermediate registries too.
        connection.execute("UPDATE leases SET heartbeat_enforced = 0")
    if prior_schema_version is None:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    elif prior_schema_version != SCHEMA_VERSION:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION),),
        )
    connection.commit()


def sync_registry(connection: sqlite3.Connection, root: Path) -> None:
    projects_root = root / "projects"
    workspaces_root = root / "workspaces"
    with connection:
        if projects_root.is_dir():
            for project_root in sorted(projects_root.iterdir()):
                metadata_path = project_root / ".novel-project.json"
                manifest_path = project_root / "novel.json"
                if not project_root.is_dir() or not metadata_path.is_file():
                    continue
                if not manifest_path.is_file():
                    continue
                metadata = read_json(metadata_path)
                project_id = validate_identifier(
                    str(metadata.get("project_id", "")), "project_id"
                )
                manifest = read_json(manifest_path)
                title = manifest.get("title")
                if not isinstance(title, str) or not title.strip():
                    continue
                created_at = str(metadata.get("created_at") or utc_now())
                updated_at = str(metadata.get("updated_at") or created_at)
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id, title, project_root, status, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO NOTHING
                    """,
                    (
                        project_id,
                        title.strip(),
                        str(project_root.resolve()),
                        str(metadata.get("status") or "active"),
                        created_at,
                        updated_at,
                    ),
                )

        project_ids = {
            row[0] for row in connection.execute("SELECT project_id FROM projects")
        }
        if workspaces_root.is_dir():
            for work_root in sorted(workspaces_root.iterdir()):
                context_path = work_root / "work.json"
                if not work_root.is_dir() or not context_path.is_file():
                    continue
                context = read_json(context_path)
                work_id = validate_identifier(
                    str(context.get("work_id", "")), "work_id"
                )
                project_id = context.get("project_id")
                if project_id is not None:
                    project_id = validate_identifier(str(project_id), "project_id")
                    if project_id not in project_ids:
                        continue
                created_at = str(context.get("created_at") or utc_now())
                updated_at = str(context.get("updated_at") or created_at)
                connection.execute(
                    """
                    INSERT INTO works(
                        work_id, work_root, project_id, purpose, client, status,
                        base_state_hash, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(work_id) DO NOTHING
                    """,
                    (
                        work_id,
                        str(work_root.resolve()),
                        project_id,
                        str(context.get("purpose") or ""),
                        str(context.get("client") or "generic"),
                        str(context.get("status") or "active"),
                        context.get("base_state_hash"),
                        created_at,
                        updated_at,
                    ),
                )


def open_registry(root: Path, *, synchronize: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(root / "registry.sqlite3", timeout=5.0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        # Validate before enabling WAL or running additive migrations so an
        # unknown future registry marker is never silently rewritten.
        _registry_schema_version(connection)
        connection.execute("PRAGMA journal_mode = WAL")
        create_schema(connection)
        if synchronize:
            sync_registry(connection, root)
        return connection
    except Exception:
        # ``sqlite3.Connection`` keeps a Windows file handle until close().
        # In particular, a future/invalid schema must not leave the registry
        # undeletable after this function fails closed.
        connection.close()
        raise


def project_state_hash(
    project_root: str | Path, *, include_staging: bool = False
) -> str:
    """Hash canonical project files.

    New work excludes ``staging/`` because it contains derived commit input.
    ``include_staging=True`` preserves the pre-2.1.0 algorithm for a
    compatibility comparison while an old work context is explicitly
    refreshed.
    """

    root = Path(project_root).resolve()
    if not (root / "novel.json").is_file():
        raise WorkspaceError(f"Not an initialized novel project: {root}")
    excluded_roots = (
        LEGACY_HASH_EXCLUDED_ROOTS if include_staging else HASH_EXCLUDED_ROOTS
    )
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in excluded_roots:
            continue
        if any(part in HASH_EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise WorkspaceError(f"Project state hashing refuses symbolic links: {path}")
        if path.is_file():
            files.append(path)
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(block)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def match_project_state_hash(
    project_root: str | Path, expected_hash: str
) -> tuple[str, int | None]:
    """Return the current hash and the algorithm that matched the baseline.

    A baseline from the previous release may still include unchanged staging
    files.  Accepting it once keeps an in-progress task recoverable; callers
    should run ``base-refresh`` afterwards to persist the current algorithm.
    """

    current_hash = project_state_hash(project_root)
    if current_hash == expected_hash:
        return current_hash, STATE_HASH_VERSION
    legacy_hash = project_state_hash(project_root, include_staging=True)
    if legacy_hash == expected_hash:
        return current_hash, LEGACY_STATE_HASH_VERSION
    return current_hash, None


def safe_project_state_hash(
    project_root: str | Path,
) -> tuple[str | None, str | None]:
    """Capture a hash for audit output without blocking lease recovery."""

    try:
        return project_state_hash(project_root), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def safe_match_project_state_hash(
    project_root: str | Path, expected_hash: str | None
) -> tuple[str | None, int | None, str | None]:
    """Best-effort hash comparison used by lease acquisition/audit paths."""

    if not expected_hash:
        current_hash, error = safe_project_state_hash(project_root)
        return current_hash, None, error
    try:
        current_hash, version = match_project_state_hash(project_root, expected_hash)
        return current_hash, version, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def project_row(connection: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    normalized = validate_identifier(project_id, "project_id")
    row = connection.execute(
        "SELECT * FROM projects WHERE project_id = ?", (normalized,)
    ).fetchone()
    if row is None:
        raise WorkspaceError(f"Unknown project_id: {normalized}")
    return row


def work_row(connection: sqlite3.Connection, work_id: str) -> sqlite3.Row:
    normalized = validate_identifier(work_id, "work_id")
    row = connection.execute(
        "SELECT * FROM works WHERE work_id = ?", (normalized,)
    ).fetchone()
    if row is None:
        raise WorkspaceError(f"Unknown work_id: {normalized}")
    return row


def register_project(
    raw_workspace: str | Path,
    raw_project_root: str | Path,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    project_root = Path(raw_project_root).expanduser().resolve()
    projects_root = (root / "projects").resolve()
    if project_root.parent != projects_root:
        raise WorkspaceError(
            f"Project must be an immediate child of {projects_root}: {project_root}"
        )
    if not (project_root / "novel.json").is_file():
        raise WorkspaceError(f"Missing novel.json in project: {project_root}")
    manifest = read_json(project_root / "novel.json")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        raise WorkspaceError("novel.json title must be a non-empty string")
    try:
        work_type = novel_project.work_type_for_manifest(manifest)
    except novel_project.ProjectError as exc:
        raise WorkspaceError(str(exc)) from exc
    normalized_id = validate_identifier(
        project_id or project_root.name, "project_id"
    )
    connection = open_registry(root)
    try:
        by_id = connection.execute(
            "SELECT * FROM projects WHERE project_id = ?", (normalized_id,)
        ).fetchone()
        by_path = connection.execute(
            "SELECT * FROM projects WHERE project_root = ?", (str(project_root),)
        ).fetchone()
        if by_id is not None and Path(by_id["project_root"]) != project_root:
            raise WorkspaceError(
                f"project_id is already registered to {by_id['project_root']}"
            )
        if by_path is not None and by_path["project_id"] != normalized_id:
            raise WorkspaceError(
                f"Project path is already registered as {by_path['project_id']}"
            )
        existing_metadata = project_root / ".novel-project.json"
        created_at = utc_now()
        prior_metadata: dict[str, Any] | None = None
        if existing_metadata.is_file():
            prior_metadata = read_json(existing_metadata)
            existing_id = validate_identifier(
                str(prior_metadata.get("project_id", "")), "project_id"
            )
            if existing_id != normalized_id:
                raise WorkspaceError(
                    f"Project metadata already uses project_id {existing_id}"
                )
            created_at = str(prior_metadata.get("created_at") or created_at)
        now = utc_now()
        metadata = dict(prior_metadata or {})
        metadata.update(
            {
                "schema_version": SCHEMA_VERSION,
                "project_id": normalized_id,
                "title": title.strip(),
                "status": "active",
                "created_at": created_at,
                "updated_at": now,
            }
        )
        metadata_keys = ["schema_version", "project_id", "title", "status"]
        if work_type == "short_story":
            metadata["work_type"] = work_type
            metadata_keys.append("work_type")
        metadata_changed = not prior_metadata or any(
            prior_metadata.get(key) != metadata.get(key)
            for key in metadata_keys
        )
        if metadata_changed:
            atomic_write_json(existing_metadata, metadata)
        else:
            metadata["updated_at"] = str(prior_metadata.get("updated_at") or created_at)
        with connection:
            connection.execute(
                """
                INSERT INTO projects(
                    project_id, title, project_root, status, created_at, updated_at
                ) VALUES(?, ?, ?, 'active', ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    title=excluded.title,
                    project_root=excluded.project_root,
                    status='active',
                    updated_at=excluded.updated_at
                """,
                (
                    normalized_id,
                    title.strip(),
                    str(project_root),
                    created_at,
                    metadata["updated_at"],
                ),
            )
        result = {
            "status": "already_registered" if by_id or by_path else "registered",
            "project_id": normalized_id,
            "title": title.strip(),
            "project_root": str(project_root),
            "state_hash": project_state_hash(project_root),
        }
        if work_type == "short_story":
            result["work_type"] = work_type
        return result
    finally:
        connection.close()


def create_project(
    raw_workspace: str | Path,
    *,
    title: str,
    genre: str = "",
    language: str = "zh-CN",
    work_type: str = "serial_novel",
    target_words: int | None = None,
    project_id: str | None = None,
    short_story_slug: str | None = None,
    project_date: str | None = None,
) -> dict[str, Any]:
    root = require_workspace(raw_workspace, auto_initialize=True)
    if work_type not in novel_project.WORK_TYPES:
        raise WorkspaceError("work_type must be serial_novel or short_story")
    if short_story_slug is not None and work_type != "short_story":
        raise WorkspaceError("short_story_slug is only valid for short_story projects.")
    reserved_short_story_root = False
    if project_id is None and work_type == "short_story":
        normalized_id, project_root = reserve_short_story_project_root(
            root, validate_short_story_slug(short_story_slug), project_date
        )
        reserved_short_story_root = True
    else:
        normalized_id = validate_identifier(
            project_id or generated_project_id(), "project_id"
        )
        project_root = (root / "projects" / normalized_id).resolve()
        if project_root.exists():
            raise WorkspaceError(f"Project directory already exists: {project_root}")
        connection = open_registry(root)
        try:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (normalized_id,)
            ).fetchone():
                raise WorkspaceError(f"project_id already exists: {normalized_id}")
        finally:
            connection.close()
    try:
        novel_project.init_project(
            SimpleNamespace(
                root=str(project_root),
                title=title,
                language=language,
                genre=genre,
                work_type=work_type,
                target_words=target_words,
            )
        )
    except novel_project.ProjectError as exc:
        if (
            reserved_short_story_root
            and project_root.parent == (root / "projects").resolve()
        ):
            shutil.rmtree(project_root, ignore_errors=True)
        raise WorkspaceError(str(exc)) from exc
    result = register_project(root, project_root, project_id=normalized_id)
    result["status"] = "created"
    return result


def project_list(raw_workspace: str | Path) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        rows = connection.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC, project_id"
        ).fetchall()
        projects = []
        for row in rows:
            project = dict(row)
            manifest_path = Path(project["project_root"]) / "novel.json"
            try:
                manifest = read_json(manifest_path)
                work_type = novel_project.work_type_for_manifest(manifest)
                if work_type == "short_story":
                    project["work_type"] = work_type
            except (WorkspaceError, novel_project.ProjectError):
                project["work_type"] = "unknown"
            projects.append(project)
        return {
            "status": "ok",
            "workspace_root": str(root),
            "count": len(projects),
            "projects": projects,
        }
    finally:
        connection.close()


def create_work(
    raw_workspace: str | Path,
    *,
    project_id: str | None = None,
    purpose: str = "",
    client: str = "generic",
    work_id: str | None = None,
) -> dict[str, Any]:
    root = require_workspace(raw_workspace, auto_initialize=True)
    normalized_work_id = validate_identifier(
        work_id or generated_work_id(), "work_id"
    )
    work_root = (root / "workspaces" / normalized_work_id).resolve()
    if work_root.parent != (root / "workspaces").resolve():
        raise WorkspaceError("Resolved work directory escapes workspaces/")
    if work_root.exists():
        raise WorkspaceError(f"Work directory already exists: {work_root}")
    normalized_project_id: str | None = None
    project_root: str | None = None
    base_hash: str | None = None
    connection = open_registry(root)
    try:
        if connection.execute(
            "SELECT 1 FROM works WHERE work_id = ?", (normalized_work_id,)
        ).fetchone():
            raise WorkspaceError(f"work_id already exists: {normalized_work_id}")
        if project_id is not None:
            project = project_row(connection, project_id)
            normalized_project_id = project["project_id"]
            project_root = project["project_root"]
            base_hash = project_state_hash(project_root)
        now = utc_now()
        context = {
            "schema_version": SCHEMA_VERSION,
            "work_id": normalized_work_id,
            "status": "active",
            "project_id": normalized_project_id,
            "project_root": project_root,
            "purpose": purpose.strip(),
            "client": client.strip() or "generic",
            "base_state_hash": base_hash,
            "created_at": now,
            "updated_at": now,
        }
        work_root.mkdir(parents=False, exist_ok=False)
        try:
            for relative in WORK_SUBDIRECTORIES:
                (work_root / relative).mkdir()
            atomic_write_json(work_root / "work.json", context)
            with connection:
                connection.execute(
                    """
                    INSERT INTO works(
                        work_id, work_root, project_id, purpose, client, status,
                        base_state_hash, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        normalized_work_id,
                        str(work_root),
                        normalized_project_id,
                        context["purpose"],
                        context["client"],
                        base_hash,
                        now,
                        now,
                    ),
                )
        except Exception:
            # The directory was created by this call and contains no user input yet.
            shutil.rmtree(work_root, ignore_errors=True)
            raise
        return {
            "status": "created",
            "workspace_root": str(root),
            "work_id": normalized_work_id,
            "work_root": str(work_root),
            "project_id": normalized_project_id,
            "project_root": project_root,
            "base_state_hash": base_hash,
        }
    finally:
        connection.close()


def find_work_context(raw_context: str | Path, workspace_root: Path) -> Path | None:
    context = Path(raw_context).expanduser().resolve()
    if context.is_file():
        context = context.parent
    if not is_within(context, workspace_root):
        return None
    candidates = (context, *context.parents)
    for candidate in candidates:
        if not is_within(candidate, workspace_root):
            break
        if (candidate / "work.json").is_file():
            return candidate
        if candidate == workspace_root:
            break
    return None


def resume_work(raw_workspace: str | Path, work_id: str) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        row = work_row(connection, work_id)
        work_root = Path(row["work_root"]).resolve()
        if work_root.parent != (root / "workspaces").resolve():
            raise WorkspaceError("Registered work directory escapes workspaces/")
        context = read_json(work_root / "work.json")
        if context.get("work_id") != row["work_id"]:
            raise WorkspaceError("work.json does not match the registry work_id")
        if context.get("project_id") != row["project_id"]:
            raise WorkspaceError("work.json project_id does not match the registry")
        if context.get("base_state_hash") != row["base_state_hash"]:
            raise WorkspaceError("work.json base_state_hash does not match the registry")
        if row["status"] != "active":
            raise WorkspaceError(
                f"Work {row['work_id']} is {row['status']}; start a new work context."
            )
        project_root: str | None = None
        if row["project_id"] is not None:
            project_root = project_row(connection, row["project_id"])["project_root"]
            if context.get("project_root") != project_root:
                raise WorkspaceError("work.json project_root does not match the registry")
        return {
            "status": "reused",
            "workspace_root": str(root),
            "work_id": row["work_id"],
            "work_root": str(work_root),
            "project_id": row["project_id"],
            "project_root": project_root,
            "base_state_hash": row["base_state_hash"],
        }
    finally:
        connection.close()


def bind_work(
    raw_workspace: str | Path, work_id: str, project_id: str
) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        work = work_row(connection, work_id)
        if work["status"] != "active":
            raise WorkspaceError(f"Cannot bind a {work['status']} work context")
        project = project_row(connection, project_id)
        if work["project_id"] not in {None, project["project_id"]}:
            raise WorkspaceError(
                f"Work is already bound to project {work['project_id']}"
            )
        work_root = Path(work["work_root"])
        context_path = work_root / "work.json"
        context = read_json(context_path)
        state_hash = project_state_hash(project["project_root"])
        now = utc_now()
        context.update(
            {
                "project_id": project["project_id"],
                "project_root": project["project_root"],
                "base_state_hash": state_hash,
                "updated_at": now,
            }
        )
        atomic_write_json(context_path, context)
        with connection:
            connection.execute(
                """
                UPDATE works
                SET project_id = ?, base_state_hash = ?, updated_at = ?
                WHERE work_id = ?
                """,
                (project["project_id"], state_hash, now, work["work_id"]),
            )
        return {
            "status": "bound",
            "work_id": work["work_id"],
            "work_root": str(work_root),
            "project_id": project["project_id"],
            "project_root": project["project_root"],
            "base_state_hash": state_hash,
        }
    finally:
        connection.close()


def ensure_work(
    raw_workspace: str | Path,
    *,
    context: str | Path | None = None,
    work_id: str | None = None,
    project_id: str | None = None,
    purpose: str = "",
    client: str = "generic",
) -> dict[str, Any]:
    root = require_workspace(raw_workspace, auto_initialize=True)
    if work_id:
        result = resume_work(root, work_id)
    else:
        found = find_work_context(context or Path.cwd(), root)
        if found is not None:
            found_context = read_json(found / "work.json")
            result = resume_work(root, str(found_context.get("work_id", "")))
        else:
            return create_work(
                root,
                project_id=project_id,
                purpose=purpose,
                client=client,
            )
    if project_id is not None:
        if result["project_id"] is None:
            return bind_work(root, result["work_id"], project_id)
        if result["project_id"] != validate_identifier(project_id, "project_id"):
            raise WorkspaceError(
                f"Work is bound to {result['project_id']}, not {project_id}"
            )
    return result


def work_list(raw_workspace: str | Path, *, active_only: bool = False) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        query = "SELECT * FROM works"
        parameters: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE status = ?"
            parameters = ("active",)
        query += " ORDER BY updated_at DESC, work_id"
        works = [dict(row) for row in connection.execute(query, parameters)]
        return {
            "status": "ok",
            "workspace_root": str(root),
            "count": len(works),
            "works": works,
        }
    finally:
        connection.close()


def lease_owner(
    connection: sqlite3.Connection, project_id: str
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM leases WHERE project_id = ?", (project_id,)
    ).fetchone()


def lease_seconds_from_row(owner: sqlite3.Row) -> int:
    """Read the additive lease duration column with old-registry fallback."""

    keys = owner.keys()
    try:
        value = int(owner["lease_seconds"]) if "lease_seconds" in keys else DEFAULT_LEASE_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_LEASE_SECONDS
    return max(30, min(MAX_LEASE_SECONDS, value))


def lease_heartbeat_enforced(owner: sqlite3.Row) -> bool:
    """Whether this row was created/renewed under the heartbeat contract."""

    keys = owner.keys()
    if "heartbeat_enforced" not in keys:
        return False
    value = owner["heartbeat_enforced"]
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        # A malformed marker must not make an old lease look more permissive
        # than its original expires_at-only behavior.
        return False


def lease_live_until(owner: sqlite3.Row) -> datetime:
    expires = parse_timestamp(owner["expires_at"])
    if not lease_heartbeat_enforced(owner):
        return expires
    heartbeat = parse_timestamp(owner["heartbeat_at"])
    heartbeat_deadline = heartbeat + timedelta(
        seconds=min(HEARTBEAT_GRACE_SECONDS, lease_seconds_from_row(owner))
    )
    return min(expires, heartbeat_deadline)


def lease_is_live(owner: sqlite3.Row, *, now: datetime | None = None) -> bool:
    current = now or utc_datetime()
    return lease_live_until(owner) > current


def lease_status(owner: sqlite3.Row, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or utc_datetime()
    heartbeat = parse_timestamp(owner["heartbeat_at"])
    expires = parse_timestamp(owner["expires_at"])
    live_until = lease_live_until(owner)
    return {
        "work_id": owner["work_id"],
        "acquired_at": owner["acquired_at"],
        "heartbeat_at": owner["heartbeat_at"],
        "expires_at": owner["expires_at"],
        "lease_seconds": lease_seconds_from_row(owner),
        "heartbeat_enforced": lease_heartbeat_enforced(owner),
        "heartbeat_age_seconds": max(0, int((current - heartbeat).total_seconds())),
        "expires_in_seconds": int((expires - current).total_seconds()),
        "live_until": live_until.replace(microsecond=0).isoformat(),
        "live": live_until > current,
    }


def record_lease_event(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    event: str,
    reason: str,
    actor_work_id: str | None,
    previous_work_id: str | None,
    previous_expires_at: str | None,
    current_state_hash: str | None,
    state_hash_error: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO lease_events(
            project_id, previous_work_id, event, reason, actor_work_id,
            occurred_at, previous_expires_at, current_state_hash, state_hash_error
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            previous_work_id,
            event,
            reason,
            actor_work_id,
            utc_now(),
            previous_expires_at,
            current_state_hash,
            state_hash_error,
        ),
    )


def acquire_lock(
    raw_workspace: str | Path,
    work_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    if lease_seconds < 30 or lease_seconds > MAX_LEASE_SECONDS:
        raise WorkspaceError(
            f"lease_seconds must be between 30 and {MAX_LEASE_SECONDS}"
        )
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        now_dt = utc_datetime()
        now = now_dt.replace(microsecond=0).isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds)).replace(
            microsecond=0
        ).isoformat()
        connection.execute("BEGIN IMMEDIATE")
        work = work_row(connection, work_id)
        if work["status"] != "active":
            raise WorkspaceError(f"Cannot lock from a {work['status']} work context")
        if work["project_id"] is None:
            raise WorkspaceError("Work must be bound to a project before locking")
        project = project_row(connection, work["project_id"])
        owner = lease_owner(connection, work["project_id"])
        status = "acquired"
        if owner is not None:
            if owner["work_id"] == work["work_id"]:
                status = "renewed"
            elif lease_is_live(owner, now=now_dt):
                raise WorkspaceError(
                    f"Project is locked by {owner['work_id']} until "
                    f"{lease_live_until(owner).replace(microsecond=0).isoformat()}"
                )
            else:
                status = "reclaimed"
        current_hash, hash_version, hash_error = safe_match_project_state_hash(
            project["project_root"], work["base_state_hash"]
        )
        connection.execute(
            """
            INSERT INTO leases(
                project_id, work_id, acquired_at, heartbeat_at, expires_at,
                lease_seconds, heartbeat_enforced
            ) VALUES(?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(project_id) DO UPDATE SET
                work_id=excluded.work_id,
                acquired_at=excluded.acquired_at,
                heartbeat_at=excluded.heartbeat_at,
                expires_at=excluded.expires_at,
                lease_seconds=excluded.lease_seconds,
                heartbeat_enforced=excluded.heartbeat_enforced
            """,
            (
                work["project_id"],
                work["work_id"],
                now,
                now,
                expires,
                lease_seconds,
            ),
        )
        if owner is not None and status in {"renewed", "reclaimed"}:
            record_lease_event(
                connection,
                project_id=work["project_id"],
                previous_work_id=owner["work_id"],
                event=status,
                reason=(
                    "expired_or_stale_heartbeat"
                    if status == "reclaimed"
                    else "lock_acquire_renewal"
                ),
                actor_work_id=work["work_id"],
                previous_expires_at=owner["expires_at"],
                current_state_hash=current_hash,
                state_hash_error=hash_error,
            )
        connection.commit()
        return {
            "status": status,
            "project_id": work["project_id"],
            "work_id": work["work_id"],
            "expires_at": expires,
            "heartbeat_at": now,
            "lease_seconds": lease_seconds,
            "base_state_hash": work["base_state_hash"],
            "current_state_hash": current_hash,
            "state_matches": hash_version is not None,
            "state_hash_version": hash_version,
            "state_hash_error": hash_error,
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def require_live_owned_lease(
    connection: sqlite3.Connection, work: sqlite3.Row
) -> sqlite3.Row:
    if work["project_id"] is None:
        raise WorkspaceError("Work is not bound to a project")
    owner = lease_owner(connection, work["project_id"])
    if owner is None or owner["work_id"] != work["work_id"]:
        raise WorkspaceError("This work context does not own the project write lock")
    if not lease_is_live(owner):
        if parse_timestamp(owner["expires_at"]) <= utc_datetime():
            raise WorkspaceError("The project write lock has expired")
        raise WorkspaceError(
            "The project write lock heartbeat is stale; renew it before writing"
        )
    return owner


def renew_lock(
    raw_workspace: str | Path,
    work_id: str,
    *,
    lease_seconds: int | None = None,
) -> dict[str, Any]:
    """Refresh an existing lease and heartbeat owned by ``work_id``."""

    if lease_seconds is not None and (
        lease_seconds < 30 or lease_seconds > MAX_LEASE_SECONDS
    ):
        raise WorkspaceError(
            f"lease_seconds must be between 30 and {MAX_LEASE_SECONDS}"
        )
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        now_dt = utc_datetime()
        now = now_dt.replace(microsecond=0).isoformat()
        connection.execute("BEGIN IMMEDIATE")
        work = work_row(connection, work_id)
        if work["status"] != "active":
            raise WorkspaceError(f"Cannot renew from a {work['status']} work context")
        if work["project_id"] is None:
            raise WorkspaceError("Work must be bound to a project before renewing")
        owner = lease_owner(connection, work["project_id"])
        if owner is None or owner["work_id"] != work["work_id"]:
            raise WorkspaceError("This work context does not own the project write lock")
        if not lease_is_live(owner, now=now_dt):
            raise WorkspaceError(
                "The project write lock is expired or has a stale heartbeat; acquire it again"
            )
        duration = lease_seconds or lease_seconds_from_row(owner)
        expires = (now_dt + timedelta(seconds=duration)).replace(
            microsecond=0
        ).isoformat()
        project = project_row(connection, work["project_id"])
        current_hash, hash_version, hash_error = safe_match_project_state_hash(
            project["project_root"], work["base_state_hash"]
        )
        connection.execute(
            """
            UPDATE leases
            SET heartbeat_at = ?, expires_at = ?, lease_seconds = ?,
                heartbeat_enforced = 1
            WHERE project_id = ? AND work_id = ?
            """,
            (now, expires, duration, work["project_id"], work["work_id"]),
        )
        record_lease_event(
            connection,
            project_id=work["project_id"],
            previous_work_id=work["work_id"],
            event="renewed",
            reason="explicit_lock_renew",
            actor_work_id=work["work_id"],
            previous_expires_at=owner["expires_at"],
            current_state_hash=current_hash,
            state_hash_error=hash_error,
        )
        connection.commit()
        return {
            "status": "renewed",
            "project_id": work["project_id"],
            "work_id": work["work_id"],
            "heartbeat_at": now,
            "expires_at": expires,
            "lease_seconds": duration,
            "base_state_hash": work["base_state_hash"],
            "current_state_hash": current_hash,
            "state_matches": hash_version is not None,
            "state_hash_version": hash_version,
            "state_hash_error": hash_error,
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def write_check(raw_workspace: str | Path, work_id: str) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        work = work_row(connection, work_id)
        owner = require_live_owned_lease(connection, work)
        project = project_row(connection, work["project_id"])
        if not work["base_state_hash"]:
            raise WorkspaceError("Work context has no base_state_hash")
        try:
            current_hash, hash_version = match_project_state_hash(
                project["project_root"], work["base_state_hash"]
            )
        except Exception as exc:
            raise WorkspaceError(f"Unable to verify project state hash: {exc}") from exc
        if hash_version is None:
            raise WorkspaceError(
                "Project changed after this work context was bound. Re-read the "
                "project, resolve differences, then refresh the base hash before writing."
            )
        return {
            "status": "pass",
            "work_id": work["work_id"],
            "project_id": work["project_id"],
            "project_root": project["project_root"],
            "state_hash": current_hash,
            "state_hash_version": hash_version,
            "legacy_hash_accepted": hash_version == LEGACY_STATE_HASH_VERSION,
            "lease": lease_status(owner),
        }
    finally:
        connection.close()


@contextlib.contextmanager
def write_guard(
    raw_workspace: str | Path,
    work_id: str,
    *,
    expected_project_root: str | Path | None = None,
):
    """Hold the project write transaction across a canonical file commit.

    ``write_check`` is a point-in-time assertion.  This guard upgrades it to a
    reservation: ``BEGIN IMMEDIATE`` prevents another official writer from
    reclaiming the lease between the final check and the atomic file replace.
    The caller should invoke ``guard.assert_live()`` from its post-write
    validator so an expired lease rolls the file transaction back.
    """

    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        work = work_row(connection, work_id)
        owner = require_live_owned_lease(connection, work)
        project = project_row(connection, work["project_id"])
        project_root = Path(project["project_root"]).resolve()
        if expected_project_root is not None:
            expected = Path(expected_project_root).expanduser().resolve()
            if expected != project_root:
                raise WorkspaceError(
                    "The work lease belongs to a different project; refusing to write"
                )
        if not work["base_state_hash"]:
            raise WorkspaceError("Work context has no base_state_hash")
        try:
            current_hash, hash_version = match_project_state_hash(
                project_root, work["base_state_hash"]
            )
        except Exception as exc:
            raise WorkspaceError(f"Unable to verify project state hash: {exc}") from exc
        if hash_version is None:
            raise WorkspaceError(
                "Project changed after this work context was bound. Re-read the "
                "project, resolve differences, then refresh the base hash before writing."
            )
        guard = WriteGuard(
            connection,
            work_id=work["work_id"],
            project_id=work["project_id"],
            project_root=str(project_root),
            state_hash=current_hash,
            hash_version=hash_version,
        )
        # Keep the local owner assertion explicit: it also documents that the
        # lease was live at the exact point the SQLite write reservation began.
        _ = owner
        yield guard
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def refresh_base(
    raw_workspace: str | Path, work_id: str, validation_reference: str
) -> dict[str, Any]:
    reference = validation_reference.strip()
    if not reference:
        raise WorkspaceError("validation_reference cannot be empty")
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        work = work_row(connection, work_id)
        require_live_owned_lease(connection, work)
        project = project_row(connection, work["project_id"])
        state_hash = project_state_hash(project["project_root"])
        now = utc_now()
        context_path = Path(work["work_root"]) / "work.json"
        context = read_json(context_path)
        context["base_state_hash"] = state_hash
        context["last_base_refresh"] = {
            "validation_reference": reference,
            "refreshed_at": now,
        }
        context["updated_at"] = now
        atomic_write_json(context_path, context)
        with connection:
            connection.execute(
                "UPDATE works SET base_state_hash = ?, updated_at = ? WHERE work_id = ?",
                (state_hash, now, work["work_id"]),
            )
        return {
            "status": "refreshed",
            "work_id": work["work_id"],
            "project_id": work["project_id"],
            "base_state_hash": state_hash,
            "validation_reference": reference,
        }
    finally:
        connection.close()


def release_lock(raw_workspace: str | Path, work_id: str) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        work = work_row(connection, work_id)
        if work["project_id"] is None:
            raise WorkspaceError("Work is not bound to a project")
        owner = lease_owner(connection, work["project_id"])
        if owner is None:
            connection.commit()
            return {
                "status": "already_released",
                "work_id": work["work_id"],
                "project_id": work["project_id"],
            }
        if owner["work_id"] != work["work_id"]:
            raise WorkspaceError(
                f"Project lock belongs to {owner['work_id']}, not {work['work_id']}"
            )
        project = project_row(connection, work["project_id"])
        current_hash, hash_error = safe_project_state_hash(project["project_root"])
        connection.execute(
            "DELETE FROM leases WHERE project_id = ?", (work["project_id"],)
        )
        record_lease_event(
            connection,
            project_id=work["project_id"],
            previous_work_id=owner["work_id"],
            event="released",
            reason="owner_release",
            actor_work_id=work["work_id"],
            previous_expires_at=owner["expires_at"],
            current_state_hash=current_hash,
            state_hash_error=hash_error,
        )
        connection.commit()
        return {
            "status": "released",
            "work_id": work["work_id"],
            "project_id": work["project_id"],
            "current_state_hash": current_hash,
            "state_hash_error": hash_error,
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def break_lock(
    raw_workspace: str | Path,
    project_id: str,
    *,
    expected_owner: str,
    reason: str,
) -> dict[str, Any]:
    """Recover an abandoned lease with an explicit owner and audit reason.

    A valid live lease can never be broken.  This prevents a convenience
    command from becoming an unreviewed override of another writer.
    """

    normalized_owner = validate_identifier(expected_owner, "expected_owner")
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise WorkspaceError("lock-break requires a non-empty reason")
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        project = project_row(connection, project_id)
        owner = lease_owner(connection, project["project_id"])
        if owner is None:
            connection.commit()
            return {
                "status": "already_released",
                "project_id": project["project_id"],
                "expected_owner": normalized_owner,
                "reason": normalized_reason,
            }
        if owner["work_id"] != normalized_owner:
            raise WorkspaceError(
                f"Expected lock owner {normalized_owner}, but current owner is {owner['work_id']}"
            )
        now_dt = utc_datetime()
        if lease_is_live(owner, now=now_dt):
            raise WorkspaceError(
                "Refusing to break a live project lock; wait for expiry or ask its owner to release it"
            )
        current_hash, hash_error = safe_project_state_hash(project["project_root"])
        connection.execute(
            "DELETE FROM leases WHERE project_id = ?", (project["project_id"],)
        )
        record_lease_event(
            connection,
            project_id=project["project_id"],
            previous_work_id=owner["work_id"],
            event="broken",
            reason=normalized_reason,
            actor_work_id=None,
            previous_expires_at=owner["expires_at"],
            current_state_hash=current_hash,
            state_hash_error=hash_error,
        )
        connection.commit()
        return {
            "status": "broken",
            "project_id": project["project_id"],
            "previous_owner": owner["work_id"],
            "previous_expires_at": owner["expires_at"],
            "previous_heartbeat_at": owner["heartbeat_at"],
            "reason": normalized_reason,
            "current_state_hash": current_hash,
            "state_hash_error": hash_error,
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def close_work(raw_workspace: str | Path, work_id: str) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        work = work_row(connection, work_id)
        if work["project_id"] is not None:
            owner = lease_owner(connection, work["project_id"])
            if owner is not None and owner["work_id"] == work["work_id"]:
                raise WorkspaceError("Release the project write lock before closing work")
        now = utc_now()
        context_path = Path(work["work_root"]) / "work.json"
        context = read_json(context_path)
        context["status"] = "closed"
        context["updated_at"] = now
        atomic_write_json(context_path, context)
        with connection:
            connection.execute(
                "UPDATE works SET status = 'closed', updated_at = ? WHERE work_id = ?",
                (now, work["work_id"]),
            )
        return {
            "status": "closed",
            "work_id": work["work_id"],
            "work_root": work["work_root"],
        }
    finally:
        connection.close()


def workspace_status(raw_workspace: str | Path) -> dict[str, Any]:
    root = require_workspace(raw_workspace)
    connection = open_registry(root)
    try:
        projects = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        active_works = connection.execute(
            "SELECT COUNT(*) FROM works WHERE status = 'active'"
        ).fetchone()[0]
        closed_works = connection.execute(
            "SELECT COUNT(*) FROM works WHERE status = 'closed'"
        ).fetchone()[0]
        leases = []
        for row in connection.execute("SELECT * FROM leases"):
            item = dict(row)
            item["lease_status"] = lease_status(row)
            leases.append(item)
        return {
            "status": "ok",
            "workspace_root": str(root),
            "projects": projects,
            "active_works": active_works,
            "closed_works": closed_works,
            "leases": leases,
            "registry": str(root / "registry.sqlite3"),
        }
    finally:
        connection.close()


DOCTOR_MODULES = (
    "novel_cli",
    "novel_project",
    "novel_workspace",
    "novel_continuity",
    "novel_export",
    "novel_memory",
    "novel_originality",
    "novel_research",
    "novel_review",
)


def _doctor_check(
    checks: list[dict[str, Any]], name: str, status: str, detail: str, **extra: Any
) -> None:
    checks.append({"name": name, "status": status, "detail": detail, **extra})


def open_read_only_registry(registry: Path) -> sqlite3.Connection:
    """Open a registry without writes while still honoring an active WAL.

    SQLite's ``immutable=1`` mode intentionally ignores ``-wal`` files.  It is
    safe for a fully checkpointed database, but would make a live workspace
    look incomplete.  Use it only when no WAL sidecar is present; otherwise a
    normal ``mode=ro`` connection with ``query_only`` reads the current WAL
    without permitting SQL writes.
    """

    registry = Path(registry).expanduser().resolve()
    wal_path = Path(f"{registry}-wal")
    shm_path = Path(f"{registry}-shm")
    query = "mode=ro"
    if not wal_path.exists() and not shm_path.exists():
        query += "&immutable=1"
    # ``as_uri`` percent-encodes spaces, ``#`` and ``%`` in Windows paths;
    # embedding ``as_posix()`` directly would make SQLite parse them as URI
    # syntax instead of filename characters.
    uri = f"{registry.as_uri()}?{query}"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    except Exception:
        connection.close()
        raise


def _humanizer_candidates(scripts_root: Path) -> list[Path]:
    """Return likely installed humanizer skill locations in preference order."""

    configured = os.environ.get("NOVEL_HUMANIZER_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        try:
            configured_path = configured_path.resolve()
        except OSError:
            # Preserve the path for a useful diagnostic; the candidate check
            # below will report the actual stat/read failure.
            configured_path = configured_path.absolute()
        return [configured_path]

    candidates = [scripts_root.parent.parent / "humanizer-zh"]
    # Skills can be installed under either supported user skill root.  The
    # local sibling comes first, then the alternate root for cross-install
    # portability (for example novel-studio in .codex and humanizer-zh in
    # .agents).
    home = Path.home()
    candidates.extend(
        [
            home / ".agents" / "skills" / "humanizer-zh",
            home / ".codex" / "skills" / "humanizer-zh",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _check_humanizer_candidate(candidate: Path) -> tuple[Path, bool, str]:
    """Validate a candidate without executing or modifying the skill."""

    try:
        candidate_is_dir = candidate.is_dir()
        candidate_is_file = candidate.is_file()
    except OSError as exc:
        return (
            candidate,
            False,
            f"path cannot be inspected ({type(exc).__name__}: {exc})",
        )
    if candidate_is_dir:
        skill_file = candidate / "SKILL.md"
    elif candidate_is_file:
        if candidate.name.casefold() != "skill.md":
            return (
                candidate,
                False,
                "configured path must be a directory containing SKILL.md or a file named SKILL.md",
            )
        skill_file = candidate
    else:
        return candidate, False, "path does not exist"

    try:
        skill_file_exists = skill_file.is_file()
    except OSError as exc:
        return (
            skill_file,
            False,
            f"SKILL.md cannot be inspected ({type(exc).__name__}: {exc})",
        )
    if not skill_file_exists:
        return skill_file, False, "SKILL.md is missing or is not a regular file"
    try:
        content = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return (
            skill_file,
            False,
            f"SKILL.md is not readable ({type(exc).__name__}: {exc})",
        )
    frontmatter = re.match(
        r"\A---\s*\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|\Z)",
        content,
        re.DOTALL,
    )
    if frontmatter is None or re.search(
        r"(?m)^\s*name\s*:\s*['\"]?humanizer-zh['\"]?\s*$",
        frontmatter.group("body"),
    ) is None:
        return (
            skill_file,
            False,
            "SKILL.md frontmatter must declare name: humanizer-zh",
        )
    return skill_file, True, "readable SKILL.md with name: humanizer-zh"


def resolve_humanizer_skill(scripts_root: Path) -> tuple[Path, bool, str]:
    """Find and validate the configured or installed humanizer skill."""

    candidates = _humanizer_candidates(scripts_root)
    failures: list[str] = []
    for candidate in candidates:
        skill_file, valid, detail = _check_humanizer_candidate(candidate)
        if valid:
            return skill_file, True, detail
        failures.append(f"{skill_file}: {detail}")
    fallback = candidates[0] if candidates else scripts_root / "humanizer-zh"
    return fallback, False, "No usable humanizer-zh found; " + "; ".join(failures)


def doctor(raw_workspace: str | Path | None = None) -> dict[str, Any]:
    """Run a read-only environment and workspace health check.

    With no workspace argument this deliberately avoids creating directories or
    SQLite files and only checks the runtime/tool installation.
    """

    checks: list[dict[str, Any]] = []
    python_ok = sys.version_info >= (3, 10)
    _doctor_check(
        checks,
        "python",
        "pass" if python_ok else "fail",
        f"Python {sys.version.split()[0]} (requires >= 3.10)",
        version=sys.version.split()[0],
    )
    sqlite_ok = False
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("SELECT 1").fetchone()
            sqlite_ok = True
        finally:
            connection.close()
    except sqlite3.Error as exc:
        _doctor_check(checks, "sqlite", "fail", str(exc))
    if sqlite_ok:
        _doctor_check(
            checks,
            "sqlite",
            "pass",
            f"SQLite {sqlite3.sqlite_version} is usable",
            version=sqlite3.sqlite_version,
        )
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    _doctor_check(
        checks,
        "stdout-encoding",
        "pass" if encoding in {"utf-8", "utf8"} else "warning",
        f"stdout encoding is {encoding or 'unknown'}; UTF-8 JSON is enforced by the CLI",
        encoding=encoding or None,
    )

    scripts_root = Path(__file__).resolve().parent
    imported: list[str] = []
    original_path = list(sys.path)
    if str(scripts_root) not in sys.path:
        sys.path.insert(0, str(scripts_root))
    try:
        importlib.invalidate_caches()
        for module_name in DOCTOR_MODULES:
            try:
                importlib.import_module(module_name)
                imported.append(module_name)
            except Exception as exc:  # pragma: no cover - environment-specific
                _doctor_check(
                    checks,
                    f"import:{module_name}",
                    "fail",
                    f"{type(exc).__name__}: {exc}",
                )
    finally:
        sys.path[:] = original_path
    for module_name in imported:
        _doctor_check(checks, f"import:{module_name}", "pass", "import succeeded")

    humanizer_path, humanizer_ok, humanizer_detail = resolve_humanizer_skill(
        scripts_root
    )
    _doctor_check(
        checks,
        "humanizer-zh",
        "pass" if humanizer_ok else "fail",
        humanizer_detail,
        path=str(humanizer_path),
        formal_work_blocked=not humanizer_ok,
    )

    workspace_result: dict[str, Any] | None = None
    if raw_workspace is None:
        _doctor_check(
            checks,
            "workspace",
            "skipped",
            "No workspace supplied; no filesystem state was opened or created",
        )
    else:
        try:
            root = resolve_root(raw_workspace)
            config_path = workspace_config_path(root)
            if not config_path.is_file():
                raise WorkspaceError(f"Missing workspace.json: {config_path}")
            config = read_json(config_path)
            if config.get("schema_version") != SCHEMA_VERSION:
                raise WorkspaceError(
                    f"workspace.json schema_version {config.get('schema_version')!r} "
                    f"is unsupported (expected {SCHEMA_VERSION})"
                )
            if config.get("kind") != WORKSPACE_KIND:
                raise WorkspaceError("workspace.json kind is not a Chinese novel workspace")
            missing_dirs = [
                relative
                for relative in ("projects", "workspaces")
                if not (root / relative).is_dir()
            ]
            if missing_dirs:
                raise WorkspaceError(
                    "Missing workspace directories: " + ", ".join(missing_dirs)
                )
            registry = root / "registry.sqlite3"
            if not registry.is_file():
                raise WorkspaceError(f"Missing registry: {registry}")
            connection = open_read_only_registry(registry)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise WorkspaceError(f"Registry integrity check failed: {integrity}")
                schema_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
                if schema_row is None:
                    raise WorkspaceError("Registry metadata is missing schema_version")
                _registry_schema_version_value = _parse_registry_schema_value(
                    schema_row[0]
                )
                if _registry_schema_version_value != SCHEMA_VERSION:
                    if _registry_schema_version_value in REGISTRY_LEGACY_SCHEMA_VERSIONS:
                        raise WorkspaceError(
                            f"Registry schema_version {_registry_schema_version_value} "
                            f"requires a writable migration to {SCHEMA_VERSION}; "
                            "doctor is read-only"
                        )
                    raise WorkspaceError(
                        f"Registry schema_version {_registry_schema_version_value} "
                        f"is unsupported (expected {SCHEMA_VERSION})"
                    )
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                required_tables = set(REQUIRED_REGISTRY_COLUMNS)
                missing_tables = sorted(required_tables - tables)
                if missing_tables:
                    raise WorkspaceError(
                        "Registry is missing tables: " + ", ".join(missing_tables)
                    )
                missing_columns = {
                    table: sorted(
                        required - _table_columns(connection, table)
                    )
                    for table, required in REQUIRED_REGISTRY_COLUMNS.items()
                }
                missing_columns = {
                    table: columns
                    for table, columns in missing_columns.items()
                    if columns
                }
                if missing_columns:
                    details = "; ".join(
                        f"{table}: {', '.join(columns)}"
                        for table, columns in sorted(missing_columns.items())
                    )
                    raise WorkspaceError(
                        "Registry is missing columns: " + details
                    )
            finally:
                connection.close()
            _doctor_check(
                checks,
                "workspace",
                "pass",
                f"Healthy read-only workspace: {root}",
                path=str(root),
                schema_version=config.get("schema_version"),
            )
            workspace_result = {"path": str(root), "schema_version": config.get("schema_version")}
        except Exception as exc:
            _doctor_check(
                checks,
                "workspace",
                "fail",
                str(exc),
                error_type=type(exc).__name__,
            )

    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warning"]
    overall = "blocked" if failures else "warning" if warnings else "pass"
    return {
        "status": overall,
        "read_only": True,
        "tool_version": novel_cli.TOOL_VERSION,
        "workspace": workspace_result,
        "checks": checks,
        "failure_count": len(failures),
        "warning_count": len(warnings),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(
        description=(
            "Create isolated, platform-independent work contexts for stateful "
            "Chinese novel projects."
        )
    )
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="Read-only runtime and workspace health check."
    )
    doctor_parser.add_argument(
        "workspace",
        nargs="?",
        help="Optional workspace root; omitted means environment-only checks.",
    )

    init = subparsers.add_parser("init", help="Initialize a workspace root.")
    init.add_argument("workspace")
    init.add_argument("--adopt-existing", action="store_true")

    status = subparsers.add_parser("status", help="Summarize workspace state.")
    status.add_argument("workspace")

    project_register = subparsers.add_parser(
        "project-register", help="Register an initialized project under projects/."
    )
    project_register.add_argument("workspace")
    project_register.add_argument("project_root")
    project_register.add_argument("--project-id")

    project_create = subparsers.add_parser(
        "project-create", help="Create and register a novel or short-story project."
    )
    project_create.add_argument("workspace")
    project_create.add_argument("--title", required=True)
    project_create.add_argument("--genre", default="")
    project_create.add_argument("--language", default="zh-CN")
    project_create.add_argument(
        "--work-type",
        choices=sorted(novel_project.WORK_TYPES),
        default="serial_novel",
    )
    project_create.add_argument("--target-words", type=int)
    project_create.add_argument("--project-id")
    project_create.add_argument(
        "--short-story-slug",
        help=(
            "One to three semantic English words used when generating a "
            "shortstory-<slug>-YYYYMMDD project id."
        ),
    )

    project_list_parser = subparsers.add_parser(
        "project-list", help="List registered projects."
    )
    project_list_parser.add_argument("workspace")

    work_start = subparsers.add_parser(
        "work-start", help="Create a new isolated work directory."
    )
    work_start.add_argument("workspace")
    work_start.add_argument("--project-id")
    work_start.add_argument("--purpose", default="")
    work_start.add_argument("--client", default="generic")
    work_start.add_argument("--work-id")

    work_ensure = subparsers.add_parser(
        "work-ensure", help="Reuse a valid context or create a work directory first."
    )
    work_ensure.add_argument("workspace")
    work_ensure.add_argument("--context")
    work_ensure.add_argument("--work-id")
    work_ensure.add_argument("--project-id")
    work_ensure.add_argument("--purpose", default="")
    work_ensure.add_argument("--client", default="generic")

    work_bind = subparsers.add_parser(
        "work-bind", help="Bind an unbound work context to a project."
    )
    work_bind.add_argument("workspace")
    work_bind.add_argument("work_id")
    work_bind.add_argument("--project-id", required=True)

    work_resume = subparsers.add_parser(
        "work-resume", help="Resume an active work context by id."
    )
    work_resume.add_argument("workspace")
    work_resume.add_argument("work_id")

    work_list_parser = subparsers.add_parser(
        "work-list", help="List known work contexts."
    )
    work_list_parser.add_argument("workspace")
    work_list_parser.add_argument("--active-only", action="store_true")

    work_close = subparsers.add_parser(
        "work-close", help="Close a work context without deleting its files."
    )
    work_close.add_argument("workspace")
    work_close.add_argument("work_id")

    lock_acquire = subparsers.add_parser(
        "lock-acquire", help="Acquire or renew the single-writer project lease."
    )
    lock_acquire.add_argument("workspace")
    lock_acquire.add_argument("work_id")
    lock_acquire.add_argument(
        "--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS
    )

    lock_renew = subparsers.add_parser(
        "lock-renew", help="Refresh the heartbeat and expiry of an owned lease."
    )
    lock_renew.add_argument("workspace")
    lock_renew.add_argument("work_id")
    lock_renew.add_argument("--lease-seconds", type=int)

    check = subparsers.add_parser(
        "write-check", help="Require a live owned lease and unchanged project hash."
    )
    check.add_argument("workspace")
    check.add_argument("work_id")

    refresh = subparsers.add_parser(
        "base-refresh", help="Refresh the work base hash after a validated write."
    )
    refresh.add_argument("workspace")
    refresh.add_argument("work_id")
    refresh.add_argument("--validation-reference", required=True)

    lock_release = subparsers.add_parser(
        "lock-release", help="Release a project write lease owned by this work."
    )
    lock_release.add_argument("workspace")
    lock_release.add_argument("work_id")

    lock_break = subparsers.add_parser(
        "lock-break",
        help="Recover an expired/stale lease with explicit owner and audit reason.",
    )
    lock_break.add_argument("workspace")
    lock_break.add_argument("project_id")
    lock_break.add_argument("--expected-owner", required=True)
    lock_break.add_argument("--reason", required=True)
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        if args.command == "doctor":
            result = doctor(args.workspace)
            return result, 0 if result["status"] == "pass" else 1
        if args.command == "init":
            return initialize_workspace(
                args.workspace, adopt_existing=args.adopt_existing
            )
        if args.command == "status":
            return workspace_status(args.workspace)
        if args.command == "project-register":
            return register_project(
                args.workspace, args.project_root, project_id=args.project_id
            )
        if args.command == "project-create":
            return create_project(
                args.workspace,
                title=args.title,
                genre=args.genre,
                language=args.language,
                work_type=args.work_type,
                target_words=args.target_words,
                project_id=args.project_id,
                short_story_slug=args.short_story_slug,
            )
        if args.command == "project-list":
            return project_list(args.workspace)
        if args.command == "work-start":
            return create_work(
                args.workspace,
                project_id=args.project_id,
                purpose=args.purpose,
                client=args.client,
                work_id=args.work_id,
            )
        if args.command == "work-ensure":
            return ensure_work(
                args.workspace,
                context=args.context,
                work_id=args.work_id,
                project_id=args.project_id,
                purpose=args.purpose,
                client=args.client,
            )
        if args.command == "work-bind":
            return bind_work(args.workspace, args.work_id, args.project_id)
        if args.command == "work-resume":
            return resume_work(args.workspace, args.work_id)
        if args.command == "work-list":
            return work_list(args.workspace, active_only=args.active_only)
        if args.command == "work-close":
            return close_work(args.workspace, args.work_id)
        if args.command == "lock-acquire":
            return acquire_lock(
                args.workspace, args.work_id, lease_seconds=args.lease_seconds
            )
        if args.command == "lock-renew":
            return renew_lock(
                args.workspace, args.work_id, lease_seconds=args.lease_seconds
            )
        if args.command == "write-check":
            return write_check(args.workspace, args.work_id)
        if args.command == "base-refresh":
            return refresh_base(
                args.workspace, args.work_id, args.validation_reference
            )
        if args.command == "lock-break":
            return break_lock(
                args.workspace,
                args.project_id,
                expected_owner=args.expected_owner,
                reason=args.reason,
            )
        return release_lock(args.workspace, args.work_id)

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_workspace",
        domain_errors=(WorkspaceError, OSError, sqlite3.Error),
    )


if __name__ == "__main__":
    sys.exit(main())
