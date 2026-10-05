"""Tests for the read-only palette diagnostics (score / ledger / variance).

All fixtures are synthetic: no real manuscript content ever enters the
repository.  The score arithmetic asserted here pins palette scoring v1 so
future rule changes fail loudly instead of silently moving book baselines.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_cli  # noqa: E402
import novel_palette  # noqa: E402


CONFIG_LINES = [
    "- 目标线：85",
    "- 死锚窗口：3",
    "- 章尾扫描段数：4",
    "- 词表·器官直标：瞳孔地震",
    "- 词表·情绪直标：感到一阵",
    "- 词表·迟钝套话：愣住了",
    "- 词表·带噪：也许",
    "- 词表·注视拍：看了一眼",
    "- 词表·凉系：发凉",
    "- 上限词：咯噔｜1",
    "- 章尾模板：回了家。｜1",
    "- 豁免：R-NOISE｜0003｜静默设计｜测试裁决",
]

TABLE = """
| 轨 | 名称 | 签名锚 | 副签名 | 已建立 | 最近复现（账） | 候选反转位 | 状态变化备注 | 检测词 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 地点签 | 灯管 | 顶灯闪 | 叫号机 | ch1 | ch2 | 全亮＝被整理过 | 同日再进还闪 | 灯管 |
| 规则签 | 更新铃 | 铃声 | 广播 | ch1 | ch1 | 铃不响＝失灵 | 事件型签名：缺席不判死锚 | 铃 |
| 物件签 | 木牌 | 木牌上的字 | — | ch1 | ch1 | 被抹＝有人动过 | 状态备注 | 木牌 |
| 地点签 | 无检测词签 | 某个锚 | — | ch1 | ch1 | — | — | |
"""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def voice_anchor(config_lines: list[str], table: str = TABLE) -> str:
    return (
        "# 声音锚点\n\n"
        "## 本书声音特征\n\n短句推进，感知落在实物上。\n\n"
        f"## 场景签名清单（表即签名账）\n{table}\n"
        "## 声音库（拟声拍取证源）\n\n铃声／脚步／粉笔。\n\n"
        "## 调色盘配置（novel_palette 机器可读段）\n\n"
        + "\n".join(config_lines)
        + "\n"
    )


_KEEPALIVE: list[tempfile.TemporaryDirectory] = []


def make_project(
    chapters: dict[int, str],
    config_lines: list[str] | None = None,
    table: str = TABLE,
) -> Path:
    temp = tempfile.TemporaryDirectory()
    _KEEPALIVE.append(temp)
    root = Path(temp.name) / "project"
    for num, text in sorted(chapters.items()):
        write_text(root / "manuscript/chapters" / f"{num:04d}-样章.md", text)
    write_text(
        root / "story-bible/voice-anchor.md",
        voice_anchor(CONFIG_LINES if config_lines is None else config_lines, table),
    )
    return root


def deductions_by_rule(chapter: dict) -> dict[str, int]:
    return {entry["rule"]: entry["points"] for entry in chapter["deductions"]}


CLEAN_CHAPTER = (
    "# 第1章 干净\n\n"
    "第二天早上，我到得比谁都早。\n\n"
    "他推门进来，把伞收好，水落在地上。\n\n"
    "也许是我多心，走廊的灯很稳。\n\n"
    "---\n\n"
    "中午的食堂挤得走不动。\n"
)


class PaletteConfigTests(unittest.TestCase):
    def test_scalars_lists_cap_tail_waiver_parse(self) -> None:
        config = novel_palette.parse_palette_config(voice_anchor(CONFIG_LINES))
        self.assertEqual(config["target_line"], 85)
        self.assertEqual(config["stale_window"], 3)
        self.assertEqual(config["tail_paras"], 4)
        self.assertEqual(config["cap_words"], {"咯噔": 1})
        self.assertEqual(
            config["tail_entries"], [{"pattern": "回了家。", "threshold": 1}]
        )
        self.assertEqual(config["waivers"][0]["rule"], "R-NOISE")
        self.assertEqual(config["waivers"][0]["scope"], "0003")
        self.assertEqual(config["waivers"][0]["source"], "测试裁决")

    def test_book_list_replaces_default_wholesale(self) -> None:
        config = novel_palette.parse_palette_config(voice_anchor(CONFIG_LINES))
        lists = novel_palette.effective_lists(config)
        self.assertEqual(lists["organ"], ["瞳孔地震"])
        self.assertEqual(lists["noise"], ["也许"])

    def test_defaults_apply_without_config_section(self) -> None:
        config = novel_palette.parse_palette_config(
            "# 声音锚点\n\n## 本书声音特征\n\n短句推进。\n"
        )
        lists = novel_palette.effective_lists(config)
        self.assertIn("瞳孔地震", lists["organ"])
        self.assertIn("看了他一眼", lists["stare"])
        self.assertIsNone(config["target_line"])
        self.assertEqual(config["tail_paras"], novel_palette.DEFAULT_TAIL_PARAS)

    def test_unknown_entry_type_raises(self) -> None:
        with self.assertRaises(novel_palette.PaletteError):
            novel_palette.parse_palette_config(voice_anchor(["- 乱写的键：值"]))

    def test_unknown_waiver_rule_raises(self) -> None:
        with self.assertRaises(novel_palette.PaletteError):
            novel_palette.parse_palette_config(
                voice_anchor(["- 豁免：R-NOPE｜全书｜理由｜来源"])
            )

    def test_table_requires_pinned_columns(self) -> None:
        with self.assertRaises(novel_palette.PaletteError):
            novel_palette.parse_signature_table(
                "## 场景签名清单\n\n"
                "| 轨 | 名称 | 签名锚 |\n| --- | --- | --- |\n| 地点签 | a | b |\n"
            )

    def test_name_column_alias_and_suffix_normalization(self) -> None:
        table = (
            "| 轨 | 地点/物件 | 签名锚 | 副签名 | 已建立 | 最近复现（账） | 状态变化备注 | 检测词 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 地点签 | 大厅 | 顶灯闪 | — | ch1 | ch2 | 备注 | 灯管 |\n"
        )
        rows = novel_palette.parse_signature_table("## 签名清单\n\n" + table)
        self.assertEqual(rows[0]["name"], "大厅")
        self.assertEqual(rows[0]["table_last"], 2)

    def test_escaped_pipes_inside_cells(self) -> None:
        rows = novel_palette.parse_signature_table(
            "## 场景签名清单\n\n" + TABLE.replace("叫号机 |", "叫\\|号机 |", 1)
        )
        self.assertEqual(rows[0]["detect"], "灯管")

    def test_triple_short_tail_entry_form(self) -> None:
        config = novel_palette.parse_palette_config(
            voice_anchor(["- 章尾模板：三连短句尾｜1"])
        )
        self.assertTrue(config["triple_short_tail"])
        self.assertEqual(config["tail_entries"], [])


class PaletteScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_project(
            {
                1: CLEAN_CHAPTER,
                2: (
                    "# 第2章 扣分\n\n"
                    "瞳孔地震，他退了半步。\n\n"
                    "咯噔。咯噔。\n\n"
                    "她看了一眼手表。\n\n"
                    "我后背发凉。\n\n"
                    "我愣住了。她愣住了。他愣住了。我们全愣住了。"
                    "还有人也愣住了。到后来人人都愣住了。大家愣住了。\n\n"
                    "感到一阵晕眩。\n"
                ),
                3: (
                    "# 第3章 静默\n\n"
                    "第二天早上，走廊里什么声音都没有。\n\n"
                    "我把这句话咽了回去。\n"
                ),
                4: (
                    "# 第4章 章尾\n\n"
                    "第二天，他收拾了行李。\n\n"
                    "也许他早就知道。\n\n"
                    "他把门带上，没有回头。\n\n"
                    "天黑了。他回了家。\n\n"
                    "灯亮了。他回了家。\n"
                ),
                5: (
                    "# 第5章 捆绑帽\n\n"
                    "第二天，也许没人注意到他。\n\n"
                    "他把门带上，没有回头。\n\n"
                    "a,b,c,d,e,f,g,h,i,j,k,l 回了家。\n\n"
                    "m,n,o,p,q,r,s,t,u,v,w,x 回了家。\n\n"
                    "车上很静。他回了家。\n"
                ),
                6: (
                    "# 第6章 对白\n\n"
                    "第二天，也许他早就知道。\n\n"
                    + "\n\n".join(f'他说："第{i}句。"' for i in range(1, 13))
                    + "\n\n"
                    + "也许" + "这句话被拉得很长，" * 12 + "直到天黑透了才说完。\n\n"
                    "第3章发生过的事，没人再提。\n"
                ),
            }
        )

    def test_clean_chapter_scores_100(self) -> None:
        report = novel_palette.command_score(self.root, {1})
        chapter = report["chapters"][0]
        self.assertEqual(chapter["score"], 100)
        self.assertEqual(chapter["deductions"], [])

    def test_multi_rule_arithmetic(self) -> None:
        report = novel_palette.command_score(self.root, {2})
        chapter = report["chapters"][0]
        rules = deductions_by_rule(chapter)
        # organ 1 + emo 1 -> 4; cap 咯噔×2 over 1 -> 5; stare 1 -> 2;
        # cool 1 -> 2; cat 愣住了×7 -> (7-6)*2 = 2; zero noise -> 3;
        # opening paragraph carries no time signal -> 4.
        self.assertEqual(
            rules,
            {
                "R-ORGAN": 4,
                "R-CAP": 5,
                "R-STARE": 2,
                "R-COOL": 2,
                "R-CAT": 2,
                "R-NOISE": 3,
                "R-LANDING": 4,
            },
        )
        self.assertEqual(chapter["score"], 78)

    def test_waiver_suppresses_deduction_and_is_auditable(self) -> None:
        report = novel_palette.command_score(self.root, {3})
        chapter = report["chapters"][0]
        self.assertEqual(chapter["deductions"], [])
        self.assertEqual(len(chapter["waived"]), 1)
        waiver = chapter["waived"][0]
        self.assertEqual(waiver["rule"], "R-NOISE")
        self.assertEqual(waiver["points_avoided"], 3)
        self.assertEqual(waiver["source"], "测试裁决")
        self.assertEqual(chapter["score"], 100)

    def test_waiver_needs_matching_chapter_scope(self) -> None:
        report = novel_palette.command_score(self.root, {1})
        self.assertEqual(report["chapters"][0]["waived"], [])

    def test_tail_entry_contribution(self) -> None:
        report = novel_palette.command_score(self.root, {4})
        chapter = report["chapters"][0]
        # 回了家。×2 inside the last four paragraphs, threshold 1
        # -> (2 - 1 + 1) * 10 = 20.
        self.assertEqual(deductions_by_rule(chapter).get("R-TAIL"), 20)
        self.assertEqual(chapter["score"], 80)

    def test_hard_bundle_cap_binds_at_thirty(self) -> None:
        report = novel_palette.command_score(self.root, {5})
        chapter = report["chapters"][0]
        rules = deductions_by_rule(chapter)
        # Tail 3 hits -> 30; halfwidth 24 -> min(10, 24) = 10; raw 40 -> cap 30.
        self.assertEqual(rules["R-TAIL"], 30)
        self.assertEqual(rules["R-HALF"], 10)
        self.assertEqual(rules["R-BUNDLE"], -10)
        self.assertEqual(chapter["score"], 70)

    def test_dialogue_run_over90_and_chapref(self) -> None:
        report = novel_palette.command_score(self.root, {6})
        chapter = report["chapters"][0]
        rules = deductions_by_rule(chapter)
        self.assertEqual(rules["R-DLG"], 6)
        self.assertEqual(rules["R-OVER90"], 2)
        self.assertEqual(rules["R-CHAPREF"], 10)
        self.assertEqual(chapter["score"], 82)

    def test_target_line_flags_below(self) -> None:
        report = novel_palette.command_score(self.root, {2, 3})
        self.assertEqual(report["summary"]["below_target"], [2])
        self.assertEqual(report["book_config"]["target_line"], 85)

    def test_score_floors_at_zero(self) -> None:
        text = (
            "# 第9章 触底\n\n"
            + "\n\n".join(["瞳孔地震。"] * 8)
            + "\n\n"
            + "\n\n".join(
                ["---\n\n他没有回头，径直往前走。"] * 20
            )
            + "\n\n"
            + '他说："走。"\n' * 12
            + "\n\n"
            + "我愣住了。" * 8
            + "\n\n"
            + "也许" + "这句话被拉得很长，" * 12 + "直到天黑透了才说完。\n"
        )
        root = make_project({9: text})
        report = novel_palette.command_score(root, {9})
        chapter = report["chapters"][0]
        # 21 sections without a time signal -> 84; organ 8 -> 15; dialogue
        # run 12 -> 6; 愣住了×8 -> 4; one long paragraph -> 2. Total 111.
        self.assertGreaterEqual(
            sum(entry["points"] for entry in chapter["deductions"]), 100
        )
        self.assertEqual(chapter["score"], 0)

    def test_score_note_carries_proxy_boundary(self) -> None:
        report = novel_palette.command_score(self.root, {1})
        self.assertIn("非 AI 检测分", report["note"])


class PaletteLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_project(
            {
                1: "第一天，灯管在闪，铃响了，木牌还挂在原处。\n",
                2: "第二天，木牌被人摸黑翻过。\n",
                3: "第三天，木牌的字淡了一点。\n",
                4: "第四天，什么都没有发生。\n",
                5: "第五天，走廊尽头的脚步声远去。\n",
            }
        )

    def ledger(self) -> dict:
        return novel_palette.command_ledger(self.root, None)

    def by_name(self, report: dict, name: str) -> dict:
        return next(s for s in report["signatures"] if s["name"] == name)

    def test_stale_and_mismatch(self) -> None:
        report = self.ledger()
        lamp = self.by_name(report, "灯管")
        # Hits only ch1; max chapter 5, window 3 -> gap 4 > 3.
        self.assertTrue(lamp["stale_risk"])
        # The table claims ch2; the scan finds ch1.
        self.assertEqual(lamp["mismatch"], {"table": 2, "scan": 1})
        flags = {(f["type"], f["signature"]) for f in report["flags"]}
        self.assertIn(("stale", "灯管"), flags)
        self.assertIn(("mismatch", "灯管"), flags)

    def test_event_type_signature_never_stale(self) -> None:
        report = self.ledger()
        bell = self.by_name(report, "更新铃")
        self.assertTrue(bell["event_type"])
        self.assertFalse(bell["stale_risk"])
        self.assertNotIn(
            ("stale", "更新铃"),
            {(f["type"], f["signature"]) for f in report["flags"]},
        )

    def test_retired_signature_never_stale(self) -> None:
        table = TABLE.replace(
            "| 地点签 | 灯管 | 顶灯闪 | 叫号机 | ch1 | ch2 | 全亮＝被整理过 | 同日再进还闪 | 灯管 |",
            "| 地点签 | 灯管 | 顶灯闪 | 叫号机 | ch1 | ch2 | 全亮＝被整理过 | 场景收束，随戏退役 | 灯管 |",
        )
        root = make_project(
            {
                1: "第一天，灯管在闪。\n",
                2: "第二天，无事发生。\n",
                3: "第三天，无事发生。\n",
                4: "第四天，无事发生。\n",
                5: "第五天，无事发生。\n",
            },
            table=table,
        )
        report = novel_palette.command_ledger(root, None)
        lamp = next(s for s in report["signatures"] if s["name"] == "灯管")
        # Gap 4 > window 3, but the scene has been retired (法则二「消失」终态).
        self.assertTrue(lamp["retired"])
        self.assertFalse(lamp["stale_risk"])
        self.assertNotIn(
            ("stale", "灯管"),
            {(f["type"], f["signature"]) for f in report["flags"]},
        )

    def test_dense_run_flagged(self) -> None:
        report = self.ledger()
        board = self.by_name(report, "木牌")
        self.assertEqual(board["dense_run"], 3)
        self.assertTrue(board["dense_flag"])
        self.assertIn(
            ("dense", "木牌"),
            {(f["type"], f["signature"]) for f in report["flags"]},
        )

    def test_healthy_signature_not_stale(self) -> None:
        report = self.ledger()
        self.assertFalse(self.by_name(report, "木牌")["stale_risk"])

    def test_missing_detect_word_is_unscannable(self) -> None:
        report = self.ledger()
        ghost = self.by_name(report, "无检测词签")
        self.assertTrue(ghost["unscannable"])
        self.assertIn(
            ("unscannable", "无检测词签"),
            {(f["type"], f["signature"]) for f in report["flags"]},
        )

    def test_compound_table_cell_matches_scan(self) -> None:
        table = TABLE.replace(
            "| 地点签 | 灯管 | 顶灯闪 | 叫号机 | ch1 | ch2 |",
            "| 地点签 | 灯管 | 顶灯闪 | 叫号机 | ch1 | ch2 激活/白态 ch1 |",
        )
        root = make_project(
            {1: "第一天，灯管在闪。\n", 2: "第二天，无事发生。\n"}, table=table
        )
        report = novel_palette.command_ledger(root, None)
        lamp = next(s for s in report["signatures"] if s["name"] == "灯管")
        self.assertIsNone(lamp["mismatch"])

    def test_report_never_writes_files(self) -> None:
        def snapshot() -> set[str]:
            return {
                str(p.relative_to(self.root))
                for p in self.root.rglob("*")
                if p.is_file()
            }

        before = snapshot()
        self.ledger()
        self.assertEqual(before, snapshot())


class PaletteVarianceTests(unittest.TestCase):
    def test_variance_reports_counts_and_stats(self) -> None:
        root = make_project(
            {
                1: "第一天，灯管在闪，铃响了，脚步声远了。\n",
                2: "第二天，无事发生。\n",
            }
        )
        report = novel_palette.command_variance(root)
        first, second = report["chapters"]
        self.assertGreater(first["anchors"], second["anchors"])
        self.assertIn("灯管", first["signature_hits"])
        self.assertGreater(first["sound_hits"], 0)
        self.assertEqual(report["stats"]["thinnest_chapter"], 2)
        self.assertIn("人工判读", report["note"])


@contextlib.contextmanager
def mock_argv(argv: list[str]):
    with mock.patch.object(sys, "argv", argv):
        yield


class PaletteCliTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, dict | None]:
        stdout = io.StringIO()
        with mock_argv(argv), contextlib.redirect_stdout(stdout):
            code = novel_palette.main()
        payload = None
        if stdout.getvalue().strip():
            payload = json.loads(stdout.getvalue())
        return code, payload

    def test_version_contract(self) -> None:
        code, payload = self.run_main(["novel_palette.py", "--version"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["tool"], "novel_palette")
        self.assertEqual(payload["version"], novel_cli.TOOL_VERSION)

    def test_missing_root_is_domain_error(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "no-such-palette-root")
        code, payload = self.run_main(["novel_palette.py", "score", missing])
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "error")

    def test_chapter_filter(self) -> None:
        root = make_project(
            {
                1: CLEAN_CHAPTER,
                2: "# 第2章 扣分\n\n瞳孔地震。\n\n也许不是。\n",
            }
        )
        code, payload = self.run_main(
            ["novel_palette.py", "score", str(root), "--chapter", "2"]
        )
        self.assertEqual(code, 0)
        self.assertEqual([c["num"] for c in payload["chapters"]], [2])

    def test_bad_chapter_argument_exits_two(self) -> None:
        root = make_project({1: CLEAN_CHAPTER})
        code, payload = self.run_main(
            ["novel_palette.py", "score", str(root), "--chapter", "章"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "error")

    def test_ledger_and_variance_commands(self) -> None:
        root = make_project(
            {1: "第一天，灯管在闪。\n", 2: "第二天，无事发生。\n"}
        )
        code, ledger = self.run_main(["novel_palette.py", "ledger", str(root)])
        self.assertEqual(code, 0)
        self.assertEqual(ledger["command"], "ledger")
        code, variance = self.run_main(["novel_palette.py", "variance", str(root)])
        self.assertEqual(code, 0)
        self.assertEqual(variance["command"], "variance")


if __name__ == "__main__":
    unittest.main()
