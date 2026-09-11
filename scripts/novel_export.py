#!/usr/bin/env python3
"""Export canonical fiction Markdown into rebuildable reading deliverables."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import novel_project
import novel_review
import novel_continuity


EXPORT_SCHEMA_VERSION = 1
EXPORT_MANIFEST = "export-manifest.json"
SUPPORTED_FORMATS = frozenset({"txt", "docx", "epub", "fanqie"})
DELIVERY_QUALITY_SCHEMA_VERSION = 1
DELIVERY_PROFILE_VERSION = 1
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_FILENAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)
SCENE_BREAK_LINE = re.compile(
    r"\s{0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})"
)
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
XML_INVALID = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)
UTF8_BOM = b"\xef\xbb\xbf"
NORMALIZABLE_HORIZONTAL_SPACES = frozenset(
    {
        "\t",
        "\u00a0",
        "\u1680",
        "\u2000",
        "\u2001",
        "\u2002",
        "\u2003",
        "\u2004",
        "\u2005",
        "\u2006",
        "\u2007",
        "\u2008",
        "\u2009",
        "\u200a",
        "\u202f",
        "\u205f",
        "\u3000",
    }
)
BIDI_CONTROL_CODEPOINTS = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
)
INVISIBLE_PLACEHOLDER_CODEPOINTS = frozenset(
    {
        0x034F,
        0x115F,
        0x1160,
        0x17B4,
        0x17B5,
        0x2800,
        0x3164,
        0xFFA0,
        0xFFFC,
    }
)
VARIATION_SELECTOR_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF))
NORMALIZATION_KEYS = (
    "crlf_to_lf",
    "cr_to_lf",
    "horizontal_space_to_ascii",
    "nfc_normalized_fragments",
    "trailing_spaces_removed",
    "terminal_newlines_removed",
    "final_newline_added",
)
TEXT_QUALITY_CHECKS = (
    "strict_utf8",
    "no_bom",
    "lf_only",
    "unicode_nfc",
    "no_forbidden_unicode",
    "no_ambiguous_invisible_characters",
    "plain_text_no_markdown_or_html",
    "no_trailing_whitespace",
    "single_terminal_newline",
    "bounded_blank_lines",
    "bounded_line_length",
    "canonical_derived_text_match",
)
MARKDOWN_RESIDUAL_PATTERNS = (
    ("Markdown heading", re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")),
    ("Markdown code fence", re.compile(r"(?m)^\s*(?:```|~~~)")),
    ("Markdown image", re.compile(r"!\[[^\]]*\]\([^)]+\)")),
    ("Markdown link", re.compile(r"\[[^\]]+\]\([^)]+\)")),
    ("HTML tag", re.compile(r"</?[A-Za-z][^>]*>")),
    ("Markdown emphasis", re.compile(r"(?:\*\*|__|~~).+?(?:\*\*|__|~~)")),
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
EPUB_NS = "http://www.idpf.org/2007/ops"

ET.register_namespace("w", W_NS)
ET.register_namespace("r", R_NS)
ET.register_namespace("cp", CP_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("dcterms", DCTERMS_NS)
ET.register_namespace("xsi", XSI_NS)


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class TextBlock:
    kind: str
    text: str


@dataclass(frozen=True)
class Chapter:
    number: int
    number_text: str
    title: str
    source_path: Path
    source_relative: str
    source_sha256: str
    blocks: tuple[TextBlock, ...]
    plain_text: str
    non_whitespace_characters: int
    source_normalizations: dict[str, int]


@dataclass(frozen=True)
class ProjectSnapshot:
    root: Path
    title: str
    language: str
    genre: str
    work_type: str
    project_id: str | None
    current_chapter: int
    source_snapshot_sha256: str
    chapters: tuple[Chapter, ...]
    validation_warnings: tuple[str, ...]
    source_normalizations: dict[str, int]


@dataclass(frozen=True)
class DeliveryProfile:
    name: str
    kind: str
    checks: tuple[str, ...]
    title_policy: str
    scene_break_policy: str
    max_line_characters: int | None = None


GENERIC_TEXT_PROFILE = DeliveryProfile(
    name="generic-plain-text",
    kind="text",
    checks=TEXT_QUALITY_CHECKS,
    title_policy="book_and_chapter_titles_in_text",
    scene_break_policy="visible_plain_text_marker_allowed",
    max_line_characters=20000,
)
FANQIE_SERIAL_PROFILE = DeliveryProfile(
    name="fanqie-serial",
    kind="text",
    checks=TEXT_QUALITY_CHECKS
    + ("body_title_not_duplicated", "standalone_scene_break_removed"),
    title_policy="filename_only_body_text",
    scene_break_policy="blank_line_only",
    max_line_characters=20000,
)
FANQIE_SHORT_STORY_PROFILE = DeliveryProfile(
    name="fanqie-short-story",
    kind="text",
    checks=TEXT_QUALITY_CHECKS
    + ("body_title_not_duplicated", "standalone_scene_break_removed"),
    title_policy="single_story_filename_only_body_text",
    scene_break_policy="blank_line_only",
    max_line_characters=20000,
)
DOCX_REVIEW_PROFILE = DeliveryProfile(
    name="docx-review-package",
    kind="package",
    checks=(
        "zip_integrity",
        "required_parts",
        "xml_well_formed",
        "unicode_text_gate",
        "chapter_heading_count",
        "canonical_text_coverage",
    ),
    title_policy="review_layout",
    scene_break_policy="visible_review_marker",
)
EPUB3_PROFILE = DeliveryProfile(
    name="epub3-reading-package",
    kind="package",
    checks=(
        "epub_mimetype",
        "zip_integrity",
        "required_parts",
        "xml_well_formed",
        "unicode_text_gate",
        "chapter_document_count",
        "canonical_text_coverage",
    ),
    title_policy="epub_navigation_and_chapter_heading",
    scene_break_policy="visible_reading_marker",
)
DELIVERY_PROFILES = {
    profile.name: profile
    for profile in (
        GENERIC_TEXT_PROFILE,
        FANQIE_SERIAL_PROFILE,
        FANQIE_SHORT_STORY_PROFILE,
        DOCX_REVIEW_PROFILE,
        EPUB3_PROFILE,
    )
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def epub_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def empty_normalizations() -> dict[str, int]:
    return {key: 0 for key in NORMALIZATION_KEYS}


def merge_normalizations(
    target: dict[str, int], *sources: dict[str, int]
) -> dict[str, int]:
    for source in sources:
        for key in NORMALIZATION_KEYS:
            target[key] = target.get(key, 0) + int(source.get(key, 0))
    return target


def profile_manifest(profile: DeliveryProfile) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": profile.name,
        "version": DELIVERY_PROFILE_VERSION,
        "kind": profile.kind,
        "checks": list(profile.checks),
        "title_policy": profile.title_policy,
        "scene_break_policy": profile.scene_break_policy,
    }
    if profile.max_line_characters is not None:
        result["max_line_characters"] = profile.max_line_characters
    return result


def is_noncharacter(codepoint: int) -> bool:
    return 0xFDD0 <= codepoint <= 0xFDEF or (
        codepoint <= 0x10FFFF and codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
    )


def is_variation_selector(codepoint: int) -> bool:
    return any(start <= codepoint <= end for start, end in VARIATION_SELECTOR_RANGES)


def emoji_tag_indexes(value: str) -> set[int]:
    allowed: set[int] = set()
    index = 0
    while index < len(value):
        if ord(value[index]) != 0x1F3F4:
            index += 1
            continue
        cursor = index + 1
        tags: list[int] = []
        while cursor < len(value) and 0xE0020 <= ord(value[cursor]) <= 0xE007E:
            tags.append(cursor)
            cursor += 1
        if tags and cursor < len(value) and ord(value[cursor]) == 0xE007F:
            allowed.update(tags)
            allowed.add(cursor)
            index = cursor + 1
        else:
            index += 1
    return allowed


def character_location(value: str, index: int) -> str:
    line = value.count("\n", 0, index) + 1
    line_start = value.rfind("\n", 0, index) + 1
    column = index - line_start + 1
    return f"line {line}, column {column}"


def raise_unicode_issue(
    context: str, value: str, index: int, reason: str
) -> None:
    character = value[index]
    codepoint = ord(character)
    name = unicodedata.name(character, "UNNAMED")
    raise ExportError(
        f"{context} contains {reason} U+{codepoint:04X} {name} "
        f"at {character_location(value, index)}"
    )


def validate_unicode_safety(
    value: str,
    *,
    context: str,
    allowed_controls: frozenset[str] = frozenset(),
) -> None:
    allowed_tags = emoji_tag_indexes(value)
    for index, character in enumerate(value):
        codepoint = ord(character)
        category = unicodedata.category(character)
        if codepoint == 0xFEFF:
            raise_unicode_issue(context, value, index, "a forbidden BOM character")
        if codepoint == 0xFFFD:
            raise_unicode_issue(context, value, index, "a replacement character")
        if codepoint in BIDI_CONTROL_CODEPOINTS:
            raise_unicode_issue(context, value, index, "a bidirectional control")
        if codepoint in INVISIBLE_PLACEHOLDER_CODEPOINTS:
            raise_unicode_issue(context, value, index, "an invisible placeholder")
        if is_noncharacter(codepoint):
            raise_unicode_issue(context, value, index, "a Unicode noncharacter")
        if category == "Cs":
            raise_unicode_issue(context, value, index, "an isolated surrogate")
        if category == "Co":
            raise_unicode_issue(context, value, index, "a private-use character")
        if category == "Cn":
            raise_unicode_issue(context, value, index, "an unassigned character")
        if category == "Cc" and character not in allowed_controls:
            raise_unicode_issue(context, value, index, "a control character")
        if category in {"Zl", "Zp"}:
            raise_unicode_issue(context, value, index, "an ambiguous line separator")
        if is_variation_selector(codepoint):
            if index == 0 or value[index - 1].isspace() or unicodedata.category(
                value[index - 1]
            ).startswith("C"):
                raise_unicode_issue(
                    context, value, index, "an isolated variation selector"
                )
            continue
        if codepoint in {0x200C, 0x200D}:
            if (
                index == 0
                or index + 1 == len(value)
                or value[index - 1].isspace()
                or value[index + 1].isspace()
                or unicodedata.category(value[index - 1]).startswith("C")
                or unicodedata.category(value[index + 1]).startswith("C")
            ):
                raise_unicode_issue(
                    context, value, index, "an isolated join-control character"
                )
            continue
        if category == "Cf" and index not in allowed_tags:
            raise_unicode_issue(context, value, index, "an invisible format character")


def normalize_unicode_for_delivery(
    value: str,
    *,
    context: str,
    multiline: bool,
) -> tuple[str, dict[str, int]]:
    before_controls = frozenset({"\t", "\r", "\n"} if multiline else {"\t"})
    validate_unicode_safety(
        value,
        context=context,
        allowed_controls=before_controls,
    )
    counts = empty_normalizations()
    if multiline:
        counts["crlf_to_lf"] = value.count("\r\n")
        normalized = value.replace("\r\n", "\n")
        counts["cr_to_lf"] = normalized.count("\r")
        normalized = normalized.replace("\r", "\n")
    else:
        normalized = value
    counts["horizontal_space_to_ascii"] = sum(
        1 for character in normalized if character in NORMALIZABLE_HORIZONTAL_SPACES
    )
    if counts["horizontal_space_to_ascii"]:
        normalized = "".join(
            " " if character in NORMALIZABLE_HORIZONTAL_SPACES else character
            for character in normalized
        )
    nfc = unicodedata.normalize("NFC", normalized)
    if nfc != normalized:
        counts["nfc_normalized_fragments"] = 1
        normalized = nfc
    validate_unicode_safety(
        normalized,
        context=context,
        allowed_controls=frozenset({"\n"} if multiline else set()),
    )
    return normalized, counts


def read_utf8_source(path: Path, *, context: str) -> tuple[str, dict[str, int]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExportError(f"Cannot read {context}: {exc}") from exc
    if UTF8_BOM in raw:
        location = "at the beginning" if raw.startswith(UTF8_BOM) else "inside the file"
        raise ExportError(f"{context} contains a UTF-8 BOM {location}")
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExportError(f"{context} is not strict UTF-8: {exc}") from exc
    return normalize_unicode_for_delivery(value, context=context, multiline=True)


def prepare_utf8_document(
    content: str, *, context: str
) -> tuple[str, dict[str, int]]:
    normalized, counts = normalize_unicode_for_delivery(
        content,
        context=context,
        multiline=True,
    )
    lines = normalized.split("\n")
    stripped_lines: list[str] = []
    for line in lines:
        stripped = line.rstrip(" ")
        counts["trailing_spaces_removed"] += len(line) - len(stripped)
        stripped_lines.append(stripped)
    normalized = "\n".join(stripped_lines)
    terminal_newlines = len(normalized) - len(normalized.rstrip("\n"))
    if terminal_newlines == 0:
        counts["final_newline_added"] = 1
    elif terminal_newlines > 1:
        counts["terminal_newlines_removed"] = terminal_newlines - 1
    normalized = normalized.rstrip("\n") + "\n"
    validate_unicode_safety(
        normalized,
        context=context,
        allowed_controls=frozenset({"\n"}),
    )
    return normalized, counts


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def write_utf8(path: Path, content: str, *, context: str | None = None) -> dict[str, int]:
    normalized, counts = prepare_utf8_document(
        content,
        context=context or path.name,
    )
    write_bytes(path, normalized.encode("utf-8"))
    return counts


def clean_xml_text(value: str) -> str:
    if XML_INVALID.search(value):
        raise ExportError("XML text contains a character forbidden by XML 1.0")
    normalized, counts = normalize_unicode_for_delivery(
        value,
        context="XML text",
        multiline=False,
    )
    if any(counts.values()) or normalized != value:
        raise ExportError("XML text reached the package builder before normalization")
    return value


def safe_filename(value: str, *, fallback: str, maximum: int = 100) -> str:
    normalized, _ = normalize_unicode_for_delivery(
        value,
        context="output filename",
        multiline=False,
    )
    normalized = normalized.strip()
    normalized = INVALID_FILENAME.sub("_", normalized)
    normalized = re.sub(r"\s+", " ", normalized).rstrip(" .")
    if not normalized:
        normalized = fallback
    if RESERVED_FILENAME.fullmatch(normalized):
        normalized = f"_{normalized}"
    if len(normalized) > maximum:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
        normalized = normalized[: maximum - 9].rstrip(" .") + "-" + digest
    return normalized


def split_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in stripped[1:-1]:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def is_separator_row(cells: Iterable[str]) -> bool:
    material = list(cells)
    return bool(material) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in material)


def strip_frontmatter(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return normalized
    end = normalized.find("\n---\n", 4)
    if end == -1:
        raise ExportError("Chapter contains unterminated YAML frontmatter")
    return normalized[end + 5 :]


def strip_first_heading(markdown: str) -> str:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(r"^\s{0,3}#{1,6}\s+", line):
            del lines[index]
        break
    return "\n".join(lines)


def clean_inline_markdown(value: str) -> str:
    text = MARKDOWN_IMAGE.sub(lambda match: match.group(1), value)
    text = MARKDOWN_LINK.sub(lambda match: match.group(1), text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(?:\*\*|__)(.+?)(?:\*\*|__)", r"\1", text)
    text = re.sub(
        r"(?<!\*)\*([^\s*](?:[^*]*?[^\s*])?)\*(?!\*)",
        r"\1",
        text,
    )
    text = re.sub(
        r"(?<!_)_([^\s_](?:[^_]*?[^\s_])?)_(?!_)",
        r"\1",
        text,
    )
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\\([\\`*{}\[\]()#+.!_>-])", r"\1", text)
    return html.unescape(text).strip()


def join_wrapped_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    result = clean_inline_markdown(lines[0])
    for raw in lines[1:]:
        addition = clean_inline_markdown(raw)
        if not addition:
            continue
        separator = ""
        if result and result[-1].isascii() and addition[0].isascii():
            if result[-1].isalnum() and addition[0].isalnum():
                separator = " "
        result += separator + addition
    return result.strip()


def markdown_blocks(markdown: str) -> tuple[TextBlock, ...]:
    text = strip_first_heading(strip_frontmatter(markdown))
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = text.splitlines()
    blocks: list[TextBlock] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        if not paragraph:
            return
        rendered = join_wrapped_lines(paragraph)
        paragraph.clear()
        if rendered:
            blocks.append(TextBlock("paragraph", rendered))

    for raw_line in lines:
        line = raw_line.rstrip()
        if re.match(r"^\s*(```|~~~)", line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            paragraph.append(line)
            continue
        if not line.strip():
            flush()
            continue
        if SCENE_BREAK_LINE.fullmatch(line):
            flush()
            blocks.append(TextBlock("scene_break", "* * *"))
            continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            flush()
            rendered = clean_inline_markdown(heading.group(1))
            if rendered:
                blocks.append(TextBlock("subheading", rendered))
            continue
        quote = re.match(r"^\s{0,3}>\s?(.*)$", line)
        if quote:
            flush()
            rendered = clean_inline_markdown(quote.group(1))
            if rendered:
                blocks.append(TextBlock("blockquote", rendered))
            continue
        unordered = re.match(r"^\s*[-+*]\s+(.+)$", line)
        if unordered:
            flush()
            rendered = clean_inline_markdown(unordered.group(1))
            if rendered:
                blocks.append(TextBlock("paragraph", f"- {rendered}"))
            continue
        ordered = re.match(r"^\s*(\d+)[.)]\s+(.+)$", line)
        if ordered:
            flush()
            rendered = clean_inline_markdown(ordered.group(2))
            if rendered:
                blocks.append(
                    TextBlock("paragraph", f"{ordered.group(1)}. {rendered}")
                )
            continue
        paragraph.append(line)
    flush()
    return tuple(blocks)


def blocks_to_plain_text(blocks: Iterable[TextBlock]) -> str:
    return "\n\n".join(block.text for block in blocks if block.text).strip()


def blocks_to_fanqie_text(blocks: Iterable[TextBlock]) -> str:
    return "\n\n".join(
        block.text
        for block in blocks
        if block.text and block.kind != "scene_break"
    ).strip()


def display_chapter_title(number: int, title: str) -> str:
    if re.match(r"^第[0-9零一二三四五六七八九十百千万两]+章(?:\s|$)", title):
        return title
    return f"第{number}章 {title}".strip()


def display_unit_title(snapshot: ProjectSnapshot, chapter: Chapter) -> str:
    if snapshot.work_type == "short_story":
        return chapter.title or snapshot.title
    return display_chapter_title(chapter.number, chapter.title)


def parse_index(root: Path) -> tuple[tuple[Chapter, ...], dict[str, int]]:
    index_path = root / "manuscript/index.md"
    index_text, index_normalizations = read_utf8_source(
        index_path,
        context="manuscript/index.md",
    )
    source_normalizations = empty_normalizations()
    merge_normalizations(source_normalizations, index_normalizations)
    manuscript_root = (root / "manuscript").resolve()
    chapters: list[Chapter] = []
    seen_numbers: set[int] = set()
    seen_targets: set[str] = set()

    for line in index_text.splitlines():
        cells = split_markdown_table_row(line)
        if len(cells) < 9 or is_separator_row(cells):
            continue
        number_text = cells[0].strip()
        if not re.fullmatch(r"\d{4}", number_text):
            continue
        number = int(number_text)
        title_value = clean_inline_markdown(cells[1])
        targets = novel_project.markdown_link_targets(cells[-1])
        if len(targets) != 1:
            raise ExportError(
                f"Chapter {number_text} index row must contain exactly one Markdown link"
            )
        target = targets[0]
        source_path = (manuscript_root / Path(target)).resolve()
        try:
            source_relative = source_path.relative_to(manuscript_root).as_posix()
        except ValueError as exc:
            raise ExportError(
                f"Chapter {number_text} link leaves manuscript directory: {target}"
            ) from exc
        if source_path.is_symlink():
            raise ExportError(f"Chapter source cannot be a symbolic link: {target}")
        if not source_path.is_file():
            raise ExportError(f"Chapter source is missing: manuscript/{source_relative}")
        filename_match = novel_project.CHAPTER_NAME.fullmatch(source_path.name)
        if filename_match is None or filename_match.group("number") != number_text:
            raise ExportError(
                f"Chapter {number_text} index number does not match {source_path.name}"
            )
        if number in seen_numbers:
            raise ExportError(f"Duplicate chapter number in index: {number_text}")
        if source_relative in seen_targets:
            raise ExportError(f"Duplicate chapter target in index: {source_relative}")
        if not title_value:
            title_value = source_path.stem[5:] or f"第{number}章"
        title, title_normalizations = normalize_unicode_for_delivery(
            title_value,
            context=f"chapter {number_text} title",
            multiline=False,
        )
        chapter_normalizations = empty_normalizations()
        merge_normalizations(chapter_normalizations, title_normalizations)
        markdown, markdown_normalizations = read_utf8_source(
            source_path,
            context=f"chapter {number_text} Markdown",
        )
        merge_normalizations(chapter_normalizations, markdown_normalizations)
        raw_blocks = markdown_blocks(markdown)
        normalized_blocks: list[TextBlock] = []
        for block_index, block in enumerate(raw_blocks, start=1):
            rendered, rendered_normalizations = normalize_unicode_for_delivery(
                block.text,
                context=f"chapter {number_text} block {block_index}",
                multiline=False,
            )
            merge_normalizations(chapter_normalizations, rendered_normalizations)
            normalized_blocks.append(TextBlock(block.kind, rendered))
        blocks = tuple(normalized_blocks)
        plain_text = blocks_to_plain_text(blocks)
        if not plain_text:
            raise ExportError(f"Chapter {number_text} has no exportable body text")
        chapters.append(
            Chapter(
                number=number,
                number_text=number_text,
                title=title,
                source_path=source_path,
                source_relative=f"manuscript/{source_relative}",
                source_sha256=sha256_file(source_path),
                blocks=blocks,
                plain_text=plain_text,
                non_whitespace_characters=sum(
                    1 for character in plain_text if not character.isspace()
                ),
                source_normalizations=chapter_normalizations,
            )
        )
        merge_normalizations(source_normalizations, chapter_normalizations)
        seen_numbers.add(number)
        seen_targets.add(source_relative)

    if not chapters:
        raise ExportError("No committed chapters are available for export")
    if [chapter.number for chapter in chapters] != sorted(
        chapter.number for chapter in chapters
    ):
        raise ExportError("manuscript/index.md chapter rows must be in ascending order")
    return tuple(chapters), source_normalizations


def source_snapshot_hash(root: Path, chapters: Iterable[Chapter]) -> str:
    paths = [root / "novel.json", root / "manuscript/index.md"]
    paths.extend(chapter.source_path for chapter in chapters)
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_snapshot(raw_root: str | Path) -> ProjectSnapshot:
    root = novel_project.resolve_root(str(raw_root))
    errors, warnings = novel_project.collect_validation(root)
    if errors:
        joined = "; ".join(errors[:8])
        raise ExportError(f"Project validation failed before export: {joined}")
    manifest = novel_project.read_json(root / "novel.json")
    title_value = manifest.get("title")
    if not isinstance(title_value, str) or not title_value.strip():
        raise ExportError("novel.json title must be a non-empty string")
    title, title_normalizations = normalize_unicode_for_delivery(
        title_value,
        context="novel.json title",
        multiline=False,
    )
    title = title.strip()
    language, language_normalizations = normalize_unicode_for_delivery(
        str(manifest.get("language") or "zh-CN"),
        context="novel.json language",
        multiline=False,
    )
    genre, genre_normalizations = normalize_unicode_for_delivery(
        str(manifest.get("genre") or ""),
        context="novel.json genre",
        multiline=False,
    )
    chapters, source_normalizations = parse_index(root)
    merge_normalizations(
        source_normalizations,
        title_normalizations,
        language_normalizations,
        genre_normalizations,
    )
    work_type = novel_project.work_type_for_manifest(manifest)
    if work_type == "short_story" and len(chapters) != 1:
        raise ExportError(
            "A short_story export requires exactly one indexed Markdown manuscript"
        )
    current_chapter = manifest.get("current_chapter")
    if current_chapter != chapters[-1].number:
        raise ExportError(
            "novel.json current_chapter does not match the final indexed chapter"
        )
    project_id: str | None = None
    metadata_path = root / ".novel-project.json"
    if metadata_path.is_file():
        metadata = novel_project.read_json(metadata_path)
        candidate = metadata.get("project_id")
        if isinstance(candidate, str) and candidate.strip():
            project_id, project_id_normalizations = normalize_unicode_for_delivery(
                candidate,
                context=".novel-project.json project_id",
                multiline=False,
            )
            project_id = project_id.strip()
            merge_normalizations(source_normalizations, project_id_normalizations)
    return ProjectSnapshot(
        root=root,
        title=title,
        language=language.strip(),
        genre=genre.strip(),
        work_type=work_type,
        project_id=project_id,
        current_chapter=int(current_chapter),
        source_snapshot_sha256=source_snapshot_hash(root, chapters),
        chapters=chapters,
        validation_warnings=tuple(warnings),
        source_normalizations=source_normalizations,
    )


def combined_text(snapshot: ProjectSnapshot) -> str:
    parts = [f"《{snapshot.title}》"]
    for chapter in snapshot.chapters:
        if snapshot.work_type != "short_story":
            parts.append(display_chapter_title(chapter.number, chapter.title))
        parts.append(chapter.plain_text)
    return "\n\n".join(parts).strip() + "\n"


def w_tag(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def r_attr(local: str) -> str:
    return f"{{{R_NS}}}{local}"


def w_attr(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def xml_bytes(element: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(
        element, encoding="utf-8", short_empty_elements=True
    )


def append_run(paragraph: ET.Element, text: str) -> None:
    run = ET.SubElement(paragraph, w_tag("r"))
    text_node = ET.SubElement(run, w_tag("t"))
    if text.startswith(" ") or text.endswith(" "):
        text_node.set(f"{{{XML_NS}}}space", "preserve")
    text_node.text = clean_xml_text(text)


def append_paragraph(
    parent: ET.Element,
    text: str,
    *,
    style: str = "Normal",
) -> ET.Element:
    paragraph = ET.SubElement(parent, w_tag("p"))
    properties = ET.SubElement(paragraph, w_tag("pPr"))
    ET.SubElement(properties, w_tag("pStyle"), {w_attr("val"): style})
    append_run(paragraph, text)
    return paragraph


def append_page_break(parent: ET.Element) -> None:
    paragraph = ET.SubElement(parent, w_tag("p"))
    run = ET.SubElement(paragraph, w_tag("r"))
    ET.SubElement(run, w_tag("br"), {w_attr("type"): "page"})


def add_style(
    styles: ET.Element,
    style_id: str,
    name: str,
    *,
    based_on: str | None = "Normal",
    font_ascii: str = "Times New Roman",
    font_east_asia: str = "宋体",
    size_half_points: int = 24,
    bold: bool = False,
    color: str = "000000",
    alignment: str = "both",
    before: int = 0,
    after: int = 0,
    line: int = 360,
    first_line: int = 0,
    keep_next: bool = False,
    page_break_before: bool = False,
    is_default: bool = False,
) -> None:
    attributes = {w_attr("type"): "paragraph", w_attr("styleId"): style_id}
    if is_default:
        attributes[w_attr("default")] = "1"
    style = ET.SubElement(styles, w_tag("style"), attributes)
    ET.SubElement(style, w_tag("name"), {w_attr("val"): name})
    if based_on:
        ET.SubElement(style, w_tag("basedOn"), {w_attr("val"): based_on})
    ET.SubElement(style, w_tag("qFormat"))
    p_pr = ET.SubElement(style, w_tag("pPr"))
    ET.SubElement(p_pr, w_tag("widowControl"))
    if keep_next:
        ET.SubElement(p_pr, w_tag("keepNext"))
    if page_break_before:
        ET.SubElement(p_pr, w_tag("pageBreakBefore"))
    ET.SubElement(p_pr, w_tag("jc"), {w_attr("val"): alignment})
    ET.SubElement(
        p_pr,
        w_tag("spacing"),
        {
            w_attr("before"): str(before),
            w_attr("after"): str(after),
            w_attr("line"): str(line),
            w_attr("lineRule"): "auto",
        },
    )
    ET.SubElement(
        p_pr,
        w_tag("ind"),
        {w_attr("firstLine"): str(first_line), w_attr("left"): "0"},
    )
    r_pr = ET.SubElement(style, w_tag("rPr"))
    ET.SubElement(
        r_pr,
        w_tag("rFonts"),
        {
            w_attr("ascii"): font_ascii,
            w_attr("hAnsi"): font_ascii,
            w_attr("eastAsia"): font_east_asia,
            w_attr("cs"): font_ascii,
        },
    )
    if bold:
        ET.SubElement(r_pr, w_tag("b"))
        ET.SubElement(r_pr, w_tag("bCs"))
    ET.SubElement(r_pr, w_tag("color"), {w_attr("val"): color})
    ET.SubElement(r_pr, w_tag("sz"), {w_attr("val"): str(size_half_points)})
    ET.SubElement(r_pr, w_tag("szCs"), {w_attr("val"): str(size_half_points)})
    ET.SubElement(
        r_pr,
        w_tag("lang"),
        {
            w_attr("val"): "en-US",
            w_attr("eastAsia"): "zh-CN",
        },
    )


def build_docx_styles() -> bytes:
    styles = ET.Element(w_tag("styles"))
    doc_defaults = ET.SubElement(styles, w_tag("docDefaults"))
    r_pr_default = ET.SubElement(doc_defaults, w_tag("rPrDefault"))
    r_pr = ET.SubElement(r_pr_default, w_tag("rPr"))
    ET.SubElement(
        r_pr,
        w_tag("rFonts"),
        {
            w_attr("ascii"): "Times New Roman",
            w_attr("hAnsi"): "Times New Roman",
            w_attr("eastAsia"): "宋体",
            w_attr("cs"): "Times New Roman",
        },
    )
    ET.SubElement(r_pr, w_tag("sz"), {w_attr("val"): "24"})
    ET.SubElement(r_pr, w_tag("szCs"), {w_attr("val"): "24"})
    p_pr_default = ET.SubElement(doc_defaults, w_tag("pPrDefault"))
    p_pr = ET.SubElement(p_pr_default, w_tag("pPr"))
    ET.SubElement(
        p_pr,
        w_tag("spacing"),
        {
            w_attr("before"): "0",
            w_attr("after"): "0",
            w_attr("line"): "360",
            w_attr("lineRule"): "auto",
        },
    )
    add_style(
        styles,
        "Normal",
        "Normal",
        based_on=None,
        first_line=480,
        is_default=True,
    )
    add_style(
        styles,
        "Title",
        "Title",
        font_ascii="Arial",
        font_east_asia="微软雅黑",
        size_half_points=60,
        bold=True,
        alignment="center",
        before=2400,
        after=240,
        line=360,
    )
    add_style(
        styles,
        "Subtitle",
        "Subtitle",
        font_ascii="Arial",
        font_east_asia="微软雅黑",
        size_half_points=32,
        color="555555",
        alignment="center",
        after=360,
        line=320,
    )
    add_style(
        styles,
        "Meta",
        "Metadata",
        font_ascii="Arial",
        font_east_asia="微软雅黑",
        size_half_points=21,
        color="666666",
        alignment="center",
        after=120,
        line=280,
    )
    add_style(
        styles,
        "Heading1",
        "heading 1",
        font_ascii="Arial",
        font_east_asia="微软雅黑",
        size_half_points=36,
        bold=True,
        alignment="center",
        after=480,
        line=360,
        keep_next=True,
        page_break_before=True,
    )
    add_style(
        styles,
        "Heading2",
        "heading 2",
        font_ascii="Arial",
        font_east_asia="微软雅黑",
        size_half_points=28,
        bold=True,
        alignment="left",
        before=240,
        after=160,
        line=320,
        keep_next=True,
    )
    add_style(
        styles,
        "TOC1",
        "toc 1",
        font_ascii="Arial",
        font_east_asia="微软雅黑",
        size_half_points=22,
        alignment="left",
        after=100,
        line=300,
    )
    add_style(
        styles,
        "SceneBreak",
        "Scene break",
        size_half_points=24,
        alignment="center",
        before=180,
        after=180,
        line=300,
    )
    add_style(
        styles,
        "BlockQuote",
        "Block quote",
        size_half_points=23,
        color="444444",
        alignment="left",
        before=100,
        after=100,
        line=340,
        first_line=0,
    )
    return xml_bytes(styles)


def build_docx_document(snapshot: ProjectSnapshot) -> bytes:
    document = ET.Element(w_tag("document"))
    body = ET.SubElement(document, w_tag("body"))
    append_paragraph(body, snapshot.title, style="Title")
    append_paragraph(body, "审阅稿", style="Subtitle")
    metadata = (
        f"短故事 · {snapshot.chapters[0].non_whitespace_characters} 字符"
        if snapshot.work_type == "short_story"
        else f"共 {len(snapshot.chapters)} 章"
    )
    if snapshot.genre:
        metadata = f"{snapshot.genre}  ·  {metadata}"
    append_paragraph(body, metadata, style="Meta")
    source_label = (
        "由单篇 Markdown 主稿生成"
        if snapshot.work_type == "short_story"
        else "由分章 Markdown 主稿生成"
    )
    append_paragraph(body, source_label, style="Meta")
    append_page_break(body)
    if snapshot.work_type != "short_story":
        append_paragraph(body, "目录", style="Heading2")
        for chapter in snapshot.chapters:
            append_paragraph(
                body,
                display_unit_title(snapshot, chapter),
                style="TOC1",
            )
    for chapter in snapshot.chapters:
        append_paragraph(
            body,
            display_unit_title(snapshot, chapter),
            style="Heading1",
        )
        for block in chapter.blocks:
            style = {
                "scene_break": "SceneBreak",
                "subheading": "Heading2",
                "blockquote": "BlockQuote",
            }.get(block.kind, "Normal")
            append_paragraph(body, block.text, style=style)

    section = ET.SubElement(body, w_tag("sectPr"))
    ET.SubElement(
        section,
        w_tag("footerReference"),
        {w_attr("type"): "default", r_attr("id"): "rId4"},
    )
    ET.SubElement(
        section,
        w_tag("footerReference"),
        {w_attr("type"): "first", r_attr("id"): "rId5"},
    )
    ET.SubElement(section, w_tag("titlePg"))
    ET.SubElement(
        section,
        w_tag("pgSz"),
        {w_attr("w"): "11906", w_attr("h"): "16838"},
    )
    ET.SubElement(
        section,
        w_tag("pgMar"),
        {
            w_attr("top"): "1440",
            w_attr("right"): "1440",
            w_attr("bottom"): "1440",
            w_attr("left"): "1440",
            w_attr("header"): "720",
            w_attr("footer"): "720",
            w_attr("gutter"): "0",
        },
    )
    ET.SubElement(section, w_tag("cols"), {w_attr("space"): "720"})
    ET.SubElement(section, w_tag("docGrid"), {w_attr("linePitch"): "312"})
    return xml_bytes(document)


def build_docx_footer(*, empty: bool = False) -> bytes:
    footer = ET.Element(w_tag("ftr"))
    paragraph = ET.SubElement(footer, w_tag("p"))
    properties = ET.SubElement(paragraph, w_tag("pPr"))
    ET.SubElement(properties, w_tag("jc"), {w_attr("val"): "center"})
    if not empty:
        run = ET.SubElement(paragraph, w_tag("r"))
        run_properties = ET.SubElement(run, w_tag("rPr"))
        ET.SubElement(run_properties, w_tag("color"), {w_attr("val"): "777777"})
        ET.SubElement(run_properties, w_tag("sz"), {w_attr("val"): "18"})
        ET.SubElement(run, w_tag("fldChar"), {w_attr("fldCharType"): "begin"})
        instruction_run = ET.SubElement(paragraph, w_tag("r"))
        instruction = ET.SubElement(instruction_run, w_tag("instrText"))
        instruction.set(f"{{{XML_NS}}}space", "preserve")
        instruction.text = " PAGE "
        separate_run = ET.SubElement(paragraph, w_tag("r"))
        ET.SubElement(
            separate_run, w_tag("fldChar"), {w_attr("fldCharType"): "separate"}
        )
        append_run(paragraph, "1")
        end_run = ET.SubElement(paragraph, w_tag("r"))
        ET.SubElement(end_run, w_tag("fldChar"), {w_attr("fldCharType"): "end"})
    return xml_bytes(footer)


def write_docx(path: Path, snapshot: ProjectSnapshot, generated_at: str) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>
  <Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
  <Override PartName="/word/footer2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
""".encode("utf-8")
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
""".encode("utf-8")
    document_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer2.xml"/>
</Relationships>
""".encode("utf-8")
    settings = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="{W_NS}">
  <w:zoom w:percent="100"/>
  <w:defaultTabStop w:val="720"/>
  <w:characterSpacingControl w:val="doNotCompress"/>
  <w:compat/>
