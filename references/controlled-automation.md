# 受控自动化与章节提交

在候选审批、框架确认、批量研究、正式写章、短故事完稿提交或重大改稿时读取本文件。正典写入顺序、租约、完整暂存清单和收尾语义以 [commit-protocol.md](commit-protocol.md) 为规范真源；机器可读字段以 [schemas-and-cli.md](schemas-and-cli.md) 为准。本文件只补充自然化行为和人工确认边界。工作隔离和单写者协议见 [workspace-isolation.md](workspace-isolation.md)，项目目录和真源优先级见 [project-contract.md](project-contract.md)，原创性闸门见 [originality-audit.md](originality-audit.md)；短故事另读 [short-story-mode.md](short-story-mode.md)。

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
python -X utf8 .\scripts\novel_project.py research-state "<project-root>" --candidate-approval approved --authorization-reference "作者于本轮批准候选 W-001 至 W-006" --workspace "<workspace-root>" --work-id "<work-id>"
python -X utf8 .\scripts\novel_project.py research-state "<project-root>" --deep-analysis in_progress --authorization-reference "候选已批准，开始本机机制拆解" --workspace "<workspace-root>" --work-id "<work-id>"
```

未批准候选时脚本拒绝进入 `in_progress` 或 `complete`。状态命令只写 frontmatter；审批记录正文和研究结论仍需同步更新。

## 框架确认状态

框架正文不能先直接改入项目再补状态；那会改变项目哈希并被写闸门拒绝。先在项目外的当前工作目录准备一个完整同步包，包含以下九个输入：

```text
<work-root>/framework-sync/
|-- planning/framework-session.md
|-- story-bible/premise.md
|-- story-bible/cast.md
|-- story-bible/world.md
|-- story-bible/style-guide.md
|-- outlines/master-outline.md
|-- memory/decisions.md
|-- memory/book-summary.md
`-- project-settings.json
```

`project-settings.json` 只允许 `schema_version`、`pov`、`tense` 和 `target_words`；字段形状见 [schemas-and-cli.md](schemas-and-cli.md)。命令把这些值合并进现有 `novel.json`，保留 `title`、`work_type`、`current_chapter`、审核策略和未知兼容字段；框架首次确认时把 `status: planning` 自动推进为 `drafting`，并自行维护 `updated_at`。

取得租约并通过 `write-check` 后，用一个事务同步正文和框架状态：

```powershell
python -X utf8 .\scripts\novel_project.py framework-sync "<project-root>" "<work-root>\framework-sync" --stage complete --confirmation confirmed --requirements-confidence 96 --story-confidence 97 --authorization-reference "作者本轮确认完整框架并要求开始写" --workspace "<workspace-root>" --work-id "<work-id>"
```

`framework-sync` 会稳定读取九个输入，要求来源位于当前 `work-id` 的工作目录，拒绝链接路径和项目内来源，并在同一正典事务中检查源与目标 CAS、写入八份 Markdown、合并 `novel.json`、刷新零章基线（如适用）和记录状态。它会阻断任一信心低于 95、`complete` 与 `confirmed` 不成对、已确认的 POV/时态为空、设置包试图覆盖受保护字段，或任一同步 Markdown 为空/仍含待确认占位符的包。已有正文时，开放失效项的 `changed_paths` 必须覆盖包内每个实际变化的正典路径，不能借用不相关失效项；语义未变的重复同步返回 `already_current`，不使连续性基线过期。同步完成后无需再运行 `framework-state`；后者只适合在正典内容已经稳定且当前租约有效时更新单独的会话 frontmatter。

若历史操作已经直接改动了项目文件导致基准哈希失配，先停止继续写入，逐项回读并按 [workspace-isolation.md](workspace-isolation.md) 的 `base-refresh --accept-external-change --validation-report` 生成一次哈希绑定验证收据；不要用普通 `base-refresh` 或再次运行 `framework-state` 掩盖未审查的变化。

## 自然化提交记录

正式正文先写入当前工作目录 `drafts/`。作者明确允许进入正式提交流程、当前工作取得项目租约且通过 `write-check` 后，才按 [commit-protocol.md](commit-protocol.md) 的清单把已完成输入组装到 `staging/chapters/<chapter-package>/`。`commit.json`、`humanization-review.json`、状态增量和审计对象的字段只在 [schemas-and-cli.md](schemas-and-cli.md) 维护。

执行者必须先保存调用前正文，实际调用 `$humanizer-zh`，再保存最终正文并如实填写哈希绑定记录。`outcome` 只能是 `revised` 或 `unchanged`：前者要求原稿与结果稿哈希不同，后者要求相同；二者必须是包内不同的 Markdown 路径，结果路径必须等于 `commit.json.chapter_file`。摘要、保护项与采用授权不得为空。该记录能验证文件、哈希与执行声明，不能从技术上证明运行时加载过 Skill，因此不能只生成 JSON 代替真实调用。

记忆卡必须链接最终 `manuscript_filename`；连续性状态的 `through_chapter` 必须推进到本章；索引摘要只能写正文已经发生的事实。连续性文件的生成、填写、重绑定和证据要求见 [continuity.md](continuity.md)，不能手写哈希绕过。`short_story` 的 `chapter_number` 必须是 `1`，其他差异只在 [short-story-mode.md](short-story-mode.md) 维护。

## 提交行为

写章前先准备并真实完成连续性上下文：

```powershell
python -X utf8 .\scripts\novel_continuity.py prepare-context "<project-root>" --chapter 1 --output "<work-root>\drafts\continuity-context.json"
```

正文完成并通过结构、人物、连续性与原创性初检后，先把该版本保存为 `chapter-before-humanizer.md`，实际调用 `$humanizer-zh`，把采用后的全文写入 `chapter.md`，再填写 `humanization-review.json`。若结果有变化，重新完成结构、连续性与原创性检查。随后只针对最终 `chapter.md` 执行 [commit-protocol.md](commit-protocol.md) 的唯一命令顺序。

`prepare-context` 的输出必须先留在工作目录。取得租约并通过 `write-check` 后，才把已经回读、补充完整的上下文复制到暂存包；这一步是写入项目 staging，不是 `prepare-context` 的豁免路径。原创性命令默认把报告写入 `staging/originality/`，由 `commit-chapter` 在成功事务中归档到 `reviews/`。

各阶段必须完成相应人工/Agent 填写：`prepare-context` 后真实回读必读文件并把上下文设为 `complete`；自然化记录必须来自真实 Skill 调用；状态增量完成后才能绑定审计；绑定后才由指定审稿者填写九维审计；原创性报告必须覆盖最终正文。正文、上下文或状态增量变化后，所有旧审计立即失效，必须重做，不能只改哈希。完整顺序只在 [commit-protocol.md](commit-protocol.md) 维护。

提交器的完整预检清单、事务覆盖范围和失败回滚语义以 [commit-protocol.md](commit-protocol.md) 为准；本模式额外要求自然化记录来自真实 `$humanizer-zh` 调用，且默认第 5 章/短故事唯一正文后的两类审核未逾期。任一审核到期或未通过时，下一章、正式导出和平台交付继续阻断。

暂存包成功后保留，作为提交输入与审计追溯记录；不要自动删除。需要清理时先确认范围和恢复需求。

## 重大改稿

普通新章提交使用事务回滚。会覆盖已提交正文、重排章节或追溯修改正典时，先按 [revision.md](revision.md) 建立 `revisions/snapshots/<timestamp>/` 恢复点，再执行影响图。原创报告、章节卡、索引、连续性账本和 SQLite 缓存都要按新正文重建；缓存永远不能反向覆盖文件真源。
