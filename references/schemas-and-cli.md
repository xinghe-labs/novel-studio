# Schema 与 CLI 契约

本文件集中记录 Agent 需要手写或回读的机器可读对象。`schema_version` 当前为 `1`；字段校验以脚本为最终准则，示例只展示最小可读形状，不代表可以省略脚本要求的嵌套字段。

## CLI JSON

模块正常加载并进入 `scripts/novel_cli.py` 的 `run_cli` 后，除 `--help` 外，工具向 stdout 输出一个完整 JSON 文档。普通业务命令直接返回各自的业务字段，不会由运行器统一补入 `tool` 或 `version`，`status` 的取值也可能是 `created`、`prepared`、`fresh` 等。模块加载失败发生在该契约之前，可能由 Python 直接输出 traceback 并返回退出码 1。只有 `--version` 固定返回：

```json
{
  "status": "ok",
  "tool": "novel_project",
  "version": "2.2.0"
}
```

例如，`novel_workspace.py work-list` 自己定义的成功结果形状为：

```json
{"status": "ok", "workspace_root": "D:/novels/workspace", "count": 0, "works": []}
```

退出码按以下语义解释；不能只看 `status` 是否等于 `ok`：

| 退出码 | 含义 |
|---|---|
| `0` | 命令成功完成 |
| `1` | 业务门禁、验证、审计或当前状态未通过；JSON 通常仍是可解析的业务结果 |
| `2` | 参数、领域或已知操作错误 |
| `3` | 未预期异常，或结果无法序列化 |

退出码 `1` 的示例是原创性资料不完整：

```json
{
  "decision": "incomplete",
  "limitations": ["原创性计划缺少完成的结构映射"]
}
```

参数、领域或已知操作错误使用退出码 `2`：

```json
{
  "status": "error",
  "error": "--limit must be from 1 to 200",
  "error_type": "MemoryIndexError"
}
```

未预期异常使用退出码 `3`，额外带 `recoverable: false`；序列化失败也使用 `3`，`error_type` 为 `SerializationError`：

```json
{
  "status": "error",
  "error": "Unhandled tool error",
  "error_type": "RuntimeError",
  "recoverable": false
}
```

`--help` 是 argparse 的人类可读帮助，属于唯一不保证 JSON 的命令行路径。

## 框架同步包

`novel_project.py framework-sync <project-root> <source-root>` 用于把尚未进入正典的框架确认结果一次性写入项目。`source-root` 必须位于项目目录之外的当前工作目录，并且必须包含以下八个 UTF-8 Markdown 普通文件和一个 UTF-8 JSON 设置文件；命令不会把其他文件复制进正典：

```text
planning/framework-session.md
story-bible/premise.md
story-bible/cast.md
story-bible/world.md
story-bible/style-guide.md
outlines/master-outline.md
memory/decisions.md
memory/book-summary.md
project-settings.json
```

同步命令必须同时提供 `--workspace` 与 `--work-id`，来源必须位于该工作的 `workspaces/<work-id>/` 下，并在取得租约后运行。`--stage`、`--confirmation`、`--requirements-confidence` 和 `--story-confidence` 可覆盖同步包会话 frontmatter 中的对应值；`--authorization-reference` 必填。确认状态要求两个信心值至少为 95，POV 与时态非空，且八份 Markdown 均非空、不得包含待确认占位符。命令会稳定读取源文件、对源文件和项目目标做 SHA-256 CAS 校验，并把八份 Markdown、受控清单更新与零章基线（如适用）放在同一正典事务中。已有正文时，开放失效项必须逐项覆盖实际变化路径；完全相同的重复同步返回 `status: already_current` 且不改正典。

`project-settings.json` 使用严格字段集，不能加入 `current_chapter`、`work_type`、`periodic_review` 或其他 `novel.json` 字段：

```json
{
  "schema_version": 1,
  "pov": "近距离第三人称",
  "tense": "过去时",
  "target_words": 180000
}
```

