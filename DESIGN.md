# Design notes

This document explains the engineering decisions behind `novel-studio`: how state is modeled, why writes are transactional, how concurrency is controlled, and what the fail-closed defaults cost. It is aimed at readers evaluating the architecture rather than writing a novel.

Domain-level contracts live in [`references/`](references/) and are written in Chinese, matching the tool's primary user base.

## 1. The state model

Writing a long serial is a long-horizon stateful task, so the first decision is what counts as truth.

**Sources of truth** are plain, human-readable files:

- the manuscript Markdown (chapter bodies)
- the chapter index
- the story bible (world rules, characters, settings)
- long-term memory records
- continuity files

**`registry.sqlite3` is a rebuildable retrieval cache, not a source of truth.** This matters more than it looks. If the database could hold authoritative story state, a corrupted or partially migrated SQLite file would be a data-loss event, and an agent that "fixed" the database could diverge from the manuscript with no way to tell which was right. Keeping truth in diffable text means the worst case is a slow rebuild, and every change is reviewable in a normal diff.

A consequence: schema evolution is **additive only**. There is no destructive migration path, because dropping a column could destroy derived-but-expensive state. An unrecognized `schema_version` is a hard failure rather than an attempt at best-effort reading.

## 2. Transactional canonical writes

`commit-chapter` performs one atomic transaction. The sequence:

1. Validate that the caller supplied the same workspace and work identity that holds the lease.
2. Verify the lease, project ownership, and baseline hash. This is checked **twice** — once at the start of the check phase and again immediately before the transactional write — because validation and writing are separated by an arbitrary amount of work, and the file could change in between.
3. Write a persistent journal describing the intended change.
4. Verify a compare-and-swap checksum before installing bytes.
5. Commit, or roll back to byte-exact prior content on any failure.

The journal is what makes interruption survivable. A keyboard interrupt, a crash, or a process kill between "some files written" and "all files written" would otherwise leave a half-committed chapter — the worst possible state for a tool whose whole value is consistency. Fault-injection tests assert byte-exact restoration rather than merely checking that an error was raised.

Note step 2's ordering: **validation before mutation, re-validated at the boundary.** A single up-front check is the common bug in this class of tool, because the gap between check and use is exactly where concurrent writers win.

## 3. Concurrency model: single-writer leases

Formal writes require a lease:

| Operation | Semantics |
|---|---|
| `lock-acquire` | Take the lease for a work identity |
| `lock-renew` | Extend a long-running drafting session |
| `write-check` | Confirm the lease is still valid before writing |
| `lock-release` | Release after validation and baseline refresh |
| `lock-break` | Recover a dead writer's lease, under strict preconditions |

Design choices worth calling out:

**A live lease cannot be forcibly released.** `lock-break` requires an explicit expected owner, a non-empty reason, and a lease that is already expired or whose heartbeat has failed. Force-releasing a live writer is how you get two agents writing the same chapter.

**Liveness is heartbeat-based, not timestamp-based.** `heartbeat_at` participates in the liveness decision, so a slow drafting session that is still making progress is not mistaken for an abandoned one.

**Every reclaim is audited.** `lease_events` records the original owner, expiry, reason, and the project hash at reclaim time. Recovering from a crashed writer is legitimate but dangerous, so it leaves a trail rather than being silent.

**`base-refresh` requires a validation reference.** Refreshing the baseline after a commit requires naming the completed validation, which prevents "refresh the hash and move on" from becoming an unexamined habit.

This is deliberate ceremony. Leases cost a few extra calls per chapter, and the cost buys the guarantee that a stale agent session cannot overwrite committed work.

## 4. Staleness by content hash

Review artifacts are bound to content hashes. When text, context, or state changes, prior continuity audits, originality reports, and quality reports are **invalidated** rather than carried forward.

The alternative — trusting a filename or a timestamp — fails in a specific and nasty way: an audit run against chapter 40 still looks like an audit run, and an agent looking for "the continuity report" will happily find and trust it three chapters later. Hash binding makes that failure impossible to reach by accident, and impossible to paper over by editing a report's metadata, since the report itself is checked against the hash it claims.

