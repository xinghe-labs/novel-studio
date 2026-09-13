#!/usr/bin/env python3
"""Shared command-line contracts for the novel-studio tools.

The workflow scripts are intentionally stdlib-only.  Keeping their command-line
error handling here makes direct invocation behave the same on UTF-8 and legacy
Windows consoles without coupling the domain modules to one another.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


TOOL_VERSION = "2.2.0"


class CliUsageError(RuntimeError):
    """Raised for invalid command-line arguments that should return exit code 2."""


class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that lets the top-level runner serialize usage errors."""

    def error(self, message: str) -> None:
        raise CliUsageError(message)


def configure_utf8_streams() -> None:
    """Make JSON output safe for Chinese text on Windows consoles.

    ``backslashreplace`` is deliberately used as a last-resort fallback.  It
    preserves a valid JSON response when a caller has forced an incompatible
    console encoding instead of emitting a traceback halfway through a result.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            # Embedded runners may expose a stream that cannot be reconfigured.
            continue


def atomic_write_bytes(path: str | Path, content: bytes) -> None:
    """Replace a regular output file after flushing a sibling temporary file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_text(path: str | Path, content: str) -> None:
    """Atomically replace a text file with the provided content as UTF-8."""

    atomic_write_bytes(path, content.encode("utf-8"))


def atomic_create_bytes(path: str | Path, content: bytes) -> tuple[int, int]:
    """Create a new file without exposing partial content or replacing a peer.

    The completed sibling temporary file is installed with an atomic hard-link
    operation.  A concurrent creator therefore wins with ``FileExistsError``
    instead of being overwritten.  If an interrupt lands after the link was
    installed, cleanup removes the target only while it still has the identity
    of this attempt; a concurrent replacement is never unlinked.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    identity: tuple[int, int] | None = None
    installed = False
    try:
        with handle:
            opened = os.fstat(handle.fileno())
            identity = (opened.st_dev, opened.st_ino)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_path, target)
        installed = True
        return identity
    except BaseException as exc:
        # ``os.link`` can be interrupted after the filesystem operation has
        # completed.  Reconcile by identity, never by pathname alone.
        # A reported existence conflict always belongs to the other creator,
        # even in the pathological case where it linked our discoverable temp
        # file first.
        if identity is not None and not isinstance(exc, FileExistsError):
            try:
                current = target.stat(follow_symlinks=False)
                if stat.S_ISREG(current.st_mode) and (
                    current.st_dev,
                    current.st_ino,
                ) == identity:
                    target.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        # Keep control-flow exceptions observable.  A cleanup failure may be
        # ignored for a fully installed hard link, but ``KeyboardInterrupt``
        # and ``SystemExit`` must never be swallowed while doing so.
        outer_exception_active = sys.exc_info()[0] is not None
        try:
            temp_path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except (KeyboardInterrupt, SystemExit):
            raise
        except OSError:
            # Once installed, the target is a complete, fsynced hard link.
            # A stale hidden link is cleanup debt, not grounds to remove or
            # corrupt the completed output during exception unwinding.  Before
            # installation, preserve an already-active failure but surface a
            # standalone cleanup failure to the caller.
            if not installed and not outer_exception_active:
                raise


def atomic_create_text(path: str | Path, content: str) -> tuple[int, int]:
    """Create a new UTF-8 text file with ``atomic_create_bytes`` semantics."""

    return atomic_create_bytes(path, content.encode("utf-8"))


def add_common_options(parser: argparse.ArgumentParser) -> None:
    """Add options shared by every executable tool."""

    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the installed novel-studio tool version as JSON.",
    )


def emit_json(result: Any) -> bool:
    """Write one complete JSON document; return ``False`` on serialization failure."""

    serialization_failed = False
    try:
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    except Exception as exc:
        # A buggy adapter must still produce the machine-readable error
        # envelope promised by every executable tool.
        serialization_failed = True
        text = json.dumps(
            {
                "status": "error",
                "error": f"Unable to serialize command result: {type(exc).__name__}",
                "error_type": "SerializationError",
                "recoverable": False,
            },
            ensure_ascii=True,
            indent=2,
        ) + "\n"
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        # A caller may replace stdout after configure_utf8_streams().
        try:
            fallback = json.dumps(result, ensure_ascii=True, indent=2) + "\n"
        except Exception as exc:
            serialization_failed = True
            fallback = json.dumps(
                {
                    "status": "error",
                    "error": f"Unable to serialize command result: {type(exc).__name__}",
                    "error_type": "SerializationError",
                    "recoverable": False,
                },
                ensure_ascii=True,
                indent=2,
            ) + "\n"
        sys.stdout.write(fallback)
        sys.stdout.flush()
    return not serialization_failed


def _split_result(value: Any) -> tuple[Any, int]:
    """Accept the existing ``result`` or ``(result, status_code)`` convention."""

    if (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[1], int)
    ):
        return value[0], value[1]
    return value, 0


def run_cli(
    build_parser: Callable[[], argparse.ArgumentParser],
    dispatch: Callable[[argparse.Namespace], Any],
    *,
    tool_name: str,
    domain_errors: Iterable[type[BaseException]] = (),
    operational_errors: Iterable[type[BaseException]] = (),
) -> int:
    """Run a tool and keep its stdout/error/exit-code contract machine-readable.

    Domain and operational errors retain the historical exit code ``2``.  An
    otherwise unhandled exception returns ``3`` with its type and message but
    no traceback, so agents can recover from a stable JSON envelope.
    """

    configure_utf8_streams()
    domain_error_types = tuple(domain_errors)
    operational_error_types = tuple(operational_errors)
    # Most tools expose their own domain exception, while write authorization
    # is shared by all of them.  Load that exception lazily to avoid the
    # novel_project <-> novel_workspace import cycle, and keep authorization
    # failures on the documented JSON/exit-code-2 path.
    try:
        import novel_workspace

        workspace_error_types: tuple[type[BaseException], ...] = (
            novel_workspace.WorkspaceError,
        )
    except (ImportError, OSError):
        workspace_error_types = ()
    domain_error_types = tuple(
        dict.fromkeys((*domain_error_types, *workspace_error_types))
    )
    try:
        # Required subparsers would otherwise reject the useful standalone
        # ``tool --version`` form before argparse can inspect the flag.
        if sys.argv[1:] == ["--version"]:
            emit_json(
                {"status": "ok", "tool": tool_name, "version": TOOL_VERSION}
            )
            return 0
        args = build_parser().parse_args()
        if getattr(args, "version", False):
            result, code = (
                {
                    "status": "ok",
                    "tool": tool_name,
                    "version": TOOL_VERSION,
                },
                0,
            )
        else:
            result, code = _split_result(dispatch(args))
    except SystemExit as exc:
        # argparse uses SystemExit for --help.  Help is intentionally human
        # readable; invalid arguments are routed through JsonArgumentParser.
        return int(exc.code or 0)
    except CliUsageError as exc:
        result, code = (
            {"status": "error", "error": str(exc), "error_type": "usage"},
            2,
        )
    except domain_error_types + operational_error_types as exc:
        result, code = (
            {"status": "error", "error": str(exc), "error_type": type(exc).__name__},
            2,
        )
    except Exception as exc:  # pragma: no cover - exercised by CLI smoke tests
        result, code = (
            {
                "status": "error",
                "error": str(exc) or "Unhandled tool error",
                "error_type": type(exc).__name__,
                "recoverable": False,
            },
            3,
        )
    if not emit_json(result):
        return 3
    return code
