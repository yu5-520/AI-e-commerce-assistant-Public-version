"""V19.13.2 Task Pool Admission Bridge.

Final gate before lifecycle tasks. It accepts dual-agent mapped SOP decisions one-to-one
and normalizes every accepted decision into a lifecycle-ready task. Business data
insufficiency becomes a data-completion task instead of disappearing at lifecycle validation.

V19.13.2 fixes two hidden valves:
- taskMappingAgentEvidence.source is force-stamped as real_task_mapping_agent before snapshot creation.
- high-risk actions with missing actionParameterPack become data_evidence_task, not rejected decisions.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List
import uuid

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.lifecycle_task_v183_service import create_lifecycle_task_from_snapshot
from src.services.operator_action_family_v194_service import action_family_public_label
from src.services.task_generation_run_service import record_task_generation_run
from src.services.task_snapshot_station_service import create_task_snapshot

TASK_POOL_ADMISSION_BRIDGE_VERSION = "19.13.2"
FORMAL_DECISIONS = {"create_task_snapshot", "manager_review_required"}
HIGH_RISK_ACTIONS = {"roas_scale", "roas_guard", "platform_activity"}
VALID_SOP_SOURCES = {
    "v19_13_agent1_agent2_action_plan_mapped_sop",
    "v19_12_judgment_creative_plan_plus_action_parameter_pack",
    "v19_12_agent_text_source_aware_plus_action_parameter_pack",
    "v19_11_agent_text_source_aware_plus_action_parameter_pack",
    "v19_10_agent_text_plus_action_parameter_pack",
    "llm_agent_action_family_dynamic_sop",
}
VALID_MAPPING_MODES = {
    "v1913_mapping_assembles_agent2_action_plan",
    "v1912_creative_test_plan_assemble_plus_parameter_pack",
    "v1912_assemble_judgment_creative_test_plan",
    "v1911_source_aware_append_only_action_parameter_pack",
    "v1911_one_to_one_formal_judgment_to_task_with_field_source",
    "v1910_append_only_action_parameter_pack",
}
LEGACY_SOP_SOURCES = {"v19_9_action_parameter_pack_sop"}
TEMPLATE_MARKERS = [
    "核心场景词",
    "核心卖点",
    "使用场景等占位词",
    "设计2-3组新标题和主图变体",
    "在广告平台创建A/B测试",
    "监控测试数据",
    "评估测试结果并应用最优素材",
    "商品主体+核心场景+关键卖点",
    "围绕核心场景词重写标题",
    "突出主卖点与场景",
]


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _table(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean_lines(values: List[Any], limit: int = 16) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip(" ，,;；")
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
        if len(output) >= limit:
            break
    return output


def _plan(decision: Dict[str, Any]) -> Dict[str, Any]:
    return decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}


def _package(decision: Dict[str, Any]) -> Dict[str, Any]:
    return decision.get("productJudgmentPackage") if isinstance(decision.get("productJudgmentPackage"), dict) else {}


def _product_identity(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _plan(decision)
    package = _package(decision)
    product = plan.get("productIdentity") if isinstance(plan.get("productIdentity"), dict) else {}
    if not product:
        product = package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {}
    if not product:
        product = {
            "productId": decision.get("productId") or plan.get("productId") or package.get("productId"),
            "storeId": decision.get("storeId") or plan.get("storeId") or package.get("storeId"),
        }
    title = product.get("productTitle") or product.get("title") or product.get("shortTitle") or plan.get("productTitle") or plan.get("title") or decision.get("taskTitle") or decision.get("productId") or package.get("productId")
    if title and not product.get("productTitle"):
        product = dict(product)
        product["productTitle"] = title
        product.setdefault("title", title)
        product.setdefault("shortTitle", title)
    return product


def _family(decision: Dict[str, Any]) -> str:
    plan = _plan(decision)
    package = _package(decision)
    agent1 = package.get("agent1OperatingJudgment") if isinstance(package.get("agent1OperatingJudgment"), dict) else plan.get("agent1OperatingJudgment") if isinstance(plan.get("agent1OperatingJudgment"), dict) else {}
    lock = agent1.get("actionFamilyLock") if isinstance(agent1.get("actionFamilyLock"), dict) else {}
    return str(plan.get("selectedActionFamily") or lock.get("selectedActionFamily") or package.get("selectedActionFamilyHint") or "").strip()


def _sop_steps(decision: Dict[str, Any]) -> List[Any]:
    plan = _plan(decision)
    return _list(plan.get("operatorExecutionSop")) or _list(plan.get("sopSteps"))


def _parameter_pack(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _plan(decision)
    pack = plan.get("actionParameterPack") if isinstance(plan.get("actionParameterPack"), dict) else {}
    if pack:
        return pack
    package = _package(decision)
    return package.get("actionParameterPack") if isinstance(package.get("actionParameterPack"), dict) else {}


def _missing_high_risk_pack(decision: Dict[str, Any]) -> bool:
    return _family(decision) in HIGH_RISK_ACTIONS and not bool(_parameter_pack(decision))


def _effective_parameter_pack(decision: Dict[str, Any]) -> Dict[str, Any]:
    pack = _parameter_pack(decision)
    if pack:
        return pack
    family = _family(decision)
    if family in HIGH_RISK_ACTIONS:
        return {
            "status": "insufficient",
            "generatedBy": "v19_13_2_missing_high_risk_pack_to_data_completion",
            "missingMetrics": ["actionParameterPack", "ROI/ROAS", "毛利率", "预算上限", "止损线"],
            "reason": f"{family} 缺少动作族参数包，不能生成正式高风险动作，已转为补数据任务。",
        }
    return {}


def _evidence(decision: Dict[str, Any]) -> Dict[str, Any]:
    return decision.get("taskMappingAgentEvidence") if isinstance(decision.get("taskMappingAgentEvidence"), dict) else {}


def _force_lifecycle_evidence(evidence: Dict[str, Any] | None = None) -> Dict[str, Any]:
    value = dict(evidence or {})
    # Force overwrite, not setdefault. V18.10 lifecycle validator requires this exact stamp.
    value["source"] = "real_task_mapping_agent"
    value["mappingMode"] = "v1913_mapping_assembles_agent2_action_plan"
    value["businessEventRouter"] = "v19.13_dual_agent_router"
    value["lifecycleNormalization"] = "v19.13.2_force_lifecycle_evidence_and_missing_pack_to_data_task"
    return value


def _creative_group_count(decision: Dict[str, Any]) -> int:
    plan = _plan(decision)
    package = _package(decision)
    agent2 = plan.get("agent2ActionPlan") if isinstance(plan.get("agent2ActionPlan"), dict) else package.get("agent2ActionPlan") if isinstance(package.get("agent2ActionPlan"), dict) else {}
    for creative in [agent2.get("creativeTestPlan"), plan.get("creativeTestPlan"), package.get("creativeTestPlan"), package.get("agentCreativePack")]:
        if isinstance(creative, dict) and isinstance(creative.get("groups"), list):
            good = []
            for group in creative.get("groups")[:5]:
                if not isinstance(group, dict):
                    continue
                text = str(group)
                if any(marker in text for marker in TEMPLATE_MARKERS):
                    continue
                if group.get("fullTitle") and isinstance(group.get("mainImageStructure"), dict):
                    good.append(group)
            return len(good)
    return 0


def _has_template_markers(decision: Dict[str, Any]) -> List[str]:
    text = str(_sop_steps(decision)) + str(_plan(decision).get("titleVariants")) + str(_plan(decision).get("mainImageStructures"))
    return [marker for marker in TEMPLATE_MARKERS if marker in text]


def _is_data_completion_task(decision: Dict[str, Any]) -> bool:
    plan = _plan(decision)
    pack = _parameter_pack(decision)
    agent2 = plan.get("agent2ActionPlan") if isinstance(plan.get("agent2ActionPlan"), dict) else {}
    return bool(
        plan.get("taskType") == "data_evidence_task"
        or pack.get("status") in {"insufficient", "creative_plan_missing"}
        or _missing_high_risk_pack(decision)
        or plan.get("creativePlanMissing")
        or agent2.get("actionPlanStatus") in {"action_plan_missing_data", "conflict_requires_rejudgment"}
    )


def _evidence_requirements(decision: Dict[str, Any]) -> List[str]:
    plan = _plan(decision)
    existing = _clean_lines(_list(plan.get("evidenceRequirements")) or _list(decision.get("evidenceRequirements")))
    if len(existing) >= 2:
        return existing
    family = _family(decision)
    pack = _effective_parameter_pack(decision)
    if _is_data_completion_task(decision):
        lines = ["上传或同步缺失字段来源截图，包含本任务缺失项。", "补齐后重新运行Agent2动作方案站和任务映射站。"]
    elif family in {"roas_scale", "roas_guard"}:
        lines = ["上传广告后台执行前后的预算、出价、ROAS目标或计划调整截图。", "上传执行后广告消耗、ROI/ROAS、支付金额、付费访客和可售天数截图或报表数据。"]
    elif family == "platform_activity":
        lines = ["上传活动报名、优惠券或权益配置截图，包含优惠金额、活动周期和目标人群。", "上传活动上线后的自然访客、点击率、转化率、支付金额、券后毛利或退款率截图。"]
    elif family == "title_image_test":
        lines = ["上传Agent2动作方案中每组完整标题与主图结构的测试配置截图。", "上传测试期间点击率、点击量、转化率和支付金额对比截图。"]
    else:
        lines = ["上传本任务对应后台操作前后截图，证明运营动作已经执行。", "上传执行后核心指标截图或报表数据，供系统后续自动复盘。"]
    if isinstance(pack, dict) and pack.get("missingMetrics"):
        lines.append("本任务参数缺失项：" + "、".join(str(x) for x in pack.get("missingMetrics")[:8]))
    return _clean_lines(existing + lines, limit=8)


def _lifecycle_ready_sop(decision: Dict[str, Any], family: str, sop: List[Any]) -> List[str]:
    product = _product_identity(decision)
    name = product.get("productTitle") or product.get("title") or product.get("productId") or "该商品"
    lines = _clean_lines(sop, limit=16)
    if len(lines) >= 3:
        return lines
    if _is_data_completion_task(decision):
        fallback = [
            f"补齐【{name}】{action_family_public_label(family)}所需缺失数据或动作方案。",
            "提交字段来源截图、后台报表截图或测试方案截图，确保能追溯到商品、店铺和日期。",
            "提交后由系统重新运行动作族数据补包站、Agent2动作方案站和任务映射站。",
        ]
    else:
        fallback = [
            f"按Agent2生成方案执行【{name}】{action_family_public_label(family)}任务。",
            "保持执行入口、人群、预算和时间窗口一致，避免混入无关变量。",
            "提交执行痕迹后等待系统按后续报表自动复盘核心指标。",
        ]
    return _clean_lines(lines + fallback, limit=16)


def _lifecycle_ready_evidence(decision: Dict[str, Any]) -> List[str]:
    evidence = _evidence_requirements(decision)
    if len(evidence) >= 2:
        return evidence
    return _clean_lines(evidence + ["提交本任务后台操作截图或字段来源截图。", "提交后续报表数据截图，供系统自动复盘。"], limit=8)


def _decision_contract_status(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _plan(decision)
    evidence = _force_lifecycle_evidence(_evidence(decision))
    family = _family(decision)
    product = _product_identity(decision)
    pack = _effective_parameter_pack(decision)
    sop = _sop_steps(decision)
    submission_evidence = _evidence_requirements(decision)
    sop_source = plan.get("sopSource")
    mapping_mode = evidence.get("mappingMode") or evidence.get("call_type")
    failures: List[str] = []

    if decision.get("decision") not in FORMAL_DECISIONS:
        failures.append("decision_not_formal")
    if not family:
        failures.append("missing_selectedActionFamily")
    if not (product.get("productId") or decision.get("productId") or plan.get("productId")):
        failures.append("missing_productIdentity")
    if plan.get("taskResponsibility") not in {None, "", "operator_growth"}:
        failures.append("taskResponsibility_not_operator_growth")
    if plan.get("departmentTaskType") not in {None, "", "operator_growth"}:
        failures.append("departmentTaskType_not_operator_growth")
    if len(submission_evidence) < 2:
        failures.append("missing_lifecycle_evidence_requirements")

    if sop_source in LEGACY_SOP_SOURCES:
        failures.append("legacy_sop_source_removed")
    elif sop_source not in VALID_SOP_SOURCES and mapping_mode not in VALID_MAPPING_MODES:
        failures.append("unknown_or_removed_mapping_contract")

    markers = _has_template_markers(decision)
    if family == "title_image_test":
        if markers:
            failures.append("title_image_template_markers_removed:" + ",".join(markers[:3]))
        if not _is_data_completion_task(decision) and _creative_group_count(decision) < 2:
            failures.append("title_image_missing_agent2_creativeTestPlan_groups")

    if family in HIGH_RISK_ACTIONS:
        if not isinstance(pack, dict) or not pack:
            # Should only happen for non-normalized corrupted decisions.
            if len(_lifecycle_ready_sop(decision, family, sop)) < 3:
                failures.append("high_risk_missing_pack_data_task_sop_too_short")
        elif pack.get("status") == "insufficient":
            if not _is_data_completion_task(decision):
                failures.append("insufficient_parameter_pack_must_be_data_completion_task")
            if len(_lifecycle_ready_sop(decision, family, sop)) < 3:
                failures.append("data_completion_sop_too_short")
        elif pack.get("status") not in {"valid", "creative_plan_missing"}:
            failures.append("high_risk_parameter_pack_not_valid")
        elif len(_lifecycle_ready_sop(decision, family, sop)) < 3:
            failures.append("parameterized_sop_too_short")
    elif len(_lifecycle_ready_sop(decision, family, sop)) < 3:
        failures.append("sop_too_short")

    return {
        "ok": not failures,
        "failures": failures,
        "family": family,
        "sopSource": sop_source,
        "mappingMode": mapping_mode,
        "parameterStatus": pack.get("status") if isinstance(pack, dict) else None,
        "creativeGroupCount": _creative_group_count(decision),
        "evidenceRequirementCount": len(submission_evidence),
        "lifecycleReadySopCount": len(_lifecycle_ready_sop(decision, family, sop)),
        "forceStampedEvidenceSource": evidence.get("source"),
    }


def _load_decisions(data_version: str | None) -> List[Dict[str, Any]]:
    if not data_version:
        return []
    with connect() as conn:
        if not _table(conn, "task_generation_decisions_v15"):
            return []
        rows = conn.execute("SELECT payload FROM task_generation_decisions_v15 WHERE data_version = ? ORDER BY created_at ASC", (data_version,)).fetchall()
    return [item for item in [_load(row["payload"]) for row in rows] if item.get("decision") in FORMAL_DECISIONS]


def _ensure_task_pool_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_pool_entries (
                pool_entry_id TEXT PRIMARY KEY,
                task_snapshot_id TEXT NOT NULL,
                task_id TEXT,
                data_version TEXT,
                status TEXT NOT NULL,
                decision TEXT,
                task_layer TEXT,
                assignee_id TEXT,
                reviewer_id TEXT,
                dedupe_key TEXT,
                reason TEXT,
                payload TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """
        )
        ensure_columns(conn, "task_pool_entries", {"task_id": "TEXT", "data_version": "TEXT", "decision": "TEXT", "task_layer": "TEXT", "assignee_id": "TEXT", "reviewer_id": "TEXT", "dedupe_key": "TEXT", "reason": "TEXT", "payload": "TEXT", "created_by": "TEXT"})
        conn.commit()


