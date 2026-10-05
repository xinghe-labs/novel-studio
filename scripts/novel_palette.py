#!/usr/bin/env python3
"""Palette diagnostics: human-flavor score, signature ledger, batch variance.

Read-only diagnostic companion to the perception-anchor discipline
(references/anchor-discipline.md).  It never writes to the project; the
reviewer stays responsible for every semantic judgment.  The score is a
structural proxy metric for narrative-voice hygiene -- it is not an AI
detection score and must never be presented as detection avoidance.

Book-level configuration lives in ``story-bible/voice-anchor.md``:

* a ``### 调色盘配置`` section with machine-readable ``- key：value`` lines
  (wordlists, cap words, tail-template entries, waivers, scalars);
* the signature ledger table under ``### 场景签名清单`` with an optional
  ``检测词`` column carrying one regex per signature.

Wordlist semantics: when a book provides any line for a list, that list
*replaces* the built-in default wholesale; lists the book omits fall back to
the built-in defaults below.  Every list entry is a Python regex fragment.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path
from typing import Any

import novel_cli


SCHEMA_VERSION = 1
CHAPTERS_RELATIVE = Path("manuscript/chapters")
VOICE_ANCHOR_RELATIVE = Path("story-bible/voice-anchor.md")
CHAPTER_NAME = re.compile(r"^(\d{4})-.*\.md$")
SECTION_SPLIT = re.compile(r"^(#{1,6})\s", re.M)

# Built-in wordlists (palette v1, validated across two books).  Entries are
# regex fragments joined with "|".  A book replaces a list wholesale by
# providing any ``- 词表·<list>：fragment`` line in its 调色盘配置 section.
DEFAULT_LISTS: dict[str, list[str]] = {
    "organ": ["瞳孔地震", "喉结滚动", "指节发白", "倒吸", "倒抽"],
    "emo": ["涌上心头", "恐惧笼罩", "愤怒涌", "感到一阵"],
    "cat": [
        "想了很久",
        "愣了一下",
        "愣住了",
        "心跳快了一拍",
        "很久没说话",
        "很久没动",
        "盯着",
    ],
    "noise": ["大概", "也许", "说不准", "没数清", "看不清", "也可能"],
    "stare": [
        "看了我一眼",
        "看了我一秒",
        "看了我两秒",
        "看了她一秒",
        "看了他一眼",
        "看了他一秒",
    ],
    "cool": ["发凉", "凉意", "冰凉", "发紧"],
    "time": [
        "早上",
        "上午",
        "下午",
        "晚上",
        "凌晨",
        "中午",
        r"\d+点",
        "第二天",
        "第[一二三四五六七八九十]+[天日月]",
        "次日",
        "当天",
        "天刚",
        "放学",
        "晨读",
        "晚自习",
        "上课铃",
        "下课铃",
        "午休",
    ],
}

LIST_KEYS = {
    "器官直标": "organ",
    "情绪直标": "emo",
    "迟钝套话": "cat",
    "带噪": "noise",
    "注视拍": "stare",
    "凉系": "cool",
    "落点锚时地": "time",
}

DEFAULT_STALE_WINDOW = 12
DEFAULT_TAIL_PARAS = 6
TRIPLE_SHORT_TAIL = "三连短句尾"
TRIPLE_SHORT_MAX_LEN = 14

HALFWIDTH_CHARS = ",;:?!"
CHAPREF_RE = re.compile(r"第\d+章")
DIALOGUE_RE = re.compile(r'"[^"]+"')

RULE_NAMES = {
    "R-TAIL": "章尾复沓模板",
    "R-CHAPREF": "章节号入文",
    "R-HALF": "半角标点",
    "R-BUNDLE": "硬扣捆绑帽（30）",
    "R-DLG": "裸对白连排",
    "R-ORGAN": "器官/情绪直标",
    "R-CAP": "上限词超限",
    "R-CAT": "迟钝套话堆叠",
    "R-OVER90": "超90字段",
    "R-NOISE": "零带噪",
    "R-LANDING": "落点锚缺失",
    "R-STARE": "注视拍",
    "R-COOL": "凉系",
}


class PaletteError(RuntimeError):
    """Domain error for missing or malformed project inputs (exit code 2)."""


# ---------------------------------------------------------------------------
# voice-anchor.md parsing
# ---------------------------------------------------------------------------


def _heading_level(line: str) -> int:
    match = re.match(r"^(#{1,6})\s", line)
    return len(match.group(1)) if match else 0


def _section_lines(text: str, heading_prefixes: tuple[str, ...]) -> list[str]:
    """Return body lines of the first heading matching any prefix (### level)."""
    lines = text.splitlines()
    start = None
    level = 0
    for index, line in enumerate(lines):
        current = _heading_level(line)
        if current and line.lstrip("#").strip().startswith(heading_prefixes):
            start = index + 1
            level = current
            break
    if start is None:
        return []
    body: list[str] = []
    for line in lines[start:]:
        current = _heading_level(line)
        if current and current <= level:
            break
        body.append(line)
    return body


def _parse_int_cell(cell: str) -> int | None:
    match = re.search(r"(\d+)", cell)
    return int(match.group(1)) if match else None


def parse_palette_config(text: str) -> dict[str, Any]:
    """Parse the ``### 调色盘配置`` section into a plain configuration dict."""
    config: dict[str, Any] = {
        "lists": {},
        "cap_words": {},
        "tail_entries": [],
        "triple_short_tail": False,
        "tail_paras": DEFAULT_TAIL_PARAS,
        "target_line": None,
        "stale_window": None,
        "waivers": [],
        "raw_lines": 0,
    }
    scalars = {
        "目标线": "target_line",
        "死锚窗口": "stale_window",
        "章尾扫描段数": "tail_paras",
    }
    for line in _section_lines(text, ("调色盘配置",)):
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if "：" not in body:
            continue
        key, _, value = body.partition("：")
        key = key.strip()
        value = value.strip()
        if not value:
            continue
        config["raw_lines"] += 1
        if key in scalars:
            try:
                config[scalars[key]] = int(value)
            except ValueError as exc:
                raise PaletteError(
                    f"调色盘配置「{key}」需要整数，读到「{value}」"
                ) from exc
        elif key.startswith("词表·") and key[3:] in LIST_KEYS:
            config["lists"].setdefault(LIST_KEYS[key[3:]], []).append(value)
        elif key == "三连短句尾":
            config["triple_short_tail"] = value in ("开", "on", "true", "是")
        elif key == "上限词":
            parts = value.split("｜")
            if len(parts) != 2:
                raise PaletteError(f"上限词条目需要「词｜每章上限」，读到「{value}」")
            try:
                config["cap_words"][parts[0].strip()] = int(parts[1].strip())
            except ValueError as exc:
                raise PaletteError(f"上限词条目上限需为整数，读到「{value}」") from exc
        elif key == "章尾模板":
            parts = value.split("｜")
            if len(parts) != 2:
                raise PaletteError(f"章尾模板条目需要「正则｜阈值」，读到「{value}」")
            if parts[0].strip() == TRIPLE_SHORT_TAIL:
                config["triple_short_tail"] = True
                continue
            try:
                threshold = int(parts[1].strip())
            except ValueError as exc:
                raise PaletteError(f"章尾模板阈值需为整数，读到「{value}」") from exc
            config["tail_entries"].append(
                {"pattern": parts[0].strip(), "threshold": threshold}
            )
        elif key == "豁免":
            parts = [part.strip() for part in value.split("｜")]
            if len(parts) < 2:
                raise PaletteError(
                    f"豁免条目需要「规则ID｜范围｜理由｜来源」，读到「{value}」"
                )
            rule = parts[0]
            if rule not in RULE_NAMES:
                raise PaletteError(f"豁免条目规则 ID 未知：{rule}")
            config["waivers"].append(
                {
                    "rule": rule,
                    "scope": parts[1],
                    "reason": parts[2] if len(parts) > 2 else "",
                    "source": parts[3] if len(parts) > 3 else "",
                }
            )
        elif key == "说明":
            continue
        else:
            raise PaletteError(f"调色盘配置含未知条目类型「{key}」，读到「{value}」")
    return config


def effective_lists(config: dict[str, Any]) -> dict[str, list[str]]:
    """Merge book wordlists (replace semantics) over the built-in defaults."""
    merged: dict[str, list[str]] = {}
    for key, default in DEFAULT_LISTS.items():
        merged[key] = list(config["lists"].get(key, default))
    return merged


def parse_signature_table(text: str) -> list[dict[str, Any]]:
    """Parse the signature ledger table; returns one record per data row."""
    rows = [line.strip() for line in _section_lines(text, ("场景签名", "签名清单", "签名账"))]
    table = [line for line in rows if line.startswith("|")]
    if len(table) < 2:
        return []
    def cells(line: str) -> list[str]:
        line = line.strip().strip("|").replace("\\|", "\x00")
        return [part.replace("\x00", "|").strip() for part in line.split("|")]

    header = [re.sub(r"（[^）]*）", "", name).strip() for name in cells(table[0])]
    index = {name: position for position, name in enumerate(header)}
    # 第三人称书惯用「地点/物件」作签名名列，与「名称」同义。
    if "名称" not in index and "地点/物件" in index:
        index["名称"] = index["地点/物件"]
    required = ("轨", "名称", "签名锚", "副签名", "已建立")
    missing = [name for name in required if name not in index]
    if missing:
        raise PaletteError(
            "场景签名清单表头缺少列：" + "、".join(missing) + "（钉死表头见 schemas-and-cli.md）"
        )
    signatures: list[dict[str, Any]] = []
    for line in table[1:]:
        if set(line) <= set("|-: "):
            continue
        values = cells(line)
        if not any(values):
            continue

        def column(name: str) -> str:
            position = index.get(name)
            return values[position] if position is not None and position < len(values) else ""

        last_cell = column("最近复现")
        signatures.append(
            {
                "track": column("轨"),
                "name": column("名称"),
                "anchor": column("签名锚"),
                "sub_anchor": column("副签名"),
                "established": _parse_int_cell(column("已建立")),
                "table_last": _parse_int_cell(last_cell),
                "table_numbers": [
                    int(match.group(1)) for match in re.finditer(r"ch(\d+)", last_cell)
                ],
                "reversal": column("候选反转位"),
                "note": column("状态变化备注"),
                "detect": column("检测词"),
            }
        )
    return signatures


def parse_sound_library(text: str) -> list[str]:
    """Parse the 声音库 section into regex fragments (split on ／、、|)."""
    parts: list[str] = []
    for line in _section_lines(text, ("声音库",)):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", "-")):
            continue
        for piece in re.split(r"／|、|\|", stripped):
            piece = piece.strip()
            piece = re.sub(r"（[^）]*）$", "", piece).strip()
            if piece:
                parts.append(piece)
    return parts


# ---------------------------------------------------------------------------
# manuscript loading
# ---------------------------------------------------------------------------


def load_chapters(root: Path, only: set[int] | None = None) -> list[dict[str, Any]]:
    directory = root / CHAPTERS_RELATIVE
    if not directory.is_dir():
        raise PaletteError(f"缺少章节目录：{directory}")
    chapters: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        match = CHAPTER_NAME.match(path.name)
        if not match:
            continue
        number = int(match.group(1))
        if only is not None and number not in only:
            continue
        text = path.read_text(encoding="utf-8")
        paragraphs = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and line.strip() != "---" and not line.startswith("#")
        ]
        sections: list[list[str]] = []
        current: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "---":
                if current:
                    sections.append(current)
                    current = []
            elif stripped and not stripped.startswith("#"):
                current.append(stripped)
        if current:
            sections.append(current)
        chapters.append(
            {
                "num": number,
                "file": path.name,
                "text": text,
                "paragraphs": paragraphs,
                "sections": sections,
                "body": "\n".join(paragraphs),
            }
        )
    if not chapters:
        raise PaletteError(f"章节目录没有可识别的 0000-*.md 章节：{directory}")
    return chapters


