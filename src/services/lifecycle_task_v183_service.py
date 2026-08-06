"""V20.28 lifecycle task service.

The lifecycle boundary consumes the current pipeline_items semantic chain:
Agent1 judgment -> Action Pack/RAG -> real Agent2 plan -> SOP structuring.
It validates the actual chain evidence instead of requiring a historical source
label such as ``real_task_mapping_agent``.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import uuid4

from src.services.competition_operator_context_service import (
    COMPETITION_OPERATOR_ID,
    COMPETITION_OPERATOR_ROLE,
    competition_store,
    operator_display,
)

LIFECYCLE_TASK_VERSION = "20.28"
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}
ENGINEERING_PATTERNS = [
    r"relationConfidence\s*(?:=|为|仅)?\s*[0-9.]+",
    r"candidateSignal\s*(?:=|为)?\s*(?:true|false)",
    r"routeSignalStrength\s*(?:=|为)?\s*\w+",
    r"metricSignalConfidence\s*(?:=|为)?\s*\w+",
    r"taskActionLevel\s*(?:=|为)?\s*\w+",
    r"future_trend_forecast_action_mapping",
    r"context_driven_flexible_sop",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def make_task_id() -> str:
    return f"LT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value not in {None, ""}:
        return [value]
    return []


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _snapshot_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(snapshot.get("payload"))


def _system_facts(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(_snapshot_payload(snapshot).get("systemFacts"))


def _task_generation_decision(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    system_facts = _system_facts(snapshot)
    decision = _dict(system_facts.get("taskGenerationDecision"))
    if not decision:
        decision = _dict(_snapshot_payload(snapshot).get("rawTaskGenerationDecision"))
    return decision


def _agent_evidence(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    for value in (
        snapshot.get("taskMappingAgentEvidence"),
        _snapshot_payload(snapshot).get("taskMappingAgentEvidence"),
        _task_generation_decision(snapshot).get("taskMappingAgentEvidence"),
    ):
        if isinstance(value, dict) and value:
            return value
    return {}


def _chain_integrity(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(_task_generation_decision(snapshot).get("chainIntegrity"))


def _evidence_package(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    system_facts = _system_facts(snapshot)
    package = _dict(system_facts.get("sceneDataJudgmentPackage"))
    if not package:
        package = _dict(snapshot.get("productJudgmentPackage"))
    return package


def _full_bundle_evidence(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(_evidence_package(snapshot).get("fullProductBundleEvidence"))


def _operating_graph_route(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    package = _evidence_package(snapshot)
    evidence = _full_bundle_evidence(snapshot)
    return _dict(package.get("operatingGraphRoute")) or _dict(
        evidence.get("operatingGraphRoute")
    )


def _forecast(snapshot: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(plan.get("operatingTrendForecast")) or _dict(
        _evidence_package(snapshot).get("operatingTrendForecast")
    )


def _sanitize(text: Any) -> str:
    value = str(text or "").strip()
    for pattern in ENGINEERING_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    value = re.sub(r"[,，；;]\s*[,，；;]+", "，", value)
    value = re.sub(r"\s+", " ", value).strip(" ，,;；")
    return value or "已根据商品、店铺、类目和趋势预估生成经营动作。"


def _deadline_minutes(deadline: Any) -> int:
    text = str(deadline or "6小时内")
    if re.search(r"\d{4}-\d{2}-\d{2}T", text):
        return 360
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    number = float(nums[0]) if nums else 6
    if "分钟" in text:
        return int(number)
    if "小时" in text:
        return int(number * 60)
    if "天" in text:
        return int(number * 1440)
    if "周" in text:
        return int(number * 10080)
    if "今日" in text:
        return 720
    return 360


def _deadline_at(created_at: str, minutes: int) -> str:
    try:
        base = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except Exception:
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (
        base + timedelta(minutes=int(minutes))
    ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _execution_time_contract(
    plan: Dict[str, Any], snapshot: Dict[str, Any]
) -> Dict[str, Any]:
    priority = plan.get("priority") or snapshot.get("priority") or "中"
    raw = plan.get("executionDeadline") or plan.get("deadline") or snapshot.get("deadline")
    minutes = _deadline_minutes(raw)
    if not raw or minutes > 720 or re.search(r"\d{4}-\d{2}-\d{2}T", str(raw)):
        raw = "6小时内" if priority == "高" else "12小时内"
        minutes = _deadline_minutes(raw)
    if priority == "高" and minutes > 720:
        raw, minutes = "12小时内", 720
    follow = plan.get("followUpDeadline") or "24小时内确认动作是否落地"
    review = plan.get("reviewCycle") or plan.get("recapCycle") or "3天后系统自动复盘"
    return {
        "deadline": str(raw),
        "executionDeadline": str(raw),
        "deadlineMinutes": int(minutes),
        "followUpDeadline": str(follow),
        "reviewCycle": str(review),
        "recapCycle": str(review),
    }


def _first_store_id(snapshot: Dict[str, Any], plan: Dict[str, Any]) -> str | None:
    product = _product_identity(snapshot, plan)
    store_id = plan.get("storeId") or product.get("storeId") or snapshot.get("storeId")
    return str(store_id) if store_id else None


def _ownership_for_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Bind every public task to the server-owned competition operator.

    Approval and department-role expansion remain enterprise-only capabilities.
    The competition runtime records that boundary but never fabricates owner,
    manager, reviewer or client-selectable identities.
    """
    plan = _dict(snapshot.get("taskPlan"))
    store_id = _first_store_id(snapshot, plan) or "COMP-STORE-1"
    store_ids = [str(item) for item in _as_list(plan.get("storeIds")) if item]
    if not store_ids:
        store_ids = [store_id]
    enterprise_approval_required = bool(
        snapshot.get("needManagerReview")
        or snapshot.get("decision") == "manager_review_required"
        or plan.get("approvalRequired")
    )
    return {
        "assignedOperatorId": COMPETITION_OPERATOR_ID,
        "reviewerId": None,
        "ownerUserId": None,
        "visibleUserIds": [COMPETITION_OPERATOR_ID],
        "visibleRoleIds": [COMPETITION_OPERATOR_ROLE],
        "visibleStoreIds": store_ids,
        "storeIds": store_ids,
        "runtimeActorMode": "fixed_competition_operator",
        "enterpriseApprovalRequired": enterprise_approval_required,
        "organizationGovernance": "enterprise_only_not_enabled",
    }


