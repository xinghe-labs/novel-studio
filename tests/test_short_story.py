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

import novel_export  # noqa: E402
import novel_originality  # noqa: E402
import novel_project  # noqa: E402
import novel_review  # noqa: E402
import novel_workspace  # noqa: E402
import novel_continuity  # noqa: E402
from continuity_test_utils import (  # noqa: E402
    complete_staged_continuity,
    refresh_fixture_base,
    seal_full_baseline,
)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ShortStoryWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)
        created = novel_workspace.create_project(
            self.workspace,
            title="最后一班潮汐",
            genre="现实悬疑",
            work_type="short_story",
            target_words=12000,
            short_story_slug="last-tide",
            project_date="20260830",
        )
        self.root = Path(created["project_root"])
        self.project_id = created["project_id"]
        work = novel_workspace.create_work(
            self.workspace,
            project_id=self.project_id,
            purpose="短故事测试",
        )
        self.work_id: str | None = work["work_id"]
        novel_workspace.acquire_lock(self.workspace, self.work_id)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def commit_args(self, package: Path) -> SimpleNamespace:
        if self.work_id is None:
            work = novel_workspace.create_work(
                self.workspace,
                project_id=self.project_id,
                purpose="短故事测试提交",
            )
            self.work_id = work["work_id"]
            novel_workspace.acquire_lock(self.workspace, self.work_id)
        novel_workspace.refresh_base(
            self.workspace, self.work_id, "测试已完成提交前项目复核"
        )
        return SimpleNamespace(
            root=str(self.root),
            package=str(package),
            workspace=str(self.workspace),
            work_id=self.work_id,
        )

    def commit(self, package: Path) -> dict:
        result = novel_project.commit_chapter(self.commit_args(package))
        assert self.work_id is not None
        novel_workspace.refresh_base(
            self.workspace, self.work_id, "测试提交后校验通过"
        )
        return result

    def confirm_framework(self) -> None:
        replacements = {
            "story-bible/premise.md": "# 故事核心\n\n失踪的摆渡员只在退潮后的末班船出现。\n",
            "story-bible/cast.md": "# 人物档案\n\n## 林汐\n\n她必须在天亮前找到父亲。\n",
            "story-bible/world.md": "# 世界规则\n\n末班船只能在最低潮位靠岸。\n",
            "story-bible/style-guide.md": "# 叙事声音\n\n近距离第三人称，克制、紧迫。\n",
            "outlines/master-outline.md": "# 全篇结构\n\n林汐登船、识破谎言并作出不可撤回的选择。\n",
        }
        for relative, content in replacements.items():
            write_text(self.root / relative, content)
        refresh_fixture_base(
            self.root,
            self.workspace,
            self.work_id,
            reference="测试夹具已回读并确认短故事框架字段",
        )
        novel_project.framework_state(
            SimpleNamespace(
                root=str(self.root),
                stage="complete",
                confirmation="confirmed",
                requirements_confidence=97,
                story_confidence=97,
                authorization_reference="测试模拟作者确认短故事完整框架",
                workspace=str(self.workspace),
                work_id=self.work_id,
            )
        )

    def prepare_originality_plan(self) -> None:
        plan = {
            "schema_version": 1,
            "status": "ready",
            "candidate": {
                "logline": {
                    "text": "女孩登上只在最低潮出现的末班船寻找失踪父亲。",
                    "influences": [],
                    "causal_transformation": "",
                },
                "relationships": [
                    {
                        "text": "女儿必须判断摆渡员是否就是失踪的父亲",
                        "influences": [],
                        "causal_transformation": "",
                    }
                ],
                "world_rules": [
                    {
                        "text": "末班船只在最低潮位靠岸一次",
                        "influences": [],
                        "causal_transformation": "",
                    }
                ],
                "first_three_nodes": [
                    {"text": "收到旧船票", "influences": [], "causal_transformation": ""},
                    {"text": "末班船靠岸", "influences": [], "causal_transformation": ""},
                    {"text": "摆渡员否认身份", "influences": [], "causal_transformation": ""},
                ],
                "core_twist": {
                    "text": "父亲留下的是让女儿停止等待的最后一次告别",
                    "influences": [],
                    "causal_transformation": "",
                },
            },
            "references": [],
            "thresholds": {"structural_similarity_review": 0.58},
        }
        write_text(
            self.root / "research/originality-plan.json",
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        )

    def stage_story(self) -> Path:
        package = self.root / "staging/chapters/0001-complete-story"
        manuscript_filename = "0001-最后一班潮汐.md"
        write_text(
            package / "chapter.md",
            "# 最后一班潮汐\n\n"
            "退潮后的码头露出一条从未登记的石阶。\n\n"
            "林汐握着父亲留下的旧船票，登上了没有航班号的末班船。\n"
            "她说‘* * *’不是接头暗号。\n\n"
            "---\n\n"
            "天亮前，她终于明白这张船票不是重逢的凭证，而是一场告别。\n",
        )
        write_text(
            package / "memory.md",
            "# 短故事全篇记忆卡\n\n"
            f"- 正文：[唯一主稿](../../manuscript/chapters/{manuscript_filename})\n"
            "- 状态：committed\n\n"
            "## 事实摘要\n\n林汐登上末班船并接受父亲留下的告别。\n",
        )
        state = read_json(self.root / "continuity/state.json")
        state.update(
            {
                "through_chapter": 1,
                "story_time": "退潮至天亮前",
                "characters": {
                    "林汐": {
                        "location": "末班船",
                        "condition": [],
                        "knowledge": ["船票是最后的告别"],
                        "beliefs": [],
                        "goal": "停止等待父亲归来",
                        "inventory": ["旧船票"],
                    }
                },
                "open_threads": [],
            }
        )
        write_text(
            package / "continuity-state.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        self.prepare_originality_plan()
        refresh_fixture_base(
            self.root,
            self.workspace,
            self.work_id,
            reference="测试夹具已回读并确认短故事原创性计划",
        )
        report, code = novel_originality.audit_project(
            SimpleNamespace(
                root=str(self.root),
                candidate=[str(package / "chapter.md")],
                reference=None,
                exact_minimum=18,
                near_threshold=0.72,
                max_findings=30,
                no_report=False,
                workspace=str(self.workspace),
                work_id=self.work_id,
            )
        )
        self.assertEqual(code, 0)
        commit = {
            "schema_version": 1,
            "chapter_number": 1,
            "manuscript_filename": manuscript_filename,
            "chapter_file": "chapter.md",
            "memory_file": "memory.md",
            "continuity_state_file": "continuity-state.json",
            "originality_report": report["report_path"],
            "index": {
                "title": "最后一班潮汐",
                "pov": "林汐",
                "story_time": "退潮至天亮前",
                "location": "旧码头与末班船",
                "fact_summary": "林汐登船并接受父亲留下的告别",
                "key_change": "从等待重逢转为接受失去",
                "thread_ids": [],
            },
        }
        write_text(
            package / "commit.json",
            json.dumps(commit, ensure_ascii=False, indent=2) + "\n",
        )
        complete_staged_continuity(
            self.root,
            package,
            workspace=self.workspace,
            work_id=self.work_id,
        )
        return package

    def test_short_story_end_to_end(self) -> None:
        manifest = read_json(self.root / "novel.json")
        project_metadata = read_json(self.root / ".novel-project.json")
        platform = read_json(self.root / "research/platform.json")
        self.assertEqual(manifest["work_type"], "short_story")
        self.assertEqual(project_metadata["work_type"], "short_story")
        self.assertEqual(manifest["target_words"], 12000)
        self.assertEqual(manifest["periodic_review"]["interval_chapters"], 1)
        self.assertEqual(
            platform["adapters"]["fanqie"]["publication_profile"],
            "fanqie_short_story_public",
        )
        self.assertEqual(
            platform["adapters"]["fanqie"]["short_story_market_data"],
            "not_implemented",
        )
        self.assertIn(
            "fanqie_short_story_public", platform["publication_profiles"]
        )
        self.assertIn(
            "短故事只提交一个完整正文单元",
            (self.root / "manuscript/index.md").read_text(encoding="utf-8"),
        )

        self.confirm_framework()
        package = self.stage_story()
        committed = self.commit(package)
        self.assertEqual(committed["work_type"], "short_story")
        self.assertTrue(committed["review_required_before_next_commit"])

        status = novel_review.review_status(self.root)
        self.assertEqual(status["review_mode"], "completion")
        self.assertTrue(status["review_due"])
        continuity = novel_continuity.continuity_status(self.root)
        self.assertEqual(continuity["status"], "review_due")
        self.assertTrue(continuity["global_review_due"])
        with self.assertRaisesRegex(novel_export.ExportError, "global continuity review"):
            novel_export.export_project(
                SimpleNamespace(root=str(self.root), format=None, force=False)
            )
        baseline = seal_full_baseline(
            self.root,
            workspace=self.workspace,
            work_id=self.work_id,
        )
        self.assertEqual(baseline["through_chapter"], 1)
        with self.assertRaisesRegex(novel_export.ExportError, "completion review"):
            novel_export.export_project(
                SimpleNamespace(root=str(self.root), format=None, force=False)
            )

        packet_path = self.base / "completion-review-packet.json"
        prepared = novel_review.prepare_review(
            SimpleNamespace(
                root=str(self.root),
                output=str(packet_path),
                report_output=None,
                through=None,
                force=False,
            )
        )
        self.assertEqual(prepared["review_mode"], "completion")
        report_path = Path(prepared["report_template_path"])
        report = read_json(report_path)
        self.assertEqual(report["report_kind"], "short_story_completion_review")
        report.update(
            {
                "status": "complete",
                "reviewed_at": "2026-08-30T00:00:00+00:00",
                "summary": "全篇因果、人物、规则、语言和结尾兑现均通过。",
                "findings": [],
                "residual_risks": [],
                "reviewer": {
                    "mode": "independent",
                    "reviewer_id": "test-independent-quality-reviewer",
                    "independent_context": True,
                },
            }
        )
        write_text(
            report_path,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        recorded = novel_review.record_review(
            SimpleNamespace(
                root=str(self.root),
                packet=str(packet_path),
                report=str(report_path),
                authorization_reference="测试完成短故事全篇审核",
                workspace=str(self.workspace),
                work_id=self.work_id,
            )
        )
        self.assertEqual(recorded["review_mode"], "completion")
        self.assertFalse(recorded["periodic_review"]["review_due"])
        self.assertTrue(
            (self.root / recorded["report_path"]).is_file()
        )

        exported = novel_export.export_project(
            SimpleNamespace(root=str(self.root), format=None, force=False)
        )
        self.assertEqual(exported["work_type"], "short_story")
        exports = self.root / "exports"
        self.assertTrue((exports / "《最后一班潮汐》-短故事定稿.txt").is_file())
        self.assertTrue(
            (exports / "《最后一班潮汐》-短故事审阅稿.docx").is_file()
        )
        fanqie = exports / "fanqie-short-story/《最后一班潮汐》.txt"
        self.assertTrue(fanqie.is_file())
        fanqie_text = fanqie.read_text(encoding="utf-8")
        self.assertFalse(fanqie_text.startswith("#"))
        self.assertIn("她说‘* * *’不是接头暗号。", fanqie_text)
        self.assertIn("不是接头暗号。\n\n天亮前", fanqie_text)
        self.assertNotRegex(fanqie_text, r"(?m)^\s*\*\s+\*\s+\*\s*$")
        self.assertFalse((exports / "fanqie").exists())
        export_manifest = read_json(exports / "export-manifest.json")
        self.assertEqual(
            export_manifest["kind"], "chinese-short-story-derived-exports"
        )
        self.assertEqual(export_manifest["source"]["work_type"], "short_story")
        self.assertEqual(
            export_manifest["fanqie_publication_profile"], "fanqie_short_story"
        )
        self.assertEqual(export_manifest["delivery_quality"]["status"], "pass")
        self.assertEqual(
            export_manifest["delivery_quality"]["compatibility_scope"],
            "verified_profiles_only",
        )
        fanqie_record = next(
            item
            for item in export_manifest["outputs"]
            if item["format"] == "fanqie"
        )
        self.assertEqual(
            fanqie_record["delivery_profile"],
            {"name": "fanqie-short-story", "version": 1},
        )
        self.assertEqual(fanqie_record["quality"]["status"], "pass")
        status, status_code = novel_export.export_status(
            SimpleNamespace(root=str(self.root))
        )
        self.assertEqual(status_code, 0)
        self.assertEqual(status["delivery_quality"], "pass")

        errors, _ = novel_project.collect_validation(self.root)
        self.assertEqual(errors, [])

    def test_short_story_rejects_a_second_canonical_unit(self) -> None:
        self.confirm_framework()
        package = self.stage_story()
        self.commit(package)
        second = self.root / "staging/chapters/0002-not-allowed"
        second.mkdir(parents=True)
        write_text(
            second / "commit.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "chapter_number": 2,
                    "manuscript_filename": "0002-不应存在.md",
                },
                ensure_ascii=False,
            )
            + "\n",
        )
        with self.assertRaisesRegex(novel_project.ProjectError, "one complete"):
            novel_project.commit_chapter(self.commit_args(second))


if __name__ == "__main__":
    unittest.main()
