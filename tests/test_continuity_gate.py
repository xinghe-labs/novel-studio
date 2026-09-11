from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILL_ROOT / "tests"))

import novel_continuity  # noqa: E402
import novel_export  # noqa: E402
import novel_originality  # noqa: E402
import novel_project  # noqa: E402
from continuity_test_utils import (  # noqa: E402
    complete_staged_continuity,
    evidence_quote,
    read_json,
    seal_full_baseline,
    write_json,
)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class ContinuityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def init_project(self, name: str, *, ready: bool = True) -> Path:
        root = self.base / name
        novel_project.init_project(
            SimpleNamespace(
                root=str(root),
                title=f"连续性测试-{name}",
                language="zh-CN",
                genre="悬疑",
            )
        )
        if ready:
            replacements = {
                "story-bible/premise.md": "# 故事核心\n\n调查员必须在债务归零前找出时间异常。\n",
                "story-bible/cast.md": "# 人物档案\n\n## 林某D\n\n她谨慎记录每一次能力消耗。\n",
                "story-bible/world.md": "# 世界规则\n\n每次停摆都会增加一分钟债务。\n",
                "story-bible/style-guide.md": "# 叙事声音\n\n近距离第三人称，克制而紧迫。\n",
                "outlines/master-outline.md": (
                    "# 总纲\n\n- [locked] 债务不能凭空消失。\n"
                    "- [planned] 主角追查时间异常。\n"
                    "- [optional] 旧钟楼支线。\n"
                    "- [abandoned] 梦境解释。\n"
                ),
            }
            for relative, content in replacements.items():
                write_text(root / relative, content)
            novel_project.framework_state(
                SimpleNamespace(
                    root=str(root),
                    stage="complete",
                    confirmation="confirmed",
                    requirements_confidence=97,
                    story_confidence=97,
                    authorization_reference="unit test author framework confirmation",
                )
            )
            self.write_originality_plan(root)
        return root

    def write_originality_plan(self, root: Path) -> None:
        plan = {
            "schema_version": 1,
            "status": "ready",
            "candidate": {
                "logline": {
                    "text": "调查员在时间债务失控前追查异常源头。",
                    "influences": [],
                    "causal_transformation": "",
                },
                "relationships": [
                    {
                        "text": "调查员与记账员互相制约",
                        "influences": [],
                        "causal_transformation": "",
                    }
                ],
                "world_rules": [
                    {
                        "text": "每次停摆都会增加一分钟债务",
                        "influences": [],
                        "causal_transformation": "",
                    }
                ],
                "first_three_nodes": [
                    {"text": "发现欠账", "influences": [], "causal_transformation": ""},
                    {"text": "核对旧账", "influences": [], "causal_transformation": ""},
                    {"text": "追查异常", "influences": [], "causal_transformation": ""},
                ],
                "core_twist": {
                    "text": "记账人也是债务规则的受益者",
                    "influences": [],
                    "causal_transformation": "",
                },
            },
            "references": [],
            "thresholds": {"structural_similarity_review": 0.58},
        }
        write_json(root / "research/originality-plan.json", plan)

    def stage_chapter(
        self,
        root: Path,
        number: int,
        *,
        package_name: str | None = None,
        risk_triggers: tuple[str, ...] = (),
        chapter_class: str = "normal",
        fact_changes: tuple[dict, ...] = (),
    ) -> Path:
        package = root / "staging/chapters" / (
            package_name or f"{number:04d}-test"
        )
        filename = f"{number:04d}-第{number}次记账.md"
        chapter_text = (
            f"# 第{number}章 第{number}次记账\n\n"
            f"林某D在第{number}日核对第{number}笔时间债务，账面仍然守恒。\n"
        )
        write_text(package / "chapter.md", chapter_text)
        write_text(
            package / "memory.md",
            f"# 第{number:04d}章记忆卡\n\n"
            f"- 正文：[{filename}](../../manuscript/chapters/{filename})\n"
            f"- 事实：林某D核对第{number}笔时间债务。\n",
        )
        state = read_json(root / "continuity/state.json")
        state.update(
            {
                "through_chapter": number,
                "story_time": f"第{number}日",
                "characters": {
                    "林某D": {
                        "location": "旧钟楼",
                        "condition": [],
                        "knowledge": [f"第{number}笔债务已核对"],
                        "beliefs": ["债务必须守恒"],
                        "goal": "找出时间异常源头",
                        "inventory": ["时间账本"],
                    }
                },
                "open_threads": ["T-DEBT"],
            }
        )
        write_json(package / "continuity-state.json", state)
        originality, code = novel_originality.audit_project(
            SimpleNamespace(
                root=str(root),
                candidate=[str(package / "chapter.md")],
                reference=None,
                exact_minimum=18,
                near_threshold=0.72,
                max_findings=30,
                no_report=False,
            )
        )
        self.assertEqual(code, 0)
        commit = {
            "schema_version": 1,
            "chapter_number": number,
            "manuscript_filename": filename,
            "chapter_file": "chapter.md",
            "memory_file": "memory.md",
            "continuity_state_file": "continuity-state.json",
            "originality_report": originality["report_path"],
            "index": {
                "title": f"第{number}次记账",
                "pov": "林某D",
                "story_time": f"第{number}日",
                "location": "旧钟楼",
                "fact_summary": f"林某D核对第{number}笔时间债务",
                "key_change": "账本新增可追溯记录",
                "thread_ids": ["T-DEBT"],
            },
        }
        write_json(package / "commit.json", commit)
        complete_staged_continuity(
            root,
            package,
            risk_triggers=risk_triggers,
            chapter_class=chapter_class,
            fact_changes=fact_changes,
        )
        return package

    def seed_direct_chapters(self, root: Path, through: int) -> None:
        index_path = root / "manuscript/index.md"
        index = index_path.read_text(encoding="utf-8").rstrip()
        rows: list[str] = []
        for number in range(1, through + 1):
            filename = f"{number:04d}-基线章.md"
            write_text(
                root / "manuscript/chapters" / filename,
                f"# 第{number}章 基线章\n\n第{number}日，第{number}笔账已经登记。\n",
            )
            write_text(
                root / "memory/chapters" / f"{number:04d}.md",
                f"# 第{number:04d}章记忆卡\n\n"
                f"- 正文：[{filename}](../../manuscript/chapters/{filename})\n",
            )
            rows.append(
                f"| {number:04d} | 基线章 | 林某D | 第{number}日 | 旧钟楼 | "
                f"登记账目 | 推进调查 | T-DEBT | [正文](chapters/{filename}) |\n"
            )
        write_text(index_path, index + "\n" + "".join(rows))
        manifest = read_json(root / "novel.json")
        manifest["current_chapter"] = through
        write_json(root / "novel.json", manifest)
        state = read_json(root / "continuity/state.json")
        state.update({"through_chapter": through, "story_time": f"第{through}日"})
        write_json(root / "continuity/state.json", state)
        seal_full_baseline(root)

    def audit_path(self, package: Path) -> Path:
        commit = read_json(package / "commit.json")
        return package / str(
            commit.get("continuity_audit_file", "continuity-audit.json")
        )

    def test_prepare_baseline_is_read_only_and_requires_installed_scaffold(self) -> None:
        root = self.init_project("baseline-read-only")
        before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        packet = self.base / "baseline-packet.json"
        novel_continuity.prepare_baseline(
            SimpleNamespace(
                root=str(root),
                output=str(packet),
                report_output=None,
            )
        )
        after = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

        facts_path = root / novel_continuity.FACTS_PATH
        facts_path.unlink()
        with self.assertRaisesRegex(novel_continuity.ContinuityError, "upgrade"):
            novel_continuity.prepare_baseline(
                SimpleNamespace(
                    root=str(root),
                    output=str(self.base / "missing-scaffold-packet.json"),
                    report_output=None,
                )
            )
        self.assertFalse(facts_path.exists())

    def test_repeated_zero_chapter_reseal_remains_valid(self) -> None:
        root = self.init_project("zero-reseal", ready=False)
        novel_project.research_state(
            SimpleNamespace(
                root=str(root),
                candidate_approval="approved",
                deep_analysis=None,
                authorization_reference="unit test candidate approval",
            )
        )
        novel_project.research_state(
            SimpleNamespace(
                root=str(root),
                candidate_approval=None,
                deep_analysis="in_progress",
                authorization_reference="unit test deep analysis start",
            )
        )
        status = novel_continuity.continuity_status(root)
        self.assertEqual(status["status"], "current")
        head = read_json(root / novel_continuity.HEAD_PATH)
        baseline = root / head["baseline_file"]
        self.assertEqual(novel_continuity.sha256_file(baseline), head["baseline_sha256"])

    def test_audit_hash_becomes_stale_after_state_delta_edit(self) -> None:
        root = self.init_project("stale-audit")
        package = self.stage_chapter(root, 1)
        delta = read_json(package / "state-delta.json")
        delta["review_note"] = "审计完成后又改了状态增量"
        write_json(package / "state-delta.json", delta)
        result, code = novel_continuity.check_package_command(
            SimpleNamespace(root=str(root), package=str(package))
        )
        self.assertEqual(code, 1)
        self.assertIn("state_delta_sha256", result["error"])

    def test_knowledge_and_outline_facts_require_candidate_bound_sources(self) -> None:
        root = self.init_project("fact-categories")
        categories = (
            ("F-KNOW-0001", "character_knows", "林某D", "知道", "第一笔债务已核对", None, "candidate", "林某D在第1日核对第1笔时间债务，账面仍然守恒。"),
            ("F-BELIEVE-0001", "character_believes", "林某D", "相信", "债务必须守恒", None, "candidate", "林某D在第1日核对第1笔时间债务，账面仍然守恒。"),
            ("F-CLAIM-0001", "character_claims", "林某D", "声称", "账面没有异常", None, "candidate", "林某D在第1日核对第1笔时间债务，账面仍然守恒。"),
            ("F-READER-0001", "reader_knows", "读者", "知道", "林某D正在核账", None, "candidate", "林某D在第1日核对第1笔时间债务，账面仍然守恒。"),
            ("F-PLAN-LOCKED", "author_plan", "大纲", "安排", "债务不能凭空消失", "locked", "outlines/master-outline.md", "- [locked] 债务不能凭空消失。"),
            ("F-PLAN-PLANNED", "author_plan", "大纲", "安排", "主角追查时间异常", "planned", "outlines/master-outline.md", "- [planned] 主角追查时间异常。"),
            ("F-PLAN-OPTIONAL", "author_plan", "大纲", "安排", "旧钟楼支线", "optional", "outlines/master-outline.md", "- [optional] 旧钟楼支线。"),
            ("F-PLAN-ABANDONED", "author_plan", "大纲", "安排", "梦境解释", "abandoned", "outlines/master-outline.md", "- [abandoned] 梦境解释。"),
        )
        fact_changes: list[dict] = []
        for (
            fact_id,
            category,
            subject,
            predicate,
            obj,
            plan_status,
            source_path,
            source_quote,
        ) in categories:
            record = {
                "schema_version": 1,
                "fact_id": fact_id,
                "category": category,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "status": "active",
                "significance": "normal",
                "valid_from_chapter": 1,
                "source": {
                    "path": source_path,
                    "location": "正文第一段" if source_path == "candidate" else "总纲状态行",
                    "quote": source_quote,
                },
            }
            if plan_status is not None:
                record["plan_status"] = plan_status
            fact_changes.append(
                {
                    "operation": "add",
                    "record": record,
                    "reason": "本章正文或已确认大纲建立该事实",
                    "evidence": [record["source"]],
                }
            )

        package = self.stage_chapter(
            root,
            1,
            fact_changes=tuple(fact_changes),
        )
        result, code = novel_continuity.check_package_command(
            SimpleNamespace(root=str(root), package=str(package))
        )
        self.assertEqual(code, 0, result)
        novel_project.commit_chapter(
            SimpleNamespace(root=str(root), package=str(package))
        )
        stored = [
            json.loads(line)
            for line in (root / novel_continuity.FACTS_PATH)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.assertEqual(
            {record["category"] for record in stored},
            {
                "character_knows",
                "character_believes",
                "character_claims",
                "reader_knows",
                "author_plan",
            },
        )
        self.assertEqual(
            {
                record["plan_status"]
                for record in stored
                if record["category"] == "author_plan"
            },
            {"locked", "planned", "optional", "abandoned"},
        )

        bad_root = self.init_project("fact-source-not-in-candidate")
        bad_change = json.loads(json.dumps(fact_changes[0], ensure_ascii=False))
        bad_change["record"]["fact_id"] = "F-KNOW-BAD"
        bad_change["record"]["source"]["quote"] = "正文中不存在的事实证据"
        with self.assertRaisesRegex(
            novel_continuity.ContinuityError, "quote was not found verbatim"
        ):
            self.stage_chapter(
                bad_root,
                1,
                fact_changes=(bad_change,),
            )

    def test_confirmed_contradictions_block_but_suspected_exception_can_pass(self) -> None:
        cases = {
            "character": (
                "character_state",
                "已骨折的人物无解释恢复奔跑",
            ),
            "secret": (
                "knowledge_boundaries",
                "人物提前知道尚未揭示的幕后身份",
            ),
            "item": (
                "items_and_resources",
                "唯一钥匙在同一时刻出现在两地",
            ),
            "number": (
                "items_and_resources",
                "时间债务余额与前章账本不一致",
            ),
        }
        for name, (dimension, problem) in cases.items():
            with self.subTest(name=name):
                root = self.init_project(f"block-{name}")
                package = self.stage_chapter(root, 1)
                audit_path = self.audit_path(package)
                audit = read_json(audit_path)
                quote = evidence_quote(package / "chapter.md")
                audit.update(
                    {
                        "decision": "block",
                        "findings": [
                            {
                                "id": f"CT-{name.upper()}",
                                "dimension": dimension,
                                "severity": "error",
                                "certainty": "confirmed",
                                "problem": problem,
                                "impact": "继续写会扩大正典矛盾",
                                "suggested_fix": "在隔离暂存区修正后重新生成状态增量和审计",
                                "author_judgment": False,
                                "evidence": [
                                    {
                                        "path": "candidate",
                                        "location": "第一行",
                                        "quote": quote,
                                    }
                                ],
                            }
                        ],
                    }
                )
                write_json(audit_path, audit)
                result, code = novel_continuity.check_package_command(
                    SimpleNamespace(root=str(root), package=str(package))
                )
                self.assertEqual(code, 1)
                self.assertIn("requires a passing continuity audit", result["error"])
                self.assertEqual(read_json(root / "novel.json")["current_chapter"], 0)

        root = self.init_project("warning-exception")
        package = self.stage_chapter(root, 1)
        audit_path = self.audit_path(package)
        audit = read_json(audit_path)
        audit["findings"] = [
            {
                "id": "CT-WARNING",
                "dimension": "threads_and_payoffs",
                "severity": "warning",
                "certainty": "intentional_exception",
                "problem": "表面冲突可能是不可靠叙述或有意误导",
                "impact": "需要在后续回收时保持标记",
                "suggested_fix": "登记例外并在回收节点复核",
                "author_judgment": True,
                "evidence": [
                    {
                        "path": "candidate",
                        "location": "第一行",
                        "quote": evidence_quote(package / "chapter.md"),
                    }
                ],
            }
        ]
        audit["decision"] = "pass"
        write_json(audit_path, audit)
        result, code = novel_continuity.check_package_command(
            SimpleNamespace(root=str(root), package=str(package))
        )
        self.assertEqual(code, 0, result)

    def test_risk_and_key_chapters_enforce_independence_and_author_confirmation(self) -> None:
        root = self.init_project("risk-review")
        package = self.stage_chapter(
            root, 1, risk_triggers=("secret_boundary",)
        )
        audit_path = self.audit_path(package)
        audit = read_json(audit_path)
        audit["reviewer"] = {
            "mode": "self",
            "reviewer_id": "same-drafting-context",
            "independent_context": False,
        }
        write_json(audit_path, audit)
        result, code = novel_continuity.check_package_command(
            SimpleNamespace(root=str(root), package=str(package))
        )
        self.assertEqual(code, 1)
        self.assertIn("independent-context reviewer", result["error"])

        key_root = self.init_project("key-confirmation")
        key_package = self.stage_chapter(key_root, 1, chapter_class="key")
        context = read_json(key_package / "continuity-context.json")
        context["author_confirmation_reference"] = ""
        write_json(key_package / "continuity-context.json", context)
        result, code = novel_continuity.check_package_command(
            SimpleNamespace(root=str(key_root), package=str(key_package))
        )
        self.assertEqual(code, 1)
        self.assertIn("author confirmation reference", result["error"])

    def test_fifth_chapter_blocks_next_context_and_delivery_until_global_review(self) -> None:
        root = self.init_project("five-chapter-gate")
        for number in range(1, 6):
            package = self.stage_chapter(root, number)
            novel_project.commit_chapter(
                SimpleNamespace(root=str(root), package=str(package))
            )
        status = novel_continuity.continuity_status(root)
        self.assertEqual(status["status"], "review_due")
        self.assertEqual(status["global_review_due_through"], 5)
        with self.assertRaisesRegex(
            novel_continuity.ContinuityError, "global continuity review"
        ):
            novel_continuity.prepare_context(
                SimpleNamespace(
                    root=str(root),
                    chapter=6,
                    output=str(self.base / "chapter-6-context.json"),
                )
            )
        with self.assertRaisesRegex(novel_export.ExportError, "global continuity review"):
            novel_export.export_project(
                SimpleNamespace(root=str(root), format=["txt"], force=False)
            )

        baseline = seal_full_baseline(root)
        self.assertEqual(baseline["through_chapter"], 5)
        self.assertEqual(novel_continuity.continuity_status(root)["status"], "current")
        with self.assertRaisesRegex(novel_export.ExportError, "periodic quality review"):
            novel_export.export_project(
                SimpleNamespace(root=str(root), format=["txt"], force=False)
            )

    def test_revision_impact_propagates_and_invalidation_blocks_until_rebaseline(self) -> None:
        root = self.init_project("revision-impact")
        self.seed_direct_chapters(root, 3)
        local = novel_continuity.dependency_impact(
            root,
            ["manuscript/chapters/0001-基线章.md"],
            "local_fact",
        )
        self.assertEqual(local["affected_chapters"], [1, 2, 3])
        core = novel_continuity.dependency_impact(
            root, ["story-bible/world.md"], "world_rule"
        )
        self.assertEqual(core["affected_chapters"], [1, 2, 3])
        invalidated = novel_continuity.invalidate_command(
            SimpleNamespace(
                root=str(root),
                changed_path=["manuscript/chapters/0001-基线章.md"],
                change_type="local_fact",
                reason="unit test old chapter correction",
                authorization_reference="unit test author approved local revision",
            )
        )
        self.assertTrue(invalidated["delivery_blocked"])
        status = novel_continuity.continuity_status(root)
        self.assertEqual(status["status"], "stale")
        self.assertEqual(status["open_invalidations"], 1)
        with self.assertRaises(novel_continuity.ContinuityError):
            novel_continuity.ensure_delivery_allowed(root)

        seal_full_baseline(root)
        recovered = novel_continuity.continuity_status(root)
        self.assertEqual(recovered["status"], "current")
        self.assertEqual(recovered["open_invalidations"], 0)

    def test_corrupt_head_and_package_path_escape_fail_closed(self) -> None:
        root = self.init_project("corrupt-head")
        write_text(root / novel_continuity.HEAD_PATH, "[]\n")
        status = novel_continuity.continuity_status(root)
        self.assertEqual(status["status"], "stale")
        self.assertTrue(status["delivery_blocked"])
        self.assertTrue(any("Invalid continuity metadata" in item for item in status["errors"]))

        path_root = self.init_project("path-escape")
        package = path_root / "staging/chapters/0001-escape"
        write_text(path_root / "staging/outside.md", "# 不应读取\n")
        write_json(
            package / "commit.json",
            {
                "schema_version": 1,
                "chapter_number": 1,
                "chapter_file": "../../outside.md",
            },
        )
        with self.assertRaisesRegex(novel_continuity.ContinuityError, "escapes staging"):
            novel_continuity.prepare_audit(
                SimpleNamespace(root=str(path_root), package=str(package))
            )


if __name__ == "__main__":
    unittest.main()
