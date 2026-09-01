# Z-Century 技术授权声明

本仓库中的部分通用系统架构来源于 **Z-Century（Z世纪）技术体系**，并以授权方式集成到 AI 电商比赛/产品项目中。

## 授权对象

本声明覆盖两类已登记的 Z 派生通用技术能力：

### 1. V24 Authority Runtime / Durable Authority Generation Controller

主要包括：

- release 外持久化的 Authority Generation 状态治理；
- Compare-And-Set 与 single-writer 权限迁移治理；
- generation sequence / generation hash / fencing token 身份结构；
- file lock、fsync、canonical state hash 与 atomic rename；
- restart recovery 与 tamper rejection；
- proof-gated prepare / rollback；
- 在运行桥接未封装完成前保持 fail-closed、禁止 authority activation；
- authority owner / cutover preparation 与 stale-generation rejection。

### 2. V25 Unified RAG / Knowledge Plane

主要包括：

- 统一知识与 RAG 字段治理；
- 字段优先、结构化过滤与语义补充的检索治理结构；
- Knowledge Revision 与人工审核治理；
- Knowledge Lifecycle Authority；
- Knowledge Index Manifest / Head；
- Retrieval Receipt 与 Knowledge Lineage；
- RAG Quantification / Retrieval Observability；
- 版本化 EvalSet、EvalRun 与 Regression Gate；
- Knowledge Center 的治理与投影边界。

上述通用结构属于由 **Z-Century（Z世纪）技术体系及其适用权利人**提供并授权本项目使用的技术能力。相关实现被复制、集成、构建、部署、运行或公开展示于本仓库，**不构成知识产权转让（Integration / Publication ≠ Assignment）**。

## AI 电商项目取得的权利

AI-e-commerce-assistant-Public-version 在登记授权范围内取得有限、非独占、不可转让的项目使用权，可用于：

- 比赛与公开演示；
- 产品验证；
- AI 电商项目自身的授权运行；
- 在 Z 派生框架仍嵌入本授权项目范围内的商业交付。

本项目**不因持有代码副本、公开仓库、Fork、构建产物或运行实现而取得**上述 Z 派生通用框架的：

- 所有权；
- 转让权；
- 再许可权；
- 再授权权；
- standalone redistribution 权限；
- 独立拆分销售或作为第三方项目底层框架再次授权的权利。

如需将 Z 派生通用框架独立用于其他产品、公司、客户项目或第三方技术授权，应取得适用权利人的另行书面授权。

## 不属于本授权声明所收归的 AI 电商项目资产

本声明不把 AI 电商项目自身独立形成的业务资产归入 Z 通用框架。下列内容按其自身权利来源和项目边界处理：

- 电商业务逻辑与业务规则；
- 项目特定 Agent 提示词、经营判断规则与 SOP 内容；
- 业务数据、客户数据和脱敏比赛数据；
- 项目特定生产路径、服务名称与部署拓扑；
- 项目特定页面文案、产品展示和交互实现；
- 不构成 Z 派生通用框架的独立电商工作流实现。

因此，本项目的业务 IP 与 Z-Century 通用技术 IP 采用明确分层：

```text
Z-Century 通用技术体系
        ↓ 有限授权
V24 Authority Runtime + V25 Knowledge Plane
        ↓ 业务集成
AI 电商 / 智策产品实现
```

## 双向授权证据

### V24 Authority Runtime

统一授权标识：

`Z-AUTH-AIECOM-V24-AUTHORITY-RUNTIME-001`

消费者侧机器可读记录：

`governance/ip/z-century-v24-authority-runtime-license.json`

授权源侧记录：

`yu5-520/Z-Century` → `licenses/authorized-projects/AI_ECOMMERCE_V24_AUTHORITY_RUNTIME.json`

Z-Century 正式主线授权记录 merge commit：

`3412619dfd904db7870f81f8079724ab574c9754`

### V25 Knowledge Plane

统一授权标识：

`Z-AUTH-AIECOM-V25-KNOWLEDGE-PLANE-001`

消费者侧机器可读记录：

`governance/ip/z-century-v25-technology-license.json`

授权源侧记录：

`yu5-520/Z-Century` → `licenses/authorized-projects/AI_ECOMMERCE_V25_KNOWLEDGE_PLANE.json`

Z Trusted Runtime 签封并发布的授权 release commit：

`c63439d621a43c28928238d27e38ab89fbf76ef7`

Z-Century 正式主线授权记录 merge commit：

`8df552c01057d4a834ba7c3481042faeb0c5233a`

对应的 Z 授权证据由 `.z-authority/mutation-envelope.json` 提供，并已通过 public-key Mutation Authority Gate 验证。

## 与顶层公开授权的关系

本仓库顶层 `LICENSE` 只授予其中明确列出的 source-available 权利。它不会扩大上述 Z 派生技术的授权范围，也不会把公开阅读、Clone、Fork 或评审行为解释为 Z 通用框架的再许可。

## 法律与主体边界

本声明用于建立仓库级技术来源、使用范围和知识产权边界证据，不把 GitHub 仓库名称本身视为独立法律主体，也不替代未来根据具体公司主体、商业交易或司法辖区签署的正式技术许可合同。

除非适用权利人通过书面文件明确转让，Z 派生通用框架相关权利不因本项目的集成、比赛提交、部署、复制、公开展示或商业运行而自动转移。
