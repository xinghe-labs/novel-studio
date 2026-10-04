# 连载周期质量审核与短故事质量完稿审核

在连续写作达到质量审核检查点、查询审核状态、处理节奏/文风/吸引力问题、准备下一章，或短故事唯一正文已经提交时读取本文件。连续性错误单独使用 [continuity.md](continuity.md) 的九维报告和全局基线；本文件只负责质量，不把两类结论混成一个分数。一般审稿原则见 [revision.md](revision.md)，项目写入与租约要求见 [controlled-automation.md](controlled-automation.md)；短故事同时读取 [short-story-mode.md](short-story-mode.md)。

## 默认策略

新项目默认启用以下策略；旧项目缺少该字段时也按同一默认值运行：

```json
{
  "periodic_review": {
    "enabled": true,
    "interval_chapters": 5,
    "first_review_chapter": 3,
    "block_next_commit": true
  }
}
```

- 新项目脚手架默认写 `first_review_chapter: 3`：第 1-2 章不触发；第 3 章先正常提交，再立即审核第 1-3 章（开篇专项审核，见下节）；通过前第 4 章的提交与导出都被阻断。此后检查点为 8、13、18……即首检章加 `interval_chapters` 的整数倍。
- 每个检查点通过后，下一个检查点在 `interval_chapters` 之后（例如首检第 3 章通过后是第 8 章，再后是第 13 章）。届时重点精读自上次通过后的新增区间，并结合前文检查读者承诺、节奏、人物弧和留存体验；同一检查点的连续性由另一份独立全局报告负责。
- 审核到期不是结构损坏，因此 `novel_project.py validate` 返回警告，不回滚刚提交的检查点章节。
- 默认只保存报告，不自动修改正文。存在 `blocker` 或 `important` 质量问题时，下一章的 `prepare-context`、正式提交、导出和平台交付都被阻断，直到修订并重新记录 `pass` 报告；不能先生成写前上下文继续写，再等提交时补审。
- `minor` 与 `note` 可以随 `pass` 报告保留为后续清理项。不能为了放行而把实际重要问题降级。

以上是 `serial_novel` 的原有行为。旧项目缺少 `work_type` 时仍按该模式执行，不做迁移或重写。旧项目 `novel.json` 缺少 `first_review_chapter` 时首检章等于 `interval_chapters`（即第 5 章），检查点网格与历史完全一致；要提前首检时用 `configure --first-review-chapter` 显式设置，值不得大于 `interval_chapters`。

`short_story` 是独立新增分支：项目只有一个完整正文单元，初始化时固定 `interval_chapters: 1`。正文提交后状态返回 `review_mode: completion`、`review_due: true`，质量审核包精读完整故事，报告写入 `reviews/completion/`。它与全篇连续性基线是两份报告，不是每场景一次的周期审核，也不会改变任何 `serial_novel` 项目的检查点。

每个项目可以把间隔改为 1-100 章，也可以明确关闭自动阻断。修改策略属于项目写入，必须先取得当前工作的项目租约并通过 `write-check`：

```powershell
python -X utf8 .\scripts\novel_review.py configure "<project-root>" --interval 5 --first-review-chapter 3 --enabled true --block-next-commit true --authorization-reference "作者确认每五章审核一次" --workspace "<workspace-root>" --work-id "<work-id>"
```

`block_next_commit: false` 是唯一一处"审核到期但不阻断"的开关，它对**提交和导出同时生效**：两条路径都读取同一判据（`novel_review.review_gate()`），因此不会出现"能提交却永远无法导出"的分歧。关闭后到期审核不阻断，但 `review_status`、`commit-chapter` 的 `warnings` 与 `export` 的 `review_warnings` 都会显式记录该状态；审核报告本身仍然照常要求，`validate` 仍返回到期警告。关闭开关只适合作者明确知情后短期使用，不能用来让未审核的正典进入正式交付。

## 质量审核范围

每次必须覆盖以下九个维度，字段名与脚本一致：

1. `hook_and_reader_promise`：开篇与阶段性承诺是否清楚、可信并得到兑现。
2. `pacing_and_scene_function`：场景是否重复、拖沓、跳步，张弛是否服务本段目标。
3. `tension_and_information_release`：压力、悬念、揭示和留白是否按读者理解能力递进。
4. `character_arc_and_emotional_force`：人物选择、关系变化和情绪后果是否有力度。
5. `prose_voice_and_readability`：叙述距离、角色声音、句式和阅读流畅度是否稳定；存在 `story-bible/voice-anchor.md` 时另按 [anchor-discipline.md](anchor-discipline.md) 审稿清单核对感知锚点执行度（织进原句、复述防回声、落点锚、签名一致性、带噪与单段上限）。
6. `originality_and_cliche_control`：是否过度依赖套路、模板反转、通用台词和熟套意象。
7. `platform_fit_and_retention`：在不牺牲故事的前提下，标题、开篇、章节兑现和阅读驱动力是否适合目标受众。
8. `humanization_and_repetition`：历史兼容机器字段，语义为叙述自然度、声音一致性与机械模式检查；关注机械排比、解释过度、均匀段落、空泛总结和无意重复，不代表规避任何检测。
9. `language_and_format`：错字、漏字、病句、标点、Markdown 格式和 POV 表达问题。

