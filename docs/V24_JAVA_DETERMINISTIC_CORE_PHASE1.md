# V24.0-V24.5 — Java Deterministic Runtime Core / Phase 1

## 目标

第一阶段只建立 Java 身份控制底座，不替换现有 Python Agent/Task/Queue 正式写链。

阶段范围：

- V24.0 Baseline
- V24.1 Registry Shadow Authority
- V24.2 Hash Lineage Shadow Authority
- V24.3 Runtime Exclusivity
- V24.4 Build Lock
- V24.5 Runtime Admission

核心原则：

> 代码存在，不等于具有运行资格。生产候选只能由已经通过 Registry、Hash Lineage 和 ActiveRuntimeGraph 证明的文件集合构成。

## V24.0 Baseline

基线冻结在 `governance/v24/v24-phase1-baseline.json`。

正式生产入口继续是：

```text
src.api.main:app
```

现有 V23 Python runtime 在第一阶段仍然是正式运行权威。Java 只做 shadow identity/admission，不具有业务写权。

## V24.1 Registry

Java 读取并交叉验证：

```text
config/competition_source_identity.json
config/v23_registry_runtime.json
config/competition_runtime_scope.json
```

必须满足：

```text
sourceIdentity.registryRootHash
== registry.registryRootHash
== lineage.registryRootHash
```

否则 Phase 1 失败关闭。

## V24.2 Hash Lineage

现有 Python `compile_competition_lineage.py` 仍然负责从真实生产入口、Registry implementationPaths、Runner、Python import closure 和前端 reference closure 编译完整运行血缘。

Java 不重新猜测依赖，也不按文件名重新扫描选择主链；Java 对已经编译出的 lineage evidence 做第二实现验证：

1. 重新按 canonical JSON + SHA-256 计算 `graphHash`；
2. 检查每一个 active file node 的真实文件 SHA-256；
3. 检查唯一 production entrypoint；
4. 生成 V24 `ActiveRuntimeGraph`。

因此第一阶段形成：

```text
Python Lineage Compiler
        ↓
lineage-graph.json
        ↓
Java Independent Verification
        ↓
ActiveRuntimeGraph
```

## V24.3 Runtime Exclusivity

`governance/v24/runtime-exclusivity-policy.json` 定义：

```text
defaultRuntimeEligibility = DENY
unknownRuntimeNode = BLOCK
compatibilityDefault = DENY
```

第一阶段不根据 `legacy`、`old`、版本号等文件名判断退役。

如果旧文件仍在当前 V23 的真实 import closure 中，它暂时属于 migration-bound ACTIVE dependency；只有完成替换并从 ActiveRuntimeGraph 中退出后，后续阶段才允许登记 RETIRED/TOMBSTONED。

这样避免为了“清理旧代码”误杀当前稳定链。

## V24.4 Build Lock

Java 生成：

```text
dist/v24-java-phase1/active-runtime-manifest.json
```

Manifest 只包含 ActiveRuntimeGraph 证明过的 file nodes，并记录：

```text
path
sha256
status = ACTIVE
runtimeEligible = true
```

构建候选必须满足 exact file set：

```text
Candidate Files
==
Active Runtime Manifest Files
```

多一个未知运行文件：BLOCK。

少一个合法运行文件：BLOCK。

文件 Hash 不一致：BLOCK。

因此 Build Lock 不再依赖“这个目录看起来像生产代码”，而依赖唯一 ActiveRuntimeGraph。

## V24.5 Runtime Admission

`Phase1Main admit` 对 materialized runtime candidate 做第二次验证：

```text
Manifest Hash
↓
Exact File Set
↓
Per-file SHA-256
↓
Runtime Admission
```

成功输出：

```text
dist/v24-java-phase1/runtime-admission-report.json
```

第一阶段 admission 仍属于 CI/Shadow Gate，不直接接管 ECS 正式启动权。正式启动权迁移将在后续 Deployment Authority 阶段完成。

## 执行入口

本地/ECS：

```bash
bash scripts/verify_v24_java_phase1.sh
```

CI：

```text
.github/workflows/v24-java-deterministic-core-phase1.yml
```

CI会顺序执行：

```text
Python exact lineage compile
↓
Java canonical/hash self-test
↓
Java ActiveRuntimeGraph compile
↓
Java ActiveRuntimeManifest compile
↓
materialize exact runtime candidate
↓
Java Runtime Admission
↓
V24_PHASE1_SHADOW_GATE=PASS
```

## 第一阶段明确不做

- 不替换 Agent1/Agent2/Agent3；
- 不建立第二个 Python worker；
- 不迁 Task/Queue/PostgreSQL 写权；
- 不取消 Runtime Generation Barrier；
- 不改 production entrypoint；
- 不按文件名自动删除 legacy 文件；
- 不允许 Java shadow 结果反向修改正式业务状态。

## Phase 1 完成定义

以下条件全部成立才视为 V24.0-V24.5 完成：

```text
BASELINE = frozen
REGISTRY_ROOT = matched
LINEAGE_GRAPH_HASH = independently reproduced
ACTIVE_RUNTIME_GRAPH = generated
DEFAULT_RUNTIME_ELIGIBILITY = DENY
BUILD_FILE_SET = exact
UNKNOWN_RUNTIME_NODE = blocked
RUNTIME_ADMISSION = passed
PYTHON_PRODUCTION_AUTHORITY = unchanged
```

下一阶段才开始迁移 Deterministic Mapping、Unified Gate、Task/State 写权。
