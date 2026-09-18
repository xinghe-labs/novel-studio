#!/usr/bin/env python3
"""Install the novel-studio skill into an agent-host skill directory.

This is a human-facing helper, not part of the JSON CLI contract: it prints
plain text.  Standard library only; run it from a clone or an extracted
release archive of this repository.

    python install.py [--root DIR] [--all] [--force]

The vendored ``humanizer-zh/`` skill ships inside the installed skill root,
so one install satisfies the declared dependency without any separate
registration.  ``NOVEL_HUMANIZER_PATH`` keeps overriding the bundled copy,
exactly as documented for ``doctor``.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

SKILL_NAME = "novel-studio"

SKIP_DIRECTORIES = {".git", "__pycache__", ".pytest_cache", ".agent-handoff"}


class InstallError(Exception):
    """A known, user-facing installation problem (exit code 2)."""


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            # Embedded runners may expose a stream that cannot be reconfigured.
            pass


def export_ignore_prefixes(source_root: Path) -> list[str]:
    """Parse the ``export-ignore`` patterns that keep releases controlled."""

    attributes = source_root / ".gitattributes"
    if not attributes.is_file():
        return []
    prefixes: list[str] = []
    for line in attributes.read_text(encoding="utf-8", errors="replace").splitlines():
        tokens = line.split()
        if len(tokens) >= 2 and tokens[-1] == "export-ignore":
            pattern = tokens[0].strip("/")
            if pattern:
                prefixes.append(pattern)
    return prefixes


def is_export_ignored(relative: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        prefix = pattern if pattern.endswith("/") else f"{pattern}/"
        if relative.startswith(prefix) or fnmatch.fnmatch(relative, pattern):
            return True
    return False


def collect_source_files(source_root: Path) -> list[Path]:
    """Return the controlled distribution files, release-archive semantics."""

    patterns = export_ignore_prefixes(source_root)
    files: list[Path] = []

    git_listing = subprocess.run(
        ["git", "-C", str(source_root), "ls-files", "-z"],
        capture_output=True,
    )
    if git_listing.returncode == 0 and git_listing.stdout:
        names = [
            name
            for name in git_listing.stdout.decode("utf-8", "replace").split("\0")
            if name
        ]
        for name in names:
            relative = PurePosixPath(name).as_posix()
            if is_export_ignored(relative, patterns):
                continue
            candidate = source_root.joinpath(*relative.split("/"))
            if candidate.is_file():
                files.append(candidate)
        return files

    # No git (or git failed): a plain walk with the same exclusions.  Release
    # archives already had export-ignored paths removed upstream; the walk
    # still filters so a full source checkout behaves identically.
    for directory, dirnames, filenames in os.walk(source_root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRECTORIES)
        base = Path(directory).relative_to(source_root)
        for filename in sorted(filenames):
            if base == Path(".") and filename == "AGENTS.md":
                continue
            candidate = Path(directory) / filename
            relative = candidate.relative_to(source_root).as_posix()
            if is_export_ignored(relative, patterns):
                continue
            files.append(candidate)
    return files


def read_tool_version(source_root: Path) -> str:
    cli_source = (source_root / "scripts" / "novel_cli.py").read_text(
        encoding="utf-8", errors="replace"
    )
    match = re.search(r'(?m)^TOOL_VERSION\s*=\s*"([^"]+)"', cli_source)
    return match.group(1) if match else "unknown"


def skill_frontmatter_name(skill_file: Path) -> str | None:
    try:
        content = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    frontmatter = re.match(
        r"\A---\s*\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|\Z)", content, re.DOTALL
    )
    if frontmatter is None:
        return None
    match = re.search(
        r"(?m)^\s*name\s*:\s*['\"]?([\w.-]+)['\"]?\s*$",
        frontmatter.group("body"),
    )
    return match.group(1) if match else None


def default_skill_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".agents" / "skills",
        home / ".codex" / "skills",
    ]


def select_roots(explicit_root: str | None, install_all: bool) -> list[Path]:
    if explicit_root:
        root = Path(explicit_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return [root]
    detected = [root for root in default_skill_roots() if root.is_dir()]
    if install_all:
        if not detected:
            raise InstallError(
                "no skill root found; expected one of: "
                + ", ".join(str(root) for root in default_skill_roots())
            )
        return detected
    if detected:
        return detected[:1]
    created = default_skill_roots()[0]
    created.mkdir(parents=True, exist_ok=True)
    print(f"no skill root found; created {created}")
    return [created]


def install_into(
    root: Path,
    source_root: Path,
    files: list[Path],
    force: bool,
) -> tuple[Path, str, int]:
    target = root / SKILL_NAME
    if target.exists() and not target.is_dir():
        raise InstallError(f"target exists and is not a directory: {target}")
    if target.exists() and target.resolve() == source_root.resolve():
        raise InstallError(
            f"{target} is the copy this installer runs from; "
            "re-run from a fresh clone or pass --root elsewhere"
        )

    action = "installed"
    if target.exists():
        existing_name = skill_frontmatter_name(target / "SKILL.md")
        if existing_name != SKILL_NAME:
            if not force:
                raise InstallError(
                    f"{target} exists and is not a {SKILL_NAME} install; "
                    "pass --force to replace it"
                )
            print(f"replacing foreign directory at {target} (--force)")
        action = "updated"

    staging = root / f".{SKILL_NAME}-install-{os.getpid()}"
    backup = root / f".{SKILL_NAME}-old-{os.getpid()}"
    for stale in (staging, backup):
        if stale.exists():
            shutil.rmtree(stale)

    copied = 0
    staging.mkdir(parents=True)
    try:
        for source in files:
            relative = source.relative_to(source_root)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
        had_old = target.exists()
        if had_old:
            os.rename(target, backup)
        try:
            os.rename(staging, target)
        except OSError:
            if backup.exists():
                os.rename(backup, target)
            raise
    except OSError as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise InstallError(f"copy failed: {exc}") from exc
    finally:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    return target, action, copied


def run_tool(script_path: Path, *args: str) -> tuple[int, dict | None]:
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(script_path), *args],
        capture_output=True,
    )
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return result.returncode, payload


def verify_install(target: Path) -> int:
    version_code, version_payload = run_tool(
        target / "scripts" / "novel_workspace.py", "--version"
    )
    if version_code != 0 or not isinstance(version_payload, dict):
        print("version check: FAILED (tool did not return a valid JSON version)")
        return 1
    print(f"version check: ok ({version_payload.get('version')})")

    _, doctor_payload = run_tool(target / "scripts" / "novel_workspace.py", "doctor")
    if not isinstance(doctor_payload, dict):
        print("doctor: FAILED (no parseable JSON output)")
        return 1
    failed = [
        check.get("name", "?")
        for check in doctor_payload.get("checks", [])
        if check.get("status") == "fail"
    ]
    status = doctor_payload.get("status")
    print(f"doctor: {status}")
    if status != "pass":
        if failed:
            print("failing checks: " + ", ".join(failed))
        print(
            "formal commits stay blocked until doctor reports pass; "
            "set NOVEL_HUMANIZER_PATH to override the bundled humanizer-zh copy"
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description=(
            f"Install the {SKILL_NAME} skill (with its bundled humanizer-zh "
            "copy) into an agent-host skill directory."
        ),
    )
    parser.add_argument(
        "--root",
        help="explicit skill root to install into (created when missing)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="install into every detected skill root instead of the first one",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing directory even when it is not a "
        "novel-studio install",
    )
    args = parser.parse_args()

    source_root = Path(__file__).resolve().parent
    files = collect_source_files(source_root)
    if not files:
        raise InstallError("no distribution files found next to install.py")
    version = read_tool_version(source_root)
    print(f"source: {source_root} (v{version}, {len(files)} controlled files)")

    try:
        roots = select_roots(args.root, args.all)
        exit_code = 0
        for root in roots:
            target, action, copied = install_into(root, source_root, files, args.force)
            print(f"{action}: {target} ({copied} files)")
            if verify_install(target) != 0:
                exit_code = 1
        return exit_code
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    _configure_utf8_streams()
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - installer must report, not traceback
        print(f"unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(3)
