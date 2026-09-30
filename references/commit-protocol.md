# 正典提交协议（规范真源）

本文件是所有模式的受控正典写入协议真源，并唯一维护完整暂存清单。`controlled-automation.md` 保留自然化行为边界，`schemas-and-cli.md` 维护机器可读字段，`short-story-mode.md` 保留唯一正文单元约束，其他文件只补充模式差异；涉及顺序、租约或 fail-closed 语义时以本文件为准。

## 唯一提交顺序

所有会推进正典的章节或短故事提交都按以下顺序执行。前一步的正文、上下文或状态变化会使后一步之前生成的绑定报告失效：

```text
1. work-resume/work-ensure
2. work-bind（已有项目时）
3. lock-acquire
4. write-check
5. 在工作目录完成正文、自然度审阅和报告准备
6. 在租约窗口内把已完成输入复制/组装到 staging 包
7. 完成 state-delta
8. bind continuity-audit
9. 完成 continuity-audit
10. 运行 originality audit，报告先写 staging/originality/
11. check-package
12. commit-chapter
13. validate、continuity status、review status、memory status
14. base-refresh
15. lock-release
```

原创性报告只有在 `commit-chapter` 成功事务中才归档到 `reviews/`；审计命令本身不应改变正典审核目录。

## 提交前

1. 当前工作必须有稳定的 `work_id`，并绑定目标 `project_id`。
2. 取得租约并检查基准哈希：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py lock-acquire "<workspace-root>" "<work-id>"
   python -X utf8 .\scripts\novel_workspace.py write-check "<workspace-root>" "<work-id>"
   ```

   长任务在五分钟心跳窗口内运行 `lock-renew`。写入前任何检查失败都停止并重新读取项目；不能只编辑 `work.json` 修补哈希。
3. 初稿留在工作目录。进入项目 `staging/` 前，确认本轮正式提交范围；`staging/` 是输入区，不是正典。
4. 每章或每篇短故事必须实际调用 `$humanizer-zh`。保存调用前原稿、最终结果和 `humanization-review.json`；该记录检查叙述自然度、声音一致性与机械模式，不承诺规避、欺骗或通过任何 AI 检测器。Skill 不可用或记录不完整时只能排队草稿。
5. 对最终正文完成状态增量、连续性审计和原创性审计。任何正文、状态或上下文变化都会使旧哈希绑定失效，必须重新绑定和复审。

## 原子提交

提交器必须显式收到工作区和工作身份：

```powershell
python -X utf8 .\scripts\novel_project.py commit-chapter "<project-root>" "staging/chapters/<package>" --workspace "<workspace-root>" --work-id "<work-id>"
```

提交器在读取阶段和事务写入前各执行一次 `write-check`，并验证租约所属项目等于 `project-root`。实际文件替换期间，提交器还持有同一注册表连接的 `BEGIN IMMEDIATE` 写事务；这段 `write_guard` 保证另一工作不能在最终授权检查与文件验证之间抢占租约。缺少参数、租约过期/心跳失效、基准哈希变化、自然化/连续性/原创性任一门禁失败时 fail closed。事务失败恢复原字节，不推进 `novel.json.current_chapter`。

当前状态哈希版本为 2，排除 `staging/`、`exports/` 和缓存目录。刚从旧版本恢复的工作上下文如果只匹配旧的 staging-inclusive 哈希，会临时以 `state_hash_version: 1` 通过；这只是迁移窗口，完成回读后必须运行 `base-refresh` 升级基准，不能长期依赖兼容分支。

事务覆盖：正文、章节记忆卡、索引、连续性状态、事实库、例外库、依赖图、审计记录、可选时间线/线索/全书记忆、项目元数据和正典 `head`。SQLite 只在事务成功后作为可重建缓存更新。

## 中断事务的恢复与冲突隔离

每次受控写入前都会自检 `.novel-transaction/`。事务 journal 在写入任何备份之前落盘，因此崩溃窗口只可能留下两类工件：`backup-NNNN.bin`（备份）和 `.<name>.tmp`（元数据临时文件）。这两者都产生于任何正典字节被替换之前，恢复时按白名单直接丢弃，并在返回结果里说明；出现未登记的工件仍然 fail closed，不会删除不确定的文件。

如果 journal 记录的哈希与磁盘上的字节既不匹配"已回滚/已提交后的旧状态"也不匹配"替换后的新状态"（典型原因：事务之外有人手工改了同一个文件），就不存在正确字节状态可恢复。此时整个事务目录被移动到 `.novel-transaction-conflicts/<UTC 时间戳>-<事务名>/`，其中保留原 journal、备份和一份 `conflict.json` 证据（记录原因、检测时间与分歧路径），并在注册表的 `transaction_conflicts` 表登记。项目随后可以继续写出。

冲突目录被排除在正典状态哈希和升级快照之外，因此不会污染 `canon_sha256` 或升级 diff；它是证据，不随清理自动删除。`validate`/`status` 对冲突返回 warning（不阻断），`doctor` 报告 `transaction-conflicts` warning 并在 `transactions` 检查里列出待恢复事务。冲突意味着对应章节的字节可能已与审核结论不一致：在信任这些章节之前，应按 `conflict.json` 的分歧路径回读正文，重新过门禁，而不是直接删除证据目录。

暂存包固定清单：

```text
commit.json
chapter-before-humanizer.md
chapter.md
humanization-review.json
memory.md
continuity-context.json
continuity-state.json
state-delta.json
continuity-audit.json
```

`timeline.md`、`threads.md`、`book-summary.md` 是可选的完整替换稿。各文件字段以 [schemas-and-cli.md](schemas-and-cli.md) 为准。

## 提交后

按以下顺序完成收尾：

1. 运行 `novel_project.py validate`、`novel_continuity.py status`、`novel_review.py status` 和 `novel_memory.py status`。
2. 到达长篇五章检查点或短故事唯一正文检查点时，分别完成独立全局连续性基线和质量审核；任一到期、过期或未通过都阻断下一章、导出和上传。
3. 复核结果后刷新基准并释放自己的租约：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py base-refresh "<workspace-root>" "<work-id>" --validation-reference "已完成项目与长期记忆复核"
   python -X utf8 .\scripts\novel_workspace.py lock-release "<workspace-root>" "<work-id>"
   ```

失败路径也只能释放自己的 owner；疑似遗留租约用 `lock-break --expected-owner ... --reason ...`，不能静默删除注册表行。释放或回收时即使项目哈希失败，也会删除确认属于自己的租约并在 `lease_events.state_hash_error` 留下错误，避免损坏项目把锁永久卡死；错误必须在后续复核中处理。
