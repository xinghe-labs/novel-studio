---
name: chinese-novel-studio
description: 通过持续互动问答、可追溯的公开数据研究、隔离工作目录、本地长期记忆和哈希绑定连续性硬门禁，规划、创作、续写、审核、系统改稿并导出中文连载小说或通常 6000-80000 字的完整短故事；支持多 Agent 或脚本并行暂存、风险触发独立审稿、每五章全局连续性与质量双审核、番茄小说与短故事交付、番茄线上上传和已上传章节的受控格式修订、来源登记、双层原创性审计、Markdown 正文与索引，以及 TXT、DOCX、EPUB 和平台包。用户要从灵感确定框架、根据近期热门数据选题、建立项目、写或续写长篇章节、完成单篇短故事、跨上下文保持一致、周期或完稿查错、分层修订、上传番茄或生成阅读与发布交付物时使用；纯非虚构写作不使用。
metadata:
  short-description: 以互动框架、长期记忆和审核创作长篇小说或短故事
---

# 中文小说与短故事工作室

把中文虚构创作当作有状态的工程：创意判断由作者掌舵，正文、设定、时间线、正文索引、长期记忆和改稿影响必须持久化。长期记忆依靠项目文件，不依赖当前聊天还能记住什么。不要把固定套路、评分表、“每章必有悬念”或“短篇必须反转”当成质量本身。

## 先建立隔离上下文

每次使用本 Skill 都必须先读取 [workspace-isolation.md](references/workspace-isolation.md)，在任何提问、搜索、策划、审稿或写作前执行 `scripts/novel_workspace.py work-ensure`。本机默认工作区是 `<workspace-root>`，除非用户明确指定其他位置。

当前目录或调用方提供的 `work_id` 能定位有效 `work.json` 时复用；否则先创建 `workspaces/work-*` 隔离目录。新工作可以暂时不绑定小说。只有用户明确要求新建一本小说时才创建 `projects/<project-id>`；继续旧书时绑定并复用已有项目，不能把一次新工作误当作一本新书。

“工作”可以来自任意 Agent、脚本、窗口、进程或人工操作，不依赖 Codex 会话 ID。调用方应保存返回的 `work_id` 或继续在原工作目录中操作。多个上下文可以并行研究和暂存，但同一本小说的正式项目写入必须取得单写者租约并通过基准状态哈希检查。

## 先判定任务模式

短故事是显式启用的纯增量分支。本 Skill 中，短故事通常指 6000-80000 字、可独立阅读并单篇完结的小说体裁；它强调相对紧凑的节奏、持续升级且有起伏的剧情、较强的感情张力和沉浸感，题材可以涵盖现实生活、现代言情、古代言情、宫斗宅斗、脑洞、悬疑、衍生、历史玄幻等。该字数区间是常见创作范围，不是脚本硬限制或未经核验的平台规则；作者另有目标或平台最新限制不同时，以已确认约束为准。

用户只要明确表达“写短故事”、创作一篇可独立阅读的完整短篇或继续一个 `work_type: short_story` 项目，就进入该分支；不要求记住命令或参数。新短故事触发后只创建或复用未绑定工作目录，不立即创建正式项目。其他情况，包括缺少 `work_type` 的旧项目和未传 `--work-type` 的新项目，都保持原有长篇创建、分章写作、每五章审核、目录结构与导出清单，不补写类型字段、不迁移项目，也不把短故事模板混入长篇。

根据用户当前目标只加载需要的参考文件：

