#!/usr/bin/env python3
"""Collect public platform data and register authorized local novel sources."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import mimetypes
import os
import re
import stat
import sys
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import novel_cli


SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MANIFEST_RELATIVE = Path("research/source-manifest.jsonl")
PLATFORM_RELATIVE = Path("research/platform.json")
ORIGINS = frozenset({"skill_collected", "project_existing", "user_local"})
RIGHTS_STATUSES = frozenset(
    {"public_web", "public_domain", "licensed", "user_authorized", "unknown"}
)
EXTERNAL_USES = frozenset({"permitted", "local_only", "prohibited"})
SOURCE_ID = re.compile(r"^SRC-[0-9A-F]{10}-[0-9A-F]{6}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ResearchError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_project(raw_root: str) -> Path:
    raw = Path(os.path.abspath(Path(raw_root).expanduser()))
    if _path_chain_has_link(raw, Path(raw.anchor)):
        raise ResearchError(
            f"Project path cannot traverse a symbolic link or junction: {raw}"
        )
    root = raw.resolve()
    if _path_chain_has_link(root, Path(root.anchor)):
        raise ResearchError(
            f"Resolved project path cannot traverse a symbolic link or junction: {root}"
        )
    manifest = root / "novel.json"
    if _path_chain_has_link(manifest, root) or not manifest.is_file():
        raise ResearchError(f"Not an initialized novel project: {root}")
    return root


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(read_stable_file(path, label="Source file")).hexdigest()


def read_stable_file(path: Path, *, label: str) -> bytes:
    """Read one regular file while detecting replacement during the read."""

    raw = Path(path).expanduser()
    if _path_chain_has_link(raw, Path(raw.anchor)):
        raise ResearchError(f"{label} cannot traverse a symbolic link or junction: {raw}")
    try:
        with raw.open("rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read(MAX_RESPONSE_BYTES + 1)
            after = os.fstat(handle.fileno())
        path_stat = raw.stat()
    except OSError as exc:
        raise ResearchError(f"Unable to read {label}: {raw}: {exc}") from exc
    if len(content) > MAX_RESPONSE_BYTES:
        raise ResearchError(
            f"{label} exceeds the {MAX_RESPONSE_BYTES}-byte safety limit: {raw}"
        )
    if not raw.is_file() or _link_like(raw):
        raise ResearchError(f"{label} is not a regular file: {raw}")
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
        raise ResearchError(f"{label} changed while being read: {raw}")
    return content


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def project_write_context(
    root: Path,
    *,
    workspace: str | Path | None = None,
    work_id: str | None = None,
    allow_bootstrap: bool = False,
):
    try:
        import novel_workspace

        @contextlib.contextmanager
        def _context():
            try:
                with novel_workspace.project_write_context(
                    root,
                    workspace=workspace,
                    work_id=work_id,
                    allow_bootstrap=allow_bootstrap,
                ) as context:
                    yield context
            except novel_workspace.WorkspaceError as exc:
                raise ResearchError(str(exc)) from exc

        return _context()
    except (ImportError, OSError) as exc:
        raise ResearchError(f"Project write authorization module unavailable: {exc}") from exc


def read_manifest(root: Path) -> list[dict[str, Any]]:
    records, _ = read_manifest_snapshot(root)
    return records


def read_manifest_snapshot(root: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Read the manifest once and return its bytes hash as a write receipt."""

    path = root / MANIFEST_RELATIVE
    if _path_chain_has_link(path, root):
        raise ResearchError("Source manifest cannot traverse a symbolic link or junction")
    if not path.exists():
        return [], None
    records: list[dict[str, Any]] = []
    raw_content = read_stable_file(path, label="Source manifest")
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResearchError(f"Source manifest is not UTF-8: {exc}") from exc
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ResearchError(
                f"Invalid JSONL at {MANIFEST_RELATIVE.as_posix()}:{line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise ResearchError(
                f"Expected an object at {MANIFEST_RELATIVE.as_posix()}:{line_number}"
            )
        records.append(record)
    return records, hashlib.sha256(raw_content).hexdigest()


def manifest_bytes(records: list[dict[str, Any]]) -> bytes:
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    return content.encode("utf-8")


def _link_like(path: Path) -> bool:
    """Return whether a path is a symlink, junction, or Windows reparse point."""

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
        # A path that cannot be inspected is unsafe to follow during a
        # provenance operation. Callers will report the boundary violation.
        return True


def _path_chain_has_link(path: Path, stop: Path) -> bool:
    current = path
    while True:
        if _link_like(current):
            return True
        if current == stop or current.parent == current:
            return False
        current = current.parent


