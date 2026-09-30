# Changelog

本文件记录会影响工作流契约、数据格式或交付判断的变更。

## 2.8.2 - 2026-09-30

- 需求层闸门补商业生存预检三件:失败同行预验尸(同题材 3-5 本死因各一句 + Agent 对抗性反证至少三条,作者逐条回应,映射为确认单预防措施)、更新节奏与存稿缓冲承诺(日更字数×每周天数、开更缓冲章数、断更预案,不填不立项)、平台义务核查(已有签约作品的独家条款范围不波及新书)。
- [market-research.md](references/market-research.md) 新增失败同行分析小节:反面样本不进正面候选池、不与成功样本混算、登记来源即可。
- 命令与数据格式无变化。

## 2.8.1 - 2026-09-30

- 开机预检实战校准(对既有项目回溯走 A-F)发现缺口并补齐:结构与大纲阶段新增「逾期线索清查」——回收窗口早于当前章且未回收未废弃的线索逐条裁决(兑现/再铺设/改窗口/废弃);开机检查单新增对应第 9 项。命令与数据格式无变化。

## 2.8.0 - 2026-09-30

- 新增 `references/building-precheck.md` 新书开机预检:A-F 六阶段建书完成度清单(方向与研究/故事核心与人物/声音与风格/结构与大纲/形式与工程/开机检查单),把 2.4.0-2.5.0 的风格合同五段、声音锚点、期待线表、对抗力量阶梯、黄金三章草案、黑名单种子、中段预案收拢为建书期的单一验收清单,每项带完成判据。
- 互动建书确认闸门挂接预检:确认单定稿前过 A-E,第一章合同之前过 F;确认单"仍未决定"分为可后定与写前必须定两类,后者清零才生成第一章合同。
- 命令与数据格式无变化。

## 2.7.0 - 2026-09-30

- 事务恢复自愈（P0）：一个没有 journal 的事务目录不再让项目永久不可写。`transactional_write` 改为先写 `prepared` journal、再写备份，因此崩溃窗口留下的工件只可能是 `backup-NNNN.bin` 或 `.<name>.tmp`，而这两者都在任何目标字节被替换之前产生——恢复时按白名单直接丢弃；无法识别的工件仍然 fail-closed，`doctor` 不再对不可写项目报 `pass`。
- 事务冲突隔离（P0）：已提交/已回滚/进行中的 journal 若与磁盘上的字节不再匹配任何可恢复状态（外部改动、目标缺失），旧行为是让 `validate`/`status`/`lock-acquire` 永久失败。现在把整个事务目录移入 `.novel-transaction-conflicts/`，写入 `conflict.json` 证据并在注册表 `transaction_conflicts` 表记录（新增表，只增不删迁移），项目可继续写出；`validate`/`status` 报 warning，`doctor` 报 `transaction-conflicts` warning。冲突目录已加入 `HASH_EXCLUDED_PARTS` 与 `UPGRADE_IGNORED_PARTS`，不参与正典哈希与升级快照。
- 提交与导出共用同一门禁判据（P1）：`commit-chapter` 一直遵守 `periodic_review.block_next_commit`，而 `export` 只看 `review_due`——选择不阻断提交的项目能提交却永远无法导出。现在两条路径都调用 `novel_review.review_gate()`；策略关闭阻断时，提交结果、导出结果与 `review_status` 都会带明确告警。
- 事务回滚安全性（P2）：journal 在提交标记后瞬时不可读时，不再猜测字节状态并回滚，而是保留事务交给恢复流程；`atomic_write_bytes` 补目录 fsync，回滚后的重命名同样耐久。
- 提交暂存守卫（P2）：`assert_staging_snapshot` 在缺少暂存快照信息时从静默跳过改为 fail-closed。
- 租约语义诚实化（P2）：`lock-acquire` 与 `lease_status` 增报 `live_until`/`live_in_seconds`/`limited_by`，说明生效上限由心跳宽限而非 `expires_at` 决定；旧注册表行的 expires-only 语义不变。
- `install.py --force` 不再删除被替换的外部目录：改名为 `novel-studio-replaced-<UTC 时间戳>` 保留并打印路径。
- `novel_cli.py` 直接运行不再静默退出 0：按统一契约输出 JSON 错误并以退出码 2 说明它是共享库而非命令（8 个可执行 CLI 不变）。
- 文档与 CI：README 行数统计改由 `ci/check_doc_stats.py` 在 CI 中核对；README 明确 Windows 3.13 job 与临时诊断 job 不阻断流水线；[schemas-and-cli.md](references/schemas-and-cli.md) 的 `--version` 示例改为版本无关占位符；[project-contract.md](references/project-contract.md) 把"不建议手工更新正典"改为硬规则；[workspace-isolation.md](references/workspace-isolation.md) 记录 `--allow-bootstrap` 边界并接入 SKILL.md 路由表。
- 命令与数据格式无变化（注册表新增 `transaction_conflicts` 表，仍为只增不删迁移；`STATE_HASH_VERSION` 不变）。