| 模式 | 典型请求 | 必读参考 |
|---|---|---|
| 互动建书 | 通过问答从灵感确定框架、边聊边设计、确认后开写 | [interactive-planning.md](references/interactive-planning.md)、[planning.md](references/planning.md)；需要落盘或开始写作时再读 [project-contract.md](references/project-contract.md) |
| 市场选题与参考研究 | 根据近期公开数据提出方向、筛选参考作品、检查原创性 | [market-research.md](references/market-research.md)、[platform-adapters.md](references/platform-adapters.md)、[source-ingestion.md](references/source-ingestion.md)、[originality-audit.md](references/originality-audit.md)、[interactive-planning.md](references/interactive-planning.md)、[planning.md](references/planning.md)；落盘时再读 [project-contract.md](references/project-contract.md) |
| 平台采集与来源导入 | 刷新番茄公开数据、重读已存来源、登记用户合法提供的本地资料 | [platform-adapters.md](references/platform-adapters.md)、[source-ingestion.md](references/source-ingestion.md) |
| 新建项目 | 从灵感开始、建立新书、整理已有构思 | [planning.md](references/planning.md)、[project-contract.md](references/project-contract.md) |
| 短故事创作 | 互动设计并完成一篇短故事、短篇完稿审核、番茄短故事交付 | [short-story-mode.md](references/short-story-mode.md)、[market-research.md](references/market-research.md)、[interactive-planning.md](references/interactive-planning.md)、[planning.md](references/planning.md)、[drafting.md](references/drafting.md)；两级闸门均确认后再读 [project-contract.md](references/project-contract.md) 建项目，提交时读 [controlled-automation.md](references/controlled-automation.md) 与 [originality-audit.md](references/originality-audit.md) |
| 故事设计 | 人物、世界观、总纲、卷纲、章纲 | [planning.md](references/planning.md)；需要落盘时再读 [project-contract.md](references/project-contract.md) |
| 写作与续写 | 写一章、续写、补场景、重写片段 | [drafting.md](references/drafting.md)、[long-term-memory.md](references/long-term-memory.md)、[continuity.md](references/continuity.md)；正式提交时再读 [controlled-automation.md](references/controlled-automation.md) 与 [originality-audit.md](references/originality-audit.md) |
| 原创性审计 | 查原句、近似措辞或单一来源结构映射风险 | [originality-audit.md](references/originality-audit.md)、[source-ingestion.md](references/source-ingestion.md) |
| 审稿与查错 | 评价章节、查矛盾、找节奏或人物问题 | [revision.md](references/revision.md)、[continuity.md](references/continuity.md)；节奏、文风、吸引力和自然度另读 [periodic-review.md](references/periodic-review.md) |
| 周期审核 | 每若干章自动检查现有章节、处理审核到期或下一章被阻断 | [periodic-review.md](references/periodic-review.md)、[revision.md](references/revision.md)、[continuity.md](references/continuity.md)、[controlled-automation.md](references/controlled-automation.md) |
| 系统改稿 | 改人物弧、重排情节、全文润色 | [revision.md](references/revision.md)、[project-contract.md](references/project-contract.md)、[controlled-automation.md](references/controlled-automation.md) |
| 出版与阅读导出 | 合并 TXT、审阅 DOCX、EPUB、番茄逐章 TXT、检查导出是否过期或是否含异常字符 | [publishing-exports.md](references/publishing-exports.md)、[platform-delivery-quality.md](references/platform-delivery-quality.md)、[project-contract.md](references/project-contract.md) |
| 番茄上传与线上修订 | 上传章节、设置定时发布、修复已上传正文中的分场符或格式问题 | [fanqie-live-publishing.md](references/fanqie-live-publishing.md)、[publishing-exports.md](references/publishing-exports.md)、[platform-delivery-quality.md](references/platform-delivery-quality.md)、[controlled-automation.md](references/controlled-automation.md) |

一次性片段润色或聊天内构思也先创建或复用工作目录，但在用户决定建立正式作品前保持未绑定，不强迫创建项目。跨章节续写、长篇连载、跨会话短故事、完稿审核、平台交付或任何需要写入正典的任务使用项目合同；短故事先读 [short-story-mode.md](references/short-story-mode.md)。

## 核心契约

