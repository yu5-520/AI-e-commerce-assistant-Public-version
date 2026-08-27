# V25 Unified RAG — Phase 2 (V25.3–V25.5)

## 目标

把 Phase1 的“字段身份 + 知识分布域”真正变成检索顺序，而不是只作为目录：

```text
Field request
↓
V25.3 EXACT_FIELD
↓ insufficient
V25.4 ALIAS canonicalization + STRUCTURED_FILTER
↓ insufficient
V25.5 scoped VECTOR supplement
↓ relationship required + scoped vector stage complete
GRAPH supplement
↓
INSUFFICIENT_EVIDENCE
```

核心约束：**向量与图谱只能补充，不得成为第一层入口，也不得把“相似”提升成系统事实。**

## V25.3 字段直取

Java `V25RetrievalAuthority` 先使用 Phase1 的 `canonicalField + fieldHash + domains` 定位知识记录。命中后立即停止，不再调用语义层。

知识记录必须携带：

- `recordId`
- `canonicalField`
- `fieldHash`
- `domains`
- `sourceRef`
- `sourceHash`

缺少来源证明的记录不具备合法检索资格。

## V25.4 同义词 / 结构化检索

`rag-alias-registry-v25.json` 只做名称归一：

```text
点击率解释 → metric.ctr.interpretation
ROAS放量策略 → action.roas.scale.strategy
```

Alias 不创建字段，也不能指向未注册字段。

结构化过滤只允许已登记键：

`platform / category / lifecycleStage / actionFamily / riskLevel / experiencePolarity / brand / tenantScope`

未知键直接 BLOCK，不转成自然语言让模型猜。

## V25.5 向量与图谱补充

保留 V23 Hash-Routed RAG 的既有边界：

- 应用拥有 route authority。
- Vector candidate 必须带 exact-route proof。
- 禁止 global fallback / cross-tag widening。
- Graph 只能在 scoped vector stage 完成后运行。
- Graph 只允许登记的 edge type，并且不能扩张到请求字段 domains 闭包之外。
- `permission.* / state.* / schema.* / execution_lock.* / gate.* / runtime.* / deployment.*` 不得成为图谱目标。

Vector/Graph 的结果标记为 `SUPPLEMENTAL`，`mayCreateSystemFact=false`。

## 当前生产边界

Phase2 仍为 **SHADOW**：

- 不修改 Agent1/2/3 正式输入。
- 不切换生产 RAG writer。
- 不启用新的生产 retrieval cutover。
- 只建立 Java 检索顺序、候选准入和 CI 证据。

因此第二阶段验证的是“统一RAG以后必须怎样找”，而不是提前替换现有比赛链。

## Gate

CI 必须证明：

1. Exact 命中后 Vector 不运行。
2. Structured 命中后 Vector 不运行。
3. Alias 只能归一到注册字段。
4. Vector 只能在前两层不足后运行，并要求 route proof。
5. Graph 必须等待 scoped vector stage。
6. Graph 不得指向系统权限/状态等硬约束。
7. 无证据时输出 `INSUFFICIENT_EVIDENCE`，不得自动补完。
8. 当前生产 Agent/RAG writer 保持不变。
