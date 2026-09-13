# V26.9.B Experience Store / Evaluation Plane

状态：`active`（PR #94 已合并；下文 activation 过程保留为历史记录）

V26.9.A 已在 `main` production-active。V26.9.B 只在 A 的执行结果之后增加经验保存、版本化评测与字段检索，不改变 `DecisionGraph → ActionAdmission → PlanGraph → OperationGraph → Task Mapping → Execution / System Review` 的业务权威。

V26.9.B 的固定边界是：**可以保存 candidate experience、计算和展示 Evaluation、生成 official retrieval receipt；不能 Promotion、不能自动更新 Knowledge Head、不能把未晋级经验送入正式 Agent。** Promotion / enabled lifecycle / Knowledge Head 正式变化仍属于 V26.9.C。

## 1. 自更新入口

B 通过既有 Update Request / Locator 自更新框架完成治理入口升级：

- `V26 Update Locator` 统一读取 `governance/update-requests/v269-current.json`；
- Registry 登记 `experience_store / evaluation_plane / experience_retrieval` 三个责任模块；
- Policy 明确 B 的 schema、migration、seed、manifest、服务、测试、SOP 只读展示与独立 gate 修改面；
- Python package initializer `src/repositories/__init__.py` 因 Experience Store 使用现有 SQLite repository 进入运行闭包，已通过 Update Request → Locator 重编译后精确登记，未放开整个 repositories 目录；
- filename-similarity search、unplanned mutation、read-only mutation 继续禁止，scope expansion 仍需重新编译 planHash。

## 2. Experience Store

第一版继续使用现有 ECS SQLite：`logs/product_workbench.sqlite3`，不引入第二数据库实现。

`v269b_experience_sources` 只保存一次来源任务、三图 Hash、图合同版本、评测版本、证据引用、业务范围和来源版本；五个经验域通过 `source_id` 引用来源：

- `experience_knowledge`
- `decision_patterns`
- `strategy_outcomes`
- `operation_patterns`
- `evaluation_results`

B 运行经验只能创建 `candidate`；Seed 只能创建 `seed`。B 不暴露把记录变成 `approved/enabled` 的 Promotion API。Seed 带 `synthetic_documentation_seed_not_business_result` 来源标识、幂等导入和 `sampleCount=0`，不能伪装成真实经营结果。

部署包不拥有运行数据库；运行经验不随发布包覆盖。备份默认写入 `logs/experience-backups`，恢复必须有显式 operator intent。

## 3. Evaluation Plane

`config/v269b_evaluation_contract.json` 冻结第一版评测向量，不合成总分：

- Agent1：Judgement Accuracy、Action Relevance、Graph Value、False Expansion Rate
- Agent2：Prediction Accuracy、Expected Delta、Actual Delta、Delta Realization Rate
- Agent3：SOP Fidelity、Execution Completion、Deviation Rate、Rollback Rate
- System：Evidence Completeness、Business Outcome

每项包含定义、公式、分子、分母、单位、观察窗口、输入条件、缺失规则、支持结论与禁止结论。

固定边界：没有可验证 label 时 Accuracy / Relevance 明确 missing；Graph Value 拒绝 Agent/model self-score；expected delta 为零或接近零时不计算 realization rate；负 expected delta 保留符号；rollback rate 不自动等同质量差；Business Outcome 只记录观测、不归因给 Agent 或 RAG；`compositeScore` 和 `causalAttribution` 固定为空。

Evaluation 持久化为 `evaluation_results` candidate experience，并保留 metric version、formula inputs、missing reason、observation window、sample count 与 interpretation limits。

## 4. System Review → candidate Experience / Evaluation

V26.9.B 接入 A 的真实 System Review seam，但保持严格下游关系：

1. A 先验证 Java Review receipt；
2. A 先由现有 lifecycle writer 写入 `SETTLED` 或 `ADJUSTMENT_REQUIRED`；
3. B 再依据冻结的 DecisionGraph / PlanGraph / OperationGraph、TARGET facts 和 Review receipt 记录 `strategy_outcomes`、`operation_patterns`、`evaluation_results` candidate；
4. B receipt 固定 `promotionPerformed=false / knowledgeHeadMutated=false`。

B 写入失败不会回滚、覆盖或重新解释 A 的 System Review。失败只记录 `v269bExperienceEvaluation.status=FAILED`，因此 Experience/Evaluation 是 A 的 fail-isolated downstream consumer，不是第二业务权威。

## 5. 三 Agent 字段检索与 Semantic Identity

B 只实现确定性字段检索，不引入向量检索或 AI 动态扩展查询。