事实、时间、知识、物品和规则是否矛盾不在本报告重复裁决；引用当前连续性审计状态即可。发现新的疑似矛盾时，将其转入连续性报告，不用质量术语模糊处理。

## 开篇专项审核（首检加项）

`first_review_chapter` 触发的首检除九个维度外，逐项核对开篇抓力（判据见 [drafting.md](drafting.md) 的开篇章与 [building-precheck.md](building-precheck.md) D 段）：

1. 第一屏硬标准：第一屏（约前三段、三百字）内出现异常、冲突或处境反差；“谁、在哪、想要什么、什么不对劲”四问至少答出三问；主角带具体可共情的处境或欲望；开场处于“变化发生中”；静态开场四件套（天气铺陈、起床洗漱、照镜子自我介绍、大段回忆或世界观讲解）零命中。
2. 黄金三章合同逐项落地：每章的进入、兑现、卡点都成文而不是被旁白宣布；三章钩子不同型，开场方式不同源。
3. 兑现节奏：每章至少一个真实兑现（爽点、关键信息或关系推进），铺垫不连续两章无回报；期待线表主线的首次铺设已发生。
4. 流失点预演的对策确实写进了正文，不是只留在确认单里。
5. 书名、简介与首章承诺一致：简介承诺的体验在前三章已被兑现第一口。

命中项按对应九维维度与严重度记录，不另立开篇分数；`blocker` 或 `important` 未修复前，第 4 章提交与导出保持阻断。作者另有历史数据时，第 1、3 章的入口信号阈值按确认单预注册仪表核对（见 [publication-feedback.md](publication-feedback.md)）。

历史报告只有同时声明 `review_domain: quality`、`continuity_review_separate: true`，完整覆盖上述九个质量维度，并记录真实的独立审稿者时才算有效。旧版把连续性和质量混在一起的报告会被状态命令忽略，并要求在当前检查点重新审核，不能继续充当放行依据。

## 读取协议

审核不能只读最近一章，也不能只读摘要：

1. 精读本检查点新增区间的全部正文。例如第二次审核精读第 6-10 章。
2. 读取截至检查点的 `manuscript/index.md`、全部章节记忆卡、`memory/book-summary.md` 和 `memory/decisions.md`。
3. 读取故事圣经、总纲、目标平台与风格合同（存在 `story-bible/voice-anchor.md` 时一并读取签名清单与声音库），以及当前已通过的连续性结论；对照 [drafting.md](drafting.md) 的写作机理评估对应维度——期待感四来源是否推进、爽点是否有铺垫与代价、对抗阶梯是否同步升级、信息差揭示是否有意图；不在此报告重做连续性判定。
4. 对节奏、人物弧、信息释放或兑现问题，回读被牵涉的旧章原文；记忆卡只能定位证据。
5. 区分“可验证的质量缺陷”“有意的叙事选择”“信息不足”和“作者偏好”，不能把个人口味包装成错误。

`prepare` 会为以上文件和截至检查点的全部章节生成 SHA-256 快照。报告记录前若任一输入变化，必须丢弃旧审核包并重新准备，不能用过期阅读结果覆盖新正文。

## 跨窗口重复扫描

窗口内精读发现不了与早先章节的重复：第 3 章与第 80 章共用同一个比喻、同一句转场话或同一种断章方式，不会同时出现在本检查点区间内。每个检查点因此额外执行一次跨窗口扫描：

1. 从新增区间提取高频短语、意象、比喻句式、场景进入方式和断章方式。
2. 用 SQLite 检索或 `rg` 在全部已提交正文中查证碰撞，并结合记忆卡的结尾钩子、开头进入与实际字数字段核对轮换与漂移。
3. 命中项按证据位置记入 `humanization_and_repetition` 或 `originality_and_cliche_control`，不在本报告内自动改写正文。

扫描不改变审核包的快照绑定；发现连续性疑点时仍转入连续性报告。旧项目记忆卡缺少上述字段时按现有字段核对，不回填。

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
python -X utf8 .\scripts\novel_review.py record "<project-root>" --packet "<work-root>\reviews\periodic\review-packet.json" --report "<work-root>\reviews\periodic\review-packet-report.json" --authorization-reference "按项目周期审核策略完成第1至5章复核" --workspace "<workspace-root>" --work-id "<work-id>"
```

连载通过报告保存在 `reviews/periodic/periodic-review-NNNN-NNNN-时间戳-摘要.json`；短故事报告保存在 `reviews/completion/completion-review-0001-0001-时间戳-摘要.json`。再次运行 `status`，并另查 `novel_continuity.py status`。只有质量 `review_due: false`、连续性 `status: current`，才表示可以提交下一章或进入定稿导出与发布准备。

## 修订与重新审核

报告不是正文修订授权。发现 `blocker` 或 `important` 后：

1. 向作者列出带证据的问题和建议修法，不直接覆盖正文。
2. 作者确认修订后，按 [revision.md](revision.md) 建立恢复点并同步修改正文、索引、记忆卡、故事圣经和连续性账本。
3. 任何已审核章节正文发生变化后，旧通过报告自动失效；重新执行 `prepare`、阅读和 `record`。
4. 新报告为 `pass` 后才刷新工作基准并继续下一章正式提交。

如果作者暂时不修改，保留报告和暂存的新章草稿，但不能用手工改 `current_chapter`、删除报告或伪造 `pass` 绕过闸门。
