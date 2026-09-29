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


if __name__ == "__main__":
    unittest.main()
