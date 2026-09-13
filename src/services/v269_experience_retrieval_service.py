"""V26.9.B deterministic field retrieval over Experience Store.

No vector search and no model-driven query expansion are used in B. Official retrieval
reads only runtime experiences already in lifecycle_status=enabled. V26.9.B itself has
no API capable of creating that state, so Promotion remains a V26.9.C responsibility.
Seed/candidate inspection must be explicitly requested and is labelled non-official.

The production Agent bridge is candidate-gated. Until the B manifest is activated (or
an explicit candidate runtime env is set), this module returns the existing A knowledge
context unchanged. Once active, retrieval receipts and matched experience records are
folded into the existing knowledgeContext contract, so current semantic identity/cache
logic automatically binds the Experience Store head without introducing a second RAG
or cache authority.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from typing import Any

from src.repositories import sqlite_repository as repo
from src.services import v269_experience_store_service as store

VERSION = "26.9.B.1"
AGENT_DOMAINS = {
    "agent1": ("decision_patterns", "experience_knowledge"),
    "agent2": ("strategy_outcomes", "experience_knowledge"),
    "agent3": ("operation_patterns", "experience_knowledge"),
}
QUERY_FIELDS = {
    "agent1": {"condition", "metric", "direction", "category", "decisionPattern"},
    "agent2": {"decisionAction", "baseline", "category", "strategyType"},
    "agent3": {"planAction", "platform", "executionType"},
}
_CONTEXT_RECEIPT_SCHEMA = "experience.retrieval.context_receipt.v269b.v1"
_CONTEXT_RECORD_SCHEMA = "experience.context.record.v269b.v1"
_CONTEXT_SCHEMAS = {_CONTEXT_RECEIPT_SCHEMA, _CONTEXT_RECORD_SCHEMA}


class ExperienceRetrievalError(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ExperienceRetrievalError("v269b_retrieval_" + reason)


def runtime_enabled() -> bool:
    cfg = store.manifest()
    if cfg.get("rolloutStatus") == "active":
        return True
    env = str(cfg.get("candidateRuntimeEnv") or "V269B_CANDIDATE_RUNTIME")
    return str(os.getenv(env, "")).strip().lower() in {"1", "true", "yes", "on"}


def _context_body(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrievalPolicyHash": value["retrievalPolicyHash"],
        "records": value["records"],
    }


def _canonical_base_context(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the incoming head and peel off one prior B projection if present.

    Reprojection is common for replay/rebinding. B records therefore replace the prior
    B layer instead of accumulating. The receipt stores the original A/base head and
    policy hash so a second projection can reconstruct that authority exactly.
    """
    _require(
        isinstance(value, dict)
        and set(value) == {"headHash", "retrievalPolicyHash", "records"}
        and isinstance(value.get("retrievalPolicyHash"), str)
        and bool(value.get("retrievalPolicyHash"))
        and isinstance(value.get("records"), list),
        "base_knowledge_context",
    )
    _require(value["headHash"] == store.digest(_context_body(value)), "knowledge_head_mismatch")

    b_records = [
        item for item in value["records"]
        if isinstance(item, dict) and item.get("schema") in _CONTEXT_SCHEMAS
    ]
    if not b_records:
        return deepcopy(value)

    receipt_records = [
        item for item in b_records if item.get("schema") == _CONTEXT_RECEIPT_SCHEMA
    ]
    _require(bool(receipt_records), "prior_context_receipt_required")
    base_policy_hashes = {
        str(item.get("baseRetrievalPolicyHash") or "") for item in receipt_records
    }
    base_head_hashes = {str(item.get("baseHeadHash") or "") for item in receipt_records}
    _require(len(base_policy_hashes) == 1 and "" not in base_policy_hashes, "base_policy_recovery")
    _require(len(base_head_hashes) == 1 and "" not in base_head_hashes, "base_head_recovery")

    base_records = [
        deepcopy(item) for item in value["records"]
        if not (isinstance(item, dict) and item.get("schema") in _CONTEXT_SCHEMAS)
    ]
    base_policy_hash = next(iter(base_policy_hashes))
    base_body = {"retrievalPolicyHash": base_policy_hash, "records": base_records}
    base_head_hash = store.digest(base_body)
    _require(base_head_hash == next(iter(base_head_hashes)), "base_head_recovery_mismatch")
    return {"headHash": base_head_hash, **base_body}


