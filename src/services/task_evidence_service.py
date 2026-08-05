"""Task evidence submission service.

V12.11.1 boundary: this service only records the operator's evidence. It must
not move task status itself; status transitions are owned by
`task_lifecycle_state_machine_service.transition_lifecycle_task`.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.services.account_service import get_user, user_display
from src.services.module_task_service import create_log, find_task, now_iso, update_task
from src.services.task_evidence_repository_service import persist_evidence_submission
from src.services.task_lifecycle_state_machine_service import get_lifecycle_task_projection
from src.services.uid import make_id

EVIDENCE_VERSION = "12.11.1"


def _task_or_none(task_id: str, viewer_id: str | None = None) -> Dict[str, Any] | None:
    task = find_task(task_id)
    if not task:
        return None
    return task


def _safe_form_fields(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, list):
        return {str(index + 1): item for index, item in enumerate(value)}
    return {}


def template_for_task(task: Dict[str, Any]) -> Dict[str, Any]:
    report = task.get("taskDetailReport") or {}
    gate = task.get("actionAuthorization") or task.get("v1282ActionGate") or {}
    fields = gate.get("operatorFactFields") or report.get("dataNeeded") or ["处理动作", "处理说明", "影响范围", "执行时间"]
    uploads = ["上传处理截图", "上传数据凭证"]
    text = " ".join(str(item or "") for item in [task.get("title"), task.get("taskType"), task.get("riskDomain"), *(task.get("judgmentTags") or [])])
    if any(word in text for word in ["主图", "标题", "素材", "点击", "转化"]):
        uploads = ["上传修改前截图", "上传修改后截图", "上传测试范围截图"]
    elif any(word in text for word in ["库存", "补货", "可售"]):
        uploads = ["上传库存截图", "上传补货或仓库确认截图"]
    elif any(word in text for word in ["售后", "退款", "客服", "差评"]):
        uploads = ["上传退款原因截图", "上传客服核实截图"]
    return {
        "version": EVIDENCE_VERSION,
        "fields": fields,
        "uploads": uploads,
        "rule": "提交页只收执行材料；提交后的复核/自动复盘由统一生命周期状态机处理。",
    }


def get_task_evidence(task_id: str, viewer_id: str | None = None) -> Dict[str, Any] | None:
    task = _task_or_none(task_id, viewer_id)
    if not task:
        return None
    return {
        "version": EVIDENCE_VERSION,
        "taskId": task_id,
        "template": template_for_task(task),
        "latestEvidenceRecord": task.get("latestEvidenceRecord"),
        "evidenceRecords": task.get("evidenceRecords") or [],
        "task": get_lifecycle_task_projection(task_id, viewer_id) or deepcopy(task),
        "rule": "运营提交材料后，系统等待后续报表/接口更新自动复盘。",
    }


def submit_task_evidence(task_id: str, body: Dict[str, Any] | None = None, submitter_id: str | None = None) -> Dict[str, Any] | None:
    body = body or {}
    task = _task_or_none(task_id, submitter_id)
    if not task:
        return None
    submitter = get_user(submitter_id) if submitter_id else None
    summary = body.get("summary") or body.get("note") or "运营已提交Agent SOP执行材料，等待系统自动复盘。"
    action = body.get("action") or body.get("handlingAction") or task.get("actionType") or task.get("taskType") or "处理完成"
    result = body.get("result") or body.get("handlingResult") or "处理材料已提交，后续由系统自动复盘。"
    form_fields = _safe_form_fields(body.get("formFields") or body.get("fields"))
    record = {
        "id": make_id("EVIDENCE"),
        "taskId": task_id,
        "submittedById": submitter_id or task.get("assigneeId"),
        "submittedByName": submitter.get("name") if submitter else user_display(submitter_id or task.get("assigneeId"), "运营账号"),
        "submittedAt": now_iso(),
        "action": action,
        "summary": summary,
        "result": result,
        "needFollowUp": bool(body.get("needFollowUp")),
        "enterRecap": True if body.get("enterRecap") is None else bool(body.get("enterRecap")),
        "operatorManualRecapRequired": False,
        "formFields": form_fields,
        "evidenceLinks": body.get("evidenceLinks") or body.get("attachments") or [],
        "status": "已提交材料",
        "version": EVIDENCE_VERSION,
    }
    records: List[Dict[str, Any]] = [record, *(task.get("evidenceRecords") or [])]
    updated = update_task(
        task_id,
        {
            "evidenceFormVersion": EVIDENCE_VERSION,
            "evidenceTemplate": template_for_task(task),
            "evidenceRecords": records[:10],
            "latestEvidenceRecord": record,
            "submitSummary": summary,
            "submissionNote": summary,
            "handlingAction": action,
            "handlingResult": result,
            "needFollowUp": bool(body.get("needFollowUp")),
            "enterRecap": record["enterRecap"],
            "operatorManualRecapRequired": False,
        },
        log_type="处理证据提交",
        action=action,
        result=summary,
    )
    latest = find_task(task_id) or updated or task
    audit = persist_evidence_submission(latest, record)
    latest["evidenceAudit"] = audit
    latest["latestEvidenceRecord"] = record
    latest["evidenceRecords"] = records[:10]
    create_log({"type": "处理证据提交", "task": latest, "status": "已提交材料", "action": action, "result": summary, "operator": record["submittedByName"]})
    return deepcopy(latest)


def review_task_evidence(task_id: str, body: Dict[str, Any] | None = None, reviewer_id: str | None = None) -> Dict[str, Any] | None:
    body = body or {}
    task = _task_or_none(task_id, reviewer_id)
    if not task:
        return None
    reviewer = get_user(reviewer_id) if reviewer_id else None
    decision = body.get("decision") or "approve"
    note = body.get("note") or body.get("comment") or ("复核通过。" if decision in {"approve", "approved", "pass", "通过"} else "复核退回。")
    record = {
        "id": make_id("REVIEW"),
        "taskId": task_id,
        "reviewedById": reviewer_id,
        "reviewedByName": reviewer.get("name") if reviewer else user_display(reviewer_id, "复核账号"),
        "reviewedAt": now_iso(),
        "decision": decision,
        "note": note,
        "version": EVIDENCE_VERSION,
    }
    reviews = [record, *(task.get("evidenceReviews") or [])]
    updated = update_task(task_id, {"evidenceReviews": reviews[:10], "latestEvidenceReview": record, "reviewNote": note}, log_type="证据复核", action="复核材料", result=note)
    return deepcopy(updated or task)
