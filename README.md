# AI ERP 企业级电商经营 SaaS 底座

Public product/API release: **V22.4.0 Release Hash Seal Lite**  
Deployment single-authority hotfix: **22.5.4**  
Three-Agent state machine and execution lock: **22.5.5**  
Agent1 input semantics: **22.5.8 / agent_input.agent1.v3**  
Hash-directed Artifact runtime and frontend views: **22.5.9**  
Interface documentation sync: **22.5.10**

V22.4负责Git Commit、Release Hash、精确Python、依赖、灰度证据、Root信任、GitHub Artifact传输、ECS目录和SQLite数据身份。V22.5在同一签封运行时内恢复三Agent业务语义分层，并以不可变Artifact Hash作为Agent执行、缓存重放和前端视图交接的唯一业务身份。

Production dependency contract: `requirements.lock`  
Gray-test dependency contract: `requirements-dev.lock`  
Runtime environment identity: `runtimePipFreezeHash == pipFreezeHash`

## 当前版本矩阵

```text
公开产品/API版本                    22.4.0
正式部署单一权威                     22.5.4
三Agent状态机与执行锁                 22.5.5
Agent1输入语义                       22.5.8
Agent1输入Schema                     agent_input.agent1.v3
Hash定向Artifact执行                 22.5.9
Agent1严格Token运行时                22.5.9
前端View Artifact/Manifest          22.5.9
接口文档同步                         22.5.10
```

这些版本承担不同职责，不应被强行改成一个数字。公开API保持22.4.0，不代表内部Agent执行仍停留在22.4.0。

## 唯一业务主链

```text
真实报表Artifact
→ 最近五份事实比较与历史趋势
→ operatingEvidenceGraph.v1
→ signalRef
→ agent1InputRef
→ 校验inputContentHash
→ executionHash / itemExecutionId
→ Agent1经营判断（最多8商品微批次）
→ 单商品Agent1输出Artifact
→ agentExecutionOutputRef
→ capabilityRef
→ agent2DraftInputRef
→ Agent2垂直类目/平台化动作草案
→ agent2DraftRef
→ agent3SopInputRef
→ Agent3公司化高质量SOP
→ agent3SopRef
→ 确定性任务映射
→ taskMappingRef
→ 权限准入、任务生命周期与自动复盘
```

输入硬边界：

```text
Agent1只读取 artifactRefs.agent1InputRef
Agent2只读取 artifactRefs.agent2DraftInputRef
Agent3只读取 artifactRefs.agent3SopInputRef
```

完整`signalRef`、`capabilityRef`、历史流水payload和原始批次输出只用于审计与血缘，不得成为模型失败回退输入。

## Hash定向执行

每个Agent执行条件生成：

```text
executionHash = Hash(
  inputArtifactRef + inputContentHash + stage + inputSchema +
  projectionVersion + promptVersion + policyHash +
  provider + model + generationParametersHash
)
```

硬规则：

```text
一个executionHash
→ 最多一个acceptedOutputRef

完全相同executionHash
→ 返回同一个不可变输出Artifact
→ 不重新调用模型

执行条件变化
→ 生成新executionHash
→ 不覆盖旧输出
```

禁止：

```text
旧Agent结果重绑新dataVersion
旧Agent结果重绑新signalId/packageId/productId
删除来源Hash后跨报表复用业务判断
```

`llm_item_result_cache_v211`不再拥有Agent业务结果重放权。正式重放由`artifact_execution_index_v2259`定位不可变输出Artifact。

## Agent1八商品微批次

Agent1继续一次Provider请求最多处理8份商品，不拆成8次接口调用。

```text
Batch Manifest
├─ itemExecutionId 1 + inputContentHash 1
├─ itemExecutionId 2 + inputContentHash 2
├─ ...
└─ itemExecutionId 8 + inputContentHash 8
```

`slot`只表示请求顺序。模型返回顺序可以变化，匹配必须使用：

```text
itemExecutionId + inputContentHash
```

分类：

```text
原始响应完全没有itemExecutionId
→ true missing
→ 仅该商品单项补偿

返回ID但Hash缺失/错误
→ output contract invalid
→ 不按漏商品重试

重复ID
→ 每一份都拒绝
→ 不接受第一条

批次外ID
→ extra
→ 不进入下游
```

