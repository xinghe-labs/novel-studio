"""Temporary diagnostic for the Windows-only canonical scope failure.

The continuity fixture tests fail on the GitHub Windows runner with:

    Canonical source is missing or out of scope: continuity/canon-facts.jsonl

raised from `canonical_snapshot`. That is contradictory on its face, because
`canonical_relative_paths` only adds a path when `(root / relative).is_file()`
is true, and `canonical_snapshot` walks the list it returns.

This script runs one of the failing tests with `canonical_snapshot`
instrumented, printing both sides of every decision that could produce the
error, so the disagreement is visible instead of inferred. It runs on Linux
and Windows in CI so the two environments can be compared directly.

Not part of the distributable skill. Remove once the cause is understood.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
TESTS = REPO / "tests"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import novel_continuity as nc  # noqa: E402

TARGET = (
    "test_continuity_gate.ContinuityGateTests."
    "test_fifth_chapter_blocks_next_context_and_delivery_until_global_review"
)


def show(label: str, value: object) -> None:
    print(f"{label:<36}: {value!r}")


def probe_environment() -> None:
    print("=" * 72)
    print("environment")
    print("=" * 72)
    show("sys.platform", sys.platform)
    show("python", sys.version.split()[0])
    show("tempfile.gettempdir()", tempfile.gettempdir())

    probe = Path(tempfile.mkdtemp(prefix="scope-probe-"))
    resolved = probe.resolve()
    show("mkdtemp raw", str(probe))
    show("mkdtemp resolved", str(resolved))
    show("raw == resolved (str)", str(probe) == str(resolved))
    show("raw == resolved (path)", probe == resolved)
    show("repr identical", repr(str(probe)) == repr(str(resolved)))

    sample = probe / "continuity" / "canon-facts.jsonl"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("{}", encoding="utf-8")
    sample_resolved = sample.resolve()
    show("sample raw", str(sample))
    show("sample resolved", str(sample_resolved))
    show("sample raw is_file", sample.is_file())
    show("sample resolved is_file", sample_resolved.is_file())
    show("is_within(resolved, root)", nc.is_within(sample_resolved, probe))
    try:
        show("_path_chain_has_link(sample)", nc._path_chain_has_link(sample))
    except Exception as exc:  # diagnostic only
        show("_path_chain_has_link raised", f"{type(exc).__name__}: {exc}")


def run_traced() -> bool:
    print()
    print("=" * 72)
    print("traced canonical_snapshot calls")
    print("=" * 72)

    original = nc.canonical_snapshot

    def traced(root, overrides=None):
        relative = nc.FACTS_PATH
        raw_path = root / relative
        print("-" * 72)
        show("root as passed", str(root))
        try:
            root_resolved = root.resolve()
        except OSError as exc:
            show("root.resolve raised", f"{type(exc).__name__}: {exc}")
            root_resolved = None
        if root_resolved is not None:
            show("root.resolve()", str(root_resolved))
            show("root str differs", str(root) != str(root_resolved))
            show("root path differs", root != root_resolved)

        show("facts relative", relative)
        show("facts raw", str(raw_path))
        show("facts raw is_file", raw_path.is_file())
        try:
            included = relative in nc.canonical_relative_paths(root, {})
            show("in canonical_relative_paths", included)
        except Exception as exc:
            show("canonical_relative_paths raised", f"{type(exc).__name__}: {exc}")
        try:
            resolved = raw_path.resolve()
        except OSError as exc:
            show("facts resolve raised", f"{type(exc).__name__}: {exc}")
            resolved = None
        if resolved is not None:
            show("facts resolved", str(resolved))
            show("facts resolved is_file", resolved.is_file())
            show("is_within(resolved, root)", nc.is_within(resolved, root))
            if root_resolved is not None:
                show(
                    "is_within(resolved, resolved root)",
                    nc.is_within(resolved, root_resolved),
                )
            show("raw parent chain same", resolved.parent == raw_path.parent)

        try:
            result = original(root, overrides)
        except Exception as exc:
            show("canonical_snapshot raised", f"{type(exc).__name__}: {exc}")
            show("overrides", None if overrides is None else sorted(overrides))
            continuity_dir = root / "continuity"
            show("continuity is_dir", continuity_dir.is_dir())
            try:
                entries = sorted(item.name for item in continuity_dir.iterdir())
            except OSError as listing_exc:
                entries = f"unreadable {type(listing_exc).__name__}: {listing_exc}"
            show("continuity entries", entries)
            raise
        show("canonical_snapshot", "ok")
        return result

    nc.canonical_snapshot = traced
    try:
        suite = unittest.TestLoader().loadTestsFromName(TARGET)
        return unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()
    finally:
        nc.canonical_snapshot = original


def main() -> int:
    probe_environment()
    passed = run_traced()
    print()
    print("=" * 72)
    print(f"target test passed: {passed}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
