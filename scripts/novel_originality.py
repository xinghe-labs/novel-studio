#!/usr/bin/env python3
"""Run wording-overlap and single-source structural-dominance audits."""

from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import html
import json
import os
import re
import stat
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import novel_cli


SCHEMA_VERSION = 1
MANIFEST_RELATIVE = Path("research/source-manifest.jsonl")
PLAN_RELATIVE = Path("research/originality-plan.json")
MAX_TEXT_BYTES = 8 * 1024 * 1024
SENTENCE_BREAK = re.compile(r"(?:\r?\n)+|(?<=[。！？!?；;])")
NON_CONTENT = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")
STRUCTURAL_DIMENSIONS = (
    "logline",
    "relationships",
    "world_rules",
    "first_three_nodes",
    "core_twist",
)


class OriginalityError(RuntimeError):
    pass


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1
        elif self.hidden_depth == 0 and tag.lower() in {
            "p",
            "div",
            "li",
            "br",
            "h1",
            "h2",
            "h3",
            "h4",
            "tr",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif self.hidden_depth == 0 and tag.lower() in {
            "p",
            "div",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "tr",
        }:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        return html.unescape("".join(self.parts))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_project(raw_root: str) -> Path:
    raw = Path(raw_root).expanduser()
    _assert_path_chain_no_links(raw, label="Project path")
    root = raw.resolve()
    _assert_path_chain_no_links(root, label="Resolved project path")
    if not (root / "novel.json").is_file():
        raise OriginalityError(f"Not an initialized novel project: {root}")
    _assert_no_links(root)
    return root


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        if checker and checker():
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _assert_path_chain_no_links(path: Path, *, label: str) -> None:
    """Reject links/reparse points in every existing component of a path."""
    absolute = path.expanduser().absolute()
    components = list(reversed(absolute.parents)) + [absolute]
    for component in components:
        if _link_like(component):
            raise OriginalityError(
                f"{label} cannot traverse a link or reparse point: {component}"
            )


def _assert_no_links(root: Path) -> None:
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = list(iterator)
        except OSError as exc:
            raise OriginalityError(f"Unable to inspect project tree: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            if _link_like(path):
                raise OriginalityError(f"Project tree contains a link or reparse point: {path}")
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
            except OSError as exc:
                raise OriginalityError(f"Unable to inspect project entry: {path}: {exc}") from exc


def _read_stable(path: Path, *, label: str) -> bytes:
    _assert_path_chain_no_links(path, label=label)
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read(MAX_TEXT_BYTES + 1)
            after = os.fstat(handle.fileno())
        _assert_path_chain_no_links(path, label=label)
        path_stat = path.stat()
        _assert_path_chain_no_links(path, label=label)
    except OSError as exc:
        raise OriginalityError(f"Unable to read {label}: {path}: {exc}") from exc
    if len(raw) > MAX_TEXT_BYTES:
        raise OriginalityError(
            f"{label} exceeds the {MAX_TEXT_BYTES}-byte local audit limit: {path}"
        )
    path_identity = (
        getattr(path_stat, "st_dev", None),
        getattr(path_stat, "st_ino", None),
        path_stat.st_size,
        path_stat.st_mtime_ns,
    )
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
        or (
            getattr(after, "st_dev", None),
            getattr(after, "st_ino", None),
            after.st_size,
            after.st_mtime_ns,
        )
        != path_identity
    ):
        raise OriginalityError(f"{label} changed while being read: {path}")
    return raw


def sha256_file(path: Path) -> str:
    return hashlib.sha256(_read_stable(path, label="Audit source")).hexdigest()


def read_json(path: Path, *, raw: bytes | None = None) -> Any:
    try:
        snapshot = raw if raw is not None else _read_stable(path, label="JSON audit input")
        return json.loads(snapshot.decode("utf-8"))
    except FileNotFoundError as exc:
        raise OriginalityError(f"Missing file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise OriginalityError(f"JSON audit input is not UTF-8: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OriginalityError(f"Invalid JSON in {path}: {exc}") from exc


def read_manifest(root: Path) -> list[dict[str, Any]]:
    path = root / MANIFEST_RELATIVE
    _assert_path_chain_no_links(path, label="Source manifest")
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        content = _read_stable(path, label="Source manifest").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OriginalityError(f"Source manifest is not UTF-8: {exc}") from exc
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OriginalityError(
                f"Invalid JSONL at {MANIFEST_RELATIVE.as_posix()}:{line_number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise OriginalityError(
                f"Expected object at {MANIFEST_RELATIVE.as_posix()}:{line_number}"
            )
        records.append(value)
    return records


def decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise OriginalityError("Text source is neither valid UTF-8 nor GB18030")


def json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from json_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from json_strings(item)


def extract_text(path: Path, *, raw: bytes | None = None) -> str:
    _assert_path_chain_no_links(path, label="Audit source")
    if not path.is_file():
        raise OriginalityError(f"Audit source is not a regular file: {path}")
    if raw is None:
        raw = _read_stable(path, label="Audit source")
    if len(raw) > MAX_TEXT_BYTES:
        raise OriginalityError(
            f"Source exceeds the {MAX_TEXT_BYTES}-byte local audit limit: {path}"
        )
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        parser = VisibleTextParser()
        parser.feed(decode_text(raw))
        return parser.text()
    if suffix == ".json":
        try:
            payload = json.loads(decode_text(raw))
        except json.JSONDecodeError as exc:
            raise OriginalityError(f"Invalid JSON source {path}: {exc}") from exc
        return "\n".join(json_strings(payload))
    return decode_text(raw)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return NON_CONTENT.sub("", normalized)


def split_segments(text: str, minimum: int = 24, maximum: int = 220) -> list[str]:
    segments: list[str] = []
    for raw_segment in SENTENCE_BREAK.split(text):
        compact = raw_segment.strip()
        normalized = normalize_text(compact)
        if len(normalized) < minimum:
            continue
        if len(normalized) <= maximum:
            segments.append(normalized)
            continue
        step = maximum - 40
        for start in range(0, len(normalized), step):
            chunk = normalized[start : start + maximum]
            if len(chunk) >= minimum:
                segments.append(chunk)
            if start + maximum >= len(normalized):
                break
    return segments


def exact_overlaps(
    candidate: str,
    reference: str,
    *,
    minimum: int,
    max_findings: int,
) -> list[dict[str, Any]]:
    if len(candidate) < minimum or len(reference) < minimum:
        return []
    positions: dict[str, list[int]] = defaultdict(list)
    for index in range(0, len(reference) - minimum + 1):
        gram = reference[index : index + minimum]
        if len(positions[gram]) < 4:
            positions[gram].append(index)
    findings: list[tuple[int, int, int, str]] = []
    covered_until = -1
    candidate_index = 0
    while candidate_index <= len(candidate) - minimum:
        gram = candidate[candidate_index : candidate_index + minimum]
        matches = positions.get(gram, [])
        best: tuple[int, int] | None = None
        for reference_index in matches:
            length = minimum
            while (
                candidate_index + length < len(candidate)
                and reference_index + length < len(reference)
                and candidate[candidate_index + length] == reference[reference_index + length]
            ):
                length += 1
            if best is None or length > best[1]:
                best = (reference_index, length)
        if best is None:
            candidate_index += 1
            continue
        reference_index, length = best
        if candidate_index >= covered_until:
            findings.append(
                (
                    candidate_index,
                    reference_index,
                    length,
                    candidate[candidate_index : candidate_index + min(length, 160)],
                )
            )
            covered_until = candidate_index + length
        candidate_index += max(1, length)
    findings.sort(key=lambda item: (-item[2], item[0], item[1]))
    return [
        {
            "candidate_offset": candidate_offset,
            "reference_offset": reference_offset,
            "normalized_length": length,
            "normalized_excerpt": excerpt,
            "severity": "block" if length >= max(24, minimum + 4) else "review",
        }
        for candidate_offset, reference_offset, length, excerpt in findings[:max_findings]
    ]


def shingles(text: str, width: int = 4) -> set[str]:
    if len(text) <= width:
        return {text} if text else set()
    return {text[index : index + width] for index in range(len(text) - width + 1)}


def near_overlaps(
    candidate_text: str,
    reference_text: str,
    *,
    threshold: float,
    max_findings: int,
) -> list[dict[str, Any]]:
    candidate_segments = split_segments(candidate_text)
    reference_segments = split_segments(reference_text)
    reference_shingles = [shingles(segment) for segment in reference_segments]
    inverted: dict[str, set[int]] = defaultdict(set)
    for index, values in enumerate(reference_shingles):
        for value in sorted(values)[::3] or sorted(values):
            inverted[value].add(index)

    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()
    for candidate_index, candidate in enumerate(candidate_segments):
        candidate_shingles = shingles(candidate)
        candidates: Counter[int] = Counter()
        for value in sorted(candidate_shingles)[::3] or sorted(candidate_shingles):
            candidates.update(inverted.get(value, ()))
        for reference_index, shared_hint in candidates.most_common(20):
            if shared_hint < 2 or (candidate_index, reference_index) in seen_pairs:
                continue
            seen_pairs.add((candidate_index, reference_index))
            reference = reference_segments[reference_index]
            reference_values = reference_shingles[reference_index]
            union = candidate_shingles | reference_values
            jaccard = (
                len(candidate_shingles & reference_values) / len(union) if union else 0.0
            )
            if jaccard < threshold * 0.55:
                continue
            sequence = difflib.SequenceMatcher(
                None, candidate, reference, autojunk=False
            ).ratio()
            similarity = max(jaccard, sequence)
            if similarity < threshold:
                continue
            findings.append(
                {
                    "candidate_segment": candidate[:220],
                    "reference_segment": reference[:220],
                    "sequence_similarity": round(sequence, 4),
                    "shingle_similarity": round(jaccard, 4),
                    "severity": "block" if similarity >= 0.86 else "review",
                }
            )
    findings.sort(
        key=lambda item: (
            -max(item["sequence_similarity"], item["shingle_similarity"]),
            item["candidate_segment"],
        )
    )
    deduplicated: list[dict[str, Any]] = []
    seen_candidate: set[str] = set()
    for finding in findings:
        key = finding["candidate_segment"][:80]
        if key in seen_candidate:
            continue
        seen_candidate.add(key)
        deduplicated.append(finding)
        if len(deduplicated) >= max_findings:
            break
    return deduplicated


def structural_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"text": value, "influences": [], "causal_transformation": ""}]
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                result.append(
                    {"text": item, "influences": [], "causal_transformation": ""}
                )
            elif isinstance(item, dict):
                result.append(item)
        return result
    return []


