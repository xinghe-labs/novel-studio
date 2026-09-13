from __future__ import annotations

import json
import inspect
import sqlite3
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_research  # noqa: E402
import novel_review  # noqa: E402
import novel_project  # noqa: E402
import novel_workspace  # noqa: E402


class ConcurrencyAuthorizationTests(unittest.TestCase):
    """Regression tests for project-write authorization and lost updates.

    These tests intentionally exercise public Python entry points rather than
    only the CLI.  A caller can import the modules directly, so authorization
    must be enforced at the function boundary as well as by argparse.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)
        project = novel_workspace.create_project(
            self.workspace,
            title="并发安全测试",
            genre="悬疑",
            project_id="novel-secure",
        )
        self.project_root = Path(project["project_root"])
        self._held_work_id: str | None = None

    def tearDown(self) -> None:
        if self._held_work_id is not None:
            try:
                novel_workspace.release_lock(self.workspace, self._held_work_id)
            except Exception:
                # The temporary workspace is about to be removed.  Cleanup
                # should not hide the assertion that caused the test to fail.
                pass
        self.temp_dir.cleanup()

    def hold_project_lease(self) -> dict:
        work = novel_workspace.create_work(
            self.workspace,
            project_id="novel-secure",
            purpose="安全不变量测试",
        )
        novel_workspace.acquire_lock(self.workspace, work["work_id"])
        self._held_work_id = work["work_id"]
        return work

    def test_base_refresh_cannot_accept_unverified_external_change(self) -> None:
        """An arbitrary prose reference must not wash an unexpected change."""

        work = self.hold_project_lease()
        premise = self.project_root / "story-bible" / "premise.md"
        premise.write_bytes(premise.read_bytes() + "\n外部未审计改动。\n".encode("utf-8"))

        before_context = json.loads(
            (Path(work["work_root"]) / "work.json").read_text(encoding="utf-8")
        )
        before_events = self._lease_event_count()

        # The current implementation accepts any non-empty string here.  The
        # fixed implementation must require machine-verifiable evidence or an
        # explicitly audited external-change acceptance operation.
        with self.assertRaises((RuntimeError, TypeError)) as raised:
            novel_workspace.refresh_base(
                self.workspace,
                work["work_id"],
                "我看过了，应该可以继续",
            )

        self.assertRegex(
            str(raised.exception).lower(),
            r"(lease|lock|base|hash|valid|audit|external|write)",
        )
        after_context = json.loads(
            (Path(work["work_root"]) / "work.json").read_text(encoding="utf-8")
        )
        self.assertEqual(after_context["base_state_hash"], before_context["base_state_hash"])
        self.assertEqual(self._registry_base_hash(work["work_id"]), before_context["base_state_hash"])
        with self.assertRaises(novel_workspace.WorkspaceError):
            novel_workspace.write_check(self.workspace, work["work_id"])
        # A rejected refresh must not claim that the external mutation was
        # accepted in the lease audit trail.
        self.assertEqual(self._lease_event_count(), before_events)

    def test_external_change_report_is_stable_and_strictly_typed(self) -> None:
        work = self.hold_project_lease()
        premise = self.project_root / "story-bible" / "premise.md"
        premise.write_bytes(premise.read_bytes() + b"\nEXTERNAL\n")
        changed_hash = novel_workspace.project_state_hash(self.project_root)
        report_path = self.base / "external-change-validation.json"
        report = {
            "schema_version": True,
            "project_root": str(self.project_root.resolve()),
            "previous_state_hash": work["base_state_hash"],
            "validated_state_hash": changed_hash,
            "result": "pass",
            "validation_reference": "unit-test external validation",
            "checked_at": "2026-09-12T00:00:00+00:00",
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")

        with self.assertRaisesRegex(novel_workspace.WorkspaceError, "schema_version"):
            novel_workspace.refresh_base(
                self.workspace,
                work["work_id"],
                "unit-test external validation",
                accept_external_change=True,
                validation_report=report_path,
            )

        report["schema_version"] = 1
        report["checked_at"] = "2026-09-12T00:00:00"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(novel_workspace.WorkspaceError, "timezone-aware"):
            novel_workspace.refresh_base(
                self.workspace,
                work["work_id"],
                "unit-test external validation",
                accept_external_change=True,
                validation_report=report_path,
            )

        report["checked_at"] = "2026-09-12T00:00:00+00:00"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with mock.patch.object(
            novel_project,
            "read_stable_bytes",
            side_effect=novel_project.ProjectError(
                "validation report changed while being read"
            ),
        ):
            with self.assertRaisesRegex(
                novel_workspace.WorkspaceError, "changed while being read"
            ):
                novel_workspace.refresh_base(
                    self.workspace,
                    work["work_id"],
                    "unit-test external validation",
                    accept_external_change=True,
                    validation_report=report_path,
                )

        self.assertEqual(self._registry_base_hash(work["work_id"]), work["base_state_hash"])

    def test_external_change_report_rejects_link_like_path(self) -> None:
        work = self.hold_project_lease()
        premise = self.project_root / "story-bible" / "premise.md"
        premise.write_bytes(premise.read_bytes() + b"\nEXTERNAL\n")
        changed_hash = novel_workspace.project_state_hash(self.project_root)
        report_path = self.base / "link-like-validation.json"
        report_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project_root": str(self.project_root.resolve()),
                    "previous_state_hash": work["base_state_hash"],
                    "validated_state_hash": changed_hash,
                    "result": "pass",
                    "validation_reference": "unit-test external validation",
                    "checked_at": "2026-09-12T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        real_link_like = novel_workspace._link_like

        def mark_report_link_like(path: Path) -> bool:
            return path == report_path or real_link_like(path)

        with mock.patch.object(
            novel_workspace, "_link_like", side_effect=mark_report_link_like
        ):
            with self.assertRaisesRegex(novel_workspace.WorkspaceError, "link"):
                novel_workspace.refresh_base(
                    self.workspace,
                    work["work_id"],
                    "unit-test external validation",
                    accept_external_change=True,
                    validation_report=report_path,
                )

        self.assertEqual(self._registry_base_hash(work["work_id"]), work["base_state_hash"])

    def test_external_change_report_must_be_outside_project_and_match_final_state(self) -> None:
        work = self.hold_project_lease()
        premise = self.project_root / "story-bible" / "premise.md"
        premise.write_bytes(premise.read_bytes() + b"\nEXTERNAL\n")
        changed_hash = novel_workspace.project_state_hash(self.project_root)
        report = {
            "schema_version": 1,
            "project_root": str(self.project_root.resolve()),
            "previous_state_hash": work["base_state_hash"],
            "validated_state_hash": changed_hash,
            "result": "pass",
            "validation_reference": "unit-test external validation",
            "checked_at": "2026-09-12T00:00:00+00:00",
        }
        inside = self.project_root / "staging" / "external-validation.json"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text(json.dumps(report), encoding="utf-8")

        with self.assertRaisesRegex(novel_workspace.WorkspaceError, "outside"):
            novel_workspace.refresh_base(
                self.workspace,
                work["work_id"],
                "unit-test external validation",
                accept_external_change=True,
                validation_report=inside,
            )

        outside = self.base / "external-validation.json"
        outside.write_text(json.dumps(report), encoding="utf-8")
        original_reader = novel_workspace._read_external_validation_report

        def mutate_after_report(*args, **kwargs):
            result = original_reader(*args, **kwargs)
            premise.write_bytes(premise.read_bytes() + b"LATE")
            return result

        with mock.patch.object(
            novel_workspace,
            "_read_external_validation_report",
            side_effect=mutate_after_report,
        ):
            with self.assertRaisesRegex(novel_workspace.WorkspaceError, "Project changed"):
                novel_workspace.refresh_base(
                    self.workspace,
                    work["work_id"],
                    "unit-test external validation",
                    accept_external_change=True,
                    validation_report=outside,
                )

        self.assertEqual(self._registry_base_hash(work["work_id"]), work["base_state_hash"])

    def test_public_review_write_cannot_bypass_another_work_lease(self) -> None:
        """Review policy writes must require the owning write context."""

        self.hold_project_lease()
        manifest_path = self.project_root / "novel.json"
        before = manifest_path.read_bytes()
        args = SimpleNamespace(
            root=str(self.project_root),
            interval=5,
            enabled="false",
            block_next_commit="false",
            authorization_reference="作者口头同意",
        )

        with self.assertRaises((RuntimeError, TypeError)) as raised:
            novel_review.configure_policy(args)

        self.assertRegex(
            str(raised.exception).lower(),
            r"(workspace|work|lease|lock|authoriz|write)",
        )
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_concurrent_source_registration_preserves_both_manifest_records(self) -> None:
        """Atomic replacement alone must not lose a concurrent manifest append."""

        # Keep a real project lease while exercising the writer.  Current
        # releases do not yet accept these identity arguments; the small
        # signature adapter below lets the regression remain useful while the
        # write-context contract is introduced without weakening the race
        # assertion itself.
        work = self.hold_project_lease()
        source_a = self.project_root / "sources" / "a.txt"
        source_b = self.project_root / "sources" / "b.txt"
        source_a.write_text("第一份参考资料\n", encoding="utf-8", newline="\n")
        source_b.write_text("第二份参考资料\n", encoding="utf-8", newline="\n")
        changed_hash = novel_workspace.project_state_hash(self.project_root)
        validation_report = self.base / "source-setup-validation.json"
        validation_report.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project_root": str(self.project_root.resolve()),
                    "previous_state_hash": work["base_state_hash"],
                    "validated_state_hash": changed_hash,
                    "result": "pass",
                    "validation_reference": "unit-test source fixture setup",
                    "checked_at": "2026-09-12T00:00:00+00:00",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        novel_workspace.refresh_base(
            self.workspace,
            work["work_id"],
            "unit-test source fixture setup",
            accept_external_change=True,
            validation_report=validation_report,
        )

        original_read = novel_research.read_manifest

        def delayed_read(root: Path) -> list[dict]:
            # Let both workers take their snapshot before either one writes.
            # This makes the lost-update interleaving deterministic against an
            # implementation that only does atomic replace without locking.
            records = original_read(root)
            time.sleep(0.2)
            return records

        common = {
            "root": self.project_root,
            "origin": "project_existing",
            "source_kind": "public_page",
            "rights_status": "public_web",
            "authorization_scope": "project_research",
            "external_use": "local_only",
            "authorization_reference": "",
            "source_url": "https://example.test/reference",
            "observed_at": "2026-09-12T00:00:00+00:00",
            "platform": "test",
            "originality_compare": False,
            "provenance_note": "concurrency regression test",
        }

        def register(source: Path) -> tuple[dict, bool]:
            kwargs = dict(common)
            kwargs["source"] = source
            parameters = inspect.signature(novel_research.register_source).parameters
            if "workspace" in parameters:
                kwargs["workspace"] = self.workspace
            elif "workspace_root" in parameters:
                kwargs["workspace_root"] = self.workspace
            if "work_id" in parameters:
                kwargs["work_id"] = work["work_id"]
            return novel_research.register_source(**kwargs)

        with mock.patch.object(novel_research, "read_manifest", side_effect=delayed_read):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(register, source) for source in (source_a, source_b)]
                results = [future.result(timeout=10) for future in futures]

        self.assertTrue(all(added for _, added in results))
        records = novel_research.read_manifest(self.project_root)
        paths = {record.get("path") for record in records}
        self.assertEqual(paths, {"sources/a.txt", "sources/b.txt"})

    def _lease_event_count(self) -> int:
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            return int(connection.execute("SELECT COUNT(*) FROM lease_events").fetchone()[0])
        finally:
            connection.close()

    def _registry_base_hash(self, work_id: str) -> str:
        connection = sqlite3.connect(self.workspace / "registry.sqlite3")
        try:
            row = connection.execute(
                "SELECT base_state_hash FROM works WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            return str(row[0])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