def _product_identity(snapshot: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    forecast = _forecast(snapshot, plan)
    package = _evidence_package(snapshot)
    evidence = _full_bundle_evidence(snapshot)
    product = _dict(plan.get("productIdentity"))
    product = {
        **_dict(forecast.get("productIdentity")),
        **_dict(package.get("productIdentity")),
        **product,
    }
    product_id = str(
        product.get("productId")
        or plan.get("productId")
        or package.get("productId")
        or evidence.get("productId")
        or snapshot.get("entityId")
        or ""
    ).strip()
    store_id = str(
        product.get("storeId")
        or plan.get("storeId")
        or package.get("storeId")
        or evidence.get("storeId")
        or "GLOBAL"
    ).strip()
    title = (
        product.get("productTitle")
        or evidence.get("title")
        or plan.get("productTitle")
        or product_id
        or "未命名商品"
    )
    return {
        "productId": product_id,
        "systemProductCode": product.get("systemProductCode")
        or product.get("productCode")
        or product_id,
        "productTitle": title,
        "shortTitle": product.get("shortTitle")
        or product.get("productTitle")
        or evidence.get("title")
        or product_id
        or "未命名商品",
        "storeId": store_id,
        "storeName": product.get("storeName") or evidence.get("storeName") or "经营单元",
        "platform": product.get("platform") or evidence.get("platform") or "经营平台",
        "skuId": product.get("skuId") or evidence.get("skuId") or "",
        "platformItemId": product.get("platformItemId")
        or evidence.get("platformItemId")
        or product_id,
        "productUrl": product.get("productUrl") or evidence.get("productUrl") or "",
        "mainImageUrl": product.get("mainImageUrl") or evidence.get("mainImageUrl") or "",
        "verticalCategory": product.get("verticalCategory")
        or evidence.get("verticalCategory")
        or "未归类",
    }


def _dynamic_metric_changes(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    package = _evidence_package(snapshot)
    evidence = _full_bundle_evidence(snapshot)
    route = _operating_graph_route(snapshot)
    for key in ["correlatedMetricChanges", "dynamicMetricChanges", "allMetricChanges"]:
        for source in [package, evidence, route]:
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, list) and value:
                return value[:16]
    return []


def _product_action_card(
    snapshot: Dict[str, Any],
    plan: Dict[str, Any],
    dynamic_changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    product = _product_identity(snapshot, plan)
    metrics = [
        item.get("summary") or item.get("metricName") or item.get("metricCode")
        for item in dynamic_changes[:4]
        if isinstance(item, dict)
    ]
    return {
        "productId": product.get("productId"),
        "productObjectId": product.get("productId"),
        "systemProductCode": product.get("systemProductCode"),
        "productTitle": product.get("productTitle"),
        "title": product.get("productTitle"),
        "shortTitle": product.get("shortTitle"),
        "storeId": product.get("storeId"),
        "storeName": product.get("storeName"),
        "store": product.get("storeName"),
        "platform": product.get("platform"),
        "skuId": product.get("skuId"),
        "platformItemId": product.get("platformItemId"),
        "productUrl": product.get("productUrl"),
        "mainImageUrl": product.get("mainImageUrl"),
        "verticalCategory": product.get("verticalCategory"),
        "primaryAction": plan.get("taskType") or "经营动作",
        "why": _sanitize(plan.get("reason") or plan.get("trendJudgment")),
        "keyMetrics": [value for value in metrics if value],
        "openProductState": {
            "productId": product.get("productId"),
            "productObjectId": product.get("productId"),
            "storeId": product.get("storeId"),
            "storeName": product.get("storeName"),
            "platformItemId": product.get("platformItemId"),
        },
    }


def _metric_facts(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "metricCode": item.get("metricCode"),
            "label": item.get("metricName") or item.get("label") or item.get("metricCode"),
            "value": item.get("currentValue"),
        }
        for item in _dynamic_metric_changes(snapshot)[:8]
        if isinstance(item, dict)
    ]


def _semantic_reason(snapshot: Dict[str, Any], plan: Dict[str, Any]) -> str:
    decision = _task_generation_decision(snapshot)
    agent2 = _dict(decision.get("agent2ActionPlan")) or _dict(plan.get("agent2ActionPlan"))
    package = _evidence_package(snapshot)
    agent1 = _dict(package.get("agent1OperatingJudgment")) or _dict(
        plan.get("agent1OperatingJudgment")
    )
    for value in (
        plan.get("reason"),
        decision.get("reason"),
        agent2.get("reason"),
        agent2.get("differentiationReason"),
        agent1.get("businessHypothesis"),
        agent1.get("primaryOperatingGap"),
        agent1.get("finding"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _prepare_lifecycle_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    prepared = deepcopy(snapshot)
    plan = dict(_dict(prepared.get("taskPlan")))
    reason = _semantic_reason(prepared, plan)
    if reason:
        plan["reason"] = reason
        plan.setdefault("trendJudgment", reason)
    prepared["taskPlan"] = plan
    return prepared


def _semantic_chain_missing(snapshot: Dict[str, Any]) -> List[str]:
    evidence = _agent_evidence(snapshot)
    chain = _chain_integrity(snapshot)
    missing: List[str] = []
    required_true = {
        "noMappingLlm": True,
        "noAgent2Rerun": True,
        "noActionPackRerun": True,
        "itemized": True,
        "noLegacyRuntimeSource": True,
        "agent2ProviderTracePassed": True,
    }
    if not evidence.get("pipelineItemId"):
        missing.append("pipelineItemId")
    for key, expected in required_true.items():
        if evidence.get(key) is not expected:
            missing.append(key)
    if evidence.get("fallbackAllowed") is not False:
        missing.append("fallbackAllowed_false")
    if chain.get("passed") is not True:
        missing.append("chainIntegrity.passed")
    for key in (
        "agent2ProviderTracePassed",
        "taskDifferentiationPassed",
        "inventoryResponsibilityPassed",
        "ragTracePassed",
    ):
        if chain.get(key) is False:
            missing.append(f"chainIntegrity.{key}")
    return list(dict.fromkeys(missing))


def validate_lifecycle_snapshot(snapshot: Dict[str, Any]) -> None:
    plan = _dict(snapshot.get("taskPlan"))
    chain_missing = _semantic_chain_missing(snapshot)
    if chain_missing:
        raise ValueError(
            "V20.28拒绝入池：双Agent语义链路证据不完整：" + ",".join(chain_missing)
        )
    if snapshot.get("decision") not in {"create_task_snapshot", "manager_review_required"}:
        raise ValueError("V20.28拒绝入池：不是正式生命周期任务决策。")
    if plan.get("taskType") == "observation_task" or snapshot.get("decision") == "system_watch":
        raise ValueError("V20.28拒绝入池：观察/后台等待项不是运营任务。")
    product = _product_identity(snapshot, plan)
    if not product.get("productId") or not product.get("productTitle"):
        raise ValueError("V20.28拒绝入池：缺少可执行商品身份。")
    if not plan.get("title") or not plan.get("reason"):
        raise ValueError("V20.28拒绝入池：taskPlan.title/reason缺失。")
    if len(_as_list(plan.get("sopSteps") or plan.get("steps"))) < 3:
        raise ValueError("V20.28拒绝入池：SOP步骤不足。")
    if len(_as_list(plan.get("evidenceRequirements"))) < 2:
        raise ValueError("V20.28拒绝入池：提交痕迹要求不足。")


def build_lifecycle_dedupe_key(snapshot: Dict[str, Any]) -> str:
    plan = _dict(snapshot.get("taskPlan"))
    ownership = _ownership_for_snapshot(snapshot)
    product = _product_identity(snapshot, plan)
    owner = (
        ownership.get("assignedOperatorId")
        or ownership.get("reviewerId")
        or ownership.get("ownerUserId")
        or "global"
    )
    return ":".join(
        str(value or "unknown")
        for value in [
            owner,
            snapshot.get("dataVersion"),
            product.get("productId"),
            plan.get("taskType"),
            plan.get("actionType"),
        ]
    )


def _evidence_title(item: Any) -> str:
    if isinstance(item, dict):
        return _sanitize(item.get("title") or item.get("summary") or "执行凭证")
    return _sanitize(item)


def create_lifecycle_task_from_snapshot(
    snapshot: Dict[str, Any], *, created_by: str | None = None
) -> Dict[str, Any]:
    snapshot = _prepare_lifecycle_snapshot(snapshot)
    validate_lifecycle_snapshot(snapshot)
    plan = _dict(snapshot.get("taskPlan"))
    judgment = _dict(snapshot.get("agentJudgment"))
    rag_context = _dict(snapshot.get("ragContext"))
    raw_evidence = _as_list(snapshot.get("evidenceRequirements")) or _as_list(
        plan.get("evidenceRequirements")
    )
    evidence_requirements = [_evidence_title(item) for item in raw_evidence]
    sop_steps = [
        _sanitize(item)
        for item in _as_list(plan.get("sopSteps") or plan.get("steps"))
    ]
    review_metrics = [str(item) for item in _as_list(plan.get("reviewMetrics"))]
    dynamic_changes = _dynamic_metric_changes(snapshot)
    route = _operating_graph_route(snapshot)
    forecast = _forecast(snapshot, plan)
    ownership = _ownership_for_snapshot(snapshot)
    store_id = (ownership.get("storeIds") or [None])[0]
    store = competition_store(store_id)
    product = _product_identity(snapshot, plan)
    product_card = _product_action_card(snapshot, plan, dynamic_changes)
    enterprise_approval_required = bool(
        snapshot.get("decision") == "manager_review_required"
        or plan.get("approvalRequired")
    )
    task_layer = "operator_execution"
    status = "待接收"
    task_id = make_task_id()
    created_at = now_iso()
    title = _sanitize(
        plan.get("title")
        or f"{product.get('systemProductCode')}｜{product.get('shortTitle')}｜经营任务"
    )
    subtitle = _sanitize(plan.get("subtitle") or "执行倒计时经营SOP")
    time_contract = _execution_time_contract(plan, snapshot)
    deadline = time_contract["deadline"]
    deadline_minutes = time_contract["deadlineMinutes"]
    deadline_at = _deadline_at(created_at, deadline_minutes)
    trend_judgment = _sanitize(
        plan.get("trendJudgment")
        or judgment.get("trendJudgment")
        or plan.get("reason")
        or judgment.get("reason")
    )
    system_change_pack = {
        "title": "商品动态数据",
        "lines": dynamic_changes,
        "dynamicMetricChanges": dynamic_changes,
        "operatingGraphRoute": route,
        "rule": "V20.28商品动态数据只展示事实变化；工程字段不进入运营话术。",
    }
    agent_operating_judgment = {
        "title": "经营趋势判断",
        "judgment": trend_judgment,
        "forecastScenario": forecast.get("scenario"),
        "scenarioName": forecast.get("scenarioName"),
        "productIdentity": product,
        "systemRecapLine": [
            f"{time_contract['reviewCycle']}：复盘 {item}"
            for item in review_metrics[:4]
        ],
    }
    detail_report = {
        "version": LIFECYCLE_TASK_VERSION,
        "taskSnapshotId": snapshot.get("taskSnapshotId"),
        "dataVersion": snapshot.get("dataVersion"),
        "warningSummary": trend_judgment,
        "systemFacts": _system_facts(snapshot),
        "systemChangePack": system_change_pack,
        "dynamicMetricChanges": dynamic_changes,
        "agentJudgment": judgment,
        "agentOperatingJudgment": agent_operating_judgment,
        "taskPlan": plan,
        "taskMappingAgentEvidence": _agent_evidence(snapshot),
        "productIdentity": product,
        "productActionCards": [product_card],
        "operatingTrendForecast": forecast,
        "poolBoundary": "V20.28正式任务必须具备真实Agent2链路、商品身份、可执行SOP和提交痕迹。",
    }
    task = {
        "id": task_id,
        "taskId": task_id,
        "dataVersion": snapshot.get("dataVersion"),
        "lifecycleTaskVersion": LIFECYCLE_TASK_VERSION,
        "taskGenerationMode": "v20_28_semantic_agent2_sop_lifecycle_task",
        "sourceModule": "task_pool_admission_core_v20_28",
        "source": "V20.28双Agent语义链路",
        "sourceEvent": snapshot.get("taskSnapshotId"),
        "sourceRoute": "business-actions",
        "productRoute": "business-products",
        "todoRoute": "business-actions",
        "logRoute": "business-report",
        "taskSnapshotId": snapshot.get("taskSnapshotId"),
        "decision": snapshot.get("decision"),
        "title": title,
        "productTitle": product.get("productTitle"),
        "productShort": product.get("shortTitle"),
        "subtitle": subtitle,
        "entityType": "product",
        "entityId": product.get("productId"),
        "productId": product.get("productId"),
        "productIdentity": product,
        "productActionCards": [product_card],
        "affectedProducts": [product_card],
        "storeIds": ownership.get("storeIds") or [],
        "visibleStoreIds": ownership.get("visibleStoreIds")
        or ownership.get("storeIds")
        or [],
        "store": (store or {}).get("name") or product.get("storeName") or "经营单元",
        "storeName": (store or {}).get("name") or product.get("storeName") or "经营单元",
        "platform": (store or {}).get("platform") or product.get("platform") or "经营平台",
        "riskDomain": plan.get("riskDomain") or plan.get("taskType"),
        "actionType": "经营动作",
        "taskType": plan.get("taskType"),
        "permissionDecision": plan.get("permissionDecision"),
        "sopSource": plan.get("sopSource"),
        "operatingTrendForecast": forecast,
        "priority": plan.get("priority") or snapshot.get("priority") or "中",
        "priorityLevel": (
            "danger"
            if (plan.get("priority") or snapshot.get("priority")) == "高"
            else "good"
            if (plan.get("priority") or snapshot.get("priority")) == "低"
            else "warning"
        ),
        "deadline": deadline,
        "executionDeadline": time_contract["executionDeadline"],
        "deadlineMinutes": deadline_minutes,
        "deadlineAt": deadline_at,
        "dueAt": deadline_at,
        "remainingMinutes": deadline_minutes,
        "followUpDeadline": time_contract["followUpDeadline"],
        "reviewCycle": time_contract["reviewCycle"],
        "recapCycle": time_contract["recapCycle"],
        "timeBucket": deadline,
        "taskLayer": task_layer,
        "status": status,
        "workflowStatus": status,
        "displayStatus": status,
        "taskCard": {
            "title": title,
            "subtitle": product.get("shortTitle"),
            "priority": plan.get("priority") or snapshot.get("priority") or "中",
            "deadline": deadline,
            "executionDeadline": deadline,
            "deadlineMinutes": deadline_minutes,
            "deadlineAt": deadline_at,
            "decision": snapshot.get("decision"),
            "taskType": plan.get("taskType"),
        },
        "taskDetailReport": detail_report,
        "systemChangePack": system_change_pack,
        "dynamicMetricChanges": dynamic_changes,
        "agentOperatingJudgment": agent_operating_judgment,
        "evidencePack": [
            {"title": item, "value": None, "type": "evidence_requirement"}
            for item in evidence_requirements
        ],
        "sopSteps": sop_steps,
        "executionRequirements": sop_steps,
        "reviewMetrics": review_metrics,
        "metricFacts": _metric_facts(snapshot),
        "operationBudget": plan.get("operationBudget") or {},
        "completionGate": {
            "type": "evidence_and_recap_required",
            "requiredEvidence": evidence_requirements,
            "reviewMetrics": review_metrics,
        },
        "failureThreshold": {
            "rule": f"{time_contract['reviewCycle']}后核心指标未改善或反向恶化，则进入复核退回或二次判断。",
            "metrics": review_metrics,
        },
        "agentJudgment": {
            **judgment,
            "status": "v20_28_semantic_agent_chain_task_snapshot",
            "decision": snapshot.get("decision"),
            "confidence": snapshot.get("confidence"),
            "ragContextApplied": bool(rag_context),
            "taskMappingAgentEvidence": _agent_evidence(snapshot),
        },
        "ownership": ownership,
        "assigneeId": (
            ownership.get("assignedOperatorId")
            if task_layer == "operator_execution"
            else None
        ),
        "reviewerId": ownership.get("reviewerId"),
        "visibleUserIds": ownership.get("visibleUserIds") or [],
        "visibleRoleIds": ownership.get("visibleRoleIds")
        or [COMPETITION_OPERATOR_ROLE],
        "enterpriseApprovalRequired": enterprise_approval_required,
        "enterpriseApprovalStatus": (
            "not_enabled_in_competition"
            if enterprise_approval_required
            else "not_required"
        ),
        "assigneeName": operator_display(
            ownership.get("assignedOperatorId"), "赛事运营工作台"
        ),
        "reviewerName": "企业组织协同版暂未开放",
        "assignedById": None,
        "assignedByName": "系统经营链路",
        "createdByRole": "system",
        "createdAt": created_at,
        "updatedAt": created_at,
        "manualOrder": deadline_minutes,
        "parentTaskId": None,
        "childTaskIds": [],
        "recapTarget": "日报",
        "dedupeKey": build_lifecycle_dedupe_key(snapshot),
        "sourceTrail": [
            "V20.28双Agent语义合同",
            "Agent2差异化执行方案",
            "SOP结构化生命周期入口",
        ],
        "availableActions": ["report", "source", "accept", "submit"],
    }
    return task