def field_texts(value: Any) -> list[str]:
    return [
        str(item.get("text", "")).strip()
        for item in structural_items(value)
        if str(item.get("text", "")).strip()
    ]


def structural_similarity(candidate: list[str], reference: list[str]) -> float:
    best = 0.0
    for candidate_text in candidate:
        normalized_candidate = normalize_text(candidate_text)
        if len(normalized_candidate) < 4:
            continue
        for reference_text in reference:
            normalized_reference = normalize_text(reference_text)
            if len(normalized_reference) < 4:
                continue
            sequence = difflib.SequenceMatcher(
                None, normalized_candidate, normalized_reference, autojunk=False
            ).ratio()
            candidate_shingles = shingles(normalized_candidate, width=2)
            reference_shingles = shingles(normalized_reference, width=2)
            union = candidate_shingles | reference_shingles
            jaccard = (
                len(candidate_shingles & reference_shingles) / len(union)
                if union
                else 0.0
            )
            best = max(best, sequence, jaccard)
    return best


def audit_structure(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise OriginalityError("originality-plan.json must contain an object")
    candidate = plan.get("candidate")
    references = plan.get("references")
    if not isinstance(candidate, dict):
        candidate = {}
    if not isinstance(references, list):
        references = []

    missing: list[str] = []
    for dimension in STRUCTURAL_DIMENSIONS:
        texts = field_texts(candidate.get(dimension))
        if dimension == "first_three_nodes":
            if len(texts) != 3:
                missing.append("first_three_nodes must contain exactly three written nodes")
        elif not texts:
            missing.append(f"{dimension} is empty")

    findings: list[dict[str, Any]] = []
    influence_map: dict[str, set[str]] = defaultdict(set)
    missing_transformations: list[dict[str, str]] = []
    for dimension in STRUCTURAL_DIMENSIONS:
        items = structural_items(candidate.get(dimension))
        per_source_nodes: Counter[str] = Counter()
        for item in items:
            influences = item.get("influences", [])
            if isinstance(influences, str):
                influences = [influences]
            if not isinstance(influences, list):
                influences = []
            clean_influences = [str(value).strip() for value in influences if str(value).strip()]
            for source_id in clean_influences:
                per_source_nodes[source_id] += 1
                if dimension != "first_three_nodes":
                    influence_map[source_id].add(dimension)
            if clean_influences and not str(item.get("causal_transformation", "")).strip():
                missing_transformations.append(
                    {
                        "dimension": dimension,
                        "text": str(item.get("text", ""))[:180],
                        "sources": ", ".join(clean_influences),
                    }
                )
        if dimension == "first_three_nodes":
            for source_id, count in per_source_nodes.items():
                if count >= 2:
                    influence_map[source_id].add(dimension)

    reference_ids: set[str] = set()
    similarity_threshold = 0.58
    thresholds = plan.get("thresholds")
    if isinstance(thresholds, dict):
        try:
            similarity_threshold = float(
                thresholds.get("structural_similarity_review", similarity_threshold)
            )
        except (TypeError, ValueError):
            pass
    for reference in references:
        if not isinstance(reference, dict):
            continue
        source_id = str(reference.get("source_id", "")).strip()
        if not source_id:
            continue
        reference_ids.add(source_id)
        structures = reference.get("structures")
        if not isinstance(structures, dict):
            structures = reference
        for dimension in STRUCTURAL_DIMENSIONS:
            similarity = structural_similarity(
                field_texts(candidate.get(dimension)),
                field_texts(structures.get(dimension)),
            )
            if similarity >= similarity_threshold:
                influence_map[source_id].add(dimension)
                findings.append(
                    {
                        "severity": "review",
                        "source_id": source_id,
                        "work": reference.get("work"),
                        "dimension": dimension,
                        "reason": "deterministic textual similarity in structural summaries",
                        "similarity": round(similarity, 4),
                    }
                )

    for source_id in sorted(set(influence_map) - reference_ids):
        findings.append(
            {
                "severity": "incomplete",
                "source_id": source_id,
                "dimension": None,
                "reason": "candidate influence is not declared in references",
            }
        )

    dominance: list[dict[str, Any]] = []
    for source_id, dimensions in sorted(influence_map.items()):
        ordered = [dimension for dimension in STRUCTURAL_DIMENSIONS if dimension in dimensions]
        high_risk_pair = {"logline", "core_twist"}.issubset(dimensions)
        relationship_chain = {"relationships", "first_three_nodes", "core_twist"}.issubset(
            dimensions
        )
        if len(dimensions) >= 3 or high_risk_pair or relationship_chain:
            dominance.append(
                {
                    "severity": "block",
                    "source_id": source_id,
                    "mapped_dimensions": ordered,
                    "reason": (
                        "A single source maps too many critical structural dimensions; "
                        "rebuild theme, causality, relationships, or twist before writing."
                    ),
                }
            )
        elif len(dimensions) >= 2:
            dominance.append(
                {
                    "severity": "review",
                    "source_id": source_id,
                    "mapped_dimensions": ordered,
                    "reason": (
                        "One source maps two critical structural dimensions. "
                        "The mapping is below the automatic block threshold but "
                        "requires explicit human review."
                    ),
                }
            )
        elif dimensions:
            dominance.append(
                {
                    "severity": "pass",
                    "source_id": source_id,
                    "mapped_dimensions": ordered,
                    "reason": (
                        "The source maps one declared structural dimension and "
                        "the candidate records a causal transformation."
                    ),
                }
            )

    for item in missing_transformations:
        findings.append(
            {
                "severity": "incomplete",
                "dimension": item["dimension"],
                "reason": "Influenced element lacks a causal transformation record.",
                "text": item["text"],
                "sources": item["sources"],
            }
        )

    if any(item["severity"] == "block" for item in dominance):
        status = "block"
    elif missing or missing_transformations or any(
        item["severity"] == "incomplete" for item in findings
    ):
        status = "incomplete"
    elif any(item["severity"] == "review" for item in dominance) or any(
        item["severity"] == "review" for item in findings
    ):
        status = "review"
    else:
        status = "pass"
    return {
        "status": status,
        "missing": missing,
        "missing_causal_transformations": missing_transformations,
        "dimension_findings": findings,
        "single_source_dominance": dominance,
        "method": (
            "Declared influences plus deterministic similarity between structural "
            "summaries; semantic judgment still requires Agent/author review."
        ),
    }


def default_candidates(root: Path) -> list[Path]:
    candidates: set[Path] = set()
    chapter_directory = root / "manuscript/chapters"
    if chapter_directory.is_dir():
        candidates.update(
            path
            for path in chapter_directory.glob("*.md")
            if not _link_like(path) and path.is_file()
        )
    staging_directory = root / "staging/chapters"
    if staging_directory.is_dir():
        candidates.update(
            path
            for path in staging_directory.rglob("chapter.md")
            if not _link_like(path) and path.is_file()
        )
    manuscript = root / "manuscript"
    if manuscript.is_dir():
        candidates.update(
            path
            for path in manuscript.glob("[0-9][0-9][0-9][0-9]*.md")
            if not _link_like(path) and path.is_file()
        )
    return sorted(candidates, key=lambda path: path.relative_to(root).as_posix())


def registered_references(
    root: Path, requested: list[str] | None
) -> list[tuple[Path, dict[str, Any]]]:
    records = read_manifest(root)
    try:
        import novel_research

        errors, _ = novel_research.validate_manifest_records(root, records)
    except (ImportError, OSError) as exc:
        raise OriginalityError(
            f"Unable to load source-manifest validator: {exc}"
        ) from exc
    if errors:
        raise OriginalityError("Invalid source manifest: " + "; ".join(errors[:8]))
    by_path = {
        str(record.get("path")): record
        for record in records
        if isinstance(record.get("path"), str)
    }
    selected_paths: list[str]
    if requested:
        selected_paths = []
        for raw in requested:
            resolved = Path(raw).expanduser()
            if not resolved.is_absolute():
                resolved = root / resolved
            _assert_path_chain_no_links(
                resolved, label="Originality reference path"
            )
            resolved = resolved.resolve()
            if not is_within(resolved, root):
                raise OriginalityError(
                    "Originality references must be registered inside the project"
                )
            selected_paths.append(resolved.relative_to(root).as_posix())
    else:
        selected_paths = [
            str(record.get("path"))
            for record in records
            if record.get("originality_compare") is True
        ]
    result: list[tuple[Path, dict[str, Any]]] = []
    for relative in selected_paths:
        record = by_path.get(relative)
        if record is None:
            raise OriginalityError(f"Reference is not registered: {relative}")
        if record.get("originality_compare") is not True:
            raise OriginalityError(
                f"Reference is not authorized for originality comparison: {relative}"
            )
        unresolved = root / relative
        _assert_path_chain_no_links(unresolved, label="Registered reference path")
        path = unresolved.resolve()
        if not path.is_file():
            raise OriginalityError(f"Registered reference is missing: {relative}")
        if sha256_file(path) != record.get("sha256"):
            raise OriginalityError(f"Registered reference hash changed: {relative}")
        result.append((path, record))
    return result


def audit_project(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = resolve_project(args.root)
    if args.candidate:
        candidate_paths: list[Path] = []
        for raw in args.candidate:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = root / path
            _assert_path_chain_no_links(path, label="Candidate path")
            path = path.resolve()
            if not is_within(path, root):
                raise OriginalityError("Candidate files must be inside the project")
            if not path.is_file():
                raise OriginalityError(f"Candidate file does not exist: {path}")
            candidate_paths.append(path)
    else:
        candidate_paths = default_candidates(root)
    references = registered_references(root, args.reference)

    candidate_data: list[dict[str, Any]] = []
    for path in candidate_paths:
        raw = _read_stable(path, label="Candidate audit source")
        candidate_data.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "text": extract_text(path, raw=raw),
            }
        )
    reference_data: list[dict[str, Any]] = []
    for path, record in references:
        raw = _read_stable(path, label="Reference audit source")
        actual_hash = hashlib.sha256(raw).hexdigest()
        if actual_hash != record.get("sha256"):
            raise OriginalityError(
                f"Registered reference hash changed: {path.relative_to(root).as_posix()}"
            )
        reference_data.append(
            {
                "path": path.relative_to(root).as_posix(),
                "source_id": record.get("source_id"),
                "sha256": actual_hash,
                "external_use": record.get("external_use"),
                "rights_status": record.get("rights_status"),
                "text": extract_text(path, raw=raw),
            }
        )

    exact_findings: list[dict[str, Any]] = []
    near_findings: list[dict[str, Any]] = []
    for candidate in candidate_data:
        normalized_candidate = normalize_text(candidate["text"])
        for reference in reference_data:
            normalized_reference = normalize_text(reference["text"])
            exact = exact_overlaps(
                normalized_candidate,
                normalized_reference,
                minimum=args.exact_minimum,
                max_findings=args.max_findings,
            )
            for finding in exact:
                finding.update(
                    {
                        "candidate_path": candidate["path"],
                        "reference_path": reference["path"],
                        "source_id": reference["source_id"],
                    }
                )
            exact_findings.extend(exact)
            near = near_overlaps(
                candidate["text"],
                reference["text"],
                threshold=args.near_threshold,
                max_findings=args.max_findings,
            )
            for finding in near:
                finding.update(
                    {
                        "candidate_path": candidate["path"],
                        "reference_path": reference["path"],
                        "source_id": reference["source_id"],
                    }
                )
            near_findings.extend(near)

    exact_findings.sort(
        key=lambda item: (-item["normalized_length"], item["candidate_path"])
    )
    near_findings.sort(
        key=lambda item: (
            -max(item["sequence_similarity"], item["shingle_similarity"]),
            item["candidate_path"],
        )
    )
    exact_findings = exact_findings[: args.max_findings]
    near_findings = near_findings[: args.max_findings]
    wording_block = any(
        finding["severity"] == "block" for finding in exact_findings + near_findings
    )
    wording_review = bool(exact_findings or near_findings)
    wording_status = "block" if wording_block else "review" if wording_review else "pass"

    plan_path = root / PLAN_RELATIVE
    plan_raw = _read_stable(plan_path, label="Originality plan")
    structure = audit_structure(read_json(plan_path, raw=plan_raw))
    plan_hash = hashlib.sha256(plan_raw).hexdigest()
    if wording_status == "block" or structure["status"] == "block":
        decision = "block"
        code = 1
    elif structure["status"] == "incomplete":
        decision = "incomplete"
        # Incomplete review input is an expected business-gate result.  Exit
        # code 3 is reserved by novel_cli for unhandled/serialization errors.
        code = 1
    elif wording_status == "review" or structure["status"] == "review":
        decision = "review"
        code = 1
    else:
        decision = "pass"
        code = 0

    generated_at = utc_now()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_root": str(root),
        "decision": decision,
        "originality_plan_sha256": plan_hash,
        "candidate_files": [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in candidate_data
        ],
        "reference_files": [
            {
                "path": item["path"],
                "source_id": item["source_id"],
                "sha256": item["sha256"],
                "rights_status": item["rights_status"],
                "external_use": item["external_use"],
            }
            for item in reference_data
        ],
        "wording": {
            "status": wording_status,
            "thresholds": {
                "exact_minimum_normalized_characters": args.exact_minimum,
                "exact_block_normalized_characters": max(24, args.exact_minimum + 4),
                "near_review_similarity": args.near_threshold,
                "near_block_similarity": 0.86,
            },
            "exact_findings": exact_findings,
            "near_findings": near_findings,
        },
        "structure": structure,
        "limitations": [
            "This report does not produce a unified originality percentage.",
            "Deterministic wording checks can miss translated, heavily paraphrased, "
            "or purely semantic borrowing and can flag common genre phrases.",
            "Structural pass requires honest influence records and Agent/author review; "
            "renaming characters does not resolve a blocked causal mapping.",
            "All source processing in this command is local; local_only sources are not uploaded.",
        ],
    }
    if not args.no_report:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fingerprint_material = {
            "candidate_files": report["candidate_files"],
            "reference_files": report["reference_files"],
            "originality_plan_sha256": plan_hash,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_material, sort_keys=True).encode("utf-8")
        ).hexdigest()[:10]
        default_path = (
            root
            / "staging"
            / "originality"
            / f"originality-audit-{timestamp}-{fingerprint}.json"
        )
        raw_output = getattr(args, "output", None)
        raw_report_path = Path(raw_output).expanduser() if raw_output else default_path
        _assert_path_chain_no_links(raw_report_path, label="Originality report output")
        report_path = raw_report_path.resolve()
        _assert_path_chain_no_links(
            report_path, label="Resolved originality report output"
        )
        if report_path.suffix.lower() != ".json":
            raise OriginalityError("Originality report output must be a .json file")
        if report_path.exists():
            raise OriginalityError(
                f"Refusing to overwrite an existing originality report: {report_path}"
            )
        if is_within(report_path, root) and not is_within(
            report_path, (root / "staging").resolve()
        ):
            raise OriginalityError(
                "Project-local originality reports must be written under staging; "
                "commit-chapter archives a passing report to reviews atomically"
            )
        report["report_path"] = (
            report_path.relative_to(root).as_posix()
            if is_within(report_path, root)
            else str(report_path)
        )
        # A report under the project tree is still a project write, even though
        # it lives in staging and is excluded from the canonical state hash.
        # Require the same explicit lease as every other project-local artifact;
        # work-directory reports remain available without a project lease.
        write_context: Any = contextlib.nullcontext()
        if is_within(report_path, root):
            try:
                import novel_workspace

                @contextlib.contextmanager
                def _authorized_context():
                    try:
                        with novel_workspace.project_write_context(
                            root,
                            workspace=getattr(args, "workspace", None),
                            work_id=getattr(args, "work_id", None),
                            allow_bootstrap=bool(getattr(args, "allow_bootstrap", False)),
                        ) as context:
                            yield context
                    except novel_workspace.WorkspaceError as exc:
                        raise OriginalityError(str(exc)) from exc

                write_context = _authorized_context()
            except (ImportError, OSError) as exc:
                raise OriginalityError(
                    f"Project write authorization module unavailable: {exc}"
                ) from exc
        with write_context as context:
            try:
                novel_cli.atomic_create_text(
                    report_path,
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                )
            except FileExistsError as exc:
                raise OriginalityError(
                    f"Refusing to overwrite an existing originality report: {report_path}"
                ) from exc
            if hasattr(context, "assert_live"):
                context.assert_live()
                context.refresh_base_after_write("originality audit report")
    return report, code


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(
        description=(
            "Audit exact/near wording overlap and single-source structural dominance "
            "without inventing a unified originality score."
        )
    )
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )
    audit = subparsers.add_parser("audit", help="Run both originality audit layers.")
    audit.add_argument("root", help="Initialized novel project directory.")
    audit.add_argument(
        "--candidate",
        action="append",
        help="Candidate project file; repeat as needed. Defaults to chapters and staging.",
    )
    audit.add_argument(
        "--reference",
        action="append",
        help=(
            "Registered project reference path; repeat as needed. Defaults to manifest "
            "records with originality_compare=true."
        ),
    )
    audit.add_argument("--exact-minimum", type=int, default=18)
    audit.add_argument("--near-threshold", type=float, default=0.72)
    audit.add_argument("--max-findings", type=int, default=30)
    audit.add_argument(
        "--output",
        help=(
            "New JSON report path. Project-local output must be under staging; "
            "an external work-directory path is also allowed."
        ),
    )
    audit.add_argument("--no-report", action="store_true")
    audit.add_argument("--workspace")
    audit.add_argument("--work-id")
    audit.add_argument("--allow-bootstrap", action="store_true")
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        if args.no_report and args.output:
            raise OriginalityError("--output cannot be combined with --no-report")
        if args.exact_minimum < 12 or args.exact_minimum > 80:
            raise OriginalityError("--exact-minimum must be from 12 to 80")
        if args.near_threshold < 0.5 or args.near_threshold > 1.0:
            raise OriginalityError("--near-threshold must be from 0.5 to 1.0")
        if args.max_findings < 1 or args.max_findings > 200:
            raise OriginalityError("--max-findings must be from 1 to 200")
        return audit_project(args)

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_originality",
        domain_errors=(OriginalityError, OSError),
    )


if __name__ == "__main__":
    sys.exit(main())
