---
name: humanizer-zh
description: CI-only stand-in for the humanizer-zh skill that novel-studio declares as a dependency.
---

# humanizer-zh (CI stub)

This directory exists so continuous integration can exercise novel-studio's
`doctor` contract without installing the real `humanizer-zh` skill.

`novel-studio` refuses to treat formal work as ready unless it can resolve a
`humanizer-zh` skill whose `SKILL.md` frontmatter declares `name: humanizer-zh`.
The CI workflow points `NOVEL_HUMANIZER_PATH` at this stub so the environment
check has something valid to resolve.

This stub performs no rewriting and must never be used for real manuscripts.