from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_workspace  # noqa: E402


class WorkspaceIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "novel-workspace"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_project(self, project_id: str = "novel-test") -> dict:
        novel_workspace.initialize_workspace(self.workspace)
        return novel_workspace.create_project(
            self.workspace,
            title="测试长篇",
            genre="悬疑",
            project_id=project_id,
        )

    def test_ensure_creates_workspace_first_and_reuses_nested_context(self) -> None:
        first = novel_workspace.ensure_work(
            self.workspace,
            context=self.base / "outside",
            purpose="互动策划",
            client="unit-test",
        )
        self.assertEqual(first["status"], "created")
        work_root = Path(first["work_root"])
        self.assertTrue((self.workspace / "workspace.json").is_file())
        self.assertTrue((self.workspace / "registry.sqlite3").is_file())
        self.assertTrue((work_root / "work.json").is_file())
        for relative in novel_workspace.WORK_SUBDIRECTORIES:
            self.assertTrue((work_root / relative).is_dir())

        second = novel_workspace.ensure_work(
            self.workspace,
            context=work_root / "drafts",
            client="another-client",
        )
        self.assertEqual(second["status"], "reused")
        self.assertEqual(second["work_id"], first["work_id"])
        self.assertEqual(
            novel_workspace.work_list(self.workspace)["count"],
            1,
        )

    def test_new_project_is_separate_from_work_and_can_be_bound_later(self) -> None:
        novel_workspace.initialize_workspace(self.workspace)
        work = novel_workspace.create_work(
            self.workspace, purpose="先访谈后决定是否建书"
        )
        self.assertIsNone(work["project_id"])
        project = novel_workspace.create_project(
            self.workspace,
            title="后来决定的新书",
            project_id="novel-later",
        )
        bound = novel_workspace.bind_work(
            self.workspace, work["work_id"], project["project_id"]
        )
        self.assertEqual(bound["project_id"], "novel-later")
        self.assertEqual(
            Path(bound["project_root"]).parent,
            self.workspace / "projects",
        )
        context = json.loads(
            (Path(work["work_root"]) / "work.json").read_text(encoding="utf-8")
        )
        self.assertEqual(context["project_id"], "novel-later")
        self.assertTrue(context["base_state_hash"])

    def test_short_story_project_id_uses_slug_date_and_collision_suffix(self) -> None:
        novel_workspace.initialize_workspace(self.workspace)
        first = novel_workspace.create_project(
            self.workspace,
            title="请替死者签收",
            genre="现实悬疑",
            work_type="short_story",
            short_story_slug="receipt",
            project_date="20260830",
        )
        second = novel_workspace.create_project(
            self.workspace,
            title="请替死者再次签收",
            genre="现实悬疑",
            work_type="short_story",
            short_story_slug="receipt",
            project_date="20260830",
        )

        self.assertEqual(first["project_id"], "shortstory-receipt-20260830")
        self.assertEqual(second["project_id"], "shortstory-receipt-20260830-2")
        self.assertEqual(Path(first["project_root"]).name, first["project_id"])
        self.assertEqual(Path(second["project_root"]).name, second["project_id"])

    def test_short_story_generated_id_requires_semantic_slug(self) -> None:
        novel_workspace.initialize_workspace(self.workspace)
        with self.assertRaisesRegex(
            novel_workspace.WorkspaceError, "short_story_slug"
        ):
            novel_workspace.create_project(
                self.workspace,
                title="没有英文标识的短故事",
                work_type="short_story",
                project_date="20260830",
            )

        with self.assertRaisesRegex(
            novel_workspace.WorkspaceError, "one to three"
        ):
            novel_workspace.create_project(
                self.workspace,
                title="标识词数过多",
                work_type="short_story",
                short_story_slug="one-two-three-four",
                project_date="20260830",
            )

    def test_concurrent_short_story_ids_do_not_collide(self) -> None:
        novel_workspace.initialize_workspace(self.workspace)

        def create(title: str) -> dict:
            return novel_workspace.create_project(
                self.workspace,
                title=title,
                genre="现实悬疑",
                work_type="short_story",
                short_story_slug="same-night",
                project_date="20260830",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            projects = list(executor.map(create, ("同夜甲", "同夜乙")))

        project_ids = {project["project_id"] for project in projects}
        self.assertEqual(
            project_ids,
            {
                "shortstory-same-night-20260830",
                "shortstory-same-night-20260830-2",
            },
        )
        for project in projects:
            self.assertTrue(Path(project["project_root"]).is_dir())

    def test_long_project_keeps_generated_novel_id(self) -> None:
        novel_workspace.initialize_workspace(self.workspace)
        created = novel_workspace.create_project(
            self.workspace,
            title="仍按旧规则创建的长篇",
        )
        self.assertRegex(created["project_id"], r"^novel-[a-f0-9]{8}$")

    def test_short_story_slug_cannot_silently_create_a_long_project(self) -> None:
        novel_workspace.initialize_workspace(self.workspace)
        with self.assertRaisesRegex(
            novel_workspace.WorkspaceError, "only valid for short_story"
        ):
            novel_workspace.create_project(
                self.workspace,
                title="漏传作品类型",
                short_story_slug="wrong-mode",
            )

    def test_project_create_cli_accepts_slug_and_returns_allocated_ids(self) -> None:
        script = SCRIPTS / "novel_workspace.py"

        def run(*arguments: str) -> dict:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", str(script), *arguments],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            return json.loads(completed.stdout)

        run("init", str(self.workspace))
        first = run(
            "project-create",
            str(self.workspace),
            "--title",
            "请替死者签收",
            "--work-type",
            "short_story",
            "--short-story-slug",
            "receipt",
        )
        second = run(
            "project-create",
            str(self.workspace),
            "--title",
            "请替死者再次签收",
            "--work-type",
            "short_story",
            "--short-story-slug",
            "receipt",
        )
        date_stamp = datetime.now().astimezone().strftime("%Y%m%d")
        self.assertEqual(first["project_id"], f"shortstory-receipt-{date_stamp}")
        self.assertEqual(
            second["project_id"], f"shortstory-receipt-{date_stamp}-2"
        )

    def test_single_writer_lock_blocks_a_second_work(self) -> None:
        project = self.create_project()
        first = novel_workspace.create_work(
            self.workspace, project_id=project["project_id"], purpose="第一项工作"
        )
        second = novel_workspace.create_work(
            self.workspace, project_id=project["project_id"], purpose="第二项工作"
        )
        acquired = novel_workspace.acquire_lock(self.workspace, first["work_id"])
        self.assertEqual(acquired["status"], "acquired")
        self.assertTrue(acquired["state_matches"])
        self.assertEqual(
            novel_workspace.write_check(self.workspace, first["work_id"])["status"],
            "pass",
        )
        with self.assertRaises(novel_workspace.WorkspaceError):
            novel_workspace.acquire_lock(self.workspace, second["work_id"])
        novel_workspace.release_lock(self.workspace, first["work_id"])
        acquired_second = novel_workspace.acquire_lock(
            self.workspace, second["work_id"]
        )
        self.assertEqual(acquired_second["status"], "acquired")
        novel_workspace.release_lock(self.workspace, second["work_id"])

    def test_state_hash_detects_parallel_project_change(self) -> None:
        project = self.create_project()
        work = novel_workspace.create_work(
            self.workspace, project_id=project["project_id"]
        )
        novel_workspace.acquire_lock(self.workspace, work["work_id"])
        project_root = Path(project["project_root"])
        premise = project_root / "story-bible/premise.md"
        premise.write_text(
            premise.read_text(encoding="utf-8") + "\n外部工作改变了项目。\n",
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaises(novel_workspace.WorkspaceError):
            novel_workspace.write_check(self.workspace, work["work_id"])
        refreshed = novel_workspace.refresh_base(
            self.workspace,
            work["work_id"],
            "测试已回读外部变化并重新验证项目",
        )
        self.assertNotEqual(refreshed["base_state_hash"], work["base_state_hash"])
        self.assertEqual(
            novel_workspace.write_check(self.workspace, work["work_id"])["status"],
            "pass",
        )
        novel_workspace.release_lock(self.workspace, work["work_id"])

    def test_registry_rebuilds_project_and_work_rows_from_metadata(self) -> None:
        project = self.create_project()
        work = novel_workspace.create_work(
            self.workspace, project_id=project["project_id"]
        )
        for suffix in ("", "-wal", "-shm"):
            (self.workspace / f"registry.sqlite3{suffix}").unlink(missing_ok=True)
        status = novel_workspace.workspace_status(self.workspace)
        self.assertEqual(status["projects"], 1)
        self.assertEqual(status["active_works"], 1)
        resumed = novel_workspace.resume_work(self.workspace, work["work_id"])
        self.assertEqual(resumed["project_id"], project["project_id"])

    def test_repeated_registration_does_not_change_project_state(self) -> None:
        project = self.create_project()
        project_root = Path(project["project_root"])
        before_hash = novel_workspace.project_state_hash(project_root)
        before_metadata = (project_root / ".novel-project.json").read_bytes()
        repeated = novel_workspace.register_project(
            self.workspace,
            project_root,
            project_id=project["project_id"],
        )
        self.assertEqual(repeated["status"], "already_registered")
        self.assertEqual(novel_workspace.project_state_hash(project_root), before_hash)
        self.assertEqual(
            (project_root / ".novel-project.json").read_bytes(), before_metadata
        )

    def test_state_hash_ignores_rebuildable_exports(self) -> None:
        project = self.create_project()
        project_root = Path(project["project_root"])
        before_hash = novel_workspace.project_state_hash(project_root)
        export = project_root / "exports/《测试小说》-全书合并稿.txt"
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text("派生内容\n", encoding="utf-8", newline="\n")
        self.assertEqual(novel_workspace.project_state_hash(project_root), before_hash)

    def test_old_state_hash_with_staging_is_accepted_until_base_refresh(self) -> None:
        project = self.create_project()
        project_root = Path(project["project_root"])
        staging = project_root / "staging/chapters/0001/input.md"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text("暂存章节", encoding="utf-8", newline="\n")
        legacy_hash = novel_workspace.project_state_hash(
            project_root, include_staging=True
        )
        current_hash = novel_workspace.project_state_hash(project_root)
        self.assertNotEqual(legacy_hash, current_hash)
        work = novel_workspace.create_work(
            self.workspace, project_id=project["project_id"], purpose="旧上下文"
        )
        context_path = Path(work["work_root"]) / "work.json"
        context = json.loads(context_path.read_text(encoding="utf-8"))
        context["base_state_hash"] = legacy_hash
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            connection.execute(
                "UPDATE works SET base_state_hash = ? WHERE work_id = ?",
                (legacy_hash, work["work_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        novel_workspace.acquire_lock(self.workspace, work["work_id"])
        check = novel_workspace.write_check(self.workspace, work["work_id"])
        self.assertTrue(check["legacy_hash_accepted"])
        self.assertEqual(check["state_hash_version"], novel_workspace.LEGACY_STATE_HASH_VERSION)
        refreshed = novel_workspace.refresh_base(
            self.workspace, work["work_id"], "升级旧状态哈希并核对暂存边界"
        )
        self.assertEqual(refreshed["base_state_hash"], current_hash)
        self.assertFalse(
            novel_workspace.write_check(self.workspace, work["work_id"])[
                "legacy_hash_accepted"
            ]
        )
        novel_workspace.release_lock(self.workspace, work["work_id"])

    def test_work_json_cannot_overwrite_live_registry_state(self) -> None:
        project = self.create_project()
        work = novel_workspace.create_work(
            self.workspace, project_id=project["project_id"]
        )
        context_path = Path(work["work_root"]) / "work.json"
        context = json.loads(context_path.read_text(encoding="utf-8"))
        context["base_state_hash"] = "0" * 64
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        novel_workspace.workspace_status(self.workspace)
        with self.assertRaises(novel_workspace.WorkspaceError):
            novel_workspace.resume_work(self.workspace, work["work_id"])

    def test_close_work_requires_release_and_persists_closed_state(self) -> None:
        project = self.create_project("novel-close")
        work = novel_workspace.create_work(
            self.workspace, project_id=project["project_id"], purpose="关闭测试"
        )
        novel_workspace.acquire_lock(self.workspace, work["work_id"])
        with self.assertRaisesRegex(novel_workspace.WorkspaceError, "Release"):
            novel_workspace.close_work(self.workspace, work["work_id"])

        novel_workspace.release_lock(self.workspace, work["work_id"])
        closed = novel_workspace.close_work(self.workspace, work["work_id"])
        self.assertEqual(closed["status"], "closed")
        context = json.loads(
            (Path(work["work_root"]) / "work.json").read_text(encoding="utf-8")
        )
        self.assertEqual(context["status"], "closed")
        listed = novel_workspace.work_list(self.workspace)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["works"][0]["status"], "closed")
        status = novel_workspace.workspace_status(self.workspace)
        self.assertEqual(status["active_works"], 0)
        self.assertEqual(status["closed_works"], 1)


if __name__ == "__main__":
    unittest.main()