`target_words` 也可以为 JSON `null`。框架确认完成时 `pov`、`tense` 必须是去除首尾空白后的非空单行字符串；同步器只合并这三个创作字段，自动维护 `status` 和 `updated_at`，其余项目运行字段保持现值。

成功结果的最小形状为：

```json
{
  "status": "synchronized",
  "project_root": "D:/novels/workspace/projects/harbor-clock",
  "source_root": "D:/novels/work/framework-sync",
  "source_files": [
    "planning/framework-session.md",
    "story-bible/premise.md",
    "story-bible/cast.md",
    "story-bible/world.md",
    "story-bible/style-guide.md",
    "outlines/master-outline.md",
    "memory/decisions.md",
    "memory/book-summary.md",
    "project-settings.json"
  ],
  "synced_files": [
    "planning/framework-session.md",
    "story-bible/premise.md",
    "story-bible/cast.md",
    "story-bible/world.md",
    "story-bible/style-guide.md",
    "outlines/master-outline.md",
    "memory/decisions.md",
    "memory/book-summary.md",
    "novel.json"
  ],
  "before": {},
  "after": {},
  "authorization_reference": "作者本轮确认框架",
  "continuity": null
}
```

`before`、`after` 和 `continuity` 的实际字段由命令返回；示例中的路径、时间和状态不是可直接提交的固定值。

## `work.json`

每个 `workspaces/<work-id>/work.json` 必须与 SQLite 注册表一致。创建时的完整顶层字段是：

```json
{
  "schema_version": 1,
  "work_id": "work-20260912t120000z-ab12cd34",
  "status": "active",
  "project_id": "harbor-clock",
  "project_root": "D:/novels/workspace/projects/harbor-clock",
  "purpose": "续写第十章",
  "client": "generic",
  "base_state_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "created_at": "2026-09-12T04:00:00+00:00",
  "updated_at": "2026-09-12T04:00:00+00:00"
}
```

`work_id` 和 `project_id` 使用 3-64 个小写 ASCII 字母、数字或连字符，并以字母或数字开头。未绑定项目的工作把 `project_id`、`project_root` 和 `base_state_hash` 都写为 JSON `null`；绑定后这三个字段必须一起更新。`work-close` 只把 `status` 改为 `closed`，保留目录和草稿。

## 外部变更验证报告

只有项目被当前工作之外的进程修改、且执行者已经逐项回读并验证该变化时，才运行 `base-refresh --accept-external-change --validation-report <path>`。报告必须放在项目目录外，使用 UTF-8 JSON，并与刷新前基准和当前项目状态哈希精确绑定：

```json
{
  "schema_version": 1,
  "project_root": "D:/novels/workspace/projects/harbor-clock",
  "previous_state_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "validated_state_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "result": "pass",
  "validation_reference": "已回读外部编辑并完成连续性与长期记忆复核",
  "checked_at": "2026-09-12T12:00:00+08:00"
}
```

脚本拒绝布尔伪装的 `schema_version`、链接型路径、读取期间被替换的报告、无时区时间、项目根或任一哈希不匹配，以及报告读取后再次发生的项目变化。该报告是哈希绑定的人工复核声明，不是故事正确性的自动证明，也不能代替连续性、质量或原创性审核。

## 章节 `commit.json`

`staging/chapters/<package>/commit.json` 的必备顶层字段：

```json
{
  "schema_version": 1,
  "chapter_number": 1,
  "manuscript_filename": "0001-标题.md",
  "chapter_file": "chapter.md",
  "humanization_review_file": "humanization-review.json",
  "memory_file": "memory.md",
  "continuity_state_file": "continuity-state.json",
  "continuity_context_file": "continuity-context.json",
  "state_delta_file": "state-delta.json",
  "continuity_audit_file": "continuity-audit.json",
  "originality_report": "staging/originality/originality-audit-...json",
  "index": {
    "title": "标题",
    "pov": "视角人物",
    "story_time": "故事时间",
    "location": "地点",
    "fact_summary": "正文已经发生的事实",
    "key_change": "本章关键变化",
    "thread_ids": ["T-001"]
  }
}
```