</w:settings>
""".encode("utf-8")
    font_table = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:fonts xmlns:w="{W_NS}">
  <w:font w:name="宋体"><w:family w:val="roman"/><w:charset w:val="86"/></w:font>
  <w:font w:name="微软雅黑"><w:family w:val="swiss"/><w:charset w:val="86"/></w:font>
  <w:font w:name="Times New Roman"><w:family w:val="roman"/></w:font>
  <w:font w:name="Arial"><w:family w:val="swiss"/></w:font>
</w:fonts>
""".encode("utf-8")
    timestamp = epub_timestamp(generated_at)
    title_xml = html.escape(clean_xml_text(snapshot.title), quote=True)
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="{CP_NS}" xmlns:dc="{DC_NS}" xmlns:dcterms="{DCTERMS_NS}" xmlns:xsi="{XSI_NS}">
  <dc:title>{title_xml} - 审阅稿</dc:title>
  <dc:creator>Chinese Novel Studio</dc:creator>
  <dc:language>{html.escape(snapshot.language, quote=True)}</dc:language>
  <dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>
</cp:coreProperties>
""".encode("utf-8")
    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Chinese Novel Studio</Application>
</Properties>
""".encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", build_docx_document(snapshot))
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("word/styles.xml", build_docx_styles())
        archive.writestr("word/settings.xml", settings)
        archive.writestr("word/fontTable.xml", font_table)
        archive.writestr("word/footer1.xml", build_docx_footer())
        archive.writestr("word/footer2.xml", build_docx_footer(empty=True))
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)


