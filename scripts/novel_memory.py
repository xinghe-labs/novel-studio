#!/usr/bin/env python3
"""Build and query a rebuildable SQLite index for long-form novel continuity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import novel_cli


SCHEMA_VERSION = 1
DB_RELATIVE = Path(".novel-cache/novel-memory.sqlite3")
CHAPTER_NAME = re.compile(r"^(?P<number>\d{4})(?:-[^/\\]+)?\.md$", re.IGNORECASE)
HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
ENTITY_TAG = re.compile(
    r"(?:^|[\s|，,；;])(?P<type>人物|角色|地点|物品|关系|线索|时间|章节)"
    r"\s*[:：]\s*(?P<value>[^\n|，,；;]{1,80})"
)
THREAD_ID = re.compile(r"\b(?:T|THREAD)-\d{1,6}\b", re.IGNORECASE)
ENTITY_TYPES = {
    "人物": "character",
    "角色": "character",
    "地点": "location",
    "物品": "item",
    "关系": "relationship",
    "线索": "thread",
    "时间": "time",
    "章节": "chapter",
}


class MemoryIndexError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    relative: str
    kind: str
    chapter_number: int | None
    sha256: str
    size_bytes: int
    mtime_ns: int


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_project(raw_root: str) -> Path:
    root = Path(raw_root).expanduser().resolve()
    if not (root / "novel.json").is_file():
        raise MemoryIndexError(f"Not an initialized novel project: {root}")
    return root


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def document_kind(root: Path, path: Path) -> tuple[str, int | None]:
    relative = path.relative_to(root).as_posix()
    chapter_match = CHAPTER_NAME.fullmatch(path.name)
    chapter_number = int(chapter_match.group("number")) if chapter_match else None
    if relative == "memory/book-summary.md":
        return "book_summary", None
    if relative == "memory/decisions.md":
        return "decision", None
    if relative.startswith("memory/chapters/"):
        return "chapter_memory", chapter_number
    if relative == "manuscript/index.md":
        return "chapter_index", None
    if relative.startswith("manuscript/chapters/") or (
        relative.startswith("manuscript/") and chapter_match
    ):
        return "chapter", chapter_number
    if relative == "continuity/state.json":
        return "continuity_state", None
    if relative == "continuity/timeline.md":
        return "timeline", None
    if relative == "continuity/threads.md":
        return "threads", None
    if relative.startswith("continuity/"):
        return "continuity", None
    if relative.startswith("story-bible/"):
        return "story_bible", None
    if relative.startswith("outlines/"):
        return "outline", None
    if relative == "planning/framework-session.md":
        return "planning", None
    return "canon", chapter_number


def canonical_paths(root: Path) -> list[Path]:
    candidates: set[Path] = set()
    fixed = (
        "planning/framework-session.md",
        "manuscript/index.md",
        "memory/book-summary.md",
        "memory/decisions.md",
        "continuity/state.json",
        "continuity/timeline.md",
        "continuity/threads.md",
    )
    for relative in fixed:
        path = root / relative
        if path.is_file():
            candidates.add(path)
    for relative_dir in (
        "story-bible",
        "outlines",
        "manuscript/chapters",
        "memory/chapters",
    ):
        directory = root / relative_dir
        if directory.is_dir():
            candidates.update(path for path in directory.glob("*.md") if path.is_file())
    manuscript = root / "manuscript"
    if manuscript.is_dir():
        candidates.update(
            path
            for path in manuscript.glob("*.md")
            if path.name.lower() != "index.md" and CHAPTER_NAME.fullmatch(path.name)
        )
    continuity = root / "continuity"
    if continuity.is_dir():
        candidates.update(
            path
            for path in continuity.iterdir()
            if path.is_file() and path.suffix.lower() in {".md", ".json"}
        )
    return sorted(candidates, key=lambda path: path.relative_to(root).as_posix())


def scan_documents(root: Path) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for path in canonical_paths(root):
        kind, chapter_number = document_kind(root, path)
        stat = path.stat()
        documents.append(
            SourceDocument(
                path=path,
                relative=path.relative_to(root).as_posix(),
                kind=kind,
                chapter_number=chapter_number,
                sha256=sha256_file(path),
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )
    return documents


def source_state_hash(documents: Iterable[SourceDocument]) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: item.relative):
        digest.update(document.relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(document.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def connect_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            chapter_number INTEGER,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            document_path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,
            heading TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            document_path TEXT NOT NULL REFERENCES documents(path) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            value TEXT NOT NULL,
            context TEXT NOT NULL,
            chapter_number INTEGER
        );
        CREATE INDEX IF NOT EXISTS chunks_document_idx ON chunks(document_path);
        CREATE INDEX IF NOT EXISTS entities_type_value_idx
            ON entities(entity_type, value);
        CREATE INDEX IF NOT EXISTS entities_document_idx ON entities(document_path);
        """
    )


