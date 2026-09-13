# 工作区与并行隔离

只有会创建草稿、研究、报告或修改项目资料的操作才需要读取本文件并建立工作上下文；只读的 `doctor`、`status`、`validate`、`help` 和版本查询可以直接执行。这里的“工作”不绑定 Codex 会话：它可以来自任何 Agent、脚本、窗口、进程或人工操作。工作身份由 `work_id` 和本地 `work.json` 决定。

## 两层目录

默认工作区由用户指定；以下命令统一使用 `<workspace-root>` 占位符，实际路径以当前项目配置为准：

```text
<workspace-root>/
|-- workspace.json
|-- registry.sqlite3
|-- projects/
|   `-- <project-id>/
|       |-- .novel-project.json
|       |-- novel.json
|       `-- ...小说正典、研究与长期记忆
`-- workspaces/
    `-- <work-id>/
        |-- work.json
        |-- drafts/
        |-- research/
        |-- reports/
        `-- temp/
```

- `projects/<project-id>/` 是一本小说的长期项目目录。同一本小说始终复用同一 `project_id`，不能因为换了 Agent、窗口或任务就复制项目。
- `workspaces/<work-id>/` 是一次独立工作的隔离目录。草稿、未批准研究结论和中间报告先写在这里，不能直接混入正典。
- `registry.sqlite3` 使用 SQLite WAL 协调项目、工作上下文和单写者租约；项目和工作目录内的 JSON 元数据可用于恢复登记。注册表不是小说正典。
- `projects/<project-id>/exports/` 是从已提交 Markdown 重建的交付目录，不是正典，也不计入项目状态哈希；它不能用来绕过正典写入协议。

## 需要写入时的预检

在需要保存草稿、研究、审核包或正典变更时，先运行：

```powershell
python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>" --client "generic"
```

如果调用方已保存 `work_id`：

```powershell
python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>" --work-id "<work-id>"
```

如果当前工作目录或父目录已经有有效 `work.json`，`work-ensure` 返回 `reused`；没有时才创建新的 `work-*` 目录。不得在同一次工作中重复创建目录，也不得用“最近一个活跃工作”猜测上下文。

工作生命周期命令：

```powershell
python -X utf8 .\scripts\novel_workspace.py work-start "<workspace-root>" --purpose "..."
python -X utf8 .\scripts\novel_workspace.py work-resume "<workspace-root>" "<work-id>"
python -X utf8 .\scripts\novel_workspace.py work-reconcile "<workspace-root>" "<work-id>"
python -X utf8 .\scripts\novel_workspace.py work-list "<workspace-root>" --active-only
python -X utf8 .\scripts\novel_workspace.py work-close "<workspace-root>" "<work-id>"
```

`work-reconcile` 只从 SQLite 注册表修复便携的 `work.json` 投影，不修改正典、草稿或注册表事实；`work-close` 只更新状态，不删除工作目录、草稿或报告；`work-resume` 只恢复 `active` 工作。

新建的工作可以先保持未绑定。需要继续已有小说时先列出项目，再绑定：

```powershell
python -X utf8 .\scripts\novel_workspace.py project-list "<workspace-root>"
python -X utf8 .\scripts\novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<project-id>"
```

只有用户明确要求创建新小说时才创建项目：

```powershell
python -X utf8 .\scripts\novel_workspace.py project-create "<workspace-root>" --title "<书名>" --genre "<题材>"
```

新短故事是更严格的特例：用户说“写短故事”时只创建未绑定工作目录，完成默认研究、两级 95% 闸门和完整确认单后才创建并绑定项目：

```powershell
python -X utf8 .\scripts\novel_workspace.py project-create "<workspace-root>" --title "<故事名>" --genre "<题材>" --work-type short_story --target-words 12000 --short-story-slug "<semantic-slug>"
python -X utf8 .\scripts\novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<返回的-project-id>"
```

短故事 ID 自动使用 `shortstory-<slug>-YYYYMMDD[-N]`。必须使用创建命令实际返回的 ID 进行绑定，不能预判同日冲突序号；ID 创建后不随标题修改。

存在多个项目而用户没有明确是哪一本时，列出项目并请求选择；不能自动选择最近更新项目。用户只在聊天中构思但尚未决定是否建书时，也先创建未绑定工作目录，不创建空小说项目。

## 工作目录边界

- 未确认的正文、方案、竞品拆解和改稿候选写入当前 `work_root` 下对应目录。
- `project_root` 中的故事圣经、总纲、正文、章节索引、长期记忆和连续性账本是正典或受控项目资料。
- 一项工作可以跨 Agent、进程或日期恢复；调用方应保存 `work_id`，或从原工作目录继续。
- 关闭工作只把 `status` 改为 `closed`，不自动删除草稿、报告或研究记录。
- `.novel-cache` 是每个项目自己的可重建缓存；工作注册表不能反向覆盖项目文件。

## 同一本小说的写入协议

不同工作可以同时读取同一本小说，也可以分别在自己的工作目录中研究和写草稿。任何会修改正典、受控研究资料、长期记忆或连续性状态的动作都必须串行执行：

```powershell
python -X utf8 .\scripts\novel_workspace.py lock-acquire "<workspace-root>" "<work-id>"
python -X utf8 .\scripts\novel_workspace.py write-check "<workspace-root>" "<work-id>"
```

`write-check` 同时要求：

1. 当前工作持有尚未过期的项目写入租约。
2. 项目文件哈希仍与绑定时的 `base_state_hash` 一致。

项目哈希排除可重建的 `exports/` 和工作输入用的 `staging/`，也排除 `.novel-cache/`、`.git/` 与 Python 缓存；这些目录的变化不会把当前工作自己的暂存包误判为并发正典修改。其他项目文件仍全部参与哈希。2.1.0 之前的工作上下文可能保存了包含 `staging/` 的旧哈希，工具只在 `write-check` 中临时识别并标记 `legacy_hash_accepted`，完成回读后必须用 `base-refresh` 写入当前算法的基准，不能把兼容状态当成永久通过。

任一条件不满足就停止写入。项目已经被其他工作修改时，先重新读取变化、处理冲突并重新验证；不能静默覆盖，也不能只修改 `work.json` 绕过检查。

`exports/` 是唯一例外：`novel_export.py` 只读通过验证的正典，先在临时目录构建派生包，并在发布前复查源快照，因此生成或刷新导出文件不要求取得正典写入租约。导出文件不计入 `base_state_hash`，多个导出任务不能据此修改正文；发现源快照变化或人工改过的派生文件时导出器必须停止。

长时间写入任务必须在租约的心跳窗口内续租。默认租约为 1800 秒，心跳超过 300 秒（或更短租约的全部时长）未刷新即视为失效：

```powershell
python -X utf8 .\scripts\novel_workspace.py lock-renew "<workspace-root>" "<work-id>"
```

同一工作重新运行 `lock-acquire` 仍可兼容地续租，但新流程应优先使用语义明确的 `lock-renew`。崩溃恢复只能使用带预期 owner 和非空原因的受控回收：

```powershell
python -X utf8 .\scripts\novel_workspace.py lock-break "<workspace-root>" "<project-id>" --expected-owner "<work-id>" --reason "原进程已退出，已核对没有活动写入者"
```

有效租约不能被 `lock-break` 回收；回收、续租和释放都会在 `lease_events` 中留下时间、owner、原因和项目哈希。若项目哈希读取失败，事件仍会写入但 `state_hash_error` 记录异常，租约仍会按明确 owner 删除。`novel_workspace.py status` 会同时报告心跳年龄、过期倒计时和实际 `live_until`。

旧注册表的迁移只增不删。只要 `leases` 缺少 `lease_seconds` 或 `heartbeat_enforced` 任一新增列，已有租约就按旧的 `expires_at`-only 语义保守迁移；明确标记为 legacy schema 的注册表也一样。只有该租约被新的 `lock-acquire` 或 `lock-renew` 成功写回后，才启用五分钟心跳门禁。只读 `doctor` 不执行迁移，因此对 legacy schema 会报告需要迁移的阻断；用可写工具打开并完成迁移后再重新运行 doctor。

正式提交的文件替换期间，提交器通过 `write_guard` 持有 `BEGIN IMMEDIATE` 注册表事务，并在最终验证中再次检查 owner 和心跳；这使租约检查与原子文件事务处于同一受保护窗口。

完成一次已授权写入后，严格执行 [commit-protocol.md](commit-protocol.md) 的完整“提交后”验证清单；本文件不再维护一个可能漂移的验证子集。验证全部通过后，再更新基准并释放租约：

```powershell
python -X utf8 .\scripts\novel_workspace.py base-refresh "<workspace-root>" "<work-id>" --validation-reference "项目与长期记忆验证通过"
python -X utf8 .\scripts\novel_workspace.py lock-release "<workspace-root>" "<work-id>"
```

`base-refresh` 必须记录非空验证说明，不能用空说明掩盖未经回读的并发变化。若项目已被当前工作之外的进程修改，普通 `base-refresh` 必须失败；只有逐项回读并验证变化后，才能额外提供 `--accept-external-change --validation-report <项目外 JSON>`。报告字段和哈希绑定见 [schemas-and-cli.md](schemas-and-cli.md#外部变更验证报告)。即使写入、验证或后续命令失败，也要尝试释放自己持有的租约；不能释放其他工作的租约。租约过期只能允许另一工作重新取得写入权，状态哈希检查仍然不能跳过。

## 项目登记与恢复

已有项目迁入工作区后，保持其内部相对路径不变，再登记：

```powershell
python -X utf8 .\scripts\novel_workspace.py project-register "<workspace-root>" "<workspace-root>\projects\<project-id>" --project-id "<project-id>"
```

项目必须是 `projects/` 的直接子目录并包含有效 `novel.json`。工作目录必须是 `workspaces/` 的直接子目录。脚本拒绝磁盘根、用户主目录、路径穿越、重复 ID 和同一路径的冲突登记。

迁移、修复或清理前先验证项目并建立可恢复副本。不要自动删除未关闭工作、未合并草稿、旧项目或 Git 历史。
