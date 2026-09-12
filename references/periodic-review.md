# 连载周期质量审核与短故事质量完稿审核

在连续写作达到质量审核检查点、查询审核状态、处理节奏/文风/吸引力问题、准备下一章，或短故事唯一正文已经提交时读取本文件。连续性错误单独使用 [continuity.md](continuity.md) 的九维报告和全局基线；本文件只负责质量，不把两类结论混成一个分数。一般审稿原则见 [revision.md](revision.md)，项目写入与租约要求见 [controlled-automation.md](controlled-automation.md)；短故事同时读取 [short-story-mode.md](short-story-mode.md)。

## 默认策略

新项目默认启用以下策略；旧项目缺少该字段时也按同一默认值运行：

```json
{
  "periodic_review": {
    "enabled": true,
    "interval_chapters": 5,
    "block_next_commit": true
  }
}
```

- 第 1-4 章不触发；第 5 章先正常提交，再立即审核第 1-5 章。
- 第 1-5 章质量审核通过后，下一个检查点是第 10 章。届时重点精读第 6-10 章，并结合前文检查读者承诺、节奏、人物弧和留存体验；同一检查点的连续性由另一份独立全局报告负责。
- 审核到期不是结构损坏，因此 `novel_project.py validate` 返回警告，不回滚刚提交的检查点章节。
- 默认只保存报告，不自动修改正文。存在 `blocker` 或 `important` 质量问题时，下一章的 `prepare-context`、正式提交、导出和平台交付都被阻断，直到修订并重新记录 `pass` 报告；不能先生成写前上下文继续写，再等提交时补审。
- `minor` 与 `note` 可以随 `pass` 报告保留为后续清理项。不能为了放行而把实际重要问题降级。

以上是 `serial_novel` 的原有行为。旧项目缺少 `work_type` 时仍按该模式和每五章默认值执行，不做迁移或重写。

`short_story` 是独立新增分支：项目只有一个完整正文单元，初始化时固定 `interval_chapters: 1`。正文提交后状态返回 `review_mode: completion`、`review_due: true`，质量审核包精读完整故事，报告写入 `reviews/completion/`。它与全篇连续性基线是两份报告，不是每场景一次的周期审核，也不会改变任何 `serial_novel` 项目的检查点。

每个项目可以把间隔改为 1-100 章，也可以明确关闭自动阻断。修改策略属于项目写入，必须先取得当前工作的项目租约并通过 `write-check`：

```powershell
python -X utf8 .\scripts\novel_review.py configure "<project-root>" --interval 5 --enabled true --block-next-commit true --authorization-reference "作者确认每五章审核一次"
```

## 质量审核范围

每次必须覆盖以下九个维度，字段名与脚本一致：

1. `hook_and_reader_promise`：开篇与阶段性承诺是否清楚、可信并得到兑现。
2. `pacing_and_scene_function`：场景是否重复、拖沓、跳步，张弛是否服务本段目标。
3. `tension_and_information_release`：压力、悬念、揭示和留白是否按读者理解能力递进。
4. `character_arc_and_emotional_force`：人物选择、关系变化和情绪后果是否有力度。
5. `prose_voice_and_readability`：叙述距离、角色声音、句式和阅读流畅度是否稳定。
6. `originality_and_cliche_control`：是否过度依赖套路、模板反转、通用台词和熟套意象。
7. `platform_fit_and_retention`：在不牺牲故事的前提下，标题、开篇、章节兑现和阅读驱动力是否适合目标受众。
8. `humanization_and_repetition`：是否出现机械排比、解释过度、均匀段落、空泛总结和无意重复。
9. `language_and_format`：错字、漏字、病句、标点、Markdown 格式和 POV 表达问题。

事实、时间、知识、物品和规则是否矛盾不在本报告重复裁决；引用当前连续性审计状态即可。发现新的疑似矛盾时，将其转入连续性报告，不用质量术语模糊处理。

历史报告只有同时声明 `review_domain: quality`、`continuity_review_separate: true`，完整覆盖上述九个质量维度，并记录真实的独立审稿者时才算有效。旧版把连续性和质量混在一起的报告会被状态命令忽略，并要求在当前检查点重新审核，不能继续充当放行依据。

## 读取协议

审核不能只读最近一章，也不能只读摘要：