## 2.6.1 - 2026-09-29

- 逐行审核低级项收尾：`project-register` 的 ID/路径冲突检查移入 `BEGIN IMMEDIATE` 预约内，并发登记同一 ID 不再能静默改写指向；`invalidate` 的 invalidations/head 写入补 `expected_existing` CAS 收据，租约换手窗口内的并发失效登记不再互相覆盖。
- `work.json` 投影失败的六处命令（work-ensure/resume、bind、write-check、base-refresh、close）给出可区分的"已提交但投影失败，运行 work-reconcile"消息，不再与提交前错误混淆。
- 研究采集：`fetch_public_json` 返回实际响应 URL，跨主机重定向直接拒绝（登记来源永不偏离请求主机），同主机重定向把最终 URL 写入 provenance 注记。
- 事务目录 fsync 在 Windows 走 `FlushFileBuffers`（best-effort），补上文档承认的重命名元数据耐久性缺口；journal 提交标记重读加短重试，瞬时读失败不再回滚已提交字节。
- DOCX 必需部件清单补 `word/_rels/document.xml.rels`、`docProps/core.xml`、`docProps/app.xml`（构建器本就写入，校验补齐纵深防御）。

## 2.6.0 - 2026-09-29

- SQLite 检索层引入 FTS5 trigram 全文索引（`SCHEMA_VERSION` 1→2，旧缓存判 stale，`rebuild` 一次即可）：启动时自动探测 FTS5 可用性，可用时 `chunks_fts` 随 rebuild/update 维护，短语检索用它做候选预筛并按 bm25 相关度参与排序；短于三字的查询、含英文字母的查询、any/all 模式下的不安全词组合、无 FTS5 环境或索引表不完整时，自动回退到全量扫描——`query_matches` 匹配语义、返回字段与实体检索完全不变。
- 检索定位协议不变：`search` 仍只用于决定回读哪些原文，不替代回读；FTS 层是可重建派生缓存的一部分，不是第五层正典。
- 测试新增：FTS 表与 chunks 计数同步（rebuild/update）、FTS 预筛与禁用回退结果等价、短词回退命中。

## 2.5.3 - 2026-09-29

- 逐行脚本审核修补。SQLite 缓存锁自愈：写入者中途被杀留下的空/残缺锁文件在下次竞争时被直接回收（进入临界区前重读并匹配自身 token，保证不会出现双持有者）；Windows 探活改用 `OpenProcess`+`WaitForSingleObject`，替代在 win32 上恒为 CTRL_C_EVENT 语义的 `os.kill(pid, 0)`。
- 导出源读取改为 `read_stable_bytes` 并同源取哈希：章节与 index 的 SHA-256 一律来自实际解析的同一份字节，消除并发改稿下"旧正文+新哈希"混合包窗口；`source_snapshot_hash` 公式因此变化，旧导出清单会判 `stale`，重新导出即可。
- `UPGRADE_IGNORED_PARTS` 补 `.git`，与状态哈希排除集一致；内嵌 git 仓库的项目不再被升级快照卷入。
- 非 UTF-8 项目文件改为结构化域错误（退出码 2）：validate/status/upgrade/commit 的裸 `read_text` 统一收口到 `read_utf8_project_text`，连续性证据校验把解码失败记入 errors 列表。
- 命令与数据格式无变化；导出清单哈希公式变化见上。

