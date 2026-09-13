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

## Legacy Projection cleanup

C 的最终 cleanup 目标是让 `lockedActionFamily / executionLock / primary*` 等旧业务字段退出 Registry 的业务语义投影与消费者合同。`legacyReadForbidden` 仍保留为 fail-closed 防线；执行互斥、权限、quota reservation、idempotency、Java authority 不属于 Legacy Projection，不删除。

## Activation 边界

候选 PR 只提交 C 实现与 candidate manifest。生产激活必须使用独立 Update Request / PR，把 `rag/manifest/v269c_manifest.json.rolloutStatus` 从 `candidate_not_activated` 切为 `active`，并重新运行 C、B、Registry Lineage、Field Authority、Business Graph、Security 与 Update Locator 门阀。候选合并不等于生产激活。
