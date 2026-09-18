# novel-studio

`novel-studio` 是一个只使用 Python 标准库的中文长篇小说与完整短故事工作流引擎。它把草稿、研究、正典、连续性、原创性、自然化审阅和导出交付分开保存，并用项目状态哈希与单写者租约阻止并发覆盖。

[English](README.md)

## 核心概念

六个术语覆盖整个系统：

- **工作区（workspace）**：运营根目录，持有 SQLite 注册表、每个任务一个的隔离工作目录和租约记账。一个工作区可以承载多个项目。
- **项目（project）**：正典手稿——故事圣经、大纲、章节、连续性事实、长期记忆、审稿产物。它是"数据库"；SQLite 只是可重建的检索缓存。
- **工作（work）**：项目外的隔离草稿目录（草稿、研究、报告），每次正式写入都绑定一个工作身份。
- **租约（lease）**：单写者锁。正式写入必须持有绑定工作区与工作身份的有效租约；过期租约只能通过有审计记录的 `lock-break` 回收。
- **暂存（staging）**：项目 `staging/` 下组装的待提交包。不计入正典状态哈希，但同样受租约门禁约束。
- **门禁（gates）**：连续性基线、周期质量审核和原创性审计，各自绑定内容哈希。正文、上下文或状态一旦改变，旧报告全部失效；不能靠改报告元数据复活过期批准。

## 安装

需要 Python 3.10 或更高版本。不需要 `pip install`，脚本只依赖标准库；`requirements.txt` 仅用于明确记录这一点。

```powershell
git clone https://github.com/xinghe-labs/novel-studio.git
cd novel-studio

python -X utf8 .\scripts\novel_workspace.py --version
python -X utf8 .\scripts\novel_workspace.py doctor
```

Windows PowerShell 建议统一使用 `python -X utf8`（脚本自身也会把 stdout/stderr 重配置为 UTF-8，并把参数错误和异常输出为 JSON）。

`doctor` 完全只读。省略工作区时检查 Python、SQLite、脚本导入、stdout 编码和 `humanizer-zh`；传入工作区后还会用只读 SQLite 连接检查目录、schema、注册表表名和必需列。

**`humanizer-zh` 依赖。** 长篇每章和短故事全文的正式提交都必须实际调用配套的 `humanizer-zh` Skill。查找顺序：`NOVEL_HUMANIZER_PATH` 环境变量（可指向目录或文件）、Skill 根目录相邻的 `humanizer-zh/`、`%USERPROFILE%\.agents\skills\humanizer-zh`、`%USERPROFILE%\.codex\skills\humanizer-zh`。目标必须含可读的 `SKILL.md`，且 frontmatter 声明 `name: humanizer-zh`。无法解析时 `doctor` 报告 `status: blocked`，正式工作不得继续——只能保留工作目录草稿，不能用人工声明、导出时检查或 `--force` 绕过。CI 用仓库内 stub（`ci/humanizer-zh-stub`，经 `NOVEL_HUMANIZER_PATH` 选用）满足该契约；stub 不做任何改写，也不用于真实稿件。

## 两种使用方式

### 作为 Agent Skill（推荐）

本引擎设计为由 AI 编码代理驱动。把它安装到代理宿主的技能目录（如 `%USERPROFILE%\.agents\skills\novel-studio` 或 `%USERPROFILE%\.codex\skills\novel-studio`），或直接让代理指向本仓库的克隆。[`SKILL.md`](SKILL.md) 是代理入口：任务到参考文档的路由表加不可绕过的边界。代理按任务只加载所需契约，路由表见 SKILL.md 的「按任务加载参考」一节；提交协议、租约模型、哈希门禁语义与 fail-closed 默认值的推理见 [DESIGN.md](DESIGN.md)。

### 直接驱动 CLI

所有工具既能被代理调用，也能人工调用，契约一致。只读查询不需要工作上下文和租约：

```powershell
python -X utf8 .\scripts\novel_workspace.py status "<workspace-root>"
python -X utf8 .\scripts\novel_project.py status "<project-root>"
python -X utf8 .\scripts\novel_project.py validate "<project-root>"
python -X utf8 .\scripts\novel_continuity.py status "<project-root>"
python -X utf8 .\scripts\novel_review.py status "<project-root>"
```

