# 受控自动化与章节提交

在候选审批、框架确认、批量研究、正式写章、短故事完稿提交或重大改稿时读取本文件。正典写入顺序、租约和收尾语义以 [commit-protocol.md](commit-protocol.md) 为规范真源；本文件只补充自然化记录、暂存包和人工确认边界。工作隔离和单写者协议见 [workspace-isolation.md](workspace-isolation.md)，项目目录和真源优先级见 [project-contract.md](project-contract.md)，原创性闸门见 [originality-audit.md](originality-audit.md)；短故事另读 [short-story-mode.md](short-story-mode.md)。

## 写入租约

不同工作可以并行读取项目并在各自 `workspaces/<work-id>/` 中生成草稿。租约取得、心跳续租、双重 `write-check`、受控回收和收尾顺序统一遵循 [commit-protocol.md](commit-protocol.md)；本节只保留最短调用提示：

```powershell
python -X utf8 .\scripts\novel_workspace.py lock-acquire "<workspace-root>" "<work-id>"
python -X utf8 .\scripts\novel_workspace.py write-check "<workspace-root>" "<work-id>"
```

长任务每五分钟内运行 `lock-renew`；`write-check` 失败时停止写入并回读项目变化。执行完已授权写入和项目验证后：

```powershell
python -X utf8 .\scripts\novel_workspace.py base-refresh "<workspace-root>" "<work-id>" --validation-reference "项目与长期记忆验证通过"
python -X utf8 .\scripts\novel_workspace.py lock-release "<workspace-root>" "<work-id>"
```

命令失败时也要尝试释放自己持有的租约；不能强制释放其他工作的有效租约。未确认内容继续留在工作目录，不能为了避免锁冲突提前写入项目 `staging/`。

## 自动与人工边界

可以自动执行：

- 公开资料采集、原始响应保存、来源登记和哈希校验。
- 从正典文件重建或增量更新 SQLite 检索缓存。
- 措辞重合扫描、结构映射检查和审计报告保存。
- 写前连续性上下文、状态增量模板、审计模板、全局基线包和质量审核包的生成。
- 到达项目检查点后的只读诊断；普通小错误只在隔离暂存区自动修复并重新审计。
- 已获授权的工作流状态写回。
- 已确认框架下的章节预检、索引生成、事务提交、验证和缓存同步。

必须由作者确认：

- 哪些候选作品进入深度拆解。
- 选用哪个原创方向，以及哪些建议成为正典。
- 完整故事框架、重大剧情变化和追溯改稿。
- 卷首卷末、重大反转、人物命运、核心真相、核心世界规则、重要关系和锁定大纲的变化。
- `review` 或 `block` 原创性发现如何裁决。
- 正式开始正文及任何会覆盖、删除、发布或外传内容的动作。

脚本存在不等于获得确认。每次受控状态变化都用简短 `authorization_reference` 记录本轮作者确认来源。

连续性风险触发独立审稿时，协调 Agent 不得让写稿上下文自称“独立”。运行环境支持子 Agent 时，创建一个只读审稿 Agent，只给它正典快照、候选正文、状态增量和审核合同，禁止它修改文件；不支持子 Agent 时使用新的隔离上下文。记录实际 `reviewer_id`、`mode: independent` 和 `independent_context: true`。每 5 章全局连续性基线与每次周期质量审核都必须走独立上下文。

## 研究状态机

候选审批和深度分析分开：

```text
pending / revision_requested
  -> 作者明确批准
approved + not_started
  -> 允许 deep_analysis=in_progress
  -> complete
```

```powershell
python -X utf8 .\scripts\novel_project.py research-state "<project-root>" --candidate-approval approved --authorization-reference "作者于本轮批准候选 W-001 至 W-006"
python -X utf8 .\scripts\novel_project.py research-state "<project-root>" --deep-analysis in_progress --authorization-reference "候选已批准，开始本机机制拆解"
```

未批准候选时脚本拒绝进入 `in_progress` 或 `complete`。状态命令只写 frontmatter；审批记录正文和研究结论仍需同步更新。

## 框架确认状态

先把确认单同步到故事圣经、总纲、作者决策和全书记忆，再执行状态写回：

```powershell
python -X utf8 .\scripts\novel_project.py framework-state "<project-root>" --stage complete --confirmation confirmed --requirements-confidence 96 --story-confidence 97 --authorization-reference "作者本轮确认完整框架并要求开始写"
```

脚本会阻断以下情况：任一信心低于 95、`complete` 与 `confirmed` 不成对、故事圣经或总纲仍含待确认占位符。它不替 Agent 检查确认单正文是否与正典语义一致，因此写回前仍按 [interactive-planning.md](interactive-planning.md) 完成同步检查。

## 章节暂存包

正式章节先写入当前工作目录 `drafts/`。作者明确允许进入正式提交流程、当前工作取得项目租约且通过 `write-check` 后，才把提交输入写入项目 `staging/chapters/<chapter-package>/`，通过连续性和原创性检查后再提交：