一个商品失败不会使同批已接受商品重跑。

## 三Agent职责

### Agent1：经营判断

负责数据波动、趋势、强关联、经营问题、观察/动作判断、经营路线和唯一动作族锁定。`act`必须锁定唯一主问题、主动作、责任人和执行对象。

Agent1不生成标题、主图成稿、投放参数成稿或最终SOP。

### Agent2：动作草案

结合Agent1执行锁、垂直类目、平台习惯、商品角色、真实执行对象、参数和权限边界，生成差异化结构草案。

```text
输入：agent_input.agent2_draft.v1
输出：agent2.action_draft.v1
状态：draft_ready / draft_missing_data / draft_conflict / draft_rejected
```

Agent2不生成最终任务标题、完整SOP步骤、公司审批流程或任务生命周期状态，不得增加第二个直接执行目标。

### Agent3：公司化SOP

结合Agent1执行锁、Agent2草案、公司管理风格、品牌审美、审批规则和公司SOP RAG，生成运营可直接执行的高质量SOP。

```text
输入：agent_input.agent3_sop.v1
输出：agent3.sop.v1
状态：sop_ready / sop_missing_data / sop_requires_approval / sop_conflict
```

Agent3不得改变动作族、扩大权限和参数边界，也不得编造执行对象。

### 确定性任务映射

任务映射只把Agent3 SOP转换为Task DTO并执行权限准入：

```text
noMappingLlm = true
compilerAddedStepCount = 0
mappingMode = deterministic_agent3_projection_only
```

映射器不增加、不删除、不重写业务步骤。

## Artifact引用链

业务阶段引用：

```text
signalRef
agent1InputRef
agent1Ref / observationRef / agent1FailureRef
capabilityRef
agent2DraftInputRef
agent2DraftRef
agent3SopInputRef
agent3SopRef
taskMappingRef
taskAdmissionRef
```

V22.5.9执行引用：

```text
agentExecutionInputRef
agentExecutionOutputRef
agentRawBatchOutputRef
batchManifestRef
inputContentHash
outputContentHash
executionHash
itemExecutionId
```

状态机只保存阶段和引用；完整语义内容保存在Artifact Hub。

## 流水阶段

```text
agent1_pending
agent1_running
agent1_completed / observed_soft_gate
agent1_output_invalid / agent1_failed

action_pack_ready
agent2_draft_input_invalid
agent2_running
agent2_draft_ready
agent2_draft_output_invalid
agent2_draft_failed

agent3_sop_running
agent3_sop_ready
agent3_sop_output_invalid
agent3_sop_failed

task_mapped
task_mapping_failed

task_admitted
```

观察商品是合法终态，不进入Agent2、Agent3或任务池。

## Token漏斗

```text
Agent1处理全部准入商品
Agent2只处理Agent1判定需要动作的商品
Agent3只处理通过草案合同的商品
```

三Agent不等于每个商品调用三次。相同`executionHash`的精确重放也执行零次Provider调用。

## 前端Hash View

稳定业务页面不再依赖按路径TTL反复拉取完整接口。

```text
业务状态变化
→ 模块View Artifacts
→ Page Manifest Artifact
→ View Head原子切换
```

前端流程：

```text
GET轻量View Head
→ 比较manifestHash

Hash未变
→ 使用本地不可变缓存
→ 不重复下载
→ 不重复渲染

Hash变化
→ 下载新Manifest
→ 比较模块contentHash
→ 只更新变化模块
```

接口：

```text
GET  /api/view/head/{view_key}
GET  /api/view/artifacts/{artifact_ref}
POST /api/view/refresh
```

旧Manifest可以在新`dataVersion`构建时继续显示，但必须保持旧Hash和旧版本身份，并标记`previous_snapshot`。Hash只负责内容寻址，不能替代租户、用户、角色和店铺范围授权。

## 唯一运行入口

```text
src.api.main:app
```

稳定门面与实现版本：

```text
Worker稳定导入：station_agent_worker_v2255_service
活动Worker元数据：22.5.9
硬接口Facade：agent_runtime_hard_interface_v2255_service
活动Facade版本：22.5.9
下游状态机：22.5.5
Agent1严格执行：agent_token_runtime_hash_exact_v2259_service
```

