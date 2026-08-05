"""V22.5.8 pipeline-live projection with Agent1 failure truth and product de-duplication."""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import pipeline_live_read_model_v208_service as legacy

THREE_AGENT_PIPELINE_VERSION = "22.5.5"
PIPELINE_LIVE_READ_MODEL_VERSION = "22.5.8"

_NODE_CONTRACT = [
    ("data_platform", "数据中台"),
    ("fact_engine", "事实引擎"),
    ("signal_engine", "信号引擎"),
    ("rag_context", "RAG 上下文"),
    ("agent1", "Agent1 研判"),
    ("action_matrix", "动作矩阵"),
    ("agent2_draft", "Agent2 动作草案"),
    ("agent3_sop", "Agent3 SOP 生成"),
    ("task_mapping", "任务映射"),
    ("task_pool", "任务池"),
    ("task_loop", "任务闭环"),
]
_NODE_ORDER = [label for _code, label in _NODE_CONTRACT]
_NODE_CODE_BY_LABEL = {label: code for code, label in _NODE_CONTRACT}
_STAGE_NODE = {
    **legacy.STAGE_NODE,
    "observed_soft_gate": "信号引擎",
    "agent1_pending": "Agent1 研判",
    "agent1_running": "Agent1 研判",
    "agent1_failed": "Agent1 研判",
    "agent1_completed": "Agent1 研判",
    "agent1_output_invalid": "Agent1 研判",
    "agent1_decision_unresolved": "Agent1 研判",
    "action_pack_ready": "动作矩阵",
    "action_pack_invalid": "动作矩阵",
    "agent2_draft_input_invalid": "Agent2 动作草案",
    "agent2_running": "Agent2 动作草案",
    "agent2_failed": "Agent2 动作草案",
    "agent2_output_invalid": "Agent2 动作草案",
    "agent2_dead_letter": "Agent2 动作草案",
    "agent2_completed": "Agent2 动作草案",
    "agent2_draft_ready": "Agent2 动作草案",
    "agent2_draft_output_invalid": "Agent2 动作草案",
    "agent2_draft_failed": "Agent2 动作草案",
    "agent2_draft_missing_data": "Agent2 动作草案",
    "agent3_sop_running": "Agent3 SOP 生成",
    "agent3_sop_ready": "Agent3 SOP 生成",
    "agent3_sop_output_invalid": "Agent3 SOP 生成",
    "agent3_sop_failed": "Agent3 SOP 生成",
    "task_mapped": "任务映射",
    "task_mapping_failed": "任务映射",
    "sop_mapped": "任务映射",
    "task_admitted": "任务池",
}
_STAGE_LABELS = {
    **legacy.STAGE_LABELS,
    "agent1_pending": "Agent1排队",
    "agent1_running": "Agent1运行",
    "agent1_completed": "Agent1完成",
    "agent1_failed": "Agent1运行失败",
    "agent1_output_invalid": "Agent1输出合同异常",
    "agent1_decision_unresolved": "Agent1判断未收敛",
    "agent2_draft_input_invalid": "Agent2输入投影失败",
    "agent2_running": "Agent2草案运行",
    "agent2_draft_ready": "Agent2草案完成",
    "agent2_draft_output_invalid": "Agent2草案输出异常",
    "agent2_draft_failed": "Agent2草案失败",
    "agent2_draft_missing_data": "Agent2证据待补充",
    "agent3_sop_running": "Agent3 SOP运行",
    "agent3_sop_ready": "Agent3 SOP完成",
    "agent3_sop_output_invalid": "Agent3 SOP输出异常",
    "agent3_sop_failed": "Agent3 SOP失败",
    "task_mapped": "任务映射完成",
    "task_mapping_failed": "任务映射失败",
    "sop_mapped": "旧任务映射完成",
}
_FAILED_STAGES = {
    "agent1_failed",
    "agent1_output_invalid",
    "agent1_decision_unresolved",
    "action_pack_invalid",
    "agent2_draft_input_invalid",
    "agent2_failed",
    "agent2_output_invalid",
    "agent2_dead_letter",
    "agent2_draft_output_invalid",
    "agent2_draft_failed",
    "agent3_sop_output_invalid",
    "agent3_sop_failed",
    "task_mapping_failed",
}


