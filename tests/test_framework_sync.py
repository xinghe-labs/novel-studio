from __future__ import annotations

import json
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

import novel_project  # noqa: E402
import novel_continuity  # noqa: E402
import novel_workspace  # noqa: E402
from continuity_test_utils import seal_full_baseline  # noqa: E402


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class FrameworkSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)
        self.root = self.workspace / "projects" / "framework-sync"
        novel_project.init_project(
            SimpleNamespace(
                root=str(self.root),
                title="框架同步测试",
                language="zh-CN",
                genre="悬疑",
            )
        )
        novel_workspace.register_project(
            self.workspace, self.root, project_id="framework-sync"
        )
        self.work = novel_workspace.create_work(
            self.workspace, project_id="framework-sync", purpose="框架同步测试"
        )
        novel_workspace.acquire_lock(self.workspace, self.work["work_id"])
        self.source = (
            self.workspace
            / "workspaces"
            / self.work["work_id"]
            / "framework-sync"
        )
        self.source_files = {
            "planning/framework-session.md": (
                "---\n"
                "schema_version: 1\n"
                "stage: complete\n"
                "confirmation: confirmed\n"
                "requirements_confidence: 97\n"
                "story_confidence: 96\n"
                "updated_at: 2026-09-13T00:00:00+00:00\n"
                "---\n\n"
                "# 框架确认会话\n\n作者确认的完整框架。\n"
            ).encode("utf-8"),
            "story-bible/premise.md": "# 故事核心\n\n主角必须在债务归零前找出时间异常。\n".encode("utf-8"),
            "story-bible/cast.md": "# 人物档案\n\n## 林某D\n\n她坚持记录每一次代价。\n".encode("utf-8"),
            "story-bible/world.md": "# 世界规则\n\n每次停摆都会增加一分钟债务。\n".encode("utf-8"),
            "story-bible/style-guide.md": "# 叙事声音\n\n近距离第三人称，克制而紧迫。\n".encode("utf-8"),
            "outlines/master-outline.md": "# 总纲\n\n主角追查异常并承担不可逆代价。\n".encode("utf-8"),
            "memory/decisions.md": (
                "# 作者决策与追溯修改\n\n"
                "| ID | 记录时间 | 生效范围 | 决策或被替代事实 | 需要同步的文件 |\n"
                "|---|---|---|---|---|\n"
                "| D-001 | 2026-09-13 | 全书 | 作者确认时间债务规则 | world.md |\n"
            ).encode("utf-8"),
            "memory/book-summary.md": (
                "# 框架同步测试：全书压缩记忆\n\n"
                "## 故事承诺与核心冲突\n\n主角必须在债务归零前找出时间异常。\n"
            ).encode("utf-8"),
            "project-settings.json": (
                '{\n  "schema_version": 1,\n  "pov": "近距离第三人称",\n'
                '  "tense": "过去时",\n  "target_words": 180000\n}\n'
            ).encode("utf-8"),
        }
        for relative, content in self.source_files.items():
            write_bytes(self.source / relative, content)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def args(self, source: Path | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            root=str(self.root),
            source_root=str(source or self.source),
            stage=None,
            confirmation=None,
            requirements_confidence=None,
            story_confidence=None,
            authorization_reference="作者本轮确认框架并同步正典",
            workspace=str(self.workspace),
            work_id=self.work["work_id"],
        )

    def test_syncs_framework_package_and_refreshes_base(self) -> None:
        result = novel_project.framework_sync(self.args())
        self.assertEqual(result["status"], "synchronized")
        self.assertEqual(
            result["source_files"], list(novel_project.FRAMEWORK_SYNC_SOURCE_FILES)
        )
        for relative, expected in self.source_files.items():
            if relative == novel_project.FRAMEWORK_SETTINGS_FILE:
                continue
            actual = (self.root / relative).read_bytes()
            if relative == "planning/framework-session.md":
                self.assertIn(b"state_authorization: ", actual)
                self.assertIn(b"stage: complete", actual)
            else:
                self.assertEqual(actual, expected)
        manifest = json.loads((self.root / "novel.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "drafting")
        self.assertEqual(manifest["pov"], "近距离第三人称")
        self.assertEqual(manifest["tense"], "过去时")
        self.assertEqual(manifest["target_words"], 180000)
        self.assertEqual(manifest["current_chapter"], 0)
        metadata = novel_project.frontmatter_metadata(
            self.root / "planning/framework-session.md"
        )
        self.assertEqual(metadata["confirmation"], "confirmed")
        self.assertEqual(metadata["requirements_confidence"], "97")
        self.assertEqual(
            novel_workspace.write_check(self.workspace, self.work["work_id"])[
                "status"
            ],
            "pass",
        )
        head = json.loads((self.root / "continuity/head.json").read_text(encoding="utf-8"))
        self.assertEqual(head["status"], "current")

    def test_sync_rolls_back_all_targets_on_interrupt(self) -> None:
        before = {
            relative: (self.root / relative).read_bytes()
            for relative in novel_project.FRAMEWORK_SYNC_FILES
        }
        before["novel.json"] = (self.root / "novel.json").read_bytes()
        real_replace = novel_project.os.replace
        calls = 0

        def interrupt_second(source: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            real_replace(source, target)

        with mock.patch.object(novel_project.os, "replace", side_effect=interrupt_second):
            with self.assertRaises(novel_project.ProjectError) as raised:
                novel_project.framework_sync(self.args())
        cause = raised.exception
        while cause.__cause__ is not None:
            cause = cause.__cause__
        self.assertIsInstance(cause, KeyboardInterrupt)

        for relative, expected in before.items():
            self.assertEqual((self.root / relative).read_bytes(), expected)
        self.assertFalse((self.root / novel_project.TRANSACTION_DIRNAME).exists())

    def test_source_must_be_outside_project_tree(self) -> None:
        with self.assertRaisesRegex(novel_project.ProjectError, "outside the project tree"):
            novel_project.framework_sync(self.args(self.root / "staging"))

    def test_source_must_belong_to_active_work(self) -> None:
        outside = self.base / "other-work" / "framework-sync"
        for relative, content in self.source_files.items():
            write_bytes(outside / relative, content)
        with self.assertRaisesRegex(novel_project.ProjectError, "active work directory"):
            novel_project.framework_sync(self.args(outside))

    def test_project_settings_cannot_replace_protected_manifest_fields(self) -> None:
        settings = json.loads(
            (self.source / novel_project.FRAMEWORK_SETTINGS_FILE).read_text(
                encoding="utf-8"
            )
        )
        settings["current_chapter"] = 99
        write_bytes(
            self.source / novel_project.FRAMEWORK_SETTINGS_FILE,
            (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        with self.assertRaisesRegex(novel_project.ProjectError, "protected or unknown"):
            novel_project.framework_sync(self.args())
        manifest = json.loads((self.root / "novel.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["current_chapter"], 0)

    def test_existing_chapter_requires_invalidation_and_rebaseline(self) -> None:
        novel_project.framework_sync(self.args())
        chapter = self.root / "manuscript/chapters/0001-基线章.md"
        write_bytes(chapter, "# 第一章 基线章\n\n林某D记录了第一笔时间债务。\n".encode("utf-8"))
        manifest_path = self.root / "novel.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({"current_chapter": 1, "status": "drafting"})
        write_bytes(
            manifest_path,
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        state_path = self.root / "continuity/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["through_chapter"] = 1
        write_bytes(
            state_path,
            (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        index_path = self.root / "manuscript/index.md"
        index_text = index_path.read_text(encoding="utf-8").rstrip()
        index_text += (
            "\n| 0001 | 基线章 | 林某D | 第一天 | 档案室 | 林某D记录时间债务 | "
            "债务开始累积 | T-001 | [正文](chapters/0001-基线章.md) |\n"
        )
        write_bytes(index_path, index_text.encode("utf-8"))
        seal_full_baseline(
            self.root,
            workspace=self.workspace,
            work_id=self.work["work_id"],
        )

        world_path = self.source / "story-bible/world.md"
        write_bytes(
            world_path,
            "# 世界规则\n\n每次停摆会增加两分钟债务，作者已确认追溯生效。\n".encode("utf-8"),
        )
        with self.assertRaisesRegex(novel_project.ProjectError, "invalidation"):
            novel_project.framework_sync(self.args())

        novel_continuity.invalidate_command(
            SimpleNamespace(
                root=str(self.root),
                changed_path=["memory/book-summary.md"],
                change_type="local_fact",
                reason="测试不相关的开放失效项不能授权世界规则变化",
                authorization_reference="测试记录不相关失效项",
                workspace=str(self.workspace),
                work_id=self.work["work_id"],
            )
        )
        with self.assertRaisesRegex(novel_project.ProjectError, "missing changed_paths"):
            novel_project.framework_sync(self.args())

        impact = novel_continuity.dependency_impact(
            self.root, ["story-bible/world.md"], "world_rule"
        )
        self.assertEqual(impact["affected_chapters"], [1])
        novel_continuity.invalidate_command(
            SimpleNamespace(
                root=str(self.root),
                changed_path=["story-bible/world.md"],
                change_type="world_rule",
                reason="作者确认追溯修改时间债务规则",
                authorization_reference="作者本轮确认规则修改",
                workspace=str(self.workspace),
                work_id=self.work["work_id"],
            )
        )
        result = novel_project.framework_sync(self.args())
        self.assertIsNone(result["continuity"])
        stale = novel_continuity.continuity_status(self.root)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["open_invalidations"], 2)

        novel_workspace.refresh_base(
            self.workspace,
            self.work["work_id"],
            "框架事务后复核工作基准",
        )
        still_stale = novel_continuity.continuity_status(self.root)
        self.assertEqual(still_stale["status"], "stale")
        self.assertEqual(still_stale["open_invalidations"], 2)

        seal_full_baseline(
            self.root,
            workspace=self.workspace,
            work_id=self.work["work_id"],
        )
        current = novel_continuity.continuity_status(self.root)
        self.assertEqual(current["status"], "current")
        self.assertEqual(current["open_invalidations"], 0)
        repeated = novel_project.framework_sync(self.args())
        self.assertEqual(repeated["status"], "already_current")
        self.assertEqual(repeated["synced_files"], [])
        self.assertEqual(novel_continuity.continuity_status(self.root)["status"], "current")


if __name__ == "__main__":
    unittest.main()
