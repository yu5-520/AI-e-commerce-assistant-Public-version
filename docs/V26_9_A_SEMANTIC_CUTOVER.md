# V26.9.A 业务语义主链迁移

状态：实现中，候选合同尚未激活；不能标记 A 完成或生产生效。

## 确定的业务边界

唯一合同登记于既有 config/v26_field_authority_contract.json 的 businessSemanticContract。Agent1 写 JudgementNode / DecisionActionNode；Agent2 为已准入动作写 PlanActionNode，不能换动作或改写系统基线；Agent3 为方案写 OperationStage，停止条件引用方案 guard。判断置信度和优先级不同于预算/目标等 PLAN 数字。

六类旧字段 primaryProblemNode、primaryAction、primaryExecutionTarget、primaryOwner、lockedActionFamily、executionLock 不得参与 Agent2 输入决策、Agent3 约束、任务准入、缓存身份、修订范围及 RAG 路由。并发锁与执行权限保持系统控制。旧 UI 如需兼容字段，只能从新图单向投影。

## 本工作分支已实现

- 候选唯一合同：精确节点字段所有权、关系类型、前置依赖方向、图规模、动作族到业务域映射。
- 三图规范化与哈希编译：拒绝未知字段、无效证据、非有限数、重复节点、循环依赖；PLAN 基线核对系统事实值及单位，预期区间与增量一致性检查。
- 动作准入：使用系统提供的允许动作集合，冲突双方暂缓、依赖未满足传播暂缓，不让 Agent2 替换候选。
- 系统分区合并：按注册业务域分区；校验分区身份、完整覆盖、重复/遗漏；跨域依赖由 DecisionGraph 确定性投影。
- 语义身份：图谱/事实、职责知识 Head、合同、业务主体、模型配置、检索策略；图谱输入投影不读取旧字段，也无缺图回退。
- 候选任务映射：三图哈希链与动作/步骤完整覆盖检查。

这些是编译与验收能力，不是第二套 Agent 执行器；原 runner 尚未替换。注册表 implementationPaths 登记依赖不代表运行入口已经切换。

## A 完成前必须继续实施

| 消费者 | 现有入口 | 必须完成的迁移 |
|---|---|---|
| Agent1 | real_product_judgment_agent_v2259_service / v26_node_edge_lineage_service | 用实际 provider 结构化输出编译 DecisionGraph，移除旧 primary 合同对输出的约束，保留精确执行身份核对 |
| Agent2 输入与调用 | agent_input_transport_v225_service / agent_input_contract_v225_service / agent_token_runtime_v22520_service | 按准入与分区投递新图，替换 familyPayload 缓存、重绑定和归一化路径 |
| Agent2 方案 | agent2_action_draft_core_v225_service | 取消单一动作族锁，接 PlanAction；实现跨分区总预算、资源和权限校验 |
| Agent3 | agent3_sop_core_v225_service / agent3_runtime_v23215_service / agent3_system_constraint_* | 替换旧输入/约束/缓存为 PlanGraph 与 planActionRefs，绑定新型执行 proof |
| 任务准入与详情 | pipeline_task_mapping_v225_service / task_pool_admission_* / v2177_agent2_single_action_contract_service | 多动作任务及节点状态、图合同准入、单向 UI 投影 |
| RAG 路由与缓存 | agent_hash_routed_rag_bridge_v1_service / v25_agent_input_ingress_service | 依据图节点分类和实际知识域 Head，清退任务级旧锁 |
| 修订与 SOP 证据 | Java ReviewContract/LocalSubgraphRevision / v26_revision_acceptance_service / v26_sop_evidence_service | 改用 DecisionAction/PlanAction 引用，并继承已有保留节点/边校验 |

还需完善候选合同：参数单位与权限预算、跨域资源总量、分区失败/重试预算、字段化验收条件，以及准入回执绑定既有授权来源。哈希一致性不能代替授权验证。

## 验收及发布边界

使用原注册表→血缘→精确包→门禁流程。候选编译器专项通过不等于实际 Agent 链路通过。必须补齐实际 provider 输入输出、旧字段扰动不变性、缺图拒绝、跨域执行完整性、终态重放不新增调用和固定三报表端到端验证。

本地 Python 3.12 的回归结果：134 passed / 6 skipped / 1 failed；失败为发布身份要求 Python 3.11.9，未修改该门禁。新编译器的 7 项专项测试通过。应在精确 3.11.9 环境重验，再评估合并。当前保持 draft，不部署、不宣称全消费者迁移完成。
