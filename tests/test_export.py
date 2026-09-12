from __future__ import annotations

import json
import sys
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILL_ROOT / "tests"))

import novel_export  # noqa: E402
import novel_project  # noqa: E402
import novel_workspace  # noqa: E402
from continuity_test_utils import seal_full_baseline  # noqa: E402


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class NovelExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "project"
        novel_project.init_project(
            SimpleNamespace(
                root=str(self.root),
                title="长夜余烬",
                language="zh-CN",
                genre="悬疑",
            )
        )
        self.add_chapter(
            1,
            "雾港来信",
            "# 第一章 雾港来信\n\n"
            "林某D在**旧邮局**收到一封[匿名来信](https://example.invalid)。\n"
            "她低声说：‘* * *’不是暗号。\n\n"
            "* * *\n\n"
            "信封里只有一把钥匙。\n",
        )
        self.add_chapter(
            2,
            "储物柜里的照片",
            "# 第二章 储物柜里的照片\n\n她用钥匙打开储物柜。\n\n> 照片拍摄于昨天。\n",
        )
        manifest = read_json(self.root / "novel.json")
        manifest["current_chapter"] = 2
        write_text(
            self.root / "novel.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        state = read_json(self.root / "continuity/state.json")
        state["through_chapter"] = 2
        write_text(
            self.root / "continuity/state.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        seal_full_baseline(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_chapter(self, number: int, title: str, content: str) -> None:
        number_text = f"{number:04d}"
        filename = f"{number_text}-{title}.md"
        write_text(self.root / "manuscript/chapters" / filename, content)
        write_text(
            self.root / "memory/chapters" / f"{number_text}.md",
            f"# 第{number_text}章记忆卡\n\n"
            f"- 正文：[{number_text}](../../manuscript/chapters/{filename})\n",
        )
        index_path = self.root / "manuscript/index.md"
        index = index_path.read_text(encoding="utf-8").rstrip()
        row = (
            f"| {number_text} | {title} | 林某D | 第{number}日 | 雾港 | "
            f"事实摘要 | 关键变化 | T-{number:03d} | "
            f"[正文](chapters/{filename}) |"
        )
        write_text(index_path, index + "\n" + row + "\n")

    def export(self, formats: list[str] | None = None, force: bool = False) -> dict:
        return novel_export.export_project(
            SimpleNamespace(root=str(self.root), format=formats, force=force)
        )

    def test_all_formats_are_derived_from_indexed_markdown(self) -> None:
        before_hash = novel_workspace.project_state_hash(self.root)
        before = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and path.parts[len(self.root.parts)] != "exports"
        }
        result = self.export()
        self.assertEqual(result["status"], "exported")
        self.assertEqual(result["formats"], ["docx", "epub", "fanqie", "txt"])

        exports = self.root / "exports"
        combined = exports / "《长夜余烬》-全书合并稿.txt"
        review = exports / "《长夜余烬》-审阅稿.docx"
        epub = exports / "《长夜余烬》.epub"
        fanqie_one = exports / "fanqie/0001-雾港来信.txt"
        fanqie_two = exports / "fanqie/0002-储物柜里的照片.txt"
        for path in (combined, review, epub, fanqie_one, fanqie_two):
            self.assertTrue(path.is_file(), path)

        combined_text = combined.read_text(encoding="utf-8")
        self.assertLess(combined_text.index("第1章 雾港来信"), combined_text.index("第2章 储物柜里的照片"))
        self.assertIn("林某D在旧邮局收到一封匿名来信。", combined_text)
        self.assertIn("她低声说：‘* * *’不是暗号。", combined_text)
        self.assertIn("不是暗号。\n\n* * *\n\n信封里", combined_text)
        self.assertNotIn("**", combined_text)
        self.assertNotIn("https://", combined_text)
        fanqie_text = fanqie_one.read_text(encoding="utf-8")
        self.assertFalse(fanqie_text.startswith("第1章"))
        self.assertIn("她低声说：‘* * *’不是暗号。", fanqie_text)
        self.assertIn("不是暗号。\n\n信封里", fanqie_text)
        self.assertNotRegex(fanqie_text, r"(?m)^\s*\*\s+\*\s+\*\s*$")

        with zipfile.ZipFile(review) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("长夜余烬", document)
            self.assertIn("信封里只有一把钥匙", document)
            self.assertIn("* * *", document)
        with zipfile.ZipFile(epub) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertIn("EPUB/text/chapter-0001.xhtml", archive.namelist())
            chapter_xhtml = archive.read("EPUB/text/chapter-0001.xhtml").decode("utf-8")
            self.assertIn("匿名来信", chapter_xhtml)
            self.assertIn('<p class="scene-break">* * *</p>', chapter_xhtml)

        manifest = read_json(exports / "export-manifest.json")
        self.assertEqual(manifest["source"]["chapter_count"], 2)
        self.assertEqual(manifest["fanqie_title_mode"], "filename_only_body_text")
        self.assertEqual(novel_workspace.project_state_hash(self.root), before_hash)
        after = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and path.parts[len(self.root.parts)] != "exports"
        }
        self.assertEqual(after, before)

    def test_modified_derived_output_requires_force(self) -> None:
        self.export()
        combined = self.root / "exports/《长夜余烬》-全书合并稿.txt"
        write_text(combined, "人工修改的派生稿\n")
        with self.assertRaises(novel_export.ExportError):
            self.export()
        self.export(force=True)
        self.assertIn("雾港来信", combined.read_text(encoding="utf-8"))

    def test_malformed_manifest_returns_a_domain_error(self) -> None:
        self.export()
        manifest_path = self.root / "exports/export-manifest.json"
        manifest = read_json(manifest_path)
        manifest["source"] = []
        write_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        with self.assertRaises(novel_export.ExportError):
            self.export()

    def test_status_reports_stale_after_markdown_changes(self) -> None:
        self.export()
        status, code = novel_export.export_status(SimpleNamespace(root=str(self.root)))
        self.assertEqual(code, 0)
        self.assertEqual(status["status"], "fresh")
        chapter = self.root / "manuscript/chapters/0002-储物柜里的照片.md"
        write_text(
            chapter,
            chapter.read_text(encoding="utf-8") + "\n照片背面写着今天的日期。\n",
        )
        status, code = novel_export.export_status(SimpleNamespace(root=str(self.root)))
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "blocked")
        self.assertTrue(
            any("canonical files changed" in issue for issue in status["issues"])
        )

    def test_selective_export_drops_old_formats_after_source_change(self) -> None:
        self.export()
        chapter = self.root / "manuscript/chapters/0002-储物柜里的照片.md"
        write_text(
            chapter,
            chapter.read_text(encoding="utf-8") + "\n林某D把照片装进证物袋。\n",
        )
        seal_full_baseline(self.root)
        result = self.export(["txt"])
        self.assertEqual(result["formats"], ["txt"])
        exports = self.root / "exports"
        self.assertTrue((exports / "《长夜余烬》-全书合并稿.txt").is_file())
        self.assertFalse((exports / "《长夜余烬》-审阅稿.docx").exists())
        self.assertFalse((exports / "《长夜余烬》.epub").exists())
        self.assertFalse((exports / "fanqie").exists())

    def test_index_order_must_be_ascending(self) -> None:
        index_path = self.root / "manuscript/index.md"
        lines = index_path.read_text(encoding="utf-8").splitlines()
        chapter_rows = [line for line in lines if line.startswith("| 000")]
        other_rows = [line for line in lines if not line.startswith("| 000")]
        write_text(index_path, "\n".join([*other_rows, *reversed(chapter_rows)]) + "\n")
        with self.assertRaises(novel_export.ExportError):
            self.export()

    def test_epub_rejects_broken_manifest_targets(self) -> None:
        self.export(["epub"])
        epub = self.root / "exports/《长夜余烬》.epub"
        broken = self.root / "broken.epub"
        with zipfile.ZipFile(epub) as source, zipfile.ZipFile(
            broken, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "EPUB/package.opf":
                    data = data.replace(
                        b'href="text/chapter-0001.xhtml"',
                        b'href="text/missing.xhtml"',
                    )
                target.writestr(
                    info.filename,
                    data,
                    compress_type=(
                        zipfile.ZIP_STORED
                        if info.filename == "mimetype"
                        else zipfile.ZIP_DEFLATED
                    ),
                )
        with self.assertRaisesRegex(novel_export.ExportError, "manifest target"):
            novel_export.validate_epub(broken, 2)

    def test_epub_rejects_broken_navigation_targets(self) -> None:
        self.export(["epub"])
        epub = self.root / "exports/《长夜余烬》.epub"
        broken = self.root / "broken-navigation.epub"
        with zipfile.ZipFile(epub) as source, zipfile.ZipFile(
            broken, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "EPUB/nav.xhtml":
                    data = data.replace(
                        b'href="text/chapter-0001.xhtml"',
                        b'href="text/missing.xhtml"',
                    )
                target.writestr(
                    info.filename,
                    data,
                    compress_type=(
                        zipfile.ZIP_STORED
                        if info.filename == "mimetype"
                        else zipfile.ZIP_DEFLATED
                    ),
                )
        with self.assertRaisesRegex(novel_export.ExportError, "navigation target"):
            novel_export.validate_epub(broken, 2)

    def test_epub_rejects_references_that_escape_epub_root(self) -> None:
        self.export(["epub"])
        epub = self.root / "exports/《长夜余烬》.epub"
        broken = self.root / "escape.epub"
        with zipfile.ZipFile(epub) as source, zipfile.ZipFile(
            broken, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "EPUB/nav.xhtml":
                    data = data.replace(
                        b'href="text/chapter-0001.xhtml"',
                        b'href="../../outside.xhtml"',
                    )
                target.writestr(
                    info.filename,
                    data,
                    compress_type=(
                        zipfile.ZIP_STORED
                        if info.filename == "mimetype"
                        else zipfile.ZIP_DEFLATED
                    ),
                )
        with self.assertRaisesRegex(novel_export.ExportError, "escapes EPUB"):
            novel_export.validate_epub(broken, 2)

    def test_delivery_text_is_strict_utf8_lf_nfc_and_preserves_valid_unicode(self) -> None:
        chapter = self.root / "manuscript/chapters/0001-雾港来信.md"
        content = (
            "# 第一章 雾港来信\r\n\r\n"
            "Cafe\u0301\u00a0旁坐着程序员👩‍💻，她认得扩展汉字\U00020000。\r\n"
        )
        chapter.write_bytes(content.encode("utf-8"))
        seal_full_baseline(self.root)

        result = self.export(["txt", "fanqie"])
        self.assertEqual(result["status"], "exported")
        exports = self.root / "exports"
        text_paths = [
            exports / "《长夜余烬》-全书合并稿.txt",
            *sorted((exports / "fanqie").glob("*.txt")),
        ]
        for path in text_paths:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(novel_export.UTF8_BOM), path)
            self.assertNotIn(b"\r", raw, path)
            rendered = raw.decode("utf-8", errors="strict")
            self.assertEqual(rendered, unicodedata.normalize("NFC", rendered))
            self.assertNotIn("\u00a0", rendered)
        normalized = text_paths[0].read_text(encoding="utf-8")
        self.assertIn("Café 旁", normalized)
        self.assertIn("👩‍💻", normalized)
        self.assertIn("\U00020000", normalized)

        manifest = read_json(exports / "export-manifest.json")
        delivery = manifest["delivery_quality"]
        self.assertEqual(delivery["status"], "pass")
        self.assertEqual(delivery["failure_policy"], "fail_closed")
        self.assertEqual(manifest["line_endings"], "LF")
        self.assertEqual(manifest["unicode_normalization"], "NFC")
        source_counts = delivery["source_text_gate"]["normalizations_applied"]
        self.assertGreater(source_counts["crlf_to_lf"], 0)
        self.assertGreater(source_counts["horizontal_space_to_ascii"], 0)
        self.assertGreater(source_counts["nfc_normalized_fragments"], 0)
        profiles = {item["name"] for item in delivery["profiles"]}
        self.assertEqual(profiles, {"generic-plain-text", "fanqie-serial"})
        status, code = novel_export.export_status(SimpleNamespace(root=str(self.root)))
        self.assertEqual(code, 0)
        self.assertEqual(status["delivery_quality"], "pass")

    def test_ambiguous_or_dangerous_unicode_blocks_export(self) -> None:
        chapter = self.root / "manuscript/chapters/0001-雾港来信.md"
        dangerous = {
            "interior_bom": "\ufeff",
            "replacement": "\ufffd",
            "nul": "\x00",
            "c0_control": "\x0b",
            "bidi_override": "\u202e",
            "private_use": "\ue000",
            "unassigned": "\u0378",
            "zero_width_space": "\u200b",
        }
        for label, character in dangerous.items():
            with self.subTest(label=label):
                chapter.write_bytes(
                    (
                        "# 第一章 雾港来信\n\n"
                        f"林某D发现异常字符{character}仍夹在正文里。\n"
                    ).encode("utf-8")
                )
                seal_full_baseline(self.root)
                with self.assertRaises(novel_export.ExportError):
                    self.export(["fanqie"])

    def test_status_rechecks_unicode_even_when_manifest_hash_is_updated(self) -> None:
        self.export(["fanqie"])
        exports = self.root / "exports"
        target = exports / "fanqie/0001-雾港来信.txt"
        target.write_text(
            target.read_text(encoding="utf-8").rstrip("\n") + "\u202e\n",
            encoding="utf-8",
            newline="\n",
        )
        manifest_path = exports / "export-manifest.json"
        manifest = read_json(manifest_path)
        record = next(item for item in manifest["outputs"] if item["path"].endswith("0001-雾港来信.txt"))
        record["bytes"] = target.stat().st_size
        record["sha256"] = novel_export.sha256_file(target)
        write_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )

        status, code = novel_export.export_status(SimpleNamespace(root=str(self.root)))
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "modified")
        self.assertTrue(
            any("bidirectional control" in issue for issue in status["issues"]),
            status["issues"],
        )

    def test_delivery_quality_failure_does_not_replace_existing_package(self) -> None:
        self.export(["fanqie"])
        exports = self.root / "exports"
        before = {
            path.relative_to(exports).as_posix(): path.read_bytes()
            for path in exports.rglob("*")
            if path.is_file()
        }
        chapter = self.root / "manuscript/chapters/0001-雾港来信.md"
        write_text(
            chapter,
            "# 第一章 雾港来信\n\n雾港来信\n\n林某D拆开了信封。\n",
        )
        seal_full_baseline(self.root)
        with self.assertRaisesRegex(novel_export.ExportError, "duplicates"):
            self.export(["fanqie"])
        after = {
            path.relative_to(exports).as_posix(): path.read_bytes()
            for path in exports.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_unmanaged_file_prevents_a_clean_delivery_status(self) -> None:
        self.export(["fanqie"])
        extra = self.root / "exports/Thumbs.db"
        extra.write_bytes(b"not part of the delivery package")
        status, code = novel_export.export_status(SimpleNamespace(root=str(self.root)))
        self.assertEqual(code, 1)
        self.assertEqual(status["status"], "modified")
        self.assertTrue(any("unmanaged files" in issue for issue in status["issues"]))
        with self.assertRaisesRegex(novel_export.ExportError, "unmanaged files"):
            self.export(["fanqie"])


if __name__ == "__main__":
    unittest.main()
