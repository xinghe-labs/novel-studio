# novel-studio

[![CI](https://github.com/xinghe-labs/novel-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/xinghe-labs/novel-studio/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](requirements.txt)

**A zero-dependency Python workflow engine for writing long-form fiction with an AI agent — built around transactional canonical writes, single-writer leases, and content-hash quality gates.**

[中文说明](README.zh-CN.md)

---

## The problem

Writing a 100+ chapter novel with an agent is not a "generate text" problem, it is a **state management** problem:

- The story bible, continuity facts, foreshadowing state, and outline no longer fit in a single context window, so the agent must read and write durable state across sessions.
- Drafting, review, and export run as **continuous, restartable, occasionally concurrent** processes. A naive file write silently clobbers whatever another session just committed.
- Review results are only meaningful if they describe the *current* text. A continuity audit that was run against chapter 40 says nothing about chapter 41, yet nothing about a filename suggests it went stale.
- A "pass" from a script is easy to overstate. An agent that reports success on contradictory output is worse than one that fails.

`novel-studio` treats the manuscript as a database with a transaction log rather than a folder of files.

## What it does

It provides nine standard-library CLI tools that an agent host drives through a single documented JSON contract:

| Tool | Responsibility |
|---|---|
| `novel_workspace.py` | Workspace lifecycle, leases, write checks, environment `doctor` |
| `novel_project.py` | Project contracts, framework sync, transactional `commit-chapter` |
| `novel_continuity.py` | Continuity baselines and gate evaluation |
| `novel_review.py` | Periodic quality review and gate status |
| `novel_originality.py` | Originality audit (wording overlap + structural mapping) |
| `novel_research.py` | Source registration, rights scope, access-control boundaries |
| `novel_memory.py` | Long-term memory retrieval and index caching |
| `novel_export.py` | DOCX / EPUB export with structural verification |
| `novel_cli.py` | Shared CLI contract, atomic file primitives, version source |

**18,577 lines** of runtime Python, **7,246 lines** of tests, and **22 progressive-disclosure reference documents**, with no third-party runtime dependency.

## Engineering highlights

These are the parts worth reading if you are evaluating the code rather than the novel.

**Transactional canonical writes with byte-fidelity rollback.** `commit-chapter` writes a persistent journal, verifies a compare-and-swap checksum, and rolls back on any failure. Keyboard interrupt or process exit mid-commit cannot leave an unrecoverable half-written chapter. Fault-injection tests assert byte-exact restoration.

**Single-writer leases with heartbeat liveness.** Formal writes require a lease bound to a workspace and work identity. `lock-break` refuses to force a live lease and requires an explicit expected owner plus a non-empty reason; every reclaim is recorded in an append-only `lease_events` audit table along with the pre-reclaim project hash.

**Content-hash gated review artifacts.** Changing text, context, or state invalidates the prior continuity, originality, and quality reports. Staleness is detected by hash, so an agent cannot revive a stale approval by editing a report's metadata field.

**Fail-closed CLI contract.** Every tool emits exactly one JSON document on stdout, keeps stderr empty, and uses defined exit codes: `0` success, `1` business gate not passed, `2` usage or domain error, `3` unexpected exception or serialization failure. A blocked gate stops downstream work instead of degrading into a warning.

**Windows-first portability.** The suite runs on a Chinese-locale Windows console: scripts reconfigure stdout/stderr to UTF-8 (verified under `PYTHONIOENCODING=cp936`), and path handling treats symbolic links, junctions, and reparse points as boundary conditions rather than assumptions.

**Concurrency and authorization tests.** Separate suites cover workspace isolation, concurrent write attempts, lease expiry and recovery, framework sync, research safety, and export link closure.

## Repository layout

```
SKILL.md          entry router — task-to-reference index and non-negotiable boundaries
references/       22 domain documents loaded on demand (commit protocol, continuity, ...)
scripts/          9 CLI tools, standard library only
tests/            164 tests (unittest, no third-party test runner)
ci/               CI-only stub for the declared humanizer-zh dependency
```

`SKILL.md` is the agent-facing entry point; it routes to `references/` per task instead of inlining every rule, so an agent loads only the contract it needs.

## Quickstart

Requires Python 3.10+. There is nothing to install.

```bash
git clone https://github.com/xinghe-labs/novel-studio.git
cd novel-studio

python -X utf8 scripts/novel_workspace.py --version
python -X utf8 scripts/novel_workspace.py doctor
```

`doctor` is strictly read-only. It checks the Python runtime, SQLite availability, module imports, stdout encoding, the resolved `humanizer-zh` dependency, and — when given a workspace — the directory layout and registry schema.

A minimal write cycle, from the repository root:

```bash
python -X utf8 scripts/novel_workspace.py work-ensure "<workspace-root>" --client "generic"
python -X utf8 scripts/novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<project-id>"
python -X utf8 scripts/novel_workspace.py lock-acquire "<workspace-root>" "<work-id>"
python -X utf8 scripts/novel_workspace.py write-check "<workspace-root>" "<work-id>"

python -X utf8 scripts/novel_project.py commit-chapter "<project-root>" \
    "staging/chapters/<package>" --workspace "<workspace-root>" --work-id "<work-id>"

python -X utf8 scripts/novel_project.py validate "<project-root>"
python -X utf8 scripts/novel_workspace.py base-refresh "<workspace-root>" "<work-id>" \
    --validation-reference "project and long-term memory recheck complete"
python -X utf8 scripts/novel_workspace.py lock-release "<workspace-root>" "<work-id>"
```

`commit-chapter` re-verifies the lease, project ownership, and baseline hash at the start of the check and again immediately before the transactional write. Missing arguments, an expired lease, a failed heartbeat, or a changed project all fail closed.

## Testing

The suite uses only `unittest`, so it needs no test runner and no `pip install`:

```bash
python -X utf8 -m unittest discover -s tests -t tests
# Ran 164 tests in 490s
# OK (skipped=3)
```

Three tests skip when the environment cannot create directory symbolic links; they execute on Linux CI. The suite takes several minutes because the CLI contract is exercised through real subprocess invocations rather than in-process mocks.

`novel-studio` declares `humanizer-zh` as a dependency and blocks formal work when it cannot resolve one. CI satisfies that contract with the checked-in stub at `ci/humanizer-zh-stub`, selected via `NOVEL_HUMANIZER_PATH`. The stub performs no rewriting and is never used for real manuscripts.

CI runs on Linux (Python 3.10, 3.12, 3.13) and Windows (3.13), plus a dedicated job asserting that `doctor` reports `pass` and stays read-only.

## Design notes

The commit protocol, lease model, hash gate semantics, and the reasoning behind fail-closed defaults are documented in [DESIGN.md](DESIGN.md). Domain contracts live in [`references/`](references/) and are written in Chinese, matching the tool's primary user base.

## Scope and honesty

This is a workflow and state-integrity tool, not a text generator, and not a detector-evasion tool. It does not claim to defeat AI-text detection, and its naturalness review is a local editorial check for mechanical repetition and voice consistency — not a platform-compliance guarantee. The tooling does not use pirated full texts and does not bypass access controls; research sources carry provenance, a SHA-256, a rights status, and an authorization scope.

## License

[MIT](LICENSE)