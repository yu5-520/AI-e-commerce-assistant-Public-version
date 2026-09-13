# V26.9.B Experience Store / Evaluation Plane

状态：`candidate_code_complete / production_activation_not_started`

V26.9.A 已在 `main` production-active。V26.9.B 只在 A 的执行结果之后增加经验保存、版本化评测与字段检索，不改变 `DecisionGraph → ActionAdmission → PlanGraph → OperationGraph → Task Mapping → Execution / System Review` 的业务权威。

V26.9.B 的边界是：**可以保存 candidate experience、计算和展示 Evaluation、生成 official retrieval receipt；不能 Promotion、不能自动更新 Knowledge Head、不能把未晋级经验送入正式 Agent。** Promotion / enabled lifecycle / Knowledge Head 正式变化仍属于 V26.9.C。

## 1. 自更新入口

B 先通过既有 Update Request / Locator 自更新框架完成治理入口升级：

- `V26 Update Locator` 不再绑定 `v269-a-current.json`，统一读取 `governance/update-requests/v269-current.json`；
- Registry 新增 `experience_store / evaluation_plane / experience_retrieval` 三个责任模块；
- candidate 阶段三个 B 模块只登记、不加入 production `requiredModules`；正式 activation 时必须独立加入 `requiredModules` 后重新验证；
- Policy 明确 B 的 schema、migration、seed、manifest、服务、测试、SOP 只读展示与独立 gate 修改面；
- Python package initializer `src/repositories/__init__.py` 作为 Experience Store 使用现有 SQLite repository 后进入运行闭包，已通过 Update Request → Locator 重新编译后精确登记；未放开整个 repositories 目录；
- filename-similarity search、unplanned mutation、read-only mutation 继续禁止，scope expansion 仍需重新编译 planHash。

## 2. Experience Store

第一版继续使用现有 ECS SQLite：`logs/product_workbench.sqlite3`，不引入第二数据库实现。

数据结构采用规范化来源：`v269b_experience_sources` 只保存一次来源任务、三图 Hash、图合同版本、评测版本、证据引用、业务范围和来源版本；五个经验域通过 `source_id` 引用来源，避免复制五份完整任务。

经验域：

- `experience_knowledge`
- `decision_patterns`
- `strategy_outcomes`
- `operation_patterns`
- `evaluation_results`

运行经验由 B 只能写成 `candidate`；Seed 只能写成 `seed`。B 不暴露任何把记录变成 `approved/enabled` 的 Promotion API。

Seed 带 `synthetic_documentation_seed_not_business_result` 来源标识，幂等导入，`sampleCount=0`，不能伪装为真实经营结果。

Manifest 固定部署边界：部署包不拥有运行数据库，运行经验不随发布包覆盖；备份默认写入 `logs/experience-backups`，恢复必须传入显式 operator intent。

## 3. Evaluation Plane

`config/v269b_evaluation_contract.json` 冻结第一版评测向量，不合成总分：

- Agent1：Judgement Accuracy、Action Relevance、Graph Value、False Expansion Rate
- Agent2：Prediction Accuracy、Expected Delta、Actual Delta、Delta Realization Rate
- Agent3：SOP Fidelity、Execution Completion、Deviation Rate、Rollback Rate
- System：Evidence Completeness、Business Outcome

每项包含定义、公式、分子、分母、单位、观察窗口、输入条件、缺失规则、支持结论与禁止结论。

边界：

- 没有可验证 label 时，Agent1 Accuracy / Relevance 明确 missing；
- Graph Value 只接受外部 Evaluation source，拒绝 Agent1 / model self-score；
- expected delta 为零或接近零时不计算 Delta Realization Rate；
- 负 expected delta 保留符号，使用 `actual_delta / expected_delta`；
- rollback rate 只表示频率，不自动等同质量差；
- Business Outcome 只记录观测，不归因给 Agent 或 RAG；
- Evaluation Vector 的 `compositeScore` 和 `causalAttribution` 固定为空。

Evaluation 持久化为 `evaluation_results` candidate experience，并保留 metric version、formula inputs、missing reason、observation window、sample count 与 interpretation limits。

## 4. System Review → candidate Experience / Evaluation

V26.9.B 已接入 A 的真实 System Review seam，但保持严格的下游关系：

1. A 先完成 Java Review receipt 验证；
2. A 先由现有 lifecycle writer 写入 `SETTLED` 或 `ADJUSTMENT_REQUIRED`；
3. B 再根据冻结的 DecisionGraph / PlanGraph / OperationGraph、TARGET facts 和 Review receipt 记录：
   - `strategy_outcomes` candidate；
   - `operation_patterns` candidate；
   - `evaluation_results` candidate；
4. B receipt 明确 `promotionPerformed=false / knowledgeHeadMutated=false`。

B 写入失败不会回滚、覆盖或重新解释 A 的 System Review 结果。失败只写入 `v269bExperienceEvaluation.status=FAILED` 证据，因此 Experience/Evaluation 是 A 的 fail-isolated downstream consumer，不是第二业务权威。

