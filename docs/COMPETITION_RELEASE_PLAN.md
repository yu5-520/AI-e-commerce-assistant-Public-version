# 比赛版筛选、部署与验证计划

本文把比赛版从“公开仓代码集合”固定为“可追溯、可重复部署、可验证的唯一稳定产品链路”。

执行主线：

```text
README 对外合同
→ 统一注册表盘点
→ 哈希血缘图追踪
→ 唯一比赛链路筛选
→ 白名单发布包
→ 阿里云 ECS 部署
→ 多轮验证与细节修正
→ 最终版本冻结
```

---

## 目标

### 对外目标

- 评委可以快速理解项目解决的问题；
- 评委可以从报表上传走到任务与复盘；
- 阿里云 ECS、百炼与通义千问的真实使用位置清晰可见；
- 商业模式、母仓能力与未来战略被完整展示；
- 当前运行、母仓已验证、企业配置和未来试点严格区分。

### 技术目标

- 比赛仓只存在一个生产入口；
- 只保留一条报表到任务的稳定业务主链；
- 不存在旧 Worker、旧 Agent2→SOP 链或第二套任务状态机；
- 所有比赛运行文件由白名单产生；
- 所有文件有来源、内容 Hash、依赖和公开状态；
- ECS 部署绑定精确 Commit 与发布清单；
- 同一发布包可以重复部署并产生一致身份。

### 安全目标

- 企业 RAG 配方、权限规则和客户适配逻辑不进入公开包；
- ERP、内部数据库和私有化部署实现不进入公开包；
- Z 架构、母仓自更新与自修复能力不进入比赛仓；
- API 密钥、数据库地址、内部路径和运行数据全部外置；
- 公开仓不依赖“隐藏按钮”保护私有实现。

---

## 阶段 0：冻结母仓基准

在开始筛选前记录：

```text
motherRepository
motherBranch
sourceCommit
sourceCommitTime
registryVersion
lineageGraphVersion
competitionExtractionVersion
```

冻结只限制比赛抽取基准，不阻止母仓继续开发。比赛版后续修复必须明确属于：

1. 母仓同步修复；
2. 比赛适配层修复；
3. 比赛前端与展示修复。

禁止在 ECS 运行目录中手工修改后不回写仓库。

---

## 阶段 1：README 对外合同

README 第一版先确定：

- 一句话价值；
- 客户问题；
- 比赛版唯一稳定链路；
- 阿里云技术栈；
- 三 Agent 职责；
- 商业模式；
- 母仓与比赛仓边界；
- 能力状态矩阵；
- 比赛后试点与商业飞轮。

此阶段不写死尚未完成最终部署验证的数据，例如：

- 在线公开地址；
- 平均运行耗时；
- 最终测试通过数量；
- 最终发布 Hash；
- 真实客户经营提升数据。

ECS 验证完成后再进行 README 第二次定稿。

---

## 阶段 2：统一注册表盘点

注册表以真实路径和可验证依赖为依据，不按文件名相似度猜测。

### 最低字段

```text
componentId
filePath
fileType
capability
runtimeEntrypoint
upstreamComponentIds
downstreamComponentIds
imports
routeBindings
artifactSchemas
publicStatus
secretLevel
runtimeRequired
evidenceType
contentHash
sourceCommit
version
ownerModule
legacyState
```

### 公开状态

```text
PUBLIC_RUNTIME
PUBLIC_EVIDENCE
MOTHER_VALIDATED
ENTERPRISE_CONFIG
PRIVATE_CORE
REMOVE_OR_LEGACY
```

### 输出

- `registry-snapshot.json`
- `runtime-entrypoints.json`
- `public-capabilities.json`
- `private-capabilities.json`
- `legacy-candidates.json`
- `registry-errors.json`

存在下列情况时不得进入下一阶段：

- 同一路由绑定多个活动实现；
- 同一 Worker 名称出现多个可启动入口；
- 同一能力存在两个活动状态机；
- 运行文件没有来源 Commit 或内容 Hash；
- 公开状态为空；
- 依赖指向不存在文件。

---

## 阶段 3：哈希血缘图追踪

从真实生产入口向下追踪，而不是扫描全仓后凭名称选择。

### 根节点

```text
src.api.main:app
报表上传路由
数据导入服务
经营证据生成
Agent1 输入构造与执行
Agent2 草案构造与执行
Agent3 SOP 构造与执行
确定性任务映射
任务准入与生命周期
前端比赛入口
百炼 Provider
Artifact 存储与执行索引
```

### 需要追踪的边

```text
IMPORTS
CALLS
ROUTE_BINDS
READS_ARTIFACT
WRITES_ARTIFACT
PRODUCES_SCHEMA
CONSUMES_SCHEMA
STARTS_WORKER
ENQUEUES
DEQUEUES
READS_CONFIG
SERVES_FRONTEND
DEPLOY_REQUIRES
TEST_COVERS
```

### 节点身份

```text
nodeHash = Hash(
  filePath + contentHash + capability + sourceCommit + publicStatus
)
```

