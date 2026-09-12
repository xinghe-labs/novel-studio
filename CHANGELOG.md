# Changelog

本文件记录会影响工作流契约、数据格式或交付判断的变更。

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
