# 平台适配层

在依据平台公开数据选题、刷新榜单快照、判断目标平台发布边界或扩展新平台时读取本文件。市场判断仍按 [market-research.md](market-research.md) 执行；来源授权和登记按 [source-ingestion.md](source-ingestion.md) 执行。

## 架构边界

通用创作核心不依赖任何单一平台。平台适配器只负责：构造无需登录的公开请求、保存原始响应、把平台字段规范化、记录指标口径和访问限制。它不能替作者确认故事，也不能把平台热度直接提升为正典。

每个适配器至少公开：

- 稳定的 `adapter_id`、平台名称和已实现能力。
- 支持的频道、排序和请求参数，未知映射不得猜测。
- 原始数据、规范化数据、绝对观察时间和原始响应 SHA-256。
- 平台指标的原生定义或“定义未公开”说明。
- 登录、验证码、付费墙、反自动化和自定义字体等限制。

新适配器必须实现与 `scripts/novel_research.py` 中 `PlatformAdapter` 等价的 `request_url`、`normalize` 和 `capability` 接口，并使用离线夹具测试解析。没有稳定公开入口的平台保持“未实现”，不要创建看似可用的半成品。

## 当前实现

v2.0 完整实现 `fanqie`；起点、晋江等平台仍可把合法公开资料登记为来源，但不声称存在完整适配器。

```powershell
python -X utf8 .\scripts\novel_research.py adapters
python -X utf8 .\scripts\novel_research.py collect "<project-root>" --platform fanqie --channel all --sort hot --page-count 18 --workspace "<workspace-root>" --work-id "<work-id>"
python -X utf8 .\scripts\novel_research.py collect "<project-root>" --platform fanqie --channel male --sort hot --input "<saved-response.json>" --observed-at "2026-08-29T12:00:00+00:00" --workspace "<workspace-root>" --work-id "<work-id>"
```

番茄适配器只使用官方公开书库接口，支持 `all`、`male`、`female` 和已核验的 `hot` 排序。网络请求失败时只报告失败，不自动更换代理、伪装登录、绕过验证码或重试轰炸。

番茄书库部分文本可能使用站点私有字体编码。适配器保留原始值并设置 `text_requires_detail_verification`，不猜测乱码对应的书名、作者或数值；需要可读文本时回到无需登录的官方详情页逐项核验。

### 作品类型边界

- `serial_novel` 继续使用上述番茄公开书库采集、规范化与近期信号流程，行为和输出不变。
- `short_story` 当前只新增创作、审核、导出和发布资料准备，不声称已经实现番茄短故事热榜采集器。公开书库的长篇数据可以作为宽泛类型信号，但不能标成“短故事热门数据”。
- 番茄后台截图只能证明短故事存在独立入口或当前页面字段，不能据此推断字数、分类、签约、审核、收益或流量规则。正式交付前从当前官方页面逐项核验，并把证据登记为来源。
- 不把短故事项目改挂到 `fanqie_public` 发布配置。短故事项目使用 `fanqie_short_story_public`，长篇项目继续只保留原有 `fanqie_public` 配置。

## 保存与复用

一次采集产生两类文件：

- `*.raw.json`：原始响应，不改字段、不混入解释。
- `*.normalized.json`：频道顺位、书籍 ID、详情页入口、原生指标、字体警告和口径边界。

两者都登记到 `research/source-manifest.jsonl`。Skill 自己采集并保存在项目 `sources/` 的文件可以在后续会话直接重读；重读前验证文件哈希，不能因为文件名相同就忽略内容变化。

`research/platform.json` 记录主平台、已启用适配器、最近观察时间和发布配置。`author_draft` 与平台发布配置不同：作者允许出现的素材不等于平台当前允许公开发布的内容。长篇沿用 `fanqie_public`；只有显式 `short_story` 项目新增 `fanqie_short_story_public`，旧长篇配置不迁移也不写回。

仓库内部名称映射如下：`fanqie_public` 是长篇项目配置，`fanqie_short_story_public` 是短故事项目配置；`fanqie-serial` 和 `fanqie-short-story` 是导出/上传质量 profile，不是项目类型字段。它们只描述本仓库的配置关系，不推断平台最新规则。

## 时效性

榜单、指标、规则和治理公告都会变化。面向番茄的当前决策优先刷新最近 3 个月信号，样本不足再扩展到 6 个月；每次刷新使用绝对日期。旧快照可用于趋势或复核当时结论，不能冒充当前榜单。

正式发布前重新核验目标平台最新官方规则，并把规则页面登记为来源；`rules_last_verified_at` 为空或过期时，不声称发布版已经符合最新规则。

## 失败与停止条件

- 返回登录、验证码、占位响应或反自动化页面：保存访问限制记录后停止。
- 响应结构变化：保留原始响应，解析失败，不静默丢字段。
- 指标定义不明：按原生字符串保存，降低结论强度。
- 公开入口消失：将适配器标为需要维护，不用搜索摘要模拟官方数据。
- 需要新凭据、付费访问、安装浏览器或绕过限制：先请求用户授权；没有授权就不执行。