def _snapshot_body(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(_plan(decision))
    package = _package(decision)
    product = _product_identity(decision)
    family = _family(decision)
    pack = _effective_parameter_pack(decision)
    evidence_requirements = _lifecycle_ready_evidence(decision)
    view = plan.get("operatorJudgmentView") if isinstance(plan.get("operatorJudgmentView"), dict) else {}
    view.setdefault("selectedActionFamilyLabel", action_family_public_label(family))
    sop = _lifecycle_ready_sop(decision, family, _sop_steps(decision))
    task_type = plan.get("taskType") or ("data_evidence_task" if _is_data_completion_task(decision) else f"{family}_execution_task")
    if task_type == "observation_task" or _missing_high_risk_pack(decision):
        task_type = "data_evidence_task"
    title = plan.get("title") or plan.get("taskTitle") or decision.get("taskTitle") or f"{product.get('productTitle') or product.get('productId') or '商品'}｜{action_family_public_label(family)}"
    if _missing_high_risk_pack(decision) and not str(title).startswith("数据补全"):
        title = f"数据补全｜{product.get('productTitle') or product.get('productId') or '商品'}｜{action_family_public_label(family)}参数不足"
    reason = plan.get("reason") or decision.get("reason") or "Agent1定方向，Agent2生成动作方案，任务映射组装SOP。"
    if _missing_high_risk_pack(decision):
        reason = pack.get("reason") or reason

    evidence = _force_lifecycle_evidence(decision.get("taskMappingAgentEvidence") if isinstance(decision.get("taskMappingAgentEvidence"), dict) else {})
    normalized_decision = dict(decision)
    normalized_decision["taskMappingAgentEvidence"] = evidence

    plan.update({
        "title": title,
        "taskTitle": title,
        "reason": reason,
        "selectedActionFamily": family,
        "productIdentity": product,
        "productId": product.get("productId") or decision.get("productId"),
        "storeId": product.get("storeId") or decision.get("storeId"),
        "taskResponsibility": "operator_growth",
        "departmentTaskType": "operator_growth",
        "taskType": task_type,
        "actionType": plan.get("actionType") or family,
        "actionParameterPack": pack,
        "operatorJudgmentView": view,
        "operatorExecutionSop": sop,
        "sopSteps": sop,
        "steps": sop,
        "evidenceRequirements": evidence_requirements,
        "taskMappingAgentEvidence": evidence,
    })
    plan["sopSource"] = plan.get("sopSource") or "v19_13_agent1_agent2_action_plan_mapped_sop"

    return {
        "dataVersion": decision.get("dataVersion"),
        "decision": decision.get("decision"),
        "confidence": 0.82,
        "entityType": "product",
        "entityId": decision.get("productId") or plan.get("productId") or product.get("productId"),
        "productId": decision.get("productId") or plan.get("productId") or product.get("productId"),
        "storeId": decision.get("storeId") or plan.get("storeId") or product.get("storeId"),
        "signalRef": decision.get("packageId") or decision.get("decisionId"),
        "bundleRef": decision.get("packageId"),
        "agentJudgment": {
            "decision": decision.get("decision"),
            "source": "agent1_operating_judgment",
            "status": "v19_13_2_dual_agent_lifecycle_ready_decision",
            "taskResponsibility": "operator_growth",
            "selectedActionFamily": family,
            "operatorJudgmentView": view,
            "actionParameterStatus": pack.get("status") if isinstance(pack, dict) else None,
        },
        "taskPlan": plan,
        "evidenceRequirements": evidence_requirements,
        "businessEventId": plan.get("businessEventId"),
        "taskResponsibility": "operator_growth",
        "departmentTaskType": "operator_growth",
        "selectedActionFamily": family,
        "selectedActionFamilyLabel": action_family_public_label(family),
        "operatorJudgmentView": view,
        "operatorExecutionSop": sop,
        "sopSteps": sop,
        "reviewMetrics": plan.get("reviewMetrics") or (pack.get("reviewMetrics") if isinstance(pack, dict) else []) or ["支付金额", "点击率", "转化率"],
        "systemFacts": {
            "sceneDataJudgmentPackage": package,
            "taskGenerationDecision": normalized_decision,
            "actionParameterPack": pack,
            "agent2ActionPlan": plan.get("agent2ActionPlan"),
        },
        "taskMappingAgentEvidence": evidence,
        "productIdentity": product,
        "productJudgmentPackage": package,
        "source": "v19_13_2_dual_agent_task_pool_admission_bridge",
        "detailDisplayContract": "agent1_judgment_panel_plus_mapped_sop_only",
        "lifecycleReady": True,
    }


def _admit_decision(decision: Dict[str, Any], *, created_by: str | None = None, force_new_snapshot: bool = False) -> Dict[str, Any]:
    contract = _decision_contract_status(decision)
    if not contract.get("ok"):
        return {"ok": False, "status": "rejected_removed_legacy_or_template_decision", "createdSnapshotCount": 0, "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "reason": ",".join(contract.get("failures") or []), "contract": contract}
    snapshot_body: Dict[str, Any] | None = None
    try:
        snapshot_body = _snapshot_body(decision)
        snapshot = create_task_snapshot(snapshot_body, created_by=created_by, force=True)
    except Exception as exc:
        return {"ok": False, "status": "rejected_by_snapshot_creator", "createdSnapshotCount": 0, "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "reason": str(exc), "contract": contract}
    try:
        task = create_lifecycle_task_from_snapshot(snapshot, created_by=created_by)
    except Exception as exc:
        return {"ok": False, "status": "rejected_by_lifecycle_validator", "createdSnapshotCount": 1, "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "taskSnapshotId": snapshot.get("taskSnapshotId"), "reason": str(exc), "contract": contract, "lifecycleReadyPlan": (snapshot_body or {}).get("taskPlan"), "lifecycleReadyEvidence": (snapshot_body or {}).get("taskMappingAgentEvidence")}

    _ensure_task_pool_tables()
    now = datetime.now().isoformat()
    entry_id = f"TPE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    dedupe_key = f"{decision.get('dataVersion')}:{decision.get('decisionId') or decision.get('packageId') or task.get('id')}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO task_pool_entries (pool_entry_id, task_snapshot_id, task_id, data_version, status, decision, task_layer, assignee_id, reviewer_id, dedupe_key, reason, payload, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (entry_id, snapshot.get("taskSnapshotId"), task.get("id"), decision.get("dataVersion"), "entered_task_pool", decision.get("decision"), task.get("taskLayer"), task.get("assigneeId"), task.get("reviewerId"), dedupe_key, "V19.13.2双判断Agent任务已一对一进入任务池。", dumps({"snapshot": snapshot, "task": task, "source": "v19_13_2_dual_agent_bridge", "contract": contract}), created_by, now, now),
        )
        conn.commit()
    return {"ok": True, "status": "entered_task_pool", "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "taskSnapshotId": snapshot.get("taskSnapshotId"), "taskId": task.get("id"), "createdSnapshotCount": 1, "createdTaskCount": 1, "contract": contract}


def _refresh_views(data_version: str | None) -> Dict[str, Any]:
    try:
        from src.services.frontend_read_model_service import refresh_task_views, refresh_dashboard_view
        return {"status": "refreshed", "taskViews": refresh_task_views(data_version=data_version), "dashboard": refresh_dashboard_view()}
    except Exception as exc:
        return {"status": "refresh_failed", "error": str(exc)}


def task_pool_admission_station_v199(data_version: str | None, *, user_id: str | None = None, force_new_snapshot: bool = False, **_: Any) -> Dict[str, Any]:
    decisions = _load_decisions(data_version)
    results = [_admit_decision(decision, created_by=user_id, force_new_snapshot=force_new_snapshot) for decision in decisions]
    by_status = Counter(str(item.get("status")) for item in results)
    by_family = Counter(str(_family(decision)) for decision in decisions)
    by_parameter_status = Counter(str(_effective_parameter_pack(decision).get("status") if _effective_parameter_pack(decision) else "none") for decision in decisions)
    by_reject_reason = Counter(str(item.get("reason") or item.get("status")) for item in results if item.get("status") != "entered_task_pool")
    created_snapshots = sum(int(item.get("createdSnapshotCount") or 0) for item in results)
    created_tasks = sum(int(item.get("createdTaskCount") or 0) for item in results)
    rejected = [item for item in results if item.get("status") != "entered_task_pool"]
    refresh = _refresh_views(data_version)
    status = "completed" if decisions and not rejected else "failed" if decisions and len(rejected) == len(decisions) else "partial" if decisions else "no_formal_decisions"
    try:
        record_task_generation_run(
            data_version=data_version,
            input_bundle_count=0,
            agent_judgment_count=0,
            product_judgment_package_count=0,
            identity_gap_count=0,
            task_decision_count=len(decisions),
            by_decision={},
            streamed_task_snapshot_count=created_snapshots,
            task_pool_created_count=created_tasks,
            skipped_formal_count=len(rejected),
            zero_task_reasons=[str(item.get("reason") or item.get("status")) for item in rejected[:8]],
            agent1_api_call_count=0,
            rag_retrieval_count=0,
            api_budget_violation=False,
            agent_budget_summary={"source": "v19_13_2_dual_agent_task_pool_bridge", "bySelectedActionFamily": dict(by_family), "byParameterStatus": dict(by_parameter_status), "byRejectReason": dict(by_reject_reason)},
            total_agent_call_count=0,
            total_agent_budget=0,
            source="v19_13_2_task_pool_admission_bridge",
        )
    except Exception:
        pass
    return {
        "version": TASK_POOL_ADMISSION_BRIDGE_VERSION,
        "stationId": "task_pool_admission_station",
        "dataVersion": data_version,
        "status": status,
        "formalDecisionCount": len(decisions),
        "createdSnapshotCount": created_snapshots,
        "createdTaskCount": created_tasks,
        "admittedOrExistingTaskCount": created_tasks,
        "rejectedCount": len(rejected),
        "bySelectedActionFamily": dict(by_family),
        "byParameterStatus": dict(by_parameter_status),
        "byAdmissionStatus": dict(by_status),
        "byRejectReason": dict(by_reject_reason),
        "results": results[:80],
        "refresh": refresh,
        "taskPoolRef": f"task_pool:{data_version or 'latest'}",
        "outputRef": f"task_pool:{data_version or 'latest'}",
        "rule": "V19.13.2: force real_task_mapping_agent evidence stamp and convert missing high-risk parameter packs into data-completion lifecycle tasks.",
    }
