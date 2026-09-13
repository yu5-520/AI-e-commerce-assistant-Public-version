"""V26.9.C reviewed Experience promotion and feedback lifecycle.

This module does not create a second Experience Store or a mutable Knowledge Head.
`v269b_experience_items.lifecycle_status` remains the lifecycle authority and the Head
remains the content hash of the currently enabled set. C only authorizes explicit
review / enable / withdraw / supersede transitions and appends immutable audit events.
System Review and Evaluation may create candidates, but never call these mutation APIs.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from src.repositories import sqlite_repository as repo
from src.services import v269_experience_store_service as store

VERSION = "26.9.C.1"
ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "rag/manifest/v269c_manifest.json"
POLICY_PATH = ROOT / "config/v269c_promotion_policy.json"
MIGRATION_PATH = ROOT / "rag/migrations/002_v269c_promotion_feedback.sql"


class PromotionGateError(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PromotionGateError("v269c_promotion_" + reason)


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), "json_object_required")
    return value


def manifest() -> dict[str, Any]:
    value = _json_object(MANIFEST_PATH)
    _require(value.get("version") == VERSION, "manifest_version")
    _require(value.get("automaticPromotion") is False, "auto_promotion_forbidden")
    _require(value.get("automaticEnable") is False, "auto_enable_forbidden")
    _require(value.get("headAuthority") == "content_addressed_enabled_set", "head_authority")
    return value


def policy() -> dict[str, Any]:
    value = _json_object(POLICY_PATH)
    _require(value.get("version") == VERSION, "policy_version")
    _require(value.get("autoPromotion") is False, "policy_auto_promotion_forbidden")
    _require(value.get("reviewRequired") is True, "review_required")
    _require(value.get("explicitEnableRequired") is True, "explicit_enable_required")
    _require(isinstance(value.get("domainRules"), dict), "domain_rules")
    return value


def policy_hash() -> str:
    return store.file_digest(POLICY_PATH)


def runtime_enabled() -> bool:
    cfg = manifest()
    if cfg.get("rolloutStatus") == "active":
        return True
    env = str(cfg.get("candidateRuntimeEnv") or "V269C_CANDIDATE_RUNTIME")
    return str(os.getenv(env, "")).strip().lower() in {"1", "true", "yes", "on"}


def ensure_promotion_tables() -> dict[str, Any]:
    store.ensure_experience_store()
    migration_hash = store.file_digest(MIGRATION_PATH)
    with repo.connect() as conn:
        conn.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        conn.execute(
            """INSERT OR IGNORE INTO v269b_experience_migrations(
               migration_id,schema_version,migration_hash,applied_at
               ) VALUES(?,?,?,?)""",
            ("002_v269c_promotion_feedback", VERSION, migration_hash, store._now()),
        )
        conn.commit()
    material = {
        "schema": "experience.promotion.install.receipt.v269c.v1",
        "version": VERSION,
        "migrationHash": migration_hash,
        "policyHash": policy_hash(),
        "manifestHash": store.file_digest(MANIFEST_PATH),
        "headAuthority": "content_addressed_enabled_set",
    }
    return {**material, "receiptHash": store.digest(material)}


def _decode(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _item(conn: Any, experience_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT i.*,s.source_task_id,s.source_type,s.source_version,s.evidence_refs,
                  s.business_scope,s.decision_graph_hash,s.plan_graph_hash,s.operation_graph_hash
           FROM v269b_experience_items i
           JOIN v269b_experience_sources s ON s.source_id=i.source_id
           WHERE i.experience_id=?""",
        (experience_id,),
    ).fetchone()
    _require(row is not None, "experience_not_found")
    return dict(row)