def _iso8601_with_timezone(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def source_record_issues(
    root: Path,
    record: Any,
    *,
    label: str,
    verify_file: bool = True,
) -> tuple[list[str], list[str]]:
    """Validate one provenance record and its project-local source."""

    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return [f"{label}: record must be an object"], warnings
    if type(record.get("schema_version")) is not int or record.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        errors.append(f"{label}: schema_version must be {SCHEMA_VERSION}")
    source_id = record.get("source_id")
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        errors.append(f"{label}: source_id has an invalid format")
    digest = record.get("sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append(f"{label}: sha256 must be 64 lowercase hex characters")
    if record.get("origin") not in ORIGINS:
        errors.append(f"{label}: origin is unsupported")
    if record.get("rights_status") not in RIGHTS_STATUSES:
        errors.append(f"{label}: rights_status is unsupported")
    if record.get("external_use") not in EXTERNAL_USES:
        errors.append(f"{label}: external_use is unsupported")
    for key in ("source_kind", "authorization_scope", "media_type", "original_name"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: {key} must be a non-empty string")
    original_name = record.get("original_name")
    if isinstance(original_name, str) and (
        Path(original_name).name != original_name
        or "/" in original_name
        or "\\" in original_name
    ):
        errors.append(f"{label}: original_name must be a basename")
    if not isinstance(record.get("originality_compare"), bool):
        errors.append(f"{label}: originality_compare must be boolean")
    size = record.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        errors.append(f"{label}: size_bytes must be a non-negative integer")
    if not _iso8601_with_timezone(record.get("registered_at")):
        errors.append(f"{label}: registered_at must be timezone-aware ISO-8601")
    observed_at = record.get("observed_at")
    if observed_at is not None and not _iso8601_with_timezone(observed_at):
        errors.append(f"{label}: observed_at must be timezone-aware ISO-8601 or null")
    for key in ("platform", "source_url", "authorization_reference", "provenance_note"):
        value = record.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{label}: {key} must be a string or null")

    origin = record.get("origin")
    rights_status = record.get("rights_status")
    external_use = record.get("external_use")
    authorization_scope = record.get("authorization_scope")
    authorization_reference = record.get("authorization_reference")
    scope_text = (
        authorization_scope.casefold()
        if isinstance(authorization_scope, str)
        else ""
    )
    scope_allows_originality = "originality" in scope_text
    scope_allows_external = any(
        token in scope_text
        for token in ("external_processing", "third_party", "remote", "upload")
    )
    if origin == "user_local":
        if rights_status not in {"public_domain", "licensed", "user_authorized"}:
            errors.append(f"{label}: user_local rights_status is not authorized")
        reference = record.get("authorization_reference")
        if not isinstance(reference, str) or not reference.strip():
            errors.append(f"{label}: user_local source lacks authorization_reference")
    if origin == "skill_collected":
        if rights_status not in {"public_web", "public_domain"}:
            errors.append(f"{label}: skill_collected source has unsupported rights_status")
        if not isinstance(record.get("source_url"), str) or not record["source_url"].strip():
            errors.append(f"{label}: skill_collected source requires source_url")
        else:
            parsed_url = urllib.parse.urlparse(record["source_url"].strip())
            if (
                parsed_url.scheme.lower() not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username
                or parsed_url.password
            ):
                errors.append(f"{label}: skill_collected source_url must be a public http(s) URL")
        if not _iso8601_with_timezone(record.get("observed_at")):
            errors.append(f"{label}: skill_collected source requires observed_at")
    if external_use == "local_only" and scope_allows_external:
        errors.append(f"{label}: local_only source authorizes external processing")
    if external_use == "permitted":
        if not isinstance(authorization_reference, str) or not authorization_reference.strip():
            errors.append(f"{label}: permitted external use requires authorization_reference")
        if not isinstance(authorization_scope, str) or not authorization_scope.strip():
            errors.append(f"{label}: permitted external use requires authorization_scope")
    if external_use == "prohibited" and record.get("originality_compare") is True:
        errors.append(f"{label}: prohibited external_use cannot enable originality comparison")
    if record.get("originality_compare") is True:
        if rights_status == "unknown":
            errors.append(f"{label}: originality comparison requires known rights_status")
        if not scope_allows_originality:
            errors.append(
                f"{label}: originality comparison requires an authorization_scope containing originality"
            )
        if external_use == "prohibited":
            errors.append(f"{label}: originality comparison is prohibited by external_use")
    if rights_status == "unknown":
        warnings.append(f"{label}: rights_status is unknown")

    relative = record.get("path")
    target: Path | None = None
    if not isinstance(relative, str) or not relative:
        errors.append(f"{label}: path must be a non-empty string")
    else:
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or "." in pure.parts
            or not pure.parts
            or pure.parts[0] != "sources"
            or pure.as_posix() != relative
        ):
            errors.append(f"{label}: path must be a normalized path under sources/")
        else:
            raw_target = root.joinpath(*pure.parts)
            sources_root = (root / "sources").resolve()
            if _link_like(sources_root):
                errors.append(f"{label}: sources/ must not be a link or junction")
            if _path_chain_has_link(raw_target, root):
                errors.append(f"{label}: source path cannot traverse a link or junction")
            else:
                target = raw_target.resolve()
                if not is_within(target, sources_root):
                    errors.append(f"{label}: source path escapes sources/")
    if verify_file and target is not None:
        if _link_like(target) or not target.is_file():
            errors.append(f"{label}: missing file {relative}")
        else:
            if isinstance(digest, str) and SHA256.fullmatch(digest):
                if sha256_file(target) != digest:
                    errors.append(f"{label}: SHA-256 mismatch for {relative}")
            if isinstance(size, int) and not isinstance(size, bool):
                if target.stat().st_size != size:
                    errors.append(f"{label}: size_bytes mismatch for {relative}")
    if (
        isinstance(source_id, str)
        and SOURCE_ID.fullmatch(source_id)
        and isinstance(digest, str)
        and SHA256.fullmatch(digest)
        and isinstance(relative, str)
        and not any(
            issue.startswith(f"{label}: path must be")
            for issue in errors
        )
    ):
        try:
            expected_id = source_id_for(digest, relative)
        except ResearchError:
            expected_id = None
        if expected_id is not None and source_id != expected_id:
            errors.append(f"{label}: source_id does not match sha256 and path")
    return errors, warnings


def source_id_for(digest: str, relative_path: str) -> str:
    """Derive the stable source identifier used by every manifest writer."""

    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ResearchError("source_id requires a lowercase SHA-256 digest")
    pure = PurePosixPath(relative_path)
    if (
        not isinstance(relative_path, str)
        or "\\" in relative_path
        or pure.is_absolute()
        or not pure.parts
        or "." in pure.parts
        or ".." in pure.parts
        or pure.as_posix() != relative_path
    ):
        raise ResearchError("source_id requires a normalized POSIX relative path")
    suffix = hashlib.sha256(f"{digest}:{relative_path}".encode("utf-8")).hexdigest()[:6].upper()
    return f"SRC-{digest[:10].upper()}-{suffix}"


def validate_manifest_records(
    root: Path, records: list[dict[str, Any]], *, verify_files: bool = True
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, record in enumerate(records, start=1):
        label = (
            str(record.get("source_id") or f"line {index}")
            if isinstance(record, dict)
            else f"line {index}"
        )
        record_errors, record_warnings = source_record_issues(
            root, record, label=label, verify_file=verify_files
        )
        errors.extend(record_errors)
        warnings.extend(record_warnings)
        if isinstance(record, dict):
            source_id = record.get("source_id")
            relative = record.get("path")
            if isinstance(source_id, str):
                if source_id in seen_ids:
                    errors.append(f"Duplicate source_id: {source_id}")
                seen_ids.add(source_id)
            if isinstance(relative, str):
                if relative in seen_paths:
                    errors.append(f"Duplicate source path: {relative}")
                seen_paths.add(relative)
    return errors, warnings


def require_valid_manifest(root: Path) -> list[dict[str, Any]]:
    records, _ = require_valid_manifest_snapshot(root)
    return records


def require_valid_manifest_snapshot(
    root: Path,
) -> tuple[list[dict[str, Any]], str | None]:
    records, manifest_hash = read_manifest_snapshot(root)
    errors, _ = validate_manifest_records(root, records)
    if errors:
        raise ResearchError("Invalid source manifest: " + "; ".join(errors[:8]))
    return records, manifest_hash


def _target_receipt(path: Path, *, label: str) -> tuple[str | None, bytes | None]:
    """Return a stable existing-target hash and bytes, or ``None`` if absent."""

    raw = Path(path).expanduser()
    if _path_chain_has_link(raw, Path(raw.anchor)):
        raise ResearchError(f"{label} cannot traverse a symbolic link or junction: {raw}")
    if not raw.exists():
        return None, None
    if not raw.is_file():
        raise ResearchError(f"{label} is not a regular file: {raw}")
    content = read_stable_file(raw, label=label)
    return hashlib.sha256(content).hexdigest(), content


def unique_destination(directory: Path, source_name: str, digest: str) -> Path:
    safe_name = Path(source_name).name or "source.bin"
    candidate = directory / f"{digest[:12]}-{safe_name}"
    candidate_hash, _ = _target_receipt(candidate, label="Registered source destination")
    if candidate_hash is None:
        return candidate
    if candidate_hash == digest:
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        numbered = directory / f"{stem}-{counter}{suffix}"
        numbered_hash, _ = _target_receipt(
            numbered, label="Registered source destination"
        )
        if numbered_hash is None:
            return numbered
        if numbered_hash == digest:
            return numbered
        counter += 1


def register_source(
    root: Path,
    source: Path,
    *,
    origin: str,
    source_kind: str,
    rights_status: str,
    authorization_scope: str,
    external_use: str,
    authorization_reference: str,
    source_url: str,
    observed_at: str,
    platform: str,
    originality_compare: bool,
    provenance_note: str,
    copy_external: bool = True,
    workspace: str | Path | None = None,
    work_id: str | None = None,
    allow_bootstrap: bool = False,
) -> tuple[dict[str, Any], bool]:
    with project_write_context(
        root,
        workspace=workspace,
        work_id=work_id,
        allow_bootstrap=allow_bootstrap,
    ) as context:
        result = _register_source(
            root,
            source,
            origin=origin,
            source_kind=source_kind,
            rights_status=rights_status,
            authorization_scope=authorization_scope,
            external_use=external_use,
            authorization_reference=authorization_reference,
            source_url=source_url,
            observed_at=observed_at,
            platform=platform,
            originality_compare=originality_compare,
            provenance_note=provenance_note,
            copy_external=copy_external,
        )
        context.assert_live()
        if result[1]:
            context.refresh_base_after_write("research source registration")
        return result


def _register_source(
    root: Path,
    source: Path,
    *,
    origin: str,
    source_kind: str,
    rights_status: str,
    authorization_scope: str,
    external_use: str,
    authorization_reference: str,
    source_url: str,
    observed_at: str,
    platform: str,
    originality_compare: bool,
    provenance_note: str,
    copy_external: bool = True,
) -> tuple[dict[str, Any], bool]:
    records, manifest_hash = require_valid_manifest_snapshot(root)
    record, added, writes = _prepare_source_registration(
        root,
        source,
        records=records,
        origin=origin,
        source_kind=source_kind,
        rights_status=rights_status,
        authorization_scope=authorization_scope,
        external_use=external_use,
        authorization_reference=authorization_reference,
        source_url=source_url,
        observed_at=observed_at,
        platform=platform,
        originality_compare=originality_compare,
        provenance_note=provenance_note,
        copy_external=copy_external,
    )
    if added:
        try:
            import novel_project

            expected_targets: dict[Path, str | None] = {}
            expected_existing: dict[Path, str | None] = {}
            manifest_path = root / MANIFEST_RELATIVE
            source_path = root.joinpath(*PurePosixPath(record["path"]).parts)
            write_targets = {Path(path).resolve() for path, _ in writes}
            for path, content in writes:
                resolved = Path(path).resolve()
                if resolved == manifest_path.resolve():
                    expected_targets[path] = manifest_hash
                    continue
                target_hash, _ = _target_receipt(
                    path, label="Registered source transaction target"
                )
                content_hash = hashlib.sha256(content).hexdigest()
                if target_hash is not None and target_hash != content_hash:
                    raise ResearchError(
                        f"Registered source target changed during preparation: {path}"
                    )
                expected_targets[path] = target_hash
            if source_path.resolve() not in write_targets:
                source_hash, _ = _target_receipt(
                    source_path, label="Registered project source"
                )
                if source_hash != record["sha256"]:
                    raise ResearchError(
                        "Registered project source changed during preparation: "
                        f"{source_path}"
                    )
                expected_existing[source_path] = source_hash
            original_source = Path(os.path.abspath(Path(source).expanduser()))
            if original_source.resolve() not in write_targets:
                original_hash, _ = _target_receipt(
                    original_source, label="Original source file"
                )
                if original_hash != record["sha256"]:
                    raise ResearchError(
                        "Original source file changed during preparation: "
                        f"{original_source}"
                    )
                expected_existing[original_source] = original_hash
            novel_project.transactional_write(
                writes,
                journal_root=root,
                expected_existing=expected_existing,
                expected_targets=expected_targets,
            )
        except Exception as exc:
            raise ResearchError(str(exc)) from exc
    return record, added


def _validate_registration_metadata(
    *,
    origin: str,
    rights_status: str,
    authorization_scope: str,
    external_use: str,
    authorization_reference: str,
) -> None:
    if origin not in ORIGINS:
        raise ResearchError(f"Unsupported source origin: {origin}")
    if rights_status not in RIGHTS_STATUSES:
        raise ResearchError(f"Unsupported rights status: {rights_status}")
    if external_use not in EXTERNAL_USES:
        raise ResearchError(f"Unsupported external-use value: {external_use}")

    if origin == "user_local":
        if not authorization_reference.strip():
            raise ResearchError(
                "user_local sources require --authorization-reference for this project"
            )
        if rights_status not in {"public_domain", "licensed", "user_authorized"}:
            raise ResearchError(
                "user_local sources require rights status public_domain, licensed, "
                "or user_authorized"
            )
        if external_use == "permitted" and authorization_scope.strip() == "":
            raise ResearchError(
                "Externally permitted use requires an explicit authorization scope"
            )


def _source_record(
    root: Path,
    target: Path,
    content: bytes,
    *,
    original_name: str,
    origin: str,
    source_kind: str,
    rights_status: str,
    authorization_scope: str,
    external_use: str,
    authorization_reference: str,
    source_url: str,
    observed_at: str,
    platform: str,
    originality_compare: bool,
    provenance_note: str,
) -> dict[str, Any]:
    relative_path = target.relative_to(root).as_posix()
    digest = hashlib.sha256(content).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id_for(digest, relative_path),
        "path": relative_path,
        "sha256": digest,
        "size_bytes": len(content),
        "media_type": mimetypes.guess_type(original_name)[0]
        or "application/octet-stream",
        "origin": origin,
        "source_kind": source_kind.strip() or "unspecified",
        "platform": platform.strip() or None,
        "source_url": source_url.strip() or None,
        "observed_at": observed_at.strip() or None,
        "registered_at": utc_now(),
        "rights_status": rights_status,
        "authorization_scope": authorization_scope.strip() or "project_research",
        "authorization_reference": authorization_reference.strip() or None,
        "external_use": external_use,
        "originality_compare": originality_compare,
        "original_name": Path(original_name).name or "source.bin",
        "provenance_note": provenance_note.strip() or None,
    }


def _append_source_record(
    root: Path,
    records: list[dict[str, Any]],
    record: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    for existing in records:
        if (
            existing.get("path") == record["path"]
            and existing.get("sha256") == record["sha256"]
        ):
            return existing, False
    candidate = [*records, record]
    errors, _ = validate_manifest_records(root, candidate, verify_files=False)
    if errors:
        raise ResearchError("Invalid source registration: " + "; ".join(errors[:8]))
    records.append(record)
    return record, True


def _prepare_bytes_registration(
    root: Path,
    target: Path,
    content: bytes,
    *,
    records: list[dict[str, Any]],
    original_name: str,
    origin: str,
    source_kind: str,
    rights_status: str,
    authorization_scope: str,
    external_use: str,
    authorization_reference: str,
    source_url: str,
    observed_at: str,
    platform: str,
    originality_compare: bool,
    provenance_note: str,
) -> tuple[dict[str, Any], bool, list[tuple[Path, bytes]]]:
    _validate_registration_metadata(
        origin=origin,
        rights_status=rights_status,
        authorization_scope=authorization_scope,
        external_use=external_use,
        authorization_reference=authorization_reference,
    )
    raw_target = Path(target).expanduser()
    if _path_chain_has_link(raw_target, root):
        raise ResearchError("Registered source target cannot traverse a link or junction")
    target = raw_target.resolve()
    sources_root = (root / "sources").resolve()
    if not is_within(target, sources_root):
        raise ResearchError("Registered source target must stay under sources/")
    writes: list[tuple[Path, bytes]] = []
    target_hash, _ = _target_receipt(target, label="Registered source target")
    if target_hash is not None:
        if target_hash != hashlib.sha256(content).hexdigest():
            raise ResearchError(f"Refusing to overwrite a different source: {target}")
    else:
        writes.append((target, content))
    record = _source_record(
        root,
        target,
        content,
        original_name=original_name,
        origin=origin,
        source_kind=source_kind,
        rights_status=rights_status,
        authorization_scope=authorization_scope,
        external_use=external_use,
        authorization_reference=authorization_reference,
        source_url=source_url,
        observed_at=observed_at,
        platform=platform,
        originality_compare=originality_compare,
        provenance_note=provenance_note,
    )
    record, added = _append_source_record(root, records, record)
    return record, added, writes if added else []


def _prepare_source_registration(
    root: Path,
    source: Path,
    *,
    records: list[dict[str, Any]],
    origin: str,
    source_kind: str,
    rights_status: str,
    authorization_scope: str,
    external_use: str,
    authorization_reference: str,
    source_url: str,
    observed_at: str,
    platform: str,
    originality_compare: bool,
    provenance_note: str,
    copy_external: bool,
) -> tuple[dict[str, Any], bool, list[tuple[Path, bytes]]]:
    _validate_registration_metadata(
        origin=origin,
        rights_status=rights_status,
        authorization_scope=authorization_scope,
        external_use=external_use,
        authorization_reference=authorization_reference,
    )

    raw_source = Path(os.path.abspath(source.expanduser()))
    if _path_chain_has_link(raw_source, Path(raw_source.anchor)):
        raise ResearchError("Source path cannot traverse a symbolic link or junction")
    if not raw_source.is_file():
        raise ResearchError(f"Source file does not exist: {raw_source}")
    # Read the source before resolving its name, and verify the metadata after
    # the read.  A concurrently replaced local file must be retried by the
    # caller, never registered under a hash that describes a different file.
    original_name = raw_source.name
    content = read_stable_file(raw_source, label="Source file")
    source = raw_source.resolve()
    digest = hashlib.sha256(content).hexdigest()
    sources_root = (root / "sources").resolve()
    inside_sources = is_within(source, sources_root)
    if origin != "user_local" and not inside_sources:
        raise ResearchError(
            "skill_collected and project_existing sources must already be under "
            "the project's sources directory"
        )
    writes: list[tuple[Path, bytes]] = []
    if not inside_sources:
        if not copy_external:
            raise ResearchError(
                "External user sources must be copied into the project for stable reuse"
            )
        destination_dir = sources_root / "local"
        destination = unique_destination(destination_dir, source.name, digest)
        destination_hash, _ = _target_receipt(
            destination, label="Registered source destination"
        )
        if destination_hash is None:
            writes.append((destination, content))
        elif destination_hash != digest:
            raise ResearchError(f"Refusing to replace a different source: {destination}")
        source = destination
    record = _source_record(
        root,
        source,
        content,
        original_name=original_name,
        origin=origin,
        source_kind=source_kind,
        rights_status=rights_status,
        authorization_scope=authorization_scope,
        external_use=external_use,
        authorization_reference=authorization_reference,
        source_url=source_url,
        observed_at=observed_at,
        platform=platform,
        originality_compare=originality_compare,
        provenance_note=provenance_note,
    )
    record, added = _append_source_record(root, records, record)
    if not added:
        return record, False, []
    writes.append((root / MANIFEST_RELATIVE, manifest_bytes(records)))
    return record, True, writes


class PlatformAdapter(ABC):
    """Stable interface for platform-specific public-data adapters."""

    adapter_id: str
    display_name: str

    @abstractmethod
    def request_url(self, args: argparse.Namespace) -> str:
        raise NotImplementedError

    @abstractmethod
    def normalize(
        self,
        payload: Any,
        *,
        source_url: str,
        observed_at: str,
        args: argparse.Namespace,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def capability(self) -> dict[str, Any]:
        raise NotImplementedError


class FanqieAdapter(PlatformAdapter):
    adapter_id = "fanqie"
    display_name = "番茄小说官方公开书库"
    endpoint = "https://fanqienovel.com/api/author/library/book_list/v0/"
    gender_by_channel = {"all": -1, "female": 0, "male": 1}

    def capability(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "status": "implemented",
            "access": "public_no_login",
            "channels": sorted(self.gender_by_channel),
            "sorts": ["hot"],
            "notes": [
                "Does not bypass login, CAPTCHA, paywalls, or anti-automation controls.",
                "Library text may use a site-specific private-use font encoding; "
                "detail-page verification remains necessary.",
            ],
        }

    def request_url(self, args: argparse.Namespace) -> str:
        if args.channel not in self.gender_by_channel:
            raise ResearchError(f"Unsupported Fanqie channel: {args.channel}")
        if args.sort != "hot":
            raise ResearchError("Fanqie adapter currently supports only sort=hot")
        query = urllib.parse.urlencode(
            {
                "page_count": args.page_count,
                "page_index": args.page_index,
                "category_id": -1,
                "creation_status": -1,
                "word_count": -1,
                "book_type": -1,
                "sort": 0,
                "gender": self.gender_by_channel[args.channel],
            }
        )
        return f"{self.endpoint}?{query}"

    def normalize(
        self,
        payload: Any,
        *,
        source_url: str,
        observed_at: str,
        args: argparse.Namespace,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ResearchError("Fanqie response must be a JSON object")
        if payload.get("code") not in (0, "0"):
            raise ResearchError(
                f"Fanqie response was not successful: code={payload.get('code')!r}"
            )
        data = payload.get("data")
        books = data.get("book_list") if isinstance(data, dict) else None
        if not isinstance(books, list):
            raise ResearchError("Fanqie response is missing data.book_list")

        normalized_books: list[dict[str, Any]] = []
        for rank, raw_book in enumerate(books, start=1):
            if not isinstance(raw_book, dict):
                continue
            text_fields = "".join(
                str(raw_book.get(key, ""))
                for key in ("book_name", "author", "abstract", "read_count", "word_count")
            )
            has_private_use = any(0xE000 <= ord(char) <= 0xF8FF for char in text_fields)
            book_id = str(raw_book.get("book_id", ""))
            normalized_books.append(
                {
                    "rank": rank,
                    "book_id": book_id,
                    "book_name_raw": raw_book.get("book_name"),
                    "author_raw": raw_book.get("author"),
                    "abstract_raw": raw_book.get("abstract"),
                    "read_count_raw": raw_book.get("read_count"),
                    "word_count_raw": raw_book.get("word_count"),
                    "creation_status": raw_book.get("creation_status"),
                    "last_chapter_time": raw_book.get("last_chapter_time"),
                    "detail_url": (
                        f"https://fanqienovel.com/page/{book_id}" if book_id else None
                    ),
                    "text_requires_detail_verification": has_private_use,
                }
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "adapter": self.adapter_id,
            "platform": "fanqie",
            "source_url": source_url,
            "observed_at": observed_at,
            "channel": args.channel,
            "sort": args.sort,
            "page_index": args.page_index,
            "requested_page_count": args.page_count,
            "returned_count": len(normalized_books),
            "has_more": data.get("has_more") if isinstance(data, dict) else None,
            "reported_total_count": (
                data.get("total_count") if isinstance(data, dict) else None
            ),
            "metric_boundary": (
                "Ranks and metric strings are preserved in platform-native form; "
                "they are not combined with metrics from other platforms."
            ),
            "books": normalized_books,
        }


ADAPTERS: dict[str, PlatformAdapter] = {"fanqie": FanqieAdapter()}


def fetch_public_json(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ChineseNovelStudio/2.0 public-research-adapter",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise ResearchError(f"Public request failed without retry or bypass: {exc}") from exc
    if len(data) > MAX_RESPONSE_BYTES:
        raise ResearchError(
            f"Response exceeds the {MAX_RESPONSE_BYTES}-byte safety limit"
        )
    return data


def updated_platform_record(
    root: Path,
    adapter: PlatformAdapter,
    source_url: str,
    observed_at: str,
    *,
    existing_bytes: bytes | None = None,
) -> tuple[Path, bytes]:
    path = root / PLATFORM_RELATIVE
    if _path_chain_has_link(path, root):
        raise ResearchError("Platform state path cannot traverse a symbolic link or junction")
    if existing_bytes is not None:
        try:
            data = json.loads(existing_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ResearchError(f"Invalid JSON in {PLATFORM_RELATIVE}: {exc}") from exc
        if not isinstance(data, dict):
            raise ResearchError(f"Expected an object in {PLATFORM_RELATIVE}")
    elif path.exists():
        try:
            data = json.loads(read_stable_file(path, label="Platform state").decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResearchError(f"Invalid JSON in {PLATFORM_RELATIVE}: {exc}") from exc
        if not isinstance(data, dict):
            raise ResearchError(f"Expected an object in {PLATFORM_RELATIVE}")
    else:
        data = {"schema_version": SCHEMA_VERSION, "primary_platform": None, "adapters": {}}
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ResearchError(
            f"{PLATFORM_RELATIVE} has an unsupported schema_version"
        )
    adapters = data.setdefault("adapters", {})
    if not isinstance(adapters, dict):
        raise ResearchError("research/platform.json adapters must be an object")
    current = adapters.setdefault(adapter.adapter_id, {})
    if not isinstance(current, dict):
        raise ResearchError(
            f"research/platform.json adapter {adapter.adapter_id} must be an object"
        )
    current.update(
        {
            "status": "implemented",
            "last_observed_at": observed_at,
            "last_source_url": source_url,
        }
    )
    return path, dump_json(data).encode("utf-8")


def _collect_platform(
    args: argparse.Namespace, root: Path
) -> tuple[dict[str, Any], bool]:
    adapter = ADAPTERS.get(args.platform)
    if adapter is None:
        raise ResearchError(f"Unknown platform adapter: {args.platform}")
    source_url = adapter.request_url(args)
    observed_at = args.observed_at or utc_now()

    if args.input:
        input_raw = Path(args.input).expanduser()
        if _path_chain_has_link(input_raw, Path(input_raw.anchor)):
            raise ResearchError("Offline input cannot traverse a symbolic link or junction")
        input_path = input_raw.resolve()
        if not input_path.is_file():
            raise ResearchError(f"Offline input does not exist: {input_path}")
        raw_bytes = read_stable_file(input_path, label="Offline input")
    else:
        input_path = None
        raw_bytes = fetch_public_json(source_url, args.timeout)

    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchError(f"Platform response is not valid UTF-8 JSON: {exc}") from exc
    normalized = adapter.normalize(
        payload,
        source_url=source_url,
        observed_at=observed_at,
        args=args,
    )

    try:
        observed_datetime = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchError("--observed-at must be a valid ISO-8601 time") from exc
    if observed_datetime.tzinfo is None:
        raise ResearchError("--observed-at must include a timezone offset")
    timestamp = observed_datetime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    collection_dir = root / "sources" / adapter.adapter_id
    raw_digest = hashlib.sha256(raw_bytes).hexdigest()
    raw_name = (
        f"{adapter.adapter_id}-{args.channel}-{args.sort}-{timestamp}-"
        f"{raw_digest[:10]}.raw.json"
    )
    raw_path = collection_dir / raw_name
    if input_path is not None and is_within(input_path, (root / "sources").resolve()):
        raw_path = input_path
    if _path_chain_has_link(raw_path, root):
        raise ResearchError("Collected source target cannot traverse a symbolic link or junction")
    raw_existing_hash, _ = _target_receipt(
        raw_path, label="Raw platform snapshot"
    )
    if raw_existing_hash is not None and raw_existing_hash != raw_digest:
        raise ResearchError(f"Refusing to overwrite a different source: {raw_path}")

    normalized["raw_sha256"] = raw_digest
    normalized_name = (
        f"{adapter.adapter_id}-{args.channel}-{args.sort}-{timestamp}-"
        f"{raw_digest[:10]}.normalized.json"
    )
    normalized_path = collection_dir / normalized_name
    normalized_bytes = dump_json(normalized).encode("utf-8")
    if _path_chain_has_link(normalized_path, root):
        raise ResearchError("Normalized source target cannot traverse a symbolic link or junction")
    normalized_existing_hash, _ = _target_receipt(
        normalized_path, label="Normalized platform snapshot"
    )
    normalized_digest = hashlib.sha256(normalized_bytes).hexdigest()
    if (
        normalized_existing_hash is not None
        and normalized_existing_hash != normalized_digest
    ):
        raise ResearchError(
            f"Refusing to overwrite a different normalized snapshot: {normalized_path}"
        )

    records, manifest_hash = require_valid_manifest_snapshot(root)
    platform_path = root / PLATFORM_RELATIVE
    platform_hash, platform_existing_bytes = _target_receipt(
        platform_path, label="Platform state"
    )
    raw_record, raw_added, raw_writes = _prepare_bytes_registration(
        root,
        raw_path,
        raw_bytes,
        records=records,
        original_name=raw_path.name,
        origin="skill_collected",
        source_kind="platform_raw_snapshot",
        rights_status="public_web",
        authorization_scope="project_research",
        external_use="local_only",
        authorization_reference="",
        source_url=source_url,
        observed_at=observed_at,
        platform=adapter.adapter_id,
        originality_compare=False,
        provenance_note="Collected or normalized by the platform adapter.",
    )
    normalized_record, normalized_added, normalized_writes = _prepare_bytes_registration(
        root,
        normalized_path,
        normalized_bytes,
        records=records,
        original_name=normalized_path.name,
        origin="skill_collected",
        source_kind="platform_normalized_snapshot",
        rights_status="public_web",
        authorization_scope="project_research",
        external_use="local_only",
        authorization_reference="",
        source_url=source_url,
        observed_at=observed_at,
        platform=adapter.adapter_id,
        originality_compare=False,
        provenance_note="Derived locally from the registered raw platform snapshot.",
    )
    platform_path, platform_bytes = updated_platform_record(
        root,
        adapter,
        source_url,
        observed_at,
        existing_bytes=platform_existing_bytes,
    )
    writes = [*raw_writes, *normalized_writes]
    if raw_added or normalized_added:
        writes.append((root / MANIFEST_RELATIVE, manifest_bytes(records)))
    platform_new_hash = hashlib.sha256(platform_bytes).hexdigest()
    platform_current_hash, _ = _target_receipt(
        platform_path, label="Platform state"
    )
    if platform_current_hash != platform_new_hash:
        writes.append((platform_path, platform_bytes))
    if writes:
        try:
            import novel_project

            write_targets = {Path(path).resolve() for path, _ in writes}
            expected_targets: dict[Path, str | None] = {}
            expected_existing: dict[Path, str | None] = {}
            for path, content in writes:
                resolved = Path(path).resolve()
                if resolved == (root / MANIFEST_RELATIVE).resolve():
                    expected_targets[path] = manifest_hash
                elif resolved == platform_path.resolve():
                    expected_targets[path] = platform_hash
                elif resolved == raw_path.resolve():
                    expected_targets[path] = raw_existing_hash
                elif resolved == normalized_path.resolve():
                    expected_targets[path] = normalized_existing_hash
                else:
                    expected_targets[path] = None
            for path, expected in (
                (raw_path, raw_digest),
                (normalized_path, normalized_digest),
                (platform_path, platform_hash),
            ):
                if path.resolve() not in write_targets:
                    expected_existing[path] = expected
            if input_path is not None and input_path.resolve() not in write_targets:
                expected_existing[input_path] = raw_digest
            novel_project.transactional_write(
                writes,
                journal_root=root,
                expected_existing=expected_existing,
                expected_targets=expected_targets,
            )
        except Exception as exc:
            raise ResearchError(str(exc)) from exc
    return (
        {
            "status": "collected",
            "platform": adapter.adapter_id,
            "source_url": source_url,
            "observed_at": observed_at,
            "raw": {"record": raw_record, "added": raw_added},
            "normalized": {"record": normalized_record, "added": normalized_added},
            "books": normalized.get("returned_count", 0),
        },
        bool(writes),
    )


def collect_platform(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_project(args.root)
    with project_write_context(
        root,
        workspace=getattr(args, "workspace", None),
        work_id=getattr(args, "work_id", None),
        allow_bootstrap=bool(getattr(args, "allow_bootstrap", False)),
    ) as context:
        result, wrote_project = _collect_platform(args, root)
        context.assert_live()
        if wrote_project:
            context.refresh_base_after_write("platform research collection")
        return result


def register_command(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_project(args.root)
    source = Path(args.source)
    if _path_chain_has_link(source.expanduser(), Path(source.anchor)):
        raise ResearchError("Source path cannot traverse a symbolic link or junction")
    resolved = source.expanduser().resolve()
    inside_sources = is_within(resolved, (root / "sources").resolve())
    origin = args.origin or ("project_existing" if inside_sources else "user_local")
    record, added = register_source(
        root,
        source,
        origin=origin,
        source_kind=args.source_kind,
        rights_status=args.rights_status,
        authorization_scope=args.authorization_scope,
        external_use=args.external_use,
        authorization_reference=args.authorization_reference,
        source_url=args.source_url,
        observed_at=args.observed_at,
        platform=args.platform,
        originality_compare=args.originality_compare,
        provenance_note=args.provenance_note,
        workspace=getattr(args, "workspace", None),
        work_id=getattr(args, "work_id", None),
        allow_bootstrap=bool(getattr(args, "allow_bootstrap", False)),
    )
    return {"status": "registered" if added else "already_registered", "record": record}


def verify_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = resolve_project(args.root)
    records = read_manifest(root)
    errors, warnings = validate_manifest_records(root, records)
    result = {
        "status": "valid" if not errors else "invalid",
        "project_root": str(root),
        "records": len(records),
        "errors": errors,
        "warnings": warnings,
    }
    return result, 0 if not errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(
        description=(
            "Collect public novel-platform snapshots and register project-local "
            "sources with provenance and authorization boundaries."
        )
    )
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )

    subparsers.add_parser("adapters", help="List implemented platform adapters.")

    collect = subparsers.add_parser(
        "collect", help="Collect or normalize a public platform snapshot."
    )
    collect.add_argument("root", help="Initialized novel project directory.")
    collect.add_argument("--platform", default="fanqie", choices=sorted(ADAPTERS))
    collect.add_argument("--channel", default="all", choices=("all", "female", "male"))
    collect.add_argument("--sort", default="hot", choices=("hot",))
    collect.add_argument("--page-count", type=int, default=18, choices=range(1, 101))
    collect.add_argument("--page-index", type=int, default=0)
    collect.add_argument(
        "--input",
        help="Use an existing raw JSON file instead of making a network request.",
    )
    collect.add_argument(
        "--observed-at", help="ISO-8601 observation time; defaults to current UTC."
    )
    collect.add_argument("--timeout", type=float, default=20.0)
    collect.add_argument("--workspace")
    collect.add_argument("--work-id")
    collect.add_argument(
        "--allow-bootstrap",
        action="store_true",
        help="Explicitly allow collecting into an unregistered standalone project.",
    )

    register = subparsers.add_parser(
        "register", help="Register an existing or explicitly authorized local source."
    )
    register.add_argument("root", help="Initialized novel project directory.")
    register.add_argument("source", help="Source file path.")
    register.add_argument("--origin", choices=sorted(ORIGINS))
    register.add_argument("--source-kind", default="reference_material")
    register.add_argument(
        "--rights-status", default="unknown", choices=sorted(RIGHTS_STATUSES)
    )
    register.add_argument("--authorization-scope", default="project_research")
    register.add_argument(
        "--external-use", default="local_only", choices=sorted(EXTERNAL_USES)
    )
    register.add_argument("--authorization-reference", default="")
    register.add_argument("--source-url", default="")
    register.add_argument("--observed-at", default="")
    register.add_argument("--platform", default="")
    register.add_argument("--originality-compare", action="store_true")
    register.add_argument("--provenance-note", default="")
    register.add_argument("--workspace")
    register.add_argument("--work-id")
    register.add_argument(
        "--allow-bootstrap",
        action="store_true",
        help="Explicitly allow registering into an unregistered standalone project.",
    )

    verify = subparsers.add_parser("verify", help="Verify source manifest integrity.")
    verify.add_argument("root", help="Initialized novel project directory.")
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        if args.command == "adapters":
            return {
                "status": "ok",
                "adapters": [adapter.capability() for adapter in ADAPTERS.values()],
            }
        if args.command == "collect":
            if args.page_index < 0:
                raise ResearchError("--page-index must be non-negative")
            if args.timeout <= 0:
                raise ResearchError("--timeout must be positive")
            return collect_platform(args)
        if args.command == "register":
            return register_command(args)
        if args.command == "verify":
            return verify_command(args)
        raise ResearchError(f"Unsupported command: {args.command}")

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_research",
        domain_errors=(ResearchError, OSError),
    )


if __name__ == "__main__":
    sys.exit(main())