- Agent1：`condition / metric / direction / category / decisionPattern`
- Agent2：`decisionAction / baseline / category / strategyType`
- Agent3：`planAction / platform / executionType`

排序固定为 `exact field match → sample_count → experience_id`。回执记录 query、domains、resultIds、ranking method、match count 和对应知识域 Head。

检索复用现有 `knowledgeContext` seam，没有第二 RAG 入口：Agent1 的 BusinessFacts、Agent2 的 admitted partition、Agent3 的 PlanGraph 都经现有 `v269_input_migration_service.project_input()` 接入。Experience retrieval receipt 与匹配记录折叠进同一个 `knowledgeContext.records`；新的 `headHash / retrievalPolicyHash` 自动进入现有 Semantic Identity，因此经验内容变化会改变语义缓存身份，但缓存命中仍不能复用旧执行授权。

无匹配返回明确 empty receipt，不用 Seed、candidate 或过期经验补全。`mode=official` 只读取 `lifecycle_status=enabled AND source_type=runtime`；Candidate/Seed 只能 inspection 查看。

因此 **B production-active 后，在 C Promotion Gate 上线前，正式 Agent 会获得合法 retrieval receipt，但 candidate 经验不会被正式检索命中。**

## 6. SOP Evaluation 证据

现有 `/api/view/tasks/{task_id}` 继续使用原 `sopEvidence`，不新建平行页面。

`sopEvidence.evaluation` 只读取已持久化 `v269b_evaluation_results`，展示 metric/version、公式、分子分母、值与单位、观察窗口、样本量、missingReason、formulaInputs、supports/interpretationLimits，以及 experience/source/evidence refs。

GET 路径固定 `recomputedOnRead=false`，不会为展示重新运行 Agent、Evaluation 或 Experience 写入；测试比较读取前后 Store 行数，保证点击 SOP 数据不会改变评测数据。

## 7. Candidate 验收

Candidate PR #93：`V26.9.B Experience / Evaluation / Retrieval runtime integration`。

- Candidate HEAD：`bd80f3a15b28b31e4c80f92e1294dfd8518e7f09`
- Candidate merge commit：`d3c0ddfbee1a4c2491b9c6ae900f0356537d3b78`
- Candidate final gates：V26.9.B Experience Evaluation Candidate、Update Locator、Registry Lineage、Field Authority、Business Graphs、Public PR Security 全部 success；Registry Lineage 包含 exact BASE→TARGET 验证与 Python 3.11.9 全仓回归。

Candidate tests证明：真实编译三图可以生成 strategy / operation / evaluation candidates；B 故障不反向破坏 A System Review；SOP Evaluation 只读持久化证据；Candidate/Seed 不进入 official retrieval；三 Agent receipt 折叠进现有 knowledgeContext；Experience Head 变化会改变现有 Semantic Identity；无匹配不会补造经验。

PR #93 合并只建立 B 的 validated code baseline，不等于 production activation。

## 8. Production Activation 事务

独立 PR #94：`V26.9.B production activation`。

Activation Update Request：`V26.9.B-2026-09-13-production-activation-01`。

本次继续复用：

`Update Request → Registry responsibility modules → Update Locator exact mutation plan → production switch → activation HEAD gates → merge main`

Update Locator run #59 已完成 compile plan、invariant 和 locator tests，明确授权本次生产切换面：

- `rag/manifest/v269b_manifest.json`
- `config/v23_registry_runtime.json`
- `docs/V26_9_B_EXPERIENCE_EVALUATION.md`

已在 activation 分支执行：

1. `manifest.rolloutStatus: candidate_not_activated → active`，commit `f84cb707dd89c8995105d762d7edc3246e0fb88e`；
2. Registry `requiredModules` 加入 `experience_store / evaluation_plane / experience_retrieval`，commit `5e0ab5cecfd8222bbda3af39bead2b1883b23a08`；
3. `promotionEnabled=false`、`automaticKnowledgeHeadMutation=false`、`officialRetrievalStatus=enabled` 保持不变，未启用任何 V26.9.C 能力。

**activation 分支上的 `active` 不是 main 的生产事实。** 只有当前 activation HEAD 的 B Candidate Gate、Registry Lineage、Field Authority、Business Graphs、Public Security 与 Update Locator 全部通过，并且 PR #94 合并 `main` 后，V26.9.B 才正式记为 `production-active / complete`。

## 9. V26.9.C 边界

V26.9.C 才负责：Promotion Gate、candidate→approved/enabled/disabled/superseded 生命周期变更、Knowledge Head 正式更新与撤回、经验回流正式启用、历史晋级追溯以及 Legacy Projection 最终清理。
