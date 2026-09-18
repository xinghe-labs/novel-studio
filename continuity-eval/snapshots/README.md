# Snapshots

The pinned real-corpus snapshot manifest (`corpus-snapshot.json`) is
deliberately **not in version control**: the manifest records local paths and
real chapter filenames, which would disclose the structure of unpublished
work. It lives locally next to this README after generation.

The published pin is the corpus digest itself:

```text
corpus_digest = 58d899a0a320532d278354d6e2a8b058163dd6c162c1a8fe64eb6e85b8b88f42
generated     = 2026-09-15 (9 projects, schema_version 1)
```

Reproduce locally:

```bash
python corpus_snapshot.py --projects-root <local projects dir> \
    --output snapshots/corpus-snapshot.json
# then, for any evaluation run:
python run_eval.py --snapshot snapshots/corpus-snapshot.json ...
```

`run_eval.py --snapshot` verifies every project digest against the corpus on
disk before evaluating, so a drifted corpus is refused rather than silently
scored. The test suite already proves the harness end-to-end against a
synthetic corpus it generates in a temporary directory
(`tests/test_inject.py`), which is the third-party reproduction path.
