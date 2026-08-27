# V24.6-V24.8 — Java Deterministic Runtime Core / Phase 2

## 范围

第二阶段建立三类 Java 确定性业务权威的 Shadow 实现：

- V24.6 Mapping Authority
- V24.7 Unified Gate Authority
- V24.8 Task/State Authority

本阶段仍不替换 Python Agent1/2/3，也不直接切换正式任务写权。

## V24.6 Mapping Authority

当前 Python `canonical_product_snapshot_service.py` 仍是生产 Canonical Product Snapshot 写入者。

Java `CanonicalProductMapper` 独立复现以下确定性规则：

```text
Product identity
Profile mapping
Metric mapping
Permission reference
Fact lineage
Completeness
Canonical SHA-256
productSnapshotHash
```

CI 先由现有 Python pure builder 生成代表性输入/输出测试向量，再要求 Java 对同一输入生成完全相同的 canonical JSON 和 Hash。

因此本阶段的迁移状态是：

```text
Python production mapper
        ↓ evidence
Java shadow mapper
        ↓ exact compare
MAPPING_SHADOW_GATE
```

Java 未通过 exact compare 前不得取得生产 Snapshot 写权。

## V24.7 Unified Gate Authority

新增统一 Gate 定义：

```text
governance/v24/unified-gate-definitions.json
```

Java `GateEngine` 使用统一结构执行：

```text
gateId
predicates
passDecision
failDecision
inputHash
gateDecisionHash
```

默认规则：

```text
unknownGate = BLOCK
canonicalHash = SHA-256
```

旧 `pipeline_gate_service.py` 的 SHA-1 只作为历史只读兼容身份，不再作为 V24 新 Gate 的 canonical identity。

第一批 Gate：

- PRODUCT_SNAPSHOT_ADMISSION
- TASK_TRANSITION_ADMISSION
- STATE_VERSION_CONFLICT

## V24.8 Task/State Authority

Java `TaskStateAuthority` 独立固化当前 Python `ALLOWED_TRANSITIONS` 与 terminal states，并新增 expectedVersion/currentVersion 判定。

```text
Current State
+ Target State
+ Current Version
+ Expected Version
        ↓
PASS / BLOCK / CONFLICT
```

规则：

```text
unknownState = BLOCK
terminalReopen = BLOCK
staleVersion = CONFLICT
sameState = idempotent PASS
```

当前 Python 状态写入口仍保持不变；PostgreSQL Source of Truth 尚未在 Phase 2 Shadow 中启用。

原因是第二阶段首先证明 Java 能稳定复现现有业务状态规则，正式写权与数据库迁移必须在 Shadow 证据通过后再切换。

## 验证

```bash
bash scripts/verify_v24_java_phase2.sh
```

验证链：

```text
Python mapping/task evidence export
↓
Java compile
↓
Mapping exact reproduction
↓
Unified Gate vectors
↓
Task transition matrix parity
↓
Version conflict / terminal reopen tests
↓
V24_PHASE2_SHADOW_GATE=PASS
```

## 网络稳定性

Phase 2 CI 不再使用 `actions/checkout` 获取仓库源码，而使用 exact commit tarball + bounded retry materialization，减少 self-hosted ECS Runner 到 `github.com:443` 的 git-fetch 超时对 Gate 的影响。

## Phase 2 完成定义

```text
MAPPING_VECTOR_PARITY = PASS
CANONICAL_SNAPSHOT_HASH_PARITY = PASS
UNKNOWN_GATE = BLOCK
GATE_DECISION_HASH = SHA-256
TASK_TRANSITION_MATRIX_PARITY = PASS
UNKNOWN_TASK_STATE = BLOCK
TERMINAL_REOPEN = BLOCK
STALE_VERSION = CONFLICT
PYTHON_PRODUCTION_WRITE_AUTHORITY = UNCHANGED
POSTGRES_SOURCE_OF_TRUTH = NOT_YET_ENABLED
```

Phase 2 通过后，下一阶段才迁 Queue model split、Stage Job、Agent1/2/3 async consumers、Idempotency 与 Generation Fencing。