`timeline_file`、`threads_file`、`book_summary_file` 可选，指向包内完整替换稿。短故事只能提交 `chapter_number: 1`。

完整暂存清单只在 [commit-protocol.md](commit-protocol.md) 维护；本文件只定义清单中机器可读对象的字段。

## `state-delta.json`

脚本要求 `status: complete`，并校验下列顶层字段与当前包/正典哈希绑定：

```json
{
  "schema_version": 1,
  "record_kind": "chapter_state_delta",
  "status": "complete",
  "chapter_number": 1,
  "chapter_sha256": "最终 chapter.md 的 SHA-256",
  "context_sha256": "continuity-context.json 的 SHA-256",
  "base_canon_sha256": "写前正典哈希",
  "base_state_sha256": "写前 continuity/state.json 的规范 JSON 哈希",
  "result_state_sha256": "包内 continuity-state.json 的规范 JSON 哈希",
  "entry_state": {},
  "state_changes": [],
  "exit_state": {},
  "changes": [
    {
      "path": "/through_chapter",
      "before_present": true,
      "before": 0,
      "after_present": true,
      "after": 1,
      "reason": "Advance the committed continuity state to this chapter",
      "evidence": []
    }
  ],
  "fact_changes": [],
  "exception_changes": []
}
```

正常下一章至少会产生上例中的 `/through_chapter` 变化；如果状态还有其他变化，数组必须完整列出。应先把包内 `continuity-state.json` 更新为预期结果，再运行 `prepare-audit` 生成差异模板，不要从空数组手写。`changes` 必须逐项重放后精确得到 `continuity-state.json`，不能只改 `result_state_sha256`。每个非 `/through_chapter` 变化都要有正文逐字证据；事实/例外变更使用 `add`、`update` 或 `retire`，更新/退休还要提供 `before_sha256`。

## `continuity-audit.json`

逐章报告的固定字段如下；短故事只把 `report_kind` 改为 `short_story_continuity_audit`：

```json
{
  "schema_version": 1,
  "report_kind": "chapter_continuity_audit",
  "status": "complete",
  "chapter_number": 1,
  "chapter_sha256": "最终正文哈希",
  "context_sha256": "上下文哈希",
  "state_delta_sha256": "state-delta.json 哈希",
  "binding_status": "current",
  "base_canon_sha256": "写前正典哈希",
  "reviewed_dimensions": [
    "causality", "timeline", "location", "character_state",
    "knowledge_boundaries", "relationships_and_names",
    "items_and_resources", "world_rules", "threads_and_payoffs"
  ],
  "checks": {
    "causality": {"status": "pass", "rationale": "因果链与既有事实一致", "evidence": [{"path": "candidate", "location": "相关场景", "quote": "正文中的逐字原句"}]},
    "timeline": {"status": "pass", "rationale": "时间推进与上下文一致", "evidence": [{"path": "candidate", "location": "时间锚点", "quote": "正文中的逐字原句"}]},
    "location": {"status": "pass", "rationale": "地点转换有正文依据", "evidence": [{"path": "candidate", "location": "地点锚点", "quote": "正文中的逐字原句"}]},
    "character_state": {"status": "pass", "rationale": "人物状态变化可追溯", "evidence": [{"path": "candidate", "location": "人物行动", "quote": "正文中的逐字原句"}]},
    "knowledge_boundaries": {"status": "pass", "rationale": "人物只使用已获得的信息", "evidence": [{"path": "candidate", "location": "信息获得处", "quote": "正文中的逐字原句"}]},
    "relationships_and_names": {"status": "pass", "rationale": "关系与称谓保持一致", "evidence": [{"path": "candidate", "location": "人物对话", "quote": "正文中的逐字原句"}]},
    "items_and_resources": {"status": "pass", "rationale": "物品取得和消耗可追溯", "evidence": [{"path": "candidate", "location": "物品出现处", "quote": "正文中的逐字原句"}]},
    "world_rules": {"status": "pass", "rationale": "事件符合已建立的世界规则", "evidence": [{"path": "candidate", "location": "规则生效处", "quote": "正文中的逐字原句"}]},
    "threads_and_payoffs": {"status": "pass", "rationale": "线索推进与回收状态明确", "evidence": [{"path": "candidate", "location": "线索推进处", "quote": "正文中的逐字原句"}]}
  },
  "reviewer": {
    "mode": "self",
    "reviewer_id": "agent-or-reviewer-id",
    "independent_context": false
  },
  "decision": "pass",
  "findings": [],
  "residual_risks": [],
  "quality_review_separate": true,
  "reviewed_at": "2026-09-12T04:00:00+00:00"
}
```

