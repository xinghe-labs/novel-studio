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
import novel_review  # noqa: E402
import novel_workspace  # noqa: E402
from continuity_test_utils import seal_full_baseline  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class LongNovelCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        novel_workspace.initialize_workspace(self.workspace)
        created = novel_workspace.create_project(
            self.workspace,
            title="长夜余烬",
            genre="悬疑",
            project_id="novel-legacy",
        )
        self.root = Path(created["project_root"])
        work = novel_workspace.create_work(
            self.workspace, project_id="novel-legacy", purpose="兼容性测试"
        )
        self.work_id = work["work_id"]
        novel_workspace.acquire_lock(self.workspace, self.work_id)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_chapters(self, through: int) -> None:
        index_path = self.root / "manuscript/index.md"
        index = index_path.read_text(encoding="utf-8").rstrip()
        rows: list[str] = []
        for number in range(1, through + 1):
            number_text = f"{number:04d}"
            title = f"旧章{number}"
            filename = f"{number_text}-{title}.md"
            write_text(
                self.root / "manuscript/chapters" / filename,
                f"# 第{number}章 {title}\n\n林某D在第{number}日记录第{number}条线索。\n",
            )
            write_text(
                self.root / "memory/chapters" / f"{number_text}.md",
                f"# 第{number_text}章记忆卡\n\n"
                f"- 正文：[{filename}](../../manuscript/chapters/{filename})\n"
                f"- 事实：林某D记录第{number}条线索。\n",
            )
            rows.append(
                f"| {number_text} | {title} | 林某D | 第{number}日 | 旧城 | "
                f"记录线索 | 推进调查 | T-{number:03d} | "
                f"[正文](chapters/{filename}) |"
            )
        write_text(index_path, index + "\n" + "\n".join(rows) + "\n")

        manifest = read_json(self.root / "novel.json")
        manifest["current_chapter"] = through
        write_text(
            self.root / "novel.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        state = read_json(self.root / "continuity/state.json")
        state["through_chapter"] = through
        write_text(
            self.root / "continuity/state.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        seal_full_baseline(
            self.root,
            workspace=self.workspace,
            work_id=self.work_id,
        )

    def test_default_long_project_keeps_legacy_metadata_and_templates(self) -> None:
        manifest_path = self.root / "novel.json"
        metadata_path = self.root / ".novel-project.json"
        manifest = read_json(manifest_path)
        metadata = read_json(metadata_path)
        platform = read_json(self.root / "research/platform.json")

        self.assertNotIn("work_type", manifest)
        self.assertNotIn("work_type", metadata)
        self.assertEqual(
            platform["adapters"]["fanqie"]["publication_profile"],
            "fanqie_public",
        )
        self.assertNotIn("market_scope", platform["adapters"]["fanqie"])
        self.assertNotIn("short_story_market_data", platform["adapters"]["fanqie"])
        self.assertNotIn(
            "fanqie_short_story_public", platform["publication_profiles"]
        )

        manifest_before = manifest_path.read_bytes()
        metadata_before = metadata_path.read_bytes()
        repeated = novel_workspace.register_project(
            self.workspace,
            self.root,
            project_id="novel-legacy",
        )
        listing = novel_workspace.project_list(self.workspace)

        self.assertEqual(repeated["status"], "already_registered")
        self.assertNotIn("work_type", repeated)
        self.assertNotIn("work_type", listing["projects"][0])
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(metadata_path.read_bytes(), metadata_before)

    def test_default_long_export_keeps_original_paths_and_manifest_shape(self) -> None:
        self.seed_chapters(2)
        novel_export.export_project(
            SimpleNamespace(root=str(self.root), format=None, force=False)
        )

        exports = self.root / "exports"
        self.assertTrue((exports / "《长夜余烬》-全书合并稿.txt").is_file())
        self.assertTrue((exports / "《长夜余烬》-审阅稿.docx").is_file())
        self.assertTrue((exports / "《长夜余烬》.epub").is_file())
        self.assertTrue((exports / "fanqie/0001-旧章1.txt").is_file())
        self.assertFalse((exports / "fanqie-short-story").exists())

        export_manifest = read_json(exports / "export-manifest.json")
        self.assertEqual(export_manifest["kind"], "chinese-novel-derived-exports")
        self.assertNotIn("work_type", export_manifest["source"])
        self.assertNotIn("fanqie_publication_profile", export_manifest)
        self.assertEqual(
            export_manifest["fanqie_title_mode"], "filename_only_body_text"
        )

    def test_default_long_review_keeps_periodic_packet_schema(self) -> None:
        self.seed_chapters(5)
        status = novel_review.review_status(self.root)
        self.assertNotIn("work_type", status)
        self.assertNotIn("review_mode", status)
        self.assertTrue(status["review_due"])

        packet_path = self.base / "legacy-periodic-packet.json"
        prepared = novel_review.prepare_review(
            SimpleNamespace(
                root=str(self.root),
                output=str(packet_path),
                report_output=None,
                through=None,
                force=False,
                workspace=str(self.workspace),
                work_id=self.work_id,
            )
        )
        packet = read_json(packet_path)
        report_path = Path(prepared["report_template_path"])
        report = read_json(report_path)
        self.assertNotIn("review_mode", prepared)
        self.assertNotIn("review_mode", packet)
        self.assertNotIn("review_mode", report)

        report.update(
            {
                "status": "complete",
                "reviewed_at": "2026-08-30T00:00:00+00:00",
                "summary": "长篇周期审核通过。",
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
                authorization_reference="测试旧长篇周期审核结构保持兼容",
                workspace=str(self.workspace),
                work_id=self.work_id,
            )
        )
        self.assertNotIn("review_mode", recorded)
        self.assertIn("reviews/periodic/", recorded["report_path"])
        self.assertFalse(recorded["periodic_review"]["review_due"])


if __name__ == "__main__":
    unittest.main()
