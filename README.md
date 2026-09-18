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

Nine standard-library CLI tools with a documented JSON contract (plus `install.py`, a plain-text installer that is not part of the contract):

| Tool | Commands | Responsibility |
|---|---|---|
| `novel_workspace.py` | `doctor` `init` `status` `project-register` `project-create` `project-list` `work-start` `work-ensure` `work-bind` `work-resume` `work-reconcile` `work-list` `work-close` `lock-acquire` `lock-renew` `write-check` `base-refresh` `lock-release` `lock-break` | Workspace lifecycle, project registry, single-writer leases, environment `doctor` |
| `novel_project.py` | `init` `upgrade` `validate` `status` `research-state` `framework-state` `framework-sync` `commit-chapter` | Project contract, framework sync, transactional `commit-chapter` |
| `novel_continuity.py` | `install` `status` `prepare-context` `prepare-audit` `bind-audit` `check-package` `prepare-baseline` `record-baseline` `impact` `invalidate` | Continuity baselines and gate evaluation |
| `novel_review.py` | `status` `prepare` `record` `configure` | Periodic quality review and gate status |
| `novel_originality.py` | `audit` | Originality audit (wording overlap + structural mapping) |
| `novel_research.py` | `adapters` `collect` `register` `verify` | Source registration, rights scope, access-control boundaries |
| `novel_memory.py` | `rebuild` `update` `status` `search` | Long-term memory retrieval and index caching |
| `novel_export.py` | `export` `status` | DOCX / EPUB export with structural verification |
| `novel_cli.py` | — | Shared CLI contract, atomic file primitives, version source |

**18,583 lines** of runtime Python, **7,348 lines** of tests, and **22 progressive-disclosure reference documents**, with no third-party runtime dependency.

## How the pieces fit

Six terms cover the whole system:

- **Workspace** — the operational root. Holds the SQLite registry, one directory per *work*, and the lease bookkeeping. One workspace can host several projects.
- **Project** — the canonical manuscript: story bible, outline, chapters, continuity facts, long-term memory, review artifacts. This is the "database"; SQLite only holds a rebuildable retrieval cache.
- **Work** — an isolated scratch directory (drafts, research, reports) outside the canonical tree. Every formal write is bound to a work identity.
- **Lease** — the single-writer lock. Formal writes require a live lease bound to the workspace and work; expired leases can only be reclaimed through an audited `lock-break`.
- **Staging** — packages assembled for commit under the project's `staging/`. Not counted in the canonical state hash, but still lease-gated.
- **Gates** — continuity baselines, periodic quality reviews, and originality audits, each bound to a content hash. Changing text, context, or state invalidates the prior reports; a stale approval cannot be revived by editing metadata.

## Installation

Requires Python 3.10+. There is nothing to install and no `pip` step.

```bash
git clone https://github.com/xinghe-labs/novel-studio.git
cd novel-studio

python -X utf8 scripts/novel_workspace.py --version
python -X utf8 scripts/novel_workspace.py doctor
```

`doctor` is strictly read-only. Without arguments it checks the Python runtime, SQLite availability, module imports, stdout encoding, and the resolved `humanizer-zh` dependency; given a workspace root it also validates the directory layout and registry schema through a read-only connection.

To install it as an agent skill, run the bundled installer:

```bash
python install.py              # installs into the first detected skill root (~/.agents/skills or ~/.codex/skills)
python install.py --root "<other-skill-root>"   # explicit target
python install.py --all        # every detected skill root
```

The installer copies only controlled files (export-ignoring `continuity-eval/`), verifies the target directory's identity before atomically replacing an existing install (`--force` for a foreign directory), and finishes by running `--version` and `doctor` on the installed copy; a blocked `doctor` exits the installer with code 1. To update an install, `git pull` in the clone and re-run `python install.py`. The installer prints plain text — it is not part of the JSON CLI contract.

