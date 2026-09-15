"""Scope checks must compare canonical path forms.

On Windows, ``Path.resolve()`` expands 8.3 short names (``RUNNER~1`` becomes
``runneradmin``) and rewrites other non-canonical components. A containment
check that resolves the child but compares it against the root *as given* then
concludes the child sits outside the project, and existing canonical sources
are reported as missing:

    Canonical source is missing or out of scope: continuity/canon-facts.jsonl

These tests pin the canonical-form comparison at the helper boundary and at
``canonical_snapshot``, the site that surfaced the failure.
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import novel_continuity  # noqa: E402


def short_path_name(path: Path) -> Path | None:
    """Return the 8.3 short form of ``path``, or None when unavailable.

    Short-name generation can be disabled per volume, so callers skip rather
    than fail when no distinct short form is produced.
    """

    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        return None
    candidate = Path(buffer.value)
    return candidate if candidate != path else None


class CanonicalScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "project"
        (self.root / "continuity").mkdir(parents=True)
        (self.root / "novel.json").write_text("{}\n", encoding="utf-8")
        (self.root / "continuity" / "canon-facts.jsonl").write_text(
            "", encoding="utf-8"
        )

    def non_canonical_root(self) -> Path:
        short = short_path_name(self.root)
        if short is None:
            self.skipTest("no distinct 8.3 short name is available for this path")
        return short

    def test_is_within_stays_lexical(self) -> None:
        short = self.non_canonical_root()
        child = (self.root / "continuity" / "canon-facts.jsonl").resolve()

        # The helper itself is deliberately a plain lexical comparison; that is
        # why each scope check has to hand it canonical forms.
        self.assertFalse(novel_continuity.is_within(child, short))
        self.assertTrue(novel_continuity.is_within(child, short.resolve()))

    def test_canonical_snapshot_accepts_non_canonical_root(self) -> None:
        expected_snapshot, expected_digest = novel_continuity.canonical_snapshot(
            self.root
        )

        snapshot, digest = novel_continuity.canonical_snapshot(
            self.non_canonical_root()
        )

        self.assertTrue(snapshot)
        self.assertEqual(snapshot, expected_snapshot)
        self.assertEqual(digest, expected_digest)

    def test_canonical_snapshot_rejects_source_outside_root(self) -> None:
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir()
        (outside / "novel.json").write_text("{}\n", encoding="utf-8")

        snapshot, _ = novel_continuity.canonical_snapshot(outside)
        self.assertEqual([entry["path"] for entry in snapshot], ["novel.json"])

        self.assertFalse(
            novel_continuity.is_within(
                (outside / "novel.json").resolve(), self.root.resolve()
            )
        )


if __name__ == "__main__":
    unittest.main()