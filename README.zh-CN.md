# novel-studio

`novel-studio` 是一个只使用 Python 标准库的中文长篇小说与完整短故事工作流 Skill。它把草稿、研究、正典、连续性、原创性、自然化审阅和导出交付分开保存，并用项目状态哈希与单写者租约阻止并发覆盖。

## 环境

- Python 3.10 或更高版本。
- 不需要 `pip install`；脚本只依赖 Python 标准库。
- `requirements.txt` 仅用于明确记录这一点，不包含第三方依赖。
- Windows PowerShell 建议使用 `python -X utf8`。脚本本身也会把 stdout/stderr 重新配置为 UTF-8，并把参数错误和异常输出为 JSON。
- 长篇每章和短故事全文正式提交前都必须实际调用 `humanizer-zh`。该 Skill 不可用时只能保留工作目录草稿，不能用人工声明、导出时检查或 `--force` 绕过。

查看工具版本和运行环境：

```powershell
python -X utf8 .\scripts\novel_workspace.py --version
python -X utf8 .\scripts\novel_workspace.py doctor
python -X utf8 .\scripts\novel_workspace.py doctor "<workspace-root>"
```

`doctor` 完全只读。省略工作区时只检查 Python、SQLite、脚本导入、stdout 编码和 `humanizer-zh`；传入工作区后还会用只读 SQLite 连接检查目录、schema、注册表表名和必需列。`humanizer-zh` 必须能在当前 skill 根目录的相邻安装、`%USERPROFILE%\.agents\skills` 或 `%USERPROFILE%\.codex\skills` 中找到可读的 `SKILL.md`，且其 frontmatter 要声明 `name: humanizer-zh`；也可以用 `NOVEL_HUMANIZER_PATH` 指向该目录或文件。`status: blocked` 表示正式工作不应继续。

## 最小写入流程

1. 初始化或复用工作区：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py work-ensure "<workspace-root>" --client "generic"
   ```

2. 绑定已有项目并取得租约：

   ```powershell
   python -X utf8 .\scripts\novel_workspace.py work-bind "<workspace-root>" "<work-id>" --project-id "<project-id>"
   python -X utf8 .\scripts\novel_workspace.py lock-acquire "<workspace-root>" "<work-id>"
   python -X utf8 .\scripts\novel_workspace.py write-check "<workspace-root>" "<work-id>"
   ```

   框架确认时先按 `references/controlled-automation.md` 把框架、作者决策、
   全书摘要和受控项目设置放在项目外的工作目录，再用
   `novel_project.py framework-sync` 在同一租约事务中同步；不要直接改项目正典后
   再运行 `framework-state`。

3. 在隔离工作目录完成草稿、自然化、连续性和原创性审阅。暂存包可以位于项目的 `staging/`，该目录不计入正典状态哈希。

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

`continuity-eval/`（连续性评测基准）与 Skill 同仓维护、由 CI 测试，但它不是 Skill 运行时内容，已通过 `.gitattributes` 的 `export-ignore` 排除在归档之外：Skill 用户拿到的是引擎与门禁，评测 harness 留在源码仓库里供开发与复现研究使用。

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