def chapter_xhtml(snapshot: ProjectSnapshot, chapter: Chapter) -> str:
    body: list[str] = []
    for block in chapter.blocks:
        escaped = html.escape(clean_xml_text(block.text), quote=False)
        if block.kind == "scene_break":
            body.append(f'<p class="scene-break">{escaped}</p>')
        elif block.kind == "subheading":
            body.append(f"<h2>{escaped}</h2>")
        elif block.kind == "blockquote":
            body.append(f"<blockquote><p>{escaped}</p></blockquote>")
        else:
            body.append(f"<p>{escaped}</p>")
    heading = html.escape(
        display_unit_title(snapshot, chapter), quote=False
    )
    language = html.escape(snapshot.language, quote=True)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{language}" lang="{language}">
<head>
  <meta charset="utf-8"/>
  <title>{heading}</title>
  <link rel="stylesheet" type="text/css" href="../styles/novel.css"/>
</head>
<body>
  <section class="chapter" epub:type="chapter" xmlns:epub="{EPUB_NS}">
    <h1>{heading}</h1>
    {''.join(body)}
  </section>
</body>
</html>
"""


def write_epub(path: Path, snapshot: ProjectSnapshot, generated_at: str) -> None:
    identifier = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, snapshot.source_snapshot_sha256)}"
    title = html.escape(clean_xml_text(snapshot.title), quote=False)
    language = html.escape(snapshot.language, quote=True)
    modified = epub_timestamp(generated_at)
    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="styles/novel.css" media-type="text/css"/>',
        '<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine_items = ['<itemref idref="cover"/>']
    nav_items: list[str] = []
    ncx_points: list[str] = []
    chapter_documents: list[tuple[str, str]] = []
    for position, chapter in enumerate(snapshot.chapters, start=1):
        chapter_id = f"chapter-{chapter.number_text}"
        href = f"text/{chapter_id}.xhtml"
        heading = html.escape(
            display_unit_title(snapshot, chapter), quote=False
        )
        manifest_items.append(
            f'<item id="{chapter_id}" href="{href}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{chapter_id}"/>')
        nav_items.append(f'<li><a href="{href}">{heading}</a></li>')
        ncx_points.append(
            f'<navPoint id="nav-{chapter.number_text}" playOrder="{position}">'
            f"<navLabel><text>{heading}</text></navLabel>"
            f"<content src=\"{href}\"/></navPoint>"
        )
        chapter_documents.append((f"EPUB/{href}", chapter_xhtml(snapshot, chapter)))

    package = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="{language}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{identifier}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>{language}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>{''.join(manifest_items)}</manifest>
  <spine toc="ncx">{''.join(spine_items)}</spine>
</package>
"""
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="{EPUB_NS}" xml:lang="{language}" lang="{language}">
<head><meta charset="utf-8"/><title>目录</title><link rel="stylesheet" type="text/css" href="styles/novel.css"/></head>
<body><nav epub:type="toc" id="toc"><h1>目录</h1><ol>{''.join(nav_items)}</ol></nav></body>
</html>
"""
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{identifier}"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap>{''.join(ncx_points)}</navMap>
</ncx>
"""
    cover = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{language}" lang="{language}">