上面的哈希和引文是字段形状示例，不是可原样提交的数据。`prepare-audit` 会生成九个键，审稿者必须把每个空 `rationale` 和证据换成当前正文的真实内容；`path: candidate` 表示包内最终正文，引文必须逐字存在。每项 `status` 可为 `pass`、`warning` 或 `not_applicable`，只有 `not_applicable` 允许空证据。阻断级 finding 必须 `certainty: confirmed`，并包含 `id`、`problem`、`impact`、`suggested_fix`、`author_judgment` 和证据。`decision` 必须由 finding 严重度推导，提交要求 `pass`。

## `humanization-review.json`

这是执行声明和哈希绑定记录，不是运行时遥测：

```json
{
  "schema_version": 1,
  "status": "complete",
  "skill": "humanizer-zh",
  "chapter_number": 1,
  "reviewed_at": "2026-09-12T04:00:00+00:00",
  "outcome": "revised",
  "summary": "检查叙述自然度、声音一致性和机械模式，未改变事实",
  "protected_elements": ["POV", "人物口吻", "时代语言"],
  "source": {"path": "chapter-before-humanizer.md", "sha256": "..."},
  "result": {"path": "chapter.md", "sha256": "..."},
  "selection": {
    "selected": "result",
    "authorization_reference": "作者确认或项目级授权"
  }
}
```

`outcome: revised` 要求两个哈希不同，`unchanged` 要求相同；两个路径必须是包内不同 Markdown 文件。实际调用 `$humanizer-zh` 不能由 JSON 自证，执行者必须如实填写。

## 原创性报告

