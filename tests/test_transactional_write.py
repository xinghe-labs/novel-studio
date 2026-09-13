from __future__ import annotations

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import novel_project  # noqa: E402


class TransactionalWriteTests(unittest.TestCase):
    def _targets(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        first = root / "first.bin"
        second = root / "second.bin"
        # Deliberately use bytes that cannot be decoded as UTF-8.  Rollback must
        # restore these exact bytes rather than round-tripping through text.
        first.write_bytes(b"\xffOLD-A")
        second.write_bytes(b"\xfeOLD-B")
        return temp_dir, first, second

    def test_replace_failure_restores_original_non_utf8_bytes(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            real_replace = os.replace
            calls = 0

            def fail_second(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected replace failure")
                real_replace(source, target)

            with mock.patch.object(novel_project.os, "replace", side_effect=fail_second):
                with self.assertRaises(novel_project.ProjectError):
                    novel_project.transactional_write(
                        [(first, b"NEW-A"), (second, b"NEW-B")]
                    )

            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(second.read_bytes(), b"\xfeOLD-B")
        finally:
            temp_dir.cleanup()

    def test_keyboard_interrupt_during_replace_rolls_back_and_is_wrapped(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            real_replace = os.replace
            calls = 0

            def interrupt_second(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt()
                real_replace(source, target)

            with mock.patch.object(novel_project.os, "replace", side_effect=interrupt_second):
                with self.assertRaises(novel_project.ProjectError) as raised:
                    novel_project.transactional_write(
                        [(first, b"NEW-A"), (second, b"NEW-B")]
                    )

            self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(second.read_bytes(), b"\xfeOLD-B")
        finally:
            temp_dir.cleanup()

    def test_keyboard_interrupt_after_replace_still_rolls_back_target(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            real_replace = os.replace
            calls = 0

            def replace_then_interrupt(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                nonlocal calls
                calls += 1
                real_replace(source, target)
                if calls == 1:
                    raise KeyboardInterrupt()

            with mock.patch.object(novel_project.os, "replace", side_effect=replace_then_interrupt):
                with self.assertRaises(novel_project.ProjectError):
                    novel_project.transactional_write(
                        [(first, b"NEW-A"), (second, b"NEW-B")]
                    )

            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(second.read_bytes(), b"\xfeOLD-B")
        finally:
            temp_dir.cleanup()

    def test_recover_pending_applying_journal_restores_bytes(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "crashed"
            transaction_dir.mkdir(parents=True)
            backup = transaction_dir / "backup-0000.bin"
            backup.write_bytes(first.read_bytes())
            first.write_bytes(b"NEW-A")
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "applying",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"\xffOLD-A"),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-A"),
                        "backup": backup.name,
                    },
                    {
                        "target": second.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"\xfeOLD-B"),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-B"),
                        "backup": None,
                    },
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal, ensure_ascii=False), encoding="utf-8"
            )
            # Simulate a crash after the second target was replaced, before the
            # recovery process discovers that its backup is missing.
            second.write_bytes(b"NEW-B")
            with self.assertRaises(novel_project.ProjectError):
                novel_project.recover_pending_transactions(first.parent)
            # The missing second backup is detected before cleanup; the journal
            # remains for a later, safe recovery attempt.
            self.assertTrue(transaction_dir.is_dir())

            second_backup = transaction_dir / "backup-0001.bin"
            second_backup.write_bytes(b"\xfeOLD-B")
            journal["files"][1]["backup"] = second_backup.name
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal, ensure_ascii=False), encoding="utf-8"
            )
            recovered = novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(second.read_bytes(), b"\xfeOLD-B")
            self.assertTrue(recovered)
            self.assertFalse(transaction_root.exists())
        finally:
            temp_dir.cleanup()

    def test_new_transaction_snapshots_after_pending_recovery(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "pending"
            transaction_dir.mkdir(parents=True)
            backup = transaction_dir / "backup-0000.bin"
            backup.write_bytes(first.read_bytes())
            first.write_bytes(b"CRASHED-NEW")
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "applying",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"\xffOLD-A"),
                        "new_sha256": novel_project.sha256_bytes(b"CRASHED-NEW"),
                        "backup": backup.name,
                    }
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )

            def fail_validation() -> None:
                raise ValueError("injected post-write validation failure")

            with self.assertRaises(novel_project.ProjectError):
                novel_project.transactional_write(
                    [(first, b"SECOND-NEW")],
                    validator=fail_validation,
                    journal_root=first.parent,
                )
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertFalse(transaction_root.exists())
        finally:
            temp_dir.cleanup()

    def test_recovery_refuses_unexpected_external_change(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "unexpected"
            transaction_dir.mkdir(parents=True)
            backup = transaction_dir / "backup.bin"
            backup.write_bytes(first.read_bytes())
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "applying",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"\xffOLD-A"),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-A"),
                        "backup": backup.name,
                    }
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )
            first.write_bytes(b"EXTERNAL")
            with self.assertRaisesRegex(novel_project.ProjectError, "unexpected change"):
                novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(first.read_bytes(), b"EXTERNAL")
            self.assertTrue(transaction_dir.is_dir())
        finally:
            temp_dir.cleanup()

    def test_recovery_refuses_journal_for_another_project(self) -> None:
        temp_dir, first, _ = self._targets()
        other_dir = tempfile.TemporaryDirectory()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "wrong-root"
            transaction_dir.mkdir(parents=True)
            backup = transaction_dir / "backup.bin"
            backup.write_bytes(first.read_bytes())
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "applying",
                "project_root": other_dir.name,
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(first.read_bytes()),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-A"),
                        "backup": backup.name,
                    }
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )
            with self.assertRaisesRegex(novel_project.ProjectError, "different project root"):
                novel_project.recover_pending_transactions(first.parent)
            self.assertTrue(transaction_dir.is_dir())
        finally:
            other_dir.cleanup()
            temp_dir.cleanup()

    def test_recovery_rejects_non_string_hash_without_type_error(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "bad-hash"
            transaction_dir.mkdir(parents=True)
            backup = transaction_dir / "backup.bin"
            backup.write_bytes(first.read_bytes())
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "applying",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(first.read_bytes()),
                        "new_sha256": {"not": "a hash"},
                        "backup": backup.name,
                    }
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )
            with self.assertRaisesRegex(novel_project.ProjectError, "invalid file hashes"):
                novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertTrue(transaction_dir.is_dir())
        finally:
            temp_dir.cleanup()

    def test_recovery_prevalidates_all_backup_hashes_before_writing(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "bad-backup"
            transaction_dir.mkdir(parents=True)
            first_backup = transaction_dir / "backup-0000.bin"
            second_backup = transaction_dir / "backup-0001.bin"
            first_backup.write_bytes(first.read_bytes())
            second_backup.write_bytes(b"CORRUPTED")
            first.write_bytes(b"NEW-A")
            second.write_bytes(b"NEW-B")
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "applying",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"\xffOLD-A"),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-A"),
                        "backup": first_backup.name,
                    },
                    {
                        "target": second.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"\xfeOLD-B"),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-B"),
                        "backup": second_backup.name,
                    },
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )
            with self.assertRaisesRegex(novel_project.ProjectError, "backup hash"):
                novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(first.read_bytes(), b"NEW-A")
            self.assertEqual(second.read_bytes(), b"NEW-B")
            self.assertTrue(transaction_dir.is_dir())
        finally:
            temp_dir.cleanup()

    def test_transaction_rejects_duplicate_targets_before_writing(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            with self.assertRaisesRegex(novel_project.ProjectError, "duplicate targets"):
                novel_project.transactional_write(
                    [(first, b"NEW-A"), (first, b"NEW-B")]
                )
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
        finally:
            temp_dir.cleanup()

    def test_interrupt_after_commit_marker_does_not_rollback(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            original_writer = novel_project._atomic_write_bytes_unpatched
            marker_seen = False

            def write_marker_then_interrupt(path: Path, content: bytes) -> None:
                nonlocal marker_seen
                original_writer(path, content)
                if path.name == "journal.json" and b'"status": "committed"' in content:
                    marker_seen = True
                    raise KeyboardInterrupt()

            with mock.patch.object(
                novel_project, "_atomic_write_bytes_unpatched", side_effect=write_marker_then_interrupt
            ):
                novel_project.transactional_write(
                    [(first, b"NEW-A")], journal_root=first.parent
                )
            self.assertTrue(marker_seen)
            self.assertEqual(first.read_bytes(), b"NEW-A")
            recovered = novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(len(recovered), 1)
            self.assertIn("cleaned committed transaction", recovered[0])
        finally:
            temp_dir.cleanup()

    def test_committed_journal_is_retained_if_target_changed(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "committed-but-modified"
            transaction_dir.mkdir(parents=True)
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "committed",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"OLD"),
                        "new_sha256": novel_project.sha256_bytes(b"EXPECTED-NEW"),
                        "backup": "backup.bin",
                    }
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )
            first.write_bytes(b"EXTERNAL-AFTER-COMMIT")

            with self.assertRaisesRegex(
                novel_project.ProjectError, "changed before journal cleanup"
            ):
                novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(first.read_bytes(), b"EXTERNAL-AFTER-COMMIT")
            self.assertTrue(transaction_dir.is_dir())
        finally:
            temp_dir.cleanup()

    def test_transaction_cleanup_refuses_unregistered_link_like_artifact(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "committed-with-link"
            transaction_dir.mkdir(parents=True)
            trap = transaction_dir / "unregistered-junction"
            trap.mkdir()
            outside = first.parent / "outside.txt"
            outside.write_bytes(b"DO-NOT-TOUCH")
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "committed",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"OLD"),
                        "new_sha256": novel_project.sha256_bytes(first.read_bytes()),
                        "backup": "backup.bin",
                    }
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )

            real_link_like = novel_project._link_like

            def mark_trap_link_like(path: Path) -> bool:
                return path == trap or real_link_like(path)

            with mock.patch.object(
                novel_project, "_link_like", side_effect=mark_trap_link_like
            ):
                with self.assertRaisesRegex(
                    novel_project.ProjectError, "link-like transaction artifact"
                ):
                    novel_project.recover_pending_transactions(first.parent)

            self.assertEqual(outside.read_bytes(), b"DO-NOT-TOUCH")
            self.assertTrue(transaction_dir.is_dir())
            self.assertTrue(trap.is_dir())
            self.assertTrue((transaction_dir / "journal.json").is_file())
        finally:
            temp_dir.cleanup()

    def test_rolled_back_journal_survives_cleanup_failure_and_is_recoverable(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            def fail_validation() -> None:
                raise ValueError("injected validation failure")

            with mock.patch.object(
                novel_project,
                "_remove_transaction_directory",
                side_effect=OSError("injected cleanup failure"),
            ):
                with self.assertRaisesRegex(
                    novel_project.ProjectError, "journal cleanup"
                ):
                    novel_project.transactional_write(
                        [(first, b"NEW-A")],
                        validator=fail_validation,
                        journal_root=first.parent,
                    )

            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dirs = list(transaction_root.iterdir())
            self.assertEqual(len(transaction_dirs), 1)
            journal = json.loads(
                (transaction_dirs[0] / "journal.json").read_text(encoding="utf-8")
            )
            self.assertEqual(journal["status"], "rolled_back")

            recovered = novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertIn("cleaned rolled-back transaction", recovered[0])
            self.assertFalse(transaction_root.exists())
        finally:
            temp_dir.cleanup()

    def test_recovery_ignores_changed_read_only_precondition_while_rolling_back(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_dir = transaction_root / "changed-precondition"
            transaction_dir.mkdir(parents=True)
            backup = transaction_dir / "backup.bin"
            backup.write_bytes(first.read_bytes())
            first.write_bytes(b"NEW-A")
            expected_second = novel_project.sha256_bytes(second.read_bytes())
            second.write_bytes(b"EXTERNAL-CHANGE")
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "applying",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(b"\xffOLD-A"),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-A"),
                        "backup": backup.name,
                    }
                ],
                "preconditions": [
                    {
                        "target": second.name,
                        "expected_sha256": expected_second,
                    }
                ],
            }
            (transaction_dir / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )

            recovered = novel_project.recover_pending_transactions(first.parent)

            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(second.read_bytes(), b"EXTERNAL-CHANGE")
            self.assertIn("rolled back transaction", recovered[0])
            self.assertFalse(transaction_root.exists())
        finally:
            temp_dir.cleanup()

    def test_prepared_transaction_is_cleaned_without_backups(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            prepared = transaction_root / "prepared"
            prepared.mkdir(parents=True)
            journal = {
                "schema_version": novel_project.TRANSACTION_SCHEMA_VERSION,
                "status": "prepared",
                "project_root": str(first.parent),
                "created_at": "2026-09-12T00:00:00+00:00",
                "files": [
                    {
                        "target": first.name,
                        "prior_exists": True,
                        "prior_sha256": novel_project.sha256_bytes(first.read_bytes()),
                        "new_sha256": novel_project.sha256_bytes(b"NEW-A"),
                        "backup": "missing-backup.bin",
                    }
                ],
            }
            (prepared / "journal.json").write_text(
                json.dumps(journal), encoding="utf-8"
            )
            recovered = novel_project.recover_pending_transactions(first.parent)

            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(len(recovered), 1)
            self.assertTrue(any("discarded prepared transaction" in item for item in recovered))
            self.assertFalse(transaction_root.exists())
        finally:
            temp_dir.cleanup()

    def test_unjournaled_artifacts_fail_closed_but_empty_directory_is_cleaned(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            abandoned = transaction_root / "abandoned"
            abandoned.mkdir(parents=True)
            artifact = abandoned / "backup-before-journal.bin"
            artifact.write_bytes(b"OLD")

            with self.assertRaisesRegex(novel_project.ProjectError, "no journal"):
                novel_project.recover_pending_transactions(first.parent)
            self.assertEqual(artifact.read_bytes(), b"OLD")

            artifact.unlink()
            recovered = novel_project.recover_pending_transactions(first.parent)
            self.assertIn("cleaned empty transaction", recovered[0])
            self.assertFalse(transaction_root.exists())
        finally:
            temp_dir.cleanup()

    def test_transaction_root_reparse_point_is_rejected(self) -> None:
        temp_dir, first, _ = self._targets()
        try:
            transaction_root = first.parent / novel_project.TRANSACTION_DIRNAME
            transaction_root.mkdir()
            real_link_like = novel_project._link_like

            def mark_root_link_like(path: Path) -> bool:
                return path == transaction_root or real_link_like(path)

            with mock.patch.object(
                novel_project, "_link_like", side_effect=mark_root_link_like
            ):
                with self.assertRaisesRegex(
                    novel_project.ProjectError, "recovery path"
                ):
                    novel_project.transactional_write(
                        [(first, b"NEW-A")], journal_root=first.parent
                    )

            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
        finally:
            temp_dir.cleanup()

    def test_compare_and_swap_rejects_change_after_snapshot(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            real_replace = os.replace
            calls = 0

            def change_second_after_first(
                source: str | os.PathLike[str], target: str | os.PathLike[str]
            ) -> None:
                nonlocal calls
                calls += 1
                real_replace(source, target)
                if calls == 1:
                    second.write_bytes(b"EXTERNAL")

            with mock.patch.object(
                novel_project.os, "replace", side_effect=change_second_after_first
            ):
                with self.assertRaisesRegex(
                    novel_project.ProjectError, "compare-and-swap"
                ):
                    novel_project.transactional_write(
                        [(first, b"NEW-A"), (second, b"NEW-B")]
                    )

            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(second.read_bytes(), b"EXTERNAL")
        finally:
            temp_dir.cleanup()

    def test_system_exit_during_replace_rolls_back(self) -> None:
        temp_dir, first, second = self._targets()
        try:
            real_replace = os.replace
            calls = 0

            def exit_on_second(
                source: str | os.PathLike[str], target: str | os.PathLike[str]
            ) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise SystemExit(7)
                real_replace(source, target)

            with mock.patch.object(novel_project.os, "replace", side_effect=exit_on_second):
                with self.assertRaises(novel_project.ProjectError) as raised:
                    novel_project.transactional_write(
                        [(first, b"NEW-A"), (second, b"NEW-B")]
                    )
            self.assertIsInstance(raised.exception.__cause__, SystemExit)
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
            self.assertEqual(second.read_bytes(), b"\xfeOLD-B")
        finally:
            temp_dir.cleanup()

    def test_transaction_rejects_symlink_target(self) -> None:
        temp_dir, first, _ = self._targets()
        link = first.parent / "link.bin"
        try:
            link.symlink_to(first)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are unavailable in this environment")
        try:
            with self.assertRaisesRegex(novel_project.ProjectError, "symbolic link"):
                novel_project.transactional_write([(link, b"NEW")])
            self.assertEqual(first.read_bytes(), b"\xffOLD-A")
        finally:
            link.unlink(missing_ok=True)
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
