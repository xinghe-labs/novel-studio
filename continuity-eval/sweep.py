# -*- coding: utf-8 -*-
"""Threshold sweep for the detector, with a dev / held-out protocol.

Why a protocol at all: the thresholds are chosen by looking at benchmark results,
so tuning on all three novels and reporting the same three would inflate every
number. One novel is the dev set and picks the configuration; the other two are
held out and only scored with the already-chosen configuration. The held-out
columns are the quotable ones.

Every configuration is scored three ways on the dev set -- injected-corpus
precision/recall/F1 and clean-corpus false positives -- and the held-out pass
also runs the frequency-only baseline for comparison. All raw configs are kept
in the report so the choice is auditable rather than asserted.

Stdlib only.
"""
from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import pathlib
import sys
from typing import Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import detect  # noqa: E402
from detect import Params  # noqa: E402
from run_eval import score as join_score  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

FULL_RULES = {"canon_fact", "slot_match", "rare_near_common"}
BASELINE_RULES = {"rare_near_common"}


def load_labels(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def eval_config(pid: str, original_root: pathlib.Path, injected_root: pathlib.Path,
                labels: list[dict], params: Params, rules: set[str]) -> dict:
    findings = detect.detect_project(injected_root / pid, rules, params)
    result = join_score(labels, findings)
    # every finding on the untouched corpus is a false positive by construction
    result["clean_fp"] = len(detect.detect_project(original_root / pid, rules, params))
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sweep", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--projects-root", required=True)
    parser.add_argument("--injected-root", required=True)
    parser.add_argument("--dev", required=True, help="Project id used to pick the configuration.")
    parser.add_argument("--held-out", required=True,
                        help="Comma-separated project ids scored only after the choice.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    projects_root = pathlib.Path(args.projects_root)
    injected_root = pathlib.Path(args.injected_root)
    held_out = args.held_out.split(",")
    all_pids = [args.dev] + held_out
    labels = {pid: load_labels(injected_root / f"labels-{pid}.jsonl") for pid in all_pids}

    grid = [
        Params(rare=rare, max_value_distance=dist, both_rare_limit=both, seq_guard=guard)
        for rare, dist, both, guard in itertools.product(
            (1, 2, 3, 5), (2, 3, 5), (1, 3), (False, True))
    ]
    configs = [("default", Params())] + [(f"c{i:02d}", params) for i, params in enumerate(grid)]

    dev_rows = []
    for name, params in configs:
        result = eval_config(args.dev, projects_root, injected_root,
                             labels[args.dev], params, FULL_RULES)
        dev_rows.append({"config": name, "params": dataclasses.asdict(params), "dev": result})

    # selection: dev F1 first, precision as tiebreak, clean FP as second tiebreak
    dev_rows.sort(key=lambda row: (-row["dev"]["f1"], -row["dev"]["precision"],
                                   row["dev"]["clean_fp"]))
    winner_name, winner_params = dev_rows[0]["config"], Params(**dev_rows[0]["params"])

    heldout_rows = []
    for name, params in (("winner", winner_params), ("default", Params())):
        for pid in held_out:
            result = eval_config(pid, projects_root, injected_root, labels[pid], params, FULL_RULES)
            heldout_rows.append({"config": name, "params": dataclasses.asdict(params),
                                 "project_id": pid, "result": result})
    for pid in held_out:
        result = eval_config(pid, projects_root, injected_root, labels[pid], Params(),
                             BASELINE_RULES)
        heldout_rows.append({"config": "baseline", "params": dataclasses.asdict(Params()),
                             "project_id": pid, "result": result})

    report = {
        "status": "ok",
        "dev": args.dev,
        "held_out": held_out,
        "winner": {"config": winner_name, "params": dataclasses.asdict(winner_params)},
        "dev_results": dev_rows,
        "heldout_results": heldout_rows,
    }
    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")

    print(f"winner: {winner_name} {dataclasses.asdict(winner_params)}")
    print("\ntop 8 dev configs (F1, P, R, clean_fp):")
    for row in dev_rows[:8]:
        d = row["dev"]
        print(f"  {row['config']:<8} F1={d['f1']:.4f} P={d['precision']:.4f} "
              f"R={d['recall']:.4f} fp={d['clean_fp']:<4} "
              f"rare={row['params']['rare']} dist={row['params']['max_value_distance']} "
              f"both={row['params']['both_rare_limit']} seq={row['params']['seq_guard']}")
    print("\nheld-out (winner vs default vs baseline):")
    for row in heldout_rows:
        r = row["result"]
        print(f"  {row['project_id']:<15} {row['config']:<8} "
              f"P={r['precision']:.4f} R={r['recall']:.4f} F1={r['f1']:.4f} fp={r['clean_fp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())