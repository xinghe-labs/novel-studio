from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILL_ROOT / "tests"))

import novel_memory  # noqa: E402
import novel_originality  # noqa: E402
import novel_project  # noqa: E402
import novel_research  # noqa: E402
from continuity_test_utils import complete_staged_continuity  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class NovelV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def init_project(self, name: str = "project") -> Path:
        root = self.base / name
        result = novel_project.init_project(
            SimpleNamespace(
                root=str(root), title="测试长篇", language="zh-CN", genre="悬疑"
            )
        )
        self.assertEqual(result["status"], "created")
        return root

    def complete_originality_plan(self, root: Path) -> None:
        plan = {
            "schema_version": 1,
            "status": "ready",
            "candidate": {
                "logline": {
                    "text": "修表师收到来自十年后的坏表，被迫查清小镇被改写的共同记忆。",
                    "influences": [],
                    "causal_transformation": "",
                },
                "relationships": [
                    {
                        "text": "修表师与失忆警员互相需要又互相怀疑",
                        "influences": [],
                        "causal_transformation": "",
                    }
                ],
                "world_rules": [
                    {
                        "text": "每修复一只未来钟表，修表师会失去一段自己的过去",
                        "influences": [],
                        "causal_transformation": "",
                    }
                ],
                "first_three_nodes": [
                    {"text": "坏表倒走", "influences": [], "causal_transformation": ""},
                    {"text": "警员否认来访", "influences": [], "causal_transformation": ""},
                    {"text": "主角忘记母亲", "influences": [], "causal_transformation": ""},
                ],
                "core_twist": {
                    "text": "主角自己发出了第一只坏表",
                    "influences": [],
                    "causal_transformation": "",
                },
            },
            "references": [],
            "thresholds": {"structural_similarity_review": 0.58},
        }
        write_text(
            root / "research/originality-plan.json",
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        )

    def confirm_framework(self, root: Path) -> None:
        replacements = {
            "story-bible/premise.md": "# 故事核心\n\n一句话故事、读者承诺、冲突和主题已经确认。\n",
            "story-bible/cast.md": "# 人物档案\n\n## 林某D\n\n目标明确。\n",
            "story-bible/world.md": "# 世界规则\n\n修复未来钟表必须失去记忆。\n",
            "story-bible/style-guide.md": "# 叙事声音\n\n第三人称限知，过去时。\n",
            "outlines/master-outline.md": "# 总纲\n\n因果骨架已经确认。\n",
        }
        for relative, content in replacements.items():
            write_text(root / relative, content)
        novel_project.framework_state(
            SimpleNamespace(
                root=str(root),
                stage="complete",
                confirmation="confirmed",
                requirements_confidence=96,
                story_confidence=96,
                authorization_reference="测试中模拟作者明确确认框架",
            )
        )

    def prepare_staged_chapter(self, root: Path, package_name: str = "0001") -> Path:
        package = root / "staging/chapters" / package_name
        write_text(
            package / "chapter.md",
            "# 第一章 坏表\n\n林某D在雾港旧邮局收到一只倒走的怀表。\n",
        )
        write_text(
            package / "memory.md",
            "# 第0001章记忆卡\n\n- 正文：[0001-坏表](../../manuscript/chapters/0001-坏表.md)\n"
            "- 状态：committed\n\n## 事实摘要\n\n林某D收到倒走的怀表。\n",
        )
        state = read_json(root / "continuity/state.json")
        state["through_chapter"] = 1
        state["story_time"] = "第一日清晨"
        state["characters"] = {
            "林某D": {
                "location": "雾港旧邮局",
                "condition": [],
                "knowledge": ["怀表正在倒走"],
                "beliefs": [],
                "goal": "找到寄件人",
                "inventory": ["倒走的怀表"],
            }
        }
        write_text(
            package / "continuity-state.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        return package

    def create_passing_audit(self, root: Path, package: Path) -> str:
        self.complete_originality_plan(root)
        report, code = novel_originality.audit_project(
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
        self.assertEqual(report["decision"], "pass")
        return report["report_path"]

    def write_commit_manifest(self, package: Path, report_path: str) -> None:
        commit = {
            "schema_version": 1,
            "chapter_number": 1,
            "manuscript_filename": "0001-坏表.md",
            "chapter_file": "chapter.md",
            "memory_file": "memory.md",
            "continuity_state_file": "continuity-state.json",
            "originality_report": report_path,
            "index": {
                "title": "坏表",
                "pov": "林某D",
                "story_time": "第一日清晨",
                "location": "雾港旧邮局",
                "fact_summary": "林某D收到一只倒走的怀表",
                "key_change": "获得怀表并决定寻找寄件人",
                "thread_ids": ["T-001"],
            },
        }
        write_text(
            package / "commit.json",
            json.dumps(commit, ensure_ascii=False, indent=2) + "\n",
        )

    def test_fanqie_adapter_uses_offline_fixture_and_registers_provenance(self) -> None:
        root = self.init_project()
        fixture = SKILL_ROOT / "tests/fixtures/fanqie-library.json"
        result = novel_research.collect_platform(
            SimpleNamespace(
                root=str(root),
                platform="fanqie",
                channel="all",
                sort="hot",
                page_count=18,
                page_index=0,
                input=str(fixture),
                observed_at="2026-08-29T12:00:00+00:00",
                timeout=5.0,
            )
        )
        self.assertEqual(result["books"], 2)
        records = novel_research.read_manifest(root)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["rights_status"] == "public_web" for record in records))
        normalized_path = root / result["normalized"]["record"]["path"]
        normalized = read_json(normalized_path)
        self.assertFalse(normalized["books"][0]["text_requires_detail_verification"])
        self.assertTrue(normalized["books"][1]["text_requires_detail_verification"])
        errors, warnings = novel_project.collect_validation(root)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_user_local_source_requires_project_authorization(self) -> None:
        root = self.init_project()
        local = self.base / "private-reference.txt"
        write_text(local, "这是用户合法提供、仅供本机分析的参考文本。")
        common = dict(
            root=root,
            source=local,
            origin="user_local",
            source_kind="authorized_full_text",
            rights_status="user_authorized",
            authorization_scope="project_research_and_originality_audit",
            external_use="local_only",
            source_url="",
            observed_at="2026-08-29T12:00:00+00:00",
            platform="",
            originality_compare=True,
            provenance_note="unit test",
        )
        with self.assertRaises(novel_research.ResearchError):
            novel_research.register_source(
                **common, authorization_reference="", copy_external=True
            )
        record, added = novel_research.register_source(
            **common,
            authorization_reference="作者在本项目中明确授权本机研究",
            copy_external=True,
        )
        self.assertTrue(added)
        self.assertEqual(record["external_use"], "local_only")
        self.assertTrue((root / record["path"]).is_file())
        result, code = novel_research.verify_command(SimpleNamespace(root=str(root)))
        self.assertEqual(code, 0)
        self.assertEqual(result["errors"], [])

    def test_sqlite_rebuild_incremental_update_and_chinese_search(self) -> None:
        root = self.init_project()
        chapter = root / "manuscript/chapters/0001-雾港来信.md"
        write_text(chapter, "# 第一章\n\n林某D在雾港旧邮局拿到姐姐留下的钥匙。\n")
        write_text(
            root / "memory/chapters/0001.md",
            "# 第0001章记忆卡\n\n- 正文：[0001](../../manuscript/chapters/0001-雾港来信.md)\n",
        )
        index = (root / "manuscript/index.md").read_text(encoding="utf-8")
        write_text(
            root / "manuscript/index.md",
            index.rstrip()
            + "\n| 0001 | 雾港来信 | 林某D | 第一日 | 雾港旧邮局 | 获得钥匙 | 决定调查 | T-001 | [正文](chapters/0001-雾港来信.md) |\n",
        )
        manifest = read_json(root / "novel.json")
        manifest["current_chapter"] = 1
        write_text(root / "novel.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        state = read_json(root / "continuity/state.json")
        state.update(
            {
                "through_chapter": 1,
                "story_time": "第一日",
                "characters": {
                    "林某D": {
                        "location": "雾港旧邮局",
                        "inventory": ["铜钥匙"],
                        "relationships": {"姐姐": "失踪十年"},
                    }
                },
                "open_threads": ["T-001"],
            }
        )
        write_text(
            root / "continuity/state.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        rebuilt = novel_memory.rebuild_index(root)
        self.assertEqual(rebuilt["status"], "rebuilt")
        result, code = novel_memory.search_index(
            SimpleNamespace(
                root=str(root),
                query="雾港旧邮局",
                mode="phrase",
                entity_type=None,
                limit=20,
                require_fresh=True,
            )
        )
        self.assertEqual(code, 0)
        self.assertGreater(result["matches"], 0)
        entity_result, _ = novel_memory.search_index(
            SimpleNamespace(
                root=str(root),
                query="林某D",
                mode="phrase",
                entity_type="character",
                limit=20,
                require_fresh=True,
            )
        )
        self.assertGreater(entity_result["matches"], 0)
        write_text(chapter, chapter.read_text(encoding="utf-8") + "她把铜钥匙藏进袖口。\n")
        self.assertEqual(novel_memory.database_status(root)["status"], "stale")
        updated = novel_memory.update_index(root)
        self.assertIn("manuscript/chapters/0001-雾港来信.md", updated["changed"])
        self.assertEqual(novel_memory.database_status(root)["status"], "fresh")

    def test_wording_and_structural_originality_layers_block_independently(self) -> None:
        root = self.init_project()
        self.complete_originality_plan(root)
        source = self.base / "reference.txt"
        copied_sentence = "钟楼敲响第十三次以后，全镇居民同时忘记了昨天发生的那场审判。"
        write_text(source, copied_sentence + "\n这是用于测试的后续段落。")
        record, _ = novel_research.register_source(
            root,
            source,
            origin="user_local",
            source_kind="authorized_full_text",
            rights_status="user_authorized",
            authorization_scope="project_originality_audit",
            external_use="local_only",
            authorization_reference="测试授权",
            source_url="",
            observed_at="2026-08-29T12:00:00+00:00",
            platform="",
            originality_compare=True,
            provenance_note="unit test",
        )
        candidate = root / "staging/chapters/0001/chapter.md"
        write_text(candidate, "# 第一章\n\n" + copied_sentence + "\n")
        report, code = novel_originality.audit_project(
            SimpleNamespace(
                root=str(root),
                candidate=[str(candidate)],
                reference=[record["path"]],
                exact_minimum=18,
                near_threshold=0.72,
                max_findings=30,
                no_report=True,
            )
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["decision"], "block")
        self.assertTrue(report["wording"]["exact_findings"])
        near = novel_originality.near_overlaps(
            "钟楼敲过第十三次以后，全镇居民同时忘记了昨天发生的那次审判。",
            copied_sentence,
            threshold=0.6,
            max_findings=5,
        )
        self.assertTrue(near)

        plan = read_json(root / "research/originality-plan.json")
        for dimension in ("logline", "core_twist"):
            plan["candidate"][dimension]["influences"] = ["REF-ONE"]
            plan["candidate"][dimension]["causal_transformation"] = "改变代价与知情权"
        plan["candidate"]["relationships"][0]["influences"] = ["REF-ONE"]
        plan["candidate"]["relationships"][0]["causal_transformation"] = "交换权力持有者"
        plan["references"] = [{"source_id": "REF-ONE", "work": "参考作品", "structures": {}}]
        structure = novel_originality.audit_structure(plan)
        self.assertEqual(structure["status"], "block")
        self.assertEqual(
            structure["single_source_dominance"][0]["severity"], "block"
        )

        single_dimension_plan = read_json(root / "research/originality-plan.json")
        single_dimension_plan["candidate"]["logline"]["influences"] = ["REF-ONE"]
        single_dimension_plan["candidate"]["logline"][
            "causal_transformation"
        ] = "改变目标、代价与信息来源"
        single_dimension_plan["references"] = [
            {"source_id": "REF-ONE", "work": "参考作品", "structures": {}}
        ]
        single_dimension = novel_originality.audit_structure(single_dimension_plan)
        self.assertEqual(single_dimension["status"], "pass")
        self.assertEqual(
            single_dimension["single_source_dominance"][0]["severity"], "pass"
        )

        single_dimension_plan["candidate"]["world_rules"][0]["influences"] = [
            "REF-ONE"
        ]
        single_dimension_plan["candidate"]["world_rules"][0][
            "causal_transformation"
        ] = "改变规则控制者与违规后果"
        two_dimensions = novel_originality.audit_structure(single_dimension_plan)
        self.assertEqual(two_dimensions["status"], "review")
        self.assertEqual(
            two_dimensions["single_source_dominance"][0]["severity"], "review"
        )

    def test_chapter_commit_and_failure_rollback(self) -> None:
        root = self.init_project("success")
        self.confirm_framework(root)
        package = self.prepare_staged_chapter(root)
        report_path = self.create_passing_audit(root, package)
        self.write_commit_manifest(package, report_path)
        complete_staged_continuity(root, package)
        result = novel_project.commit_chapter(
            SimpleNamespace(root=str(root), package=str(package))
        )
        self.assertEqual(result["status"], "committed")
        self.assertEqual(result["humanization_outcome"], "unchanged")
        self.assertEqual(result["humanization_review"], "humanization-review.json")
        self.assertTrue((root / "manuscript/chapters/0001-坏表.md").is_file())
        self.assertEqual(read_json(root / "novel.json")["current_chapter"], 1)
        errors, _ = novel_project.collect_validation(root)
        self.assertEqual(errors, [])

        rollback_root = self.init_project("rollback")
        self.confirm_framework(rollback_root)
        rollback_package = self.prepare_staged_chapter(rollback_root)
        rollback_report = self.create_passing_audit(rollback_root, rollback_package)
        self.write_commit_manifest(rollback_package, rollback_report)
        complete_staged_continuity(rollback_root, rollback_package)
        original_index = (rollback_root / "manuscript/index.md").read_bytes()
        real_replace = os.replace
        calls = {"count": 0}

        def fail_once(source_path: str, target_path: str) -> None:
            calls["count"] += 1
            if calls["count"] == 3:
                raise OSError("injected transaction failure")
            real_replace(source_path, target_path)

        with mock.patch.object(novel_project.os, "replace", side_effect=fail_once):
            with self.assertRaises(novel_project.ProjectError):
                novel_project.commit_chapter(
                    SimpleNamespace(
                        root=str(rollback_root), package=str(rollback_package)
                    )
                )
        self.assertFalse(
            (rollback_root / "manuscript/chapters/0001-坏表.md").exists()
        )
        self.assertFalse((rollback_root / "memory/chapters/0001.md").exists())
        self.assertEqual(read_json(rollback_root / "novel.json")["current_chapter"], 0)
        self.assertEqual(
            (rollback_root / "manuscript/index.md").read_bytes(), original_index
        )

    def test_chapter_commit_requires_humanization_review_manifest_entry(self) -> None:
        root = self.init_project("missing-humanization")
        self.confirm_framework(root)
        package = self.prepare_staged_chapter(root)
        report_path = self.create_passing_audit(root, package)
        self.write_commit_manifest(package, report_path)
        complete_staged_continuity(root, package)
        commit = read_json(package / "commit.json")
        del commit["humanization_review_file"]
        write_text(
            package / "commit.json",
            json.dumps(commit, ensure_ascii=False, indent=2) + "\n",
        )

        with self.assertRaisesRegex(
            novel_project.ProjectError, "humanization_review_file"
        ):
            novel_project.commit_chapter(
                SimpleNamespace(root=str(root), package=str(package))
            )
        self.assertFalse((root / "manuscript/chapters/0001-坏表.md").exists())

    def test_chapter_commit_rejects_stale_humanization_result_hash(self) -> None:
        root = self.init_project("stale-humanization")
        self.confirm_framework(root)
        package = self.prepare_staged_chapter(root)
        report_path = self.create_passing_audit(root, package)
        self.write_commit_manifest(package, report_path)
        complete_staged_continuity(root, package)
        review = read_json(package / "humanization-review.json")
        review["result"]["sha256"] = "0" * 64
        write_text(
            package / "humanization-review.json",
            json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        )

        with self.assertRaisesRegex(
            novel_project.ProjectError,
            "Humanization review result hash does not match",
        ):
            novel_project.commit_chapter(
                SimpleNamespace(root=str(root), package=str(package))
            )
        self.assertFalse((root / "manuscript/chapters/0001-坏表.md").exists())

    def test_upgrade_is_add_only_and_idempotent(self) -> None:
        root = self.init_project()
        new_files = (
            root / "research/platform.json",
            root / "research/source-manifest.jsonl",
            root / "research/originality-plan.json",
        )
        for path in new_files:
            path.unlink()
        (root / "staging/chapters").rmdir()
        (root / "staging").rmdir()
        protected = (
            root / "novel.json",
            root / "planning/framework-session.md",
            root / "manuscript/index.md",
            root / "research/comparable-works.md",
        )
        before = {path: path.read_bytes() for path in protected}
        first = novel_project.upgrade_project(SimpleNamespace(root=str(root)))
        second = novel_project.upgrade_project(SimpleNamespace(root=str(root)))
        self.assertEqual(first["status"], "upgraded")
        self.assertEqual(second["status"], "already_current")
        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        errors, _ = novel_project.collect_validation(root)
        self.assertEqual(errors, [])

    def test_controlled_state_transitions_enforce_approval_and_confirmation_gates(self) -> None:
        root = self.init_project()
        with self.assertRaises(novel_project.ProjectError):
            novel_project.research_state(
                SimpleNamespace(
                    root=str(root),
                    candidate_approval=None,
                    deep_analysis="in_progress",
                    authorization_reference="测试尝试越过审批",
                )
            )
        approved = novel_project.research_state(
            SimpleNamespace(
                root=str(root),
                candidate_approval="approved",
                deep_analysis=None,
                authorization_reference="作者明确批准候选",
            )
        )
        self.assertEqual(approved["after"]["candidate_approval"], "approved")
        started = novel_project.research_state(
            SimpleNamespace(
                root=str(root),
                candidate_approval=None,
                deep_analysis="in_progress",
                authorization_reference="已批准候选，开始拆解",
            )
        )
        self.assertEqual(started["after"]["deep_analysis"], "in_progress")
        with self.assertRaises(novel_project.ProjectError):
            novel_project.framework_state(
                SimpleNamespace(
                    root=str(root),
                    stage="complete",
                    confirmation="confirmed",
                    requirements_confidence=96,
                    story_confidence=96,
                    authorization_reference="占位文件尚未同步",
                )
            )


if __name__ == "__main__":
    unittest.main()
