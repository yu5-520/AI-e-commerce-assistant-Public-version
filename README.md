# AI 电商公开版

面向电商业务的受治理多 Agent 决策运行系统。

当前架构由 **Java V24 确定性控制平面**、**Python / LLM 智能计算平面**、**结构化 Agent 运行链** 与 **V25 知识平面** 组成。

> 当前 Runtime：V24 Authority Runtime + V25 Knowledge Plane

---

## 1. 系统架构

```text
                    经营数据
                       │
                 标准事实层
                       │
                       ▼
┌──────────────────────────────────────────────┐
│           Java V24 确定性控制平面            │
│                                              │
│ Authority Generation                        │
│ Gate Engine                                  │
│ Queue Authority                              │
│ Task State Authority                         │
│ Generation Fencing                           │
│ 信息 / 调用 / 时间 / 执行权限                 │
└──────────────────────┬───────────────────────┘
                       │ Authority Envelope
                       ▼
┌──────────────────────────────────────────────┐
│          Python / LLM 智能计算平面            │
│                                              │
│ Agent1 / Agent2 / Agent3                     │
│ LLM / RAG / Semantic Reasoning               │
└──────────────────────┬───────────────────────┘
                       │ Proposal Artifact
                       ▼
┌──────────────────────────────────────────────┐
│              Java 确定性验收                 │
│                                              │
│ Schema / Hash / Generation / Authority       │
│ Allowed Edge / Temporal Scope / Gate         │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
                任务 / 验收 / 复盘 / 回流
```

核心原则：

> **系统拥有调用图，AI 只拥有节点内判断权。**

---

## 2. 核心技术模型

### 真实性偏差 → 权限穿透模型

| 真实性偏差 | 系统行为 | 权限穿透 | 工程控制 |
| --- | --- | --- | --- |
| 补完式偏差 | 根据上下文补全缺失信息 | 信息权限穿透 | Information Authority |
| 完美式偏差 | 为完整完成任务扩大 Agent 调用范围 | 调用权限穿透 | Invocation Authority |
| 回溯式偏差 | 回看旧任务并重新激活执行链 | 时间权限穿透 | Temporal Authority |

模型能力可以增强推理质量，但事实边界、调用边界、时间边界和执行边界仍由确定性系统控制。

```text
Generative Completion Pressure
            │
            ▼
   Reality Fidelity Bias
       真实性偏差
            │
   ┌────────┼────────┐
   │        │        │
补完式    完美式    回溯式
   │        │        │
信息穿透  调用穿透  时间穿透
   └────────┼────────┘
            ▼
    Authority Penetration
            │
            ▼
Java V24 Deterministic Control Plane
```

---

## 3. Java V24 确定性控制平面

```text
java-control-plane/
└── src/main/java/com/zcentury/v24/
    ├── AuthorityGenerationStore.java
    ├── GenerationFencer.java
    ├── GateEngine.java
    ├── QueueAuthority.java
    ├── TaskStateAuthority.java
    ├── CompatibilityAuthority.java
    ├── DeploymentAuthority.java
    ├── FrontendViewAuthority.java
    ├── V25RetrievalAuthority.java
    ├── V25KnowledgeCompositionAuthority.java
    └── V25KnowledgeDomainAuthority.java
```

| 组件 | 工程职责 |
| --- | --- |
| `AuthorityGenerationStore` | 权限代际、单写者、CAS、状态持久化、Rollback |
| `GenerationFencer` | 旧 Generation 写入隔离与 Fencing |
| `GateEngine` | Fail-Closed 确定性门控 |
| `QueueAuthority` | Agent Stage Claim / Lease / Handoff / Retry / Idempotency |
| `TaskStateAuthority` | 任务生命周期状态权 |
| `CompatibilityAuthority` | Python / Java 兼容与迁移边界 |
| `DeploymentAuthority` | 部署 Authority 与生产切换边界 |
| `V25RetrievalAuthority` | 知识检索 Authority |
| `V25KnowledgeCompositionAuthority` | 知识组合与上下文构造 Authority |

Java 控制平面负责确定性状态、权限、队列、生命周期、Generation、Fencing 与最终验收；Python / LLM 不拥有最终 Authority。

---

## 4. Python / LLM 智能计算平面

Python 侧保留模型与业务语义计算能力：

```text
LLM Provider
RAG
Embedding
Agent 业务判断
Prompt / Output Parsing
Semantic Reasoning
业务快速迭代
```

运行边界：

```text
Python != Authority Owner
Python != Workflow Owner
Python != State Authority
Python != Execution Authority
```

Python / LLM 在 Java 授予的事实、调用、时间与执行范围内完成推理，并输出 Proposal Artifact 交由确定性控制平面验收。

---

## 5. Agent 业务运行链

```text
经营报表
   ↓
事实清洗 / 商品快照
   ↓
信号准入
   ↓
Agent1：经营判断
   ↓
动作族锁定
   ↓
Agent2：动作方案
   ↓
Agent3：企业 SOP
   ↓
确定性任务映射
   ↓
任务池
   ↓
执行 / 验收 / 复盘
```

### Agent 运行硬约束

