from __future__ import annotations

import copy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_project  # noqa: E402
import novel_research  # noqa: E402
import novel_workspace  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def project_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class ResearchSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def init_project(self, name: str) -> tuple[Path, dict]:
        root = self.workspace / "projects" / name
        novel_project.init_project(
            SimpleNamespace(
                root=str(root),
                title=f"研究安全测试-{name}",
                language="zh-CN",
                genre="悬疑",
            )
        )
        novel_workspace.register_project(self.workspace, root, project_id=name)
        work = novel_workspace.create_work(
            self.workspace,
            project_id=name,
            purpose="研究事务与来源边界测试",
        )
        novel_workspace.acquire_lock(self.workspace, work["work_id"])
        return root, work

    def registry_base_hash(self, work_id: str) -> str:
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

    def assert_project_and_base_unchanged(
        self,
        root: Path,
        work: dict,
        before_files: dict[str, bytes],
        before_hash: str,
        before_work_json: bytes,
    ) -> None:
        self.assertEqual(project_files(root), before_files)
        self.assertEqual(novel_workspace.project_state_hash(root), before_hash)
        self.assertEqual(self.registry_base_hash(work["work_id"]), before_hash)
        work_json = Path(work["work_root"]) / "work.json"
        self.assertEqual(work_json.read_bytes(), before_work_json)
        transaction_root = root / ".novel-transaction"
        self.assertFalse(
            transaction_root.exists() and any(transaction_root.iterdir()),
            "failed research transaction left a pending journal",
        )
        self.assertEqual(
            novel_workspace.write_check(self.workspace, work["work_id"])["status"],
            "pass",
        )

    def snapshot(self, root: Path, work: dict) -> tuple[dict[str, bytes], str, bytes]:
        return (
            project_files(root),
            novel_workspace.project_state_hash(root),
            (Path(work["work_root"]) / "work.json").read_bytes(),
        )

    def external_registration_kwargs(
        self, root: Path, work: dict, source: Path
    ) -> dict:
        return {
            "root": root,
            "source": source,
            "origin": "user_local",
            "source_kind": "authorized_full_text",
            "rights_status": "user_authorized",
            "authorization_scope": "project_research_and_originality_audit",
            "external_use": "local_only",
            "authorization_reference": "作者明确授权本项目在本机研究",
            "source_url": "",
            "observed_at": "2026-09-13T00:00:00+00:00",
            "platform": "",
            "originality_compare": True,
            "provenance_note": "research safety regression",
            "copy_external": True,
            "workspace": self.workspace,
            "work_id": work["work_id"],
        }

    def collect_args(self, root: Path, work: dict) -> SimpleNamespace:
        return SimpleNamespace(
            root=str(root),
            platform="fanqie",
            channel="all",
            sort="hot",
            page_count=18,
            page_index=0,
            input=str(SKILL_ROOT / "tests/fixtures/fanqie-library.json"),
            observed_at="2026-09-13T01:02:03+00:00",
            timeout=5.0,
            workspace=str(self.workspace),
            work_id=work["work_id"],
        )

    def injected_replace(self, predicate, error_factory):
        real_replace = novel_project._REAL_OS_REPLACE

        def replace(source: str | Path, target: str | Path) -> None:
            if predicate(Path(target)):
                raise error_factory()
            real_replace(source, target)

        return mock.patch.object(novel_project.os, "replace", side_effect=replace)

    @staticmethod
    def cause_chain(error: BaseException) -> list[BaseException]:
        result: list[BaseException] = []
        current: BaseException | None = error
        while current is not None and current not in result:
            result.append(current)
            current = current.__cause__
        return result

    def test_source_copy_then_manifest_failure_rolls_back_everything(self) -> None:
        root, work = self.init_project("source-manifest-failure")
        source = self.base / "authorized-reference.txt"
        write_text(source, "用户提供的合法参考文本。\n")
        before_files, before_hash, before_work_json = self.snapshot(root, work)
        manifest = (root / novel_research.MANIFEST_RELATIVE).resolve()

        with self.injected_replace(
            lambda target: target.resolve() == manifest,
            lambda: OSError("injected manifest replacement failure"),
        ):
            with self.assertRaises(novel_research.ResearchError):
                novel_research.register_source(
                    **self.external_registration_kwargs(root, work, source)
                )

        self.assert_project_and_base_unchanged(
            root, work, before_files, before_hash, before_work_json
        )
        self.assertEqual(list((root / "sources/local").glob("*")), [])

    def test_source_registration_keyboard_interrupt_is_rolled_back(self) -> None:
        root, work = self.init_project("source-keyboard-interrupt")
        source = self.base / "interrupt-reference.txt"
        write_text(source, "发生中断时不得留下半份来源登记。\n")
        before_files, before_hash, before_work_json = self.snapshot(root, work)
        manifest = (root / novel_research.MANIFEST_RELATIVE).resolve()

        with self.injected_replace(
            lambda target: target.resolve() == manifest,
            KeyboardInterrupt,
        ):
            with self.assertRaises(novel_research.ResearchError) as raised:
                novel_research.register_source(
                    **self.external_registration_kwargs(root, work, source)
                )

        self.assertTrue(
            any(isinstance(item, KeyboardInterrupt) for item in self.cause_chain(raised.exception))
        )
        self.assert_project_and_base_unchanged(
            root, work, before_files, before_hash, before_work_json
        )

    def test_platform_failures_roll_back_each_business_target(self) -> None:
        cases = (
            ("raw", lambda root, target: target.name.endswith(".raw.json")),
            ("normalized", lambda root, target: target.name.endswith(".normalized.json")),
            (
                "manifest",
                lambda root, target: target.resolve()
                == (root / novel_research.MANIFEST_RELATIVE).resolve(),
            ),
            (
                "platform",
                lambda root, target: target.resolve()
                == (root / novel_research.PLATFORM_RELATIVE).resolve(),
            ),
        )
        for label, predicate in cases:
            with self.subTest(target=label):
                root, work = self.init_project(f"platform-failure-{label}")
                before_files, before_hash, before_work_json = self.snapshot(root, work)
                with self.injected_replace(
                    lambda target, root=root, predicate=predicate: predicate(root, target),
                    lambda: OSError(f"injected {label} replacement failure"),
                ):
                    with self.assertRaises(novel_research.ResearchError):
                        novel_research.collect_platform(self.collect_args(root, work))
                self.assert_project_and_base_unchanged(
                    root, work, before_files, before_hash, before_work_json
                )

    def test_platform_interrupts_roll_back_and_preserve_base(self) -> None:
        cases = (
            (
                "keyboard",
                lambda root, target: target.name.endswith(".normalized.json"),
                KeyboardInterrupt,
            ),
            (
                "system-exit",
                lambda root, target: target.resolve()
                == (root / novel_research.PLATFORM_RELATIVE).resolve(),
                lambda: SystemExit(7),
            ),
        )
        for label, predicate, error_factory in cases:
            with self.subTest(interrupt=label):
                root, work = self.init_project(f"platform-interrupt-{label}")
                before_files, before_hash, before_work_json = self.snapshot(root, work)
                with self.injected_replace(
                    lambda target, root=root, predicate=predicate: predicate(root, target),
                    error_factory,
                ):
                    with self.assertRaises(novel_research.ResearchError) as raised:
                        novel_research.collect_platform(self.collect_args(root, work))
                expected_type = KeyboardInterrupt if label == "keyboard" else SystemExit
                self.assertTrue(
                    any(
                        isinstance(item, expected_type)
                        for item in self.cause_chain(raised.exception)
                    )
                )
                self.assert_project_and_base_unchanged(
                    root, work, before_files, before_hash, before_work_json
                )

    def test_external_source_replaced_after_preparation_is_rejected(self) -> None:
        root, work = self.init_project("source-preparation-race")
        source = self.base / "replace-during-preparation.txt"
        replacement = self.base / "replacement.txt"
        write_text(source, "准备阶段读取到的原始内容。\n")
        write_text(replacement, "准备完成后被替换的新内容。\n")
        before_files, before_hash, before_work_json = self.snapshot(root, work)
        original_prepare = novel_research._prepare_source_registration

        def prepare_then_replace(*args, **kwargs):
            result = original_prepare(*args, **kwargs)
            novel_project._REAL_OS_REPLACE(replacement, source)
            return result

        with mock.patch.object(
            novel_research,
            "_prepare_source_registration",
            side_effect=prepare_then_replace,
        ):
            with self.assertRaisesRegex(
                novel_research.ResearchError, "changed during preparation"
            ):
                novel_research.register_source(
                    **self.external_registration_kwargs(root, work, source)
                )

        self.assert_project_and_base_unchanged(
            root, work, before_files, before_hash, before_work_json
        )

    def register_valid_source(self, root: Path, work: dict) -> tuple[dict, Path]:
        source = self.base / f"{root.name}-valid-reference.txt"
        write_text(source, "这是一份有明确本地原创性比对授权的参考资料。\n")
        record, added = novel_research.register_source(
            **self.external_registration_kwargs(root, work, source)
        )
        self.assertTrue(added)
        return record, source

    def test_strict_source_schema_is_shared_by_verify_and_project_validation(self) -> None:
        root, work = self.init_project("source-schema")
        valid_record, _ = self.register_valid_source(root, work)
        manifest = root / novel_research.MANIFEST_RELATIVE
        valid_manifest = manifest.read_bytes()
        valid_hash = novel_workspace.project_state_hash(root)
        valid_work_json = (Path(work["work_root"]) / "work.json").read_bytes()
        cases = (
            ("schema bool", {"schema_version": True}, "schema_version"),
            (
                "source id mismatch",
                {"source_id": "SRC-0000000000-000000"},
                "source_id does not match",
            ),
            ("uppercase digest", {"sha256": "A" * 64}, "lowercase hex"),
            ("origin enum", {"origin": "imported"}, "origin is unsupported"),
            (
                "rights enum",
                {"rights_status": "assumed"},
                "rights_status is unsupported",
            ),
            (
                "external use enum",
                {"external_use": "cloud_ok"},
                "external_use is unsupported",
            ),
            ("source kind type", {"source_kind": []}, "source_kind"),
            ("media type type", {"media_type": None}, "media_type"),
            (
                "originality type",
                {"originality_compare": "true"},
                "originality_compare must be boolean",
            ),
            (
                "registered time",
                {"registered_at": "2026-09-13T00:00:00"},
                "registered_at",
            ),
            ("observed time type", {"observed_at": 7}, "observed_at"),
            ("size bool", {"size_bytes": True}, "size_bytes"),
            (
                "path outside sources",
                {"path": "research/platform.json"},
                "path must be a normalized path under sources/",
            ),
            (
                "local only external scope",
                {"authorization_scope": "project_research_external_processing"},
                "local_only source authorizes external processing",
            ),
            (
                "originality without scope",
                {"authorization_scope": "project_research"},
                "originality comparison requires an authorization_scope",
            ),
            (
                "prohibited originality",
                {"external_use": "prohibited"},
                "prohibited",
            ),
        )

        for label, changes, expected in cases:
            with self.subTest(case=label):
                changed = copy.deepcopy(valid_record)
                changed.update(changes)
                manifest.write_bytes(novel_research.manifest_bytes([changed]))
                verified, code = novel_research.verify_command(
                    SimpleNamespace(root=str(root))
                )
                self.assertEqual(code, 1)
                self.assertIn(expected, "\n".join(verified["errors"]))
                project_errors, _ = novel_project.collect_validation(root)
                self.assertIn(expected, "\n".join(project_errors))
                manifest.write_bytes(valid_manifest)

        self.assertEqual(novel_workspace.project_state_hash(root), valid_hash)
        self.assertEqual(self.registry_base_hash(work["work_id"]), valid_hash)
        self.assertEqual(
            (Path(work["work_root"]) / "work.json").read_bytes(), valid_work_json
        )
        self.assertEqual(
            novel_workspace.write_check(self.workspace, work["work_id"])["status"],
            "pass",
        )

    def test_source_symlink_is_rejected_when_supported(self) -> None:
        root, work = self.init_project("source-symlink")
        record, outside = self.register_valid_source(root, work)
        registered = root / record["path"]
        original = registered.read_bytes()
        registered.unlink()
        try:
            registered.symlink_to(outside)
        except OSError as exc:
            registered.write_bytes(original)
            self.skipTest(f"symbolic links are unavailable in this environment: {exc}")
        try:
            result, code = novel_research.verify_command(SimpleNamespace(root=str(root)))
            self.assertEqual(code, 1)
            self.assertRegex("\n".join(result["errors"]), r"link|junction")
            project_errors, _ = novel_project.collect_validation(root)
            self.assertRegex("\n".join(project_errors), r"link|junction")
        finally:
            registered.unlink(missing_ok=True)
            registered.write_bytes(original)

    def test_reparse_attribute_is_treated_as_link_like(self) -> None:
        path = mock.Mock()
        path.is_symlink.return_value = False
        path.is_junction.return_value = False
        metadata = SimpleNamespace(st_file_attributes=0x400)
        with mock.patch.object(novel_research.os, "lstat", return_value=metadata):
            self.assertTrue(novel_research._link_like(path))


if __name__ == "__main__":
    unittest.main()