1. **正典按事实类型裁决。** 用户本轮明确决定最高；锁定世界规则和人物底层设定以故事圣经为准，已经发生的事件以已提交正文为准，人物知道/相信/声称和读者知道分别记账。大纲只允许 `locked/planned/optional/abandoned` 四态。发现冲突时指出并请求裁决，不静默改写正典。
2. **工作与项目必须隔离。** 每项独立工作先有 `work_id` 和工作目录；每本小说使用稳定 `project_id` 和唯一项目目录。未确认草稿与中间研究写入工作目录，不能直接混入正典。不同工作可以并行读取和暂存，同一项目的正式写入必须持有租约、通过状态哈希检查并在完成验证后刷新基准。
3. **长期记忆必须落盘。** 不把聊天上下文、模型记忆或“刚才读过”当作长期记忆。Markdown、JSON、分章正文和索引是唯一真源；本地 SQLite 只能从这些文件重建，不能反向覆盖正典。已提交章节必须有独立正文、索引条目、章节记忆卡和同步后的连续性状态。
4. **互动决定必须分层。** 互动策划时把内容分为已确认、暂定、未决定和已排除；模型建议不能自动成为正典。项目存在时，每轮实质决定都写回 `planning/framework-session.md`，跨上下文从“下一轮”恢复。
5. **两级理解闸门。** 新书、重大方向变化和市场选型先执行需求层 95% 闸门，具体故事再执行故事层 95% 闸门。每轮展示已理解、仍不确定和当前信心；有阻断项时信心不得超过 94%。用户委托分析不等于确认正典。新短故事还必须先完成默认市场研究、参考候选审批和原创方向选择，并确认标题、人物、冲突、场景图、高潮、结尾、尺度与交付目标；此前只写工作目录，不创建项目。
6. **研究必须可追溯并受授权约束。** 近期热门、平台规则和公开指标都记录来源、绝对日期、SHA-256、权利状态与使用范围。Skill 自采并保存在项目的资料可直接重读；用户本地文本逐项目明确授权，私有全文只在本机处理。先交候选让用户审批，再做深度拆解；不使用盗版全文、不绕过访问控制，也不把定义不同的指标拼成伪总分。
7. **续写必须通过写前连续性上下文。** `continuity-context.json` 绑定当前正典哈希，强制读取全书摘要、作者决策、索引、故事圣经、总纲、状态、时间线、线索、稳定事实、例外、依赖图和最近正文；被触及事实必须回读来源原文。不能只读上一章、聊天记忆或数据库摘要就宣称与全书一致。
8. **最少但充分地澄清。** 复用用户已经给出的信息。只追问会实质改变结果的缺口；其余可以采用可逆假设，并在交付中列出。
9. **先结构，后文字。** 重大剧情、人物动机或世界规则未成立时，不靠华丽句子遮盖。审稿和改稿按概念、结构、场景、文风、校对的顺序由外向内处理。
10. **写后受控原子提交。** 正文先进入工作目录，获准写回后才建立项目暂存包；用 `state-delta.json` 精确重放状态变化，状态增量完成后运行 `bind-audit`，再完成九维连续性审计和双层原创性审计。正文、上下文或增量变化会让旧报告立即过期。事务同时写正文、记忆、事实、例外、依赖、审计和新正典 `head`；任一步失败恢复原字节且不推进状态。
11. **连续性与质量分开审核。** 确定连续性矛盾阻断，疑似伏笔/误导可警告；普通小错只在暂存区自动修，核心设定、人物命运、重要关系、世界规则和重大伏笔必须问作者。复杂时间线、跨十章回调、秘密、核心规则、重要物品等风险章强制独立只读审稿。长篇每 5 章分别做全局连续性与质量审核；短故事提交全文后分别做全篇连续性与质量完稿审核。任一到期或未通过时禁止下一章、导出和上传。
12. **评审默认只报告。** 用户只要求审稿、分析或找问题时，不覆盖原文；只有明确要求修改时才落盘改稿。重大改稿前保留可恢复快照。
13. **不机械套公式。** “展示而非讲述”、冲突、钩子、短句等都是工具。叙述压缩、安静章节、开放结尾和有意重复都可以成立，判断标准是它们是否服务本书的叙事目的。
14. **不复制参考作品。** 可以从多部作品分别提炼高层机制并围绕新的主题、代价和因果重新组合，但不复制独特措辞、角色、设定组合、标志场面、反转答案或连续情节链。原创审计同时检查原句/近似措辞及一句话故事、人物关系、世界规则、前三节点和核心反转；高风险必须重构，不能只改名字。具体作者风格转为中性可描述特征。
15. **Markdown 是唯一正文主稿。** 连载使用分章 Markdown 与章节索引；短故事使用一个完整 Markdown 正文单元与唯一索引行。TXT、DOCX、EPUB、番茄分章包和番茄短故事单文件只能从已提交主稿派生。导出前正典 `head`、全局连续性基线、质量审核和失效清单必须全部有效；`--force` 不能绕过。`exports/` 必须带源快照、文件哈希和平台交付质量清单，危险 Unicode 或任何复检失败都不能替换正式包。
16. **平台副本不是正典。** 番茄网页中的正文是发布副本。只在已证明正文语义不变时直接做平台格式修复，例如删除独占分场标记并保留段落空行；任何增删改写、标题变化或情节修订都必须先回到 Markdown 正典完成受控改稿，再重新导出和发布。平台操作不会反向覆盖本地主稿。

