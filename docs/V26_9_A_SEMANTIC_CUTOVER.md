# V26.9.A 业务语义主链迁移

状态：同一 PR 中实施；businessSemanticContract 仍为 candidate_not_activated。
真实运行器的新协议路径已接通，但生产调度、权限准入及 Java 评审尚未全量切换，不能标记 A 完成或生产生效。

## 唯一业务合同

合同登记于既有 config/v26_field_authority_contract.json 的 businessSemanticContract，复用原注册表与系统权威根。

| Agent | 新协议输入 → 输出 | 职责 |
|---|---|---|
| Agent1 | BusinessFacts + 冻结知识 → DecisionGraph | 判断、证据、因果关系、候选动作、权重；不能写预算与目标参数 |
| Agent2 | DecisionGraph + 系统准入/分区 + factValues → PlanGraph | 对分区内动作逐个方案化；不得重选动作；基线、预期值、范围与增量一致 |
| Agent3 | PlanGraph + 冻结公司知识 → OperationGraph | 执行步骤、负责人、对象、顺序、回滚、停止条件及验收动作；阶段绑定 planActionRefs |

primaryProblemNode、primaryAction、primaryExecutionTarget、primaryOwner、lockedActionFamily、executionLock 在新协议输入、生成、归一化、语义缓存和图谱 RAG 路由中不再参与业务决策。来源投影剔除这些字段；在封装好的新输入或模型输出中夹带旧字段会被拒绝。旧协议仍供尚未切换的生产消费者使用；这不等于全仓已删除旧字段。

## 已接入原运行器

- Agent1 使用真实 v3 输入接口及其 22,000 字符预算。旧知识入口对新协议保留 knowledgeContext，不再注入 diagnosticRag/unifiedKnowledge。精确输出仍按 itemExecutionId + inputContentHash 匹配；未知业务字段直接拒绝。
- Agent2 提示词直接读取系统分区与 DecisionGraph，输出 PlanGraph。实际批次保持原 Artifact、claim、provider、输出接受流程；新协议按业务域分组，不调用旧 selected_family。分区结果必须完整覆盖指定动作。
- Agent3 直接读取 PlanGraph，并回传执行身份。匹配失败不进入归一化与接受；不再调用旧动作族约束编译器。
- 三者语义身份绑定各自事实/图谱、知识快照 Head、合同、业务主体、模型与生成配置；Agent2 另绑定分区和系统事实；修订输入额外绑定 scope 与父图。
- Agent2/3 在原 accepted execution index 查找图谱缓存。验证来源 Artifact 类型、内容哈希、语义身份和主体，当前输入重编译通过后写入新的输出 Artifact。不能复用旧 familyPayload 或旧 SOP；缓存命中不生成模型调用。
- Agent1 缓存重绑定重新编译 DecisionGraph 并校验证据引用，保留原精确执行流水。

## 图谱、修订与 SOP 证据

编译器校验字段所有权、有限数值、证据引用、图规模、重复节点/边与依赖环；重算哈希不能绕过 DecisionGraph 的语义校验。动作准入回执记录系统提供的 allowedActionKeys，分区时重新推导冲突和依赖结果，拒绝仅重签哈希的篡改。

系统按注册域分区并合并为单一 PlanGraph，补回跨域依赖；分区缺失、重复、换动作或覆盖不完整时拒绝合并。

Python 修订验收增加 Decision/Plan 图类型，保留节点内容及关联边必须不变；差异证据逐字段记录修改。它只验证内容和 scope 一致性，不签发 Java 授权。

Java ReviewContractAuthority 新增逐 PlanAction 冻结入口：验证 Python 图/节点哈希与指定合同、核对系统基线事实、预期值/增量/区间，并保留每个动作独立的观察窗口。指标以 PlanAction 引用区分，避免同名指标混淆。未知验收条件及尚未编译的 guard/riskBoundary 明确成为不确定评审合同，不能自动判成功。

Java LocalSubgraphRevisionAuthority 读取新三图的 decisionActionRef、judgementRefs、planActionRefs 与依赖边，复用原确定性范围算法，输出新的 Decision/Plan/Operation 修订字段。跨语言测试验证 Python 图哈希→Java 评审/局部 scope→Python scope 验证；保留“未证明成功的节点不得声称局部保留”的原门禁。新增入口仍须接入现有 RootBoundAuthorityAdapter 调用链，方法存在不等于生产权威已交接。

SOP 证据使用原 v26.sop_evidence.v1 展示接口，记录判断依据、动作权重、方案参数、冻结基线、预期结果、观察窗口、保护条件、执行与回滚。预期增量卡显示公式 expectedValue - baseline.value、输入值、单位、来源证据和图/节点哈希。只展示已记录的结构化决策依据，不暴露或补造模型内部思考。

知识 Head 当前表示单次输入知识快照的内容身份；尚非 Experience Store 全库 Head。不宣称已建立评测或回流效果。

## 验证

新增集成用例使用临时 SQLite 与真实本地 Artifact 存储，调用现有三个运行器；仅模型网关使用固定响应：

1. Agent1 精确输入产出 DecisionGraph。
2. Agent2 两个业务域执行、接受、完整合并 PlanGraph。
3. Agent3 产出 OperationGraph，完成三图映射校验。
4. 更换执行身份后 Agent2/3 命中语义缓存、重编译并写新输出 Artifact；网关调用次数不增加。

另覆盖错误执行身份、缺图、旧字段注入、知识篡改、换动作、非法参数、基线漂移、旧缓存拒绝、保留边被修改及 SOP 公式复算。

这属于运行器集成验证，不等于固定三报表到任务池的生产端到端验证，也不等于线上模型质量验证。精确 Python 3.11.9 的默认回归由既有 V26 Registry Lineage PR Gate 执行；本地 Python 3.12 的版本门禁失败不予放宽。

## 全量激活前仍需完成

| 入口 | 剩余工作 |
|---|---|
| Agent1 调度及动作包站点 | 将新事实输入和系统动作准入连接到现有流水线，替换旧动作包合同及字段登记 |
| Agent2 系统调度 | 自动生成、持久化所有分区输入并执行合并；绑定现有授权来源；校验跨域总预算、资源和失败重试范围 |
| Agent3 输入站点 | 将已接受的合并 PlanGraph 与公司权限上下文通过注册入口交接 |
| 任务映射/任务池 | 迁移多动作权限与生命周期消费者，保留真实调用证明及授权额度；不能把图哈希或模型状态当作执行许可 |
| Java ReviewContract/LocalSubgraphRevision | 新图内容接口及跨语言验证已完成；仍需接入生产根授权调用与任务观察生命周期，编译公司 guard/riskBoundary |
| 收口 | 按原注册表→血缘→精确包→门禁流程，跑固定三报表全链验收后统一评估合并 |

保持同一 PR 持续推进，不以内部步骤完成替代整个 A 的验收。持久化经验库、Evaluation Plane 和 Promotion Gate 属于语义稳定后的后续阶段。

远端提交 9869a3c4590747cfdce9585bbae8d1861426e315 的四项 PR 门禁全部通过。后续 Java 接口更新需在新提交上重跑门禁，不能沿用旧提交的通过状态。