### 链路准入规则

进入比赛运行白名单的节点必须同时满足：

1. 可以从生产根节点到达；
2. 属于唯一比赛业务主链；
3. `publicStatus == PUBLIC_RUNTIME`；
4. `runtimeRequired == true`；
5. 不经过 `PRIVATE_CORE` 节点；
6. 不依赖 `REMOVE_OR_LEGACY` 节点；
7. 所有 Schema 和 Artifact 引用闭合；
8. 对应测试或烟雾证明存在。

### 输出

- `lineage-graph.json`
- `competition-runtime-nodes.json`
- `competition-runtime-edges.json`
- `unresolved-dependencies.json`
- `private-boundary-crossings.json`
- `shadow-entrypoints.json`

---

## 阶段 4：唯一稳定链路筛选

### 目标链路

```text
报表 Artifact
→ 最近五份事实比较与历史趋势
→ operatingEvidenceGraph.v1
→ signalRef
→ agent1InputRef
→ executionHash / itemExecutionId
→ Agent1 输出 Artifact
→ agent2DraftInputRef
→ Agent2 动作草案
→ agent3SopInputRef
→ Agent3 执行 SOP
→ 确定性任务映射
→ 任务准入、执行、验收与复盘
```

### 必须排除

- 旧 Agent2 直接生成最终 SOP 的链路；
- 兼容文件启动的影子 Worker；
- 第二套队列消费者；
- 旧缓存拥有业务结果重放权的链路；
- 可以把旧结果重绑新报表或新商品的实现；
- 未使用 `itemExecutionId + inputContentHash` 的批次匹配；
- Mapping LLM；
- 不经过任务准入的直接写库路径；
- 企业增值接口和母仓治理能力。

### 三类白名单

#### 运行白名单

比赛产品运行必需的代码、前端、配置、依赖和启动文件。

#### 证明白名单

README、架构说明、脱敏测试、接口合同、发布证据和演示材料。

#### 外置清单

环境变量、密钥、数据库、日志、上传报表、运行 Artifact 和服务器目录。

---

## 阶段 5：精准迁移与发布包

比赛包由白名单生成，不先复制整个母仓再删除。

### 发布 Manifest

```json
{
  "releaseType": "competition-public-runtime",
  "sourceRepository": "<mother repo>",
  "sourceCommit": "<40-char sha>",
  "competitionRepository": "yu5-520/AI-e-commerce-assistant-Public-version",
  "competitionCommit": "<40-char sha>",
  "registryVersion": "<version>",
  "lineageGraphVersion": "<version>",
  "runtimeFileCount": 0,
  "evidenceFileCount": 0,
  "entrypoint": "src.api.main:app",
  "provider": "aliyun-bailian",
  "modelFamily": "qwen",
  "manifestHash": "<sha256>",
  "releaseHash": "<sha256>"
}
```

### 必需文件

```text
release-manifest.json
runtime-files.sha256
evidence-files.sha256
public-capabilities.json
excluded-capabilities.json
lineage-summary.json
requirements.lock
source-identity.json
deployment-entrypoint.txt
```

### 校验顺序

```text
先迁移白名单文件
→ 再计算比赛仓文件 Hash
→ 与来源注册表 Hash 比对
→ 检查新增文件是否全部有准入记录
→ 检查排除文件是否不存在
→ 生成最终发布 Hash
```

不在传输前对每个文件反复人工判断；边界判断由注册表和血缘图完成，迁移后统一进行全量 Hash 验证。

---

## 阶段 6：阿里云 ECS 不可变部署

### 目录建议

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

### 部署流程

```text
接收精确比赛 Commit
→ 下载目标发布包
→ 校验 releaseHash 与文件 Hash
→ 建立新 releases/<releaseHash>
→ 安装 requirements.lock
→ 注入 shared/.env
→ 验证百炼连接
→ 初始化或迁移数据库
→ 启动候选实例
→ 健康检查
→ 主链烟雾测试
→ current 原子切换
→ 保留上一版本用于回滚
```

### 环境变量边界

ECS 外置：

- 百炼 API Key；
- 模型名称与 Endpoint；
- 数据库地址；
- Redis / 队列配置；
- 域名、HTTPS 和反向代理配置；
- 用户与演示账号；
- 日志和 Artifact 存储路径。

不得进入比赛仓：

- `.env`；
- 密钥示例中的真实值；
- ECS 内部 IP；
- 数据库备份；
- 用户上传数据；
- 生产日志正文。

---

## 阶段 7：验证矩阵

### A. 发布身份验证

- Commit 与 Manifest 一致；
- runtimeFiles Hash 一致；
- requirements.lock 一致；
- Provider 为阿里云百炼；
- 当前模型为通义千问系列；
- API、Worker 和前端来自同一 releaseHash；
- ECS 重启后身份不漂移。

### B. 基础运行验证

- `/api/health`；
- `/api/version`；
- Release Identity；
- Data Identity；
- 前端静态资源；
- 数据库连接；
- Artifact 存储；
- Worker 启动；
- 百炼调用。