## 标准工作流

### 1. 发现上下文

- 先确认 `work_id`、`work_root`、是否已绑定 `project_id`；没有有效工作上下文时先创建。工作目录存在不等于小说项目已建立。
- 先查找现有 `novel.json` 并读取 `work_type`；旧项目缺少该字段时按 `serial_novel`。再读取策划、平台配置、来源登记、原创性计划、故事圣经、大纲、`manuscript/index.md`、正文、长期记忆、连续性、暂存包和 SQLite 缓存状态。运行 `novel_continuity.py status`；旧书显示 `baseline_required` 时先完成全量独立基线，不能直接续写。
- 有项目时从断点继续，不重做用户已经确认的工作。
- 用户明确新建长篇时，按 [project-contract.md](references/project-contract.md) 在工作区 `projects/` 下创建专用项目并绑定当前工作。用户表达新写短故事时保持工作未绑定，直到默认研究、两个 95% 闸门和完整确认单都通过；届时由 Agent 从最终标题生成 1-3 个英文小写语义词作为 slug，创建 `shortstory-<slug>-YYYYMMDD[-N]` 项目并绑定原工作。项目 ID 创建后不随标题变化。
- 用户提供的是旧项目时保持原目录约定；不要为了套模板而批量移动或重命名文件。

### 2. 建立写作合同

在开始正文前，先用 `novel_continuity.py prepare-context` 生成写前上下文并真实回读自动来源。至少明确本次交付单位、视角人物、时间地点、场景目的、进入状态、关键变化、结束状态、被触及事实、允许/禁止变化、风险触发器和不能违反的正典。新书还要建立故事承诺、核心冲突、人物驱动力、世界限制与叙事声音。

用户要求互动确定框架时，执行 [interactive-planning.md](references/interactive-planning.md)：每轮只处理当前关键分歧，有项目就同步会话文件，在框架确认单得到明确确认后再把内容提升为正典。用户同时要求“确认后开始写”时，确认提交完成后直接进入第一章合同和写作，不重复询问已经确认的总框架。

用户要求依据热门数据选题时，先执行 [market-research.md](references/market-research.md)。新短故事无论用户是否另外说“热门”“搜索”或“由你决定”，需求层达到 95% 后都默认执行该研究；继续既有短故事时复用并核验项目中仍适用的研究，不机械重做。通过平台适配器或合法公开页面研究最近 3-6 个月信号，把原始响应与规范化记录登记到来源清单，先提交 6-9 个参考候选和选择理由；作者批准前不做深度拆解。研究后提出 3 个原创方向并明确推荐一个，仍由用户确认。

### 3. 执行当前单位

