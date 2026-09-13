from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_originality  # noqa: E402
import novel_cli  # noqa: E402
import novel_project  # noqa: E402
import novel_research  # noqa: E402
import novel_workspace  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class OriginalityStableReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)
        self.root = self.workspace / "projects" / "originality-safety"
        novel_project.init_project(
            SimpleNamespace(
                root=str(self.root),
                title="原创性稳定读取测试",
                language="zh-CN",
                genre="悬疑",
            )
        )
        novel_workspace.register_project(
            self.workspace, self.root, project_id="originality-safety"
        )
        self.work = novel_workspace.create_work(
            self.workspace,
            project_id="originality-safety",
            purpose="原创性输入稳定性测试",
        )
        novel_workspace.acquire_lock(self.workspace, self.work["work_id"])
        self.candidate = self.root / "staging/chapters/0001/chapter.md"
        write_text(self.candidate, "# 第一章\n\n候选正文在审计期间必须保持稳定。\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def audit_args(self, *, reference: list[str] | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            root=str(self.root),
            candidate=[str(self.candidate)],
            reference=reference,
            exact_minimum=18,
            near_threshold=0.72,
            max_findings=30,
            no_report=True,
            output=None,
            workspace=None,
            work_id=None,
        )

    def unstable_read_patch(self, target: Path):
        stable_reader = novel_originality._read_stable
        target = target.resolve()

        def read(path: Path, *, label: str) -> bytes:
            current = Path(path).resolve()
            if current == target:
                # Exercise the caller's fail-closed path without patching
                # pathlib's metadata methods (which would also affect link
                # detection). The production reader has its own byte/identity
                # race check; this models the resulting safety exception at
                # the narrow boundary under test.
                raise novel_originality.OriginalityError(
                    f"{label} changed while being read: {path}"
                )
            return stable_reader(path, label=label)

        return mock.patch.object(novel_originality, "_read_stable", side_effect=read)

    def test_candidate_replacement_during_read_blocks_audit(self) -> None:
        with self.unstable_read_patch(self.candidate):
            with self.assertRaisesRegex(
                novel_originality.OriginalityError, "changed while being read"
            ):
                novel_originality.audit_project(self.audit_args())

    def test_report_interrupt_leaves_no_partial_project_output(self) -> None:
        output = self.root / "staging/originality/interrupted.json"
        args = self.audit_args()
        args.no_report = False
        args.output = str(output)
        args.workspace = str(self.workspace)
        args.work_id = self.work["work_id"]

        with mock.patch.object(novel_cli.os, "fsync", side_effect=SystemExit(7)):
            with self.assertRaises(SystemExit):
                novel_originality.audit_project(args)

        self.assertFalse(output.exists())
        self.assertEqual(list(output.parent.iterdir()), [])

    def test_report_does_not_overwrite_concurrent_creator(self) -> None:
        output = self.root / "staging/originality/concurrent.json"
        args = self.audit_args()
        args.no_report = False
        args.output = str(output)
        args.workspace = str(self.workspace)
        args.work_id = self.work["work_id"]

        def concurrent_winner(_source: Path, destination: Path) -> None:
            Path(destination).write_bytes(b"concurrent report")
            raise FileExistsError("injected concurrent creator")

        with mock.patch.object(
            novel_cli.os, "link", side_effect=concurrent_winner
        ):
            with self.assertRaisesRegex(
                novel_originality.OriginalityError, "Refusing to overwrite"
            ):
                novel_originality.audit_project(args)

        self.assertEqual(output.read_bytes(), b"concurrent report")
        self.assertEqual(
            [path for path in output.parent.iterdir() if path.suffix == ".tmp"],
            [],
        )

    def test_registered_reference_replacement_during_read_blocks_audit(self) -> None:
        reference = self.root / "sources/reference.txt"
        write_text(reference, "登记后的参考文本在审计读取期间也必须保持稳定。\n")
        changed_hash = novel_workspace.project_state_hash(self.root)
        report = self.base / "source-fixture-validation.json"
        report.write_text(
            "{\n"
            '  "schema_version": 1,\n'
            f'  "project_root": "{str(self.root.resolve()).replace(chr(92), chr(92) * 2)}",\n'
            f'  "previous_state_hash": "{self.work["base_state_hash"]}",\n'
            f'  "validated_state_hash": "{changed_hash}",\n'
            '  "result": "pass",\n'
            '  "validation_reference": "originality source fixture",\n'
            '  "checked_at": "2026-09-13T00:00:00+00:00"\n'
            "}\n",
            encoding="utf-8",
            newline="\n",
        )
        novel_workspace.refresh_base(
            self.workspace,
            self.work["work_id"],
            "originality source fixture",
            accept_external_change=True,
            validation_report=report,
        )
        record, added = novel_research.register_source(
            self.root,
            reference,
            origin="project_existing",
            source_kind="authorized_reference",
            rights_status="public_web",
            authorization_scope="project_research_and_originality_audit",
            external_use="local_only",
            authorization_reference="",
            source_url="https://example.test/reference",
            observed_at="2026-09-13T00:00:00+00:00",
            platform="test",
            originality_compare=True,
            provenance_note="originality stable-read regression",
            copy_external=False,
            workspace=self.workspace,
            work_id=self.work["work_id"],
        )
        self.assertTrue(added)

        with self.unstable_read_patch(reference):
            with self.assertRaisesRegex(
                novel_originality.OriginalityError, "changed while being read"
            ):
                novel_originality.audit_project(
                    self.audit_args(reference=[record["path"]])
                )


if __name__ == "__main__":
    unittest.main()
