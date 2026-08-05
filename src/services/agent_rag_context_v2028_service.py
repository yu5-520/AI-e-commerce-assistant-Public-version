"""V20.28 dynamic Agent RAG context service.

The current Agent chain keeps stable operating policy in Agent1 and retrieves
approved, effective historical experience once per product after Agent1 locks the
action family. Retrieval is context only: an empty result never blocks Agent2.

Production retrieval deliberately excludes seed_approved/demo cards and reads
only cards that were approved from a real task recap.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, loads

AGENT_RAG_CONTEXT_VERSION = "20.28"
AGENT_RAG_CONTEXT_MODE = "approved_effective_experience_after_agent1_lock"
DEFAULT_LIMIT = max(1, min(8, int(os.getenv("AGENT_DYNAMIC_RAG_LIMIT", "5"))))
MIN_QUALITY = max(0.0, min(1.0, float(os.getenv("AGENT_DYNAMIC_RAG_MIN_QUALITY", "0.70"))))
DYNAMIC_RAG_ENABLED = str(os.getenv("AGENT_DYNAMIC_RAG_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}

LEGACY_PROBLEM_TYPES = {
    "title_image_test": {"title_image_test", "low_ctr_low_conversion"},
    "roas_scale": {"roas_scale", "paid_growth_window", "general_operation"},
    "roas_guard": {"roas_guard", "low_roi_high_refund"},
    "platform_activity": {"platform_activity", "low_inventory_activity"},
    "conversion_repair": {"conversion_repair", "detail_page_conversion", "low_ctr_low_conversion"},
    "similar_product_test": {"similar_product_test", "competitor_signal_to_test", "listing_test_path"},
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in {"", "—", "未识别", "UNKNOWN", "null", "None"})


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _table_exists(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _family(package: Dict[str, Any]) -> str:
    agent1 = _dict(package.get("agent1OperatingJudgment"))
    lock = _dict(agent1.get("actionFamilyLock"))
    matrix = _dict(package.get("matrixDispatch"))
    return str(
        lock.get("selectedActionFamily")
        or agent1.get("selectedActionFamily")
        or package.get("actionFamily")
        or package.get("selectedActionFamily")
        or matrix.get("selectedActionFamily")
        or "similar_product_test"
    ).strip()


def _identity(package: Dict[str, Any]) -> Dict[str, Any]:
    identity = _dict(package.get("productIdentity"))
    return {
        **identity,
        "productId": package.get("productId") or identity.get("productId"),
        "storeId": package.get("storeId") or identity.get("storeId") or "global",
        "productTitle": package.get("productTitle") or package.get("title") or identity.get("productTitle") or identity.get("title") or identity.get("shortTitle"),
        "platform": identity.get("platform") or package.get("platform"),
        "verticalCategory": identity.get("verticalCategory") or package.get("verticalCategory") or package.get("categoryId"),
        "productRole": identity.get("productRole") or package.get("productRole"),
    }


def _agent1(package: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(package.get("agent1OperatingJudgment"))


def _clean_text(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _query_payload(package: Dict[str, Any], action_pack: Dict[str, Any] | None = None) -> Dict[str, Any]:
    identity = _identity(package)
    judgment = _agent1(package)
    pack = _dict(action_pack)
    family = _family(package)
    mode_labels = [str(item.get("label") or item.get("mode") or "") for item in _arr(pack.get("operationModeCandidates")) if isinstance(item, dict)]
    return {
        "actionFamily": family,
        "productTitle": identity.get("productTitle"),
        "platform": identity.get("platform"),
        "verticalCategory": identity.get("verticalCategory"),
        "storeId": identity.get("storeId"),
        "productRole": identity.get("productRole"),
        "primaryBusinessSignal": judgment.get("primaryBusinessSignal"),
        "primaryOperatingGap": judgment.get("primaryOperatingGap") or judgment.get("businessHypothesis"),
        "selectedOperatingRoute": judgment.get("selectedOperatingRoute") or package.get("selectedOperatingRoute"),
        "operationModeCandidates": mode_labels[:4],
        "reviewMetrics": [str(item) for item in _arr(pack.get("reviewMetrics"))[:8]],
    }


def _fingerprint(query_payload: Dict[str, Any]) -> str:
    raw = json.dumps(query_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _row_to_case(row: Any) -> Dict[str, Any]:
    try:
        payload = loads(row["payload"])
    except Exception:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    payload.update(
        {
            "caseId": row["case_id"],
            "caseType": row["case_type"],
            "level": row["level"],
            "status": row["status"],
            "categoryId": row["category_id"],
            "platform": row["platform"],
            "storeId": row["store_id"],
            "problemType": row["problem_type"],
            "operatorStyle": row["operator_style"],
            "qualityScore": float(row["quality_score"] or 0),
            "effective": bool(row["effective"]),
            "sourceTaskId": row["source_task_id"],
            "updatedAt": row["updated_at"],
        }
    )
    return payload


def _load_real_approved_cases(limit: int = 300) -> List[Dict[str, Any]]:
    """Read only real, reviewed task experience; never seed Demo cards."""
    with connect() as conn:
        if not _table_exists(conn, "rag_experience_cards"):
            return []
        rows = conn.execute(
            """
            SELECT *
            FROM rag_experience_cards
            WHERE status = 'approved'
              AND effective = 1
              AND quality_score >= ?
              AND source_task_id IS NOT NULL
              AND TRIM(source_task_id) != ''
            ORDER BY quality_score DESC, updated_at DESC
            LIMIT ?
            """,
            (MIN_QUALITY, max(20, min(1000, int(limit)))),
        ).fetchall()
    result = []
    for row in rows:
        item = _row_to_case(row)
        if item.get("seedVersion") or str(item.get("status") or "") == "seed_approved":
            continue
        result.append(item)
    return result


def _tokens(value: Any) -> List[str]:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", " ", str(value or "").lower())
    parts = [part for part in text.split() if len(part) >= 2]
    return list(dict.fromkeys(parts))[:30]


def _case_blob(card: Dict[str, Any]) -> str:
    values: List[str] = []
    for key in ["title", "initialJudgment", "resultSummary", "problemType", "operatorStyle", "platform", "categoryId", "caseType"]:
        if card.get(key):
            values.append(str(card.get(key)))
    for key in ["effectiveActions", "applicableConditions", "notApplicableConditions", "judgmentTags", "crossValidationRules"]:
        values.extend(str(item) for item in _arr(card.get(key)))
    return " ".join(values).lower()


def _score_case(card: Dict[str, Any], query: Dict[str, Any]) -> float:
    family = str(query.get("actionFamily") or "")
    score = _safe_float(card.get("qualityScore"), 0.0)
    if card.get("problemType") in LEGACY_PROBLEM_TYPES.get(family, {family}):
        score += 0.45
    if card.get("actionFamily") == family:
        score += 0.45
    if query.get("verticalCategory") and card.get("categoryId") in {query.get("verticalCategory"), "global"}:
        score += 0.24
    if query.get("platform") and card.get("platform") in {query.get("platform"), "通用"}:
        score += 0.18
    if query.get("storeId") and card.get("storeId") in {query.get("storeId"), "global"}:
        score += 0.10
    query_text = " ".join(str(query.get(key) or "") for key in ["productTitle", "primaryBusinessSignal", "primaryOperatingGap", "selectedOperatingRoute"])
    blob = _case_blob(card)
    score += min(0.40, 0.05 * sum(1 for token in _tokens(query_text) if token in blob))
    return round(score, 4)


def _metric_excerpt(value: Any) -> Dict[str, Any]:
    data = _dict(value)
    keys = list(data.keys())[:8]
    return {key: data.get(key) for key in keys}


def _compact_card(card: Dict[str, Any], score: float) -> Dict[str, Any]:
    """Return principles and result signals, never a copy-ready historical SOP."""
    actions = [_clean_text(item, 120) for item in _arr(card.get("effectiveActions")) if _clean_text(item, 120)]
    applicable = [_clean_text(item, 100) for item in _arr(card.get("applicableConditions")) if _clean_text(item, 100)]
    not_applicable = [_clean_text(item, 100) for item in _arr(card.get("notApplicableConditions")) if _clean_text(item, 100)]
    return {
        "caseId": card.get("caseId"),
        "caseType": card.get("caseType"),
        "level": card.get("level"),
        "actionFamily": card.get("actionFamily") or card.get("problemType"),
        "platform": card.get("platform"),
        "categoryId": card.get("categoryId"),
        "storeId": card.get("storeId"),
        "qualityScore": _safe_float(card.get("qualityScore"), 0.0),
        "retrievalScore": score,
        "experiencePrinciples": actions[:3],
        "applicableConditions": applicable[:4],
        "notApplicableConditions": not_applicable[:4],
        "resultSignal": _clean_text(card.get("resultSummary"), 160),
        "beforeMetrics": _metric_excerpt(card.get("beforeMetrics")),
        "afterMetrics": _metric_excerpt(card.get("afterMetrics")),
        "sourceTaskId": card.get("sourceTaskId"),
        "contentRule": "principles_only_no_copy_ready_historical_sop",
    }


def build_agent_rag_context_snapshot(
    package: Dict[str, Any],
    action_pack: Dict[str, Any] | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    query = _query_payload(package, action_pack)
    fingerprint = _fingerprint(query)
    existing = _dict(package.get("ragContextSnapshot"))
    if existing.get("version") == AGENT_RAG_CONTEXT_VERSION and existing.get("queryFingerprint") == fingerprint:
        return {**existing, "reusedFromPipelinePayload": True, "retrievalCountThisCall": 0}

    base = {
        "version": AGENT_RAG_CONTEXT_VERSION,
        "mode": AGENT_RAG_CONTEXT_MODE,
        "queryFingerprint": fingerprint,
        "query": query,
        "actionFamily": query.get("actionFamily"),
        "operatingGap": query.get("primaryOperatingGap"),
        "retrievalSource": "rag_experience_cards",
        "retrievalExecuted": bool(DYNAMIC_RAG_ENABLED),
        "retrievalCount": 1 if DYNAMIC_RAG_ENABLED else 0,
        "retrievalCountThisCall": 1 if DYNAMIC_RAG_ENABLED else 0,
        "taskGate": False,
        "emptyResultAllowed": True,
        "demoSeedExcluded": True,
        "minimumQuality": MIN_QUALITY,
        "generatedAt": datetime.now().isoformat(),
    }
    if not DYNAMIC_RAG_ENABLED:
        return {**base, "status": "disabled_by_env", "matchedCount": 0, "approvedCaseIds": [], "positiveExperienceCards": [], "negativeCases": []}

    candidates = _load_real_approved_cases()
    scored = [(card, _score_case(card, query)) for card in candidates]
    scored.sort(key=lambda item: item[1], reverse=True)
    positive: List[Dict[str, Any]] = []
    negative: List[Dict[str, Any]] = []
    for card, score in scored:
        if score < MIN_QUALITY:
            continue
        compact = _compact_card(card, score)
        is_negative = card.get("caseType") == "negative_case" or card.get("level") == "L4"
        if is_negative and len(negative) < 2:
            negative.append(compact)
        elif not is_negative and len(positive) < max(1, int(limit)):
            positive.append(compact)
        if len(positive) >= max(1, int(limit)) and len(negative) >= 2:
            break

    all_cards = positive + negative
    ids = [str(item.get("caseId")) for item in all_cards if item.get("caseId")]
    return {
        **base,
        "status": "matched" if ids else "empty",
        "matchedCount": len(ids),
        "approvedCaseIds": ids,
        "positiveExperienceCards": positive,
        "negativeCases": negative,
        "retrievalRule": "approved + effective + real sourceTaskId + metadata/keyword/quality rerank; seed_approved is excluded",
        "agentInstruction": "Use experience as applicability/risk context. Recalculate all objects, parameters and steps from current product facts; never copy a historical SOP.",
    }


def rag_context_summary(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    item = _dict(snapshot)
    return {
        "version": item.get("version"),
        "status": item.get("status"),
        "queryFingerprint": item.get("queryFingerprint"),
        "matchedCount": int(item.get("matchedCount") or 0),
        "approvedCaseIds": item.get("approvedCaseIds") or [],
        "retrievalSource": item.get("retrievalSource"),
        "demoSeedExcluded": item.get("demoSeedExcluded") is True,
        "taskGate": False,
    }