候选运行由 manifest 的 `candidateRuntimeEnv=V269B_CANDIDATE_RUNTIME` 显式打开；正式生产只认 manifest `rolloutStatus=active`。

## 5. 三 Agent 字段检索与 Semantic Identity

B 只实现确定性字段检索，不引入向量检索或 AI 动态扩展查询。

Agent1：`condition / metric / direction / category / decisionPattern`

Agent2：`decisionAction / baseline / category / strategyType`

Agent3：`planAction / platform / executionType`

排序固定为：`exact field match → sample_count → experience_id`。每次检索回执记录 query、domains、resultIds、ranking method、match count 和对应知识域 Head。

检索通过现有 `knowledgeContext` seam 接入 `v269_input_migration_service.project_input()`，没有第二 RAG 入口：

- Agent1 的 BusinessFacts 投影进入 `project_input(agent1)`；
- Agent2 的每个 admitted partition 进入 `project_input(agent2)`；
- Agent3 的 PlanGraph 输入进入 `project_input(agent3)`。

Experience retrieval receipt 与匹配记录被折叠进同一个 `knowledgeContext.records`；新的 `headHash / retrievalPolicyHash` 自动进入现有 Semantic Identity，因此经验内容变化会改变语义缓存身份，但缓存命中仍不能复用旧执行授权。

无匹配是合法状态：生成明确 empty retrieval receipt，不用 Seed、candidate 或过期经验补全。

`mode=official` 只读取 `lifecycle_status=enabled AND source_type=runtime`。Candidate/Seed 只能显式使用 inspection mode 查看；官方模式禁止 include_seed。

因此 **B production-active 后，在 C Promotion Gate 上线前，Agent 可以得到正式检索回执，但不会读取 candidate 经验**。

## 6. SOP Evaluation 证据

现有任务详情 `/api/view/tasks/{task_id}` 继续使用原 `sopEvidence`，没有新建平行页面。

`sopEvidence.evaluation` 只读取已经持久化的 `v269b_evaluation_results`：

- metricId / metricVersion；
- definition / formula；
- numerator / denominator / value / unit；
- observationWindow / sampleCount；
- missingReason；
- formulaInputs；
- supports / interpretationLimits；
- experience lifecycle、sourceHash、sourceVersion、evidenceRefs。

GET 路径固定 `recomputedOnRead=false`，不会为了展示再次运行 Agent、Evaluation 或 Experience 写入。测试会比较读取前后的 Experience Store 行数，保证点击 SOP 数据不会改变评测数据。

## 7. Knowledge Head

B 的正式 Head 只对 `enabled` 经验内容计算。Candidate、Seed 的新增不会改变正式知识域 Head。

这使 Store / Evaluation / Retrieval 可以在 B 完整生产运行，同时把“经验是否可以影响未来 Agent”留给 C 的 Promotion Gate 控制。

## 8. Candidate 验收

独立 `V26.9.B Experience Evaluation Candidate` gate 与既有 V26 gates 验证：

1. Experience / Evaluation / Retrieval 服务与 A 生产 seams 在 Python 3.11.9 编译；
2. A `businessSemanticContract` 仍为 `26.9.0 / active`；
3. Promotion 与 automatic knowledge-head mutation 均为 false；
4. Registry 包含三个 B 责任模块；candidate 状态下它们不得提前进入 `requiredModules`；
5. Experience source normalization、幂等、Seed 隔离、备份恢复约束通过；
6. Evaluation missing rules、负/零 delta、self-score 禁止、rollback 解释边界通过；
7. 真实三图可生成 strategy / operation / evaluation candidates，且不能 Promotion；
8. B Evaluation 写入故障不会反向破坏 A System Review；
9. SOP Evaluation 只读持久化证据，不在 GET 重算或回写；
10. Candidate/Seed 不进入 official retrieval；
11. Agent1/2/3 按各自字段合同生成 receipt，并折叠进现有 knowledgeContext；
12. Agent1 测试证明 Experience Head 变化会改变现有 Semantic Identity；
13. 无匹配不补造经验；
14. Registry Lineage、Field Authority、Business Graph、Security、Update Locator 与全仓回归仍必须通过。

## 9. Production Activation

Candidate PR 合并 `main` 仍不等于 B production-active。

独立 activation 事务至少必须完成：

1. Update Request 明确申请 B production activation；
2. Locator 授权 `rag/manifest/v269b_manifest.json` 与 `config/v23_registry_runtime.json`；
3. `manifest.rolloutStatus: candidate_not_activated → active`；
4. `experience_store / evaluation_plane / experience_retrieval` 加入 Registry `requiredModules`；
5. activation HEAD 再跑 B Candidate Gate、Registry Lineage、Field Authority、Business Graph、Security 与 Locator；
6. 独立 activation evidence 通过后才合并 main。

V26.9.C 才负责 Promotion Gate、approved/enabled/disabled/superseded 生命周期变更、Knowledge Head 正式更新、撤回、历史检索及 Legacy Projection 最终清理。
