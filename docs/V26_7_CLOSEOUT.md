# V26.7 收口：确定性边界与 SOP 证据展示

Base: `475ac5aadb675b9c19aca462e3daaecf5fb17172` (V26.6).

## 更新框架

沿用 `v26_field_authority` profile：统一注册表 → 精确 BASE/TARGET 血缘比较 → hosted PR 验证 → 原 Release Hash Seal / V24 Production Authority Bundle → ECS Verifier / Runtime Identity。

只登记本次实际影响文件、task_mapping/task_pool/frontend_view 模块；不改 registryRootHash、不增加 SystemStage、Agent、权限根或特殊哈希豁免。PR 不使用 self-hosted runner。

## 收口内容

| 问题 | 本次处理 | 验证 |
| --- | --- | --- |
| NaN/Infinity、嵌套数值、字段负载 | 拒绝非有限数和超出合同预算的字段 | V26 Python closeout |
| 证据引用补造 | 新判断节点证据必须来自归一化输入已有引用 | V26 graph regressions |
| 缓存与执行身份耦合 | 缓存采用去除执行来源的业务图摘要，保留店铺/商品和业务内容 | Agent2 cache + V26 closeout |
| 生命周期覆盖、重启与竞争写入 | 初始化不能清除活动锁；提供文件持久化、原子替换、跨实例锁与版本检查 | V267CloseoutMain |
| 观察期重复调用 | 观察锁期间突破不创建竞争任务；后续验收决定调整 | V263 + V267 |
| 验收标准仅留痕未执行 | 未编译的必需条件不能 SETTLED；检查窗口、上下界和证据数量 | V264 + V267 |
| 时间改变导致重复身份 | 同一合同和相同证据的验收哈希不受重试时间变化影响 | V267 |
| 局部映射不完整 | 逐项覆盖异常指标与动作；保留需明确成功状态，否则全图兼容 | V266 |
| 依赖环与图膨胀 | 节点/边/深度/字符预算与依赖环检查 | V26 closeout |
| 新输出冒充历史兼容 | 新 operationStages 缺 actionRefs 拒绝，历史投影显式标记 | V26 graph tests |
| 修订输出越界 | 完整图/节点/边哈希校验、保留节点不可变、成员变更需另行授权 | revision acceptance tests |
| SOP 展示 | 运行时冻结决策记录 → taskPlan → 原任务 DTO → SOP 内可点击展开 | DTO + renderer checks |
| 数据公式 | 冻结观测值显示源数据；相邻快照变化量显示真实公式和操作数；不为报表原值编造公式 | evidence tests |
| RAG 审核回流 | 按 taskId 读取 V25 不可变版本/审核事件并复算哈希，不读取完整知识库 | scoped audit tests |

## 人类可读展示

SOP 默认仍为执行步骤。数据与决策依据区使用原生 details/summary，支持键盘与移动端；点击不调用 LLM。按 FACT / DERIVED / PLAN / DECISION 区分原始观测、系统计算、计划参数和结构化决策记录。候选方案仅展示当时确实记录的字段。原始 prompt、私有思维过程和 provider 凭据不进入该 DTO。

来源不全显示未记录，哈希不匹配不展示其卡片。RAG 引用与 RAG 效果严格区分：没有 BASE/TARGET 对照评测时显示无效果证据；审核通过不宣称线上生效或因果改善。知识审核记录与任务创建时冻结的决策记录分开展示。

## 运行边界与后续验收条件

- 本次不改变 `READY_NO_AUTHORITY`；Java 生产所有权转移仍需已有切换合同，不把 shadow proof 当作 ECS 生产闭环。
- `ProductLifecycleAuthority(Path)` 是耐久适配器，无参构造保留为 shadow/test 适配器。正式绑定必须使用带店铺隔离的商品标识；生命周期与队列之间的跨存储事务不能因本地状态耐久而被宣称自动完成。
- 修订输入的 scope 必须由既有 Java/Artifact 授权投影提供，模型不能提供或扩大。当前验收不允许擅自增删节点，需结构变化的任务走独立授权修订。
- 冷启动、紧急止损、超时升级、任务预算、计量口径与对照效果评估必须使用明确系统策略和真实数据。不能从模型输出或缺失证据推断为已完成；尚无真实策略/数据时保持原有拒绝/未记录状态。合同登记预算作为后续适配依据，不宣称未接线的限制已经在生产调度生效。
- 历史任务缺少运行时决策回执不调用 LLM 补写。新任务的回执经过 Agent3 输出、确定性任务映射、TaskSnapshot 和原 DTO 传输。
- 展示证据不授予新的生产执行权限，发现无法解析的风险条件应进入已有人工/调整流程。

## 验证入口

```bash
python -m pytest -q tests/test_v26_field_authority_contract.py tests/test_v26_business_graph_bridge.py tests/test_v22_4_v267_closeout.py
python -m unittest -q tests.test_agent2_family_payload_semantic_cache_v1
python -m pytest -q
```

Java 全树编译后运行 V263PreAgentAdmissionMain、V264SystemReviewMain、V266LocalSubgraphRevisionMain、V267CloseoutMain。最后一个也在原 production bundle 验证脚本运行。正式发布仍使用仓库固定 Python 3.11.9 / 依赖锁验证。