def set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def metadata_dict(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["key"]): str(row["value"])
        for row in connection.execute("SELECT key, value FROM metadata")
    }


def markdown_chunks(text: str) -> list[tuple[str, int, int, str]]:
    lines = text.splitlines()
    if not lines:
        return [("[empty]", 1, 1, "")]
    chunks: list[tuple[str, int, int, str]] = []
    heading = "[document]"
    start = 1
    buffer: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal buffer
        if buffer:
            chunks.append((heading, start, max(start, end_line), "\n".join(buffer)))
        buffer = []

    for line_number, line in enumerate(lines, start=1):
        match = HEADING.match(line)
        if match and buffer:
            flush(line_number - 1)
            heading = match.group("title").strip()
            start = line_number
        elif match:
            heading = match.group("title").strip()
            start = line_number
        buffer.append(line)
        if sum(len(item) + 1 for item in buffer) >= 6000:
            flush(line_number)
            start = line_number + 1
            heading = f"{heading} (continued)"
    flush(len(lines))
    return chunks


def add_entity(
    entities: set[tuple[str, str, str, int | None]],
    entity_type: str,
    value: Any,
    context: str,
    chapter_number: int | None,
) -> None:
    if value is None:
        return
    normalized = str(value).strip()
    if not normalized:
        return
    entities.add((entity_type, normalized[:200], context[:500], chapter_number))


def structured_entities(
    document: SourceDocument, text: str
) -> set[tuple[str, str, str, int | None]]:
    entities: set[tuple[str, str, str, int | None]] = set()
    if document.chapter_number is not None:
        add_entity(
            entities,
            "chapter",
            f"{document.chapter_number:04d}",
            document.relative,
            document.chapter_number,
        )

    for match in ENTITY_TAG.finditer(text):
        add_entity(
            entities,
            ENTITY_TYPES[match.group("type")],
            match.group("value"),
            match.group(0).strip(),
            document.chapter_number,
        )
    for match in THREAD_ID.finditer(text):
        add_entity(
            entities,
            "thread",
            match.group(0).upper(),
            document.relative,
            document.chapter_number,
        )

    if document.kind == "continuity_state":
        try:
            state = json.loads(text)
        except json.JSONDecodeError:
            return entities
        if isinstance(state, dict):
            add_entity(entities, "time", state.get("story_time"), "current story time", None)
            characters = state.get("characters")
            if isinstance(characters, dict):
                for name, details in characters.items():
                    add_entity(entities, "character", name, "continuity state", None)
                    if not isinstance(details, dict):
                        continue
                    add_entity(
                        entities,
                        "location",
                        details.get("location"),
                        f"{name} location",
                        None,
                    )
                    inventory = details.get("inventory")
                    if isinstance(inventory, list):
                        for item in inventory:
                            add_entity(entities, "item", item, f"held by {name}", None)
                    relationships = details.get("relationships")
                    if isinstance(relationships, dict):
                        for other, relation in relationships.items():
                            add_entity(
                                entities,
                                "relationship",
                                f"{name} - {other}: {relation}",
                                "continuity state",
                                None,
                            )
            open_threads = state.get("open_threads")
            if isinstance(open_threads, list):
                for thread in open_threads:
                    add_entity(entities, "thread", thread, "open thread", None)

    if document.kind in {"threads", "timeline"}:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", ":"}:
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if document.kind == "threads" and cells and cells[0].lower() != "id":
                add_entity(entities, "thread", cells[0], line, None)
            if document.kind == "timeline" and cells:
                if cells[0] not in {"故事时间", "时间"}:
                    add_entity(entities, "time", cells[0], line, None)

    if document.kind == "story_bible":
        for line in text.splitlines():
            match = HEADING.match(line)
            if not match or len(match.group("marks")) < 2:
                continue
            title = match.group("title").strip()
            if title in {"人物档案", "人物", "世界规则", "世界观", "叙事声音"}:
                continue
            entity_type = "character" if document.path.name == "cast.md" else "location"
            add_entity(entities, entity_type, title, line, None)
    return entities


