# 来源登记与本地资料授权

在保存网页、榜单、合法试读、公版文本、用户提供的本地资料或准备原创性对照时读取本文件。平台采集同时读取 [platform-adapters.md](platform-adapters.md)，作品研究同时读取 [market-research.md](market-research.md)。

## 唯一登记表

`research/source-manifest.jsonl` 每行一个 JSON 对象，是来源权限和完整性的机器可读登记表。`research/source-index.md` 仍是给作者阅读的研究引用索引；两者用途不同，不能只写其中一个。

核心字段：

```json
{
  "schema_version": 1,
  "source_id": "SRC-...",
  "path": "sources/local/hash-reference.txt",
  "sha256": "...",
  "size_bytes": 1234,
  "media_type": "text/plain",
  "origin": "user_local",
  "source_kind": "authorized_full_text",
  "platform": null,
  "source_url": null,
  "observed_at": "2026-08-29T12:00:00+00:00",
  "rights_status": "user_authorized",
  "authorization_scope": "project_research_and_originality_audit",
  "authorization_reference": "作者在本项目中明确授权本机分析",
  "external_use": "local_only",
  "originality_compare": true
}
```

不要在登记表中保存密码、Cookie、API key、付费账号信息或不必要的本机绝对路径。

## 来源类型与权限

| 来源 | 可直接重读 | 必要条件 | 默认外部处理 |
|---|---|---|---|
| Skill 在本项目采集的公开资料 | 是 | 位于 `sources/`，保留 URL、观察时间和哈希 | `local_only`，除非另有明确许可 |
| 项目中已有且来源可追溯的资料 | 是 | 登记 rights、provenance 和使用范围 | 按登记值 |
| 用户合法提供的本地文本 | 否，先授权 | 每个项目有明确 `authorization_reference`，rights 不得为 unknown | `local_only` |
| 公版文本 | 是 | 记录版本、入口和公版判断依据 | 仍按最小必要原则 |
| 付费、登录后或私有全文 | 仅在合法提供且授权后 | 不绕过访问控制，不代替用户下载 | `local_only` 或 `prohibited` |
| 来源不明全文 | 否 | 无法确认权利与来源 | 不登记、不使用 |

`local_only` 表示只能由本机脚本和当前执行环境读取，不得上传外部 API、搜索服务、向量数据库或第三方模型。安装在本机不等于获得上传权限。

## 登记命令

项目内已有公开资料：

```powershell
python -X utf8 .\scripts\novel_research.py register "<project-root>" "<project-root>\sources\snapshot.html" --origin project_existing --source-kind public_page --rights-status public_web --external-use local_only --source-url "https://example.com/page" --observed-at "2026-08-29T12:00:00+00:00" --workspace "<workspace-root>" --work-id "<work-id>"
```

用户明确授权的本地文本：

```powershell
python -X utf8 .\scripts\novel_research.py register "<project-root>" "<authorized-reference-path>" --origin user_local --source-kind authorized_full_text --rights-status user_authorized --authorization-scope project_research_and_originality_audit --authorization-reference "作者于 2026-08-29 明确授权本项目本机分析" --external-use local_only --originality-compare --workspace "<workspace-root>" --work-id "<work-id>"
```

外部本地文件会以哈希前缀复制到项目 `sources/local/`，不会覆盖同名不同内容的文件。没有项目级授权、rights 为 unknown 或试图把 `local_only` 声称为外部处理许可时，脚本必须拒绝。

## 完整性检查

```powershell
python -X utf8 .\scripts\novel_research.py verify "<project-root>"
python -X utf8 .\scripts\novel_project.py validate "<project-root>"
```

验证至少检查：稳定 ID、项目内路径、文件存在、SHA-256、来源类型、权利状态、授权范围和本地资料授权引用。哈希变化表示来源内容已经变化，应重新登记或恢复原快照，不能直接修改旧哈希让验证通过。

## 最小使用原则

- 市场研究只保存支撑结论所需的页面和字段，不因“公开”而批量复制全文。
- 原创性审计只读取 `originality_compare: true` 的登记来源。
- 没有合法全文时只分析实际看到的简介、试读、访谈和评论，不声称完成全文拆解。
- 研究引用只摘录必要短句；正文创作不把来源原句放入提示上下文后改几个词使用。
- 用户撤销某项使用授权时，停止后续处理并标记受影响报告；删除或迁移文件属于独立的有风险操作，先确认范围和恢复方式。
