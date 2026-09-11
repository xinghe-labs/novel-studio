# 中文虚构项目合同

在新建、接管、恢复或写入一个连载小说或正式短故事项目时读取本文件。短故事同时读取 [short-story-mode.md](short-story-mode.md)。这里的目录是新项目默认值，不是强制迁移旧项目的理由。

项目必须先通过 [workspace-isolation.md](workspace-isolation.md) 归属到工作区中的稳定 `project_id`，当前执行必须有独立 `work_id`。工作目录保存未确认内容，下面的项目目录保存正典、受控研究资料和长期记忆；二者不能混用。

```text
<workspace-root>/
|-- projects/<project-id>/    本文件所称 <project-root>
`-- workspaces/<work-id>/     当前任务的草稿、研究中间态和报告
```

## 默认目录

```text
<project-root>/
|-- novel.json
|-- planning/
|   `-- framework-session.md
|-- research/
|   |-- platform.json
|   |-- source-manifest.jsonl
|   |-- source-index.md
|   |-- market-scan.md
|   |-- comparable-works.md
|   |-- originality-map.md
|   `-- originality-plan.json
|-- sources/
|-- story-bible/
|   |-- premise.md
|   |-- cast.md
|   |-- world.md
|   `-- style-guide.md
|-- outlines/
|   `-- master-outline.md
|-- manuscript/
|   |-- index.md
|   `-- chapters/
|       `-- 0001-chapter-title.md
|-- memory/
|   |-- book-summary.md
|   |-- decisions.md
|   `-- chapters/
|       `-- 0001.md
|-- continuity/
|   |-- policy.json
|   |-- head.json
|   |-- canon-facts.jsonl
|   |-- intentional-exceptions.jsonl
|   |-- dependencies.json
|   |-- invalidations.json
|   |-- state.json
|   |-- timeline.md
|   |-- threads.md
|   `-- baselines/
|-- reviews/
|   |-- continuity/               逐章与全局连续性审计
|   |-- periodic/                 独立周期质量审核
|   `-- completion/               短故事独立质量完稿审核
|-- staging/
|   `-- chapters/
|-- revisions/
|   `-- snapshots/
|-- exports/                       按需生成、可重建，不是正典
|   |-- export-manifest.json
|   |-- 《书名》-全书合并稿.txt
|   |-- 《书名》-审阅稿.docx
|   |-- 《书名》.epub
|   `-- fanqie/
`-- .novel-cache/
    `-- novel-memory.sqlite3   可重建派生缓存，不是正典
```

`exports/` 只在用户要求导出时创建。它与 `.novel-cache/` 一样可以从文件真源重建，但用途不同：缓存服务于检索，导出文件服务于审阅、阅读或平台交付。二者都不能反向覆盖正文、索引或连续性账本。

作品类型决定同一目录合同的语义：缺少 `novel.json.work_type` 或值为 `serial_novel` 时沿用原有多个分章文件；只有显式值为 `short_story` 时，才启用 `manuscript/chapters/0001-故事名.md` 唯一完整正文单元。保留 `chapters/` 路径是为了复用事务提交、索引、原创审计和导出器，不表示短故事成品必须显示章号。

新项目根目录应是工作区 `projects/` 的直接子目录，不能直接使用工作区根、磁盘根、用户主目录、工作目录或一个已有的非小说项目目录。初始化前显示解析后的完整路径。脚本拒绝覆盖非空且尚未初始化的目录。

## 初始化脚本

从包含本文件的 Skill 目录定位脚本：

```powershell
python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>"
python -X utf8 .\scripts\novel_workspace.py project-create "<workspace-root>" --title "<书名>" --genre "<题材>"
python -X utf8 .\scripts\novel_workspace.py project-create "<workspace-root>" --title "<故事名>" --genre "<题材>" --work-type short_story --target-words 12000 --short-story-slug "<semantic-slug>"
python -X utf8 .\scripts\novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<project-id>"
python -X utf8 .\scripts\novel_project.py init "<project-root>" --title "<书名>" --genre "<题材>"
python -X utf8 .\scripts\novel_project.py upgrade "<project-root>"
python -X utf8 .\scripts\novel_project.py validate "<project-root>"
python -X utf8 .\scripts\novel_project.py status "<project-root>"
python -X utf8 .\scripts\novel_research.py adapters
python -X utf8 .\scripts\novel_research.py verify "<project-root>"
python -X utf8 .\scripts\novel_memory.py rebuild "<project-root>"
python -X utf8 .\scripts\novel_memory.py status "<project-root>"
python -X utf8 .\scripts\novel_originality.py audit "<project-root>"
python -X utf8 .\scripts\novel_review.py status "<project-root>"
python -X utf8 .\scripts\novel_export.py export "<project-root>"
python -X utf8 .\scripts\novel_export.py status "<project-root>"
```