### C. 唯一主链验证

固定脱敏报表至少连续运行多次：

```text
上传
→ 数据清洗
→ 商品趋势
→ 经营证据
→ Agent1
→ 观察终态 / 动作准入
→ Agent2
→ Agent3
→ 任务映射
→ 任务详情
→ 生命周期
→ 复盘
```

每次记录：

- 总耗时；
- 每阶段耗时；
- 商品数量；
- 观察数量；
- 动作数量；
- Agent 调用次数；
- 重试次数；
- executionHash；
- acceptedOutputRef；
- 任务数量；
- 页面状态。

### D. 重复性与重放验证

- 同一 executionHash 不重复调用模型；
- 同一输入定位同一不可变输出；
- 输入变化生成新 executionHash；
- 不出现旧结果重绑；
- 不出现重复任务；
- 商品输出不串线；
- 单商品失败不导致全批重跑。

### E. 异常验证

- 报表格式错误；
- 必需字段缺失；
- 百炼超时；
- 模型返回非法 JSON；
- 模型漏商品；
- 模型返回重复 ID；
- 批次外 ID；
- Worker 中断；
- 服务重启；
- 前端刷新；
- 数据库短暂失败。

失败必须满足：

- 状态明确；
- 错误可读；
- 可以精确重试；
- 不污染旧 Artifact；
- 不把失败显示为完成；
- 不生成半成品任务。

### F. 权限与安全验证

- 越权用户无法读取其他店铺；
- 无权限任务不进入任务池；
- Agent3 不扩大权限；
- Mapping 不增加步骤；
- API Key 不出现在页面、日志和仓库；
- 公开包不存在母仓私有文件；
- Git 历史不存在从母仓直接复制的敏感提交。

### G. 评委体验验证

由不了解系统的人独立完成：

1. 理解产品解决的问题；
2. 找到演示入口；
3. 上传正确样例；
4. 看到当前执行阶段；
5. 找到 Agent 判断；
6. 理解为什么生成或不生成任务；
7. 打开 SOP 和任务详情；
8. 完成一次生命周期操作；
9. 理解商业扩展边界。

任何一步需要开发者现场解释，均记录为 README、页面文案或交互待修项。

---

## 阶段 8：细节修复原则

验证阶段只允许三类修改：

### 稳定性修复

- 运行断点；
- 错误状态；
- 超时与重试；
- 数据串线；
- 重复任务；
- 部署与回滚。

### 评委路径修复

- 上传提示；
- 当前阶段；
- 结果入口；
- SOP 可读性；
- 空状态；
- 错误说明；
- 移动端展示。

### 比赛材料修复

- README；
- 架构图；
- 能力状态矩阵；
- 演示视频；
- 测试证据；
- 商业计划。

冻结阶段不引入：

- 新部门链路；
- 新 ERP 接口；
- 新 RAG 类型；
- 第二条业务主链；
- 新模型供应商；
- 大型基础架构升级。

---

## 阶段 9：README 第二次定稿

最终 README 只写已经有对应证据的运行事实：

- 公开体验地址；
- 最终比赛版本；
- releaseHash；
- 阿里云 ECS 部署状态；
- 百炼千问调用位置；
- 演示步骤；
- 测试通过情况；
- 平均主链耗时；
- 当前已知边界；
- 母仓已验证能力；
- 企业配置能力；
- 首批试点计划。

每项声明必须至少对应一种证明：

```text
公开网页
公开代码
公开测试证据
脱敏母仓验证摘要
明确标注的企业配置能力
明确标注的试点目标
```

---

## 完成标准

比赛版满足以下条件后才允许冻结：

- README 首屏清楚说明产品、价值和阿里云技术栈；
- 唯一生产入口已确认；
- 注册表无未解析运行依赖；
- 血缘图无影子 Worker 和第二主链；
- 公开运行白名单完整；
- 私有能力排除清单通过；
- 发布包 Hash 全量一致；
- ECS 可以从空发布目录重复部署；
- 固定报表多次完整跑通；
- 同 Hash 重放不重复调用模型；
- 异常不会污染任务和 Artifact；
- 移动端和桌面端评委路径均可完成；
- README、视频、架构图与实际运行一致；
- 最终版本冻结后只接受 P0 故障修复。

---

## 推荐执行顺序

```text
1. 合并 README 首页与状态矩阵
2. 冻结母仓来源 Commit
3. 导出统一注册表快照
4. 生成哈希血缘图
5. 审查影子入口和私有边界穿越
6. 输出比赛运行白名单
7. 精准迁移并进行全量 Hash 校验
8. 生成不可变比赛发布包
9. 部署到阿里云 ECS 候选目录
10. 完成基础、主链、重放、异常、权限测试
11. 根据评委路径修复页面与文案
12. 再次完整回归
13. 冻结最终 releaseHash
14. 回写 README 最终运行数据
15. 发布公开体验、演示视频与比赛材料
```
