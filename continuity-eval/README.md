# continuity-eval

A read-only, advisory measurement harness for the continuity layer of
`novel-studio`. Three parts:

- **`inject.py`** — a deterministic, seeded contradiction injector. It copies a
  novel project, mutates *specific* values in the manuscript, and records a
  label for every mutation (what changed, where, how far the corroborating
  occurrence is, and whether the value is anchored in the declared canon
  store).
- **`detect.py`** — a deterministic, zero-LLM contradiction detector scored
  against those labels.
- **`run_eval.py` + `sweep.py`** — the harness that scores detector
  configurations against the labels, and the dev/held-out protocol that picks
  thresholds without leaking the held-out books into the choice.

## Invariants

These are not style preferences; violating one invalidates the measurement.

1. **Never writes to a canonical project.** The injector reads a source project
   and writes only into a scratch directory. A test asserts the source tree's
   hashes are unchanged after an injection run.
2. **Advisory only, never gating.** The detector is deliberately *not* wired into
   `commit-chapter`. The engine's continuity gate stays deterministic and
   schema/hash-bound; this harness is the non-deterministic, best-effort layer
   that sits beside it. Adding an LLM call to the commit path would destroy the
   property that makes the gate trustworthy.
3. **Inputs are pinned.** Every run records the corpus snapshot hash it ran
   against. An evaluation whose inputs are not pinned is not an evaluation.
   The real-corpus manifest itself is kept out of version control (its local
   paths and chapter filenames would disclose unpublished work); the published
   pin is the corpus digest, recorded in
   [`snapshots/README.md`](snapshots/README.md).
4. **Zero third-party dependencies**, like the rest of the repository. Charts are
   rendered as SVG by hand rather than pulling in a plotting library.
5. **Ground truth is labelled twice.** Injections are machine-labelled. Real
   contradictions already in the corpus are the second, harder column — a
   detector that scores well on injections and finds none of the real ones is
   overfitted to its own injector, and reporting only the first column would be
   dishonest.
6. **The detector runs blind.** It sees only the scratch book itself (manuscript,
   canon facts, memory cards) — never the labels, never the pre-injection text.

## Scope

Chinese long-form web fiction only. The corpus is three novels by the author
(174 chapters, ~495k characters) with different POV and tense, which gives a
held-out generalisation split for free rather than a single-book self-test.

Not in scope: prose quality judgement, rewrite suggestions, AI-detector
evasion claims, or generalisation to other languages/genres.

## Pipeline

```powershell
# 1. pin the corpus (records per-file hashes and a corpus digest)
python -X utf8 corpus_snapshot.py --projects-root <projects-root> --output snapshot.json

# 2. inject contradictions (seed=1 is reproducible; writes scratch copies + labels)
python -X utf8 inject.py --project <projects-root>\project-dev-a --out <scratch-root>\injected --seed 1

# 3. verify the dataset before trusting any number run on it
python -X utf8 verify_dataset.py --injected-root <scratch-root>\injected --output verify-report.json

# 4. score full vs baseline, by class / distance bucket / anchor, plus the second column
#    (--snapshot refuses to run if any project's live bytes drifted from the pinned manifest)
python -X utf8 run_eval.py --projects-root <projects-root> --injected-root <scratch-root>\injected --snapshot snapshot.json --output eval-report.json --chart eval-recall.svg

# 5. sweep thresholds under the dev / held-out protocol
python -X utf8 sweep.py --projects-root <projects-root> --injected-root <scratch-root>\injected --dev project-dev-a --held-out project-heldout-b,project-heldout-c --output sweep-report.json
```

`run_eval.py --no-attributes` skips the second column (below); default is to
include it.

## How the benchmark works

The injector mutates one value per label (数字 amounts, counts, dates, years,
durations, ordinals) and records:

- **distance bucket** — chapters between the mutated occurrence and the nearest
  surviving corroborating occurrence: `0` (same chapter), `1-5`, `6-20`, `21+`,
  plus `fact-only` (no prose corroboration at all; only the declared canon fact
  contradicts the mutation). A detector that treats a same-chapter typo like a
  60-chapter drift is measuring the wrong thing; the buckets exist so that
  cannot hide inside an aggregate.
- **anchor** — `canon` (the true value is declared in `canon-facts.jsonl`, so
  the `canon_fact` channel can see it) vs `prose` (only the prose contradicts).
- **class** — `amount` / `count` / `date` / `date_part` / `year` / `era_time` /
  `duration` / `ordinal`.