`staging/` and `exports/` are excluded from the canonical state hash — they are working artifacts, not canonical state — but that exclusion is **not** an exemption from the lease or the committer. Excluded from the hash, still behind the gate.

## 5. Fail-closed CLI contract

Nine tools share one contract, enforced by `novel_cli.py`:

- exactly one JSON document on stdout
- stderr stays empty
- exit `0` success, `1` business gate not passed, `2` usage or domain error, `3` unexpected exception or serialization failure

Structured stdout with an empty stderr means a caller never has to parse a mix of human-readable notes and machine output, and an agent can branch on `status` and exit code without string matching. Even an unexpected exception is converted into a structured document, so a crashing tool is debuggable by the same code path as a passing one.

The `1` versus `2` distinction is load-bearing: `1` means "the tool worked and the answer is no" (a real gate), `2` means "the caller asked something incoherent." Collapsing them would make a genuine quality-gate failure indistinguishable from a typo.

A blocked gate **stops** downstream work. It does not downgrade to a warning and continue to export or publish.

## 6. Zero runtime dependencies

The runtime is standard library only: `argparse`, `sqlite3`, `hashlib`, `json`, `pathlib`, `threading`. CI enforces the claim by rejecting any installable entry in `requirements.txt`.

The trade-off is real — no third-party HTTP client, no ORM, no rich CLI framework. It buys three things that matter for this workload:

- **Durability.** A writing tool must still run years from now, on whatever Python is installed, without a dependency-resolution archaeology exercise. A novel is a multi-year artifact.
- **Auditability.** Every byte of behavior is inspectable. There is no transitive dependency that could change semantics under a caret range.
- **Portability without a build step.** No wheels, no compilation, no venv. It runs from a clone.

## 7. Windows-first portability

The primary environment is a Chinese-locale Windows console, which surfaces two classes of problem that a Linux-first codebase discovers late:

**Encoding.** Tools reconfigure stdout/stderr to UTF-8 themselves, so output stays valid JSON regardless of the console code page. A test pins this by running with `PYTHONIOENCODING=cp936` and asserting UTF-8 JSON, and a `doctor` check reports the actual encoding state.

**Filesystem boundaries.** Path handling treats symbolic links, junctions, and reparse points as explicit boundary conditions rather than assuming POSIX semantics. These are the cases where "verify the file is where it should be" checks quietly become bypassable, so the research and export paths reject them. Three tests skip on environments that cannot create directory symlinks — and those tests genuinely execute on Linux CI, which is one reason the CI matrix spans both platforms.

## 8. Quality gates as first-class state

Gates are not advisory flags:

- Serials require a global continuity baseline and an independent quality review every 5 chapters.
- Short stories require a whole-work continuity baseline plus a whole-work quality review after the single body commit.
- A gate that is **expired** blocks just as hard as a gate that **failed**.
- Naturalness review must actually invoke the declared `humanizer-zh` skill; a manual claim, an export-time check, or a `--force` flag cannot substitute for it.

Gates compose with the staleness model: because artifacts are hash-bound and gates expire, "the last review passed" cannot be stretched to cover work that came after it.

## 9. What this design does not claim

- It does not claim to defeat AI-text detection, and the naturalness review is a local editorial check for mechanical repetition and voice consistency — not a platform-compliance guarantee.
- A passing script is not proof that a story is free of contradictions. The scripts verify state integrity and gate freshness; they cannot validate literary coherence.
- Automated export validation does not replace reading the output. DOCX structure can pass while pages have missing glyphs or broken pagination, so a visual pass is tracked as a separate, explicit step.
- Research sources carry provenance, a SHA-256, a rights status, and an authorization scope. The tooling does not use pirated full texts and does not bypass access controls.

## 10. Distribution model

The distributable is only the set of controlled files in version control. Local runtime state — `.agent-handoff/`, a root `AGENTS.md`, `__pycache__/`, `.pytest_cache/` — is not skill content even when it exists on the same disk.

Releases use `git archive` rather than zipping the directory, because `archive` can only read committed paths and therefore cannot sweep in untracked handoff state or caches. Each verified checkpoint is a commit; changes to tool capabilities, contracts, or compatibility also update `TOOL_VERSION`, the README, and `CHANGELOG.md`.