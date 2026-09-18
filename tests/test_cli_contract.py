from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_cli  # noqa: E402
import novel_project  # noqa: E402
import novel_workspace  # noqa: E402


class CliContractTests(unittest.TestCase):
    def run_script(self, script: str, *arguments: str, env: dict[str, str] | None = None):
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / script), *arguments],
            capture_output=True,
            env=process_env,
        )

    def test_atomic_write_text_uses_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "text.txt"
            novel_cli.atomic_write_text(target, "中文内容\n")
            self.assertEqual(target.read_bytes(), "中文内容\n".encode("utf-8"))

    def test_atomic_write_bytes_preserves_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "raw.bin"
            content = b"\xff\xfe\x00raw\x80"
            novel_cli.atomic_write_bytes(target, content)
            self.assertEqual(target.read_bytes(), content)

    def test_atomic_write_replacement_leaves_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "replace.bin"
            target.write_bytes(b"old")
            novel_cli.atomic_write_bytes(target, b"new")
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(
                [path for path in root.iterdir() if path.suffix == ".tmp"],
                [],
            )

    def test_atomic_create_refuses_existing_target_without_changing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "new.bin"
            target.write_bytes(b"existing")

            with self.assertRaises(FileExistsError):
                novel_cli.atomic_create_bytes(target, b"replacement")

            self.assertEqual(target.read_bytes(), b"existing")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_atomic_create_interrupt_before_install_leaves_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "new.bin"

            with mock.patch.object(
                novel_cli.os, "fsync", side_effect=KeyboardInterrupt()
            ):
                with self.assertRaises(KeyboardInterrupt):
                    novel_cli.atomic_create_bytes(target, b"complete content")

            self.assertFalse(target.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_atomic_create_preserves_concurrent_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "new.bin"

            def concurrent_winner(_source: Path, destination: Path) -> None:
                Path(destination).write_bytes(b"winner")
                raise FileExistsError("injected concurrent creator")

            with mock.patch.object(
                novel_cli.os, "link", side_effect=concurrent_winner
            ):
                with self.assertRaises(FileExistsError):
                    novel_cli.atomic_create_bytes(target, b"ours")

            self.assertEqual(target.read_bytes(), b"winner")
            self.assertEqual(
                [path for path in root.iterdir() if path.suffix == ".tmp"],
                [],
            )

    def test_atomic_create_does_not_delete_concurrent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "new.bin"
            real_link = os.link

            def replace_then_interrupt(source: Path, destination: Path) -> None:
                real_link(source, destination)
                Path(destination).unlink()
                Path(destination).write_bytes(b"concurrent")
                raise SystemExit("injected interrupt after install")

            with mock.patch.object(
                novel_cli.os, "link", side_effect=replace_then_interrupt
            ):
                with self.assertRaises(SystemExit):
                    novel_cli.atomic_create_bytes(target, b"ours")

            self.assertEqual(target.read_bytes(), b"concurrent")
            self.assertEqual(
                [path for path in root.iterdir() if path.suffix == ".tmp"],
                [],
            )

    def test_every_tool_exposes_json_version_and_usage_errors(self) -> None:
        scripts = (
            "novel_cli.py",
            "novel_continuity.py",
            "novel_export.py",
            "novel_memory.py",
            "novel_originality.py",
            "novel_project.py",
            "novel_research.py",
            "novel_review.py",
            "novel_workspace.py",
        )
        # novel_cli.py is a shared module rather than an executable command, so
        # only assert its importability here.
        self.assertEqual(self.run_script("novel_cli.py").returncode, 0)
        for script in scripts[1:]:
            version = self.run_script(script, "--version")
            self.assertEqual(version.returncode, 0, version.stderr.decode())
            payload = json.loads(version.stdout.decode("utf-8"))
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["version"], novel_cli.TOOL_VERSION)

            invalid = self.run_script(script, "not-a-command")
            self.assertEqual(invalid.returncode, 2)
            error = json.loads(invalid.stdout.decode("utf-8"))
            self.assertEqual(error["status"], "error")
            self.assertEqual(error["error_type"], "usage")
            self.assertEqual(invalid.stderr, b"")

    def test_cp936_environment_still_receives_utf8_json(self) -> None:
        result = self.run_script(
            "novel_workspace.py", "doctor", env={"PYTHONIOENCODING": "cp936"}
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(payload["status"], "pass")
        self.assertTrue(any("humanizer-zh" == item["name"] for item in payload["checks"]))

    def test_unexpected_exception_is_structured_and_returns_three(self) -> None:
        parser = novel_cli.JsonArgumentParser()
        parser.add_argument("--version", action="store_true")
        output = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["probe"]
            with contextlib.redirect_stdout(output):
                code = novel_cli.run_cli(
                    lambda: parser,
                    lambda _args: (_ for _ in ()).throw(KeyError("broken")),
                    tool_name="probe",
                )
        finally:
            sys.argv = old_argv
        self.assertEqual(code, 3)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error_type"], "KeyError")
        self.assertFalse(payload["recoverable"])

    def test_result_serialization_failure_is_structured_and_returns_three(self) -> None:
        parser = novel_cli.JsonArgumentParser()
        parser.add_argument("--version", action="store_true")
        output = io.StringIO()
        old_argv = sys.argv
        try:
            sys.argv = ["probe"]
            with contextlib.redirect_stdout(output):
                code = novel_cli.run_cli(
                    lambda: parser,
                    lambda _args: {"not_json": object()},
                    tool_name="probe",
                )
        finally:
            sys.argv = old_argv
        self.assertEqual(code, 3)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error_type"], "SerializationError")
        self.assertFalse(payload["recoverable"])

    def test_domain_and_blocked_cli_results_have_stable_exit_codes(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "novel-studio-missing-project")
        cases = (
            ("novel_workspace.py", ("status", missing), 2, "error"),
            ("novel_continuity.py", ("status", missing), 2, "error"),
            ("novel_export.py", ("status", missing), 2, "error"),
            ("novel_memory.py", ("status", missing), 2, "error"),
            (
                "novel_originality.py",
                ("audit", missing, "--candidate", missing),
                2,
                "error",
            ),
            ("novel_project.py", ("validate", missing), 1, "invalid"),
            ("novel_research.py", ("verify", missing), 2, "error"),
            ("novel_review.py", ("status", missing), 2, "error"),
        )
        for script, arguments, expected_code, expected_status in cases:
            with self.subTest(script=script):
                result = self.run_script(script, *arguments)
                self.assertEqual(
                    result.returncode,
                    expected_code,
                    result.stderr.decode(errors="replace"),
                )
                payload = json.loads(result.stdout.decode("utf-8"))
                self.assertEqual(payload["status"], expected_status)
                if expected_status == "error":
                    self.assertNotEqual(payload.get("error_type"), "usage")
                self.assertEqual(result.stderr, b"")

        with tempfile.TemporaryDirectory() as temp:
            fake_humanizer = Path(temp) / "SKILL.md"
            fake_humanizer.write_text("invalid", encoding="utf-8")
            blocked = self.run_script(
                "novel_workspace.py",
                "doctor",
                env={"NOVEL_HUMANIZER_PATH": str(fake_humanizer)},
            )
            self.assertEqual(blocked.returncode, 1)
            blocked_payload = json.loads(blocked.stdout.decode("utf-8"))
            self.assertEqual(blocked_payload["status"], "blocked")
            self.assertEqual(blocked.stderr, b"")

    def test_incomplete_originality_audit_is_a_business_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            initialized = self.run_script(
                "novel_project.py", "init", str(root), "--title", "原创性契约测试"
            )
            self.assertEqual(initialized.returncode, 0)

            result = self.run_script(
                "novel_originality.py", "audit", str(root), "--no-report"
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(payload["decision"], "incomplete")
            self.assertNotIn("recoverable", payload)
            self.assertEqual(result.stderr, b"")

    def test_workspace_bind_close_and_status_cli_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"

            def run(*arguments: str) -> dict:
                result = self.run_script(
                    "novel_workspace.py", *arguments
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                self.assertEqual(result.stderr, b"")
                return json.loads(result.stdout.decode("utf-8"))

            run("init", str(workspace))
            project = run(
                "project-create",
                str(workspace),
                "--title",
                "CLI 状态测试",
                "--project-id",
                "novel-cli-status",
            )
            work = run("work-start", str(workspace), "--purpose", "CLI 绑定关闭")
            bound = run(
                "work-bind",
                str(workspace),
                work["work_id"],
                "--project-id",
                project["project_id"],
            )
            self.assertEqual(bound["status"], "bound")
            status = run("status", str(workspace))
            self.assertEqual(status["active_works"], 1)
            run("lock-acquire", str(workspace), work["work_id"])
            run("lock-release", str(workspace), work["work_id"])
            closed = run("work-close", str(workspace), work["work_id"])
            self.assertEqual(closed["status"], "closed")
            final = run("status", str(workspace))
            self.assertEqual(final["active_works"], 0)
            self.assertEqual(final["closed_works"], 1)


class LeaseAndDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)
        created = novel_workspace.create_project(
            self.workspace, title="租约测试", project_id="novel-lease"
        )
        self.project_root = Path(created["project_root"])
        work = novel_workspace.create_work(
            self.workspace, project_id="novel-lease", purpose="租约测试"
        )
        self.work_id = work["work_id"]
        novel_workspace.acquire_lock(
            self.workspace, self.work_id, lease_seconds=300
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_explicit_renew_updates_heartbeat_and_status(self) -> None:
        before = novel_workspace.workspace_status(self.workspace)["leases"][0]
        renewed = novel_workspace.renew_lock(self.workspace, self.work_id)
        self.assertEqual(renewed["status"], "renewed")
        after = novel_workspace.workspace_status(self.workspace)["leases"][0]
        self.assertEqual(after["lease_status"]["work_id"], self.work_id)
        self.assertGreaterEqual(
            after["lease_status"]["heartbeat_age_seconds"], 0
        )
        self.assertGreaterEqual(
            after["lease_status"]["expires_in_seconds"],
            before["lease_status"]["expires_in_seconds"] - 1,
        )

    def test_stale_heartbeat_is_not_live_and_break_is_audited(self) -> None:
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            connection.execute(
                "UPDATE leases SET heartbeat_at = ?, expires_at = ? WHERE work_id = ?",
                (
                    "2000-01-01T00:00:00+00:00",
                    "2999-01-01T00:00:00+00:00",
                    self.work_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(novel_workspace.WorkspaceError, "heartbeat"):
            novel_workspace.write_check(self.workspace, self.work_id)
        broken = novel_workspace.break_lock(
            self.workspace,
            "novel-lease",
            expected_owner=self.work_id,
            reason="测试进程已崩溃，确认心跳过期",
        )
        self.assertEqual(broken["status"], "broken")
        self.assertEqual(broken["previous_owner"], self.work_id)
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            event = connection.execute(
                "SELECT event, reason FROM lease_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(event, ("broken", "测试进程已崩溃，确认心跳过期"))

    def test_doctor_with_workspace_is_read_only(self) -> None:
        before = {
            path.relative_to(self.workspace).as_posix(): path.read_bytes()
            for path in self.workspace.rglob("*")
            if path.is_file()
        }
        result = novel_workspace.doctor(self.workspace)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["read_only"])
        after = {
            path.relative_to(self.workspace).as_posix(): path.read_bytes()
            for path in self.workspace.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_doctor_accepts_special_uri_path_and_rejects_fake_humanizer_file(self) -> None:
        special_workspace = self.base / "workspace#20%2Fsafe"
        novel_workspace.initialize_workspace(special_workspace)
        result = novel_workspace.doctor(special_workspace)
        self.assertEqual(result["status"], "pass")

        fake = self.base / "humanizer.txt"
        fake.write_text("not a skill", encoding="utf-8")
        old_value = os.environ.get("NOVEL_HUMANIZER_PATH")
        os.environ["NOVEL_HUMANIZER_PATH"] = str(fake)
        try:
            check = next(item for item in novel_workspace.doctor()["checks"] if item["name"] == "humanizer-zh")
            self.assertEqual(check["status"], "fail")
            self.assertTrue(check["formal_work_blocked"])
        finally:
            if old_value is None:
                os.environ.pop("NOVEL_HUMANIZER_PATH", None)
            else:
                os.environ["NOVEL_HUMANIZER_PATH"] = old_value

    def test_doctor_rejects_unreadable_or_malformed_humanizer_skill(self) -> None:
        malformed = self.base / "humanizer-malformed"
        malformed.mkdir()
        (malformed / "SKILL.md").write_text(
            "这不是带有 skill frontmatter 的文件。", encoding="utf-8"
        )
        old_value = os.environ.get("NOVEL_HUMANIZER_PATH")
        os.environ["NOVEL_HUMANIZER_PATH"] = str(malformed)
        try:
            check = next(
                item
                for item in novel_workspace.doctor()["checks"]
                if item["name"] == "humanizer-zh"
            )
            self.assertEqual(check["status"], "fail")
            self.assertIn("frontmatter", check["detail"])
            self.assertTrue(check["formal_work_blocked"])
        finally:
            if old_value is None:
                os.environ.pop("NOVEL_HUMANIZER_PATH", None)
            else:
                os.environ["NOVEL_HUMANIZER_PATH"] = old_value

    def test_humanizer_resolution_prefers_bundled_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            scripts_root = base / "novel-studio" / "scripts"
            scripts_root.mkdir(parents=True)
            bundled = base / "novel-studio" / "humanizer-zh"
            bundled.mkdir()
            (bundled / "SKILL.md").write_text(
                "---\nname: humanizer-zh\n---\n内置副本。", encoding="utf-8"
            )
            stale_sibling = base / "humanizer-zh"
            stale_sibling.mkdir()
            (stale_sibling / "SKILL.md").write_text(
                "没有 frontmatter 的旧副本。", encoding="utf-8"
            )

            old_value = os.environ.get("NOVEL_HUMANIZER_PATH")
            os.environ.pop("NOVEL_HUMANIZER_PATH", None)
            try:
                candidates = novel_workspace._humanizer_candidates(scripts_root)
                self.assertEqual(candidates[0], bundled)
                self.assertEqual(candidates[1], stale_sibling)
                _, valid, detail = novel_workspace._check_humanizer_candidate(
                    candidates[0]
                )
                self.assertTrue(valid, detail)
                resolved_file, resolved_valid, _ = (
                    novel_workspace.resolve_humanizer_skill(scripts_root)
                )
                self.assertTrue(resolved_valid)
                self.assertEqual(resolved_file, bundled / "SKILL.md")
            finally:
                if old_value is None:
                    os.environ.pop("NOVEL_HUMANIZER_PATH", None)
                else:
                    os.environ["NOVEL_HUMANIZER_PATH"] = old_value

    def test_unknown_registry_schema_fails_closed_without_overwrite(self) -> None:
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            connection.execute(
                "UPDATE metadata SET value = '99' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(novel_workspace.WorkspaceError, "unsupported"):
            novel_workspace.workspace_status(self.workspace)
        doctor = novel_workspace.doctor(self.workspace)
        self.assertEqual(doctor["status"], "blocked")
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()[0],
                "99",
            )
        finally:
            connection.close()

    def test_unknown_registry_schema_does_not_leave_database_handle_open(self) -> None:
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            connection.execute(
                "UPDATE metadata SET value = '99' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(novel_workspace.WorkspaceError):
            novel_workspace.workspace_status(self.workspace)

        # Windows refuses to remove an open SQLite file.  A successful rename
        # therefore verifies the failure path closed its connection.
        moved = self.base / "future-registry.sqlite3"
        (self.workspace / "registry.sqlite3").replace(moved)
        self.assertTrue(moved.is_file())

    def test_missing_registry_schema_marker_fails_closed_without_adoption(self) -> None:
        registry = self.workspace / "registry.sqlite3"
        connection = sqlite3.connect(registry)
        try:
            connection.execute(
                "DELETE FROM metadata WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(novel_workspace.WorkspaceError, "missing schema_version"):
            novel_workspace.workspace_status(self.workspace)
        doctor = novel_workspace.doctor(self.workspace)
        self.assertEqual(doctor["status"], "blocked")
        connection = sqlite3.connect(registry)
        try:
            self.assertIsNone(
                connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_doctor_reports_legacy_schema_without_mutating_until_writable_migration(self) -> None:
        registry = self.workspace / "registry.sqlite3"
        connection = sqlite3.connect(registry)
        try:
            connection.execute(
                "UPDATE metadata SET value = '0' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()

        doctor = novel_workspace.doctor(self.workspace)
        self.assertEqual(doctor["status"], "blocked")
        workspace_check = next(
            item for item in doctor["checks"] if item["name"] == "workspace"
        )
        self.assertIn("writable migration", workspace_check["detail"])
        connection = sqlite3.connect(registry)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()[0],
                "0",
            )
        finally:
            connection.close()

        status = novel_workspace.workspace_status(self.workspace)
        self.assertEqual(status["status"], "ok")
        self.assertFalse(status["leases"][0]["lease_status"]["heartbeat_enforced"])
        connection = sqlite3.connect(registry)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()[0],
                "1",
            )
        finally:
            connection.close()

    def test_write_guard_holds_sqlite_reservation_until_file_commit_finishes(self) -> None:
        second = novel_workspace.create_work(
            self.workspace, project_id="novel-lease", purpose="第二写者"
        )
        entered = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []

        def holder() -> None:
            try:
                with novel_workspace.write_guard(
                    self.workspace,
                    self.work_id,
                    expected_project_root=self.project_root,
                ):
                    entered.set()
                    release.wait(5)
            except Exception as exc:  # pragma: no cover - assertion below
                errors.append(exc)

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(entered.wait(3))
        acquired: list[object] = []

        def contender() -> None:
            try:
                acquired.append(novel_workspace.acquire_lock(self.workspace, second["work_id"]))
            except Exception as exc:
                acquired.append(exc)

        contender_thread = threading.Thread(target=contender)
        contender_thread.start()
        time.sleep(0.25)
        self.assertTrue(contender_thread.is_alive())
        release.set()
        thread.join(5)
        contender_thread.join(6)
        self.assertFalse(errors)
        self.assertEqual(len(acquired), 1)
        self.assertIsInstance(acquired[0], novel_workspace.WorkspaceError)

    def test_legacy_registry_lease_keeps_expiry_only_semantics_until_renewed(self) -> None:
        registry = self.workspace / "registry.sqlite3"
        source = sqlite3.connect(registry)
        project_row = source.execute("SELECT * FROM projects").fetchone()
        work_row = source.execute("SELECT * FROM works").fetchone()
        source.close()
        legacy = self.base / "legacy.sqlite3"
        connection = sqlite3.connect(legacy)
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                project_root TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE works (
                work_id TEXT PRIMARY KEY, work_root TEXT NOT NULL UNIQUE,
                project_id TEXT REFERENCES projects(project_id), purpose TEXT NOT NULL,
                client TEXT NOT NULL, status TEXT NOT NULL, base_state_hash TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE leases (
                project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
                work_id TEXT NOT NULL REFERENCES works(work_id),
                acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        connection.execute("INSERT INTO metadata VALUES('schema_version', '1')")
        connection.execute("INSERT INTO projects VALUES(?, ?, ?, ?, ?, ?)", project_row)
        connection.execute("INSERT INTO works VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)", work_row)
        connection.execute(
            "INSERT INTO leases VALUES(?, ?, ?, ?, ?)",
            (
                project_row[0],
                work_row[0],
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                "2999-01-01T00:00:00+00:00",
            ),
        )
        connection.commit()
        connection.close()
        for suffix in ("", "-wal", "-shm"):
            (self.workspace / f"registry.sqlite3{suffix}").unlink(missing_ok=True)
        legacy.replace(registry)

        status = novel_workspace.workspace_status(self.workspace)
        lease_status = status["leases"][0]["lease_status"]
        self.assertTrue(lease_status["live"])
        self.assertFalse(lease_status["heartbeat_enforced"])
        renewed = novel_workspace.renew_lock(self.workspace, self.work_id)
        self.assertEqual(renewed["status"], "renewed")
        self.assertTrue(
            novel_workspace.workspace_status(self.workspace)["leases"][0]["lease_status"][
                "heartbeat_enforced"
            ]
        )

    def test_partial_lease_migration_keeps_existing_rows_on_expiry_only_semantics(self) -> None:
        registry = self.workspace / "registry.sqlite3"
        connection = sqlite3.connect(registry)
        try:
            connection.execute("ALTER TABLE leases RENAME TO leases_before_partial_migration")
            connection.execute(
                """
                CREATE TABLE leases (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
                    work_id TEXT NOT NULL REFERENCES works(work_id),
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    lease_seconds INTEGER NOT NULL DEFAULT 1800
                )
                """
            )
            connection.execute(
                """
                INSERT INTO leases(
                    project_id, work_id, acquired_at, heartbeat_at, expires_at,
                    lease_seconds
                )
                SELECT project_id, work_id, acquired_at, heartbeat_at, expires_at,
                       lease_seconds
                FROM leases_before_partial_migration
                """
            )
            connection.execute("DROP TABLE leases_before_partial_migration")
            connection.commit()
        finally:
            connection.close()

        status = novel_workspace.workspace_status(self.workspace)
        lease_status = status["leases"][0]["lease_status"]
        self.assertFalse(lease_status["heartbeat_enforced"])
        inspect_connection = sqlite3.connect(registry)
        try:
            columns = {
                row[1]
                for row in inspect_connection.execute("PRAGMA table_info(leases)")
            }
        finally:
            inspect_connection.close()
        self.assertIn("heartbeat_enforced", columns)

    def test_doctor_reads_migrated_lease_events_from_an_active_wal(self) -> None:
        registry = self.workspace / "registry.sqlite3"
        connection = sqlite3.connect(registry)
        try:
            connection.execute("DROP TABLE lease_events")
            connection.commit()
        finally:
            connection.close()

        migration_connection = novel_workspace.open_registry(
            self.workspace, synchronize=False
        )
        try:
            # Pin a read snapshot so the following renewal remains in an
            # active WAL instead of being immediately checkpointed away.
            migration_connection.execute("BEGIN")
            table = migration_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'lease_events'"
            ).fetchone()
            self.assertEqual(table[0], "lease_events")

            renewed = novel_workspace.acquire_lock(self.workspace, self.work_id)
            self.assertEqual(renewed["status"], "renewed")
            wal_path = Path(f"{registry}-wal")
            self.assertTrue(wal_path.is_file())
            self.assertGreater(wal_path.stat().st_size, 0)

            read_only = novel_workspace.open_read_only_registry(registry)
            try:
                event_count = read_only.execute(
                    "SELECT COUNT(*) FROM lease_events"
                ).fetchone()[0]
                self.assertGreaterEqual(event_count, 1)
                required = {
                    row[0]
                    for row in read_only.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {"metadata", "projects", "works", "leases", "lease_events"}
                    <= required
                )
            finally:
                read_only.close()

            doctor = novel_workspace.doctor(self.workspace)
            self.assertEqual(doctor["status"], "pass")
            workspace_check = next(
                item for item in doctor["checks"] if item["name"] == "workspace"
            )
            self.assertEqual(workspace_check["status"], "pass")
        finally:
            if migration_connection.in_transaction:
                migration_connection.rollback()
            migration_connection.close()

    def test_doctor_rejects_registry_with_missing_required_columns(self) -> None:
        registry = self.workspace / "registry.sqlite3"
        connection = sqlite3.connect(registry)
        try:
            connection.execute("ALTER TABLE leases RENAME TO leases_before_column_check")
            connection.execute(
                """
                CREATE TABLE leases (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
                    work_id TEXT NOT NULL REFERENCES works(work_id),
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        doctor = novel_workspace.doctor(self.workspace)
        self.assertEqual(doctor["status"], "blocked")
        workspace_check = next(
            item for item in doctor["checks"] if item["name"] == "workspace"
        )
        self.assertEqual(workspace_check["status"], "fail")
        self.assertIn("heartbeat_enforced", workspace_check["detail"])

    def test_release_records_hash_failure_and_still_removes_lease(self) -> None:
        (self.project_root / "novel.json").unlink()
        released = novel_workspace.release_lock(self.workspace, self.work_id)
        self.assertEqual(released["status"], "released")
        self.assertIsNone(released["current_state_hash"])
        self.assertTrue(released["state_hash_error"])
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            event = connection.execute(
                "SELECT event, current_state_hash, state_hash_error "
                "FROM lease_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(event[0], "released")
        self.assertIsNone(event[1])
        self.assertTrue(event[2])

    def test_same_owner_acquire_and_reclaim_write_audited_hashes(self) -> None:
        renewed = novel_workspace.acquire_lock(self.workspace, self.work_id)
        self.assertEqual(renewed["status"], "renewed")
        second = novel_workspace.create_work(
            self.workspace, project_id="novel-lease", purpose="回收者"
        )
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            connection.execute(
                "UPDATE leases SET expires_at = ?, heartbeat_at = ? WHERE project_id = ?",
                (
                    "2000-01-01T00:00:00+00:00",
                    "2000-01-01T00:00:00+00:00",
                    "novel-lease",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        reclaimed = novel_workspace.acquire_lock(self.workspace, second["work_id"])
        self.assertEqual(reclaimed["status"], "reclaimed")
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            events = connection.execute(
                "SELECT event, current_state_hash, state_hash_error "
                "FROM lease_events ORDER BY event_id"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([row[0] for row in events[-2:]], ["renewed", "reclaimed"])
        self.assertTrue(events[-2][1])
        self.assertIsNone(events[-2][2])
        self.assertTrue(events[-1][1])
        self.assertIsNone(events[-1][2])

    def test_commit_function_rejects_missing_write_identity(self) -> None:
        with self.assertRaisesRegex(novel_project.ProjectError, "--workspace and --work-id"):
            novel_project.commit_chapter(
                SimpleNamespace(root=str(self.project_root), package="staging/chapters/noop")
            )


if __name__ == "__main__":
    unittest.main()