Scoring joins findings to labels by file + span overlap. The reported
**baseline** is `rare_near_common` only — the frequency fallback without the
slot or canon channels — the "trivial" strategy the full detector has to beat.

### The detector's three rules

- `canon_fact` — prose value vs the declared fact store (strongest signal).
- `slot_match` — same verbatim slot (±12 chars around a blanked numeral)
  recurring across chapters with a conflicting value.
- `rare_near_common` — a rare value co-occurring with a frequent one in range.

A fourth channel, `attributes.py`, is **deliberately out of the benchmark**: the
injector does not generate name-attribute contradictions, so there is nothing
honest to score it against. It binds birth/death years to the declared cast
(fact subjects + the 知情者 column of `threads.md`) and reports *any* two
distinct values for one (person, attribute) — a 46-year gap on a singular
attribute is not a "near value" problem. Its findings are the **second column**:
run on the untouched corpus, reported under `projects.<id>.attributes` for
author adjudication, never merged into injection scores.

## Results (2026-09-16 run, seed=1, 252 labels)

| detector | precision | recall | F1 |
|---|---|---|---|
| full (`canon_fact,slot_match,rare_near_common`) | 0.240 | 0.298 | **0.266** |
| baseline (`rare_near_common`) | 0.235 | 0.270 | 0.251 |

Per project (full): `project-heldout-c` F1 0.514 / `project-dev-a`
0.303 / `project-heldout-b` 0.159. The ranking tracks how dense the legal
near-value traffic is in each book: project-heldout-b's debt ledgers increment legally
(债务递增), which is exactly the pattern the frequency guard cannot
distinguish from a contradiction.

Recall by distance bucket (full vs baseline):

| bucket | labels | full | baseline |
|---|---|---|---|
| 0 (same chapter) | 60 | 0.383 | 0.367 |
| 1-5 | 65 | 0.200 | 0.200 |
| 6-20 | 65 | 0.200 | 0.200 |
| 21+ | 44 | 0.409 | 0.409 |
| fact-only | 18 | 0.444 | 0.111 |

Readings we consider load-bearing, not footnotes:

- **Far is easier than near.** 21+ and fact-only outperform the 1-5 bucket: a
  nearby injection tends to collide with a native value of the book, so the
  distance stratification earns its keep and the aggregate hides it.
- **Class asymmetry.** `era_time` (1.00) and full dates (0.63) are strong;
  `count` runs 0.33–0.70; `amount` and `ordinal` sit at 0.10–0.35 — native
  ordinals (第一个/第二个) are so frequent in running prose that an injected one
  is not a rare signal.
- **Full beats the frequency baseline** (0.266 vs 0.251 aggregate; on the
  fact-only bucket 0.444 vs 0.111 — only the canon channel can see those), but
  the margin is thin: the slot channel has not yet earned its complexity on
  aggregate F1. Its value is visibility (prose-vs-declared-fact findings), not
  the headline number.
- **Threshold sweeps do not transfer.** Under the dev/held-out protocol the
  best dev configuration's edge does not survive transfer: on the two held-out
  books it wins one and loses one, while tripling clean-corpus noise (162 → 268
  findings per run) on the weakest book. The ceiling is signal, not
  thresholds; defaults stay.
- **Clean-text false positives** run 1.1–2.7 findings / 10k chars depending on
  the book. Every one of them is surfaced to the author, never auto-applied.

### Second column (real-corpus attribute channel)

On the untouched corpus the channel found **2 conflicts, both in project-dev-a, both
genuine**: `张某A[death] {<conflicting-values>}` — an earlier chapter's registry passage states one date while the
declared canon states another — and `张某A[birth] {<conflicting-values>}`  (one value belongs to another character).
Zero findings on the other two books. These go to the author as adjudication
input, matching the advisory-only invariant.

## Limitations

- Recall is ~0.30. We report it because the failure modes are diagnosable
  (near-distance collisions, ordinal density, debt progressions), not because
  the number flatters anyone. The next layer is semantic: LLM-extracted claims
  compared deterministically — the numeric channels above are the deterministic
  floor, not the ceiling.
- The detector shares its tokenizer with the injector, so the benchmark measures
  the *comparison* layer, not tokenisation.
- The corpus is three novels by one author. Held-out books are held out *within*
  that corpus; generalisation beyond it is unmeasured.
- Every number above is backed by a file in the report artifacts; none is
  hand-copied.
