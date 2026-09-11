# 工作区与并行隔离

每次使用本 Skill 都先读取本文件并执行目录预检。这里的“工作”不绑定 Codex 会话：它可以来自任何 Agent、脚本、窗口、进程或人工操作。工作身份由 `work_id` 和本地 `work.json` 决定。

## 两层目录

默认工作区由用户指定；本机约定为 `<workspace-root>`：

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

## 调用前强制预检

在提问、搜索、策划、审稿或写作之前，先运行：

```powershell
python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>" --client "generic"
```

如果调用方已保存 `work_id`：

```powershell
python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>" --work-id "<work-id>"
```

如果当前工作目录或父目录已经有有效 `work.json`，`work-ensure` 返回 `reused`；没有时才创建新的 `work-*` 目录。不得在同一次工作中重复创建目录，也不得用“最近一个活跃工作”猜测上下文。

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

任一条件不满足就停止写入。项目已经被其他工作修改时，先重新读取变化、处理冲突并重新验证；不能静默覆盖，也不能只修改 `work.json` 绕过检查。

`exports/` 是唯一例外：`novel_export.py` 只读通过验证的正典，先在临时目录构建派生包，并在发布前复查源快照，因此生成或刷新导出文件不要求取得正典写入租约。导出文件不计入 `base_state_hash`，多个导出任务不能据此修改正文；发现源快照变化或人工改过的派生文件时导出器必须停止。

完成一次已授权写入后，先运行项目级验证，再更新基准并释放租约：

```powershell
python -X utf8 .\scripts\novel_project.py validate "<project-root>"
python -X utf8 .\scripts\novel_memory.py status "<project-root>"
python -X utf8 .\scripts\novel_workspace.py base-refresh "<workspace-root>" "<work-id>" --validation-reference "项目与长期记忆验证通过"
python -X utf8 .\scripts\novel_workspace.py lock-release "<workspace-root>" "<work-id>"
```

`base-refresh` 必须记录非空验证说明，不能用空说明掩盖未经回读的并发变化。即使写入、验证或后续命令失败，也要尝试释放自己持有的租约；不能释放其他工作的租约。租约过期只能允许另一工作重新取得写入权，状态哈希检查仍然不能跳过。

## 项目登记与恢复

已有项目迁入工作区后，保持其内部相对路径不变，再登记：

```powershell
python -X utf8 .\scripts\novel_workspace.py project-register "<workspace-root>" "<projects-root>\<project-id>" --project-id "<project-id>"
```

项目必须是 `projects/` 的直接子目录并包含有效 `novel.json`。工作目录必须是 `workspaces/` 的直接子目录。脚本拒绝磁盘根、用户主目录、路径穿越、重复 ID 和同一路径的冲突登记。

迁移、修复或清理前先验证项目并建立可恢复副本。不要自动删除未关闭工作、未合并草稿、旧项目或 Git 历史。
