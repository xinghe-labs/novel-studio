# 连载与短故事导出

用户要求合并稿、审阅稿、电子书、平台分章包、番茄短故事单文件或“最终成品”时读取本文件，并同时读取 [platform-delivery-quality.md](platform-delivery-quality.md)。导出是从已提交正文生成的可重建读模型，不是新的正文来源。短故事同时读取 [short-story-mode.md](short-story-mode.md)。

## 唯一主稿

- `serial_novel` 的 `manuscript/chapters/NNNN-title.md` 是分章主稿；`short_story` 只允许 `manuscript/chapters/0001-故事名.md` 一个完整正文主稿。
- `manuscript/index.md` 决定导出章节、顺序、标题和正文路径；草稿区、暂存包、SQLite 摘要和 `exports/` 中的文件不能进入导出输入。
- 修改 TXT、DOCX、EPUB 或番茄分章 TXT 不会回写 Markdown。需要改正文时先改正典章节并完成受控提交，再重新导出。
- `exports/` 是项目根目录下的派生目录，不计入项目正典状态哈希，可以删除并从主稿重建。
- 导出前必须同时通过连续性硬门禁和当前质量审核。`--force` 只能处理已确认可覆盖的派生文件，不能绕过正典哈希、开放失效项、全局连续性审核或质量审核。

## 默认产物

```text
<project-root>/exports/
|-- export-manifest.json
|-- 《书名》-全书合并稿.txt
|-- 《书名》-审阅稿.docx
|-- 《书名》.epub
`-- fanqie/
    |-- 0001-第一章标题.txt
    |-- 0002-第二章标题.txt
    `-- ...
```

- 合并 TXT：UTF-8 无 BOM，包含书名、按索引排序的章标题和正文。
- 审阅 DOCX：A4 中文审阅版式，含封面、静态目录、每章分页、正文首行缩进和页码；它用于审阅，不是回写源。
- EPUB：EPUB 3 包，另含兼容旧阅读器的 NCX 目录；每章一个 XHTML 文件。
- 番茄分章包：UTF-8 无 BOM，每个文件只含章节正文，章号与标题由文件名承载，便于分别填写平台的章节标题和正文输入框。实际发布前仍需核对当日番茄官方规则，导出脚本不执行登录或上传。
- 番茄正文中的 Markdown 分场线（如 `---`、`***`、`* * *`、`___`）统一派生为一个空行，不输出可见星号；只匹配独占一行的分场线，正文行内的 `* * *` 保持原文。该规则同时适用于长篇 `fanqie/` 和短故事 `fanqie-short-story/`，不改变合并 TXT、DOCX 或 EPUB 中原有的可见分场样式。
- `export-manifest.json`：记录源章节哈希、源快照哈希、编码、换行、Unicode 规范化、平台配置、质量检查、实际规范化计数、每个派生文件的 SHA-256 和字节数，用于判断新鲜度、防止误覆盖并证明上传包通过本地质量闸门。

短故事的默认产物是：

```text
<project-root>/exports/
|-- export-manifest.json
|-- 《故事名》-短故事定稿.txt
|-- 《故事名》-短故事审阅稿.docx
|-- 《故事名》.epub
`-- fanqie-short-story/
    `-- 《故事名》.txt
```

短故事 TXT 与 DOCX 不显示技术性的“第 1 章”。番茄短故事文件只含清理后的正文，不含 Markdown、文件名标题或章号；平台上的标题、简介、分类、标签、封面和 AI 使用说明仍需在独立字段填写。清单记录 `work_type: short_story` 和 `fanqie_publication_profile: fanqie_short_story`。

正式导出有两道内容门禁：`novel_continuity.py status` 必须为 `current`，且 `novel_review.py status` 不能有到期质量审核。连载每 5 章分别完成全局连续性和周期质量审核；短故事提交唯一全文后分别完成全篇连续性基线和质量完稿审核。任一报告缺失、未通过、哈希过期或存在开放修订失效项时，导出器停止。

## 命令

默认一次生成全部格式：

```powershell
python -X utf8 .\scripts\novel_export.py export "<project-root>"
```

只生成需要的格式；`--format` 可以重复：

```powershell
python -X utf8 .\scripts\novel_export.py export "<project-root>" --format txt
python -X utf8 .\scripts\novel_export.py export "<project-root>" --format docx --format epub
python -X utf8 .\scripts\novel_export.py export "<project-root>" --format fanqie
```

检查导出是否仍与主稿一致：

```powershell
python -X utf8 .\scripts\novel_export.py status "<project-root>"
```

`blocked/fresh/stale/modified/missing` 的唯一状态定义和质量字段语义见 [platform-delivery-quality.md](platform-delivery-quality.md) 的“清单与状态”。本文件不另行维护镜像定义。

## 写入与覆盖边界

导出器先验证项目和源文本 Unicode，再完整构建临时包，对文本字节和 DOCX/EPUB 内部结构做二次验证，并在正式替换前复查源快照。生成期间主稿发生变化或任一质量检查失败时停止，不发布混合版本，也不覆盖已有有效派生包。

正常重新导出只覆盖清单中哈希未变化的派生文件。发现人工改过的派生文件或同名未登记文件时默认停止；只有用户确认不需要保留这些派生修改后才使用 `--force`。`--force` 不能改动 `exports/` 以外的路径。

同一源快照下按格式导出时，仍然新鲜的其他格式会保留。源快照已经变化时，只保留本次重新生成的格式，并清理清单中仍保持旧哈希的过时派生文件，避免新旧版本混在一个交付目录中。

## 交付检查

1. 依次运行 `novel_continuity.py status <project-root>`、`novel_review.py status <project-root>` 和 `novel_project.py validate <project-root>`。要求连续性为 `current`、质量审核未到期且项目无错误；任何 `baseline_required`、`review_due`、`stale`、开放失效项或非通过审核都不导出。源文本质量检查统一按 [platform-delivery-quality.md](platform-delivery-quality.md) 处理。
2. 运行导出命令并确认连载返回章节数与 `novel.json.current_chapter` 一致；短故事必须返回 `work_type: short_story` 且正文单元数为 1。
3. 运行 `novel_export.py status <project-root>`，按 [platform-delivery-quality.md](platform-delivery-quality.md) 的单一状态定义确认本次目标可交付；旧清单不能沿用新版质量证明。
4. DOCX 交付前用可用的 Word/LibreOffice 渲染器转成逐页图片，检查全部页面是否有缺字、重叠、裁切、异常分页或页码问题。
5. EPUB 至少检查 ZIP、mimetype、OPF、导航、NCX 和全部章节 XHTML；有 EPUBCheck 时再运行标准校验。
6. TXT、番茄文件和短故事单文件的编码、Unicode、库存、标题映射与正典派生字节复检，统一采用 [platform-delivery-quality.md](platform-delivery-quality.md) 的纯文本包规则。

导出完成不等于授权发布。登录平台、上传作品、填写作者信息、选择签约设置或实际发布仍需用户明确授权。

用户明确要求在番茄网页上传、定时发布或修订已经上传的章节时，再读取 [fanqie-live-publishing.md](fanqie-live-publishing.md)。线上操作必须以本文件生成并验证为 `fresh` 的番茄包为输入；平台副本中的格式修复不能反向成为 Markdown 主稿。