## 2.5.2 - 2026-09-29

- 脚本层审核收尾：新增 `tests/test_memory_cache.py`，直接覆盖 SQLite 缓存的损坏库报告与重建、过期库判定、`search --require-fresh` 拒绝路径、以及实体抽取只认显式标签不从正文臆造的边界。
- `test_cli_contract` 新增退出码契约固化测试：业务门禁失败返回退出码 1 且 stdout 保持可解析的业务 JSON（与退出码 2/3 的形状区分）。
- 命令、数据格式与门禁行为无变化。

## 2.5.1 - 2026-09-29

- 完整审核的文档修补：周期质量审核读取协议纳入 [drafting.md](references/drafting.md) 的写作机理（期待感来源、爽点前提、对抗阶梯、信息差），使 2.5.0 的机理进入审核对照；写前必读清单在 [continuity.md](references/continuity.md) 钉为唯一全集，其余清单为视角子集。
- [schemas-and-cli.md](references/schemas-and-cli.md) 修正 `--version` 示例版本并标注随发布变化；新增 `framework-state` 的调用语法与边界小节。
- README（中/英）补充 `novel_workspace.py init --adopt-existing` 的收编语义；[publication-feedback.md](references/publication-feedback.md) 新增质量信号类记录（编辑反馈、审核标记、留存骤降）及回灌 `style-guide.md` 黑名单的路径。
- 命令、数据格式与门禁无变化。

## 2.5.0 - 2026-09-29

- 写作知识内容层增强，全部为“机理+失败模式”紧凑条目，不新增节拍模板：[drafting.md](references/drafting.md) 新增「信息差设计」「期待感与兑现」「爽点与情绪价值」三节，扩写对话潜台词、冲突升级阶梯、话语权与群戏技术，并新增三组“平写 vs 活写”机理标注的「对照示例」。
- [planning.md](references/planning.md) 新增「对抗力量阶梯」（即时/章节/卷/幕后分层、升级同步、压迫-喘息循环）与「中段防塌陷」（重复打怪、升级无感、关系停滞、目标模糊、支线资格五类慢性病对策）。
- 命令、数据格式与门禁无变化。

## 2.4.0 - 2026-09-29

- 写作链路补正向定调层：`story-bible/style-guide.md` 明确为风格合同的唯一归宿，最少字段新增句长节奏、比喻密度、对话留白、每章字数带、断章风格与禁用措辞黑名单（脚手架模板同步扩充）；新增可选 `story-bible/voice-anchor.md` 收录作者亲写片段，作为写前定调与自然化保留项的正向基准。数据格式无变化。
- 写作合同模板新增 `节拍` 与 `钩子` 字段；[drafting.md](references/drafting.md) 新增“开篇章与卷首”一节，前 3 章与卷首逐项确认冲突进入位置、首个兑现与开篇承诺一致性。
- 章节记忆卡新增质量审核字段：结尾钩子、开头进入与实际字数；[periodic-review.md](references/periodic-review.md) 新增“跨窗口重复扫描”，每个检查点用 SQLite 检索或 `rg` 在全本范围核对高频短语、意象与断章方式的碰撞和漂移。旧项目不回填，连续性结论不依赖新字段。
- 内置 `humanizer-zh/` 副本新增小说专项模式：情绪直接命名、器官反应套话、对话节拍机械交替、章首场景重置公式、比喻密度过高、心理转场套话、人物声音趋同。
- 入口 `SKILL.md` 拆分运行契约段落，frontmatter 合规说明移入 [DESIGN.md](DESIGN.md)；文档行为边界无变化。

## 2.3.0 - 2026-09-18

