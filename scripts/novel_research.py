#!/usr/bin/env python3
"""Collect public platform data and register authorized local novel sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
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


class ResearchError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_project(raw_root: str) -> Path:
    root = Path(raw_root).expanduser().resolve()
    if not (root / "novel.json").is_file():
        raise ResearchError(f"Not an initialized novel project: {root}")
    return root


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def atomic_write_text(path: Path, content: str) -> None:
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
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def read_manifest(root: Path) -> list[dict[str, Any]]:
    path = root / MANIFEST_RELATIVE
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
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
    return records


def write_manifest(root: Path, records: list[dict[str, Any]]) -> None:
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    atomic_write_text(root / MANIFEST_RELATIVE, content)


def unique_destination(directory: Path, source_name: str, digest: str) -> Path:
    safe_name = Path(source_name).name or "source.bin"
    candidate = directory / f"{digest[:12]}-{safe_name}"
    if not candidate.exists() or sha256_file(candidate) == digest:
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        numbered = directory / f"{stem}-{counter}{suffix}"
        if not numbered.exists() or sha256_file(numbered) == digest:
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
) -> tuple[dict[str, Any], bool]:
    if origin not in ORIGINS:
        raise ResearchError(f"Unsupported source origin: {origin}")
    if rights_status not in RIGHTS_STATUSES:
        raise ResearchError(f"Unsupported rights status: {rights_status}")
    if external_use not in EXTERNAL_USES:
        raise ResearchError(f"Unsupported external-use value: {external_use}")

    source = source.expanduser().resolve()
    if not source.is_file():
        raise ResearchError(f"Source file does not exist: {source}")

    sources_root = (root / "sources").resolve()
    inside_sources = is_within(source, sources_root)
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
    elif not inside_sources:
        raise ResearchError(
            "skill_collected and project_existing sources must already be under "
            "the project's sources directory"
        )

    original_name = source.name
    digest = sha256_file(source)
    if not inside_sources:
        if not copy_external:
            raise ResearchError(
                "External user sources must be copied into the project for stable reuse"
            )
        destination_dir = sources_root / "local"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = unique_destination(destination_dir, source.name, digest)
        if not destination.exists():
            shutil.copy2(source, destination)
        source = destination.resolve()

    relative_path = source.relative_to(root).as_posix()
    records = read_manifest(root)
    for record in records:
        if record.get("path") == relative_path and record.get("sha256") == digest:
            return record, False

    id_material = f"{digest}:{relative_path}".encode("utf-8")
    suffix = hashlib.sha256(id_material).hexdigest()[:6].upper()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_id": f"SRC-{digest[:10].upper()}-{suffix}",
        "path": relative_path,
        "sha256": digest,
        "size_bytes": source.stat().st_size,
        "media_type": mimetypes.guess_type(source.name)[0]
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
        "original_name": original_name,
        "provenance_note": provenance_note.strip() or None,
    }
    records.append(record)
    write_manifest(root, records)
    return record, True


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


def update_platform_record(
    root: Path, adapter: PlatformAdapter, source_url: str, observed_at: str
) -> None:
    path = root / PLATFORM_RELATIVE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ResearchError(f"Invalid JSON in {PLATFORM_RELATIVE}: {exc}") from exc
        if not isinstance(data, dict):
            raise ResearchError(f"Expected an object in {PLATFORM_RELATIVE}")
    else:
        data = {"schema_version": SCHEMA_VERSION, "primary_platform": None, "adapters": {}}
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
    atomic_write_text(path, dump_json(data))


def collect_platform(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_project(args.root)
    adapter = ADAPTERS.get(args.platform)
    if adapter is None:
        raise ResearchError(f"Unknown platform adapter: {args.platform}")
    source_url = adapter.request_url(args)
    observed_at = args.observed_at or utc_now()

    if args.input:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.is_file():
            raise ResearchError(f"Offline input does not exist: {input_path}")
        raw_bytes = input_path.read_bytes()
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
    collection_dir.mkdir(parents=True, exist_ok=True)
    raw_digest = hashlib.sha256(raw_bytes).hexdigest()
    raw_name = (
        f"{adapter.adapter_id}-{args.channel}-{args.sort}-{timestamp}-"
        f"{raw_digest[:10]}.raw.json"
    )
    raw_path = collection_dir / raw_name
    if input_path is not None and is_within(input_path, (root / "sources").resolve()):
        raw_path = input_path
    elif not raw_path.exists():
        atomic_write_bytes(raw_path, raw_bytes)
    elif sha256_file(raw_path) != raw_digest:
        raise ResearchError(f"Refusing to overwrite a different source: {raw_path}")

    normalized["raw_sha256"] = raw_digest
    normalized_name = (
        f"{adapter.adapter_id}-{args.channel}-{args.sort}-{timestamp}-"
        f"{raw_digest[:10]}.normalized.json"
    )
    normalized_path = collection_dir / normalized_name
    if normalized_path.exists():
        existing = json.loads(normalized_path.read_text(encoding="utf-8"))
        if existing != normalized:
            raise ResearchError(
                f"Refusing to overwrite a different normalized snapshot: {normalized_path}"
            )
    else:
        atomic_write_text(normalized_path, dump_json(normalized))

    raw_record, raw_added = register_source(
        root,
        raw_path,
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
    normalized_record, normalized_added = register_source(
        root,
        normalized_path,
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
    update_platform_record(root, adapter, source_url, observed_at)
    return {
        "status": "collected",
        "platform": adapter.adapter_id,
        "source_url": source_url,
        "observed_at": observed_at,
        "raw": {"record": raw_record, "added": raw_added},
        "normalized": {"record": normalized_record, "added": normalized_added},
        "books": normalized.get("returned_count", 0),
    }


def register_command(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_project(args.root)
    source = Path(args.source)
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
    )
    return {"status": "registered" if added else "already_registered", "record": record}


def verify_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = resolve_project(args.root)
    records = read_manifest(root)
    errors: list[str] = []
    warnings: list[str] = []
    ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        label = record.get("source_id") or f"line {index}"
        if label in ids:
            errors.append(f"Duplicate source_id: {label}")
        ids.add(str(label))
        for key in (
            "schema_version",
            "source_id",
            "path",
            "sha256",
            "origin",
            "rights_status",
            "authorization_scope",
            "external_use",
        ):
            if record.get(key) in (None, ""):
                errors.append(f"{label}: missing {key}")
        relative = record.get("path")
        if not isinstance(relative, str):
            continue
        path = (root / relative).resolve()
        if not is_within(path, root):
            errors.append(f"{label}: path escapes the project root")
            continue
        if not path.is_file():
            errors.append(f"{label}: missing file {relative}")
            continue
        actual_hash = sha256_file(path)
        if actual_hash != record.get("sha256"):
            errors.append(f"{label}: SHA-256 mismatch for {relative}")
        if record.get("origin") == "user_local" and not record.get(
            "authorization_reference"
        ):
            errors.append(f"{label}: user_local source lacks authorization_reference")
        if record.get("external_use") == "local_only" and record.get(
            "authorization_scope"
        ) == "external_processing":
            errors.append(f"{label}: local_only source authorizes external processing")
        if record.get("rights_status") == "unknown":
            warnings.append(f"{label}: rights_status is unknown")
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
        return verify_command(args)

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_research",
        domain_errors=(ResearchError, OSError),
    )


if __name__ == "__main__":
    sys.exit(main())
