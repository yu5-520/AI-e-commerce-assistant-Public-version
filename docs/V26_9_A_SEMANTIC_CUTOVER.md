# V26.9.A 业务语义主链迁移与生产激活凭证

状态：`production_activation_pending_required_gates`

V26.9.A 将当前业务解释链统一为：

`DecisionGraph → ActionAdmissionRecord → PlanGraph → OperationGraph → Task Mapping → Execution / System Review`

旧单动作字段 `primaryProblemNode / primaryAction / primaryExecutionTarget / primaryOwner / lockedActionFamily / executionLock` 不再作为 V26.9.A 当前业务语义权威或失败回退来源。

## 1. 唯一业务语义合同

合同登记在既有 `config/v26_field_authority_contract.json` 的 `businessSemanticContract`，继续复用原 Field Authority、Registry、Hash Lineage、Task Pool 与 Java production authority root，不新增第二权威根或平行 Agent runner。

| 组件 | 输入 → 输出 | 权责 |
|---|---|---|
| Agent1 | BusinessFacts + 冻结知识 → DecisionGraph | 经营判断、证据、因果、候选动作、动作权重；不得写 PLAN 数字 |
| System Admission | DecisionGraph + 权限/依赖/冲突 → ActionAdmissionRecord | 只准入 Agent1 已提出的动作，不创造新业务动作 |
| Agent2 | admitted DecisionAction + 冻结事实 → PlanGraph | 逐动作参数化；不得换动作；负责预算、目标、guard、riskBoundary、expectedOutcome |
| Agent3 | PlanGraph → OperationGraph | 只编译执行阶段；共享步骤显式绑定 PlanAction；不得扩张授权 |
| System | OperationGraph → Task Mapping → Execution / Review | 原子 reservation、任务接纳、durable scheduling、System Review / Revision |

## 2. Candidate 基线

V26.9.A 候选实现已在 PR #91 完成 `code-complete / candidate-validated` 并合入 `main`。

- PR：`#91 V26.9.A 唯一业务语义合同与三图生产主链切换`
- Candidate HEAD：`1334bac729d93086bfdd1ddd7ccd3a0e38ad4f16`
- Merge commit：`7fb1b83e5d1c4c173c598d58fadf4a3dd8fd0ec1`
- 固定三报表候选验收：`V26.9.A Three Report Candidate Gate` run #23，`completed / success`
- 已覆盖：唯一语义合同、Agent1/2/3 三图链、ActionAdmission、Semantic Identity、原子 graph authority reservation、task-pool admission、durable scheduler、System Review / LocalSubgraphRevision、legacy poison probe、fixed three-report attestation。

PR merge 仅表示候选代码进入 `main`，不自动等价于 production activation。

## 3. Production Activation Update Locator

生产切换由独立 PR #92 执行，先提交 Update Request，再由既有 Registry / Policy 编译 exact mutation scope。

Update Request：`V26.9.A-2026-09-13-production-activation-02`

第一次 activation Locator 暴露旧 workflow invariant 仍硬编码上一轮 candidate 文件。修复后，Locator invariant 改为依据当前 request 校验：

- `requestId / policyProfile` 必须与 compiled plan 一致；
- `selectedModules` 必须等于当前 request 的 registryModules 并集；
- `evidencePaths` 必须等于当前 request 的 evidencePaths 并集；
- evidence 必须属于 `editablePaths`；
- `editablePaths` 与 `readOnlyContextPaths` 不得重叠；
- filename similarity search、unplanned mutation、read-only mutation 均保持禁止；
- scope expansion 必须重新编译。

V26 Update Locator run #16：`completed / success`。

Compiled plan：

- `planHash = sha256:96673981e87ae0418ebf1e0fb4c7a0cbaa5ffdb3370d25f8802a77b5d2d73d67`
- `registryRootHash = sha256:c6308a05333fadc9467413cb7a68099d2e6958bceca0b265b764a4407b4eb0ac`
- `config/v26_field_authority_contract.json`：authorized editable path
- `docs/V26_9_A_SEMANTIC_CUTOVER.md`：authorized activation evidence path
- `.github/workflows/v26-update-locator.yml` 与 `tests/test_v26_update_locator.py`：authorized locator repair evidence paths

## 4. Production Semantic Switch

Locator 授权后执行唯一生产语义开关：

`businessSemanticContract.rolloutStatus: candidate_not_activated → active`

Activation switch commit：`343a3b983043fa891633efbac9e1e1089e016574`

该切换只改变已经完成 candidate validation 的 V26.9.A 业务语义合同状态，不引入新 Agent、第二业务语义、第二权限根或并行 runner。

## 5. Required Gates

本 activation receipt 只有在当前 activation HEAD 的 required gates 全部通过后才可视为 `verified_active`：

- `V26 Update Locator`
- `V26 Registry Lineage PR Gate`
- `V26 Field Authority Phase1`
- `V26 Business Graphs Phase2`
- `V24 Production Authority Bundle`
- `Competition Registry Lineage`
- `V26.9.A Three Report Candidate Gate`

任何 required gate 失败时，PR #92 不应合并，`active` 仅为待验证分支状态，不构成 `main` 的生产生效事实。

## 6. V26.9.A 明确边界

V26.9.A 不激活以下后续能力：

- Experience Store 正式运行态；
- Evaluation Plane 正式指标运行；
- Experience Promotion / Knowledge Head 更新；
- RAG experience feedback / 自动经验回流。

这些继续留给 V26.9.B / V26.9.C，避免 A 阶段重新引入第二套业务语义或把经验层与生产语义切换混为一个事务。

## 7. 完成判定

V26.9.A 的最终完成条件为：

`PR #91 candidate validated + merged main`

→ `PR #92 Locator exact plan verified`

→ `rolloutStatus = active`

→ `activation HEAD required gates all success`

→ `PR #92 merge main`

只有最后一步完成后，V26.9.A 才正式记为 `production-active / complete`。
