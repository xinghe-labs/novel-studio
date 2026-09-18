# Changelog

本文件记录会影响工作流契约、数据格式或交付判断的变更。

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