def _activate_contract() -> None:
    legacy.NODE_ORDER[:] = _NODE_ORDER
    legacy.STAGE_NODE.clear()
    legacy.STAGE_NODE.update(_STAGE_NODE)
    legacy.STAGE_LABELS.clear()
    legacy.STAGE_LABELS.update(_STAGE_LABELS)
    legacy.FAILED_STAGES.update(_FAILED_STAGES)
    legacy.COMPLETED_STAGES.update({"agent2_draft_ready", "agent3_sop_ready", "task_mapped"})
    legacy.RUNNING_STAGES.update({"agent3_sop_running"})
    legacy.ACTIVE_PRODUCT_STAGES.update(
        {
            "agent2_draft_ready",
            "agent2_draft_missing_data",
            "agent3_sop_running",
            "agent3_sop_ready",
            "task_mapped",
        }
    )


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _stage(stages: List[Dict[str, Any]], code: str) -> Dict[str, Any]:
    for item in stages:
        label = str(item.get("label") or item.get("node") or "")
        if item.get("nodeCode") == code or _NODE_CODE_BY_LABEL.get(label) == code:
            return item
    return {}


def _failure_type(stage: str, error_code: str) -> str:
    text = f"{stage} {error_code}".lower()
    if "provider" in text or "timeout" in text or "http" in text:
        return "模型接口失败"
    if "input_contract" in text or "projection" in text or "source_lineage" in text:
        return "输入证据合同异常"
    if "output_invalid" in text or "decision_type" in text or "normalization" in text:
        return "输出格式不兼容"
    if "no_matching" in text or "identity" in text:
        return "商品身份缺失"
    if "execution_lock" in text:
        return "执行动作未锁定"
    return _STAGE_LABELS.get(stage, stage or "商品处理异常")


