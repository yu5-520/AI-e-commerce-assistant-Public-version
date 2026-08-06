"""V21.8.0 dashboard read-model service.

Dashboard stays a pure read path. It reads materialized frontend views and exposes
personal growth plus the neural operating projection without rebuilding snapshots,
running Agent, generating tasks or touching the live business pipeline.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.competition_operator_context_service import current_user, visible_store_ids_for_user
from src.services.frontend_read_model_service import (
    FRONTEND_READ_MODEL_VERSION,
    read_dashboard_view,
    read_product_views,
    read_task_views,
)
from src.services.neural_operating_read_model_v218_service import (
    NEURAL_OPERATING_READ_MODEL_VERSION,
    build_neural_operating_projection,
    visible_tasks_for_user,
)

DASHBOARD_VERSION = "21.8.0"
DASHBOARD_WORKBENCH_SECTIONS = [
    "operatorProfile",
    "neuralOperating",
    "todayPriorityTasks",
    "allVisibleTasks",
    "latestReportResult",
    "pendingReviewItems",
    "completionProgress",
]
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _task_card(task: Dict[str, Any], rank: int) -> Dict[str, Any]:
    return {
        "rank": rank,
        "id": task.get("taskId") or task.get("id"),
        "title": task.get("title") or "经营任务",
        "subtitle": task.get("subtitle") or task.get("workflowStatus") or task.get("status") or "SOP任务",
        "productId": task.get("productId"),
        "riskDomain": task.get("riskDomain") or task.get("subtitle") or "经营",
        "priority": task.get("priority") or "中",
        "priorityLevel": "danger" if task.get("priority") == "高" else "warning" if task.get("priority") == "中" else "good",
        "deadline": task.get("deadline") or "本周内",
        "status": task.get("workflowStatus") or task.get("status") or "待处理",
        "source": "frontend_task_view",
        "assigneeId": task.get("assigneeId") or task.get("assigneeUserId"),
        "assigneeName": task.get("assigneeName") or "未派发",
        "reviewerId": task.get("reviewerId") or task.get("reviewerUserId"),
        "reviewerName": task.get("reviewerName") or "待复核人",
        "reason": task.get("subtitle") or "经营任务",
        "route": "business-actions",
        "signalId": task.get("signalId") or task.get("pipelineItemId") or task.get("itemId"),
        "pipelineItemId": task.get("pipelineItemId") or task.get("itemId"),
        "dataVersion": task.get("dataVersion"),
        "actionFamily": task.get("actionFamily") or task.get("selectedActionFamily"),
        "productIdentity": task.get("productIdentity") or {},
        "overdue": bool(task.get("overdue")),
    }


def _active(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        task
        for task in tasks
        if task.get("status") not in DONE_STATUS and task.get("workflowStatus") not in DONE_STATUS
    ]


def _priority_key(task: Dict[str, Any]) -> tuple[int, str]:
    weight = {"高": 0, "紧急": 0, "中": 1, "低": 2}
    return weight.get(str(task.get("priority") or "中"), 1), str(task.get("deadline") or "")


def _product_store_id(product: Dict[str, Any]) -> str:
    identity = _dict(product.get("productIdentity"))
    store = _dict(product.get("store"))
    return str(identity.get("storeId") or product.get("storeId") or store.get("id") or "").strip()


def _visible_products(user_id: str | None, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    user = current_user(user_id)
    if user.get("roleId") == "owner":
        return products
    visible_stores = set(visible_store_ids_for_user(user.get("id")))
    return [item for item in products if _product_store_id(item) in visible_stores]


def _dashboard_workbench(tasks: List[Dict[str, Any]], dashboard: Dict[str, Any]) -> Dict[str, Any]:
    active = sorted(_active(tasks), key=_priority_key)
    all_visible_cards = [_task_card(task, index) for index, task in enumerate(active, start=1)]
    priority_tasks = all_visible_cards[:5]
    review_items = [
        _task_card(task, index)
        for index, task in enumerate(
            [
                task
                for task in active
                if task.get("managerApproval")
                or task.get("status") in {"待复核", "待拆分"}
                or task.get("workflowStatus") in {"待复核", "待拆分"}
            ][:3],
            start=1,
        )
    ]
    completed_count = len(
        [
            task
            for task in tasks
            if task.get("status") in DONE_STATUS or task.get("workflowStatus") in DONE_STATUS
        ]
    )
    total_count = max(len(active) + completed_count, 1)
    return {
        "mode": "v21_8_neural_dashboard_read_model",
        "sections": DASHBOARD_WORKBENCH_SECTIONS,
        "todayPriorityTasks": priority_tasks,
        "allVisibleTasks": all_visible_cards,
        "pendingReviewItems": review_items,
        "emptyPriorityText": "当前没有待执行经营任务。新的有效变化会先进入经营判断，再按优先级传导到任务队列。",
        "latestReportResult": {
            "label": "最新经营数据",
            "status": "已同步" if dashboard.get("ready") else "待同步",
            "summary": "最新报表已同步，经营信号与任务状态已更新。" if dashboard.get("ready") else "等待最新经营数据进入系统。",
            "taskHint": f"当前执行任务 {len(active)} 个",
            "updatedModules": ["数据", "经营", "任务", "日志"],
            "latestSyncedAt": dashboard.get("cachedAt") or dashboard.get("updatedAt"),
        },
        "completionProgress": {
            "visibleActive": len(active),
            "processing": len(
                [
                    task
                    for task in active
                    if task.get("status") in {"处理中", "执行中", "已接收", "待提交"}
                    or task.get("workflowStatus") in {"处理中", "执行中", "已接收", "待提交"}
                ]
            ),
            "pendingReview": len(review_items),
            "returned": len([task for task in active if task.get("workflowStatus") == "已退回"]),
            "completed": completed_count,
            "completionRate": round(completed_count / total_count * 100),
            "summary": f"已完成 {completed_count} 个，当前执行任务 {len(active)} 个",
        },
    }


def get_dashboard_summary(user_id: str | None = None) -> Dict[str, Any]:
    dashboard = read_dashboard_view()
    task_view = read_task_views(limit=200)
    product_view = read_product_views(limit=500)
    all_tasks = task_view.get("items") or []
    all_products = product_view.get("items") or []
    tasks = visible_tasks_for_user(user_id, all_tasks)
    products = _visible_products(user_id, all_products)
    workbench = _dashboard_workbench(tasks, dashboard)
    neural = build_neural_operating_projection(user_id, tasks=tasks, dashboard=dashboard)
    visible_store_ids = {
        store_id
        for store_id in (_product_store_id(product) for product in products)
        if store_id
    }
    if not visible_store_ids:
        visible_store_ids = set(visible_store_ids_for_user(user_id))
    has_data = bool(dashboard.get("ready") or tasks or products)
    return {
        "apiEntry": "/api/modules/dashboard",
        "canonicalReadModelEntry": "/api/view/dashboard",
        "version": DASHBOARD_VERSION,
        "readModelVersion": FRONTEND_READ_MODEL_VERSION,
        "neuralOperatingReadModelVersion": NEURAL_OPERATING_READ_MODEL_VERSION,
        "dashboardMode": "v21_8_personal_neural_operating_center",
        "workbenchSections": DASHBOARD_WORKBENCH_SECTIONS,
        "hasData": has_data,
        "emptyState": "暂无经营信号",
        "title": "AI运营中心",
        "heroBadge": f"LV{neural['operatorProfile']['level']} {neural['operatorProfile']['levelName']}",
        "operatorProfile": neural["operatorProfile"],
        "neuralOperating": neural,
        "latestImport": workbench["latestReportResult"],
        "metrics": [
            {"label": "待执行", "value": neural["signalCounts"]["actionReady"], "desc": "任务传导"},
            {"label": "处理中", "value": neural["signalCounts"]["executing"], "desc": "员工执行"},
            {"label": "待复核", "value": neural["signalCounts"]["reviewPending"], "desc": "等待验证"},
            {"label": "已沉淀", "value": neural["signalCounts"]["learned"], "desc": "经营记忆"},
            {"label": "店铺", "value": len(visible_store_ids), "desc": "经营对象"},
            {"label": "商品", "value": len(products), "desc": "经营对象"},
        ],
        "taskQueue": workbench["allVisibleTasks"],
        "tasks": tasks[:6],
        "todayWorkbench": workbench,
        "recentLogs": [],
        "snapshot": {
            "version": FRONTEND_READ_MODEL_VERSION,
            "readMode": "frontend_read_model_only",
            "snapshotKey": dashboard.get("cachedAt"),
            "pipelineGate": None,
        },
        "objectSummary": {
            "productCount": len(products),
            "storeCount": len(visible_store_ids),
            "source": "frontend_product_view_scoped",
        },
        "productsCount": len(products),
        "forbiddenRuntimeStages": [
            "materialize_system_product_snapshot",
            "materialize_product_signal_snapshot",
            "generate_signal_pool",
            "run_agent_judgment_station",
            "sync_ready_task_snapshots",
            "projection_summary",
            "projected_products",
            "dataset_rows",
            "rag_retrieval",
            "llm_generation",
        ],
        "rule": "V21.8.0：首页与全局神经层按登录用户和店铺范围读取现有投影；不触发Agent、RAG、SOP、任务同步或快照重算。",
    }
