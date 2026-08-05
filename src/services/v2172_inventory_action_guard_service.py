"""V21.7.2 inventory/action-family and partial-completion guard.

Inventory and sellable-days are operating constraints, not an operator traffic
hypothesis. This overlay runs after the V21.6.2 observation contract and V21.7.1
Agent2 structural alignment. It deterministically returns baseline or
inventory-only ROAS misroutes to observation before Action Pack/Agent2, keeps
legitimate paid-efficiency actions inside the upstream experiment permission,
repairs already diagnosed inventory-ticket misroutes without admitting a task,
and makes the live headline report failures as attention rather than completion.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, dumps, loads

INVENTORY_ACTION_GUARD_VERSION = "21.7.2"
ROAS_FAMILIES = {"roas_guard", "roas_scale"}
OBSERVATION_HINTS = {
    "observe_only",
    "metric_observation",
    "product_level_observation",
}
PAID_EFFICIENCY_METRICS = {"roi", "roas"}
INVENTORY_METRICS = {"inventory", "availableDays", "sellableDays"}
ISOLATED_MODES = {
    "isolated_test",
    "directional_test",
    "formal_optimization_test",
}
_AGENT2_RESULT_FIELDS = {
    "agent2ActionPlan",
    "agent2Provider",
    "agent2Source",
    "agent2ExecutionProof",
    "actionPlanSource",
    "actionPlanStatus",
    "plan",
    "operationPlan",
    "sopDecision",
    "taskAdmission",
    "decisionId",
    "taskId",
    "reason",
    "blockedReason",
    "missing",
    "failureOwner",
    "frontendFailureLabel",
    "taskAdmissionAllowed",
    "agent2RetryPolicy",
}
_INSTALLED = False


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _number(value: Any) -> float | None:
    if value in {None, "", "—", "未识别", "UNKNOWN", "未提供"}:
        return None
    try:
        text = (
            str(value)
            .replace("¥", "")
            .replace("￥", "")
            .replace(",", "")
            .replace("元", "")
            .replace("%", "")
            .strip()
        )
        return float(text)
    except (TypeError, ValueError):
        return None


def _ratio(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return number / 100 if abs(number) > 1 else number


def _payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        outer = value
    else:
        try:
            parsed = loads(value) if value else {}
        except Exception:
            try:
                parsed = json.loads(value or "{}")
            except Exception:
                parsed = {}
        outer = parsed if isinstance(parsed, dict) else {}
    inner = outer.get("payload")
    return inner if isinstance(inner, dict) else outer


def _source_payload(judgment: Dict[str, Any]) -> Dict[str, Any]:
    signal = _dict(judgment.get("signal"))
    signal_payload = _dict(signal.get("payload"))
    return signal_payload or signal or judgment


def _cross(value: Dict[str, Any]) -> Dict[str, Any]:
    direct = _dict(value.get("crossValidation"))
    if direct:
        return direct
    source = _source_payload(value)
    direct = _dict(source.get("crossValidation"))
    if direct:
        return direct
    raw_agent1 = _dict(value.get("rawAgent1Judgment"))
    source = _source_payload(raw_agent1)
    return _dict(source.get("crossValidation"))


def _policy(value: Dict[str, Any]) -> Dict[str, Any]:
    cross = _cross(value)
    return _dict(cross.get("experimentPolicy")) or _dict(value.get("experimentPolicy"))


def _decision(value: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(_cross(value).get("decision"))


def _feature(value: Dict[str, Any], code: str) -> Dict[str, Any]:
    features = _dict(_cross(value).get("timeSeriesFeatures"))
    direct = _dict(features.get(code))
    if direct:
        return direct
    if code == "roi":
        return _dict(features.get("roas"))
    if code == "roas":
        return _dict(features.get("roi"))
    return {}


def _all_text(value: Any) -> str:
    parts: List[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item[:50]:
                visit(child)
        elif isinstance(item, str):
            parts.append(item)

    visit(value)
    return " ".join(parts).lower()


def _baseline_only(value: Dict[str, Any]) -> bool:
    cross = _cross(value)
    policy = _policy(value)
    maturity = _dict(cross.get("observationMaturity")) or _dict(
        value.get("observationMaturity")
    )
    source_count = int(cross.get("sourceVersionCount") or 0)
    maturity_code = _text(maturity.get("maturity"))
    mode = _lower(policy.get("experimentMode"))
    allowed = policy.get("allowed")
    return bool(
        source_count == 1
        or maturity_code == "M0_baseline"
        or mode == "baseline_only"
        or (allowed is False and mode in {"", "baseline_only"})
    )


def _paid_efficiency_support(value: Dict[str, Any]) -> Dict[str, Any]:
    decision = _decision(value)
    hypothesis = _lower(decision.get("hypothesisCode"))
    status = _lower(decision.get("status"))
    primary = _dict(decision.get("primaryEvidence"))
    primary_code = _lower(primary.get("metricCode"))
    primary_direction = _lower(primary.get("direction"))
    primary_magnitude = _number(primary.get("magnitude")) or 0.0

    feature = _feature(value, "roi")
    deltas = [
        _number(feature.get(key))
        for key in ("previousDelta", "mom", "yoy", "slope5", "slope10", "slope30")
    ]
    downward_values = [number for number in deltas if number is not None and number <= -0.03]
    temporal_down = bool(downward_values)
    confirmed_graph = bool(
        hypothesis == "paid_efficiency_decline"
        and status == "confirmed"
        and primary_code in PAID_EFFICIENCY_METRICS
        and primary_direction == "down"
        and (primary_magnitude >= 0.03 or temporal_down)
    )

    text = _all_text(
        {
            "finding": value.get("finding"),
            "businessHypothesis": value.get("businessHypothesis"),
            "agent1OperatingJudgment": value.get("agent1OperatingJudgment"),
            "requiredActionData": value.get("requiredActionData"),
        }
    )
    safety_line = bool(
        re.search(
            r"(roi|roas).{0,20}(低于|跌破|below).{0,20}(安全线|利润线|profit|safety)|"
            r"(安全线|利润线).{0,20}(被跌破|breach|低于)",
            text,
            flags=re.IGNORECASE,
        )
    )
    return {
        "supported": bool(confirmed_graph or safety_line),
        "confirmedGraph": confirmed_graph,
        "safetyLineEvidence": safety_line,
        "hypothesisCode": hypothesis,
        "validationStatus": status,
        "primaryMetricCode": primary_code,
        "primaryDirection": primary_direction,
        "primaryMagnitude": primary_magnitude,
        "roiDownwardSignals": downward_values,
    }


def _inventory_pressure(value: Dict[str, Any]) -> Dict[str, Any]:
    cross = _cross(value)
    changed = {str(item) for item in _arr(cross.get("changedMetrics"))}
    abnormal = {str(item) for item in _arr(cross.get("abnormalMetrics"))}
    metrics: Dict[str, Any] = {}
    pressure = False
    for code in ("inventory", "availableDays"):
        feature = _feature(value, code)
        signals = {
            key: _number(feature.get(key))
            for key in ("previousDelta", "mom", "yoy", "slope5", "slope10", "slope30")
        }
        adverse = [number for number in signals.values() if number is not None and number <= -0.03]
        if adverse or code in changed or code in abnormal:
            pressure = True
        metrics[code] = {
            "signals": signals,
            "adverseSignals": adverse,
            "changed": code in changed,
            "abnormal": code in abnormal,
        }

    text = _all_text(
        {
            "capacityConstraints": value.get("capacityConstraints"),
            "companyHooks": value.get("companyHooks"),
            "finding": value.get("finding"),
            "agent1OperatingJudgment": value.get("agent1OperatingJudgment"),
        }
    )
    textual = any(
        marker in text
        for marker in (
            "库存",
            "缺货",
            "断货",
            "补货",
            "可售天数",
            "inventory",
            "stockout",
            "sellable days",
        )
    )
    return {
        "present": bool(pressure or textual),
        "metricPressure": pressure,
        "textualConstraint": textual,
        "metrics": metrics,
    }


def _observation_judgment(
    judgment: Dict[str, Any],
    *,
    reason: str,
    guard: Dict[str, Any],
) -> Dict[str, Any]:
    value = copy.deepcopy(judgment)
    original_family = _text(
        (value.get("lockedActionFamily") or value.get("selectedActionFamilyHint"))
        or _dict(value.get("actionFamilyLock")).get("selectedActionFamily")
    )
    original_route = _text(value.get("selectedOperatingRoute"))
    route_lock = {
        "locked": True,
        "selectedOperatingRoute": "observe",
        "lockReason": reason,
    }
    family_lock = {
        "locked": True,
        "selectedActionFamily": None,
        "lockReason": "库存约束或首份基线不进入Agent2、SOP或任务池",
        "forbiddenOverride": True,
        "observationOnly": True,
    }
    agent1 = dict(_dict(value.get("agent1OperatingJudgment")))
    agent1.update(
        selectedOperatingRoute="observe",
        selectedActionFamily=None,
        routeLock=route_lock,
        actionFamilyLock=family_lock,
    )
    value.update(
        decisionHint="observe_only",
        finding=reason,
        selectedOperatingRoute="observe",
        selectedActionFamilyHint=None,
        routeLock=route_lock,
        actionFamilyLock=family_lock,
        agent1OperatingJudgment=agent1,
        observationOnly=True,
        actionable=False,
        taskAdmissionAllowed=False,
        observationDisposition="observed_soft_gate",
        observationDeposited=True,
        inventoryActionGuard={
            "version": INVENTORY_ACTION_GUARD_VERSION,
            "reason": guard.get("reason"),
            "originalActionFamily": original_family,
            "originalOperatingRoute": original_route,
            **guard,
        },
    )
    return value


def guard_agent1_judgment(judgment: Dict[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(judgment)
    family = _lower(
        (value.get("lockedActionFamily") or value.get("selectedActionFamilyHint"))
        or _dict(value.get("actionFamilyLock")).get("selectedActionFamily")
    )
    if _baseline_only(value) and family:
        return _observation_judgment(
            value,
            reason="首份报表只建立商品指标基线，等待下一份报表形成可比趋势",
            guard={
                "reason": "first_report_baseline_must_not_enter_action_chain",
                "baselineOnly": True,
            },
        )
    if family != "roas_guard":
        return value

    paid = _paid_efficiency_support(value)
    inventory = _inventory_pressure(value)
    if paid["supported"]:
        value["inventoryActionGuard"] = {
            "version": INVENTORY_ACTION_GUARD_VERSION,
            "reason": "paid_efficiency_independently_supported",
            "paidEfficiency": paid,
            "inventoryConstraint": inventory,
            "inventoryMayConstrainButNotAuthorizeTrafficAction": True,
        }
        return value

    return _observation_judgment(
        value,
        reason=(
            "当前没有独立的ROI/ROAS恶化或利润安全线证据；库存与可售天数只作为仓储约束沉淀，"
            "不得生成运营断流或ROAS任务"
        ),
        guard={
            "reason": "roas_guard_without_paid_efficiency_evidence",
            "paidEfficiency": paid,
            "inventoryConstraint": inventory,
            "inventoryOnly": inventory["present"],
        },
    )


def _package_family(package: Dict[str, Any]) -> str:
    matrix = _dict(package.get("matrixDispatch"))
    agent1 = _dict(package.get("agent1OperatingJudgment"))
    return _lower(
        package.get("actionFamily")
        or (package.get("lockedActionFamily") or package.get("selectedActionFamilyHint"))
        or matrix.get("selectedActionFamily")
        or agent1.get("selectedActionFamily")
    )


def _target_selector(package: Dict[str, Any], policy: Dict[str, Any]) -> str:
    product_id = _text(package.get("productId") or _dict(package.get("productIdentity")).get("productId"))
    sku_id = _text(package.get("skuId") or _dict(package.get("productIdentity")).get("skuId"))
    store_id = _text(package.get("storeId") or _dict(package.get("productIdentity")).get("storeId"))
    target = _text(policy.get("targetObject") or "new_ad_plan")
    scope = _text(policy.get("operationScope") or policy.get("experimentMode") or "isolated_test")
    parts = [
        f"productId={product_id}" if product_id else "",
        f"skuId={sku_id}" if sku_id else "",
        f"storeId={store_id}" if store_id else "",
        f"create={target}",
        f"scope={scope}",
    ]
    return ";".join(part for part in parts if part)


def _cap_permission_values(value: Any, policy: Dict[str, Any], audit: List[Dict[str, Any]], path: str = "") -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, child in value.items():
            key_lower = str(key).lower()
            child_path = f"{path}.{key}" if path else str(key)
            ceiling = None
            parser = _ratio
            if any(token in key_lower for token in ("trafficshare", "traffic_share", "flowshare", "流量占比")):
                ceiling = _ratio(policy.get("trafficShareCeiling"))
            elif any(
                token in key_lower
                for token in (
                    "budgetchangerate",
                    "budget_change_rate",
                    "预算调整比例",
                    "预算变化比例",
                )
            ):
                ceiling = _ratio(policy.get("budgetChangeCeiling"))
            if ceiling is not None:
                number = parser(child)
                if number is not None and number > ceiling:
                    result[key] = ceiling
                    audit.append(
                        {
                            "path": child_path,
                            "original": number,
                            "normalized": ceiling,
                            "reason": "upstream_experiment_ceiling",
                        }
                    )
                    continue
            result[key] = _cap_permission_values(child, policy, audit, child_path)
        return result
    if isinstance(value, list):
        return [
            _cap_permission_values(child, policy, audit, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    return value


def align_agent2_raw_to_action_family(
    raw: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    aligned = copy.deepcopy(raw)
    family = _package_family(package)
    policy = _policy(package)
    audit: List[Dict[str, Any]] = []
    if family not in ROAS_FAMILIES:
        return aligned

    expected_mode = _lower(policy.get("experimentMode"))
    current_mode = _lower(aligned.get("operationMode"))
    if expected_mode in ISOLATED_MODES and current_mode not in ISOLATED_MODES:
        aligned["operationMode"] = expected_mode
        audit.append(
            {
                "path": "operationMode",
                "original": current_mode or None,
                "normalized": expected_mode,
                "reason": "upstream_experiment_mode",
            }
        )

    execution = dict(_dict(aligned.get("executionObject")))
    selector = _lower(execution.get("targetSelector"))
    target_type = _lower(execution.get("targetType") or execution.get("type"))
    if (
        not execution.get("targetId")
        and (
            not execution.get("targetSelector")
            or "inventory_coordination_ticket" in selector
            or target_type in {"inventory_ticket", "warehouse_ticket"}
        )
    ):
        replacement = _target_selector(package, policy)
        audit.append(
            {
                "path": "executionObject.targetSelector",
                "original": execution.get("targetSelector"),
                "normalized": replacement,
                "reason": "roas_family_executes_on_ad_plan_not_inventory_ticket",
            }
        )
        execution = {
            **execution,
            "targetSelector": replacement,
            "targetType": "ad_plan",
        }
        execution.pop("targetId", None)
    aligned["executionObject"] = execution
    aligned = _cap_permission_values(aligned, policy, audit)
    aligned["inventoryActionGuardAlignment"] = {
        "version": INVENTORY_ACTION_GUARD_VERSION,
        "actionFamily": family,
        "inventoryIsConstraintNotTrafficAuthority": True,
        "normalizations": audit,
    }
    return aligned


def _failure_missing(payload: Dict[str, Any]) -> List[str]:
    plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
    result: List[str] = []
    for value in _arr(payload.get("missing")) + _arr(plan.get("semanticContractMissing")):
        item = _text(value)
        if item and item not in result:
            result.append(item)
    return result


def classify_inventory_failure(payload: Dict[str, Any], action_family: str | None = None) -> Dict[str, Any]:
    plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
    execution = _dict(plan.get("executionObject"))
    selector = _lower(execution.get("targetSelector"))
    missing = _failure_missing(payload)
    combined = " ".join([*missing, _text(payload.get("reason")), _text(plan.get("conflictReason"))]).lower()
    family = _lower(action_family or payload.get("actionFamily") or (payload.get("lockedActionFamily") or payload.get("selectedActionFamilyHint")))
    inventory_route = bool(
        "inventory_coordination_ticket" in selector
        or "inventory_cannot_directly_cut_operator_traffic" in combined
    )
    paid = _paid_efficiency_support(payload)
    return {
        "matched": bool(family in ROAS_FAMILIES and inventory_route),
        "actionFamily": family,
        "inventoryRoute": inventory_route,
        "paidEfficiency": paid,
        "disposition": (
            "requeue_agent2_with_roas_scope"
            if inventory_route and paid["supported"]
            else "observe_inventory_only"
            if inventory_route
            else "leave_untouched"
        ),
    }


def _clean_agent2_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in _AGENT2_RESULT_FIELDS
    }


def recover_inventory_action_misroutes(
    data_version: str | None,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    if not data_version:
        return {
            "version": INVENTORY_ACTION_GUARD_VERSION,
            "dataVersion": None,
            "observedCount": 0,
            "requeuedCount": 0,
        }

    from src.services.pipeline_item_service import (
        build_item_envelope,
        record_pipeline_item_event,
    )

    observed = requeued = 0
    events: List[Tuple[Dict[str, Any], str, Dict[str, Any], str]] = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pipeline_items
            WHERE data_version=?
              AND current_stage='agent2_output_invalid'
              AND status='failed'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (data_version, max(1, min(200, int(limit or 50)))),
        ).fetchall()
        for row in rows:
            outer = loads(row["payload"]) if row["payload"] else {}
            outer = outer if isinstance(outer, dict) else {}
            payload = _payload(outer)
            previous_recovery = _dict(payload.get("inventoryActionGuardRecovery"))
            if previous_recovery.get("version") == INVENTORY_ACTION_GUARD_VERSION:
                continue
            classification = classify_inventory_failure(payload, row["action_family"])
            if not classification["matched"]:
                continue

            cleaned = _clean_agent2_payload(payload)
            disposition = classification["disposition"]
            recovery = {
                "version": INVENTORY_ACTION_GUARD_VERSION,
                "disposition": disposition,
                "previousStage": row["current_stage"],
                "previousActionFamily": row["action_family"],
                "previousMissing": _failure_missing(payload),
                "classification": classification,
                "singleRecovery": True,
            }
            cleaned["inventoryActionGuardRecovery"] = recovery
            if disposition == "observe_inventory_only":
                cleaned = _observation_judgment(
                    cleaned,
                    reason=(
                        "库存与可售天数只作为仓储约束沉淀；当前没有独立投放效率证据，"
                        "不进入Agent2、SOP或任务池"
                    ),
                    guard={
                        "reason": "historical_agent2_inventory_misroute_to_observation",
                        "recovery": recovery,
                    },
                )
                stage = "observed_soft_gate"
                status = "observed"
                route = "observe"
                family = None
                output_ref = f"inventory_guard_observed:{data_version}:{row['item_id']}"
                observed += 1
            else:
                cleaned["inventoryActionGuardRecovery"] = recovery
                stage = "action_pack_ready"
                status = "retry"
                route = row["route"]
                family = row["action_family"]
                output_ref = f"inventory_guard_requeue:{data_version}:{row['item_id']}"
                requeued += 1

            if isinstance(outer.get("payload"), dict):
                outer["payload"] = cleaned
                envelope = dict(_dict(outer.get("envelope")))
                envelope["stage"] = stage
                envelope["route"] = route
                envelope["actionFamily"] = family
                outer["envelope"] = envelope
                stored = outer
            else:
                stored = cleaned
            conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?, status=?, route=?, action_family=?,
                    retry_count=0, error_reason=NULL, output_ref=?, payload=?, updated_at=?
                WHERE item_id=?
                """,
                (
                    stage,
                    status,
                    route,
                    family,
                    output_ref,
                    dumps(stored),
                    datetime.now().isoformat(),
                    row["item_id"],
                ),
            )
            envelope = build_item_envelope(
                data_version=row["data_version"],
                item_id=row["item_id"],
                product_id=row["product_id"],
                store_id=row["store_id"],
                signal_id=row["signal_id"],
                package_id=row["package_id"],
                decision_id=row["decision_id"],
                action_family=family,
                route=route,
                output_ref=output_ref,
                stage=stage,
            )
            events.append((envelope, status, cleaned, output_ref))
        conn.commit()

    for envelope, status, payload, output_ref in events:
        record_pipeline_item_event(
            envelope,
            station_id="inventory_action_guard_station",
            stage=envelope.get("stage"),
            status=status,
            output_ref=output_ref,
            payload=payload,
        )
    return {
        "version": INVENTORY_ACTION_GUARD_VERSION,
        "dataVersion": data_version,
        "observedCount": observed,
        "requeuedCount": requeued,
        "recoveredItemCount": observed + requeued,
        "rule": "Inventory-only ROAS misroutes become observations; independently supported paid-efficiency items replay Agent2 once inside the locked experiment scope.",
    }


