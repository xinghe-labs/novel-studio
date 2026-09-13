# 连续性硬门禁

已有旧项目首次接入连续性账本时，先运行一次只增式安装命令；它只补齐缺失目录和模板，不改写既有正文：

```powershell
python -X utf8 .\scripts\novel_continuity.py install "<project-root>" --workspace "<workspace-root>" --work-id "<work-id>"
```

安装后先运行 `status`。若返回 `baseline_required`，必须完成全书基线和独立审核，才能继续续写或导出。

在续写、写后提交、跨章查错、旧章修订、导出或平台交付前读取本文件，并与 [long-term-memory.md](long-term-memory.md)、[controlled-automation.md](controlled-automation.md) 配合使用。此协议同时适用于连载长篇和短故事，但两类项目仍使用各自独立的正文、审核和导出流程。

脚本负责封存正典、绑定哈希、校验证据、阻断过期报告和传播修订影响；语义判断必须由 Agent 精读原文完成。不能把“脚本运行通过”冒充“内容没有矛盾”。

## 三层防线

1. **写前约束**：先生成绑定当前正典的 `continuity-context.json`，读完自动上下文和被触及事实的原文证据，再确定章节合同、允许变化、禁止变化和风险等级。
2. **写后对账**：用 `state-delta.json` 精确列出进入状态、正文造成的每项变化、退出状态、事实增删改和有意例外；每项变化必须能回放成最终 `continuity-state.json`。
3. **独立复核**：对九个连续性维度给出逐字证据。风险章由独立只读审稿 Agent 处理；连载每 5 章再做一次全局独立连续性基线，短故事完整正文提交后做一次。

只要任一层过期、缺证据或发现确定矛盾，正典提交、派生导出和平台上传全部停止。

## 正典权威

不要使用单一文件覆盖所有事实，按事实类型裁决：

- 用户本轮明确决定始终最高；把决定写入 `memory/decisions.md` 后才可跨上下文依赖。
- 已锁定的世界规则、人物底层设定、作品承诺以故事圣经为准。
- 已经发生了什么以已提交 Markdown 正文为准；摘要、索引和账本与正文冲突时先修账本，不能反向改正文来迁就摘要。
- `character_knows`、`character_believes`、`character_claims`、`reader_knows` 分开记录。人物说过不等于人物相信，更不等于客观真相。
- 大纲项必须标为 `locked`、`planned`、`optional` 或 `abandoned`。只有 `locked` 是不能无授权改写的未来约束；`planned` 是当前方案，`optional` 只是候选，`abandoned` 不得在续写时复活。
- 有意撒谎、不可靠叙述、误导、梦境、时间跳跃和规则例外写入 `intentional-exceptions.jsonl`；未登记时只能当疑点，不能擅自判为漏洞或自动修掉。

## 九个连续性维度

每章审计和全局基线都覆盖：

1. `causality`：行为动机、触发、结果和后续反应。
2. `timeline`：日期、昼夜、年龄、时长、倒计时、先后顺序和旅行耗时。
3. `location`：人物移动、同场关系、空间限制和同一时刻是否分身。
4. `character_state`：伤势、疲劳、能力、情绪余波、身份、目标和生死状态。
5. `knowledge_boundaries`：谁知道、相信、声称、隐瞒什么，信息从何获得，秘密是否提前泄露。
6. `relationships_and_names`：姓名、称谓、亲疏、权力、信任、公开身份和关系阶段。
7. `items_and_resources`：物品持有人、数量、金钱、能力、消耗、来源、去向和代价守恒。
8. `world_rules`：能力成本、制度后果、地理、技术、社会规则、例外和公开程度。
9. `threads_and_payoffs`：伏笔、承诺、线索的埋设、延期、回收、废弃和重复。

节奏、钩子、文风、吸引力、叙述自然度、声音一致性和语言格式不写入连续性结论，另走 [periodic-review.md](periodic-review.md) 的质量审稿，避免一个模糊总分掩盖正典错误。

## 项目文件

安装或升级后增加：

```text
continuity/
|-- policy.json
|-- head.json
|-- canon-facts.jsonl
|-- intentional-exceptions.jsonl
|-- dependencies.json
|-- invalidations.json
|-- state.json
|-- timeline.md
|-- threads.md
`-- baselines/
reviews/
`-- continuity/
```