```text
staging/chapters/0001-broken-watch/
|-- commit.json
|-- chapter-before-humanizer.md
|-- chapter.md
|-- humanization-review.json
|-- memory.md
|-- continuity-state.json
|-- continuity-context.json
|-- state-delta.json
|-- continuity-audit.json
|-- timeline.md          可选，完整替换稿
|-- threads.md           可选，完整替换稿
`-- book-summary.md      可选，完整替换稿
```

`commit.json` 最小结构：

```json
{
  "schema_version": 1,
  "chapter_number": 1,
  "manuscript_filename": "0001-坏表.md",
  "chapter_file": "chapter.md",
  "humanization_review_file": "humanization-review.json",
  "memory_file": "memory.md",
  "continuity_state_file": "continuity-state.json",
  "continuity_context_file": "continuity-context.json",
  "state_delta_file": "state-delta.json",
  "continuity_audit_file": "continuity-audit.json",
  "originality_report": "reviews/originality-audit-...json",
  "index": {
    "title": "坏表",
    "pov": "林某D",
    "story_time": "第一日清晨",
    "location": "雾港旧邮局",
    "fact_summary": "林某D收到一只倒走的怀表",
    "key_change": "获得怀表并决定寻找寄件人",
    "thread_ids": ["T-001"]
  }
}
```

`humanization-review.json` 最小结构：

```json
{
  "schema_version": 1,
  "status": "complete",
  "skill": "humanizer-zh",
  "chapter_number": 1,
  "reviewed_at": "ISO-8601 时间",
  "outcome": "revised",
  "summary": "删除机械排比并调整无意的齐整句式，未改变情节事实",
  "protected_elements": ["POV", "人物口吻", "时代语言"],
  "source": {
    "path": "chapter-before-humanizer.md",
    "sha256": "调用前原稿的 SHA-256"
  },
  "result": {
    "path": "chapter.md",
    "sha256": "最终结果稿的 SHA-256"
  },
  "selection": {
    "selected": "result",
    "authorization_reference": "作者确认或项目级逐章自然化授权"
  }
}
```

`outcome` 只能是 `revised` 或 `unchanged`：前者要求两个哈希不同，后者要求两个哈希相同。`source` 与 `result` 必须是暂存包内两个不同的 Markdown 文件，`result.path` 必须指向 `commit.json.chapter_file`。摘要、保护项与授权依据都不得为空。记录只能验证文件、哈希与执行声明，不能从技术上证明模型运行时加载过 Skill；执行者必须先真实调用 `$humanizer-zh`，再如实填写记录，不能只生成 JSON。

记忆卡必须链接最终 `manuscript_filename`；连续性状态的 `through_chapter` 必须推进到本章；索引摘要只能写正文已经发生的事实。连续性三个文件的生成、填写、重绑定和证据要求见 [continuity.md](continuity.md)，不能手写哈希绕过。

`short_story` 使用同一暂存格式，但 `chapter_number` 必须是 `1`，`chapter.md` 必须包含完整故事，记忆卡是全篇记忆卡，索引只有一行。技术命令仍叫 `commit-chapter`，但成品不会显示“第一章”。提交器拒绝短故事的 `0002`；后续修改唯一主稿属于重大改稿，不能伪装成新增章节。

## 提交

写章前先准备并真实完成连续性上下文：

```powershell
python -X utf8 .\scripts\novel_continuity.py prepare-context "<project-root>" --chapter 1 --output "<project-root>\staging\chapters\0001-broken-watch\continuity-context.json"
```

正文完成并通过结构、人物、连续性与原创性初检后，先把该版本保存为 `chapter-before-humanizer.md`，实际调用 `$humanizer-zh`，把采用后的全文写入 `chapter.md`，再填写 `humanization-review.json`。若结果有变化，重新完成结构、连续性与原创性检查。随后只针对最终 `chapter.md` 运行正式的哈希绑定审计与提交：

```powershell
python -X utf8 .\scripts\novel_originality.py audit "<project-root>" --candidate "staging/chapters/0001-broken-watch/chapter.md"
python -X utf8 .\scripts\novel_continuity.py prepare-audit "<project-root>" "staging\chapters\0001-broken-watch"
python -X utf8 .\scripts\novel_continuity.py bind-audit "<project-root>" "staging\chapters\0001-broken-watch"
python -X utf8 .\scripts\novel_continuity.py check-package "<project-root>" "staging\chapters\0001-broken-watch"
python -X utf8 .\scripts\novel_project.py commit-chapter "<project-root>" "staging/chapters/0001-broken-watch" --workspace "<workspace-root>" --work-id "<work-id>"
```

命令之间必须完成相应人工/Agent 填写：`prepare-context` 后真实回读必读文件并把上下文设为 `complete`；自然化记录必须来自真实 Skill 调用；`prepare-audit` 后完成状态增量并设为 `complete`；`bind-audit` 后才由指定审稿者填写九维审计。正文、上下文或状态增量变化后，旧审计立即失效，必须重做，不能只改哈希。

提交器的完整预检清单、事务覆盖范围和失败回滚语义以 [commit-protocol.md](commit-protocol.md) 为准；本模式额外要求自然化记录来自真实 `$humanizer-zh` 调用，且默认第 5 章/短故事唯一正文后的两类审核未逾期。任一审核到期或未通过时，下一章、正式导出和平台交付继续阻断。

暂存包成功后保留，作为提交输入与审计追溯记录；不要自动删除。需要清理时先确认范围和恢复需求。

## 重大改稿

普通新章提交使用事务回滚。会覆盖已提交正文、重排章节或追溯修改正典时，先按 [revision.md](revision.md) 建立 `revisions/snapshots/<timestamp>/` 恢复点，再执行影响图。原创报告、章节卡、索引、连续性账本和 SQLite 缓存都要按新正文重建；缓存永远不能反向覆盖文件真源。
