---
name: novel-studio
description: 规划、创作、续写、审核、修订并导出有长期记忆和连续性门禁的中文长篇小说或完整短故事。用户要互动确定框架、根据近期公开数据选题、跨上下文续写、做周期或完稿审核、生成番茄交付物或受控修订线上副本时使用；长篇每章和短故事全文都必须实际调用 humanizer-zh，纯非虚构写作不使用。
metadata:
  short-description: 以互动框架、长期记忆和审核创作长篇小说或短故事
  requires: humanizer-zh
---

# 小说工作室

这是入口路由，不是全部操作手册。当前工具版本为 `2.2.0`，要求 Python 3.10+，只使用标准库。模块正常加载并进入 `scripts/novel_cli.py` 的 `run_cli` 后，除 `--help` 外向 stdout 输出单个 JSON 文档；业务命令的 `status`/`decision` 随命令而异，退出码 1 表示业务门禁未通过，参数或领域错误为 2，未预期异常与序列化失败为 3。模块加载失败发生在该运行时契约之前，可能由 Python 直接输出 traceback 并返回退出码 1。`--version` 才固定返回 `status: ok`、`tool` 和 `version`。`metadata.requires` 声明本 Skill 的 `humanizer-zh` 依赖；顶层 `requires` 不属于当前技能规范允许的 frontmatter 字段。详细字段见 [schemas-and-cli.md](references/schemas-and-cli.md)。

## 先判断是否需要写入

只读查询不需要创建或复用工作目录，也不需要租约：

```powershell
python -X utf8 .\scripts\novel_workspace.py --version
python -X utf8 .\scripts\novel_workspace.py doctor
python -X utf8 .\scripts\novel_workspace.py status "<workspace-root>"
python -X utf8 .\scripts\novel_project.py status "<project-root>"
python -X utf8 .\scripts\novel_project.py validate "<project-root>"
python -X utf8 .\scripts\novel_continuity.py status "<project-root>"
python -X utf8 .\scripts\novel_review.py status "<project-root>"
```

任何会创建草稿、研究、报告或修改正典的工作才需要隔离上下文。先运行 `work-ensure`，或用已知 `work_id` 运行 `work-resume`；工作目录存在不等于小说项目已经建立。

工作生命周期命令如下：

| 命令 | 作用 |
|---|---|
| `work-start` | 创建新的隔离工作目录 |
| `work-ensure` | 复用当前目录已有工作，或按需创建 |
| `work-resume` | 按 `work_id` 恢复活动工作 |
| `work-reconcile` | 从注册表修复漂移的 `work.json` 投影 |
| `work-list` | 列出工作上下文，可只看活动项 |
| `work-close` | 将工作标记为 `closed`，保留文件，不删除草稿 |

## 最小成功路径

### 只读/草稿阶段

1. 需要写草稿时创建或恢复工作：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>" --client "generic"
   ```

2. 继续既有项目时列出并绑定项目；新短故事在两个 95% 闸门和作者确认完成前保持未绑定：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py project-list "<workspace-root>"
   python -X utf8 .\scripts\novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<project-id>"
   ```

3. 正文、研究和中间报告先写当前 `work_root`。`prepare-context` 的输出必须位于工作目录或其他项目外路径；完成租约和 `write-check` 后，才复制到项目 `staging/chapters/<package>/`。

框架确认也遵循同一边界：把框架、作者决策、全书摘要和受控项目设置放在项目外的 `framework-sync/` 包，取得租约后调用 `novel_project.py framework-sync` 一次性同步；不要先直接修改项目内正典再用 `framework-state` 追认。包结构只在 [controlled-automation.md](references/controlled-automation.md) 维护。

### 正典提交阶段

不要从本入口重建提交顺序或暂存清单；它们只在 [commit-protocol.md](references/commit-protocol.md) 维护。按该协议完成租约与 `write-check`、组装暂存包、绑定最终正文的连续性和原创性审计、运行 `commit-chapter`，再完成统一验证、基准刷新和租约释放。原创性报告先写 `staging/originality/`，仅在成功提交的同一事务中归档到 `reviews/`。

## 不可绕过的边界