def index_document(connection: sqlite3.Connection, document: SourceDocument) -> None:
    text = document.path.read_text(encoding="utf-8")
    indexed_at = utc_now()
    connection.execute(
        "INSERT INTO documents(path, kind, chapter_number, sha256, size_bytes, "
        "mtime_ns, indexed_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            document.relative,
            document.kind,
            document.chapter_number,
            document.sha256,
            document.size_bytes,
            document.mtime_ns,
            indexed_at,
        ),
    )
    chunks = (
        markdown_chunks(text)
        if document.path.suffix.lower() == ".md"
        else [("[json]", 1, max(1, len(text.splitlines())), text)]
    )
    connection.executemany(
        "INSERT INTO chunks(document_path, heading, start_line, end_line, text) "
        "VALUES(?, ?, ?, ?, ?)",
        [
            (document.relative, heading, start, end, chunk_text)
            for heading, start, end, chunk_text in chunks
        ],
    )
    entities = structured_entities(document, text)
    connection.executemany(
        "INSERT INTO entities(document_path, entity_type, value, context, "
        "chapter_number) VALUES(?, ?, ?, ?, ?)",
        [
            (document.relative, entity_type, value, context, chapter_number)
            for entity_type, value, context, chapter_number in sorted(
                entities, key=lambda item: (item[0], item[1], item[2])
            )
        ],
    )


def finalize_metadata(
    connection: sqlite3.Connection, documents: list[SourceDocument]
) -> None:
    set_metadata(connection, "schema_version", str(SCHEMA_VERSION))
    set_metadata(connection, "source_state_hash", source_state_hash(documents))
    set_metadata(connection, "built_at", utc_now())
    set_metadata(connection, "canonical_truth", "project_files")
    set_metadata(connection, "build_complete", "true")