`init` 只创建新文件，不覆盖既有项目。`upgrade` 只为已初始化项目补充缺失的互动策划、平台配置、来源登记、原创性计划、暂存区、章节索引和长期记忆目录，不移动、重写或改版已有正文和研究资料；旧章仍需按原文回填索引和记忆卡。升级是幂等的。旧项目需要接管时，先盘点其现有真源和命名，不要用脚本强行初始化。

新建短故事时显式传 `--work-type short_story` 和 `--short-story-slug`。slug 由 Agent 根据最终确认标题选择 1-3 个简短的英文小写语义词，只含 ASCII 字母、数字和连字符；脚本生成 `shortstory-<slug>-YYYYMMDD`，同日冲突自动追加 `-2`、`-3`。项目 ID 创建后保持稳定，不随标题变化。未传 `--work-type` 时完全沿用原有长篇创建路径，清单不必新增类型字段；旧项目缺少 `work_type` 也只按长篇兼容模式读取，不能迁移、写回或根据篇幅自动改型。`target_words` 是创作目标，不是平台规则，平台限制发布前另行核验。

常规新建优先使用 `novel_workspace.py project-create`，它在 `projects/` 下建立项目并登记稳定 ID；直接调用 `novel_project.py init` 只适合脚本测试、接管前准备或已有工作区管理器明确提供了目标目录的情况。项目创建和工作目录创建是不同动作：每项工作都需要工作目录。长篇仍在用户明确要求新书时建立项目；新短故事只有在默认研究、参考候选审批、原创方向选择、两个 95% 闸门和完整故事确认全部完成后才建立项目，再绑定原工作目录。

## 互动框架会话

`planning/framework-session.md` 保存互动策划的恢复点，包含用户原始材料、已确认、暂定、未决定、已排除、下一轮和框架确认单。它是策划过程记录，不高于故事圣经、总纲或作者决策；其中暂定内容不得当作正典用于正文。

文件 frontmatter 的 `stage` 表示当前覆盖阶段，`confirmation` 表示框架是否已经由用户确认。允许状态和写回协议见 [interactive-planning.md](interactive-planning.md)。每轮产生实质决定后更新会话文件；用户确认框架时先同步正典，再把状态设为 `stage: complete` 与 `confirmation: confirmed`。二者不一致时 `validate` 应阻断。

`requirements_confidence` 和 `story_confidence` 分别记录需求层与故事层的执行准备度。它们是 0-100 的整数；框架确认完成时都必须至少为 95。百分比必须能由会话文件中的已确认、明确委托和未决阻断项解释。

## 市场研究与来源

`sources/` 保存原始搜索结果、网页记录或查询日志；`research/` 保存平台配置、机器可读来源登记、可读来源索引、市场扫描、参考作品审批、原创性地图与审计计划。外部研究前先检查已有来源，所有当前性结论记录绝对日期。来源权限和本地资料授权见 [source-ingestion.md](source-ingestion.md)，平台接口见 [platform-adapters.md](platform-adapters.md)。

`research/comparable-works.md` 的 frontmatter 分开记录候选审批和深度分析状态。候选未获用户明确批准时，深度分析只能是 `not_started`；验证器应阻断越过审批闸门的状态。完整研究与原创边界见 [market-research.md](market-research.md) 和 [originality-audit.md](originality-audit.md)。审批状态用 [controlled-automation.md](controlled-automation.md) 的受控命令写回。

## `novel.json`

它记录工作流状态，不承载完整设定。关键字段：

```json
{
  "schema_version": 1,
  "title": "书名",
  "language": "zh-CN",
  "genre": "题材或复合题材",
  "status": "planning",
  "current_volume": 1,
  "current_chapter": 0,
  "pov": "第三人称限知",
  "tense": "过去时",
  "target_words": null,
  "periodic_review": {
    "enabled": true,
    "interval_chapters": 5,
    "block_next_commit": true
  },
  "updated_at": "ISO-8601 时间"
}
```

上例是原有长篇清单结构。短故事在此基础上显式增加 `"work_type": "short_story"`，并把默认审核间隔设为 1；不得为了统一格式给旧长篇批量补字段。

正文、锁定故事圣经和连续性事实账本共同组成内容真源。章节索引、长期记忆卡与 `.novel-cache/novel-memory.sqlite3` 是检索读模型，不得反过来覆盖原文。`continuity/head.json` 用哈希封存当前正典，不能作为人工修改正典的替代入口。SQLite 损坏、缺失或过期时从文件重建。不要把整个小说塞入 JSON，也不要把推断写成确认事实。