<head><meta charset="utf-8"/><title>{title}</title><link rel="stylesheet" type="text/css" href="../styles/novel.css"/></head>
<body class="cover"><h1>{title}</h1><p>电子阅读版</p><p>{'短故事' if snapshot.work_type == 'short_story' else f'共 {len(snapshot.chapters)} 章'}</p></body>
</html>
"""
    css = """html { writing-mode: horizontal-tb; }
body { margin: 5%; font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", serif; line-height: 1.8; }
p { margin: 0 0 0.75em 0; text-indent: 2em; text-align: justify; }
h1 { margin: 1.8em 0 1.5em; text-align: center; font-size: 1.55em; }
h2 { margin: 1.4em 0 0.8em; font-size: 1.15em; }
.scene-break { text-align: center; text-indent: 0; letter-spacing: 0.35em; margin: 1.4em 0; }
blockquote { margin: 1em 1.5em; color: #444; }
blockquote p { text-indent: 0; }
.cover { text-align: center; padding-top: 28%; }
.cover h1 { font-size: 2em; margin-bottom: 2em; }
.cover p { text-indent: 0; text-align: center; color: #555; }
nav ol { padding-left: 1.5em; }
nav li { margin: 0.45em 0; }
nav a { color: inherit; text-decoration: none; }
"""
    container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype_info, "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED
        )
        archive.writestr(
            "EPUB/package.opf", package, compress_type=zipfile.ZIP_DEFLATED
        )
        archive.writestr("EPUB/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("EPUB/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr(
            "EPUB/text/cover.xhtml", cover, compress_type=zipfile.ZIP_DEFLATED
        )
        archive.writestr(
            "EPUB/styles/novel.css", css, compress_type=zipfile.ZIP_DEFLATED
        )
        for archive_path, content in chapter_documents:
            archive.writestr(
                archive_path, content, compress_type=zipfile.ZIP_DEFLATED
            )


def validate_xml_tree_unicode(element: ET.Element, *, context: str) -> None:
    for node in element.iter():
        values = [node.text, node.tail, *node.attrib.values()]
        for value in values:
            if value is None:
                continue
            validate_unicode_safety(
                value,
                context=context,
                allowed_controls=frozenset({"\n", "\t"}),
            )
            if unicodedata.normalize("NFC", value) != value:
                raise ExportError(f"{context} contains text that is not Unicode NFC")


def validate_zip_member_names(names: list[str], *, context: str) -> None:
    if len(names) != len(set(names)):
        raise ExportError(f"{context} contains duplicate ZIP member names")
    for name in names:
        normalized = name.replace("\\", "/")
        parts = normalized.split("/")
        if (
            not normalized
            or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ExportError(f"{context} contains an unsafe ZIP member path: {name}")


def validate_text_delivery(
    path: Path,
    profile: DeliveryProfile,
    *,
    forbidden_first_lines: Iterable[str] = (),
) -> dict[str, Any]:
    if profile.kind != "text":
        raise ExportError(f"Delivery profile is not a text profile: {profile.name}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExportError(f"Cannot read text export {path.name}: {exc}") from exc
    if UTF8_BOM in raw:
        location = "at the beginning" if raw.startswith(UTF8_BOM) else "inside the file"
        raise ExportError(f"Text export {path.name} contains a UTF-8 BOM {location}")
    try:
        content = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExportError(f"UTF-8 text validation failed for {path.name}: {exc}") from exc
    if "\r" in content:
        raise ExportError(f"Text export must use LF line endings only: {path.name}")
    if unicodedata.normalize("NFC", content) != content:
        raise ExportError(f"Text export is not Unicode NFC: {path.name}")
    validate_unicode_safety(
        content,
        context=f"text export {path.name}",
        allowed_controls=frozenset({"\n"}),
    )
    exotic_spaces = [
        character
        for character in content
        if character in NORMALIZABLE_HORIZONTAL_SPACES
    ]
    if exotic_spaces:
        codepoint = ord(exotic_spaces[0])
        raise ExportError(
            f"Text export {path.name} contains non-portable horizontal whitespace "
            f"U+{codepoint:04X}"
        )
    if not content.strip():
        raise ExportError(f"Text export is empty: {path.name}")
    if not content.endswith("\n") or content.endswith("\n\n"):
        raise ExportError(
            f"Text export must end with exactly one LF newline: {path.name}"
        )
    lines = content[:-1].split("\n")
    if any(line.endswith(" ") for line in lines):
        raise ExportError(f"Text export contains trailing spaces: {path.name}")
    if "\n\n\n" in content:
        raise ExportError(
            f"Text export contains more than one consecutive blank line: {path.name}"
        )
    max_line = max((len(line) for line in lines), default=0)
    if profile.max_line_characters is not None and max_line > profile.max_line_characters:
        raise ExportError(
            f"Text export {path.name} contains a {max_line}-character line; "
            f"the delivery guardrail is {profile.max_line_characters}"
        )
    for label, pattern in MARKDOWN_RESIDUAL_PATTERNS:
        if pattern.search(content):
            raise ExportError(f"Text export {path.name} still contains {label}")
    if profile.scene_break_policy == "blank_line_only":
        for line in lines:
            if SCENE_BREAK_LINE.fullmatch(line):
                raise ExportError(
                    f"Text export {path.name} still contains a standalone scene-break marker"
                )
    first_line = next((line.strip() for line in lines if line.strip()), "")
    forbidden = {line.strip() for line in forbidden_first_lines if line.strip()}
    if first_line and first_line in forbidden:
        raise ExportError(
            f"Text export {path.name} duplicates its platform title in the body"
        )
    return {
        "status": "pass",
        "profile": profile.name,
        "profile_version": DELIVERY_PROFILE_VERSION,
        "encoding": "UTF-8",
        "bom": False,
        "line_endings": "LF",
        "unicode_normalization": "NFC",
        "final_newline": "single_lf",
        "line_count": len(lines),
        "max_line_characters": max_line,
        "non_whitespace_characters": sum(
            1 for character in content if not character.isspace()
        ),
        "canonical_derived_text_match": False,
        "checks": list(profile.checks),
    }


def validate_docx(
    path: Path,
    expected_chapters: int,
    snapshot: ProjectSnapshot | None = None,
) -> dict[str, Any]:
    required = {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/styles.xml",
        "word/settings.xml",
        "word/fontTable.xml",
        "word/footer1.xml",
        "word/footer2.xml",
    }
    try:
        with zipfile.ZipFile(path) as archive:
            member_names = archive.namelist()
            validate_zip_member_names(member_names, context="DOCX")
            names = set(member_names)
            missing = required - names
            if missing:
                raise ExportError(f"DOCX is missing package parts: {sorted(missing)}")
            if archive.testzip() is not None:
                raise ExportError("DOCX ZIP integrity check failed")
            trees: dict[str, ET.Element] = {}
            for name in member_names:
                if name.endswith((".xml", ".rels")):
                    tree = ET.fromstring(archive.read(name))
                    validate_xml_tree_unicode(tree, context=f"DOCX member {name}")
                    trees[name] = tree
            document = trees["word/document.xml"]
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ExportError(f"DOCX validation failed: {exc}") from exc
    headings = document.findall(f".//{w_tag('pStyle')}[@{w_attr('val')}='Heading1']")
    if len(headings) != expected_chapters:
        raise ExportError(
            f"DOCX chapter heading count mismatch: expected {expected_chapters}, got {len(headings)}"
        )
    if snapshot is not None:
        document_text = "\n".join(document.itertext())
        for chapter in snapshot.chapters:
            heading = display_unit_title(snapshot, chapter)
            if heading not in document_text:
                raise ExportError(
                    f"DOCX is missing the canonical heading for chapter {chapter.number_text}"
                )
            for block in chapter.blocks:
                if block.text not in document_text:
                    raise ExportError(
                        f"DOCX is missing canonical text from chapter {chapter.number_text}"
                    )
    return {
        "status": "pass",
        "profile": DOCX_REVIEW_PROFILE.name,
        "profile_version": DELIVERY_PROFILE_VERSION,
        "chapter_count": expected_chapters,
        "canonical_text_coverage": snapshot is not None,
        "checks": list(DOCX_REVIEW_PROFILE.checks),
    }


def validate_epub(
    path: Path,
    expected_chapters: int,
    snapshot: ProjectSnapshot | None = None,
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            validate_zip_member_names(
                [info.filename for info in infos],
                context="EPUB",
            )
            if not infos or infos[0].filename != "mimetype":
                raise ExportError("EPUB mimetype must be the first ZIP entry")
            if infos[0].compress_type != zipfile.ZIP_STORED:
                raise ExportError("EPUB mimetype must be uncompressed")
            if archive.read("mimetype") != b"application/epub+zip":
                raise ExportError("EPUB mimetype content is invalid")
            if archive.testzip() is not None:
                raise ExportError("EPUB ZIP integrity check failed")
            required = {
                "META-INF/container.xml",
                "EPUB/package.opf",
                "EPUB/nav.xhtml",
                "EPUB/toc.ncx",
                "EPUB/text/cover.xhtml",
                "EPUB/styles/novel.css",
            }
            missing = required - set(archive.namelist())
            if missing:
                raise ExportError(f"EPUB is missing package parts: {sorted(missing)}")
            trees: dict[str, ET.Element] = {}
            for name in archive.namelist():
                if name.endswith((".xml", ".xhtml", ".opf", ".ncx")):
                    tree = ET.fromstring(archive.read(name))
                    validate_xml_tree_unicode(tree, context=f"EPUB member {name}")
                    trees[name] = tree
            chapter_names = [
                name
                for name in archive.namelist()
                if re.fullmatch(r"EPUB/text/chapter-\d{4}\.xhtml", name)
            ]
            if len(chapter_names) != expected_chapters:
                raise ExportError(
                    "EPUB chapter count mismatch: "
                    f"expected {expected_chapters}, got {len(chapter_names)}"
                )
            for name in chapter_names:
                if name not in trees:
                    raise ExportError(f"EPUB chapter XML was not validated: {name}")
            if snapshot is not None:
                for chapter in snapshot.chapters:
                    name = f"EPUB/text/chapter-{chapter.number_text}.xhtml"
                    tree = trees.get(name)
                    if tree is None:
                        raise ExportError(
                            f"EPUB is missing chapter document {chapter.number_text}"
                        )
                    chapter_text = "\n".join(tree.itertext())
                    heading = display_unit_title(snapshot, chapter)
                    if heading not in chapter_text:
                        raise ExportError(
                            f"EPUB is missing the canonical heading for chapter {chapter.number_text}"
                        )
                    for block in chapter.blocks:
                        if block.text not in chapter_text:
                            raise ExportError(
                                f"EPUB is missing canonical text from chapter {chapter.number_text}"
                            )
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ExportError(f"EPUB validation failed: {exc}") from exc
    return {
        "status": "pass",
        "profile": EPUB3_PROFILE.name,
        "profile_version": DELIVERY_PROFILE_VERSION,
        "chapter_count": expected_chapters,
        "canonical_text_coverage": snapshot is not None,
        "checks": list(EPUB3_PROFILE.checks),
    }


def validate_text(path: Path) -> dict[str, Any]:
    return validate_text_delivery(path, GENERIC_TEXT_PROFILE)


def normalize_formats(values: list[str] | None) -> set[str]:
    if not values or "all" in values:
        return set(SUPPORTED_FORMATS)
    selected = set(values)
    unsupported = selected - SUPPORTED_FORMATS
    if unsupported:
        raise ExportError(f"Unsupported formats: {sorted(unsupported)}")
    return selected


def validate_managed_relative_path(relative: str) -> str:
    normalized = relative.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise ExportError(f"Invalid managed output path: {relative}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ExportError(f"Invalid managed output path: {relative}")
    for part in parts:
        safe_part, counts = normalize_unicode_for_delivery(
            part,
            context=f"managed output filename {part}",
            multiline=False,
        )
        if safe_part != part or any(counts.values()):
            raise ExportError(
                f"Managed output filename is not in portable normalized form: {part}"
            )
        if INVALID_FILENAME.search(part) or part.endswith((" ", ".")):
            raise ExportError(f"Managed output filename is not portable: {part}")
        if RESERVED_FILENAME.fullmatch(part):
            raise ExportError(f"Managed output filename uses a Windows device name: {part}")
        if len(part.encode("utf-16-le")) // 2 > 120:
            raise ExportError(f"Managed output filename is too long: {part}")
    if len(normalized.encode("utf-16-le")) // 2 > 240:
        raise ExportError(f"Managed relative output path is too long: {relative}")
    return normalized


def output_path(output_root: Path, relative: str) -> Path:
    normalized = validate_managed_relative_path(relative)
    if not normalized or normalized == EXPORT_MANIFEST:
        raise ExportError(f"Invalid managed output path: {relative}")
    target = (output_root / Path(normalized)).resolve()
    try:
        target.relative_to(output_root.resolve())
    except ValueError as exc:
        raise ExportError(f"Managed output leaves exports directory: {relative}") from exc
    return target


def read_previous_manifest(output_root: Path) -> dict[str, Any] | None:
    path = output_root / EXPORT_MANIFEST
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        if UTF8_BOM in raw:
            raise ExportError("Existing export-manifest.json contains a UTF-8 BOM")
        content = raw.decode("utf-8", errors="strict")
        if "\r" in content:
            raise ExportError("Existing export-manifest.json must use LF line endings")
        if unicodedata.normalize("NFC", content) != content:
            raise ExportError("Existing export-manifest.json is not Unicode NFC")
        validate_unicode_safety(
            content,
            context="export-manifest.json",
            allowed_controls=frozenset({"\n"}),
        )
        data = json.loads(content)
    except ExportError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(
            "Existing export-manifest.json is invalid; move it aside before exporting"
        ) from exc
    if not isinstance(data, dict) or data.get("schema_version") != EXPORT_SCHEMA_VERSION:
        raise ExportError("Existing export-manifest.json has an unsupported schema")
    if not isinstance(data.get("outputs"), list):
        raise ExportError("Existing export-manifest.json outputs must be a list")
    if not isinstance(data.get("source"), dict):
        raise ExportError("Existing export-manifest.json source must be an object")
    return data


def record_for(
    path: Path,
    relative: str,
    format_name: str,
    profile: DeliveryProfile,
    quality: dict[str, Any],
    *,
    normalizations: dict[str, int] | None = None,
) -> dict[str, Any]:
    normalized_relative = validate_managed_relative_path(relative)
    return {
        "path": normalized_relative,
        "format": format_name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "delivery_profile": {
            "name": profile.name,
            "version": DELIVERY_PROFILE_VERSION,
        },
        "quality": quality,
        "normalizations_applied": dict(
            normalizations if normalizations is not None else empty_normalizations()
        ),
    }


def validate_output_record(output_root: Path, record: dict[str, Any]) -> str | None:
    relative = record.get("path")
    expected = record.get("sha256")
    expected_bytes = record.get("bytes")
    if (
        not isinstance(relative, str)
        or not isinstance(expected, str)
        or not isinstance(expected_bytes, int)
        or expected_bytes < 0
    ):
        return "manifest output record is malformed"
    try:
        path = output_path(output_root, relative)
    except ExportError as exc:
        return str(exc)
    if path.is_symlink():
        return f"managed output is a symbolic link: {relative}"
    if not path.is_file():
        return f"managed output is missing: {relative}"
    if path.stat().st_size != expected_bytes:
        return f"managed output byte count changed: {relative}"
    if sha256_file(path) != expected:
        return f"managed output was modified: {relative}"
    return None


def profile_for_output(
    format_name: str,
    relative: str,
    snapshot: ProjectSnapshot | None = None,
) -> DeliveryProfile:
    if format_name == "txt":
        return GENERIC_TEXT_PROFILE
    if format_name == "docx":
        return DOCX_REVIEW_PROFILE
    if format_name == "epub":
        return EPUB3_PROFILE
    if format_name != "fanqie":
        raise ExportError(f"Unknown output format in manifest: {format_name}")
    normalized = validate_managed_relative_path(relative)
    if normalized.startswith("fanqie-short-story/"):
        if snapshot is not None and snapshot.work_type != "short_story":
            raise ExportError("A serial novel cannot use the fanqie-short-story profile")
        return FANQIE_SHORT_STORY_PROFILE
    if normalized.startswith("fanqie/"):
        if snapshot is not None and snapshot.work_type == "short_story":
            raise ExportError("A short story cannot use the fanqie-serial profile")
        return FANQIE_SERIAL_PROFILE
    raise ExportError(f"Fanqie output is outside a recognized package: {relative}")


def export_base(snapshot: ProjectSnapshot) -> str:
    title = safe_filename(snapshot.title, fallback="novel")
    return f"《{title}》"


def fanqie_relative(snapshot: ProjectSnapshot, chapter: Chapter) -> str:
    base = export_base(snapshot)
    if snapshot.work_type == "short_story":
        return f"fanqie-short-story/{base}.txt"
    chapter_title = safe_filename(
        chapter.title,
        fallback=f"第{chapter.number}章",
        maximum=80,
    )
    return f"fanqie/{chapter.number_text}-{chapter_title}.txt"


def expected_output_paths(
    snapshot: ProjectSnapshot,
    formats: Iterable[str],
) -> dict[str, str]:
    selected = set(formats)
    base = export_base(snapshot)
    short_story = snapshot.work_type == "short_story"
    paths: dict[str, str] = {}
    if "txt" in selected:
        relative = (
            f"{base}-短故事定稿.txt" if short_story else f"{base}-全书合并稿.txt"
        )
        paths[relative] = "txt"
    if "docx" in selected:
        relative = f"{base}-短故事审阅稿.docx" if short_story else f"{base}-审阅稿.docx"
        paths[relative] = "docx"
    if "epub" in selected:
        paths[f"{base}.epub"] = "epub"
    if "fanqie" in selected:
        for chapter in snapshot.chapters:
            paths[fanqie_relative(snapshot, chapter)] = "fanqie"
    return paths


def fanqie_context(
    snapshot: ProjectSnapshot,
    relative: str,
) -> tuple[Chapter, tuple[str, ...]]:
    matches = [
        chapter
        for chapter in snapshot.chapters
        if fanqie_relative(snapshot, chapter) == relative
    ]
    if len(matches) != 1:
        raise ExportError(f"Fanqie path does not map to exactly one chapter: {relative}")
    chapter = matches[0]
    variants = {
        chapter.title,
        display_unit_title(snapshot, chapter),
    }
    if snapshot.work_type == "short_story":
        variants.update({snapshot.title, f"《{snapshot.title}》"})
    return chapter, tuple(sorted(variants))


def validate_text_matches_snapshot(
    path: Path,
    relative: str,
    format_name: str,
    snapshot: ProjectSnapshot,
) -> None:
    if format_name == "txt":
        expected_paths = expected_output_paths(snapshot, {"txt"})
        if expected_paths.get(relative) != "txt":
            raise ExportError(f"Generic TXT path does not match the canonical title: {relative}")
        expected_text = combined_text(snapshot)
    elif format_name == "fanqie":
        chapter, _ = fanqie_context(snapshot, relative)
        expected_text = blocks_to_fanqie_text(chapter.blocks)
    else:
        raise ExportError(f"Canonical text comparison does not support {format_name}")
    normalized, _ = prepare_utf8_document(
        expected_text,
        context=f"canonical comparison for {relative}",
    )
    if path.read_bytes() != normalized.encode("utf-8"):
        raise ExportError(
            f"Text export does not exactly match the canonical derived text: {relative}"
        )


def quality_for_output(
    path: Path,
    relative: str,
    format_name: str,
    *,
    snapshot: ProjectSnapshot | None,
    expected_chapters: int,
) -> tuple[DeliveryProfile, dict[str, Any]]:
    profile = profile_for_output(format_name, relative, snapshot)
    if format_name == "txt":
        quality = validate_text_delivery(path, profile)
        if snapshot is not None:
            validate_text_matches_snapshot(path, relative, format_name, snapshot)
        quality["canonical_derived_text_match"] = snapshot is not None
        return profile, quality
    if format_name == "fanqie":
        forbidden_titles: tuple[str, ...] = ()
        if snapshot is not None:
            _, forbidden_titles = fanqie_context(snapshot, relative)
        quality = validate_text_delivery(
            path,
            profile,
            forbidden_first_lines=forbidden_titles,
        )
        if snapshot is not None:
            validate_text_matches_snapshot(path, relative, format_name, snapshot)
        quality["canonical_derived_text_match"] = snapshot is not None
        return profile, quality
    if format_name == "docx":
        return profile, validate_docx(path, expected_chapters, snapshot)
    if format_name == "epub":
        return profile, validate_epub(path, expected_chapters, snapshot)
    raise ExportError(f"Unsupported output format: {format_name}")


def validate_record_quality_metadata(
    record: dict[str, Any],
    profile: DeliveryProfile,
    quality: dict[str, Any],
    *,
    compare_quality: bool,
) -> str | None:
    expected_profile = {
        "name": profile.name,
        "version": DELIVERY_PROFILE_VERSION,
    }
    if record.get("delivery_profile") != expected_profile:
        return f"manifest delivery profile is missing or wrong: {record.get('path')}"
    if compare_quality and record.get("quality") != quality:
        return f"manifest quality report is missing or stale: {record.get('path')}"
    normalizations = record.get("normalizations_applied")
    if not isinstance(normalizations, dict):
        return f"manifest normalization report is missing: {record.get('path')}"
    for key in NORMALIZATION_KEYS:
        value = normalizations.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"manifest normalization report is malformed: {record.get('path')}"
    return None


def rebuild_record_from_existing(
    output_root: Path,
    record: dict[str, Any],
    snapshot: ProjectSnapshot,
) -> dict[str, Any]:
    relative = str(record["path"])
    format_name = str(record["format"])
    path = output_path(output_root, relative)
    profile, quality = quality_for_output(
        path,
        relative,
        format_name,
        snapshot=snapshot,
        expected_chapters=len(snapshot.chapters),
    )
    return record_for(path, relative, format_name, profile, quality)


def unmanaged_export_files(
    output_root: Path,
    manifest: dict[str, Any] | None,
) -> list[str]:
    if not output_root.is_dir():
        return []
    managed = {EXPORT_MANIFEST}
    if manifest is not None:
        for record in manifest.get("outputs", []):
            if isinstance(record, dict) and isinstance(record.get("path"), str):
                managed.add(record["path"].replace("\\", "/"))
    extras: list[str] = []
    for path in output_root.rglob("*"):
        if not path.is_file() and not path.is_symlink():
            continue
        relative = path.relative_to(output_root).as_posix()
        if relative not in managed:
            extras.append(relative)
    return sorted(extras)


def planned_outputs(
    staging: Path,
    snapshot: ProjectSnapshot,
    formats: set[str],
    generated_at: str,
) -> list[dict[str, Any]]:
    base = export_base(snapshot)
    short_story = snapshot.work_type == "short_story"
    records: list[dict[str, Any]] = []
    if "txt" in formats:
        relative = (
            f"{base}-短故事定稿.txt" if short_story else f"{base}-全书合并稿.txt"
        )
        path = staging / relative
        normalizations = write_utf8(
            path,
            combined_text(snapshot),
            context=f"generic text export {relative}",
        )
        _, quality = quality_for_output(
            path,
            relative,
            "txt",
            snapshot=snapshot,
            expected_chapters=len(snapshot.chapters),
        )
        records.append(
            record_for(
                path,
                relative,
                "txt",
                GENERIC_TEXT_PROFILE,
                quality,
                normalizations=normalizations,
            )
        )
    if "docx" in formats:
        relative = f"{base}-短故事审阅稿.docx" if short_story else f"{base}-审阅稿.docx"
        path = staging / relative
        write_docx(path, snapshot, generated_at)
        quality = validate_docx(path, len(snapshot.chapters), snapshot)
        records.append(
            record_for(
                path,
                relative,
                "docx",
                DOCX_REVIEW_PROFILE,
                quality,
            )
        )
    if "epub" in formats:
        relative = f"{base}.epub"
        path = staging / relative
        write_epub(path, snapshot, generated_at)
        quality = validate_epub(path, len(snapshot.chapters), snapshot)
        records.append(
            record_for(
                path,
                relative,
                "epub",
                EPUB3_PROFILE,
                quality,
            )
        )
    if "fanqie" in formats:
        if short_story:
            chapter = snapshot.chapters[0]
            relative = fanqie_relative(snapshot, chapter)
            path = staging / Path(relative)
            normalizations = write_utf8(
                path,
                blocks_to_fanqie_text(chapter.blocks),
                context=f"Fanqie short-story export {relative}",
            )
            _, quality = quality_for_output(
                path,
                relative,
                "fanqie",
                snapshot=snapshot,
                expected_chapters=len(snapshot.chapters),
            )
            records.append(
                record_for(
                    path,
                    relative,
                    "fanqie",
                    FANQIE_SHORT_STORY_PROFILE,
                    quality,
                    normalizations=normalizations,
                )
            )
        else:
            for chapter in snapshot.chapters:
                relative = fanqie_relative(snapshot, chapter)
                path = staging / Path(relative)
                normalizations = write_utf8(
                    path,
                    blocks_to_fanqie_text(chapter.blocks),
                    context=f"Fanqie serial export {relative}",
                )
                _, quality = quality_for_output(
                    path,
                    relative,
                    "fanqie",
                    snapshot=snapshot,
                    expected_chapters=len(snapshot.chapters),
                )
                records.append(
                    record_for(
                        path,
                        relative,
                        "fanqie",
                        FANQIE_SERIAL_PROFILE,
                        quality,
                        normalizations=normalizations,
                    )
                )
    return records


def export_project(args: argparse.Namespace) -> dict[str, Any]:
    try:
        novel_continuity.ensure_delivery_allowed(args.root)
    except novel_continuity.ContinuityError as exc:
        raise ExportError(str(exc)) from exc
    snapshot = load_snapshot(args.root)
    review = novel_review.review_status(snapshot.root)
    if review["review_due"]:
        if snapshot.work_type == "short_story":
            raise ExportError(
                "The complete short story must pass a current full-manuscript "
                "independent quality completion review before formal exports are generated"
            )
        raise ExportError(
            "The due independent periodic quality review must pass before formal "
            "exports or platform delivery are generated"
        )
    formats = normalize_formats(args.format)
    output_root = snapshot.root / "exports"
    if output_root.is_symlink():
        raise ExportError("Project exports directory cannot be a symbolic link")
    output_root.mkdir(parents=True, exist_ok=True)
    previous = read_previous_manifest(output_root)
    unmanaged = unmanaged_export_files(output_root, previous)
    if unmanaged:
        raise ExportError(
            "Exports directory contains unmanaged files; move them outside the "
            f"delivery package before exporting: {', '.join(unmanaged[:8])}"
        )
    generated_at = utc_now()

    with tempfile.TemporaryDirectory(prefix=".novel-export-", dir=output_root) as temp:
        staging = Path(temp)
        new_records = planned_outputs(staging, snapshot, formats, generated_at)
        new_by_path = {record["path"]: record for record in new_records}
        previous_records = previous.get("outputs", []) if previous else []
        previous_source = (
            previous["source"].get("source_snapshot_sha256") if previous else None
        )
        same_source = previous_source == snapshot.source_snapshot_sha256
        preserved: list[dict[str, Any]] = []
        stale_paths: list[Path] = []

        for record in previous_records:
            if not isinstance(record, dict):
                raise ExportError("Existing export manifest contains a malformed record")
            relative = record.get("path")
            format_name = record.get("format")
            if not isinstance(relative, str) or not isinstance(format_name, str):
                raise ExportError("Existing export manifest contains a malformed record")
            target = output_path(output_root, relative)
            if relative in new_by_path:
                continue
            issue = validate_output_record(output_root, record)
            if same_source and format_name not in formats and issue is None:
                preserved.append(
                    rebuild_record_from_existing(output_root, record, snapshot)
                )
                continue
            if issue is not None and not args.force:
                raise ExportError(f"{issue}; use --force only if replacement is intended")
            if target.exists() or target.is_symlink():
                stale_paths.append(target)

        previous_by_path = {
            record.get("path"): record
            for record in previous_records
            if isinstance(record, dict) and isinstance(record.get("path"), str)
        }
        for relative in new_by_path:
            target = output_path(output_root, relative)
            if not target.exists() and not target.is_symlink():
                continue
            previous_record = previous_by_path.get(relative)
            issue = (
                validate_output_record(output_root, previous_record)
                if isinstance(previous_record, dict)
                else f"unmanaged file already exists: {relative}"
            )
            if issue is not None and not args.force:
                raise ExportError(f"{issue}; use --force only if replacement is intended")

        refreshed = load_snapshot(snapshot.root)
        if refreshed.source_snapshot_sha256 != snapshot.source_snapshot_sha256:
            raise ExportError("Canonical Markdown changed during export; run export again")

        output_records = sorted(
            [*preserved, *new_records], key=lambda item: item["path"]
        )
        output_formats = {record["format"] for record in output_records}
        expected_layout = expected_output_paths(snapshot, output_formats)
        actual_layout = {
            record["path"]: record["format"] for record in output_records
        }
        if actual_layout != expected_layout:
            raise ExportError(
                "Derived output inventory does not match the canonical chapter and title mapping"
            )
        source_manifest = {
            "title": snapshot.title,
            "language": snapshot.language,
            "genre": snapshot.genre,
            "project_id": snapshot.project_id,
            "current_chapter": snapshot.current_chapter,
            "chapter_count": len(snapshot.chapters),
            "source_snapshot_sha256": snapshot.source_snapshot_sha256,
            "chapters": [
                {
                    "number": chapter.number_text,
                    "title": chapter.title,
                    "source": chapter.source_relative,
                    "source_sha256": chapter.source_sha256,
                    "non_whitespace_characters": chapter.non_whitespace_characters,
                    "delivery_normalizations": dict(
                        chapter.source_normalizations
                    ),
                }
                for chapter in snapshot.chapters
            ],
        }
        if snapshot.work_type == "short_story":
            source_manifest["work_type"] = snapshot.work_type
        output_normalizations = empty_normalizations()
        for record in output_records:
            merge_normalizations(
                output_normalizations,
                record.get("normalizations_applied", {}),
            )
        used_profile_names = sorted(
            {
                record["delivery_profile"]["name"]
                for record in output_records
            }
        )
        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "kind": (
                "chinese-short-story-derived-exports"
                if snapshot.work_type == "short_story"
                else "chinese-novel-derived-exports"
            ),
            "generated_at": generated_at,
            "source": source_manifest,
            "encoding": "UTF-8 without BOM",
            "line_endings": "LF",
            "unicode_normalization": "NFC",
            "fanqie_title_mode": (
                "single_story_filename_only_body_text"
                if snapshot.work_type == "short_story"
                else "filename_only_body_text"
            ),
            "formats": sorted({record["format"] for record in output_records}),
            "outputs": output_records,
            "delivery_quality": {
                "schema_version": DELIVERY_QUALITY_SCHEMA_VERSION,
                "status": "pass",
                "validated_at": generated_at,
                "failure_policy": "fail_closed",
                "compatibility_scope": "verified_profiles_only",
                "unknown_platform_policy": (
                    "use_generic_plain_text_then_verify_current_platform_rules"
                ),
                "source_text_gate": {
                    "status": "pass",
                    "scope": (
                        "novel metadata, manuscript index, and committed chapter Markdown"
                    ),
                    "normalizations_applied": dict(
                        snapshot.source_normalizations
                    ),
                },
                "output_normalizations_applied": output_normalizations,
                "profiles": [
                    profile_manifest(DELIVERY_PROFILES[name])
                    for name in used_profile_names
                ],
            },
        }
        if snapshot.work_type == "short_story":
            manifest["fanqie_publication_profile"] = "fanqie_short_story"
        manifest_stage = staging / EXPORT_MANIFEST
        write_utf8(
            manifest_stage,
            json.dumps(manifest, ensure_ascii=False, indent=2),
            context="export-manifest.json",
        )

        for record in new_records:
            relative = record["path"]
            source = staging / Path(relative)
            target = output_path(output_root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                if not args.force:
                    raise ExportError(f"Refusing to replace symbolic link: {relative}")
                target.unlink()
            os.replace(source, target)
        for stale in sorted(stale_paths, key=lambda item: len(item.parts), reverse=True):
            if stale.is_symlink() or stale.is_file():
                stale.unlink()
        fanqie_dir = output_root / "fanqie"
        if fanqie_dir.is_dir() and not any(fanqie_dir.iterdir()):
            fanqie_dir.rmdir()
        short_story_dir = output_root / "fanqie-short-story"
        if short_story_dir.is_dir() and not any(short_story_dir.iterdir()):
            short_story_dir.rmdir()
        os.replace(manifest_stage, output_root / EXPORT_MANIFEST)

    result = {
        "status": "exported",
        "project_root": str(snapshot.root),
        "exports_root": str(output_root),
        "title": snapshot.title,
        "chapters": len(snapshot.chapters),
        "formats": manifest["formats"],
        "outputs": output_records,
        "source_snapshot_sha256": snapshot.source_snapshot_sha256,
        "delivery_quality": "pass",
        "verified_profiles": used_profile_names,
        "validation_warnings": list(snapshot.validation_warnings),
    }
    if snapshot.work_type == "short_story":
        result["work_type"] = snapshot.work_type
    return result


def normalization_report_is_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(value.get(key), int)
        and not isinstance(value.get(key), bool)
        and value[key] >= 0
        for key in NORMALIZATION_KEYS
    )


def validate_delivery_quality_manifest(
    manifest: dict[str, Any],
    snapshot: ProjectSnapshot,
    *,
    source_is_fresh: bool,
) -> list[str]:
    issues: list[str] = []
    if manifest.get("encoding") != "UTF-8 without BOM":
        issues.append("manifest encoding policy is missing or wrong")
    if manifest.get("line_endings") != "LF":
        issues.append("manifest line-ending policy is missing or wrong")
    if manifest.get("unicode_normalization") != "NFC":
        issues.append("manifest Unicode normalization policy is missing or wrong")
    quality = manifest.get("delivery_quality")
    if not isinstance(quality, dict):
        return [*issues, "manifest delivery_quality report is missing"]
    if quality.get("schema_version") != DELIVERY_QUALITY_SCHEMA_VERSION:
        issues.append("manifest delivery-quality schema is unsupported")
    if quality.get("status") != "pass":
        issues.append("manifest delivery-quality status is not pass")
    if quality.get("failure_policy") != "fail_closed":
        issues.append("manifest delivery-quality failure policy is not fail_closed")
    if quality.get("compatibility_scope") != "verified_profiles_only":
        issues.append("manifest compatibility scope is missing or wrong")
    if not isinstance(quality.get("validated_at"), str):
        issues.append("manifest delivery-quality validation time is missing")
    source_gate = quality.get("source_text_gate")
    if not isinstance(source_gate, dict) or source_gate.get("status") != "pass":
        issues.append("manifest source-text gate result is missing or not pass")
    else:
        source_counts = source_gate.get("normalizations_applied")
        if not normalization_report_is_valid(source_counts):
            issues.append("manifest source normalization report is malformed")
        elif source_is_fresh and source_counts != snapshot.source_normalizations:
            issues.append("manifest source normalization report is stale")
    records = manifest.get("outputs", [])
    output_counts = empty_normalizations()
    profile_names: set[str] = set()
    actual_formats: set[str] = set()
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            format_name = record.get("format")
            if isinstance(format_name, str):
                actual_formats.add(format_name)
            profile_data = record.get("delivery_profile")
            if isinstance(profile_data, dict) and isinstance(
                profile_data.get("name"), str
            ):
                profile_names.add(profile_data["name"])
            normalizations = record.get("normalizations_applied")
            if normalization_report_is_valid(normalizations):
                merge_normalizations(output_counts, normalizations)
            record_quality = record.get("quality")
            if not isinstance(record_quality, dict) or record_quality.get("status") != "pass":
                issues.append(
                    f"manifest output quality is missing or not pass: {record.get('path')}"
                )
    if manifest.get("formats") != sorted(actual_formats):
        issues.append("manifest format inventory does not match output records")
    if quality.get("output_normalizations_applied") != output_counts:
        issues.append("manifest aggregate output normalization report is stale")
    if not profile_names.issubset(DELIVERY_PROFILES):
        issues.append("manifest references an unknown delivery profile")
    else:
        expected_profiles = [
            profile_manifest(DELIVERY_PROFILES[name])
            for name in sorted(profile_names)
        ]
        if quality.get("profiles") != expected_profiles:
            issues.append("manifest delivery profile definitions are missing or stale")
    return issues


def export_status(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    continuity = novel_continuity.continuity_status(args.root)
    if continuity["delivery_blocked"]:
        return {
            "status": "blocked",
            "project_root": continuity["project_root"],
            "exports_root": str(Path(continuity["project_root"]) / "exports"),
            "source_is_fresh": False,
            "delivery_quality": "fail",
            "continuity": continuity,
            "issues": [
                *(continuity.get("errors") or []),
                *(continuity.get("warnings") or []),
            ],
        }, 1
    review = novel_review.review_status(args.root)
    if review["review_due"]:
        return {
            "status": "blocked",
            "project_root": continuity["project_root"],
            "exports_root": str(Path(continuity["project_root"]) / "exports"),
            "source_is_fresh": False,
            "delivery_quality": "fail",
            "continuity": continuity,
            "quality_review": review,
            "issues": [
                (
                    "Independent short-story completion quality review is due"
                    if review.get("review_mode") == "completion"
                    else "Independent periodic quality review is due"
                )
            ],
        }, 1
    snapshot = load_snapshot(args.root)
    output_root = snapshot.root / "exports"
    manifest = read_previous_manifest(output_root)
    if manifest is None:
        result = {
            "status": "missing",
            "project_root": str(snapshot.root),
            "exports_root": str(output_root),
            "source_snapshot_sha256": snapshot.source_snapshot_sha256,
            "issues": ["No export-manifest.json exists"],
        }
        if snapshot.work_type == "short_story":
            result["work_type"] = snapshot.work_type
        return result, 0
    issues: list[str] = []
    previous_hash = manifest["source"].get("source_snapshot_sha256")
    source_is_fresh = previous_hash == snapshot.source_snapshot_sha256
    exported_chapter_count = manifest["source"].get("chapter_count")
    if (
        not isinstance(exported_chapter_count, int)
        or isinstance(exported_chapter_count, bool)
        or exported_chapter_count < 1
    ):
        issues.append("manifest source chapter_count is malformed")
        exported_chapter_count = len(snapshot.chapters)
    unmanaged = unmanaged_export_files(output_root, manifest)
    if unmanaged:
        issues.append(
            "unmanaged files exist in exports: " + ", ".join(unmanaged[:8])
        )
    record_paths: list[str] = []
    for record in manifest["outputs"]:
        if not isinstance(record, dict):
            issues.append("manifest output record is malformed")
            continue
        relative = record.get("path")
        format_name = record.get("format")
        if not isinstance(relative, str) or not isinstance(format_name, str):
            issues.append("manifest output record is malformed")
            continue
        record_paths.append(relative)
        issue = validate_output_record(output_root, record)
        if issue:
            issues.append(issue)
            continue
        path = output_path(output_root, relative)
        try:
            profile, current_quality = quality_for_output(
                path,
                relative,
                format_name,
                snapshot=snapshot if source_is_fresh else None,
                expected_chapters=exported_chapter_count,
            )
            metadata_issue = validate_record_quality_metadata(
                record,
                profile,
                current_quality,
                compare_quality=source_is_fresh,
            )
            if metadata_issue:
                issues.append(metadata_issue)
        except ExportError as exc:
            issues.append(f"{relative}: {exc}")
    if len(record_paths) != len(set(record_paths)):
        issues.append("manifest contains duplicate managed output paths")
    if source_is_fresh:
        actual_formats = {
            record.get("format")
            for record in manifest["outputs"]
            if isinstance(record, dict) and isinstance(record.get("format"), str)
        }
        expected_layout = expected_output_paths(snapshot, actual_formats)
        actual_layout = {
            str(record.get("path")): str(record.get("format"))
            for record in manifest["outputs"]
            if isinstance(record, dict)
            and isinstance(record.get("path"), str)
            and isinstance(record.get("format"), str)
        }
        if actual_layout != expected_layout:
            issues.append(
                "output inventory does not match the canonical chapter and title mapping"
            )
    issues.extend(
        validate_delivery_quality_manifest(
            manifest,
            snapshot,
            source_is_fresh=source_is_fresh,
        )
    )
    if issues:
        status = "modified"
        code = 1
    elif not source_is_fresh:
        status = "stale"
        code = 1
    else:
        status = "fresh"
        code = 0
    result = {
        "status": status,
        "project_root": str(snapshot.root),
        "exports_root": str(output_root),
        "source_is_fresh": source_is_fresh,
        "source_snapshot_sha256": snapshot.source_snapshot_sha256,
        "exported_source_snapshot_sha256": previous_hash,
        "formats": manifest.get("formats", []),
        "outputs": len(manifest["outputs"]),
        "delivery_quality": "pass" if not issues else "fail",
        "verified_profiles": sorted(
            {
                record.get("delivery_profile", {}).get("name")
                for record in manifest["outputs"]
                if isinstance(record, dict)
                and isinstance(record.get("delivery_profile"), dict)
                and isinstance(record["delivery_profile"].get("name"), str)
            }
        ),
        "issues": issues,
    }
    if snapshot.work_type == "short_story":
        result["work_type"] = snapshot.work_type
    return result, code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export validated novel chapters or a complete short-story Markdown "
            "manuscript into rebuildable TXT, DOCX, EPUB, and Fanqie files."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export", help="Build selected derived formats.")
    export.add_argument("root", help="Initialized novel project directory.")
    export.add_argument(
        "--format",
        action="append",
        choices=("all", "txt", "docx", "epub", "fanqie"),
        help="Repeat to select formats; defaults to all.",
    )
    export.add_argument(
        "--force",
        action="store_true",
        help="Replace modified derived outputs managed by export-manifest.json.",
    )
    status = subparsers.add_parser(
        "status", help="Check whether derived exports match current Markdown."
    )
    status.add_argument("root", help="Initialized novel project directory.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "export":
            result = export_project(args)
            code = 0
        else:
            result, code = export_status(args)
    except (ExportError, novel_project.ProjectError, novel_continuity.ContinuityError) as exc:
        result = {"status": "error", "error": str(exc)}
        code = 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