`novel_originality.py audit` 默认输出到 `staging/originality/`；`--output` 只能指定项目 `staging/` 下路径或项目外工作目录路径，不能覆盖已有文件。报告至少包含：

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-12T04:00:00+00:00",
  "project_root": "项目绝对路径",
  "decision": "pass",
  "originality_plan_sha256": "...",
  "candidate_files": [{"path": "...", "sha256": "..."}],
  "reference_files": [],
  "wording": {"status": "pass", "thresholds": {}, "exact_findings": [], "near_findings": []},
  "structure": {"status": "pass"},
  "limitations": ["..."],
  "report_path": "staging/originality/originality-audit-....json"
}
```

提交器要求 `decision: pass`、措辞层和结构层均为 `pass`，并且候选文件哈希覆盖最终 `chapter.md`。成功提交时 staging 报告以原始 bytes 复制到 `reviews/`，`commit` 返回归档后的路径。

## 质量审核报告

`novel_review.py prepare` 会同时生成审核包和报告模板。执行者应填写模板，不应从零手写。周期/完稿报告在交给 `record` 前必须包含以下顶层字段；这不是完整 finding schema：

```text
schema_version
report_kind
status
project_title
chapter_from
chapter_through
global_context_through
packet_sha256
reviewed_at
reviewed_dimensions
review_domain
continuity_review_separate
reviewer
decision
summary
findings
residual_risks
```

短故事报告还必须包含 `review_mode: completion`；长篇周期报告不带该字段。`source_snapshot` 不由审稿者手写：它来自哈希绑定的审核包，`record` 验证当前正典后才加入归档报告。九个固定质量维度中的历史键 `humanization_and_repetition` 语义为“叙述自然度、声音一致性与机械模式检查”，不表示规避检测。每个 finding 还必须有 `id`、`location`、`evidence`、`problem`、`impact`、`suggested_fix`、`severity`、`scope`、`author_judgment` 和非空 `chapters`。

## `export-manifest.json`

导出器自动生成，不建议手写。稳定的顶层结构为：

```json
{
  "schema_version": 1,
  "kind": "chinese-novel-derived-exports",
  "generated_at": "2026-09-12T04:00:00+00:00",
  "source": {
    "title": "书名",
    "language": "zh-CN",
    "genre": "题材",
    "project_id": "project-id",
    "current_chapter": 1,
    "chapter_count": 1,
    "source_snapshot_sha256": "...",
    "chapters": []
  },
  "encoding": "UTF-8 without BOM",
  "line_endings": "LF",
  "unicode_normalization": "NFC",
  "fanqie_title_mode": "filename_only_body_text",
  "formats": ["txt"],
  "outputs": [],
  "delivery_quality": {
    "schema_version": 1,
    "status": "pass",
    "validated_at": "2026-09-12T04:00:00+00:00",
    "failure_policy": "fail_closed",
    "compatibility_scope": "verified_profiles_only",
    "source_text_gate": {"status": "pass"},
    "output_normalizations_applied": {},
    "profiles": []
  }
}
```

长篇的 `kind` 是 `chinese-novel-derived-exports`，`fanqie_title_mode` 是 `filename_only_body_text`。短故事分别使用 `chinese-short-story-derived-exports` 和 `single_story_filename_only_body_text`，并额外要求 `source.work_type: short_story` 与顶层 `fanqie_publication_profile: fanqie_short_story`；这两个条件字段不会写入长篇清单。

每个 `outputs` 记录由导出器生成，必须带相对路径、格式、字节数、SHA-256、交付 profile、质量结果和规范化计数；`status` 会复检这些字段。只有 `status: fresh`、`delivery_quality: pass` 且目标 profile 在 `verified_profiles` 中时，才可把包交付给平台。

## 平台配置名

仓库内部名称按用途区分：

| 名称 | 含义 |
|---|---|
| `fanqie_public` | 长篇项目的番茄公开发布配置 |
| `fanqie_short_story_public` | 短故事项目的番茄公开发布配置 |
| `fanqie-serial` | 导出/上传质量 profile 名称，表示长篇逐章纯正文 |
| `fanqie-short-story` | 导出/上传质量 profile 名称，表示短故事单文件 |

这些名称只描述本仓库的配置映射，不推断平台最新规则；发布前仍须读取当前官方页面并获得明确授权。

## `publication-feedback.jsonl`

`research/publication-feedback.jsonl` 是追加式 JSONL 文件，每一行必须是一个独立对象；空行、注释和跨行 JSON 都不属于该格式。下面的 JSON Schema 是发布反馈字段的唯一真源。`publication-feedback.md` 只描述采集、授权和回流顺序，不再复制一套字段定义。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://novel-studio.local/schemas/publication-feedback.schema.json",
  "title": "novel-studio publication feedback record",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "record_id",
    "project_id",
    "observed_at",
    "platform",
    "platform_work_id",
    "scope",
    "metrics",
    "evidence_path",
    "evidence_sha256",
    "interpretation",
    "next_decision",
    "authorization_reference",
    "conclusion",
    "status"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "record_id": {
      "type": "string",
      "pattern": "^feedback-[a-z0-9][a-z0-9-]{2,63}$"
    },
    "project_id": {
      "type": "string",
      "pattern": "^[a-z0-9][a-z0-9-]{2,63}$"
    },
    "observed_at": {"type": "string", "format": "date-time"},
    "platform": {"type": "string", "minLength": 1},
    "platform_work_id": {"type": "string", "minLength": 1},
    "scope": {"$ref": "#/$defs/scope"},
    "metrics": {
      "type": "object",
      "minProperties": 1,
      "additionalProperties": {"$ref": "#/$defs/metric"}
    },
    "evidence_path": {
      "type": "string",
      "minLength": 1,
      "description": "相对当前工作目录的证据文件路径，不得是本机绝对路径"
    },
    "evidence_sha256": {
      "type": "string",
      "pattern": "^[0-9a-f]{64}$"
    },
    "interpretation": {"$ref": "#/$defs/interpretation"},
    "next_decision": {"type": "string", "minLength": 1},
    "authorization_reference": {"type": "string", "minLength": 1},
    "conclusion": {
      "type": "string",
      "enum": ["supports", "does_not_support", "inconclusive"]
    },
    "status": {"const": "recorded"}
  },
  "$defs": {
    "scope": {
      "type": "object",
      "additionalProperties": false,
      "required": ["chapter_from", "chapter_through", "window"],
      "properties": {
        "chapter_from": {"type": "integer", "minimum": 1},
        "chapter_through": {"type": "integer", "minimum": 1},
        "window": {"type": "string", "minLength": 1}
      }
    },
    "metric": {
      "type": "object",
      "additionalProperties": false,
      "required": ["value", "definition", "evidence_location"],
      "properties": {
        "value": {
          "oneOf": [
            {"type": "number"},
            {"type": "string"},
            {"type": "null"}
          ]
        },
        "unit": {"type": "string", "minLength": 1},
        "definition": {"type": "string", "minLength": 1},
        "evidence_location": {"type": "string", "minLength": 1}
      }
    },
    "interpretation": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "observed_facts",
        "possible_explanations",
        "counterfactors"
      ],
      "properties": {
        "observed_facts": {
          "type": "array",
          "minItems": 1,
          "items": {"type": "string", "minLength": 1}
        },
        "possible_explanations": {
          "type": "array",
          "items": {"type": "string", "minLength": 1}
        },
        "counterfactors": {
          "type": "array",
          "minItems": 1,
          "items": {"type": "string", "minLength": 1}
        }
      }
    }
  }
}
```