短故事项目把 `work_type` 设为 `short_story`，`current_chapter` 仍只用于表示唯一正文单元是否已提交：`0` 为尚未提交，`1` 为已提交；不得出现 `2`。其默认审核间隔固定为 1，用来触发全篇完稿审核，不表示每个场景都要单独审核。

`periodic_review` 只控制质量审核节奏和提交闸门，不承载审稿内容。新项目默认每 5 章审核一次；旧项目没有该字段时也采用同一默认值。连续性每 5 章的独立全局审核由 `continuity/policy.json` 单独控制。两份报告彼此独立，具体协议见 [continuity.md](continuity.md) 和 [periodic-review.md](periodic-review.md)。

## 正典优先级

1. 用户本轮明确修改或裁决；跨上下文使用前写入 `memory/decisions.md`。
2. 已锁定世界规则、人物底层设定和作品承诺以 `story-bible/` 为准。
3. 已经发生的事件以已提交 `manuscript/chapters/` 为准；索引、摘要或账本不一致时先修读模型。
4. 人物知道、相信、声称的内容和读者知道的内容分别以 `canon-facts.jsonl` 的对应类别与正文证据为准。
5. `outlines/` 的未来节点按 `locked`、`planned`、`optional`、`abandoned` 四态判断，不能把计划写成已发生事实。
6. `memory/book-summary.md`、章节记忆卡、`planning/framework-session.md` 中的暂定内容、旧审稿报告、草稿、灵感池和模型推断只用于定位与建议。

市场研究文件是决策证据，不是故事正典。公开热度、读者评论或竞品机制不能覆盖作者确认的主题、人物和世界规则。

低优先级内容与高优先级内容冲突时，只报告差异或提出改稿方案。只有用户确认后才提升为正典。

## 章节命名与合同

连载正文默认使用 `manuscript/chapters/NNNN-title.md`，每章一个文件，四位章号便于排序；禁止把后续章节持续追加到同一个正文文件。短故事只使用 `manuscript/chapters/0001-故事名.md`，正文内部可用二级标题或场景分隔线组织，但不得提交 `0002`。两种模式的中间态都放在 `staging/chapters/<package>/`，每个写作单位必须实际调用 `humanizer-zh` 并通过自然化、原创性与连续性硬门禁后事务提交。每个写作单位在大纲中应有最小合同：

- 章节目的与读者应获得的变化
- POV、时间、地点和进入状态
- 场景节拍及因果关系
- 本章关键选择、代价、揭示或转折
- 结束状态与下一步压力
- 必须延续、推进或暂不触碰的线索
- 禁止违反的世界规则、人物知识与语气约束

标题可以晚定，合同不能用空泛的“推进剧情”“加深感情”代替可观察变化。

## 派生导出

Markdown 正文与 `manuscript/index.md` 是出版导出的唯一输入。连载继续按原格式生成合并 TXT、审阅 DOCX、EPUB 和 `exports/fanqie/` 逐章 TXT，原有 `export-manifest.json` 字段结构不变；短故事生成短故事定稿 TXT、审阅 DOCX、EPUB 和 `exports/fanqie-short-story/《故事名》.txt`，并在短故事导出清单中额外登记作品类型与发布配置。两者都登记源快照和输出哈希。

导出目录不参与正典状态哈希，不能用来恢复或修改正文。重新导出发现人工修改的派生文件时默认停止，只有用户确认不保留这些派生修改后才能强制覆盖。格式、覆盖与验证协议见 [publishing-exports.md](publishing-exports.md)。

## 章节索引

`manuscript/index.md` 是全书导航和第一层检索入口，每个已提交章节恰好一行：

```markdown
| 章号 | 标题 | POV | 故事时间 | 地点 | 事实摘要 | 关键变化 | 线索 ID | 正文 |
|---:|---|---|---|---|---|---|---|---|
| 0001 | 雾港来信 | 林某D | 第一日凌晨 | 旧邮局 | 林某D收到失踪姐姐的信 | 获得储物柜钥匙 | T-001,T-002 | [正文](chapters/<chapter-file>) |
```

摘要只写正文已经发生的事实，不写评价、推测或未来计划。改名、拆章、合章或删除章节时，同时修订索引链接，并在 `memory/decisions.md` 留下追溯记录。

连载正文首行的章号和标题、短故事正文首行的作品标题，都必须与 `manuscript/index.md` 对应行一致。项目验证和正式导出会机械检查这一点；修改标题时必须同时更新正文首行、索引、文件名、记忆卡链接及需要保留的发布映射，不能只改其中一处。

