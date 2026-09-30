"""Check the repository statistics stated in README.md against the tree.

README.md states three counts: lines of runtime Python, lines of tests, and
the number of progressive-disclosure reference documents.  They drift
silently whenever a tool grows or a reference file is added, so CI recomputes
them here and fails while the stated numbers disagree.

The three numbers are read back with one tolerant pattern, so the surrounding
wording and emphasis can change without breaking the check:

    python -X utf8 ci/check_doc_stats.py           # compare with README.md
    python -X utf8 ci/check_doc_stats.py --print   # print the current values

Lines are counted as newline characters, matching ``wc -l``: a final line
without a trailing newline is not counted.  Test files are taken from
``git ls-files`` (falling back to a directory walk outside a git checkout).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
SCRIPTS = REPO / "scripts"
TESTS = REPO / "tests"
REFERENCES = REPO / "references"

# One pattern for the README sentence.  Emphasis markers, spacing, and the
# connecting words may change; the three numbers and their units may not.
STATS_PATTERN = re.compile(
    r"(?P<runtime>[\d,]+)\s*(?:\*\*)?\s*lines?\s*(?:\*\*)?\s*of\s+runtime\s+Python"
    r".{0,200}?"
    r"(?P<tests>[\d,]+)\s*(?:\*\*)?\s*lines?\s*(?:\*\*)?\s*of\s+tests?"
    r".{0,200}?"
    r"(?P<references>[\d,]+)\s*(?:\*\*)?\s*progressive-disclosure\s+"
    r"reference\s+documents?",
    re.IGNORECASE | re.DOTALL,
)


def count_lines(path: Path) -> int:
    """Count newlines in one file without decoding its bytes."""

    return path.read_bytes().count(b"\n")


def tracked_test_files() -> list[Path]:
    """Return the tracked ``tests/**/*.py`` files in stable order."""

    try:
        listing = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z", "--", "tests"],
            capture_output=True,
        )
    except OSError:
        listing = None
    if listing is not None and listing.returncode == 0:
        names = listing.stdout.decode("utf-8", "replace").split("\0")
        return sorted(
            REPO / name
            for name in names
            if name.endswith(".py") and (REPO / name).is_file()
        )
    return sorted(
        path for path in TESTS.rglob("*.py") if "__pycache__" not in path.parts
    )


def current_stats() -> dict[str, int]:
    """Recompute the three README statistics from the working tree."""

    return {
        "runtime_python_lines": sum(
            count_lines(path) for path in sorted(SCRIPTS.glob("*.py"))
        ),
        "test_lines": sum(count_lines(path) for path in tracked_test_files()),
        "reference_documents": len(sorted(REFERENCES.glob("*.md"))),
    }


def stated_stats() -> dict[str, int] | None:
    """Read the three numbers back out of README.md."""

    match = STATS_PATTERN.search(README.read_text(encoding="utf-8"))
    if match is None:
        return None
    keys = {
        "runtime": "runtime_python_lines",
        "tests": "test_lines",
        "references": "reference_documents",
    }
    return {
        keys[name]: int(match.group(name).replace(",", "")) for name in keys
    }


def main() -> int:
    stats = current_stats()
    if "--print" in sys.argv[1:]:
        for name, value in stats.items():
            print(f"{name}={value}")
        return 0

    stated = stated_stats()
    if stated is None:
        print("README.md: statistics sentence not found; cannot compare")
        return 1
    if stated == stats:
        print("README.md statistics match the repository:")
        for name, value in stats.items():
            print(f"  {name}: {value}")
        return 0

    print("README.md statistics do not match the repository:")
    for name, value in stats.items():
        print(f"  {name}: README says {stated[name]}, actual {value}")
    print("Refresh the numbers in README.md from:")
    print("  python -X utf8 ci/check_doc_stats.py --print")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
