# 双层原创性审计

在候选作品深度拆解后形成原创方向、框架确认前、正式章节提交前或用户担心相似度时读取本文件。来源必须先按 [source-ingestion.md](source-ingestion.md) 登记；机制融合原则仍以 [market-research.md](market-research.md) 为准。

## 不使用统一原创百分比

原创性不是一个可以由字符重合率完整表示的单值。审计分成两个独立层级，并报告具体证据、限制和处置动作：

1. **措辞层**：检查较长原句和近似措辞重合，能定位候选文件、来源、片段和阈值。
2. **结构层**：检查一句话故事、人物关系、世界规则、前三个关键节点和核心反转是否过度映射同一来源。

措辞层通过不表示结构原创；结构层通过也不能证明不存在翻译、深度释义或未登记来源。最终判断需要 Agent 结合原文证据和作者声明复核。

## 原创性计划

`research/originality-plan.json` 是结构审计输入。候选结构的每个元素记录正文用途、影响来源和真正改变因果的改造：

```json
{
  "schema_version": 1,
  "status": "ready",
  "candidate": {
    "logline": {
      "text": "一句话故事",
      "influences": ["W-001"],
      "causal_transformation": "把能力奖励改为不可逆的关系代价"
    },
    "relationships": [],
    "world_rules": [],
    "first_three_nodes": [],
    "core_twist": {
      "text": "核心反转",
      "influences": [],
      "causal_transformation": ""
    }
  },
  "references": [
    {
      "source_id": "W-001",
      "work": "参考作品",
      "structures": {
        "logline": "只写已核验的结构摘要",
        "relationships": [],
        "world_rules": [],
        "first_three_nodes": [],
        "core_twist": ""
      }
    }
  ]
}
```

`first_three_nodes` 必须恰好写出三个节点。只改人名、时代、地点、职业或能力外观不能填写为 `causal_transformation`；改造必须改变选择、代价、知识边界、权力归属或事件因果。

## 运行审计

来源登记时只有明确标记 `originality_compare: true` 的文件会被默认读取：

```powershell
python -X utf8 .\scripts\novel_originality.py audit "<project-root>" --workspace "<workspace-root>" --work-id "<work-id>"
python -X utf8 .\scripts\novel_originality.py audit "<project-root>" --candidate "staging/chapters/0001/chapter.md" --workspace "<workspace-root>" --work-id "<work-id>"
```

报告默认先写入 `staging/originality/originality-audit-<timestamp>-<hash>.json`，记录候选和来源 SHA-256、计划哈希、两层发现及方法限制；成功执行 `commit-chapter` 时才以原始字节归档到 `reviews/`。整个命令只在本地处理；`local_only` 来源不会上传。

默认措辞阈值用于发现较长且有信息量的重合：规范化后 18 字开始复核，24 字以上或高近似度会阻断。阈值不是版权结论；常用成语、类型术语、法规原文、作品名和必要引用可能误报，必须查看上下文。为了让报告通过而缩短同一句、替换同义词或拆句，仍属于未解决风险。

## 结构阻断

以下情况视为单一来源支配并阻断：

- 同一来源映射三个或更多关键结构维度。
- 同一来源同时映射一句话故事和核心反转。
- 同一来源同时映射主要人物关系、前三节点和核心反转。
- 声明受某来源影响，却没有写清因果改造。
- 影响来源未在计划的 `references` 中登记。

同一来源只映射一个结构维度、且该元素已经诚实登记因果改造时，可以通过自动结构闸门；这代表来源分布未达到已知支配风险，不代表版权或语义原创性的绝对证明。同一来源映射两个尚未触发上述阻断组合的维度时返回 `review`，由 Agent 或作者结合具体因果链裁决；三个或更多维度及上述高风险组合仍直接 `block`。确定性结构摘要相似命中也保持 `review`，不能因声明了来源就自动放行。

确定性相似度只负责提示复核，不能独立完成中文语义判断。Agent 应回到对应参考机制和候选因果链，判断是否仍能一一对位。

## 处置状态

- `pass`：两层均无已知阻断，且结构计划完整；可以进入下一受控步骤。
- `review`：存在需要人工判断的措辞或结构提示；未裁决前不正式提交。
- `incomplete`：结构字段、来源声明或因果改造不完整；先补材料。
- `block`：发现高风险措辞或单一来源支配；必须重构，不能只改名。

阻断后的优先修法是改变主题问题和因果发动机，再调整权力关系、成功代价、知识边界、节点顺序与反转答案。重构后更新原创性计划，重新运行审计并保留前后报告。

正式章节提交只接受覆盖该章节 SHA-256、结构计划未变且 `decision: pass` 的报告。报告通过后若正文或原创性计划发生变化，旧报告失效。
