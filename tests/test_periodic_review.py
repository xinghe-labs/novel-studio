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

import novel_project  # noqa: E402
import novel_review  # noqa: E402
import novel_continuity  # noqa: E402
import novel_workspace  # noqa: E402
from continuity_test_utils import seal_full_baseline  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PeriodicReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.packet_counter = 0
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)
        self.work_ids: dict[Path, str] = {}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def init_project(self, name: str = "project") -> Path:
        root = self.workspace / "projects" / name
        novel_project.init_project(
            SimpleNamespace(
                root=str(root), title="周期审核测试", language="zh-CN", genre="悬疑"
            )
        )
        novel_workspace.register_project(self.workspace, root, project_id=name)
        return root

    def commit_args(self, root: Path, package: Path) -> SimpleNamespace:
        work_id = self.work_ids.get(root.resolve())
        if work_id is None:
            work = novel_workspace.create_work(
                self.workspace, project_id=root.name, purpose="周期审核测试提交"
            )
            work_id = work["work_id"]
            self.work_ids[root.resolve()] = work_id
            novel_workspace.acquire_lock(self.workspace, work_id)
        novel_workspace.refresh_base(
            self.workspace, work_id, "测试已完成提交前项目复核"
        )
        return SimpleNamespace(
            root=str(root),
            package=str(package),
            workspace=str(self.workspace),
            work_id=work_id,
        )

    def seed_chapters(self, root: Path, through: int) -> None:
        chapter_dir = root / "manuscript/chapters"
        memory_dir = root / "memory/chapters"
        for path in chapter_dir.glob("*.md"):
            path.unlink()
        for path in memory_dir.glob("*.md"):
            path.unlink()

        index = (root / "manuscript/index.md").read_text(encoding="utf-8")
        table_start = index.find("| 章号 |")
        if table_start >= 0:
            header_end = index.find("\n", index.find("\n", table_start) + 1) + 1
            index = index[:header_end]
        rows: list[str] = []
        for number in range(1, through + 1):
            filename = f"{number:04d}-测试章.md"
            write_text(
                chapter_dir / filename,
                f"# 第{number}章 测试章\n\n林某D在第{number}日记录第{number}条线索。\n",
            )
            write_text(
                memory_dir / f"{number:04d}.md",
                f"# 第{number:04d}章记忆卡\n\n"
                f"- 正文：[{filename}](../../manuscript/chapters/{filename})\n"
                f"- 事实：林某D记录第{number}条线索。\n",
            )
            rows.append(
                f"| {number:04d} | 测试章 | 林某D | 第{number}日 | 书房 | "
                f"记录线索 | 推进调查 | T-{number:03d} | [正文](chapters/{filename}) |\n"
            )
        write_text(root / "manuscript/index.md", index.rstrip() + "\n" + "".join(rows))

        manifest = read_json(root / "novel.json")
        manifest["current_chapter"] = through
        write_text(root / "novel.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        state = read_json(root / "continuity/state.json")
        state["through_chapter"] = through
        state["story_time"] = f"第{through}日" if through else ""
        write_text(
            root / "continuity/state.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        seal_full_baseline(root)

    def prepare_and_record(
        self,
        root: Path,
        *,
        decision: str = "pass",
        severity: str | None = None,
    ) -> dict:
        self.packet_counter += 1
        packet_path = self.base / f"packet-{self.packet_counter}.json"
        prepared = novel_review.prepare_review(
            SimpleNamespace(
                root=str(root),
                output=str(packet_path),
                report_output=None,
                through=None,
                force=False,
            )
        )
        report_path = Path(prepared["report_template_path"])
        report = read_json(report_path)
        report.update(
            {
                "status": "complete",
                "reviewed_at": "2026-08-30T00:00:00+00:00",
                "decision": decision,
                "summary": "已按全部维度复核现有章节。",
                "residual_risks": [],
                "reviewer": {
                    "mode": "independent",
                    "reviewer_id": "test-independent-quality-reviewer",
                    "independent_context": True,
                },
            }
        )
        if severity is not None:
            report["findings"] = [
                {
                    "id": "PR-001",
                    "severity": severity,
                    "scope": "next_chapters",
                    "chapters": [1],
                    "location": "第0001章第二段",
                    "evidence": "人物在同一时刻出现在两个地点。",
                    "problem": "地点连续性冲突。",
                    "impact": "后续行动的因果链无法成立。",
                    "suggested_fix": "统一人物所在地点并同步记忆卡。",
                    "author_judgment": False,
                }
            ]
        write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return novel_review.record_review(
            SimpleNamespace(
                root=str(root),
                packet=str(packet_path),
                report=str(report_path),
                authorization_reference="单元测试中确认记录本轮审核结论",
            )
        )

    def test_default_policy_triggers_after_five_chapters(self) -> None:
        root = self.init_project()
        manifest = read_json(root / "novel.json")
        self.assertEqual(
            manifest["periodic_review"],
            {
                "enabled": True,
                "interval_chapters": 5,
                "block_next_commit": True,
            },
        )

        self.seed_chapters(root, 4)
        before = novel_review.review_status(root)
        self.assertFalse(before["review_due"])
        self.assertEqual(before["next_due_chapter"], 5)

        self.seed_chapters(root, 5)
        due = novel_review.review_status(root)
        self.assertTrue(due["review_due"])
        self.assertEqual((due["review_from"], due["review_through"]), (1, 5))
        self.assertTrue(due["commit_blocked"])
        errors, warnings = novel_project.collect_validation(root)
        self.assertEqual(errors, [])
        self.assertTrue(any("Periodic review is due" in item for item in warnings))

    def test_passing_report_advances_next_checkpoint_to_ten(self) -> None:
        root = self.init_project()
        self.seed_chapters(root, 5)
        recorded = self.prepare_and_record(root)
        self.assertEqual(recorded["decision"], "pass")

        status = novel_review.review_status(root)
        self.assertFalse(status["review_due"])
        self.assertEqual(status["last_passed_through"], 5)
        self.assertEqual(status["next_due_chapter"], 10)
        self.assertFalse(status["commit_blocked"])

    def test_important_finding_blocks_until_a_new_passing_review(self) -> None:
        root = self.init_project()
        self.seed_chapters(root, 5)
        self.prepare_and_record(root, decision="needs_revision", severity="important")

        status = novel_review.review_status(root)
        self.assertTrue(status["commit_blocked"])
        self.assertEqual(status["latest_nonpassing_review"]["decision"], "needs_revision")
        with self.assertRaises(novel_review.ReviewError):
            novel_review.ensure_commit_allowed(root, 6)

        chapter = root / "manuscript/chapters/0001-测试章.md"
        write_text(chapter, chapter.read_text(encoding="utf-8") + "地点冲突已经修正。\n")
        self.prepare_and_record(root)
        status = novel_review.ensure_commit_allowed(root, 6)
        self.assertFalse(status["commit_blocked"])
        self.assertEqual(status["last_passed_through"], 5)

    def test_changing_a_reviewed_chapter_invalidates_the_old_pass(self) -> None:
        root = self.init_project()
        self.seed_chapters(root, 5)
        self.prepare_and_record(root)
        chapter = root / "manuscript/chapters/0003-测试章.md"
        write_text(chapter, chapter.read_text(encoding="utf-8") + "补写一条事实。\n")

        status = novel_review.review_status(root)
        self.assertTrue(status["review_due"])
        self.assertEqual(status["last_passed_through"], 0)
        self.assertTrue(any("source changed" in item for item in status["warnings"]))

    def test_legacy_mixed_report_cannot_satisfy_the_quality_gate(self) -> None:
        root = self.init_project()
        self.seed_chapters(root, 5)
        legacy_report = {
            "schema_version": novel_review.SCHEMA_VERSION,
            "report_kind": "periodic_novel_review",
            "status": "complete",
            "decision": "pass",
            "chapter_from": 1,
            "chapter_through": 5,
            "source_snapshot": novel_review.build_source_snapshot(root, 5),
        }
        write_text(
            root / "reviews/periodic/periodic-review-legacy-mixed.json",
            json.dumps(legacy_report, ensure_ascii=False, indent=2) + "\n",
        )

        status = novel_review.review_status(root)
        self.assertTrue(status["review_due"])
        self.assertEqual(status["last_passed_through"], 0)
        self.assertTrue(
            any("quality and continuity review separate" in item for item in status["warnings"])
        )

    def test_chapter_heading_number_and_title_must_match_the_index(self) -> None:
        root = self.init_project()
        self.seed_chapters(root, 2)
        chapter = root / "manuscript/chapters/0002-测试章.md"
        write_text(chapter, "# 第九章 另一个标题\n\n正文未变。\n")

        errors, _ = novel_project.collect_validation(root)
        self.assertTrue(any("heading number does not match" in item for item in errors))
        self.assertTrue(any("heading title does not match" in item for item in errors))

    def test_chapter_six_commit_is_blocked_before_any_canonical_write(self) -> None:
        root = self.init_project()
        self.seed_chapters(root, 5)
        package = root / "staging/chapters/0006"
        write_text(
            package / "commit.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "chapter_number": 6,
                    "manuscript_filename": "0006-测试章.md",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        commit_args = self.commit_args(root, package)

        with self.assertRaisesRegex(novel_project.ProjectError, "Periodic review is overdue"):
            novel_project.commit_chapter(commit_args)
        self.assertFalse((root / "manuscript/chapters/0006-测试章.md").exists())
        self.assertEqual(read_json(root / "novel.json")["current_chapter"], 5)

    def test_quality_review_blocks_next_chapter_context_before_drafting(self) -> None:
        root = self.init_project()
        self.seed_chapters(root, 5)

        with self.assertRaisesRegex(
            novel_continuity.ContinuityError,
            "Quality review hard gate blocks continuity context preparation",
        ):
            novel_continuity.build_context(root, 6)

        self.prepare_and_record(root)
        context = novel_continuity.build_context(root, 6)
        self.assertEqual(context["chapter_number"], 6)

    def test_review_interval_can_be_configured_per_project(self) -> None:
        root = self.init_project()
        result = novel_review.configure_policy(
            SimpleNamespace(
                root=str(root),
                interval=3,
                enabled=None,
                block_next_commit=None,
                authorization_reference="作者确认改为每三章审核一次",
            )
        )
        self.assertEqual(result["after"]["interval_chapters"], 3)
        self.seed_chapters(root, 3)
        status = novel_review.review_status(root)
        self.assertTrue(status["review_due"])
        self.assertEqual(status["review_through"], 3)


if __name__ == "__main__":
    unittest.main()
