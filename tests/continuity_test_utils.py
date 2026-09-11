from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import novel_continuity


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def evidence_quote(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return line.strip()
    raise AssertionError(f"Test evidence source is empty: {path}")


def seal_full_baseline(root: Path, reviewer_id: str = "test-independent-baseline") -> dict[str, Any]:
    """Seal directly seeded fixture chapters through the production baseline path."""
    novel_continuity.install_project(root)
    packet = novel_continuity.build_baseline_packet(root)
    token = uuid.uuid4().hex
    packet_path = root / "staging/test-continuity" / f"packet-{token}.json"
    report_path = root / "staging/test-continuity" / f"report-{token}.json"
    write_json(packet_path, packet)
    report = novel_continuity.baseline_report_template(
        packet, novel_continuity.sha256_file(packet_path)
    )
    through = int(packet["chapter_through"])
    facts: list[dict[str, Any]] = []
    dependencies: dict[str, Any] = {}
    for number, relative in novel_continuity.chapter_paths(root, through):
        quote = evidence_quote(root / relative)
        fact_id = f"F-CHAPTER-{number:04d}"
        facts.append(
            {
                "schema_version": 1,
                "fact_id": fact_id,
                "category": "canon_truth",
                "subject": f"第{number:04d}章",
                "predicate": "正文事件已发生",
                "object": quote,
                "status": "active",
                "significance": "normal",
                "valid_from_chapter": number,
                "source": {
                    "path": relative,
                    "location": "首个非空行",
                    "quote": quote,
                },
            }
        )
        dependencies[f"{number:04d}"] = {
            "fact_ids": [fact_id],
            "depends_on_chapters": ([number - 1] if number > 1 else []),
            "evidence_paths": [relative],
            "risk_triggers": [],
        }
    report.update(
        {
            "status": "complete",
            "reviewed_chapters": list(range(1, through + 1)),
            "reviewer": {
                "mode": "independent" if through else "deterministic",
                "reviewer_id": reviewer_id,
                "independent_context": bool(through),
            },
            "decision": "pass",
            "summary": "单元测试已逐章核对连续性并建立稳定事实基线。",
            "findings": [],
            "residual_risks": [],
            "facts": facts,
            "intentional_exceptions": [],
            "chapter_dependencies": dependencies,
            "reviewed_at": "2026-09-02T00:00:00+00:00",
        }
    )
    write_json(report_path, report)
    return novel_continuity.record_baseline(
        SimpleNamespace(
            root=str(root),
            packet=str(packet_path),
            report=str(report_path),
            authorization_reference="unit test fixture continuity baseline",
        )
    )


def complete_staged_continuity(
    root: Path,
    package: Path,
    *,
    risk_triggers: Iterable[str] = (),
    chapter_class: str = "normal",
    reviewer_id: str = "test-continuity-reviewer",
    fact_changes: Iterable[dict[str, Any]] = (),
) -> None:
    """Complete a hash-bound passing continuity package for commit tests."""
    commit = read_json(package / "commit.json")
    chapter_number = int(commit["chapter_number"])
    context_path = package / str(
        commit.get("continuity_context_file", "continuity-context.json")
    )
    novel_continuity.prepare_context(
        SimpleNamespace(
            root=str(root), chapter=chapter_number, output=str(context_path)
        )
    )
    context = read_json(context_path)
    candidate = package / str(commit.get("chapter_file", "chapter.md"))
    humanization_source = package / "chapter-before-humanizer.md"
    humanization_source.write_bytes(candidate.read_bytes())
    humanization_review_path = package / "humanization-review.json"
    write_json(
        humanization_review_path,
        {
            "schema_version": 1,
            "status": "complete",
            "skill": "humanizer-zh",
            "chapter_number": chapter_number,
            "reviewed_at": "2026-09-11T00:00:00+00:00",
            "outcome": "unchanged",
            "summary": "测试夹具模拟逐章专项自然化审阅，结论为无需修改。",
            "protected_elements": ["POV 与人物声音"],
            "source": {
                "path": humanization_source.name,
                "sha256": novel_continuity.sha256_file(humanization_source),
            },
            "result": {
                "path": candidate.name,
                "sha256": novel_continuity.sha256_file(candidate),
            },
            "selection": {
                "selected": "result",
                "authorization_reference": "unit test project-level humanization authorization",
            },
        },
    )
    commit["humanization_review_file"] = humanization_review_path.name
    write_json(package / "commit.json", commit)
    candidate_quote = evidence_quote(candidate)
    triggers = sorted(set(risk_triggers))
    required_reading: list[dict[str, Any]] = []
    for item in context["automatic_reading"]:
        reading_path = item["path"]
        reading_text = (root / reading_path).read_text(encoding="utf-8")
        reading_evidence = []
        if reading_text.strip():
            reading_evidence = [
                {
                    "path": reading_path,
                    "location": "首个非空行",
                    "quote": evidence_quote(root / reading_path),
                }
            ]
        required_reading.append(
            {
                "path": reading_path,
                "sha256": item["sha256"],
                "reason": "核对本章入口状态与既有事实",
                "evidence": reading_evidence,
            }
        )
    context.update(
        {
            "status": "complete",
            "required_reading": required_reading,
            "touched_entities": [{"type": "character", "name": "测试人物"}],
            "touched_fact_ids": [],
            "chapter_contract": {
                "delivery": "推进本章承诺",
                "pov": "测试人物",
                "story_time": f"第{chapter_number}日",
                "location": "测试地点",
                "purpose": "验证连续性事务",
                "entry_state": "承接已封存正典",
                "allowed_changes": ["本章明示的状态变化"],
                "forbidden_changes": ["无解释改写既有事实"],
                "exit_direction": "形成可追溯的新状态",
            },
            "invariants": ["人物、时间、知识和物品状态不得无证据跳变"],
            "risk_assessment": {
                "chapter_class": chapter_class,
                "triggers": triggers,
                "max_callback_span": 0,
                "requires_independent_review": False,
                "rationale": "根据测试章节触及的正典范围分类",
            },
            "author_confirmation_reference": (
                "unit test author confirmation" if chapter_class == "key" else ""
            ),
        }
    )
    independent, confirmation, _ = novel_continuity.derived_risk(context)
    context["risk_assessment"]["requires_independent_review"] = independent
    if confirmation and not context["author_confirmation_reference"]:
        context["author_confirmation_reference"] = "unit test author confirmation"
    write_json(context_path, context)

    novel_continuity.prepare_audit(
        SimpleNamespace(root=str(root), package=str(package))
    )
    delta_path = package / str(commit.get("state_delta_file", "state-delta.json"))
    delta = read_json(delta_path)
    for change in delta["changes"]:
        if change["path"] != "/through_chapter":
            change["reason"] = "正文明确造成该状态变化"
            change["evidence"] = [
                {
                    "path": "candidate",
                    "location": "首个非空行",
                    "quote": candidate_quote,
                }
            ]
    delta.update(
        {
            "status": "complete",
            "entry_state": {},
            "state_changes": [],
            "exit_state": {},
            "fact_changes": list(fact_changes),
        }
    )
    write_json(delta_path, delta)
    novel_continuity.bind_audit(
        SimpleNamespace(root=str(root), package=str(package))
    )

    audit_path = package / str(
        commit.get("continuity_audit_file", "continuity-audit.json")
    )
    audit = read_json(audit_path)
    for check in audit["checks"].values():
        check.update(
            {
                "status": "pass",
                "rationale": "已对照上下文、状态增量和正文证据核验",
                "evidence": [
                    {
                        "path": "candidate",
                        "location": "首个非空行",
                        "quote": candidate_quote,
                    }
                ],
            }
        )
    audit.update(
        {
            "status": "complete",
            "reviewer": {
                "mode": "independent" if independent else "self",
                "reviewer_id": reviewer_id,
                "independent_context": independent,
            },
            "decision": "pass",
            "findings": [],
            "residual_risks": [],
            "reviewed_at": "2026-09-02T00:00:00+00:00",
        }
    )
    write_json(audit_path, audit)