```text
完整 Artifact 直接读取      : 禁止
Agent 输入 fallback          : 禁止
模型动态扩张调用图           : 禁止
跨动作族重写                 : 禁止
LLM 二次任务映射             : 禁止
旧 Generation 写入           : 禁止
未审核知识直接进入 Agent RAG : 禁止
```

---

## 6. 权限边界

### Information Authority

控制 Agent 可以读取、推理和使用哪些信息。

```text
Source Artifact
      ↓
Field / Ownership Registry
      ↓
System Projection
      ↓
Agent Input Ref
      ↓
Agent
```

关键约束：

- 仅允许注册字段进入 Agent 投影；
- 输入必须绑定 Source Artifact / Content Hash；
- 完整 Artifact 不直接进入模型上下文；
- 推理结果不能自动晋升为事实或执行参数；
- 缺失权限参数时 Fail Closed，不按动作族或上下文猜测。

### Invocation Authority

控制当前任务允许进入哪些 Agent、Stage 和调用边。

```text
Allowed Stage / Edge
        ↓
Queue Authority
        ↓
Agent Node
        ↓
Deterministic Handoff
```

模型可以提出建议，但不能自行增加 Agent、Stage 或调用边。

### Temporal Authority

控制已完成任务的历史访问与重新执行边界。

```text
Task T1
  ↓
Frozen Evidence
  ↓
RETROSPECTIVE_READONLY
  ↓
Recap / Knowledge Candidate
```

旧任务不直接重新获得执行权；新的行动应生成新的任务、Execution Identity 与 Authority Generation。

---

## 7. 工程能力

| 工程能力 | 实现方式 | 状态 |
| --- | --- | ---: |
| 确定性执行身份 | ExecutionHash / Hash | ✅ |
| 不可变节点传输 | Artifact Ref | ✅ |
| 默认拒绝门控 | Java GateEngine | ✅ |
| 单写者 Authority | Authority Generation | ✅ |
| 旧代写入隔离 | Generation Fencing | ✅ |
| 重复执行抑制 | Idempotency Key | ✅ |
| Agent 输入隔离 | Hard Input Contract | ✅ |
| 动作族锁定 | Agent Runtime Contract | ✅ |
| 最终任务确定性映射 | Agent3 Projection | ✅ |
| 历史证据冻结 | dataVersion / frozenAt | ✅ |
| 知识不可变 Revision | Knowledge Revision | ✅ |
| 人工审核知识回流 | Human Review Gate | ✅ |
| Hash / Lineage 审计 | Registry / Governance | ✅ |

---

## 8. V25 统一知识平面

### 检索链

```text
Query / Agent Need
        ↓
字段约束
        ↓
结构化过滤
        ↓
语义检索
        ↓
Retrieval Receipt
        ↓
Agent Context
```

### 知识回流链

```text
任务结果
   ↓
复盘
   ↓
Knowledge Candidate
   ↓
人工审核
   ↓
Immutable Revision
   ↓
Knowledge Index
   ↓
未来 Agent Retrieval
```

未审核的 `pending_review` 经验不进入生产 Agent 检索。

---

## 9. 仓库结构

```text
.
├── java-control-plane/   # Java 确定性控制平面
├── src/                  # Python 智能计算与业务服务
├── contracts/            # 字段 / 接口 / Ownership 注册表
├── governance/           # Hash 血缘、Authority 与治理证据
├── config/               # Runtime / Registry 配置
├── fixtures/             # 脱敏测试与公开数据
├── release/              # 发布、校验与运行证明
├── tests/                # Contract / Runtime / Regression 测试
├── web_demo/             # 演示前端
└── docs/                 # 架构与技术文档
```

---

## 10. 版本演进

```text
V22  Python Agent 稳定链
V23  Hard Interface / Registry / Artifact
V24  Java Deterministic Authority Runtime
V25  Unified Knowledge Plane
```

当前重点：

- Java Authority Runtime；
- Python / Java Authority 边界；
- Information / Invocation / Temporal Authority；
- Agent Hard Interface；
- Hash / Artifact / Generation / Fencing；
- V25 Knowledge Retrieval / Revision / Review。

---

## 11. 构建、运行与验证

### 环境

- Java 17+
- Python 3.x
- FastAPI
- SQLite / Runtime Adapter
- LLM Provider

仓库中的具体构建、运行、测试与部署脚本以当前版本目录和 `release/`、`scripts/`、`.github/workflows/` 中的实现为准。

公开版重点保留：

- 可运行业务链；
- Java Control Plane；
- Agent Contract；
- Hash / Artifact / Lineage；
- Knowledge Plane；
- 测试与验证证据。

---

## 12. 技术说明与授权

本仓库公开 AI 电商业务实现、工程设计与部分治理能力。

通用 Z-Century 技术体系的独立实现、完整治理框架及未授权能力不属于本仓库公开范围。

请参阅：

- [`LICENSE`](./LICENSE)
- [`Z_CENTURY_TECHNOLOGY_NOTICE.md`](./Z_CENTURY_TECHNOLOGY_NOTICE.md)

---

## 13. 联系方式

**商务联系**  
225447370@qq.com

**技术探索 / 交流**  
2254473740
