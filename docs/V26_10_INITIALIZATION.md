# V26.10 业务初始化与验收收口

当前是实现候选，不声明 ECS 已部署或真实模型全链通过。

## 更新框架

沿用 `governance/update-requests/v269-current.json` 作为当前请求入口，requestId 已升级为 V26.10。先登记 business_initialization 责任模块及精确路径，再运行原 compile_v26_update_locator。未新增权限根或 Agent runner。当前模块不自动加入 requiredModules，激活应以最终门禁与部署证据为准。

## 数据范围

本批初始化读取现有下载接口同源的 ERA 三份 XLSX：每期 10 个商品 × 3 个店铺，共 30 个经营单元；商品、店铺、流量三张工作表分别为 30/3/150 行。源快照保留商品完整原始行、所有工作表表头/行数/内容哈希、XLSX 哈希和显式字段映射。3 个商品的小型信号场景仍只用于三报表链路回归，不冒充完整商品库。所有样本均是合成测试数据，真实经营样本量为零。

编译命令：

```sh
python scripts/compile_v2610_initialization.py
python scripts/compile_v2610_initialization.py --check
```

配置中登记报表字段、单位、缺失规则、货币、时区、公式、参数覆盖顺序（profile → category → store）。当前支持相对波动最低阈值的类目/店铺覆盖，其余参数不能任意注入。新企业口径必须编译新配置版本，不能让 Agent 修改系统公式。

前两份报表生成每个经营单元的数值基线、变化量、相对变化量及初始阈值；第三份只作为留出数据。调整第三期数据改变 bundleHash，但不能改变 initializationHash 和方法内容。两期只能形成 provisional 阈值，不能宣称稳定统计规律。

报表 ROI 原值保留。`revenue / ad_spend` 是独立派生比值，不能当作报表 ROI，更不能宣称归因广告收入。`clicks / traffic` 同样只保留字面分母含义，不把 traffic 自动解释为曝光量。

## 初始化与方法知识

`GET /api/ops/initialization` 只读预览清单与基线，不写数据库。

`POST /api/ops/initialization` 要求 `explicitOperatorIntent=true` 和当前精确 `bundleHash`，安装既有 Experience/Promotion 表、保存不可变初始化包并写入 90 条方法知识。返回 experienceIds，供原有审核与启用 API 操作。

初始化方法仍存于既有 Experience Store 的 experience_knowledge 域，source_type 保持 seed，sampleCount=0，不能转成历史成功案例。新增的 bundle/membership 表仅证明注册方法的来源和内容，不拥有新的知识生命周期。

流程为 initialization → candidate → 既有 C reviewer approve → 既有 C operator enable。系统不自动审核、不自动启用。普通 Seed 即使伪造 knowledgeType 也不能通过：Gate 必须验证已安装清单、方法成员关系、payload/applicability 哈希和零真实样本。

正式检索现在允许两类已启用记录：runtime experience，以及精确登记并经 C 审核启用的初始化方法。方法必须匹配 Agent、店铺、商品以及查询包含的类目/平台；没有业务范围不返回方法。撤回立即退出检索，Head 变化沿用现有缓存身份。重复初始化不会复活已撤回的方法。

## SOP 与评测

初始化知识在实际进入 knowledgeContext 后冻结为 PRESET 数据卡，显示方法、前两期基线、公式、输入引用及 profileHash。历史卡片不会随配置更换而重算。

任务详情页补上 B 已有 evaluation 接口的展开渲染：指标值、单位、公式版本、输入、观察窗口、样本量、缺失原因与解释边界。没有执行数据时不填写完成率、收益或回滚率。

## 收口修复

主分支通用三报表工作流此前仍调用旧 history-warmup 入口；改为调用现有 V26.9 图谱 wrapper，保留原完整三报表、恢复和终态检查。是否解决远端失败须由新提交的 self-hosted 证据确认，不能用入口修改代替验收结果。

B/C gate 对当前 requestId 的旧版本前缀断言改为验证既有 request schema 与 policy profile，继续执行原业务边界测试。V26.9 B/C 文档顶部同步已合并的 active 状态，历史阶段说明保留。

## 验收范围与后续

本批测试覆盖：精确重编译、留出隔离、报表口径差异、幂等安装、无假结果、审核/启用分离、作用域隔离、撤回与 Head 改变、伪造 Seed 拒绝、SOP 冻结和缓存重投影。

完整 V26.10 收口还需要：店铺汇总与流量来源明细的独立评测配置扩展，企业目标及观察窗口等更完整的参数继承，真实模型与 ECS 部署验收，以及执行后评测→审核→启用→下一任务召回的现场证据。不得仅以候选测试通过宣称完成这些阶段。

### 精确包资源闭包修复

三报表候选运行 `34773620861` 的失败证据确认：Agent1 输入构建读取
`rag/manifest/v269b_manifest.json` 时文件缺失，尚未调用 Provider。
原因是公开运行 scope 的模块列表仍停留在旧链路，遗漏已经启用的经验和回流模块。
V26.10 将 Experience Store、Evaluation、Retrieval、Promotion、Initialization
纳入 scope 的必需注册模块；六个已注册 RAG 静态文件按精确路径准入。
运行数据库、运行经验和备份不进入发布包。
新增回归从真实血缘选择集复制静态资源，在隔离数据库执行建表、迁移及初始化，
防止源码目录测试通过而精确发布包缺文件。远端三报表重跑结果另行验收。

后续运行 `34774052503` 已通过全部基础三报表断言和四段持久化调度证明。
剩余失败来自旧探针仍要求 `candidate_not_activated`，与主分支已启用的合同冲突。
探针和工作流同步改为严格要求 `active`，保留多域拆分、依赖、冲突、旧字段污染
以及重复执行断言；经验模块的声明状态与本测试是否实际验证回流分开记录。
