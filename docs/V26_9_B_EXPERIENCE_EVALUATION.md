# V26.9.B Experience Store / Evaluation Plane

状态：`candidate_foundation_in_progress`

V26.9.A 已在 `main` production-active。V26.9.B 只在 A 的执行结果之后增加经验保存、版本化评测与字段检索，不改变 `DecisionGraph → ActionAdmission → PlanGraph → OperationGraph → Task Mapping → Execution / System Review` 的业务权威。

## 1. 自更新入口

B 先通过既有 Update Request / Locator 自更新框架完成一次治理入口升级：

- `V26 Update Locator` 不再绑定 `v269-a-current.json`，统一读取 `governance/update-requests/v269-current.json`；
- Registry 新增 `experience_store / evaluation_plane / experience_retrieval` 三个责任模块；
- 三个 B 模块暂不加入 production `requiredModules`，不会因为登记而自动进入生产闭包；
- Policy 明确 B 可修改的 schema、migration、seed、manifest、服务、测试、文档及独立 gate；
- filename-similarity search、unplanned mutation、read-only mutation 继续禁止，scope expansion 仍需重新编译 planHash。

## 2. Experience Store

第一版继续使用现有 ECS SQLite：`logs/product_workbench.sqlite3`，不引入第二数据库实现。

数据结构采用规范化来源：

`v269b_experience_sources` 只保存一次来源任务、三图 Hash、图合同版本、评测版本、证据引用、业务范围和来源版本；五个经验域通过 `source_id` 引用来源，避免复制五份完整任务。

经验域：

- `experience_knowledge`
- `decision_patterns`
- `strategy_outcomes`
- `operation_patterns`
- `evaluation_results`

运行经验由 B 只能写成 `candidate`；Seed 只能写成 `seed`。B 不暴露任何把记录变成 `approved/enabled` 的 Promotion API。

Seed 带 `synthetic_documentation_seed_not_business_result` 来源标识，幂等导入，`sampleCount=0`，不允许伪装为真实经营结果。

Manifest 固定：部署包不拥有运行数据库，运行经验不随发布包覆盖；备份默认写入 `logs/experience-backups`，恢复必须传入显式 operator intent。

## 3. Evaluation Plane

`config/v269b_evaluation_contract.json` 冻结第一版评测向量，不合成总分：

- Agent1：Judgement Accuracy、Action Relevance、Graph Value、False Expansion Rate
- Agent2：Prediction Accuracy、Expected Delta、Actual Delta、Delta Realization Rate
- Agent3：SOP Fidelity、Execution Completion、Deviation Rate、Rollback Rate
- System：Evidence Completeness、Business Outcome

每项包含定义、公式、分子、分母、单位、观察窗口、输入条件、缺失规则、支持结论与禁止结论。

重要边界：

- 没有可验证 label 时，Agent1 Accuracy / Relevance 明确返回 missing；
- Graph Value 只接受外部 Evaluation source，拒绝 Agent1 / model self-score；
- expected delta 为零或接近零时不计算 Delta Realization Rate；
- 负 expected delta 保留符号，使用 `actual_delta / expected_delta`；
- rollback rate 只表示频率，不自动等同质量差；
- Business Outcome 只记录观测，不归因给 Agent 或 RAG；
- Evaluation Vector 的 `compositeScore` 和 `causalAttribution` 固定为空。

Evaluation 可持久化为 `evaluation_results` candidate experience，并保留 metric version、formula inputs、missing reason 与 interpretation limits。

## 4. 三 Agent 字段检索

B 只实现确定性字段检索，不引入向量检索或 AI 动态扩展查询。

Agent1：`condition / metric / direction / category / decisionPattern`

Agent2：`decisionAction / baseline / category / strategyType`

Agent3：`planAction / platform / executionType`

排序固定为：`exact field match → sample_count → experience_id`，回执记录 query、domains、resultIds、ranking method、match count 和对应知识域 Head。

无匹配是正常状态，返回 `emptyResult=true` 和空 results；不能用 Seed 自动填充。

`mode=official` 只读取 `lifecycle_status=enabled AND source_type=runtime`。`candidate/seed` 只能显式使用 inspection mode 查看；官方模式禁止 include_seed。

## 5. Knowledge Head

B 的 Head 只对 `enabled` 经验内容计算。Candidate、Seed 的新增不会改变正式知识域 Head。

因此 B 可以先完整建设 Store / Evaluation / Retrieval，却不会改变 A 当前 Agent 的正式知识输入。只有 V26.9.C Promotion Gate 完成审核并改变 lifecycle 后，Head 才可能更新。

## 6. 当前验收

独立 `V26.9.B Experience Evaluation Candidate` gate 验证：

1. 三个 B service 可在 Python 3.11.9 编译；
2. A `businessSemanticContract` 仍为 `26.9.0 / active`；
3. Promotion 与 automatic knowledge-head mutation 均为 false；
4. Registry 包含三个 B 责任模块；
5. Experience source normalization、幂等、Seed 隔离、备份恢复约束通过；
6. Evaluation missing rules、负/零 delta、self-score 禁止、rollback 解释边界通过；
7. Candidate/Seed 不进入 official retrieval；
8. 未来由 C 晋级后的 enabled 记录可以被同一检索合同读取并改变 Head；
9. Agent1/2/3 均按各自字段合同确定性检索；
10. 无匹配不补造经验。

## 7. B 后续接线

本 foundation 完成后，下一步仍属于 V26.9.B：

- 将真实 System Review / execution result 组装成 Experience source；
- 将 SOP 数据卡增加 Evaluation formula / inputs / version / missingReason 展示；
- 将三 Agent 的经验检索结果以只读上下文接到现有输入投影，并保存 retrieval receipt；
- 保持 official retrieval 只读 enabled，因此在 C Promotion Gate 上线前，生产 Agent 会得到合法的明确空结果，而不是候选经验。

V26.9.C 才负责 Promotion Gate、approved/enabled/disabled/superseded 生命周期变更、Knowledge Head 正式更新、撤回、历史检索及 Legacy Projection 最终清理。