def apply_attention_headline(result: Dict[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(result)
    summary = _dict(value.get("summary"))
    failed = int(summary.get("failed") or 0)
    running = int(summary.get("running") or 0)
    queued = int(summary.get("queued") or 0)
    observed = int(summary.get("observedDeposited") or summary.get("observedSignalCount") or 0)
    if failed:
        value["flowStatus"] = "attention"
        if running or queued:
            value["headline"] = (
                f"本轮仍在处理中 · 观察沉淀{observed} · 异常{failed}"
            )
        else:
            value["headline"] = (
                f"本轮处理结束 · 观察沉淀{observed} · 异常{failed}"
            )
    value["processingComplete"] = bool(
        value.get("ready") and not failed and not running and not queued
    )
    value["inventoryActionGuardVersion"] = INVENTORY_ACTION_GUARD_VERSION
    return value


def install_v2172_inventory_action_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
    from src.services import pipeline_agent1_microbatch_v20101_service as agent1_worker
    from src.services import pipeline_live_read_model_v208_service as live
    from src.services import real_product_judgment_agent_v196_service as agent1

    if getattr(agent1, "_V2172_INVENTORY_ACTION_GUARD_INSTALLED", False):
        _INSTALLED = True
        return

    original_agent1_judgments = agent1._real_agent_judgments
    original_agent2_messages = agent2._build_messages
    original_agent2_normalize = agent2._normalize_plan
    original_pipeline_tick = pipeline_worker.run_agent_pipeline_tick
    original_live_read = live._read_pipeline_live_model

    def real_agent_judgments_v2172(
        signals: List[Dict[str, Any]],
        data_version: str | None,
        rag_context: Dict[str, Any] | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        judgments, provider = original_agent1_judgments(
            signals,
            data_version,
            rag_context,
        )
        guarded = [guard_agent1_judgment(item) for item in judgments]
        observed = sum(
            1 for item in guarded if _lower(item.get("decisionHint")) in OBSERVATION_HINTS
        )
        changed = sum(
            1
            for before, after in zip(judgments, guarded)
            if (before.get("lockedActionFamily") or before.get("selectedActionFamilyHint"))
            and not (after.get("lockedActionFamily") or after.get("selectedActionFamilyHint"))
        )
        provider = {
            **provider,
            "inventoryActionGuardVersion": INVENTORY_ACTION_GUARD_VERSION,
            "inventoryActionGuardObservedCount": observed,
            "inventoryActionFamilyClearedCount": changed,
        }
        return guarded, provider

    def build_messages_v2172(
        data_version: str | None,
        packages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        messages, payload = original_agent2_messages(data_version, packages)
        messages[0]["content"] += (
            "V21.7.2库存与投放边界：库存、可售天数、断货和补货只能写入capacityConstraints或companyHooks，"
            "不能成为暂停广告、停止放量、切断流量或修改ROAS的原因。ROAS动作的executionObject必须是"
            "experimentPolicy指定的新建/隔离广告计划，禁止使用inventory_coordination_ticket作为执行对象。"
            "operationMode必须等于experimentPolicy.experimentMode；所有流量占比和预算变化比例不得超过上游上限。"
            "若没有独立ROI/ROAS恶化或利润安全线证据，不得生成roas_guard方案。"
        )
        payload["inventoryActionGuardVersion"] = INVENTORY_ACTION_GUARD_VERSION
        return messages, payload

    def normalize_plan_v2172(
        raw: Dict[str, Any],
        package: Dict[str, Any],
        proof: Dict[str, Any],
    ) -> Dict[str, Any]:
        aligned = align_agent2_raw_to_action_family(raw, package)
        plan = original_agent2_normalize(aligned, package, proof)
        plan["inventoryActionGuardVersion"] = INVENTORY_ACTION_GUARD_VERSION
        plan["inventoryIsConstraintNotTrafficAuthority"] = True
        return plan

    def run_pipeline_tick_v2172(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        data_version = kwargs.get("data_version")
        if data_version is None and args:
            data_version = args[0]
        if not data_version:
            data_version = pipeline_worker.latest_data_version()
        recovery = recover_inventory_action_misroutes(data_version)
        result = original_pipeline_tick(*args, **kwargs)
        result["inventoryActionGuardVersion"] = INVENTORY_ACTION_GUARD_VERSION
        result["inventoryActionGuardRecovery"] = recovery
        return result

    def read_pipeline_live_v2172(
        data_version: str | None = None,
        *,
        limit: int = 80,
    ) -> Dict[str, Any]:
        return apply_attention_headline(
            original_live_read(data_version=data_version, limit=limit)
        )

    agent1._real_agent_judgments = real_agent_judgments_v2172
    agent1_worker._real_agent_judgments = real_agent_judgments_v2172
    agent2._build_messages = build_messages_v2172
    agent2._normalize_plan = normalize_plan_v2172
    pipeline_worker.run_agent_pipeline_tick = run_pipeline_tick_v2172
    live._read_pipeline_live_model = read_pipeline_live_v2172

    agent1.INVENTORY_ACTION_GUARD_VERSION = INVENTORY_ACTION_GUARD_VERSION
    agent2.INVENTORY_ACTION_GUARD_VERSION = INVENTORY_ACTION_GUARD_VERSION
    live.INVENTORY_ACTION_GUARD_VERSION = INVENTORY_ACTION_GUARD_VERSION
    agent1._V2172_INVENTORY_ACTION_GUARD_INSTALLED = True
    agent1_worker._V2172_INVENTORY_ACTION_GUARD_INSTALLED = True
    agent2._V2172_INVENTORY_ACTION_GUARD_INSTALLED = True
    live._V2172_INVENTORY_ACTION_GUARD_INSTALLED = True
    _INSTALLED = True


__all__ = [
    "INVENTORY_ACTION_GUARD_VERSION",
    "align_agent2_raw_to_action_family",
    "apply_attention_headline",
    "classify_inventory_failure",
    "guard_agent1_judgment",
    "install_v2172_inventory_action_guard",
    "recover_inventory_action_misroutes",
]