def _current_rows(data_version: str | None) -> List[Dict[str, Any]]:
    with connect() as conn:
        if data_version:
            rows = conn.execute(
                """
                SELECT * FROM pipeline_items
                WHERE data_version=?
                  AND product_id IS NOT NULL
                  AND TRIM(product_id) != ''
                ORDER BY updated_at DESC
                """,
                (data_version,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM pipeline_items
                WHERE product_id IS NOT NULL
                  AND TRIM(product_id) != ''
                ORDER BY updated_at DESC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def _identity(row: Dict[str, Any]) -> str:
    return f"{str(row.get('store_id') or '')}::{str(row.get('product_id') or row.get('item_id') or '')}"


def _deduplicated_attention(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        stage = str(row.get("current_stage") or "")
        status = str(row.get("status") or "").lower()
        if stage == "observed_soft_gate":
            continue
        bucket = (
            "failed"
            if status in {"failed", "error"} or stage in _FAILED_STAGES
            else "running"
            if status in {"running", "processing"}
            else "queued"
            if status in {"ready", "queued", "retry", "pending"}
            else ""
        )
        if not bucket:
            continue
        identity = _identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        error_code = str(row.get("last_error_code") or row.get("error_reason") or "")
        result.append(
            {
                "itemId": row.get("item_id"),
                "productId": row.get("product_id"),
                "storeId": row.get("store_id"),
                "identityKey": identity,
                "title": row.get("product_id") or row.get("signal_id") or "商品包",
                "node": _STAGE_NODE.get(stage, "数据中台"),
                "currentStage": stage,
                "stageLabel": _failure_type(stage, error_code)
                if bucket == "failed"
                else _STAGE_LABELS.get(stage, stage),
                "failureType": _failure_type(stage, error_code) if bucket == "failed" else None,
                "lastErrorCode": row.get("last_error_code"),
                "errorReason": row.get("error_reason"),
                "retryCount": _int(row.get("retry_count")),
                "status": row.get("status"),
                "bucket": bucket,
                "actionFamily": row.get("action_family")
                or ("等待修复" if bucket == "failed" else "未锁定动作"),
                "updatedAt": row.get("updated_at"),
            }
        )
        if len(result) >= max(1, int(limit)):
            break
    return result


def read_pipeline_live_model(
    data_version: str | None = None,
    *,
    limit: int = 80,
) -> Dict[str, Any]:
    _activate_contract()
    result = legacy.read_pipeline_live_model(data_version=data_version, limit=limit)
    resolved = result.get("dataVersion") or data_version
    rows = _current_rows(resolved)
    unique_rows: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        unique_rows.setdefault(_identity(row), row)
    current = list(unique_rows.values())

    raw_stages = result.get("stages") if isinstance(result.get("stages"), list) else []
    stages: List[Dict[str, Any]] = []
    for raw in raw_stages:
        item = dict(raw) if isinstance(raw, dict) else {}
        label = str(item.get("label") or item.get("node") or "")
        item["nodeCode"] = _NODE_CODE_BY_LABEL.get(label, label)
        item["label"] = label
        item["current"] = {
            "queued": _int(item.get("queued")),
            "running": _int(item.get("running")),
            "completed": _int(item.get("completed")),
            "failed": _int(item.get("failed")),
            "observed": _int(item.get("observed")),
            "admitted": _int(item.get("admitted")),
        }
        item["history"] = {"completed": _int(item.get("historyCompleted"))}
        stages.append(item)

    agent1_failed = sum(1 for row in current if str(row.get("current_stage") or "") == "agent1_failed")
    agent1_invalid = sum(1 for row in current if str(row.get("current_stage") or "") == "agent1_output_invalid")
    agent1_unresolved = sum(1 for row in current if str(row.get("current_stage") or "") == "agent1_decision_unresolved")
    observed = sum(1 for row in current if str(row.get("current_stage") or "") == "observed_soft_gate")
    action_candidates = sum(1 for row in current if str(row.get("action_family") or "").strip())
    product_failed = sum(
        1
        for row in current
        if str(row.get("status") or "").lower() in {"failed", "error"}
        or str(row.get("current_stage") or "") in _FAILED_STAGES
    )
    batch = result.get("batchState") if isinstance(result.get("batchState"), dict) else {}
    batch_failed = 1 if str(batch.get("status") or "") == "failed" else 0

    agent1 = _stage(stages, "agent1")
    if agent1:
        agent1["failed"] = agent1_failed + agent1_invalid + agent1_unresolved
        agent1.setdefault("current", {})["failed"] = agent1["failed"]
        agent1["status"] = "attention" if agent1["failed"] else agent1.get("status")
        agent1["currentCount"] = max(
            _int(agent1.get("queued")),
            _int(agent1.get("running")),
            _int(agent1.get("completed")),
            _int(agent1.get("failed")),
        )

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    summary.update(
        productTotal=len(current),
        observed=observed,
        actionCandidates=action_candidates,
        productFailed=product_failed,
        batchFailed=batch_failed,
        failed=product_failed + batch_failed,
        agent1Failed=agent1_failed,
        agent1OutputInvalid=agent1_invalid,
        agent1DecisionUnresolved=agent1_unresolved,
        agent1Observed=observed,
        agent1Current=(
            _int(agent1.get("queued"))
            + _int(agent1.get("running"))
            + _int(agent1.get("completed"))
            + _int(agent1.get("failed"))
        )
        if agent1
        else 0,
    )
    result.update(
        version=PIPELINE_LIVE_READ_MODEL_VERSION,
        threeAgentPipelineVersion=THREE_AGENT_PIPELINE_VERSION,
        summary=summary,
        stages=stages,
        items=_deduplicated_attention(rows, limit),
        pipelineNodes=[{"nodeCode": code, "label": label} for code, label in _NODE_CONTRACT],
        attentionDedupKey="dataVersion+storeId+productId",
        countContract={
            "productTotal": "unique current product identities",
            "observed": "current legal observation products",
            "productFailed": "unique current failed products",
            "agent1Failed": "current agent1_failed products",
            "agent1OutputInvalid": "current agent1_output_invalid products",
            "attentionItems": "latest current row per storeId+productId",
        },
        rule=(
            "Provider, input evidence, output normalization and execution-lock failures "
            "remain separate; attention items are unique current products."
        ),
    )
    return result


__all__ = ["PIPELINE_LIVE_READ_MODEL_VERSION", "read_pipeline_live_model"]