**The `humanizer-zh` dependency (bundled).** Formal commits (every long-form chapter, every complete short story) must actually invoke the `humanizer-zh` skill. The repository vendors a `humanizer-zh/` copy inside the skill root — a third-party MIT skill (a Chinese translation of blader/humanizer; copyright and provenance live in that directory's `LICENSE` and `SKILL.md` frontmatter) — so a single install is self-contained. Resolution order: the `NOVEL_HUMANIZER_PATH` environment variable (a directory or file), the bundled copy, a sibling `humanizer-zh/` directory next to the skill root, `~/.agents/skills/humanizer-zh`, or `~/.codex/skills/humanizer-zh`. The target must contain a readable `SKILL.md` whose frontmatter declares `name: humanizer-zh`. When nothing resolves, `doctor` reports `status: blocked` and formal work must not proceed — there is no `--force` and no bypass. If the host has not registered a separate humanizer skill, the agent follows the bundled copy's `SKILL.md` to run the same naturalization pass. CI satisfies the contract with the checked-in stub at `ci/humanizer-zh-stub`, selected via `NOVEL_HUMANIZER_PATH`; the stub performs no rewriting and is never used for real manuscripts.

## Usage

There are two ways to use the engine. They share the same CLI and the same contracts.

### As an agent skill (the intended use)

`novel-studio` is designed to be operated by an AI coding agent. Install it with `python install.py` (see Installation), or simply point the agent at a clone of this repository. [`SKILL.md`](SKILL.md) is the agent-facing entry point: a task-to-reference router plus the non-negotiable boundaries. The agent loads only the contract it needs:

| Task | Reference documents (in `references/`) |
|---|---|
| Interactive planning / framework confirmation | `interactive-planning`, `planning`, `controlled-automation` |
| Market research / source registration | `market-research`, `platform-adapters`, `source-ingestion` |
| Create or upgrade a project | `project-contract` |
| Drafting / continuation / memory retrieval | `drafting`, `long-term-memory`, `continuity` |
| Canonical staging & commit | `commit-protocol`, `controlled-automation`, `schemas-and-cli` |
| Originality audit | `originality-audit` |
| Review cycles / short-story finalization | `revision`, `periodic-review`, `short-story-mode` |
| Batch revision | `batch-revision` |
| Export & platform delivery | `publishing-exports`, `platform-delivery-quality` |
| Live platform publishing | `fanqie-live-publishing` — only with explicit per-action authorization |
| Post-publication feedback | `publication-feedback`, `market-research`, `periodic-review` |

The reference documents are written in Chinese, matching the tool's primary user base. `DESIGN.md` explains the commit protocol, lease model, hash-gate semantics, and the reasoning behind fail-closed defaults for readers evaluating the code rather than writing the novel.

### Directly from the shell

A complete cycle for a brand-new project. Set `python -X utf8` first on Windows (the scripts also reconfigure stdout/stderr to UTF-8 themselves); on Linux/macOS plain `python3` works.

```bash
# 0) Environment check (strictly read-only, no arguments needed)
python -X utf8 scripts/novel_workspace.py doctor

# 1) One-time setup: create a workspace, then a registered project inside it.
#    The command prints the project id; the canonical tree is created at
#    <workspace-root>/projects/<project-id>
python -X utf8 scripts/novel_workspace.py init "<workspace-root>"
python -X utf8 scripts/novel_workspace.py project-create "<workspace-root>" \
    --title "书名" --work-type serial_novel --genre "都市脑洞"

# 2) Open an isolated work context and take the single-writer lease
python -X utf8 scripts/novel_workspace.py work-ensure "<workspace-root>" --client "generic"
python -X utf8 scripts/novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<project-id>"
python -X utf8 scripts/novel_workspace.py lock-acquire "<workspace-root>" "<work-id>"
python -X utf8 scripts/novel_workspace.py write-check "<workspace-root>" "<work-id>"

# 3) Draft, humanize, and audit inside the work directory. When the chapter is
#    ready, assemble a staged package under the project's
#    staging/chapters/<package>/  — staging is outside the canonical hash, but
#    canonical writes only ever happen through commit-chapter.

# 4) Transactional canonical commit
python -X utf8 scripts/novel_project.py commit-chapter "<project-root>" \
    "staging/chapters/<package>" --workspace "<workspace-root>" --work-id "<work-id>"

# 5) Validate, refresh the baseline, release the lease
python -X utf8 scripts/novel_project.py validate "<project-root>"
python -X utf8 scripts/novel_workspace.py base-refresh "<workspace-root>" "<work-id>" \
    --validation-reference "project and long-term memory recheck complete"
python -X utf8 scripts/novel_workspace.py lock-release "<workspace-root>" "<work-id>"
```

`commit-chapter` re-verifies the lease, project ownership, and baseline hash at the start of the check and again immediately before the transactional write. Missing arguments, an expired lease, a failed heartbeat, or a changed project all fail closed. Long-running tasks renew the lease with `lock-renew`; a dead lease is reclaimed with `lock-break`, which refuses live leases and records every reclaim in an append-only audit table.

Read-only queries never need a work context or a lease:

```bash
python -X utf8 scripts/novel_workspace.py status "<workspace-root>"
python -X utf8 scripts/novel_project.py status "<project-root>"
python -X utf8 scripts/novel_project.py validate "<project-root>"
python -X utf8 scripts/novel_continuity.py status "<project-root>"
python -X utf8 scripts/novel_review.py status "<project-root>"
```

## The output contract

Every tool emits exactly one JSON document on stdout, keeps stderr empty, and uses defined exit codes:

| Exit code | Meaning |
|---|---|
| `0` | Success |
| `1` | A business gate, validation, or audit did not pass — the JSON is still a parseable business result |
| `2` | Usage or domain error |
| `3` | Unexpected exception or serialization failure |

A blocked gate stops downstream work instead of degrading into a warning. `--help` is the only human-readable path; the machine-readable object shapes are documented in [`references/schemas-and-cli.md`](references/schemas-and-cli.md).

## Repository layout

```
SKILL.md          entry router — task-to-reference index and non-negotiable boundaries
references/       22 domain documents loaded on demand (commit protocol, continuity, ...)
scripts/          9 CLI tools, standard library only
install.py        one-command installer into agent-host skill roots
humanizer-zh/     bundled naturalization skill — third-party MIT, see its LICENSE
tests/            engine test suite (unittest, no third-party runner)
continuity-eval/  seeded-contradiction benchmark for the continuity layer (dev-only)
agents/           agent-host integration metadata
ci/               CI-only stub for the declared humanizer-zh dependency
```

## Testing

The suite uses only `unittest`, so it needs no test runner and no `pip install`:

```bash
python -X utf8 -m unittest discover -s tests -t tests
# Ran 168 tests in 569s
# OK (skipped=3)
```

Three tests skip when the environment cannot create directory symbolic links; they execute on Linux CI. The suite takes several minutes because the CLI contract is exercised through real subprocess invocations rather than in-process mocks.

CI runs on Linux (Python 3.10, 3.12, 3.13) and Windows (3.13), plus a dedicated job asserting that `doctor` reports `pass` and stays read-only.

## Continuity evaluation benchmark

[`continuity-eval/`](continuity-eval/) is a read-only, advisory measurement harness for the continuity layer: a deterministic, seeded contradiction injector, a zero-LLM contradiction detector scored against those labels, and a dev/held-out sweep protocol that picks thresholds without leaking held-out books into the choice. It never writes to a canonical project and is deliberately not wired into the commit gate. It is maintained in this repository and exercised by CI, but excluded from skill distribution archives via `.gitattributes` (`export-ignore`) — skill users get the engine and gates; the benchmark stays in the source repository for development and reproducibility research.

## Scope and honesty

This is a workflow and state-integrity tool, not a text generator, and not a detector-evasion tool. It does not claim to defeat AI-text detection, and its naturalness review is a local editorial check for mechanical repetition and voice consistency — not a platform-compliance guarantee. The tooling does not use pirated full texts and does not bypass access controls; research sources carry provenance, a SHA-256, a rights status, and an authorization scope.

## License

[MIT](LICENSE)
