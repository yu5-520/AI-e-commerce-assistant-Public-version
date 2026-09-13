# V26.9.C Promotion Gate / Feedback Loop

状态：`candidate_not_activated`

V26.9.A 继续拥有唯一业务语义链与执行权限；V26.9.B 继续负责 candidate Experience、Evaluation Plane 与 enabled-only field retrieval。V26.9.C 只增加**显式审核后的经验晋级/撤回/替换治理**，不改变三 Agent 的写权限，也不允许 System Review 自动晋级经验。

## 固定闭环

`Execution Result → System Review → Evaluation → candidate Experience → Promotion Gate → reviewer approve → operator enable → enabled-set Knowledge Head → official retrieval`

其中：

- `SETTLED` 只允许生成 candidate，不代表批准；
- reviewer `approve` 只把 `candidate → approved`，不会进入正式检索；
- operator 必须再次显式 `enable` 才能 `approved → enabled`；
- formal retrieval 仍只读取 `enabled + runtime`；
- `disable`/withdraw 后记录仍保留在 inspection/history，但立即退出 formal retrieval；
- 新经验与同一 applicability 的已启用经验冲突时，必须在审核时显式指定 `supersedesExperienceId`；enable 时旧记录与新记录在同一 SQLite 事务内完成 `enabled → superseded` 与 `approved → enabled`。

## 三 Agent 回流来源

System Review 完成后只写 candidate，不直接改 Head：

- Agent1：从已封印 `DecisionGraph + PlanGraph + later target facts` 生成 `decision_patterns`。只为进入 PlanGraph 的 DecisionAction 沉淀经验，未执行候选动作不会伪造成历史经验；direction 由冻结 baseline 与 later target 确定，`graphValueScore` 不由 Agent1 自评。
- Agent2：继续由 B 生成 `strategy_outcomes` 与版本化 Evaluation，Promotion policy 要求可用的 `agent2.actual_delta / delta_realization_rate` 证据。
- Agent3：继续由 B 生成 `operation_patterns`，记录 PlanAction、execution type、完成状态等执行经验。

三个 domain 都先进入 `candidate`；只有 reviewer approve + operator enable 后，才会被对应 Agent 的 official field retrieval 读取。

## Knowledge Head

C 不保存一个可单独修改的 Head 指针。Head 继续由 Experience Store 对当前 `enabled` 集合做内容寻址计算：

`Head = hash(domain + enabled experience ids + payload hashes + updated_at)`

C 只在 enable/withdraw/supersede 后追加 `previous_head → next_head` 审计事件。因此不存在“数据库状态是一套、Head 指针又是另一套”的第二真相。

## Promotion Gate

阈值全部来自 `config/v269c_promotion_policy.json`，实现代码不硬编码业务阈值。Gate 检查：

- runtime source；
- candidate/approved 生命周期；
- System Review 状态与政策允许值；
- evidence 引用完整度；
- domain sampleCount；
- 版本化 Evaluation 是否满足 domain policy；
- policy metric threshold；
- applicability risk flag；
- 显式 expiry；
- enabled duplicate / conflict；
- conflict 是否有显式 supersede。

Gate 本身只返回 receipt，不做状态修改。

## 审计与撤回

`002_v269c_promotion_feedback.sql` 仅新增三类审计表：review、lifecycle event、domain head event。Experience 的实际 lifecycle 仍在 `v269b_experience_items.lifecycle_status`，没有第二套状态机。

Ops API 提供独立的 gate/history/review/enable/disable 路径；review 与 enable 是两个显式动作，withdraw 也要求显式 operator intent。

## Legacy Projection cleanup

C candidate 已把 `agent1.locked_action_family / agent1.execution_lock` 从生产 Registry 业务 field projection 中撤掉；`primaryProblemNode / primaryAction / primaryExecutionTarget / primaryOwner / lockedActionFamily / executionLock` 继续保留在 `businessSemanticContract.legacyReadForbidden`，作为 fail-closed 防线而不是可读业务输入。

执行互斥、权限、quota reservation、idempotency、Java authority 不属于 Legacy Projection，不删除。旧字段扰动继续不得改变 graph input、cache identity、retrieval、admission 或 revision。

## Candidate / Activation 边界

候选阶段已注册 `experience_promotion` 模块，但它不进入 `requiredModules`；`rag/manifest/v269c_manifest.json` 仍保持 `candidate_not_activated`。这允许 Registry/Lineage 对 C 实现做完整候选验证，同时避免候选代码合并被误报为生产激活。

生产激活必须使用独立 Update Request / PR，把 `rag/manifest/v269c_manifest.json.rolloutStatus` 从 `candidate_not_activated` 切为 `active`，并把 `experience_promotion` 加入 Registry `requiredModules`，随后重新运行 C、B、Registry Lineage、Field Authority、Business Graph、Security 与 Update Locator 门阀。候选合并不等于生产激活。