def load_voice_anchor(root: Path) -> tuple[Path, str]:
    path = root / VOICE_ANCHOR_RELATIVE
    if not path.is_file():
        raise PaletteError(f"缺少书级配置文件：{path}")
    return path, path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# waivers
# ---------------------------------------------------------------------------


def _scope_chapters(scope: str) -> set[int] | None:
    """全书 scope returns None (matches every chapter); else the number set."""
    if scope in ("全书", "*", ""):
        return None
    numbers: set[int] = set()
    for piece in re.split(r"[、,，\s]+", scope):
        piece = piece.strip()
        if not piece:
            continue
        match = re.fullmatch(r"(?:ch)?(\d{1,4})", piece)
        if not match:
            raise PaletteError(f"豁免范围无法解析：{piece}")
        numbers.add(int(match.group(1)))
    return numbers


def waiver_for(
    config: dict[str, Any], rule: str, chapter_num: int
) -> dict[str, Any] | None:
    for waiver in config["waivers"]:
        if waiver["rule"] != rule:
            continue
        scope = _scope_chapters(waiver["scope"])
        if scope is None or chapter_num in scope:
            return waiver
    return None


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def _deduction(rule: str, points: int, detail: str) -> dict[str, Any]:
    return {"rule": rule, "name": RULE_NAMES[rule], "points": points, "detail": detail}


