# V25.0-V25.2：统一 RAG 知识运行系统第一阶段

## 目标

第一阶段只建立知识身份和寻址基础，不切换生产 Agent 输入，不启用新的向量检索，不改变现有 RAG 写入权。

- **V25.0 知识盘点**：把三个 Agent 当前固定知识、经验 RAG、公司上下文和系统硬约束分开登记。
- **V25.1 统一 RAG 字段注册表**：Agent 以后声明“需要哪些知识字段”，而不是拥有自己的物理 RAG。
- **V25.2 知识分布域**：知识字段先解析到一个或多个分布域，后续检索只能在这些域内继续执行。

## 核心原则

```text
知识 ≠ 系统规则

统一 RAG = 一个物理知识库 + 多个逻辑知识分布域

Agent
↓
知识需求字段
↓
统一 RAG 字段注册表
↓
知识分布域
↓
后续才允许字段直取 / 结构化筛选 / 向量 / 图谱
```

第一阶段明确禁止把以下确定性规则迁入 RAG：

- 权限与审批硬门槛
- 状态机和 Gate
- Input / Output Schema
- Execution Lock
- 动作族不可变边界
- Generation / Runtime / Deployment Authority

## 当前知识盘点

现有链路不是“完全没有 RAG”：

1. Agent1 仍使用固定 `operating_policy_context`，其中 `principles` / `familyGuidance` 属于未来知识迁移候选；权限和 RAG 边界仍属于系统规则。
2. Agent2 已读取 `rag_experience_cards` 中真实、已审核、有效的历史经验，并已有 Hash Route 桥。
3. Agent3 的 company context 同时包含固定默认值、ENV 配置和历史经验投影。
4. `hash_routed_rag_service.py` 已有 Hash Route / exact route / vector / graph 的路由契约，但不是知识存储层。

## 第一批统一 RAG 字段

第一阶段登记 18 个知识字段，覆盖：

- Agent1：经营诊断、指标关系、平台/类目背景
- Agent2：标题主图、ROAS、活动、转化策略、正负经验
- Agent3：企业 SOP、任务时限、品牌表达、历史 SOP 案例

字段身份采用：

```text
fieldHash = SHA256(
  schema
  + fieldId
  + canonicalField
  + valueType
  + sorted(distributionDomains)
)
```

## 第一批知识分布域

建立 11 个逻辑分布域：

- 经营诊断域
- 指标关系域
- 平台运营域
- 类目运营域
- 创意运营域
- 付费运营域
- 活动运营域
- 转化优化域
- 企业 SOP 域
- 品牌表达域
- 执行经验域

一个字段允许分布在多个域，例如“跨指标诊断模式”同时属于经营诊断域和指标关系域。

## 验证边界

CI 必须证明：

- 现有固定知识与 RAG 骨架的基线源文件仍存在且 Hash 可追溯。
- 统一字段注册表中的每个 `fieldHash` 可独立复算。
- 每个字段引用的分布域必须存在，未知字段/未知域默认 BLOCK。
- 系统硬约束字段不能进入 RAG 字段注册表。
- Agent1 / Agent2 / Agent3 的代表知识字段能解析到正确分布域。
- 当前生产 Agent 输入、RAG 写入权和向量检索路径保持不变。

第一阶段是 **SHADOW Authority**。正式 Knowledge Store、字段直取、向量升级、知识组合表和回流在后续阶段接管。