def _sample_count(conn: Any, item: dict[str, Any]) -> int:
    domain = item["domain"]
    table = {
        "decision_patterns": ("v269b_decision_patterns", "sample_count"),
        "strategy_outcomes": ("v269b_strategy_outcomes", "sample_count"),
        "operation_patterns": ("v269b_operation_patterns", "sample_count"),
    }.get(domain)
    if table:
        row = conn.execute(
            f"SELECT {table[1]} AS n FROM {table[0]} WHERE experience_id=?",
            (item["experience_id"],),
        ).fetchone()
        return int((row or {"n": 0})["n"] or 0)
    payload = _decode(item.get("payload"), {})
    return int(payload.get("sampleCount") or 0) if isinstance(payload, dict) else 0


def _linked_evaluations(conn: Any, experience_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.metric_id,r.metric_version,r.value,r.sample_count,r.missing_reason,
                  i.experience_id,i.lifecycle_status
           FROM v269b_evaluation_results r
           JOIN v269b_experience_items i ON i.experience_id=r.experience_id
           WHERE r.subject_experience_id=?
           ORDER BY r.metric_id,r.evaluation_id""",
        (experience_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _threshold_ok(value: Any, rule: dict[str, Any]) -> bool:
    if type(value) not in (int, float):
        return False
    comparator = rule.get("comparator")
    target = rule.get("value")
    if type(target) not in (int, float):
        return False
    if comparator == "GTE":
        return float(value) >= float(target)
    if comparator == "LTE":
        return float(value) <= float(target)
    return False


def _expiry_failed(applicability: dict[str, Any], field: str) -> bool:
    raw = applicability.get(field)
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= parsed.astimezone(timezone.utc)
    except Exception:
        return True


def _head_in_conn(conn: Any, domain: str) -> str:
    rows = conn.execute(
        """SELECT experience_id,domain,payload,updated_at
           FROM v269b_experience_items
           WHERE lifecycle_status='enabled' AND domain=?
           ORDER BY domain,experience_id""",
        (domain,),
    ).fetchall()
    material = [
        {
            "experienceId": row["experience_id"],
            "domain": row["domain"],
            "payloadHash": store.digest(_decode(row["payload"], {})),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
    return store.digest({"version": store.VERSION, "domains": [domain], "enabled": material})


def _gate_in_conn(conn: Any, experience_id: str) -> dict[str, Any]:
    item = _item(conn, experience_id)
    cfg = policy()
    from src.services.v2610_initialization_service import is_registered_method
    initialization_method = is_registered_method(conn, item)
    domain = item["domain"]
    rule = (cfg.get("domainRules") or {}).get(domain)
    failures: list[str] = []
    applicability = _decode(item.get("applicability"), {})
    payload = _decode(item.get("payload"), {})
    evidence_refs = _decode(item.get("evidence_refs"), [])
    evaluations = _linked_evaluations(conn, experience_id)
    sample_count = _sample_count(conn, item)

    if not initialization_method and item.get("source_type") != cfg.get("sourceType"):
        failures.append("SOURCE_TYPE_NOT_RUNTIME")
    if item.get("lifecycle_status") not in {"candidate", "approved"}:
        failures.append("LIFECYCLE_NOT_PROMOTABLE")
    if not isinstance(rule, dict):
        failures.append("DOMAIN_NOT_PROMOTABLE")
        rule = {}
    review_status = applicability.get("reviewStatus") if isinstance(applicability, dict) else None
    if not initialization_method and review_status not in set(cfg.get("eligibleReviewStatuses") or []):
        failures.append("SOURCE_REVIEW_NOT_SETTLED")
    if len(set(str(ref) for ref in evidence_refs if str(ref))) < int(cfg.get("minimumEvidenceRefs") or 0):
        failures.append("EVIDENCE_INCOMPLETE")
    if not initialization_method and sample_count < int(rule.get("minimumSampleCount") or 0):
        failures.append("SAMPLE_COUNT_LOW")
    for flag in cfg.get("deniedApplicabilityFlags") or []:
        if isinstance(applicability, dict) and applicability.get(flag) is True:
            failures.append("RISK_FLAG:" + str(flag))
    if _expiry_failed(applicability if isinstance(applicability, dict) else {}, str(cfg.get("expiryField") or "expiresAt")):
        failures.append("EXPIRED_OR_INVALID_EXPIRY")

    required_any = list(rule.get("requiredEvaluationAnyOf") or [])
    minimum_eval_samples = int(rule.get("minimumEvaluationSampleCount") or 0)
    usable = [
        row for row in evaluations
        if row.get("metric_id") in required_any
        and row.get("missing_reason") in (None, "")
        and int(row.get("sample_count") or 0) >= minimum_eval_samples
    ]
    if required_any and not usable:
        failures.append("EVALUATION_INSUFFICIENT")
    thresholds = rule.get("metricThresholds") or {}
    for metric_id, threshold in thresholds.items():
        candidates = [row for row in evaluations if row.get("metric_id") == metric_id and row.get("missing_reason") in (None, "")]
        if not candidates or not any(_threshold_ok(row.get("value"), threshold) for row in candidates):
            failures.append("EVALUATION_THRESHOLD:" + metric_id)

    enabled = conn.execute(
        """SELECT experience_id,payload,supersedes_experience_id
           FROM v269b_experience_items
           WHERE domain=? AND lifecycle_status='enabled' AND applicability=? AND experience_id<>?
           ORDER BY experience_id""",
        (domain, item["applicability"], experience_id),
    ).fetchall()
    duplicate_ids: list[str] = []
    conflict_ids: list[str] = []
    candidate_payload_hash = store.digest(payload)
    for row in enabled:
        if store.digest(_decode(row["payload"], {})) == candidate_payload_hash:
            duplicate_ids.append(str(row["experience_id"]))
        else:
            conflict_ids.append(str(row["experience_id"]))
    if duplicate_ids:
        failures.append("ENABLED_DUPLICATE_EXISTS")
    supersedes = str(item.get("supersedes_experience_id") or "")
    unresolved = [value for value in conflict_ids if value != supersedes]
    if unresolved:
        failures.append("ENABLED_CONFLICT_REQUIRES_SUPERSEDE")

    material = {
        "schema": "experience.promotion.gate.v269c.v1",
        "version": VERSION,
        "experienceId": experience_id,
        "domain": domain,
        "lifecycleStatus": item["lifecycle_status"],
        "sourceTaskId": item["source_task_id"],
        "policyHash": policy_hash(),
        "sampleCount": sample_count,
        "knowledgeKind": "initialization_method" if initialization_method else "runtime_experience",
        "historicalOutcomeProof": not initialization_method,
        "evidenceRefCount": len(set(str(ref) for ref in evidence_refs if str(ref))),
        "linkedEvaluationCount": len(evaluations),
        "usableEvaluationMetrics": sorted({row["metric_id"] for row in usable}),
        "duplicateEnabledExperienceIds": duplicate_ids,
        "conflictingEnabledExperienceIds": conflict_ids,
        "supersedesExperienceId": item.get("supersedes_experience_id"),
        "failures": sorted(set(failures)),
        "approvedForPromotion": not failures,
        "autoPromotionPerformed": False,
    }
    return {**material, "gateHash": store.digest(material)}


def evaluate_promotion_gate(experience_id: str) -> dict[str, Any]:
    ensure_promotion_tables()
    with repo.connect() as conn:
        return _gate_in_conn(conn, str(experience_id))


def _event_id(material: dict[str, Any], prefix: str) -> str:
    return prefix + store.digest(material)[-20:].upper()


def _insert_lifecycle_event(
    conn: Any, *, experience_id: str, domain: str, from_status: str, to_status: str,
    actor_id: str, reason: str, review_id: str | None,
) -> str:
    material = {
        "experienceId": experience_id,
        "domain": domain,
        "fromStatus": from_status,
        "toStatus": to_status,
        "actorId": actor_id,
        "reason": reason,
        "reviewId": review_id,
        "policyHash": policy_hash(),
    }
    event_id = _event_id(material, "V269C-EVT-")
    conn.execute(
        """INSERT OR IGNORE INTO v269c_lifecycle_events(
           event_id,experience_id,domain,from_status,to_status,actor_id,reason,review_id,policy_hash,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (event_id, experience_id, domain, from_status, to_status, actor_id, reason, review_id, policy_hash(), store._now()),
    )
    return event_id


def _insert_head_event(conn: Any, *, domain: str, experience_id: str, lifecycle_event_id: str, previous_head: str, next_head: str) -> str | None:
    if previous_head == next_head:
        return None
    material = {
        "domain": domain,
        "experienceId": experience_id,
        "lifecycleEventId": lifecycle_event_id,
        "previousHead": previous_head,
        "nextHead": next_head,
        "policyHash": policy_hash(),
    }
    head_event_id = _event_id(material, "V269C-HEAD-")
    conn.execute(
        """INSERT OR IGNORE INTO v269c_domain_head_events(
           head_event_id,domain,experience_id,lifecycle_event_id,previous_head,next_head,policy_hash,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (head_event_id, domain, experience_id, lifecycle_event_id, previous_head, next_head, policy_hash(), store._now()),
    )
    return head_event_id


def _require_runtime() -> None:
    _require(runtime_enabled(), "runtime_not_active")


def review_candidate(
    experience_id: str, *, reviewer_id: str, decision: str, rationale: str,
    supersedes_experience_id: str | None = None,
) -> dict[str, Any]:
    """Human review moves candidate -> approved or candidate -> disabled; never enabled."""
    _require_runtime()
    _require(decision in {"approve", "reject"}, "review_decision")
    _require(isinstance(reviewer_id, str) and reviewer_id.strip(), "reviewer_required")
    _require(isinstance(rationale, str) and rationale.strip(), "rationale_required")
    ensure_promotion_tables()
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = _item(conn, experience_id)
        _require(item["lifecycle_status"] == "candidate", "review_requires_candidate")
        if supersedes_experience_id:
            prior = _item(conn, supersedes_experience_id)
            _require(prior["domain"] == item["domain"], "supersede_domain_mismatch")
            _require(prior["lifecycle_status"] == "enabled", "supersede_requires_enabled")
            conn.execute(
                "UPDATE v269b_experience_items SET supersedes_experience_id=?,updated_at=? WHERE experience_id=?",
                (supersedes_experience_id, store._now(), experience_id),
            )
            item = _item(conn, experience_id)
        gate = _gate_in_conn(conn, experience_id)
        if decision == "approve":
            _require(gate["approvedForPromotion"] is True, "gate_rejected:" + ",".join(gate["failures"]))
        review_material = {
            "experienceId": experience_id,
            "reviewerId": reviewer_id,
            "decision": decision,
            "rationale": rationale,
            "gateHash": gate["gateHash"],
            "supersedesExperienceId": item.get("supersedes_experience_id"),
        }
        review_id = _event_id(review_material, "V269C-REV-")
        existing = conn.execute("SELECT review_id FROM v269c_promotion_reviews WHERE review_id=?", (review_id,)).fetchone()
        if existing:
            conn.commit()
            return {"reviewId": review_id, "idempotentHit": True, "gate": gate, "lifecycleStatus": item["lifecycle_status"]}
        conn.execute(
            """INSERT INTO v269c_promotion_reviews(
               review_id,experience_id,reviewer_id,decision,rationale,gate_hash,gate_payload,supersedes_experience_id,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (review_id, experience_id, reviewer_id, decision, rationale, gate["gateHash"], json.dumps(gate, ensure_ascii=False, sort_keys=True), item.get("supersedes_experience_id"), store._now()),
        )
        next_status = "approved" if decision == "approve" else "disabled"
        conn.execute("UPDATE v269b_experience_items SET lifecycle_status=?,updated_at=? WHERE experience_id=?", (next_status, store._now(), experience_id))
        event_id = _insert_lifecycle_event(
            conn, experience_id=experience_id, domain=item["domain"], from_status="candidate", to_status=next_status,
            actor_id=reviewer_id, reason="REVIEW_" + decision.upper(), review_id=review_id,
        )
        conn.commit()
    return {
        "schema": "experience.promotion.review_receipt.v269c.v1",
        "version": VERSION,
        "experienceId": experience_id,
        "reviewId": review_id,
        "lifecycleEventId": event_id,
        "lifecycleStatus": next_status,
        "gate": gate,
        "enabled": False,
        "knowledgeHeadMutated": False,
        "idempotentHit": False,
    }


def enable_experience(experience_id: str, *, operator_id: str, explicit_operator_intent: bool = False) -> dict[str, Any]:
    """Explicit operator action only: approved -> enabled, optionally superseding one enabled row."""
    _require_runtime()
    _require(explicit_operator_intent, "enable_requires_explicit_operator_intent")
    _require(isinstance(operator_id, str) and operator_id.strip(), "operator_required")
    ensure_promotion_tables()
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = _item(conn, experience_id)
        if item["lifecycle_status"] == "enabled":
            head = _head_in_conn(conn, item["domain"])
            conn.commit()
            return {"experienceId": experience_id, "lifecycleStatus": "enabled", "knowledgeHead": head, "idempotentHit": True}
        _require(item["lifecycle_status"] == "approved", "enable_requires_approved")
        gate = _gate_in_conn(conn, experience_id)
        _require(gate["approvedForPromotion"] is True, "enable_gate_rejected:" + ",".join(gate["failures"]))
        domain = item["domain"]
        before = _head_in_conn(conn, domain)
        supersedes = str(item.get("supersedes_experience_id") or "")
        supersede_event = None
        if supersedes:
            prior = _item(conn, supersedes)
            _require(prior["domain"] == domain and prior["lifecycle_status"] == "enabled", "supersede_target_not_enabled")
            conn.execute("UPDATE v269b_experience_items SET lifecycle_status='superseded',updated_at=? WHERE experience_id=?", (store._now(), supersedes))
            supersede_event = _insert_lifecycle_event(
                conn, experience_id=supersedes, domain=domain, from_status="enabled", to_status="superseded",
                actor_id=operator_id, reason="SUPERSEDED_BY:" + experience_id, review_id=None,
            )
        conn.execute("UPDATE v269b_experience_items SET lifecycle_status='enabled',updated_at=? WHERE experience_id=?", (store._now(), experience_id))
        event_id = _insert_lifecycle_event(
            conn, experience_id=experience_id, domain=domain, from_status="approved", to_status="enabled",
            actor_id=operator_id, reason="EXPLICIT_ENABLE", review_id=None,
        )
        after = _head_in_conn(conn, domain)
        head_event_id = _insert_head_event(
            conn, domain=domain, experience_id=experience_id, lifecycle_event_id=event_id,
            previous_head=before, next_head=after,
        )
        conn.commit()
    return {
        "schema": "experience.promotion.enable_receipt.v269c.v1",
        "version": VERSION,
        "experienceId": experience_id,
        "lifecycleStatus": "enabled",
        "lifecycleEventId": event_id,
        "supersedeLifecycleEventId": supersede_event,
        "headEventId": head_event_id,
        "previousKnowledgeHead": before,
        "knowledgeHead": after,
        "knowledgeHeadMutated": before != after,
        "promotionPerformed": True,
        "idempotentHit": False,
    }


def disable_experience(
    experience_id: str, *, operator_id: str, reason: str, explicit_operator_intent: bool = False,
) -> dict[str, Any]:
    """Explicit withdrawal. Rows remain queryable through inspection/history."""
    _require_runtime()
    _require(explicit_operator_intent, "disable_requires_explicit_operator_intent")
    _require(isinstance(operator_id, str) and operator_id.strip(), "operator_required")
    _require(isinstance(reason, str) and reason.strip(), "disable_reason_required")
    ensure_promotion_tables()
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = _item(conn, experience_id)
        if item["lifecycle_status"] == "disabled":
            head = _head_in_conn(conn, item["domain"])
            conn.commit()
            return {"experienceId": experience_id, "lifecycleStatus": "disabled", "knowledgeHead": head, "idempotentHit": True}
        _require(item["lifecycle_status"] in {"enabled", "approved"}, "disable_status_invalid")
        domain = item["domain"]
        before = _head_in_conn(conn, domain)
        previous = item["lifecycle_status"]
        conn.execute("UPDATE v269b_experience_items SET lifecycle_status='disabled',updated_at=? WHERE experience_id=?", (store._now(), experience_id))
        event_id = _insert_lifecycle_event(
            conn, experience_id=experience_id, domain=domain, from_status=previous, to_status="disabled",
            actor_id=operator_id, reason=reason, review_id=None,
        )
        after = _head_in_conn(conn, domain)
        head_event_id = _insert_head_event(
            conn, domain=domain, experience_id=experience_id, lifecycle_event_id=event_id,
            previous_head=before, next_head=after,
        )
        conn.commit()
    return {
        "schema": "experience.promotion.withdraw_receipt.v269c.v1",
        "version": VERSION,
        "experienceId": experience_id,
        "lifecycleStatus": "disabled",
        "lifecycleEventId": event_id,
        "headEventId": head_event_id,
        "previousKnowledgeHead": before,
        "knowledgeHead": after,
        "knowledgeHeadMutated": before != after,
        "historyPreserved": True,
        "idempotentHit": False,
    }


def promotion_history(experience_id: str) -> dict[str, Any]:
    ensure_promotion_tables()
    with repo.connect() as conn:
        item = _item(conn, experience_id)
        reviews = [dict(row) for row in conn.execute(
            "SELECT * FROM v269c_promotion_reviews WHERE experience_id=? ORDER BY created_at,review_id", (experience_id,)
        ).fetchall()]
        events = [dict(row) for row in conn.execute(
            "SELECT * FROM v269c_lifecycle_events WHERE experience_id=? ORDER BY created_at,event_id", (experience_id,)
        ).fetchall()]
        heads = [dict(row) for row in conn.execute(
            "SELECT * FROM v269c_domain_head_events WHERE experience_id=? ORDER BY created_at,head_event_id", (experience_id,)
        ).fetchall()]
        current_head = _head_in_conn(conn, item["domain"])
    for review in reviews:
        review["gate_payload"] = _decode(review.get("gate_payload"), {})
    body = {
        "schema": "experience.promotion.history.v269c.v1",
        "version": VERSION,
        "experienceId": experience_id,
        "domain": item["domain"],
        "lifecycleStatus": item["lifecycle_status"],
        "currentKnowledgeHead": current_head,
        "reviews": reviews,
        "lifecycleEvents": events,
        "headEvents": heads,
    }
    return {**body, "receiptHash": store.digest(body)}


def task_promotion_candidates(task_id: str) -> dict[str, Any]:
    ensure_promotion_tables()
    task = str(task_id or "").strip()
    with repo.connect() as conn:
        rows = conn.execute(
            """SELECT i.experience_id,i.domain,i.lifecycle_status,i.applicability,i.updated_at
               FROM v269b_experience_items i
               JOIN v269b_experience_sources s ON s.source_id=i.source_id
               WHERE s.source_task_id=? AND s.source_type='runtime'
               ORDER BY i.domain,i.experience_id""",
            (task,),
        ).fetchall()
        items = []
        for row in rows:
            value = dict(row)
            value["applicability"] = _decode(value["applicability"], {})
            if value["lifecycle_status"] in {"candidate", "approved"} and value["domain"] != "evaluation_results":
                value["promotionGate"] = _gate_in_conn(conn, value["experience_id"])
            items.append(value)
    body = {
        "schema": "experience.promotion.task_candidates.v269c.v1",
        "version": VERSION,
        "taskId": task,
        "items": items,
        "autoPromotionPerformed": False,
    }
    return {**body, "receiptHash": store.digest(body)}


__all__ = [
    "VERSION",
    "manifest",
    "policy",
    "policy_hash",
    "runtime_enabled",
    "ensure_promotion_tables",
    "evaluate_promotion_gate",
    "review_candidate",
    "enable_experience",
    "disable_experience",
    "promotion_history",
    "task_promotion_candidates",
]