def _tail_hits(
    chapter: dict[str, Any], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """Evaluate tail-template entries; returns (hit details, raw points)."""
    paragraphs = chapter["paragraphs"]
    window = config["tail_paras"]
    tail = "\n".join(paragraphs[-window:])
    hits: list[dict[str, Any]] = []
    points = 0
    for entry in config["tail_entries"]:
        count = len(re.findall(entry["pattern"], tail))
        if count >= entry["threshold"] and count >= 1:
            contribution = (count - entry["threshold"] + 1) * 10
            hits.append(
                {
                    "pattern": entry["pattern"],
                    "threshold": entry["threshold"],
                    "count": count,
                    "points": contribution,
                }
            )
            points += contribution
    if config["triple_short_tail"]:
        if len(paragraphs) >= 3 and all(
            len(p) <= TRIPLE_SHORT_MAX_LEN for p in paragraphs[-3:]
        ):
            hits.append(
                {
                    "pattern": TRIPLE_SHORT_TAIL,
                    "threshold": 1,
                    "count": 1,
                    "points": 10,
                }
            )
            points += 10
    return hits, points


def _dialogue_max_run(paragraphs: list[str]) -> int:
    runs: list[int] = []
    run = 0
    for paragraph in paragraphs:
        if DIALOGUE_RE.search(paragraph):
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return max(runs) if runs else 0


def _landing_missing(chapter: dict[str, Any], time_regex: re.Pattern[str]) -> list[int]:
    return [
        index
        for index, section in enumerate(chapter["sections"])
        if section and not time_regex.search(section[0][:40])
    ]


def score_chapter(
    chapter: dict[str, Any],
    config: dict[str, Any],
    lists: dict[str, list[str]],
    signature_patterns: list[tuple[str, re.Pattern[str]]],
) -> dict[str, Any]:
    body = chapter["body"]
    paragraphs = chapter["paragraphs"]
    deductions: list[dict[str, Any]] = []
    waived: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    num = chapter["num"]

    def account(rule: str, points: int, detail: str) -> None:
        waiver = waiver_for(config, rule, num)
        if waiver is not None:
            waived.append(
                {
                    "rule": rule,
                    "points_avoided": points,
                    "scope": waiver["scope"],
                    "reason": waiver["reason"],
                    "source": waiver["source"],
                }
            )
            return
        if points:
            deductions.append(_deduction(rule, points, detail))

    # Hard bundle: tail templates + chapter references + halfwidth, cap 30.
    tail_hits, tail_points = _tail_hits(chapter, config)
    metrics["tail_hits"] = tail_hits
    if tail_points:
        detail = "；".join(
            f"{hit['pattern']}×{hit['count']}/阈值{hit['threshold']}" for hit in tail_hits
        )
        account("R-TAIL", tail_points, detail)
    chapref = len(CHAPREF_RE.findall(body))
    metrics["chapref"] = chapref
    if chapref:
        account("R-CHAPREF", 10, f"「第N章」入文 {chapref} 处")
    halfwidth = sum(body.count(char) for char in HALFWIDTH_CHARS)
    metrics["halfwidth"] = halfwidth
    if halfwidth:
        account("R-HALF", min(10, halfwidth), f"半角 ,;:?! 共 {halfwidth} 处")

    # ---- 落点锚缺失 (deducted outside the bundle, like the registered v1 run) ----
    # renwei deducted landing anchors as an independent line; the 30-point cap
    # in v1 applies only to the tail/chapref/halfwidth bundle above.
    missing = _landing_missing(chapter, re.compile("|".join(lists["time"])))
    metrics["landing_missing"] = missing
    if missing:
        account("R-LANDING", 4 * len(missing), f"缺失段索引 {missing}")

    dlg_max = _dialogue_max_run(paragraphs)
    metrics["dlg_max"] = dlg_max
    if dlg_max > 10:
        account("R-DLG", min(20, (dlg_max - 10) * 3), f"裸对白连排最长 {dlg_max} 行")

    organ_count = sum(len(re.findall(frag, body)) for frag in lists["organ"])
    emo_count = sum(len(re.findall(frag, body)) for frag in lists["emo"])
    metrics["organ"] = organ_count
    metrics["emo"] = emo_count
    if lists["organ"] or lists["emo"]:
        account(
            "R-ORGAN",
            min(15, (organ_count + emo_count) * 2),
            f"器官 {organ_count}＋情绪 {emo_count}",
        )

    for word in sorted(config["cap_words"]):
        cap = config["cap_words"][word]
        count = body.count(word)
        metrics.setdefault("cap_words", {})[word] = count
        if count > cap:
            account("R-CAP", 5, f"「{word}」{count} 次，超每章上限 {cap}")

    cat_count = sum(len(re.findall(frag, body)) for frag in lists["cat"])
    metrics["cat_sum"] = cat_count
    if lists["cat"] and cat_count > 6:
        account("R-CAT", min(10, (cat_count - 6) * 2), f"迟钝套话 {cat_count} 处")

    over90 = sum(1 for paragraph in paragraphs if len(paragraph) > 90)
    metrics["over90"] = over90
    if over90:
        account("R-OVER90", min(10, over90 * 2), f"超 90 字段落 {over90} 段")

    noise_count = len(re.findall("|".join(lists["noise"]), body))
    metrics["noise"] = noise_count
    if lists["noise"] and noise_count == 0:
        account("R-NOISE", 3, "全书级带噪词表零命中")

    stare_count = sum(len(re.findall(frag, body)) for frag in lists["stare"])
    cool_count = sum(len(re.findall(frag, body)) for frag in lists["cool"])
    metrics["stare"] = stare_count
    metrics["cool"] = cool_count
    if lists["stare"] and stare_count:
        account("R-STARE", min(6, stare_count * 2), f"注视拍 {stare_count} 处")
    if lists["cool"] and cool_count:
        account("R-COOL", min(4, cool_count * 2), f"凉系 {cool_count} 处")

    signatures_on = [name for name, pattern in signature_patterns if pattern.search(body)]
    chars = len(chapter["body"].replace(" ", ""))
    raw_total = sum(entry["points"] for entry in deductions)
    if raw_total > 30 and any(
        entry["rule"] in ("R-TAIL", "R-CHAPREF", "R-HALF") for entry in deductions
    ):
        bundle_members = [
            entry["points"]
            for entry in deductions
            if entry["rule"] in ("R-TAIL", "R-CHAPREF", "R-HALF")
        ]
        over = sum(bundle_members) - 30
        if over > 0:
            deductions.append(
                {
                    "rule": "R-BUNDLE",
                    "name": RULE_NAMES["R-BUNDLE"],
                    "points": -over,
                    "detail": "章尾/章节号/半角合计超 30，回调至帽值",
                }
            )

    score = max(0, 100 - sum(entry["points"] for entry in deductions))
    return {
        "num": num,
        "file": chapter["file"],
        "chars": chars,
        "score": score,
        "deductions": deductions,
        "waived": waived,
        "metrics": metrics,
        "signatures_on": signatures_on,
    }


def command_score(root: Path, only: set[int] | None) -> dict[str, Any]:
    _, anchor_text = load_voice_anchor(root)
    config = parse_palette_config(anchor_text)
    lists = effective_lists(config)
    signatures = parse_signature_table(anchor_text)
    signature_patterns = []
    unscannable = []
    for signature in signatures:
        if signature["detect"]:
            try:
                signature_patterns.append(
                    (signature["name"], re.compile(signature["detect"]))
                )
            except re.error as exc:
                raise PaletteError(
                    f"签名「{signature['name']}」检测词正则无效：{signature['detect']}（{exc}）"
                ) from exc
        else:
            unscannable.append(signature["name"])
    chapters = load_chapters(root, only)
    scored = [score_chapter(chapter, config, lists, signature_patterns) for chapter in chapters]
    scores = [entry["score"] for entry in scored]
    target = config["target_line"]
    below = [entry["num"] for entry in scored if target is not None and entry["score"] < target]
    return {
        "status": "ok",
        "tool": "novel_palette",
        "command": "score",
        "score_version": SCHEMA_VERSION,
        "project": root.name,
        "note": "结构代理指标，非 AI 检测分；不承担平台检测目标。",
        "book_config": {
            "target_line": target,
            "tail_paras": config["tail_paras"],
            "cap_words": config["cap_words"],
            "tail_entries": len(config["tail_entries"]),
            "triple_short_tail": config["triple_short_tail"],
            "waivers": len(config["waivers"]),
            "lists_replaced": sorted(config["lists"]),
            "unscannable_signatures": unscannable,
        },
        "chapters": scored,
        "summary": {
            "count": len(scored),
            "mean": round(sum(scores) / len(scores), 1) if scores else 0,
            "min": min(scores, default=0),
            "max": max(scores, default=0),
            "below_target": below,
        },
    }


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------


def command_ledger(root: Path, stale_window: int | None) -> dict[str, Any]:
    _, anchor_text = load_voice_anchor(root)
    config = parse_palette_config(anchor_text)
    window = stale_window or config["stale_window"] or DEFAULT_STALE_WINDOW
    signatures = parse_signature_table(anchor_text)
    if not signatures:
        raise PaletteError("场景签名清单没有可解析的数据行")
    chapters = load_chapters(root)
    max_chapter = max(chapter["num"] for chapter in chapters)
    bodies = {chapter["num"]: chapter["body"] for chapter in chapters}

    records: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for signature in signatures:
        signature_text = signature["name"] + signature["note"]
        record: dict[str, Any] = {
            "track": signature["track"],
            "name": signature["name"],
            "established": signature["established"],
            "table_last": signature["table_last"],
            "event_type": "事件型" in signature_text,
            "retired": "退役" in signature_text,
            "note": signature["note"],
        }
        if not signature["detect"]:
            record.update(
                {
                    "unscannable": True,
                    "hit_chapters": [],
                    "detected_last": None,
                    "stale_risk": False,
                    "dense_run": 0,
                    "dense_flag": False,
                    "mismatch": None,
                }
            )
            flags.append(
                {
                    "signature": signature["name"],
                    "type": "unscannable",
                    "detail": "签名账未配置「检测词」列，跳过正文扫描",
                }
            )
            records.append(record)
            continue
        try:
            pattern = re.compile(signature["detect"])
        except re.error as exc:
            raise PaletteError(
                f"签名「{signature['name']}」检测词正则无效：{signature['detect']}（{exc}）"
            ) from exc
        hit_chapters = sorted(
            num for num, body in bodies.items() if pattern.search(body)
        )
        detected_last = hit_chapters[-1] if hit_chapters else None
        dense_run = 0
        run = 0
        previous: int | None = None
        for num in hit_chapters:
            run = run + 1 if previous is not None and num - previous == 1 else 1
            dense_run = max(dense_run, run)
            previous = num
        stale_risk = False
        stale_detail = ""
        if record["event_type"]:
            stale_detail = "事件型签名：缺席可能即叙事，不判死锚"
        elif record["retired"]:
            stale_detail = "退役签名：场景已收束，缺席即常态（法则二「消失」终态）"
        elif signature["established"] is not None:
            reference = detected_last if detected_last is not None else signature["established"]
            gap = max_chapter - reference
            if gap > window:
                stale_risk = True
                stale_detail = (
                    f"ch{reference} 后 {gap} 章未复现（窗口 {window}）"
                    if detected_last is not None
                    else f"建立后正文零复现（窗口 {window}）"
                )
        mismatch = None
        if signature["table_last"] is not None and detected_last is not None:
            # 复合账记（如「ch2 激活／白态 ch21」）看章号集合，只取首号会误报。
            if detected_last not in set(signature["table_numbers"]):
                mismatch = {"table": signature["table_last"], "scan": detected_last}
        record.update(
            {
                "unscannable": False,
                "hit_chapters": hit_chapters,
                "detected_last": detected_last,
                "stale_risk": stale_risk,
                "stale_detail": stale_detail,
                "dense_run": dense_run,
                "dense_flag": dense_run >= 3,
                "mismatch": mismatch,
            }
        )
        if stale_risk:
            flags.append(
                {
                    "signature": signature["name"],
                    "type": "stale",
                    "detail": stale_detail,
                }
            )
        if record["dense_flag"]:
            flags.append(
                {
                    "signature": signature["name"],
                    "type": "dense",
                    "detail": f"连续 {dense_run} 章复现，密锚嫌疑（打卡感），批审人工判读",
                }
            )
        if mismatch is not None:
            flags.append(
                {
                    "signature": signature["name"],
                    "type": "mismatch",
                    "detail": f"签名账记 ch{mismatch['table']}，正文扫描 ch{mismatch['scan']}，账实不符",
                }
            )
        records.append(record)
    return {
        "status": "ok",
        "tool": "novel_palette",
        "command": "ledger",
        "project": root.name,
        "stale_window": window,
        "max_chapter": max_chapter,
        "signatures": records,
        "flags": flags,
        "note": "只读对账报告，不回写签名账；回写仍走审稿轮人工确认。",
    }


# ---------------------------------------------------------------------------
# variance
# ---------------------------------------------------------------------------


def command_variance(root: Path) -> dict[str, Any]:
    _, anchor_text = load_voice_anchor(root)
    signatures = parse_signature_table(anchor_text)
    sound = parse_sound_library(anchor_text)
    signature_patterns: list[tuple[str, re.Pattern[str]]] = []
    for signature in signatures:
        if signature["detect"]:
            signature_patterns.append(
                (signature["name"], re.compile(signature["detect"]))
            )
    sound_pattern = re.compile("|".join(sound)) if sound else None
    config = parse_palette_config(anchor_text)
    lists = effective_lists(config)
    time_regex = re.compile("|".join(lists["time"]))
    chapters = load_chapters(root)

    rows: list[dict[str, Any]] = []
    for chapter in chapters:
        body = chapter["body"]
        paragraphs = chapter["paragraphs"]
        signature_hits = {name: len(pattern.findall(body)) for name, pattern in signature_patterns}
        sound_hits = len(sound_pattern.findall(body)) if sound_pattern else 0
        anchors = sum(signature_hits.values()) + sound_hits
        lengths = [len(paragraph) for paragraph in paragraphs]
        rows.append(
            {
                "num": chapter["num"],
                "chars": len(body.replace(" ", "")),
                "anchors": anchors,
                "per_1000": round(anchors / max(1, len(body)) * 1000, 1),
                "signature_hits": signature_hits,
                "sound_hits": sound_hits,
                "noise": len(re.findall("|".join(lists["noise"]), body)),
                "over90": sum(1 for length in lengths if length > 90),
                "dlg_max": _dialogue_max_run(paragraphs),
                "landing_missing": len(_landing_missing(chapter, time_regex)),
                "para_stddev": round(statistics.pstdev(lengths), 1) if len(lengths) > 1 else 0,
            }
        )
    rates = [row["per_1000"] for row in rows]
    anchor_totals = [row["anchors"] for row in rows]
    lowest = min(rows, key=lambda row: row["per_1000"], default=None)
    highest = max(rows, key=lambda row: row["per_1000"], default=None)
    return {
        "status": "ok",
        "tool": "novel_palette",
        "command": "variance",
        "project": root.name,
        "chapters": rows,
        "stats": {
            "mean_per_1000": round(sum(rates) / len(rates), 1) if rates else 0,
            "stdev_per_1000": round(statistics.pstdev(rates), 1) if len(rates) > 1 else 0,
            "mean_anchors": round(sum(anchor_totals) / len(anchor_totals), 1)
            if anchor_totals
            else 0,
            "thinnest_chapter": lowest["num"] if lowest else None,
            "thinnest_per_1000": lowest["per_1000"] if lowest else None,
            "densest_chapter": highest["num"] if highest else None,
            "densest_per_1000": highest["per_1000"] if highest else None,
        },
        "note": "只出数供批审人工判读（锚密度跟情绪曲线走），不做每章达标判断。",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_root(raw: str) -> Path:
    root = Path(raw)
    if not root.is_dir():
        raise PaletteError(f"项目根目录不存在：{root}")
    return root


def _parse_chapter_args(values: list[str] | None) -> set[int] | None:
    if not values:
        return None
    numbers: set[int] = set()
    for value in values:
        match = re.fullmatch(r"\d{1,4}", value)
        if not match:
            raise PaletteError(f"--chapter 需要章节号数字，读到「{value}」")
        numbers.add(int(value))
    return numbers


def build_parser() -> argparse.ArgumentParser:
    parser = novel_cli.JsonArgumentParser(
        description=(
            "Read-only palette diagnostics: structural human-flavor score, "
            "signature ledger reconciliation, and batch anchor-density variance. "
            "Structural proxy metrics only -- never an AI detection score."
        )
    )
    novel_cli.add_common_options(parser)
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=novel_cli.JsonArgumentParser
    )
    score = subparsers.add_parser(
        "score", help="Per-chapter human-flavor score (structural proxy metric)."
    )
    score.add_argument("root", help="Novel project directory.")
    score.add_argument(
        "--chapter",
        action="append",
        help="Chapter number to score; repeat as needed. Defaults to all chapters.",
    )
    ledger = subparsers.add_parser(
        "ledger", help="Reconcile the signature ledger table against the manuscript."
    )
    ledger.add_argument("root", help="Novel project directory.")
    ledger.add_argument(
        "--stale-window",
        type=int,
        help="Chapters without reproduction before a signature counts as stale "
        f"(default: book config or {DEFAULT_STALE_WINDOW}).",
    )
    variance = subparsers.add_parser(
        "variance", help="Per-chapter anchor-density counts for batch review."
    )
    variance.add_argument("root", help="Novel project directory.")
    return parser


def main() -> int:
    def dispatch(args: argparse.Namespace) -> Any:
        root = _resolve_root(args.root)
        if args.command == "score":
            return command_score(root, _parse_chapter_args(args.chapter))
        if args.command == "ledger":
            if args.stale_window is not None and args.stale_window < 1:
                raise PaletteError("--stale-window 需为正整数")
            return command_ledger(root, args.stale_window)
        return command_variance(root)

    return novel_cli.run_cli(
        build_parser,
        dispatch,
        tool_name="novel_palette",
        domain_errors=(PaletteError,),
    )


if __name__ == "__main__":
    sys.exit(main())
