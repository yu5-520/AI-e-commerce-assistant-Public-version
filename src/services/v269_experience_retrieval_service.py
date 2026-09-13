"""V26.9.B deterministic field retrieval over Experience Store.

No vector search and no model-driven query expansion are used in B. Official retrieval
reads only runtime experiences already in lifecycle_status=enabled. V26.9.B itself has
no API capable of creating that state, so Promotion remains a V26.9.C responsibility.
Seed/candidate inspection must be explicitly requested and is labelled non-official.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from src.repositories import sqlite_repository as repo
from src.services import v269_experience_store_service as store

VERSION = "26.9.B.1"
AGENT_DOMAINS = {
    "agent1": ("decision_patterns", "experience_knowledge"),
    "agent2": ("strategy_outcomes",),
    "agent3": ("operation_patterns",),
}
QUERY_FIELDS = {
    "agent1": {"condition", "metric", "direction", "category", "decisionPattern"},
    "agent2": {"decisionAction", "baseline", "category", "strategyType"},
    "agent3": {"planAction", "platform", "executionType"},
}


class ExperienceRetrievalError(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ExperienceRetrievalError("v269b_retrieval_" + reason)


def _decode(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


def _fetch_rows(agent: str, *, mode: str, include_seed: bool) -> list[dict[str, Any]]:
    domains = AGENT_DOMAINS[agent]
    placeholders = ",".join("?" for _ in domains)
    if mode == "official":
        status_clause = "i.lifecycle_status='enabled' AND s.source_type='runtime'"
        params: list[Any] = list(domains)
    else:
        statuses = ["candidate", "approved", "enabled", "disabled", "superseded"]
        if include_seed:
            statuses.append("seed")
        status_placeholders = ",".join("?" for _ in statuses)
        status_clause = f"i.lifecycle_status IN ({status_placeholders})"
        params = list(domains) + statuses
    sql = f"""SELECT i.*,s.source_task_id,s.decision_graph_hash,s.plan_graph_hash,
                     s.operation_graph_hash,s.graph_contract_version,s.evaluation_version,
                     s.evidence_refs,s.business_scope,s.source_type,s.source_version,
                     d.decision_action_key,d.condition_key AS d_condition,d.metric AS d_metric,
                     d.direction AS d_direction,d.category AS d_category,d.decision_pattern,
                     d.graph_value_score,d.graph_value_metric_version,d.sample_count AS d_sample_count,
                     k.condition_key AS k_condition,k.metric AS k_metric,k.direction AS k_direction,
                     k.category AS k_category,k.knowledge_type,
                     st.decision_action_key AS st_decision_action_key,st.plan_action_key AS st_plan_action_key,
                     st.category AS st_category,st.strategy_type,st.baseline,st.expected,st.actual,
                     st.prediction_error,st.sample_count AS st_sample_count,
                     op.plan_action_key AS op_plan_action_key,op.platform,op.execution_type,
                     op.completion_status,op.rollback_occurred,op.sample_count AS op_sample_count
              FROM v269b_experience_items i
              JOIN v269b_experience_sources s ON s.source_id=i.source_id
              LEFT JOIN v269b_decision_patterns d ON d.experience_id=i.experience_id
              LEFT JOIN v269b_experience_knowledge k ON k.experience_id=i.experience_id
              LEFT JOIN v269b_strategy_outcomes st ON st.experience_id=i.experience_id
              LEFT JOIN v269b_operation_patterns op ON op.experience_id=i.experience_id
              WHERE i.domain IN ({placeholders}) AND {status_clause}"""
    with repo.connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def _field_value(agent: str, row: dict[str, Any], field: str) -> Any:
    domain = row["domain"]
    if agent == "agent1":
        if field == "condition":
            return row["d_condition"] if domain == "decision_patterns" else row["k_condition"]
        if field == "metric":
            return row["d_metric"] if domain == "decision_patterns" else row["k_metric"]
        if field == "direction":
            return row["d_direction"] if domain == "decision_patterns" else row["k_direction"]
        if field == "category":
            return row["d_category"] if domain == "decision_patterns" else row["k_category"]
        if field == "decisionPattern":
            return row["decision_pattern"]
    elif agent == "agent2":
        return {
            "decisionAction": row["st_decision_action_key"],
            "baseline": _decode(row["baseline"], None),
            "category": row["st_category"],
            "strategyType": row["strategy_type"],
        }.get(field)
    else:
        return {
            "planAction": row["op_plan_action_key"],
            "platform": row["platform"],
            "executionType": row["execution_type"],
        }.get(field)
    return None


def _matches(agent: str, row: dict[str, Any], query: dict[str, Any]) -> tuple[bool, int, list[str]]:
    matched: list[str] = []
    for field, expected in query.items():
        actual = _field_value(agent, row, field)
        if actual != expected:
            return False, 0, []
        matched.append(field)
    return True, len(matched), sorted(matched)


def _sample_count(row: dict[str, Any]) -> int:
    if row["domain"] == "decision_patterns":
        return int(row["d_sample_count"] or 0)
    if row["domain"] == "strategy_outcomes":
        return int(row["st_sample_count"] or 0)
    if row["domain"] == "operation_patterns":
        return int(row["op_sample_count"] or 0)
    return 0


def _result(row: dict[str, Any], *, matched_fields: list[str], mode: str) -> dict[str, Any]:
    payload = _decode(row["payload"], {})
    applicability = _decode(row["applicability"], {})
    official = row["lifecycle_status"] == "enabled" and row["source_type"] == "runtime"
    return {
        "experienceId": row["experience_id"],
        "domain": row["domain"],
        "lifecycleStatus": row["lifecycle_status"],
        "sourceType": row["source_type"],
        "sourceVersion": row["source_version"],
        "sourceTaskId": row["source_task_id"],
        "graphIdentity": {
            "DecisionGraphHash": row["decision_graph_hash"],
            "PlanGraphHash": row["plan_graph_hash"],
            "OperationGraphHash": row["operation_graph_hash"],
            "contractVersion": row["graph_contract_version"],
        },
        "evaluationVersion": row["evaluation_version"],
        "evidenceRefs": _decode(row["evidence_refs"], []),
        "businessScope": _decode(row["business_scope"], {}),
        "applicability": applicability,
        "payload": payload,
        "sampleCount": _sample_count(row),
        "matchedFields": matched_fields,
        "rankingBasis": {
            "method": "exact_field_match_then_sample_count_then_experience_id",
            "matchedFieldCount": len(matched_fields),
            "sampleCount": _sample_count(row),
        },
        "officialEligible": official,
        "retrievalMode": mode,
    }


def retrieve_experience(
    agent: str,
    query: dict[str, Any],
    *,
    limit: int = 10,
    mode: str = "official",
    include_seed: bool = False,
) -> dict[str, Any]:
    """Return an auditable retrieval receipt. No match is a valid explicit empty result."""
    _require(agent in AGENT_DOMAINS, "agent_unknown")
    _require(isinstance(query, dict), "query_object")
    _require(set(query) <= QUERY_FIELDS[agent], "query_field_not_allowed")
    _require(type(limit) is int and 1 <= limit <= 100, "limit")
    _require(mode in {"official", "inspection"}, "mode")
    if mode == "official":
        _require(include_seed is False, "official_seed_forbidden")
    store.ensure_experience_store()
    rows = _fetch_rows(agent, mode=mode, include_seed=include_seed)
    matched: list[tuple[int, int, str, dict[str, Any]]] = []
    for row in rows:
        ok, score, fields = _matches(agent, row, query)
        if not ok:
            continue
        item = _result(row, matched_fields=fields, mode=mode)
        matched.append((score, item["sampleCount"], item["experienceId"], item))
    matched.sort(key=lambda item: (-item[0], -item[1], item[2]))
    results = [item[3] for item in matched[:limit]]
    domains = AGENT_DOMAINS[agent]
    material = {
        "schema": "experience.retrieval.receipt.v269b.v1",
        "version": VERSION,
        "agent": agent,
        "query": deepcopy(query),
        "mode": mode,
        "includeSeed": bool(include_seed),
        "domains": list(domains),
        "matchCount": len(results),
        "emptyResult": len(results) == 0,
        "rankingMethod": "exact_field_match_then_sample_count_then_experience_id",
        "knowledgeHead": store.knowledge_head(domains),
        "resultIds": [item["experienceId"] for item in results],
    }
    return {
        **material,
        "results": results,
        "receiptHash": store.digest(material),
    }