## 最小写入流程

0. 只读体检（可选但建议）：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py doctor
   ```

1. 一次性初始化：创建工作区，再在其中创建并注册项目。命令会输出项目 ID，正典目录落在 `<workspace-root>\projects\<project-id>`：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py init "<workspace-root>"
   python -X utf8 .\scripts\novel_workspace.py project-create "<workspace-root>" --title "书名" --work-type serial_novel --genre "都市脑洞"
   ```

   `--work-type` 可选 `serial_novel`（长篇连载）或 `short_story`（完整短故事）；短故事可加 `--short-story-slug`（一至三个有语义的英文单词）生成 `shortstory-<slug>-YYYYMMDD` 项目 ID。

2. 创建或复用隔离工作目录，绑定项目并取得租约：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>" --client "generic"
   python -X utf8 .\scripts\novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<project-id>"
   python -X utf8 .\scripts\novel_workspace.py lock-acquire "<workspace-root>" "<work-id>"
   python -X utf8 .\scripts\novel_workspace.py write-check "<workspace-root>" "<work-id>"
   ```

   框架确认时先按 `references/controlled-automation.md` 把框架、作者决策、全书摘要和受控项目设置放在项目外的工作目录，再用 `novel_project.py framework-sync` 在同一租约事务中同步；不要直接改项目正典后再运行 `framework-state`。

3. 正文、研究和中间报告先写当前 `work_root`。`prepare-context` 的输出必须位于工作目录或其他项目外路径；完成租约和 `write-check` 后，才复制到项目 `staging/chapters/<package>/`。暂存包不计入正典状态哈希。

4. 提交时必须显式提供同一工作区和工作身份：

   ```powershell
   python -X utf8 .\scripts\novel_project.py commit-chapter "<project-root>" "staging/chapters/<package>" --workspace "<workspace-root>" --work-id "<work-id>"
   ```

   提交器会在检查开始和事务写入前各验证一次租约、项目归属和基准哈希。缺少参数、租约过期、心跳失效或项目变化都会 fail closed。

5. 项目验证通过后刷新基准并释放租约：

   ```powershell
   python -X utf8 .\scripts\novel_project.py validate "<project-root>"
   python -X utf8 .\scripts\novel_workspace.py base-refresh "<workspace-root>" "<work-id>" --validation-reference "已完成项目与长期记忆复核"
   python -X utf8 .\scripts\novel_workspace.py lock-release "<workspace-root>" "<work-id>"
   ```

长时间运行的写作任务应在租约有效期内定期续租：

```powershell
python -X utf8 .\scripts\novel_workspace.py lock-renew "<workspace-root>" "<work-id>"
```

`lock-break` 只接受明确的项目 ID、预期 owner 和非空原因，并且只能回收已过期或心跳失效的租约：

```powershell
python -X utf8 .\scripts\novel_workspace.py lock-break "<workspace-root>" "<project-id>" --expected-owner "<work-id>" --reason "原进程已退出，已核对没有活动写入者"
```

每次回收都会写入注册表的 `lease_events` 审计表，包含原 owner、过期时间、原因和回收时的项目哈希。有效租约不能被强制释放。

## 命令地图

九个工具的子命令一览（`novel_cli.py` 是共享运行时契约，不提供子命令）：

| 工具 | 子命令 |
|---|---|
| `novel_workspace.py` | `doctor` `init` `status` `project-register` `project-create` `project-list` `work-start` `work-ensure` `work-bind` `work-resume` `work-reconcile` `work-list` `work-close` `lock-acquire` `lock-renew` `write-check` `base-refresh` `lock-release` `lock-break` |
| `novel_project.py` | `init` `upgrade` `validate` `status` `research-state` `framework-state` `framework-sync` `commit-chapter` |
| `novel_continuity.py` | `install` `status` `prepare-context` `prepare-audit` `bind-audit` `check-package` `prepare-baseline` `record-baseline` `impact` `invalidate` |
| `novel_review.py` | `status` `prepare` `record` `configure` |
| `novel_originality.py` | `audit` |
| `novel_research.py` | `adapters` `collect` `register` `verify` |
| `novel_memory.py` | `rebuild` `update` `status` `search` |
| `novel_export.py` | `export` `status` |

## 输出契约与退出码

每个工具向 stdout 输出单个完整 JSON 文档，stderr 保持为空，退出码语义固定：

| 退出码 | 含义 |
|---|---|
| `0` | 命令成功完成 |
| `1` | 业务门禁、验证、审计或当前状态未通过；JSON 通常仍是可解析的业务结果 |
| `2` | 参数、领域或已知操作错误 |
| `3` | 未预期异常，或结果无法序列化 |

门禁未通过会阻断下游工作，不会降级为警告。`--help` 是唯一人类可读路径；机器可读对象的字段形状集中在 [`references/schemas-and-cli.md`](references/schemas-and-cli.md) 维护。

## 版本与 schema

工具版本在 `scripts/novel_cli.py` 的 `TOOL_VERSION` 中维护。当前版本是 `2.2.0`。工作区 JSON 的 `schema_version` 仍为 `1`；本版本对旧注册表采用只增不删的 SQLite 迁移，为租约补充 `lease_seconds`、`heartbeat_enforced` 和 `lease_events`。只读 `doctor` 不迁移 legacy registry，需由可写命令完成迁移后再检查；打开注册表不会改写小说正典。

升级前先复制工作区并运行：

```powershell
python -X utf8 .\scripts\novel_workspace.py doctor "<workspace-root>"
python -X utf8 .\scripts\novel_workspace.py status "<workspace-root>"
```

若将来出现不支持的 `schema_version`，不要手工修改 JSON；先阅读 `CHANGELOG.md` 中对应版本的迁移说明，再使用该版本提供的迁移命令。未知版本会保持硬失败。

## 受控分发

Skill 的发布物只包含版本控制中已经提交的受控文件。工作区运行时产生的 `.agent-handoff/`、根目录 `AGENTS.md`、`__pycache__/` 和 `.pytest_cache/` 都不是 Skill 内容；它们即使存在于本机目录，也不得复制进分发包。发布前先检查 `git status --short` 和 `git ls-files --others --exclude-standard`，确认没有把本地状态或代理指令误当成 Skill 文件。

`continuity-eval/`（连续性评测基准）与 Skill 同仓维护、由 CI 测试，但它不是 Skill 运行时内容，已通过 `.gitattributes` 的 `export-ignore` 排除在归档之外：Skill 用户拿到的是引擎与门禁，评测 harness 留在源码仓库里供开发与复现研究使用。该基准包含确定性种子矛盾注入器、零 LLM 矛盾检测器和 dev/held-out 阈值选择协议；只读、只做建议性度量，不写入任何正典项目，也不接入提交门禁。

发布某个已提交版本时，从 Skill 根目录使用 Git 归档，而不是直接把整个目录压缩：

```powershell
git archive --format=zip --output="novel-studio-2.2.0.zip" HEAD
```

`git archive` 只读取提交中的受控路径，不会带入未跟踪的交接状态、缓存或本机临时文件。若要发布标签或其他已核对的提交，把 `HEAD` 替换为该提交引用，并在归档后重新列出压缩包内容做一次只读检查。

每个通过验证的完成节点保存为一个本地 Git commit；会改变工具能力、契约或兼容性的节点同时更新 `TOOL_VERSION`、本文件和 `CHANGELOG.md`，并创建 `v<version>` 本地标签。远端推送、发布和归档分发仍需单独授权。

## 导出视觉检查

自动校验不能代替逐页阅读。生成 DOCX 后，若系统有 LibreOffice，可在临时目录渲染为 PDF：

```powershell
soffice --headless --convert-to pdf --outdir "<temporary-output>" "<project-root>\exports\《书名》-审阅稿.docx"
```

再用可用的 PDF 阅读器或渲染工具逐页检查缺字、重叠、裁切、异常分页和页码。`soffice` 不存在或转换失败时，交付状态应记为未完成，不能把 DOCX 结构通过当成视觉通过。

EPUB 的本地校验还会检查 `container.xml`、OPF manifest/spine、导航、NCX 和所有章节 XHTML 的引用闭包；有 EPUBCheck 时再运行标准校验。