- 策划任务产出可执行的大纲或章节合同，而不是只给抽象建议。
- 写作任务按场景推进，每个场景至少改变信息、关系、目标、风险或选择中的一项；短故事还要检查该场景是否积累高潮与结尾兑现，避免重复功能。
- 长篇每个新章与短故事完整初稿都先保存在当前工作目录。结构、人物、连续性与原创性初检通过后，必须实际调用 `$humanizer-zh` 执行 [drafting.md](references/drafting.md) 的逐章自然化末轮；保留调用前原稿并生成审阅结论和独立结果稿，不原地覆盖任何正文。该 Skill 不可用或调用未完成时只能保留草稿，不得进入正式提交。
- 续写先执行写前连续性上下文和 [long-term-memory.md](references/long-term-memory.md) 的检索协议：检查 SQLite 是否新鲜，加载稳定记忆与近期记忆，按实体、事实和线索搜索旧章，再回读命中的原文，不凭模糊记忆或数据库摘要续接。
- 审稿给出带原文证据的问题、影响和最小修法，区分确定矛盾与需要作者判断的偏好。

### 4. 校验并提交

- 修改项目正典前按 [workspace-isolation.md](references/workspace-isolation.md) 取得项目租约并执行 `write-check`；失败时停止，不得覆盖其他工作的变化。
- 检查因果、时间、地点、知识边界、人物状态、物品、称谓、世界规则和伏笔。
- 对有意的不可靠叙述、误导、时间跳跃或角色撒谎标记为“有意例外”，不要自动修掉。
- 正式章节先在 `staging/chapters/<package>/` 放入调用前原稿 `chapter-before-humanizer.md`、最终结果 `chapter.md`、哈希绑定的 `humanization-review.json`、记忆卡、新连续性状态和提交清单；生成精确状态增量，重新绑定并完成连续性审计，再运行 `scripts/novel_originality.py audit`。自然化、连续性或原创性任一门禁未完成时不得正式提交。
- 作者把逐章自然化设为项目常规要求时，可据此采用不改变语义的结果稿，无需每章重复询问。建议若改变事实、剧情、人物动机、关系、世界规则或 POV，仍必须单独确认；结果稿改变正文时重新执行连续性与原创性检查，确保两份报告覆盖最终全文哈希。
- 使用 `scripts/novel_project.py commit-chapter` 原子写入分章正文、索引、记忆卡、状态、事实库、例外、依赖、审计和正典 `head`；不要手工追加巨型文件，也不要先改 `current_chapter`。
- 提交结果显示检查点到期时立即处理两条线：连载做全局连续性基线和周期质量审核，短故事做全篇连续性基线和质量完稿审核。未通过时可以在工作目录保留修订稿，但不能继续下一章、宣称定稿、导出或上传。
- 写入后运行 `scripts/novel_continuity.py status <project-root>`、`scripts/novel_review.py status <project-root>`、`scripts/novel_project.py validate <project-root>` 和 `scripts/novel_memory.py status <project-root>`；结构或绑定错误必须修复，到期审核必须完成，缓存过期则从文件更新或重建。
- 用户要求阅读或发布交付物时，按 [publishing-exports.md](references/publishing-exports.md) 和 [platform-delivery-quality.md](references/platform-delivery-quality.md) 从通过验证的分章 Markdown 生成派生文件，再运行 `scripts/novel_export.py status <project-root>`；只有 `status: fresh`、`delivery_quality: pass` 且目标配置出现在 `verified_profiles` 中才可交付对应上传包。不得把导出稿中的修改当作正文修改。
- 用户要求登录番茄上传、提交审核、设置定时发布或修订已上传章节时，必须再读 [fanqie-live-publishing.md](references/fanqie-live-publishing.md)，先固定作品、章节范围、排除项和保留设置，得到明确授权后才执行外部操作。
- 验证通过后运行 `scripts/novel_workspace.py base-refresh`，再释放自己持有的项目租约；失败路径也要尝试释放租约，但不能释放其他工作的租约。
- 交付时说明完成了什么、改动了哪些正典、仍有哪些开放线索或需作者决定的问题。

## 组合能力

长篇每个新章和每篇短故事都必须在结构、人物、连续性与原创性初检之后实际调用 `$humanizer-zh` 做专项复核。只采用它对填充词、机械排比、节奏单一、宣传腔、解释过度与协作式痕迹的检查，保留人物口吻、时代语言、意象系统、有意重复、叙述者偏见、不可靠叙述和原有 POV。调用前原稿与最终结果必须分别保存并以 SHA-256 绑定到 `humanization-review.json`；结果可以是修订稿，也可以是明确记录“无需修改”的同内容文件。该记录验证文件、哈希与执行声明，不能从技术上证明模型运行时已加载 Skill，因此执行者必须真实调用并如实填写。不得承诺规避或通过任何 AI 检测器；没有该 Skill 或调用未完成时明确说明阻断，只保留草稿，不得正式提交。

