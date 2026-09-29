# Changelog

本文件记录会影响工作流契约、数据格式或交付判断的变更。

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