- 正文 Markdown、章节索引、故事圣经、长期记忆和连续性文件是真源；SQLite 只是可重建检索缓存。
- 未确认内容留在工作目录；正式写入正典必须持有项目租约、通过状态哈希检查，并使用 `commit-chapter` 的单次原子事务。
- `staging/` 和 `exports/` 不计入当前状态哈希，但不因此获得绕过租约或提交器的权限。
- `prepare-context` 是只读项目读取加外部输出；复制进项目 staging 属于写入，必须在租约和 `write-check` 之后完成。
- 正文、上下文或状态增量改变后，旧的连续性审计、原创性报告和质量报告全部失效；不能只修改报告里的哈希。
- 长篇每 5 章做独立全局连续性基线和质量审核；短故事唯一正文提交后做全篇双审核。未通过或到期时停止下一章、导出和上传。
- 研究来源必须有来源记录、SHA-256、权利状态和授权范围；不使用盗版全文、不绕过访问控制。
- 平台副本不是正典。线上写入、上传、定时发布或覆盖已有章节需要另行明确授权，并以当前官方规则核验。

## 自然度与原创性

长篇每章和短故事全文在正式提交前都必须实际调用 `$humanizer-zh`。保留 `chapter-before-humanizer.md`、最终 `chapter.md` 与哈希绑定的 `humanization-review.json`；Skill 不可用或调用未完成时只能保留草稿。依赖名和 JSON 的 `skill` 字段写 `humanizer-zh`，Agent 调用语法写 `$humanizer-zh`，两者不要混用。

质量报告中的历史机器字段 `humanization_and_repetition` 统一解释为“叙述自然度、声音一致性与机械模式检查”，包括机械排比、解释过度、段落过于均匀、空泛总结和无意重复。它不承担平台检测目标，也不代表规避、欺骗或通过任何 AI 检测器；不得向作者或平台作此承诺。

原创性审计同时检查措辞重合和结构映射，不能用一个百分比代替证据。单一来源主导、独特场面或因果链高风险时必须重构或请求作者裁决。

## 按任务加载参考

下表是参考文件路由的唯一索引；各参考中的交叉链接只补充局部前置条件，不另行定义一套模式映射。

| 任务 | 先读 |
|---|---|
| 互动建书/框架确认 | [interactive-planning.md](references/interactive-planning.md)、[planning.md](references/planning.md)、[controlled-automation.md](references/controlled-automation.md) |
| 市场研究/来源登记 | [market-research.md](references/market-research.md)、[platform-adapters.md](references/platform-adapters.md)、[source-ingestion.md](references/source-ingestion.md) |
| 新建或升级项目 | [project-contract.md](references/project-contract.md) |
| 写作/续写/记忆检索 | [drafting.md](references/drafting.md)、[long-term-memory.md](references/long-term-memory.md)、[continuity.md](references/continuity.md) |
| 正典暂存与提交 | [commit-protocol.md](references/commit-protocol.md)、[controlled-automation.md](references/controlled-automation.md)、[schemas-and-cli.md](references/schemas-and-cli.md) |
| 原创性审计 | [originality-audit.md](references/originality-audit.md) |
| 审稿/周期审核/短故事完稿 | [revision.md](references/revision.md)、[periodic-review.md](references/periodic-review.md)、[short-story-mode.md](references/short-story-mode.md) |
| 批量改稿 | [batch-revision.md](references/batch-revision.md) |
| 导出与平台交付 | [publishing-exports.md](references/publishing-exports.md)、[platform-delivery-quality.md](references/platform-delivery-quality.md) |
| 番茄线上操作 | [fanqie-live-publishing.md](references/fanqie-live-publishing.md)；仅在获得当次明确授权后执行 |
| 发布后复盘/数据回流 | [publication-feedback.md](references/publication-feedback.md)、[market-research.md](references/market-research.md)、[periodic-review.md](references/periodic-review.md) |

一次性聊天内构思或片段润色不强迫创建正式项目；需要跨会话恢复、研究归档、审核包、导出或正典写入时才绑定项目。新短故事还要先完成默认研究、候选审批、原创方向和完整确认单。

## 完成时必须说明

交付时报告实际修改的文件/正典、运行过的验证、当前连续性和质量门禁状态、尚未解决的线索或作者裁决项。不得把“脚本通过”表述为“故事没有矛盾”，也不得把本地自然度审阅表述为检测规避能力。