兼容文件不允许启动影子Worker、第二套队列或旧Agent2→SOP链。

## 发布DNA

```text
精确Git Commit
→ 纯静态合同检查
→ Python 3.6 Bootstrap兼容检查
→ Root Verifier固定权测试
→ FastAPI干净子进程烟雾测试
→ 编译、Shell、前端和pytest真实日志
→ Python 3.11.9精确生产环境
→ requirements.lock依赖闭包
→ 灰度证据语义绑定
→ runtimeFiles
→ attestedFiles
→ testEvidenceFiles
→ testRunHash
→ dependencyLockHash
→ runtimePipFreezeHash
→ pipFreezeHash
→ manifestHash
→ releaseHash
→ GitHub Actions不可变Artifact
→ api.github.com精确Artifact传输
→ ECS固定Root Verifier
→ validated SQLite backup
→ release-data-lineage.json
→ releases/<releaseHash>
→ current原子切换
→ API、Worker、环境、证明和SQLite数据身份共同验明身份
```

服务器运行身份由以下内容共同确定：

```text
sourceCommit
releaseHash
dependencyLockHash
runtimePipFreezeHash
pipFreezeHash
testRunHash
evidenceSemanticVerified
Root Verifier pinned SHA256
SQLite schemaHash / release data lineage
```

## 三类签封文件

### runtimeFiles

正式运行代码、前端、配置、启动与部署脚本、`requirements.lock`和发布Policy。

### attestedFiles

测试、静态检查器、Manifest生成器、Python 3.6兼容检查器、GitHub Artifact传输器、工作流、README和VERSION。

### testEvidenceFiles

CI真实证据位于：

```text
release/attestation/
```

最低集合：

```text
compile-syntax.log
static-contract.log
app-route-smoke.log
pytest.log
production-runtime-verification.json
attested-files.sha256
test-attestation.json
pip-freeze.txt
python-runtime.json
```

## ECS目录

```text
/opt/ai-ecommerce-assistant/
├── releases/<releaseHash>/
├── current -> releases/<releaseHash>/
└── shared/
    ├── .env
    ├── .venv
    ├── data/
    ├── logs/
    ├── outputs/
    └── artifacts/
```

Release目录不可原地修改。数据、日志、密钥和Python虚拟环境通过`shared`复用。

## 部署

正式更新使用已绑定Token的精确签封部署命令：

```bash
sudo deploy-ai-release <40-character-main-commit>
```

底层传输器只接受目标Commit对应的已完成、未过期、成功`push main` Release Artifact。部署器不执行动态`git pull`、`git reset`或`latest`工作树部署。

## 状态接口

```text
GET /api/version
GET /api/health
GET /api/system/release-identity
GET /api/system/data-identity
GET /api/system/agent-pipeline-status
GET /api/view/pipeline-live
GET /api/view/head/{view_key}
GET /api/view/artifacts/{artifact_ref}
```

状态接口必须同时展示公开版本、状态机版本、Agent1输入版本、Hash执行版本和接口文档版本，不能用一个字段覆盖全部层级。

## 当前文档

```text
docs/V22.4.0_RELEASE_HASH_SEAL.md
docs/V22.4.0.7_GITHUB_ARTIFACT_TRANSPORT.md
docs/V22.5.4_SINGLE_DEPLOYMENT_AUTHORITY.md
docs/V22.5.9_INTERFACE_AND_MIGRATION.md
docs/V22.5.9_HASH_DIRECTED_ARTIFACT_RUNTIME.md
docs/V22.5.9_AGENT_BATCH_MANIFEST.md
docs/V22.5.9_FRONTEND_VIEW_ARTIFACT.md
```

历史职责与恢复文档：

```text
docs/V22.5.0_THREE_AGENT_SEMANTIC_PIPELINE.md
docs/V22.5.0_INTERFACE_AND_MIGRATION.md
docs/V22.5.8_AGENT1_EVIDENCE_OUTPUT_CONTRACT.md
docs/V22.5.8_DEPLOYMENT_AND_RECOVERY.md
```

## MVP边界

当前不加入Merkle Tree、Ed25519发布签名或蓝绿双实例。系统继续使用轻量Hash签封、不可变Artifact、精确执行索引、单实例Worker、SQLite部署血缘和自动回滚。