- `head.json`：当前正典哈希、父哈希、覆盖章数、全局基线和最新章审计。
- `canon-facts.jsonl`：稳定事实库。每条至少有 `fact_id`、事实类别、主语、谓语、宾语、有效章节、重要度和原文来源；`author_plan` 还要有四态 `plan_status`。
- `dependencies.json`：每章依赖的旧章、事实 ID、证据路径、风险触发器和审计哈希。
- `invalidations.json`：旧章或核心设定变化造成的开放失效范围。存在开放项时禁止续写、导出和上传。
- `baselines/`：独立全局审核通过后封存的不可混用快照。
- `reviews/continuity/`：逐章审计和未通过的全局审计报告。

`state.json` 只保留当前可操作状态，不写成全文摘要。历史进入 `timeline.md`，线索生命周期进入 `threads.md`，章节事实和回调锚点进入 `memory/chapters/NNNN.md`。

## 旧项目首次启用

旧项目有正文时，升级只添加结构，不会自动宣称已有章节没有问题：

```powershell
python -X utf8 .\scripts\novel_project.py upgrade "<project-root>" --workspace "<workspace-root>" --work-id "<work-id>"
python -X utf8 .\scripts\novel_continuity.py prepare-baseline "<project-root>" --output "<work-root>\reviews\continuity\baseline-packet.json"
```

`prepare-baseline` 是只读项目操作，只在工作目录生成数据包和报告模板。独立审稿 Agent 必须精读 `required_full_text_chapters` 的全部正文，填写九维结果、每章依赖、稳定事实、有意例外、残余风险和逐字证据。非空项目的 `reviewer.mode` 必须为 `independent`，`independent_context` 必须为 `true`，并记录实际 reviewer ID。

重大问题按关联关系分批询问作者：同一人物状态链一批、同一时间线一批、同一物品或数值账一批、同一核心规则或伏笔一批。不要一次抛出无结构的长清单。结论映射：

- 确定矛盾：`certainty: confirmed`，严重度 `error` 或 `blocker`，结论为 `needs_author` 或 `block`。
- 疑似误导、伏笔或信息不足：`certainty: suspected`，严重度 `warning` 或 `note`，可以保留但要登记风险。
- 已确认的有意例外：`certainty: intentional_exception`，严重度 `warning` 或 `note`，并同步例外库。

只有报告为 `pass` 才在取得项目写入租约、通过 `write-check` 后封存：

```powershell
python -X utf8 .\scripts\novel_continuity.py record-baseline "<project-root>" --packet "<packet>" --report "<completed-report>" --authorization-reference "作者确认记录本次连续性基线" --workspace "<workspace-root>" --work-id "<work-id>"
```

未通过报告可以保存，但不建立可用 `head`，也不允许继续写、导出或上传。

## 每章写前上下文

正式写作前生成本章上下文：

```powershell
python -X utf8 .\scripts\novel_continuity.py prepare-context "<project-root>" --chapter 56 --output "<work-root>\drafts\continuity-context.json"
```

`prepare-context` 只读取项目，输出必须位于项目外的工作目录。Agent 回读并补全上下文后，取得项目租约并通过 `write-check`，再把它复制到 `staging/chapters/0056-title/continuity-context.json`；项目 staging 没有写前豁免。

Agent 必须真实读取并填入 `required_reading`：全书摘要、作者决策、章节索引、故事圣经、总纲、当前状态、时间线、线索账本、稳定事实库、有意例外、依赖图、失效记录和最近 3 章正文/记忆卡。空 JSONL 账本用文件哈希证明读取，不伪造引文。任何 `touched_fact_ids` 的原始证据也必须回读。

上下文还要明确：

- 本章交付、POV、故事时间、地点、目的、入口状态和出口方向。
- 被触及的人物、关系、地点、物品、规则、数值、秘密、线索和事实 ID。
- 允许改变与禁止改变的状态，以及不能静默违反的 invariants。
- 风险分类、回调跨度和作者确认引用。