1. 精读本检查点新增区间的全部正文。例如第二次审核精读第 6-10 章。
2. 读取截至检查点的 `manuscript/index.md`、全部章节记忆卡、`memory/book-summary.md` 和 `memory/decisions.md`。
3. 读取故事圣经、总纲、目标平台与风格合同，以及当前已通过的连续性结论；不在此报告重做连续性判定。
4. 对节奏、人物弧、信息释放或兑现问题，回读被牵涉的旧章原文；记忆卡只能定位证据。
5. 区分“可验证的质量缺陷”“有意的叙事选择”“信息不足”和“作者偏好”，不能把个人口味包装成错误。

`prepare` 会为以上文件和截至检查点的全部章节生成 SHA-256 快照。报告记录前若任一输入变化，必须丢弃旧审核包并重新准备，不能用过期阅读结果覆盖新正文。

## 执行流程

先查询状态：

```powershell
python -X utf8 .\scripts\novel_review.py status "<project-root>"
```

当 `review_due` 为 `true` 时，把审核包和可编辑报告模板放在当前隔离工作目录，不放入正文目录：

```powershell
python -X utf8 .\scripts\novel_review.py prepare "<project-root>" --output "<work-root>\reviews\periodic\review-packet.json"
```

必须由独立只读审稿 Agent 按审核包的 `reading_scope` 完成阅读，不能由写稿上下文自称独立。填写同目录生成的 `review-packet-report.json`，保留 `review_domain: quality`、`continuity_review_separate: true`，并填写真实 `reviewer_id`、`mode: independent`、`independent_context: true`。每条发现必须有具体位置、正文证据、问题、影响、最小修法、影响范围和是否需要作者判断。

短故事使用相同命令，但建议把工作目录目标设为 `reviews\completion\review-packet.json`；生成的 `packet_kind` 与 `report_kind` 分别为 `short_story_completion_review_packet` 和 `short_story_completion_review`。报告中的 `chapters: [1]` 指唯一完整正文单元，位置字段应进一步注明场景、标题或段落锚点。

严重度与结论严格映射。脚本字段使用英文枚举；中文审核记录中的“阻断/重要/一般/建议”分别对应 `blocker`/`important`/`minor`/`note`：

| 发现严重度 | 含义 | 报告结论 |
|---|---|---|
| `blocker` | 作品承诺、结构或阅读体验已无法成立，继续写会扩大返工 | `block` |
| `important` | 明显影响理解、节奏、人物弧、吸引力或结尾兑现 | `needs_revision` |
| `minor` | 局部语言、格式或低影响瑕疵 | `pass` |
| `note` | 可选优化或需留意的风险 | `pass` |

报告字段示例：

```json
{
  "id": "PR-001",
  "severity": "important",
  "scope": "next_chapters",
  "chapters": [3, 5],
  "location": "第0004至0005章开头",
  "evidence": "两章都用近似段落重复解释主角为何接案",
  "problem": "连续两章重复完成同一场景功能",
  "impact": "主线停滞，检查点前阅读驱动力下降",
  "suggested_fix": "保留第0004章动机，第0005章直接从新阻力开始",
  "author_judgment": false
}
```

当前工作持有租约且报告填写完成后，记录到项目：

```powershell
python -X utf8 .\scripts\novel_review.py record "<project-root>" --packet "<work-root>\reviews\periodic\review-packet.json" --report "<work-root>\reviews\periodic\review-packet-report.json" --authorization-reference "按项目周期审核策略完成第1至5章复核"
```

连载通过报告保存在 `reviews/periodic/periodic-review-NNNN-NNNN-时间戳-摘要.json`；短故事报告保存在 `reviews/completion/completion-review-0001-0001-时间戳-摘要.json`。再次运行 `status`，并另查 `novel_continuity.py status`。只有质量 `review_due: false`、连续性 `status: current`，才表示可以提交下一章或进入定稿导出与发布准备。

## 修订与重新审核

报告不是正文修订授权。发现 `blocker` 或 `important` 后：

1. 向作者列出带证据的问题和建议修法，不直接覆盖正文。
2. 作者确认修订后，按 [revision.md](revision.md) 建立恢复点并同步修改正文、索引、记忆卡、故事圣经和连续性账本。
3. 任何已审核章节正文发生变化后，旧通过报告自动失效；重新执行 `prepare`、阅读和 `record`。
4. 新报告为 `pass` 后才刷新工作基准并继续下一章正式提交。

如果作者暂时不修改，保留报告和暂存的新章草稿，但不能用手工改 `current_chapter`、删除报告或伪造 `pass` 绕过闸门。