- 内置 `humanizer-zh/` 副本：解析顺序改为 `NOVEL_HUMANIZER_PATH` → Skill 根目录内置副本 → 相邻安装 → `.agents` → `.codex`。内置副本是第三方 MIT Skill（译自 blader/humanizer，版权与来源标注见其目录内 `LICENSE` 与 SKILL.md frontmatter），属于运行时内容、随受控分发归档交付；正式提交仍要求实际调用，人工声明与 `--force` 依旧不可绕过。
- 新增根目录 `install.py` 一键安装器：只复制受控文件（`git ls-files`，缺 git 时按同规则目录遍历），应用 `.gitattributes` 的 `export-ignore`（当前排除 `continuity-eval/`），校验目标目录身份后原子替换既有安装（外来目录需 `--force`），装完以子进程运行 `--version` 与 `doctor` 并报告；`doctor` 未通过时退出码 1。安装器输出人类可读文本，不属于 CLI JSON 契约。
- 仓库新增 `continuity-eval/`：连续性评测基准（注入器 + 确定性检测器 + 属性通道 + 评测/扫参），CI 增加其测试步骤（42 个 unittest）。
- 分发边界变更：`continuity-eval/` 经 `.gitattributes` `export-ignore` 排除出 `git archive` 发布物——它随仓库维护、由 CI 测试，但不是 Skill 运行时内容。引擎能力与数据格式无变化。
- 隐私整改：真实语料快照清单移出版本控制（本地保留；`continuity-eval/snapshots/README.md` 记录 `corpus_digest` 作为钉死存证，测试套件的合成语料不受影响）。

## 2.2.0 - 2026-09-13

- 正典写入增加持久事务 journal、比较并交换校验和字节保真回滚，键盘中断与系统退出不再留下不可恢复的半提交。
- 连续性、质量、原创性、研究和导出流程增加稳定读取、完整来源范围校验，以及符号链接、junction 与 reparse point 边界。
- `framework-sync` 以九输入事务同步框架和受控项目设置，并在已有正文时强制处理连续性失效。
- `prepare-context` 和审核准备产物必须先写项目外工作目录，项目 staging 写入继续受租约与 `write-check` 约束。
- 新增统一的 Schema 与 CLI 契约，收敛提交顺序、暂存包、审核报告和工作生命周期文档；Skill 入口改为渐进披露路由。
- 扩充事务、并发授权、框架同步、研究、原创性、连续性和导出故障注入回归测试。

## 2.1.0 - 2026-09-12

- 为全部命令行工具统一 UTF-8 stdout/stderr、JSON 参数错误和未预期异常契约，并增加 JSON `--version`。
- `commit-chapter` 强制要求 `--workspace` 与 `--work-id`，在事务写入前重新校验租约、项目归属和基准哈希。
- 工作区增加显式 `lock-renew`、带 owner/原因审计的 `lock-break`，并让 `heartbeat_at` 参与租约存活判定。
- 注册表采用只增 SQLite 迁移，增加租约时长和 `lease_events` 审计字段；`staging/` 与 `exports/` 不计入正典状态哈希。
- 增加只读 `novel_workspace.py doctor [workspace]`，检查运行环境、脚本导入、SQLite、humanizer-zh、工作区 schema，以及注册表必需表和列的完整性。
- EPUB 校验增加 `container.xml`、OPF manifest/spine、nav 和 NCX 引用闭包检查。
- 补充 CLI、租约恢复、导出断链和中文 Windows 编码回归测试。
- `doctor` 现在验证 humanizer `SKILL.md` 可读且声明正确名称，并能在 `.agents`/`.codex` 两种用户 skill 根之间解析；活跃 SQLite WAL 也由只读检查正确读取。
- 旧注册表只缺部分租约列时按过期时间语义保守迁移；旧状态哈希只临时兼容并要求 `base-refresh`，释放/回收的哈希异常写入 `lease_events.state_hash_error`。

## 2.0.0

- 重命名为 `novel-studio`，保留长篇与短故事双模式、连续性硬门禁、原创性审计和平台交付质量契约。
