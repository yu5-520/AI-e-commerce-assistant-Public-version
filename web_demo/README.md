# Web Demo

这是 AI + RPA + ERP + CRM 电商经营工作台的前端产品原型。

当前页面已经从单页流程演示升级为带侧边栏的工作台结构，包含：

```text
经营总览
数据导入
AI 诊断
任务中心
审批中心
报告中心
知识库
运行日志
系统状态
```

页面入口加载：

```text
web_demo/app-v2.js
```

`app-v2.js` 是当前稳定入口，用于承接日志筛选、系统状态和清空 Demo 数据等产品操作。

## 运行方式一：API 模式

在仓库根目录安装依赖：

```bash
pip install -r requirements.txt
```

启动服务：

```bash
uvicorn src.api.main:app --reload
```

然后打开：

```text
http://127.0.0.1:8000/
```

或：

```text
http://127.0.0.1:8000/web_demo/index.html
```

页面会优先调用 FastAPI。

## 运行方式二：本地样例模式

直接用浏览器打开：

```text
web_demo/index.html
```

这种方式不会调用 API，只展示内置样例数据。

## 当前页面模块

### 经营总览

展示商品诊断数量、客户分层数量、任务草案数量和待人工确认数量。

### 数据导入

展示当前 Mock ERP / CRM 数据源、字段校验结果、数据关系校验结果和导入记录。

API 模式下调用：

```text
GET  /api/data/sources
POST /api/data/validate
POST /api/data/import/mock
GET  /api/data/imports
```

### AI 诊断

展示商品诊断、客户分层和风险等级。

### 任务中心

展示由 AI 诊断生成的 RPA 任务草案，并读取 SQLite 中的 `task_status` 持久化状态。

API 模式下调用：

```text
GET /api/tasks
```

### 审批中心

支持任务确认 / 拒绝，并展示 SQLite 中的 `approval_records` 审批历史。

API 模式下调用：

```text
GET  /api/approvals/records
POST /api/approvals/{task_id}/approve
POST /api/approvals/{task_id}/reject
```

### 报告中心

展示 SQLite 中的 `report_records` 报告记录，并读取 Markdown 报告内容。

API 模式下读取：

```text
GET /api/reports
GET /api/reports/demo
```

### 知识库

展示当前 RAG 召回依据。

### 运行日志

展示 WorkflowRun 和 ExecutionLog，并支持筛选、分页、按 workflow_run_id 查看节点详情。

API 模式下调用：

```text
GET /api/logs/workflow-runs?limit=20&offset=0&workflow_type=full_mock_workflow&status=success
GET /api/logs/execution-logs?limit=20&offset=0&status=success
GET /api/logs/workflow-runs/{workflow_run_id}/execution-logs?limit=100&status=success
```

### 系统状态

展示 SQLite 文件、表数量、记录数、文件大小和每张表的最近更新时间。

API 模式下调用：

```text
GET  /api/system/db-status
POST /api/system/clear-demo-data?confirm=true&include_audit_logs=true
```

清空 Demo 数据只删除运行生成的 SQLite 和 JSONL 日志，不删除源码、Mock 数据和产品文档。

## 当前边界

当前页面和 API 都不连接真实 ERP / CRM，不执行真实店铺后台操作，不自动改价、不自动投放、不自动群发、不自动退款。