def _decode(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


def _fetch_rows(agent: str, *, mode: str, include_seed: bool, business_scope: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    domains = AGENT_DOMAINS[agent]
    placeholders = ",".join("?" for _ in domains)
    if mode == "official":
        status_clause = "i.lifecycle_status='enabled' AND (s.source_type='runtime' OR (json_extract(s.business_scope,'$.storeId')=? AND json_extract(s.business_scope,'$.productId')=?))"
        scope=business_scope or {}
        params: list[Any] = list(domains)+[scope.get('storeId',''),scope.get('productId','')]
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
    result=[]
    with repo.connect() as conn:
        from src.services.v2610_initialization_service import is_registered_method
        for raw in rows:
            row=dict(raw)
            row['initializationMethod']=is_registered_method(conn,row)
            if mode!='official' or row['source_type']=='runtime' or row['initializationMethod']:
                result.append(row)
    return result


def _field_value(agent: str, row: dict[str, Any], field: str) -> Any:
    domain = row["domain"]
    payload = _decode(row.get("payload"), {})
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
            "decisionAction": payload.get("decisionAction", row["st_decision_action_key"]),
            "baseline": _decode(row["baseline"], None),
            "category": row["st_category"],
            "strategyType": row["strategy_type"],
        }.get(field)
    else:
        return {
            "planAction": payload.get("planAction", row["op_plan_action_key"]),
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
    official = row["lifecycle_status"] == "enabled" and (row["source_type"] == "runtime" or row.get("initializationMethod") is True)
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
    business_scope: dict[str, Any] | None = None,
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
    rows = _fetch_rows(agent, mode=mode, include_seed=include_seed, business_scope=business_scope)
    matched: list[tuple[int, int, str, dict[str, Any]]] = []
    for row in rows:
        if row.get('initializationMethod'):
            from src.services.v2610_initialization_service import method_matches
            ok=method_matches(row,agent,query,business_scope)
            fields=sorted(k for k in query if k in {'category','platform'})
            score=len(fields)
        else:
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
    return {**material, "results": results, "receiptHash": store.digest(material)}


def _direction(current: Any, previous: Any) -> str | None:
    if isinstance(current, bool) or isinstance(previous, bool):
        return None
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return None
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


def derive_queries(agent: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministically derive only query fields already present in the current input."""
    _require(agent in AGENT_DOMAINS and isinstance(source, dict), "query_source")
    queries: list[dict[str, Any]] = []
    if agent == "agent1":
        facts = source.get("BusinessFacts") if isinstance(source.get("BusinessFacts"), dict) else {}
        signals = facts.get("fieldSignals") if isinstance(facts, dict) else []
        prepared = []
        for signal in signals or []:
            if not isinstance(signal, dict):
                continue
            metric = str(signal.get("metricCode") or signal.get("metricName") or "").strip()
            if not metric:
                continue
            query: dict[str, Any] = {"metric": metric}
            direction = _direction(signal.get("current", signal.get("latest")), signal.get("previous"))
            if direction:
                query["direction"] = direction
            for key, output in (("category", "category"), ("condition", "condition")):
                value = signal.get(key)
                if isinstance(value, str) and value.strip():
                    query[output] = value.strip()
            prepared.append(query)
        queries = sorted(prepared, key=lambda q: store.digest(q))[:4]
    elif agent == "agent2":
        graph = source.get("DecisionGraph") if isinstance(source.get("DecisionGraph"), dict) else {}
        nodes = graph.get("nodes") if isinstance(graph, dict) else []
        for node in nodes or []:
            if not isinstance(node, dict) or node.get("kind") != "DecisionActionNode":
                continue
            action_type = str(node.get("actionType") or "").strip()
            action_family = str(node.get("actionFamily") or "").strip()
            if not action_type or not action_family:
                continue
            queries.append({
                "decisionAction": {"actionType": action_type, "actionFamily": action_family},
                "strategyType": action_family,
            })
        queries.sort(key=store.digest)
    else:
        graph = source.get("PlanGraph") if isinstance(source.get("PlanGraph"), dict) else {}
        nodes = graph.get("nodes") if isinstance(graph, dict) else []
        for node in nodes or []:
            if not isinstance(node, dict) or node.get("kind") != "PlanActionNode":
                continue
            action_family = str(node.get("actionFamily") or "").strip()
            query: dict[str, Any] = {}
            if action_family:
                query["planAction"] = {"actionFamily": action_family}
            operations = ((node.get("parameters") or {}).get("operationPlan") or {}).get("operations")
            if isinstance(operations, list) and operations:
                operation_type = str((operations[0] or {}).get("operationType") or "").strip()
                if operation_type:
                    query["executionType"] = operation_type
            if query:
                queries.append(query)
        queries.sort(key=store.digest)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        key = store.digest(query)
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique[:8]


def attach_official_experience_context(
    agent: str,
    source: dict[str, Any],
    base_context: dict[str, Any],
) -> dict[str, Any]:
    """Fold official retrieval receipts into the existing knowledgeContext authority.

    The incoming head is validated before any transformation. If it already contains
    one B projection layer, that layer is peeled and deterministically replaced. This
    keeps replay/rebinding idempotent and prevents B from healing a tampered A head.
    """
    _require(agent in AGENT_DOMAINS, "agent_unknown")
    base = _canonical_base_context(base_context)
    if not runtime_enabled():
        return deepcopy(base_context)

    queries = derive_queries(agent, source)
    receipts: list[dict[str, Any]] = []
    experience_records: dict[str, dict[str, Any]] = {}
    if queries:
        for query in queries:
            receipt = retrieve_experience(agent, query, limit=5, mode="official", business_scope={k:source.get(k) for k in ("storeId","productId")})
            receipts.append(receipt)
            for item in receipt["results"]:
                experience_records[item["experienceId"]] = {
                    "schema": _CONTEXT_RECORD_SCHEMA,
                    "experienceId": item["experienceId"],
                    "domain": item["domain"],
                    "payload": deepcopy(item["payload"]),
                    "applicability": deepcopy(item["applicability"]),
                    "sampleCount": item["sampleCount"],
                    "evidenceRefs": deepcopy(item["evidenceRefs"]),
                    "evaluationVersion": item["evaluationVersion"],
                    "sourceVersion": item["sourceVersion"],
                    "graphIdentity": deepcopy(item["graphIdentity"]),
                }
    else:
        material = {
            "schema": "experience.retrieval.receipt.v269b.v1",
            "version": VERSION,
            "agent": agent,
            "query": {},
            "mode": "official",
            "includeSeed": False,
            "domains": list(AGENT_DOMAINS[agent]),
            "matchCount": 0,
            "emptyResult": True,
            "rankingMethod": "exact_field_match_then_sample_count_then_experience_id",
            "knowledgeHead": store.knowledge_head(AGENT_DOMAINS[agent]),
            "resultIds": [],
            "reason": "QUERY_FIELDS_UNAVAILABLE",
        }
        receipts.append({**material, "results": [], "receiptHash": store.digest(material)})

    receipt_records = [
        {
            "schema": _CONTEXT_RECEIPT_SCHEMA,
            "receiptHash": receipt["receiptHash"],
            "query": deepcopy(receipt["query"]),
            "matchCount": receipt["matchCount"],
            "emptyResult": receipt["emptyResult"],
            "knowledgeHead": receipt["knowledgeHead"],
            "baseRetrievalPolicyHash": base["retrievalPolicyHash"],
            "baseHeadHash": base["headHash"],
            **({"reason": receipt["reason"]} if receipt.get("reason") else {}),
        }
        for receipt in receipts
    ]
    records = deepcopy(base["records"]) + receipt_records + [
        experience_records[key] for key in sorted(experience_records)
    ]
    policy_material = {
        "schema": "v269b.experience_retrieval_policy.v1",
        "version": VERSION,
        "agent": agent,
        "baseRetrievalPolicyHash": base["retrievalPolicyHash"],
        "baseHeadHash": base["headHash"],
        "mode": "official_exact_field_only",
        "vectorRetrieval": False,
        "dynamicQueryExpansion": False,
        "receiptHashes": [receipt["receiptHash"] for receipt in receipts],
    }
    body = {"retrievalPolicyHash": store.digest(policy_material), "records": records}
    return {"headHash": store.digest(body), **body}


__all__ = [
    "VERSION",
    "AGENT_DOMAINS",
    "QUERY_FIELDS",
    "runtime_enabled",
    "derive_queries",
    "retrieve_experience",
    "attach_official_experience_context",
]
