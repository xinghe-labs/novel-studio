from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_memory  # noqa: E402


def build_minimal_project(base: Path) -> Path:
    """A project tree with just enough canonical files for the memory cache."""

    root = base / "project"
    (root / "manuscript" / "chapters").mkdir(parents=True)
    (root / "memory" / "chapters").mkdir(parents=True)
    (root / "continuity").mkdir(parents=True)
    (root / "novel.json").write_text(
        json.dumps({"schema_version": 1, "title": "雾港"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "manuscript" / "chapters" / "0001-雾港来信.md").write_text(
        "# 第0001章 雾港来信\n\n"
        "林某D在旧邮局收到信，信封上有周槐的字迹。\n\n"
        "人物：林某D\n地点：旧邮局\n线索：T-001\n",
        encoding="utf-8",
    )
    (root / "memory" / "chapters" / "0001.md").write_text(
        "# 第0001章记忆卡\n\n- 正文路径：`manuscript/chapters/0001-雾港来信.md`\n",
        encoding="utf-8",
    )
    (root / "memory" / "book-summary.md").write_text(
        "# 全书摘要\n\n承诺：破谜。\n",
        encoding="utf-8",
    )
    (root / "continuity" / "state.json").write_text(
        json.dumps({"through_chapter": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def search_args(root: Path, query: str, **overrides) -> SimpleNamespace:
    values = {
        "root": str(root),
        "query": query,
        "mode": "phrase",
        "entity_type": None,
        "limit": 20,
        "require_fresh": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MemoryCacheTests(unittest.TestCase):
    def test_rebuild_status_and_search_on_minimal_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            rebuild = novel_memory.rebuild_index(root)
            self.assertEqual(rebuild["status"], "rebuilt")
            self.assertEqual(novel_memory.database_status(root)["status"], "fresh")
            result, code = novel_memory.search_index(search_args(root, "雾港来信"))
            self.assertEqual(code, 0)
            hits = json.dumps(result, ensure_ascii=False)
            self.assertIn("0001-雾港来信.md", hits)
            chapter_rows, entity_code = novel_memory.search_index(
                search_args(root, "林某D", entity_type="character")
            )
            self.assertEqual(entity_code, 0)
            self.assertIn("林某D", json.dumps(chapter_rows, ensure_ascii=False))

    def test_entities_come_only_from_explicit_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            novel_memory.rebuild_index(root)
            # 周槐 only appears in prose without an explicit label; the extractor
            # must not invent an entity from narrative mention.
            character_rows, _ = novel_memory.search_index(
                search_args(root, "周槐", entity_type="character")
            )
            self.assertEqual(character_rows["results"], [])

    def test_require_fresh_refuses_stale_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            novel_memory.rebuild_index(root)
            (root / "manuscript" / "chapters" / "0002-第二座停钟.md").write_text(
                "# 第0002章 第二座停钟\n\n人物：赵十千\n",
                encoding="utf-8",
            )
            status = novel_memory.database_status(root)
            self.assertTrue(status["stale"])
            self.assertTrue(
                any(path.endswith("0002-第二座停钟.md") for path in status["changed"])
            )
            with self.assertRaisesRegex(novel_memory.MemoryIndexError, "stale"):
                novel_memory.search_index(search_args(root, "停钟", require_fresh=True))
            # Without --require-fresh the search still runs against the stale
            # index (which only knows chapter 0001) and flags the staleness.
            result, code = novel_memory.search_index(search_args(root, "雾港来信"))
            self.assertEqual(code, 0)
            self.assertTrue(result["index_stale"])
            self.assertGreaterEqual(result["matches"], 1)

    def test_corrupted_database_is_reported_and_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            novel_memory.rebuild_index(root)
            database = root / novel_memory.DB_RELATIVE
            database.write_bytes(b"this is not a sqlite database")
            status = novel_memory.database_status(root)
            self.assertEqual(status["status"], "invalid")
            self.assertTrue(status["stale"])
            with self.assertRaisesRegex(novel_memory.MemoryIndexError, "unavailable"):
                novel_memory.search_index(search_args(root, "雾港"))
            novel_memory.rebuild_index(root)
            self.assertEqual(novel_memory.database_status(root)["status"], "fresh")
            result, code = novel_memory.search_index(search_args(root, "雾港来信"))
            self.assertEqual(code, 0)
            self.assertIn("0001-雾港来信.md", json.dumps(result, ensure_ascii=False))

    def test_cache_lock_recovers_from_garbage_lock_file(self) -> None:
        """A writer killed between creating and filling the lock file must not
        permanently wedge rebuild/update behind "Malformed memory-cache lock"."""

        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            cache_dir = root / ".novel-cache"
            cache_dir.mkdir()
            (cache_dir / novel_memory.CACHE_LOCK_NAME).write_bytes(b"")
            rebuild = novel_memory.rebuild_index(root)
            self.assertEqual(rebuild["status"], "rebuilt")
            self.assertFalse((cache_dir / novel_memory.CACHE_LOCK_NAME).exists())

    def test_cache_pid_probe(self) -> None:
        self.assertIs(novel_memory._cache_pid_alive(os.getpid()), True)
        # A pid far beyond the platform range cannot exist on any host.
        self.assertIs(novel_memory._cache_pid_alive(999_999_999), False)

    def test_fts_table_is_maintained_across_rebuild_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            novel_memory.rebuild_index(root)
            database = root / novel_memory.DB_RELATIVE
            connection = __import__("sqlite3").connect(database)
            try:
                chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                fts = connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
                self.assertEqual(chunks, fts)
            finally:
                connection.close()
            (root / "manuscript" / "chapters" / "0002-第二座停钟.md").write_text(
                "# 第二座停钟\n\n人物：赵十千\n",
                encoding="utf-8",
            )
            novel_memory.update_index(root)
            connection = __import__("sqlite3").connect(database)
            try:
                chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                fts = connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
                self.assertEqual(chunks, fts)
            finally:
                connection.close()

    def test_fts_prefilter_matches_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            (root / "manuscript" / "chapters" / "0002-第二座停钟.md").write_text(
                "# 第二座停钟\n\n人物：赵十千\n",
                encoding="utf-8",
            )
            novel_memory.rebuild_index(root)
            result, code = novel_memory.search_index(
                search_args(root, "第二座停钟")
            )
            self.assertEqual(code, 0)
            fts_paths = [item["path"] for item in result["results"]]
            self.assertTrue(fts_paths)
            with mock.patch.object(novel_memory, "_FTS5_CACHE", False):
                fallback, fallback_code = novel_memory.search_index(
                    search_args(root, "第二座停钟")
                )
            self.assertEqual(fallback_code, 0)
            self.assertEqual(
                [item["path"] for item in fallback["results"]], fts_paths
            )

    def test_short_phrase_falls_back_to_full_scan(self) -> None:
        """Trigram FTS cannot match 1-2 character queries; the legacy scan must
        still find them."""

        with tempfile.TemporaryDirectory() as temp:
            root = build_minimal_project(Path(temp))
            (root / "manuscript" / "chapters" / "0002-第二座停钟.md").write_text(
                "# 第二座停钟\n\n人物：赵十千\n",
                encoding="utf-8",
            )
            novel_memory.rebuild_index(root)
            result, code = novel_memory.search_index(search_args(root, "停钟"))
            self.assertEqual(code, 0)
            self.assertEqual(result["matches"], 1)
            self.assertEqual(
                result["results"][0]["path"],
                "manuscript/chapters/0002-第二座停钟.md",
            )


if __name__ == "__main__":
    unittest.main()
