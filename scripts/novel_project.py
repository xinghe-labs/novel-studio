#!/usr/bin/env python3
"""Create, validate, transition, and transactionally commit novel projects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import novel_review
import novel_continuity


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
MARKDOWN_LINK = re.compile(r"\]\((?P<target>[^)#]+\.md)(?:#[^)]+)?\)", re.IGNORECASE)
INDEX_TITLE = re.compile(
    r"^\|\s*(?P<number>\d{4})\s*\|\s*(?P<title>(?:\\\||[^|])*)\|"
)
SERIAL_CHAPTER_HEADING = re.compile(
    r"^#\s*第(?P<number>[0-9零〇一二三四五六七八九十百千两]+)章\s*(?P<title>.+?)\s*$"
)


class ProjectError(RuntimeError):
    pass


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
    root = Path(raw_root).expanduser().resolve()
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


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
    files: list[tuple[Path, bytes]], validator: Any | None = None
) -> None:
    """Replace a set of files and restore all prior bytes if any replace fails."""
    backups: dict[Path, bytes | None] = {
        target: target.read_bytes() if target.exists() else None for target, _ in files
    }
    temp_paths: dict[Path, Path] = {}
    applied: list[Path] = []
    try:
        for target, content in files:
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
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temp_paths[target] = temp_path
        for target, _ in files:
            os.replace(temp_paths[target], target)
            applied.append(target)
        if validator is not None:
            validator()
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(applied):
            prior = backups[target]
            try:
                if prior is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write_text(target, prior.decode("utf-8"))
            except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O
                rollback_errors.append(f"{target}: {rollback_exc}")
        suffix = (
            "; rollback errors: " + "; ".join(rollback_errors)
            if rollback_errors
            else ""
        )
        raise ProjectError(f"Transactional write failed and was rolled back: {exc}{suffix}") from exc
    finally:
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)


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


def frontmatter_metadata(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProjectError(f"Missing file: {path}") from exc

    if not text.startswith("---\n"):
        raise ProjectError(f"Missing YAML frontmatter in {path}")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ProjectError(f"Unterminated YAML frontmatter in {path}")

    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata


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
        result = {
            "status": "already_initialized",
            "project_root": str(root),
            "title": title,
        }
        existing_work_type = work_type_for_manifest(manifest)
        if existing_work_type == "short_story":
            result["work_type"] = existing_work_type
        return result

    if root.exists() and any(root.iterdir()):
        raise ProjectError(
            "Refusing to initialize a non-empty directory without novel.json."
        )

    root.mkdir(parents=True, exist_ok=True)
    for relative_dir in REQUIRED_DIRS:
        (root / relative_dir).mkdir(parents=True, exist_ok=True)

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

    created: list[str] = []
    write_new(manifest_path, dump_json(manifest))
    created.append("novel.json")
    write_new(root / "continuity/state.json", dump_json(continuity))
    created.append("continuity/state.json")
    for relative_path, content in markdown_templates(title, work_type).items():
        write_new(root / relative_path, content)
        created.append(relative_path)

    continuity_install = novel_continuity.install_project(root)
    created.extend(continuity_install["created_files"])

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
    manifest = read_json(root / "novel.json")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ProjectError("novel.json title must be a non-empty string")
    work_type = work_type_for_manifest(manifest)

    created_dirs: list[str] = []
    for relative_dir in REQUIRED_DIRS:
        path = root / relative_dir
        if not path.exists():
            path.mkdir(parents=True, exist_ok=False)
            created_dirs.append(relative_dir)
        elif not path.is_dir():
            raise ProjectError(f"Expected a directory: {path}")

    templates = markdown_templates(title, work_type)
    created_files: list[str] = []
    for relative_file in UPGRADE_FILES:
        path = root / relative_file
        if not path.exists():
            write_new(path, templates[relative_file])
            created_files.append(relative_file)
        elif not path.is_file():
            raise ProjectError(f"Expected a file: {path}")

    try:
        continuity_install = novel_continuity.install_project(root)
    except novel_continuity.ContinuityError as exc:
        raise ProjectError(str(exc)) from exc

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
        "status": (
            "upgraded"
            if created_dirs
            or created_files
            or continuity_install["status"] == "installed"
            else "already_current"
        ),
        "project_root": str(root),
        "created_directories": sorted(created_dirs),
        "created_files": sorted(created_files),
        "chapters_requiring_memory_backfill": backfill,
        "continuity": continuity_install,
    }


def source_manifest_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProjectError(
                f"Invalid JSONL in research/source-manifest.jsonl:{line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise ProjectError(
                "research/source-manifest.jsonl entries must be JSON objects: "
                f"line {line_number}"
            )
        records.append(record)
    return records


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
    source_ids: set[str] = set()
    for index, record in enumerate(records, 1):
        label = str(record.get("source_id") or f"line {index}")
        if label in source_ids:
            errors.append(f"Duplicate source manifest ID: {label}")
        source_ids.add(label)
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
                errors.append(f"Source manifest {label} is missing {key}")
        relative = record.get("path")
        if not isinstance(relative, str):
            continue
        source_path = (root / relative).resolve()
        if not is_within(source_path, root):
            errors.append(f"Source manifest {label} path escapes the project root")
        elif not source_path.is_file():
            errors.append(f"Source manifest {label} file is missing: {relative}")
        elif sha256_file(source_path) != record.get("sha256"):
            errors.append(f"Source manifest {label} SHA-256 mismatch: {relative}")
        if record.get("origin") == "user_local" and not record.get(
            "authorization_reference"
        ):
            errors.append(
                f"Source manifest {label} user_local entry lacks authorization_reference"
            )
        if record.get("rights_status") == "unknown":
            warnings.append(f"Source manifest {label} has unknown rights status")
    return errors, warnings


def collect_validation(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not root.is_dir():
        return [f"Project directory does not exist: {root}"], warnings

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


def research_state(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
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
    atomic_write_text(path, updated)
    continuity_result: dict[str, Any] | None = None
    try:
        if read_json(root / "novel.json").get("current_chapter") == 0:
            continuity_result = novel_continuity.reseal_zero_baseline(
                root, authorization
            )
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


def framework_state(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
    path = root / "planning/framework-session.md"
    current = frontmatter_metadata(path)
    stage = args.stage or current.get("stage")
    confirmation = args.confirmation or current.get("confirmation")
    try:
        requirements_confidence = (
            args.requirements_confidence
            if args.requirements_confidence is not None
            else int(current.get("requirements_confidence", "-1"))
        )
        story_confidence = (
            args.story_confidence
            if args.story_confidence is not None
            else int(current.get("story_confidence", "-1"))
        )
    except ValueError as exc:
        raise ProjectError("Current framework confidence is not an integer") from exc
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
        canonical_paths = (
            "story-bible/premise.md",
            "story-bible/cast.md",
            "story-bible/world.md",
            "story-bible/style-guide.md",
            "outlines/master-outline.md",
        )
        unresolved: list[str] = []
        for relative in canonical_paths:
            text = (root / relative).read_text(encoding="utf-8")
            if "[待作者确认]" in text or "[待确认]" in text:
                unresolved.append(relative)
        if unresolved:
            raise ProjectError(
                "Cannot confirm before canonical files are synchronized: "
                + ", ".join(unresolved)
            )
    if all(
        value is None
        for value in (
            args.stage,
            args.confirmation,
            args.requirements_confidence,
            args.story_confidence,
        )
    ):
        raise ProjectError("Specify at least one framework state field")
    authorization = clean_authorization_reference(args.authorization_reference)
    updates: dict[str, Any] = {
        "stage": stage,
        "confirmation": confirmation,
        "requirements_confidence": requirements_confidence,
        "story_confidence": story_confidence,
        "updated_at": utc_now(),
        "state_authorization": authorization,
    }
    original = path.read_text(encoding="utf-8")
    updated = replace_frontmatter(original, updates)
    atomic_write_text(path, updated)
    continuity_result: dict[str, Any] | None = None
    try:
        if read_json(root / "novel.json").get("current_chapter") == 0:
            continuity_result = novel_continuity.reseal_zero_baseline(
                root, authorization
            )
    except novel_continuity.ContinuityError as exc:
        atomic_write_text(path, original)
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


def markdown_cell(value: Any) -> str:
    if isinstance(value, list):
        text = ",".join(str(item) for item in value)
    else:
        text = str(value or "")
    return " ".join(text.replace("|", "\\|").split())


def resolve_package_file(package: Path, relative: str) -> Path:
    path = (package / relative).resolve()
    if not is_within(path, package):
        raise ProjectError(f"Staging path escapes its package: {relative}")
    if not path.is_file():
        raise ProjectError(f"Missing staged file: {relative}")
    return path


def validate_originality_report(
    root: Path, report_relative: str, chapter_path: Path
) -> dict[str, Any]:
    report_path = (root / report_relative).resolve()
    if not is_within(report_path, (root / "reviews").resolve()):
        raise ProjectError("Originality report must be under the project's reviews directory")
    report = read_json(report_path)
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
    if report_plan_hash != sha256_file(plan_path):
        raise ProjectError("Originality plan changed after the report was generated")
    references = report.get("reference_files")
    if not isinstance(references, list):
        raise ProjectError("Originality report reference_files must be an array")
    for reference in references:
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
            raise ProjectError("Originality report contains an invalid reference entry")
        reference_path = (root / reference["path"]).resolve()
        if not is_within(reference_path, root) or not reference_path.is_file():
            raise ProjectError("Originality report reference is missing or out of scope")
        if sha256_file(reference_path) != reference.get("sha256"):
            raise ProjectError("An originality reference changed after audit")
    return report


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


def commit_chapter(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_root(args.root)
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
    originality_report = validate_originality_report(
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
        errors, _ = collect_validation(root)
        if errors:
            raise ProjectError("Post-commit validation failed: " + "; ".join(errors))

    transactional_write(writes, validator=validate_committed_state)
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
        "originality_report": report_relative,
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
    parser = argparse.ArgumentParser(
        description=(
            "Create, upgrade, validate, transition, and transactionally commit an "
            "interactive, research-backed, stateful Chinese fiction project."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "init":
            result = init_project(args)
            code = 0
        elif args.command == "upgrade":
            result = upgrade_project(args)
            code = 0
        elif args.command == "validate":
            result, code = validate_project(args)
        elif args.command == "status":
            result, code = project_status(args)
        elif args.command == "research-state":
            result = research_state(args)
            code = 0
        elif args.command == "framework-state":
            result = framework_state(args)
            code = 0
        else:
            result = commit_chapter(args)
            code = 0
    except ProjectError as exc:
        result = {"status": "error", "error": str(exc)}
        code = 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
