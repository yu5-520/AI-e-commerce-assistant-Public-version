"""V26.9.A production admission for the canonical three-graph business chain.

This service is the only V26.9 task-pool admission path. It does not translate
legacy primary-action semantics into the new graph contract. It verifies the
accepted Agent execution chain, evaluates the whole PlanGraph under the existing
authority tables, creates an idempotent quota reservation, materializes the task,
and commits task-pool admission + quota usage in one SQLite transaction.

TaskSnapshot is materialized before the final admission transaction because the
existing snapshot station owns product-lineage freezing. A failed final admission
never consumes quota and the reservation is released; an orphan snapshot is not a
formal task-pool admission and is safe to recover by the existing snapshot/dedupe
mechanisms.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Dict
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services import action_authority_v214_service as authority
from src.services import v269_semantic_graph_service as graphs
from src.services.task_snapshot_station_service import create_task_snapshot

VERSION = "26.9.0"
RESERVATION_SCHEMA = "v269.graph_authority_reservation.v1"
TASK_ADMISSION_SCHEMA = "v269.graph_task_admission.v1"


class GraphReservationConflict(ValueError):
    pass


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _reservation_ttl_seconds() -> int:
    try:
        value = int(os.getenv("V269_AUTHORITY_RESERVATION_TTL_SECONDS", "900"))
    except Exception:
        value = 900
    return max(60, min(3600, value))


def ensure_graph_reservation_tables() -> None:
    authority.ensure_action_authority_tables()
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS v269_graph_authority_reservations (
                reservation_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                plan_graph_hash TEXT NOT NULL,
                authority_receipt_hash TEXT NOT NULL,
                policy_hash TEXT NOT NULL,
                resource_usage_receipt_hash TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                store_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                adjustment_amount REAL NOT NULL,
                status TEXT NOT NULL,
                task_id TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "v269_graph_authority_reservations",
            {
                "task_id": "TEXT",
                "payload": "TEXT NOT NULL DEFAULT '{}'",
                "expires_at": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v269_graph_reservation_operator_status "
            "ON v269_graph_authority_reservations(operator_id,status,expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v269_graph_reservation_plan "
            "ON v269_graph_authority_reservations(plan_graph_hash,status)"
        )
        conn.commit()


def _reservation_key(authorization: Dict[str, Any]) -> str:
    evaluation = authorization.get("evaluation") or {}
    usage = evaluation.get("resourceUsage") or {}
    material = {
        "schema": RESERVATION_SCHEMA,
        "planGraphHash": evaluation.get("planGraphHash"),
        "authorityReceiptHash": evaluation.get("receiptHash"),
        "policyHash": evaluation.get("policyHash"),
        "resourceUsageReceiptHash": usage.get("receiptHash"),
        "operatorId": authorization.get("operatorId"),
        "storeId": authorization.get("storeId"),
        "productId": authorization.get("productId"),
    }
    return graphs.digest(material)


def _current_usage(conn: Any, operator_id: str) -> tuple[float, float]:
    now = _now_dt()
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    rolling_start = (now - timedelta(hours=24)).isoformat()
    day = conn.execute(
        """SELECT COALESCE(SUM(ABS(adjustment_amount)),0) AS value
           FROM action_authority_usage
           WHERE user_id=? AND status IN ('authorized','executed') AND occurred_at>=?""",
        (operator_id, day_start),
    ).fetchone()
    rolling = conn.execute(
        """SELECT COALESCE(SUM(ABS(adjustment_amount)),0) AS value
           FROM action_authority_usage
           WHERE user_id=? AND status IN ('authorized','executed') AND occurred_at>=?""",
        (operator_id, rolling_start),
    ).fetchone()
    return float(day["value"] or 0), float(rolling["value"] or 0)


def _active_reserved_amount(conn: Any, operator_id: str, *, exclude_key: str) -> float:
    row = conn.execute(
        """SELECT COALESCE(SUM(adjustment_amount),0) AS value
           FROM v269_graph_authority_reservations
           WHERE operator_id=? AND status='reserved' AND expires_at>?
             AND idempotency_key<>?""",
        (operator_id, _now(), exclude_key),
    ).fetchone()
    return float(row["value"] or 0)


def _reservation_view(row: Any, *, idempotent_hit: bool = False) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {
        "schema": RESERVATION_SCHEMA,
        "version": VERSION,
        "reservationId": row["reservation_id"],
        "idempotencyKey": row["idempotency_key"],
        "planGraphHash": row["plan_graph_hash"],
        "authorityReceiptHash": row["authority_receipt_hash"],
        "policyHash": row["policy_hash"],
        "resourceUsageReceiptHash": row["resource_usage_receipt_hash"],
        "operatorId": row["operator_id"],
        "storeId": row["store_id"],
        "productId": row["product_id"],
        "adjustmentAmount": float(row["adjustment_amount"] or 0),
        "status": row["status"],
        "taskId": row["task_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "expiresAt": row["expires_at"],
        "idempotentHit": bool(idempotent_hit),
        "payloadHash": graphs.digest(payload),
    }


def reserve_plan_authority(decision: Dict[str, Any], authorization: Dict[str, Any]) -> Dict[str, Any]:
    """Reserve the whole-plan monetary envelope under BEGIN IMMEDIATE.

    Existing usage and other live V26.9 reservations are re-read while holding the
    SQLite write lock. The earlier pure authorization evaluation is therefore not a
    race-prone permission grant.
    """
    ensure_graph_reservation_tables()
    graphs.require(authorization.get("decision") == authority.AUTO_EXECUTE, "reservation_requires_auto_execute")
    evaluation = authorization.get("evaluation") or {}
    usage = evaluation.get("resourceUsage") or {}
    graphs.require(evaluation.get("receiptHash") == graphs.digest({k: v for k, v in evaluation.items() if k != "receiptHash"}), "reservation_authority_receipt")
    graphs.require(usage.get("receiptHash") == graphs.digest({k: v for k, v in usage.items() if k != "receiptHash"}), "reservation_usage_receipt")
    graphs.require(evaluation.get("planGraphHash") == decision.get("PlanGraph", {}).get("graphHash"), "reservation_plan_mismatch")
    operator_id = str(authorization.get("operatorId") or "")
    store_id = str(authorization.get("storeId") or "")
    product_id = str(authorization.get("productId") or "")
    graphs.require(all((operator_id, store_id, product_id)), "reservation_identity_missing")
    key = _reservation_key(authorization)
    amount = float(usage.get("totalAdjustmentAmount") or 0)
    policy = deepcopy(evaluation.get("policy") or {})
    graphs.require(policy.get("source") == "existing_operator_action_authority", "reservation_policy_source")
    now = _now()
    expires = (_now_dt() + timedelta(seconds=_reservation_ttl_seconds())).isoformat()
    payload = {
        "schema": RESERVATION_SCHEMA,
        "version": VERSION,
        "authorization": deepcopy(authorization),
        "resourceUsage": deepcopy(usage),
        "decisionGraphHash": decision.get("DecisionGraph", {}).get("graphHash"),
        "admissionHash": (decision.get("actionAdmission") or {}).get("receiptHash"),
        "planGraphHash": decision.get("PlanGraph", {}).get("graphHash"),
        "operationGraphHash": decision.get("OperationGraph", {}).get("graphHash"),
    }
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM v269_graph_authority_reservations WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if existing and existing["status"] in {"reserved", "committed"} and (
            existing["status"] == "committed" or str(existing["expires_at"] or "") > now
        ):
            conn.commit()
            return _reservation_view(existing, idempotent_hit=True)

        used_today, used_rolling = _current_usage(conn, operator_id)
        reserved = _active_reserved_amount(conn, operator_id, exclude_key=key)
        effective_policy = deepcopy(policy)
        effective_policy["usedToday"] = used_today + reserved
        effective_policy["usedRolling24h"] = used_rolling + reserved
        refreshed = graphs.evaluate_plan_authority(decision["PlanGraph"], decision.get("factValues", {}), effective_policy)
        if refreshed.get("decision") != authority.AUTO_EXECUTE:
            conn.rollback()
            raise GraphReservationConflict(
                "v269_graph_reservation_conflict:" + ",".join(refreshed.get("reasons") or ["authority_changed"])
            )
        reservation_id = (
            str(existing["reservation_id"])
            if existing
            else "V269-RSV-" + graphs.digest({"key": key})[-20:].upper()
        )
        if existing:
            conn.execute(
                """UPDATE v269_graph_authority_reservations
                   SET plan_graph_hash=?,authority_receipt_hash=?,policy_hash=?,
                       resource_usage_receipt_hash=?,operator_id=?,store_id=?,product_id=?,
                       adjustment_amount=?,status='reserved',task_id=NULL,payload=?,
                       updated_at=?,expires_at=? WHERE idempotency_key=?""",
                (
                    evaluation["planGraphHash"], evaluation["receiptHash"], evaluation["policyHash"],
                    usage["receiptHash"], operator_id, store_id, product_id, amount,
                    dumps(payload), now, expires, key,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO v269_graph_authority_reservations(
                   reservation_id,idempotency_key,plan_graph_hash,authority_receipt_hash,
                   policy_hash,resource_usage_receipt_hash,operator_id,store_id,product_id,
                   adjustment_amount,status,task_id,payload,created_at,updated_at,expires_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,'reserved',NULL,?,?,?,?,?)""",
                (
                    reservation_id, key, evaluation["planGraphHash"], evaluation["receiptHash"],
                    evaluation["policyHash"], usage["receiptHash"], operator_id, store_id,
                    product_id, amount, dumps(payload), now, now, expires,
                ),
            )
        row = conn.execute(
            "SELECT * FROM v269_graph_authority_reservations WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        conn.commit()
    return _reservation_view(row)


def _commit_reservation_in_conn(conn: Any, reservation_id: str, task_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM v269_graph_authority_reservations WHERE reservation_id=?",
        (reservation_id,),
    ).fetchone()
    if not row:
        raise ValueError("v269_reservation_not_found")
    if row["status"] == "committed":
        if str(row["task_id"] or "") != str(task_id):
            raise ValueError("v269_reservation_committed_to_other_task")
        return _reservation_view(row, idempotent_hit=True)
    if row["status"] != "reserved" or str(row["expires_at"] or "") <= _now():
        raise ValueError("v269_reservation_not_active")
    payload = loads(row["payload"])
    usage = (payload.get("resourceUsage") or {})
    authorization = (payload.get("authorization") or {})
    occurred = _now()
    for action in usage.get("actions") or []:
        amount = float(action.get("adjustmentAmount") or 0)
        family = str(action.get("actionFamily") or "")
        if not family:
            raise ValueError("v269_reservation_action_family_missing")
        budget_ops = [op for op in action.get("operations") or [] if op.get("operationType") == "budget_update"]
        roas_ops = [op for op in action.get("operations") or [] if op.get("operationType") == "target_roas_update"]
        current_budget = budget_ops[0].get("currentValue") if budget_ops else None
        target_budget = budget_ops[0].get("targetValue") if budget_ops else None
        current_roas = roas_ops[0].get("currentValue") if roas_ops else None
        target_roas = roas_ops[0].get("targetValue") if roas_ops else None
        conn.execute(
            """INSERT INTO action_authority_usage(
               user_id,store_id,product_id,action_family,task_id,adjustment_amount,
               current_budget,target_budget,current_roas,target_roas,status,occurred_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                authorization.get("operatorId"), authorization.get("storeId"),
                authorization.get("productId"), family, task_id, amount,
                current_budget, target_budget, current_roas, target_roas,
                "authorized", occurred,
            ),
        )
    conn.execute(
        """UPDATE v269_graph_authority_reservations
           SET status='committed',task_id=?,updated_at=? WHERE reservation_id=? AND status='reserved'""",
        (task_id, occurred, reservation_id),
    )
    row = conn.execute(
        "SELECT * FROM v269_graph_authority_reservations WHERE reservation_id=?",
        (reservation_id,),
    ).fetchone()
    return _reservation_view(row)


def release_reservation(reservation_id: str, *, reason: str) -> Dict[str, Any]:
    ensure_graph_reservation_tables()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM v269_graph_authority_reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return {"released": False, "reason": "reservation_not_found"}
        if row["status"] == "committed":
            conn.rollback()
            return {"released": False, "reason": "reservation_already_committed"}
        payload = loads(row["payload"])
        payload["releaseReason"] = str(reason)[:500]
        conn.execute(
            """UPDATE v269_graph_authority_reservations
               SET status='released',payload=?,updated_at=? WHERE reservation_id=?""",
            (dumps(payload), _now(), reservation_id),
        )
        conn.commit()
    return {"released": True, "reservationId": reservation_id, "reason": reason}


def _priority(decision: Dict[str, Any]) -> str:
    nodes = graphs.index(decision["DecisionGraph"])
    scores = [float(node.get("priority") or 0) for node in nodes.values() if node.get("kind") == "DecisionActionNode"]
    top = max(scores or [0])
    return "高" if top >= 0.8 else "中" if top >= 0.45 else "低"


def _graph_snapshot_body(decision: Dict[str, Any], authorization: Dict[str, Any], chain: Dict[str, Any]) -> Dict[str, Any]:
    mapping = graphs.map_task(
        decision["DecisionGraph"], decision["actionAdmission"],
        decision["PlanGraph"], decision["OperationGraph"],
    )
    operation_nodes = sorted(graphs.index(decision["OperationGraph"]).values(), key=lambda node: (node["sequence"], node["nodeKey"]))
    plan_nodes = graphs.index(decision["PlanGraph"])
    decision_nodes = graphs.index(decision["DecisionGraph"])
    instructions = [str(node["instruction"]).strip() for node in operation_nodes]
    acceptance = [
        str(item).strip()
        for node in operation_nodes
        for item in node.get("acceptanceActions") or []
        if str(item).strip()
    ]
    metrics = sorted({metric for node in plan_nodes.values() for metric in node.get("affectedMetrics") or []})
    admitted = set((decision.get("actionAdmission") or {}).get("admitted") or [])
    actions = [decision_nodes[key] for key in admitted if key in decision_nodes]
    action_names = [str(node.get("actionType") or node["nodeKey"]) for node in actions]
    title = str(decision.get("taskTitle") or (" + ".join(action_names[:3]) + "｜经营任务") or "V26.9经营任务")
    reason = "；".join(
        str(node.get("reasoning") or "").strip()
        for node in decision_nodes.values()
        if node.get("kind") == "JudgementNode" and str(node.get("reasoning") or "").strip()
    )[:1600] or "DecisionGraph 已通过系统准入并形成可执行 PlanGraph。"
    product_identity = {
        "productId": decision.get("productId"),
        "storeId": decision.get("storeId"),
        "productTitle": decision.get("productTitle") or decision.get("productId"),
    }
    formal = "manager_review_required" if authorization.get("approvalRequired") else "create_task_snapshot"
    task_plan = {
        "title": title,
        "taskTitle": title,
        "reason": reason,
        "trendJudgment": reason,
        "taskType": "v269_graph_execution",
        "actionType": "multi_action_plan",
        "priority": _priority(decision),
        "productId": decision.get("productId"),
        "storeId": decision.get("storeId"),
        "productIdentity": product_identity,
        "sopSource": "v269_operation_graph",
        "sopSteps": instructions,
        "steps": instructions,
        "operatorExecutionSop": instructions,
        "evidenceRequirements": acceptance or ["提交对应 OperationStage 的执行凭证"],
        "reviewMetrics": metrics,
        "approvalRequired": bool(authorization.get("approvalRequired")),
        "authorizationDecision": authorization,
        "taskGraphIdentity": mapping,
        "compilerAddedStepCount": 0,
    }
    evidence = {
        "pipelineItemId": decision.get("pipelineItemId") or decision.get("packageId") or mapping["receiptHash"],
        "noMappingLlm": True,
        "noAgent2Rerun": True,
        "noActionPackRerun": True,
        "itemized": True,
        "noLegacyRuntimeSource": True,
        "agent2ProviderTracePassed": chain.get("provenanceVerified") is True,
        "fallbackAllowed": False,
        "compilerAddedStepCount": 0,
        "graphExecutionChainHash": chain.get("receiptHash"),
    }
    return {
        "version": VERSION,
        "dataVersion": decision.get("dataVersion"),
        "decision": formal,
        "confidence": max(
            [float(node.get("confidence") or 0) for node in decision_nodes.values()] or [0]
        ),
        "entityType": "product",
        "entityId": decision.get("productId"),
        "productId": decision.get("productId"),
        "storeId": decision.get("storeId"),
        "signalRef": mapping["receiptHash"],
        "bundleRef": decision.get("packageId") or mapping["receiptHash"],
        "needManagerReview": bool(authorization.get("approvalRequired")),
        "taskPlan": task_plan,
        "operatorExecutionSop": instructions,
        "sopSteps": instructions,
        "reviewMetrics": metrics,
        "authorizationDecision": authorization,
        "actionAuthorization": authorization,
        "authorizationVersion": VERSION,
        "taskMappingAgentEvidence": evidence,
        "productIdentity": product_identity,
        "systemFacts": {
            "taskGenerationDecision": {
                "semanticContractVersion": VERSION,
                "DecisionGraph": decision["DecisionGraph"],
                "actionAdmission": decision["actionAdmission"],
                "PlanGraph": decision["PlanGraph"],
                "OperationGraph": decision["OperationGraph"],
                "taskGraphMapping": mapping,
                "executionChain": chain,
                "authorizationDecision": authorization,
                "fallbackAllowed": False,
            }
        },
        "source": "v269_canonical_graph_task_admission",
        "detailDisplayContract": "v269_decision_admission_plan_operation",
        "lifecycleReady": True,
    }


def _graph_task(snapshot: Dict[str, Any], decision: Dict[str, Any], authorization: Dict[str, Any]) -> Dict[str, Any]:
    task_id = "LT269-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:6].upper()
    plan = snapshot.get("taskPlan") or {}
    review = bool(authorization.get("approvalRequired"))
    task = {
        "id": task_id,
        "taskId": task_id,
        "dataVersion": decision.get("dataVersion"),
        "taskSnapshotId": snapshot.get("taskSnapshotId"),
        "semanticContractVersion": VERSION,
        "taskGenerationMode": "v269_canonical_graph_lifecycle",
        "sourceModule": "v269_production_admission_service",
        "source": "V26.9唯一业务语义图链",
        "decision": "manager_review_required" if review else "create_task_snapshot",
        "title": plan.get("title"),
        "entityType": "product",
        "entityId": decision.get("productId"),
        "productId": decision.get("productId"),
        "storeId": decision.get("storeId"),
        "taskType": "v269_graph_execution",
        "actionType": "multi_action_plan",
        "priority": plan.get("priority"),
        "taskLayer": "manager_dispatch" if review else "operator_execution",
        "status": "待审批" if review else "处理中",
        "workflowStatus": "待审批" if review else "处理中",
        "displayStatus": "待审批" if review else "处理中",
        "lifecycleStage": "approval_required" if review else "accepted",
        "assigneeId": None if review else authorization.get("operatorId"),
        "reviewerId": None,
        "sopSteps": deepcopy(plan.get("sopSteps") or []),
        "executionRequirements": deepcopy(plan.get("sopSteps") or []),
        "reviewMetrics": deepcopy(plan.get("reviewMetrics") or []),
        "authorizationDecision": deepcopy(authorization),
        "taskGraphIdentity": deepcopy(plan.get("taskGraphIdentity")),
        "DecisionGraph": decision["DecisionGraph"],
        "actionAdmission": decision["actionAdmission"],
        "PlanGraph": decision["PlanGraph"],
        "OperationGraph": decision["OperationGraph"],
        "createdAt": _now(),
        "updatedAt": _now(),
        "availableActions": ["source", "detail"] if review else ["submit", "source", "detail"],
    }
    return task


def _existing_entry(dedupe_key: str) -> Dict[str, Any] | None:
    from src.services import task_pool_admission_core_v20_service as legacy_pool
    legacy_pool._ensure_task_pool_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_pool_entries WHERE dedupe_key=? AND status='entered_task_pool' ORDER BY created_at DESC LIMIT 1",
            (dedupe_key,),
        ).fetchone()
    if not row:
        return None
    return {
        "poolEntryId": row["pool_entry_id"],
        "taskSnapshotId": row["task_snapshot_id"],
        "taskId": row["task_id"],
        "payload": loads(row["payload"]),
    }


def admit_graph_decision_to_task_pool(
    decision: Dict[str, Any], *, created_by: str | None = None, force_new_snapshot: bool = False
) -> Dict[str, Any]:
    """Admit only the canonical V26.9 graph contract; no legacy fallback."""
    from src.services.v269_input_migration_service import uses_graph_contract, verify_task_execution_chain
    from src.services import task_pool_admission_core_v20_service as legacy_pool

    if not uses_graph_contract(decision):
        return {"ok": False, "status": "v269_graph_contract_required", "createdTaskCount": 0}
    reservation_id = None
    try:
        chain = verify_task_execution_chain(decision)
        mapping = graphs.map_task(
            decision["DecisionGraph"], decision["actionAdmission"],
            decision["PlanGraph"], decision["OperationGraph"],
        )
        graphs.require(chain.get("mapping") == mapping, "task_execution_mapping_mismatch")
        authorization = authority.authorize_plan_graph(decision)
        if authorization.get("decision") == authority.AUTHORIZATION_DATA_MISSING:
            return {
                "ok": False,
                "status": "rejected_by_v269_authority_contract",
                "createdTaskCount": 0,
                "reason": authorization.get("reason"),
                "authorizationDecision": authorization,
            }
        formal = "manager_review_required" if authorization.get("approvalRequired") else "create_task_snapshot"
        dedupe_key = graphs.digest({
            "schema": TASK_ADMISSION_SCHEMA,
            "dataVersion": decision.get("dataVersion"),
            "storeId": decision.get("storeId"),
            "productId": decision.get("productId"),
            "mappingHash": mapping["receiptHash"],
            "authorizationReceiptHash": (authorization.get("evaluation") or {}).get("receiptHash"),
            "decision": formal,
        })
        if not force_new_snapshot:
            existing = _existing_entry(dedupe_key)
            if existing:
                return {
                    "ok": True, "status": "entered_task_pool", "idempotentHit": True,
                    "createdTaskCount": 0, "taskSnapshotId": existing["taskSnapshotId"],
                    "taskId": existing["taskId"], "taskGraphMapping": mapping,
                    "authorizationDecision": authorization, "contractVersion": VERSION,
                }
        if authorization.get("decision") == authority.AUTO_EXECUTE:
            reservation = reserve_plan_authority(decision, authorization)
            reservation_id = reservation["reservationId"]
            authorization = {**authorization, "reservation": reservation, "reservationCreated": True}
        else:
            authorization = {**authorization, "reservationCreated": False}

        snapshot = create_task_snapshot(
            _graph_snapshot_body(decision, authorization, chain),
            created_by=created_by,
            force=True,
        )
        task = _graph_task(snapshot, decision, authorization)
        legacy_pool._ensure_task_pool_tables()
        now = _now()
        entry_id = "TPE269-" + uuid4().hex[:20].upper()
        payload = {
            "schema": TASK_ADMISSION_SCHEMA,
            "version": VERSION,
            "snapshot": snapshot,
            "task": task,
            "taskGraphMapping": mapping,
            "executionChain": chain,
            "authorizationDecision": authorization,
            "reservationId": reservation_id,
            "legacyBusinessSemanticsUsed": False,
        }
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT task_id,task_snapshot_id FROM task_pool_entries WHERE dedupe_key=? AND status='entered_task_pool' LIMIT 1",
                (dedupe_key,),
            ).fetchone()
            if duplicate:
                conn.rollback()
                if reservation_id:
                    release_reservation(reservation_id, reason="idempotent_task_pool_hit")
                return {
                    "ok": True, "status": "entered_task_pool", "idempotentHit": True,
                    "createdTaskCount": 0, "taskSnapshotId": duplicate["task_snapshot_id"],
                    "taskId": duplicate["task_id"], "taskGraphMapping": mapping,
                    "authorizationDecision": authorization, "contractVersion": VERSION,
                }
            conn.execute(
                """INSERT INTO task_pool_entries(
                   pool_entry_id,task_snapshot_id,task_id,data_version,status,decision,task_layer,
                   assignee_id,reviewer_id,dedupe_key,reason,payload,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry_id, snapshot.get("taskSnapshotId"), task["taskId"],
                    decision.get("dataVersion"), "entered_task_pool", formal,
                    task.get("taskLayer"), task.get("assigneeId"), task.get("reviewerId"),
                    dedupe_key, authorization.get("reason") or "V26.9 graph authority completed.",
                    dumps(payload), created_by, now, now,
                ),
            )
            if reservation_id:
                committed = _commit_reservation_in_conn(conn, reservation_id, task["taskId"])
                task["authorizationDecision"]["reservation"] = committed
                task["authorizationDecision"]["reservationCreated"] = True
            conn.commit()
        return {
            "ok": True,
            "status": "entered_task_pool",
            "decision": formal,
            "taskSnapshotId": snapshot.get("taskSnapshotId"),
            "taskId": task["taskId"],
            "createdSnapshotCount": 1,
            "createdTaskCount": 1,
            "authorizationDecision": task.get("authorizationDecision"),
            "taskGraphMapping": mapping,
            "executionChainHash": chain.get("receiptHash"),
            "reservationId": reservation_id,
            "contractVersion": VERSION,
            "legacyBridgeUsed": False,
            "legacyBusinessSemanticsUsed": False,
        }
    except (ValueError, KeyError, TypeError, GraphReservationConflict) as exc:
        if reservation_id:
            release_reservation(reservation_id, reason=str(exc))
        return {
            "ok": False,
            "status": "rejected_by_v269_production_admission",
            "createdTaskCount": 0,
            "reason": str(exc),
            "contractVersion": VERSION,
            "legacyFallbackUsed": False,
        }


__all__ = [
    "VERSION",
    "ensure_graph_reservation_tables",
    "reserve_plan_authority",
    "release_reservation",
    "admit_graph_decision_to_task_pool",
]