该硬门禁约束规则生效后新写的章节、完整短故事和整章重写，不追溯否定或自动改写已经提交的历史章节；历史章进入重写流程时，从新稿开始执行同一自然化门禁。

## 完成标准

- 产物符合用户要求的题材、长度、视角、尺度和交付范围。
- 每次执行都能定位有效 `work.json`；新工作在实质操作前已经创建隔离目录，同一本小说没有因调用方变化而复制成多个项目。
- 并行工作只在各自目录保存未确认内容；项目写入有有效租约和匹配的基准哈希，没有静默覆盖其他工作。
- 新增内容有绑定当前正典的写前上下文、可精确重放的状态增量和九维审计；复杂回调使用独立 reviewer，没有无解释的状态跳变。
- 章节不是大纲扩写稿：有具体行动、选择、阻力和变化，叙述声音稳定。
- 连载每个已提交章都有唯一正文文件、索引条目和章节记忆卡；短故事恰好有一个完整正文文件、一条索引和一张全篇记忆卡，索引链接可以定位原文。
- 互动策划项目能从 `planning/framework-session.md` 恢复；已确认、暂定、未决定和已排除内容没有混写，框架确认状态与故事圣经和总纲一致。
- 新短故事在正式项目创建前能从工作目录恢复需求、研究、候选审批、原创方向和故事确认状态；项目只在两个信心值均至少为 95 且完整确认单获作者确认后建立，ID 符合 `shortstory-<slug>-YYYYMMDD[-N]`。
- 市场研究能从 `sources/` 和 `research/` 复核；候选审批没有被跳过，近期结论带绝对日期，原创性地图能说明参考机制、禁用元素和因果改造。
- 每个实际使用的来源都有机器可读 provenance、SHA-256、权利状态、授权范围和外部使用边界；用户本地全文没有未经授权进入项目或外部服务。
- 原创性报告分别给出措辞与结构结论，覆盖当前候选哈希；没有用统一百分比掩盖具体风险，单一来源支配已重构或由作者明确裁决。
- 全书压缩记忆、作者决策、连续性状态、稳定事实、例外和依赖图与已提交正文同步；知道/相信/声称分开，大纲四态明确，未完成或未确认内容没有被写成既定事实。
- SQLite 索引能从真源重建并报告新鲜度；搜索命中最终回到相关原文核验，数据库未反向修改正文。
- 导出前连续性为 `current`、质量审核未到期且无开放失效项；导出清单能证明 TXT、DOCX、EPUB、平台分章文件或番茄短故事单文件来自哪个主稿快照。导出状态为 `fresh`、`delivery_quality` 为 `pass`，实际文件经过严格 UTF-8/无 BOM/LF/NFC、危险与不可见字符、文件名、格式残留、库存和平台标题/分场规则复检；DOCX 已做逐页渲染检查，任何派生文件都没有反向成为正典。
- 番茄线上任务有明确授权范围和排除项；每章提交前正文差异检查通过，章节标题、分卷、AI 使用声明和原定发布状态/时间均按确认单保留，超时没有盲目重试，最终列表审计与正文抽查能够证明目标章节完成且非目标章节未被改动。
- 审稿结论有证据，改稿有影响范围和恢复点，不用伪精确总分代替判断。
- 连载检查点同时有独立全局连续性基线和独立周期质量报告；短故事定稿前同时有全篇连续性基线和 `review_mode: completion` 的独立质量报告。状态为 `current`，或明确记录阻断问题且没有继续正式交付。
- 长篇每个已提交章和短故事唯一正文都保留完整的 `humanizer-zh` 审阅记录、调用前原稿与最终结果哈希及采用授权；正式正文只来自记录中的结果文件，任何自然化变化都已重新通过最终连续性与原创性检查。