以下任一项强制独立审稿：复杂时间线、`callback_span >= 10` 的回调、秘密知情边界、核心世界规则、重要关系、关键物品、数值账、疑似冲突、卷首卷末、重大揭示、人物命运和锁定大纲。卷首卷末、重大反转、人物命运、核心真相、核心规则和锁定大纲还必须先取得作者确认。普通章可以由当前 Agent 审核。

## 写后状态增量与审计

完整暂存包清单只在 [commit-protocol.md](commit-protocol.md) 维护。连续性子集固定为：

```text
continuity-context.json
continuity-state.json
state-delta.json
continuity-audit.json
```

各 JSON 字段见 [schemas-and-cli.md](schemas-and-cli.md)。

先生成模板：

```powershell
python -X utf8 .\scripts\novel_continuity.py prepare-audit "<project-root>" "staging\chapters\0056-title" --workspace "<workspace-root>" --work-id "<work-id>"
```

填写 `state-delta.json` 后把 `status` 设为 `complete`。每项状态变化必须写原因和正文逐字证据；变化列表必须能从旧 `state.json` 精确重放为暂存的新状态。事实变更和例外变更同样需要来源证据，不能把大纲预期写成已经发生。

状态增量定稿后必须重新绑定审计模板：

```powershell
python -X utf8 .\scripts\novel_continuity.py bind-audit "<project-root>" "staging\chapters\0056-title" --workspace "<workspace-root>" --work-id "<work-id>"
```

然后才填写 `continuity-audit.json`。九维检查都要有理由和证据；确定矛盾不能给 `pass`。普通小错误可以在隔离暂存区自动修正，然后重新生成状态增量、重新绑定并复审。涉及核心设定、人物命运、重要关系、世界规则或重大伏笔时立即停止，向作者询问，不能替作者决定。

审计完成后检查：

```powershell
python -X utf8 .\scripts\novel_continuity.py check-package "<project-root>" "staging\chapters\0056-title"
```

任何正文、上下文或状态增量在审计后发生变化，哈希绑定立即过期。不得手改哈希；保留旧包作为记录，在新暂存包中重新执行流程。

## 每五章全局复核

连载第 5、10、15 章等检查点章节可以先通过逐章审计并原子提交。提交后 `continuity status` 变为 `review_due`，第 6、11、16 章的上下文准备和提交，以及所有导出、上传都会被阻断。使用完整基线流程独立复核从第 1 章到当前检查点的正典和依赖；通过后封存新基线。

短故事只提交唯一 `0001` 全文，提交后立即触发同样的全篇独立连续性基线。它与质量完稿审核是两份报告，两者都通过才可定稿导出。

状态命令：

```powershell
python -X utf8 .\scripts\novel_continuity.py status "<project-root>"
```

- `current`：正典哈希、基线、依赖和失效项全部有效。
- `review_due`：结构未损坏，但独立全局连续性审核到期；下一章和交付被锁住。
- `baseline_required`：旧正文尚未建立全量基线。
- `stale`：正典被绕过修改、审计或基线过期、存在开放失效项或元数据损坏。
- `not_installed`：先运行项目升级；不能用旧流程继续正式交付。

## 旧章修订与传播

先在工作目录提出修改，不直接覆盖正典。用依赖图计算范围：

```powershell
python -X utf8 .\scripts\novel_continuity.py impact "<project-root>" --changed-path "manuscript/chapters/0012-title.md" --change-type timeline
```

局部事实按事实 ID 和章节依赖闭包传播；核心世界规则、人物命运、核心关系、核心真相或无法确定边界的变化，从最早受影响章开始重审全部后续章节。作者批准本地修订后，取得租约、建立恢复快照并登记失效：

```powershell
python -X utf8 .\scripts\novel_continuity.py invalidate "<project-root>" --changed-path "manuscript/chapters/0012-title.md" --change-type timeline --reason "修正第12章日期" --authorization-reference "作者本轮确认本地修订" --workspace "<workspace-root>" --work-id "<work-id>"
```

同步修改正文、索引、记忆卡、状态、时间线、事实库、例外库、线索和大纲，再做覆盖受影响范围的新全局基线。开放失效项由新基线解决前，不能续写、导出或上传。

本地 Markdown 始终先修改并审核。线上版本只有在本地验证、连续性审核、质量审核、重新导出全部通过后，另列线上修订清单并再次取得明确确认才可改；平台副本永远不能反向成为正典。