有效记录的最小可读形状如下；`record_id`、时间、哈希、指标值和引文必须替换为本次实际数据，不能把示例直接写入项目：

```json
{
  "schema_version": 1,
  "record_id": "feedback-20260912-opening",
  "project_id": "harbor-clock",
  "observed_at": "2026-09-12T12:00:00+08:00",
  "platform": "fanqie",
  "platform_work_id": "平台作品 ID",
  "scope": {
    "chapter_from": 1,
    "chapter_through": 5,
    "window": "发布后 7 天"
  },
  "metrics": {
    "reads": {
      "value": 1234,
      "definition": "平台原字段",
      "evidence_location": "截图第 1 页"
    },
    "retention": {
      "value": 0.42,
      "unit": "ratio",
      "definition": "平台原字段",
      "evidence_location": "截图第 1 页"
    }
  },
  "evidence_path": "research/evidence/feedback-20260912.png",
  "evidence_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "interpretation": {
    "observed_facts": ["第 1-5 章的留存率为平台页面所示值"],
    "possible_explanations": ["开篇承诺与读者预期可能存在偏差"],
    "counterfactors": ["观察窗口较短，尚未排除推荐流量变化"]
  },
  "next_decision": "下一次质量审核重点检查开篇承诺和第 3-5 章转折",
  "authorization_reference": "作者确认记录本次数据用于项目研究",
  "conclusion": "inconclusive",
  "status": "recorded"
}
```

`scope.chapter_from` 不得大于 `scope.chapter_through`；平台没有定义某个指标时，保留原字段名并把 `definition` 写为 `unknown`，不要自行换算或与其他作品拼成总分。`conclusion` 的中文含义依次是“支持”“不支持”“尚无结论”。