短故事索引只允许一行 `0001`，该行链接整篇唯一主稿；场景级顺序和功能写在 `outlines/master-outline.md`。短故事修订不增加索引行，必须按重大改稿流程更新唯一正文、记忆、连续性与审核快照。

## 长期记忆层

- `memory/book-summary.md`：压缩记忆；连载记录全书承诺与跨卷状态，短故事记录全篇承诺、场景推进、已建立证据和结尾所需回收。
- `memory/decisions.md`：作者明确裁决、重写、废弃设定和追溯生效范围。
- `memory/chapters/NNNN.md`：每章记忆卡，记录事实摘要、状态变化、知识变化、关键物品、线索 ID、回调锚点和续写约束。
- `.novel-cache/novel-memory.sqlite3`：从以上文件派生的本地查询缓存，可按人物、地点、物品、关系、线索、时间和章节检索；不是正典，也不是必须交付的正文文件。

完整的读取、检索和写回协议见 [long-term-memory.md](long-term-memory.md)。长期记忆不能只存在于聊天中。

## 上下文加载

不要为普通章节把整部书全文都塞进上下文，但必须使用 `novel_continuity.py prepare-context` 生成的哈希绑定清单并真实完成自动读取。优先读取：

1. `memory/book-summary.md`、`memory/decisions.md` 与 `manuscript/index.md`。
2. 本章合同与相关总纲片段。
3. 故事圣经中本章涉及的人物、地点和规则。
4. `continuity/state.json`、稳定事实库、有意例外、依赖图、相关时间线与活跃线索。
5. 最近 2-3 章记忆卡，以及按人物、地点、物品、关系或线索 ID 命中的旧章记忆卡。
6. 上一章结尾和所有命中旧章的相关原文片段。
7. 风格指南中的 POV、叙述距离、禁用习惯和角色声音。

被触及事实的来源旧章必须回读；跨 10 章回调、秘密、核心规则、关键物品、数值账和疑似冲突必须交给独立审稿上下文。账本与原文冲突时回到原文核验，并把账本修复列为独立任务。

## 提交顺序

正式提交使用 [controlled-automation.md](controlled-automation.md) 的章节暂存包与 `commit-chapter` 命令；以下是事务内部的正典顺序，不建议手工逐个更新：

进入事务前，当前工作必须持有项目写入租约，并且 `write-check` 证明项目哈希仍等于该工作的 `base_state_hash`。验证失败表示另一项工作已经改变项目，必须重新读取和裁决差异，不能直接覆盖。

1. 写前上下文必须绑定当前 `head`，并完成自动来源、被触及事实和相关旧章原文的证据读取。
2. 在工作目录完成初稿及结构、人物、连续性与原创性初检，随后每章实际调用 `humanizer-zh`；调用前原稿、最终结果与审阅记录分别进入暂存包。短故事写完整唯一正文；修订已有正典先建立快照，不先推进 `novel.json`。
3. 校验自然化记录的 Skill 声明、章节号、授权、两个独立 Markdown 文件及其 SHA-256。非语义调整可依据项目级逐章授权采用；会改变事实、剧情、人物动机、关系、世界规则或 POV 时先取得作者确认，结果变化后重做最终检查。
4. 生成并填写精确 `state-delta.json`，随后运行 `bind-audit` 把九维审计绑定到最终增量哈希。
5. 风险章由独立上下文审稿；确定矛盾必须阻断，疑似伏笔或已确认例外才可作为警告保留。
6. 运行连续性包检查和双层原创性审计，二者都为 `pass` 且覆盖最终结果哈希后才调用 `commit-chapter`。
7. 事务写入正文、记忆卡、索引、状态、事实库、例外库、依赖图、审计报告、可选时间线/线索/全书记忆、`novel.json` 和新 `head`；任一步失败恢复原字节。
8. 运行 `validate`；若 SQLite 已存在则增量更新。任何自然化记录、哈希绑定、索引、记忆或审核缺失时提交失败，不得用重建项目掩盖错误。
9. 连载每 5 章分别完成全局连续性基线和独立周期质量审核；短故事完成全篇连续性基线和独立质量完稿审核。任一到期或未通过时停止下一章、导出和上传。
10. 项目验证、两类审核状态与长期记忆状态处理完成后刷新当前工作的基准哈希，再释放写入租约。

重大改稿先把受影响原文复制到 `revisions/snapshots/<timestamp>/` 或使用仓库已有版本控制。不要自动提交、推送或删除旧稿。