def rebuild_index(root: Path) -> dict[str, Any]:
    documents = scan_documents(root)
    database = root / DB_RELATIVE
    database.parent.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        delete=False, dir=database.parent, prefix="novel-memory-", suffix=".sqlite3.tmp"
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    try:
        connection = connect_database(temp_path)
        try:
            initialize_schema(connection)
            with connection:
                for document in documents:
                    index_document(connection, document)
                finalize_metadata(connection, documents)
            counts = {
                "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            }
        finally:
            connection.close()
        os.replace(temp_path, database)
    finally:
        temp_path.unlink(missing_ok=True)
    return {
        "status": "rebuilt",
        "database": str(database),
        **counts,
        "source_state_hash": source_state_hash(documents),
        "canonical_truth": "project_files",
    }


def database_status(root: Path) -> dict[str, Any]:
    database = root / DB_RELATIVE
    documents = scan_documents(root)
    current = {document.relative: document for document in documents}
    if not database.is_file():
        return {
            "status": "missing",
            "database": str(database),
            "canonical_documents": len(documents),
            "stale": True,
            "changed": sorted(current),
            "deleted": [],
        }
    try:
        connection = connect_database(database)
        try:
            metadata = metadata_dict(connection)
            indexed = {
                str(row["path"]): str(row["sha256"])
                for row in connection.execute("SELECT path, sha256 FROM documents")
            }
            counts = {
                "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            }
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        return {
            "status": "invalid",
            "database": str(database),
            "stale": True,
            "error": str(exc),
        }
    changed = sorted(
        relative
        for relative, document in current.items()
        if indexed.get(relative) != document.sha256
    )
    deleted = sorted(set(indexed) - set(current))
    schema_ok = metadata.get("schema_version") == str(SCHEMA_VERSION)
    complete = metadata.get("build_complete") == "true"
    stale = (
        not schema_ok
        or not complete
        or bool(changed)
        or bool(deleted)
        or metadata.get("source_state_hash") != source_state_hash(documents)
    )
    return {
        "status": "stale" if stale else "fresh",
        "database": str(database),
        "stale": stale,
        "schema_ok": schema_ok,
        "build_complete": complete,
        "changed": changed,
        "deleted": deleted,
        "source_state_hash": source_state_hash(documents),
        "indexed_source_state_hash": metadata.get("source_state_hash"),
        **counts,
    }


def update_index(root: Path) -> dict[str, Any]:
    database = root / DB_RELATIVE
    if not database.is_file():
        result = rebuild_index(root)
        result["status"] = "created"
        return result
    documents = scan_documents(root)
    current = {document.relative: document for document in documents}
    connection = connect_database(database)
    try:
        initialize_schema(connection)
        metadata = metadata_dict(connection)
        if metadata.get("schema_version") != str(SCHEMA_VERSION):
            raise MemoryIndexError(
                "SQLite index schema is incompatible; run rebuild instead of update"
            )
        indexed = {
            str(row["path"]): str(row["sha256"])
            for row in connection.execute("SELECT path, sha256 FROM documents")
        }
        changed = sorted(
            relative
            for relative, document in current.items()
            if indexed.get(relative) != document.sha256
        )
        deleted = sorted(set(indexed) - set(current))
        with connection:
            for relative in deleted:
                connection.execute("DELETE FROM documents WHERE path = ?", (relative,))
            for relative in changed:
                connection.execute("DELETE FROM documents WHERE path = ?", (relative,))
                index_document(connection, current[relative])
            finalize_metadata(connection, documents)
        counts = {
            "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        }
    finally:
        connection.close()
    return {
        "status": "updated" if changed or deleted else "already_fresh",
        "database": str(database),
        "changed": changed,
        "deleted": deleted,
        **counts,
        "source_state_hash": source_state_hash(documents),
    }


def query_matches(text: str, terms: list[str], mode: str) -> tuple[bool, int, int]:
    lowered = text.casefold()
    lowered_terms = [term.casefold() for term in terms]
    positions = [lowered.find(term) for term in lowered_terms]
    if mode == "all":
        matches = all(position >= 0 for position in positions)
    else:
        matches = any(position >= 0 for position in positions)
    score = sum(lowered.count(term) for term in lowered_terms)
    first = min((position for position in positions if position >= 0), default=0)
    return matches, score, first


def excerpt(text: str, position: int, width: int = 240) -> str:
    start = max(0, position - width // 3)
    end = min(len(text), start + width)
    value = text[start:end].replace("\r", " ").replace("\n", " ").strip()
    if start > 0:
        value = "..." + value
    if end < len(text):
        value += "..."
    return value


def search_index(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = resolve_project(args.root)
    status = database_status(root)
    if status.get("status") in {"missing", "invalid"}:
        raise MemoryIndexError("Memory index is unavailable; run rebuild first")
    if args.require_fresh and status.get("stale"):
        raise MemoryIndexError("Memory index is stale; run update before searching")
    query = args.query.strip()
    if not query:
        raise MemoryIndexError("Search query cannot be empty")
    terms = [query] if args.mode == "phrase" else [term for term in query.split() if term]
    mode = "all" if args.mode in {"all", "phrase"} else "any"
    database = root / DB_RELATIVE
    connection = connect_database(database)
    try:
        results: list[dict[str, Any]] = []
        if args.entity_type:
            rows = connection.execute(
                "SELECT entity_type, value, context, document_path, chapter_number "
                "FROM entities WHERE entity_type = ? ORDER BY document_path, value",
                (args.entity_type,),
            )
            for row in rows:
                joined = f"{row['value']}\n{row['context']}"
                matches, score, position = query_matches(joined, terms, mode)
                if matches:
                    results.append(
                        {
                            "result_type": "entity",
                            "entity_type": row["entity_type"],
                            "value": row["value"],
                            "path": row["document_path"],
                            "chapter_number": row["chapter_number"],
                            "context": excerpt(joined, position),
                            "score": score + 10,
                        }
                    )
        else:
            rows = connection.execute(
                "SELECT c.document_path, d.kind, d.chapter_number, c.heading, "
                "c.start_line, c.end_line, c.text FROM chunks c "
                "JOIN documents d ON d.path = c.document_path"
            )
            for row in rows:
                text = str(row["text"])
                matches, score, position = query_matches(text, terms, mode)
                if not matches:
                    continue
                line = int(row["start_line"]) + text[:position].count("\n")
                results.append(
                    {
                        "result_type": "document",
                        "path": row["document_path"],
                        "kind": row["kind"],
                        "chapter_number": row["chapter_number"],
                        "heading": row["heading"],
                        "line": line,
                        "excerpt": excerpt(text, position),
                        "score": score,
                    }
                )
        results.sort(
            key=lambda item: (
                -int(item.get("score", 0)),
                item.get("chapter_number") is None,
                -(item.get("chapter_number") or 0),
                str(item.get("path", "")),
            )
        )
        limited = results[: args.limit]
    finally:
        connection.close()
    return {
        "status": "ok",
        "query": query,
        "mode": args.mode,
        "entity_type": args.entity_type,
        "index_stale": bool(status.get("stale")),
        "matches": len(results),
        "results": limited,
        "canonical_truth": "project_files",
    }, 0


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(
        description=(
            "Build, update, inspect, and query a rebuildable local SQLite index. "
            "Markdown, JSON, and chapter files remain the only canonical truth."
        )
    )
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )
    for command, help_text in (
        ("rebuild", "Rebuild the SQLite cache from canonical project files."),
        ("update", "Incrementally update changed and deleted canonical files."),
        ("status", "Report whether the derived index is fresh."),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        subparser.add_argument("root", help="Initialized novel project directory.")

    search = subparsers.add_parser("search", help="Search indexed continuity context.")
    search.add_argument("root", help="Initialized novel project directory.")
    search.add_argument("query", help="Chinese phrase, stable ID, or search terms.")
    search.add_argument("--mode", choices=("phrase", "all", "any"), default="phrase")
    search.add_argument(
        "--entity-type",
        choices=("character", "location", "item", "relationship", "thread", "time", "chapter"),
    )
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--require-fresh", action="store_true")
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        root = resolve_project(args.root)
        if args.command == "rebuild":
            return rebuild_index(root)
        if args.command == "update":
            return update_index(root)
        if args.command == "status":
            return database_status(root)
        if args.limit < 1 or args.limit > 200:
            raise MemoryIndexError("--limit must be from 1 to 200")
        return search_index(args)

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_memory",
        domain_errors=(MemoryIndexError, OSError, sqlite3.DatabaseError),
    )


if __name__ == "__main__":
    sys.exit(main())
