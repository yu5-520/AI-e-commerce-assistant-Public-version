"""V26.9.B normalized Experience Store.

This layer is downstream of the production-active V26.9.A semantic chain. It records
post-execution evidence as candidate experience only. It cannot promote an experience,
mutate an Agent knowledge head, grant execution permission, or rewrite graph authority.
Seed rows are explicitly synthetic and are never eligible for official retrieval.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from src.repositories import sqlite_repository as repo

VERSION = "26.9.B.1"
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "rag/schema/v269b_experience_store.sql"
MIGRATION_PATH = ROOT / "rag/migrations/001_v269b_experience_store.sql"
SEED_PATH = ROOT / "rag/seeds/v269b_seed.json"
MANIFEST_PATH = ROOT / "rag/manifest/v269b_manifest.json"
DOMAINS = {
    "experience_knowledge",
    "decision_patterns",
    "strategy_outcomes",
    "operation_patterns",
    "evaluation_results",
}
RUNTIME_STATUS = "candidate"
SEED_STATUS = "seed"


class ExperienceStoreError(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ExperienceStoreError("v269b_" + reason)


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), "json_object_required")
    return value


def manifest() -> dict[str, Any]:
    value = _json_object(MANIFEST_PATH)
    _require(value.get("version") == VERSION, "manifest_version_mismatch")
    _require(value.get("runtimeWritePolicy", {}).get("promotionEnabled") is False, "promotion_must_remain_disabled")
    _require(value.get("runtimeWritePolicy", {}).get("automaticKnowledgeHeadMutation") is False, "knowledge_head_mutation_must_remain_disabled")
    return value


def ensure_experience_store() -> dict[str, Any]:
    """Install the canonical schema and migration ledger in the existing ECS SQLite DB."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")
    migration_hash = file_digest(MIGRATION_PATH)
    applied_at = _now()
    with repo.connect() as conn:
        conn.executescript(schema_sql)
        conn.executescript(migration_sql)
        conn.execute(
            """INSERT OR IGNORE INTO v269b_experience_migrations(
               migration_id,schema_version,migration_hash,applied_at
               ) VALUES(?,?,?,?)""",
            ("001_v269b_experience_store", VERSION, migration_hash, applied_at),
        )
        conn.commit()
    material = {
        "schema": "experience.store.install.receipt.v269b.v1",
        "version": VERSION,
        "databaseEngine": "sqlite",
        "databasePath": str(repo.DB_PATH),
        "schemaHash": file_digest(SCHEMA_PATH),
        "migrationHash": migration_hash,
        "manifestHash": file_digest(MANIFEST_PATH),
    }
    return {**material, "receiptHash": digest(material)}


def _normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(source, dict), "source_required")
    required = {
        "sourceTaskId",
        "decisionGraphHash",
        "planGraphHash",
        "operationGraphHash",
        "graphContractVersion",
        "evaluationVersion",
        "evidenceRefs",
        "businessScope",
        "sourceType",
        "sourceVersion",
    }
    _require(set(source) == required, "source_shape")
    result = deepcopy(source)
    _require(isinstance(result["sourceTaskId"], str) and result["sourceTaskId"], "source_task_required")
    _require(result["sourceType"] in {"runtime", "seed"}, "source_type")
    _require(isinstance(result["sourceVersion"], str) and result["sourceVersion"], "source_version")
    _require(isinstance(result["graphContractVersion"], str) and result["graphContractVersion"], "graph_contract_version")
    _require(result["evaluationVersion"] == VERSION, "evaluation_version_mismatch")
    _require(
        isinstance(result["evidenceRefs"], list)
        and all(isinstance(item, str) and item for item in result["evidenceRefs"]),
        "evidence_refs",
    )
    result["evidenceRefs"] = sorted(set(result["evidenceRefs"]))
    _require(isinstance(result["businessScope"], dict) and result["businessScope"], "business_scope")
    graph_fields = ("decisionGraphHash", "planGraphHash", "operationGraphHash")
    if result["sourceType"] == "runtime":
        for field in graph_fields:
            value = result[field]
            _require(isinstance(value, str) and value.startswith("sha256:") and len(value) == 71, "runtime_graph_identity_required")
    else:
        _require(all(result[field] is None for field in graph_fields), "seed_graph_identity_forbidden")
        _require(result["sourceTaskId"].startswith("seed:"), "seed_task_prefix")
    return result


def _source_identity(source: dict[str, Any]) -> tuple[str, str]:
    normalized = _normalize_source(source)
    source_hash = digest(normalized)
    source_id = "V269B-SRC-" + source_hash[-20:].upper()
    return source_id, source_hash


def _ensure_source(conn: sqlite3.Connection, source: dict[str, Any]) -> tuple[str, bool]:
    normalized = _normalize_source(source)
    source_id, source_hash = _source_identity(normalized)
    row = conn.execute(
        "SELECT source_id FROM v269b_experience_sources WHERE source_hash=?",
        (source_hash,),
    ).fetchone()
    if row:
        return str(row["source_id"]), True
    conn.execute(
        """INSERT INTO v269b_experience_sources(
           source_id,source_task_id,decision_graph_hash,plan_graph_hash,operation_graph_hash,
           graph_contract_version,evaluation_version,evidence_refs,business_scope,
           source_type,source_version,source_hash,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            source_id,
            normalized["sourceTaskId"],
            normalized["decisionGraphHash"],
            normalized["planGraphHash"],
            normalized["operationGraphHash"],
            normalized["graphContractVersion"],
            normalized["evaluationVersion"],
            json.dumps(normalized["evidenceRefs"], ensure_ascii=False, sort_keys=True),
            json.dumps(normalized["businessScope"], ensure_ascii=False, sort_keys=True),
            normalized["sourceType"],
            normalized["sourceVersion"],
            source_hash,
            _now(),
        ),
    )
    return source_id, False


def _nonnegative_int(value: Any, reason: str) -> int:
    _require(type(value) is int and value >= 0, reason)
    return int(value)


def _validate_domain_payload(domain: str, payload: dict[str, Any]) -> None:
    _require(domain in DOMAINS, "domain_unknown")
    _require(isinstance(payload, dict) and payload, "payload_required")
    if domain == "experience_knowledge":
        _require(isinstance(payload.get("knowledgeType"), str) and payload["knowledgeType"], "knowledge_type")
    elif domain == "decision_patterns":
        _require(isinstance(payload.get("decisionPattern"), str) and payload["decisionPattern"], "decision_pattern")
        _nonnegative_int(payload.get("sampleCount", 0), "sample_count")
        score = payload.get("graphValueScore")
        if score is not None:
            _require(type(score) in (int, float) and 0 <= score <= 1, "graph_value_score")
            _require(payload.get("graphValueSource") == "evaluation_plane", "graph_value_self_score_forbidden")
            _require(isinstance(payload.get("graphValueMetricVersion"), str) and payload["graphValueMetricVersion"], "graph_value_version")
    elif domain == "strategy_outcomes":
        for field in ("decisionActionKey", "planActionKey", "strategyType"):
            _require(isinstance(payload.get(field), str) and payload[field], "strategy_identity")
        _require(isinstance(payload.get("baseline"), dict), "strategy_baseline")
        _require(isinstance(payload.get("expected"), dict), "strategy_expected")
        _nonnegative_int(payload.get("sampleCount", 0), "sample_count")
    elif domain == "operation_patterns":
        for field in ("planActionKey", "executionType"):
            _require(isinstance(payload.get(field), str) and payload[field], "operation_identity")
        _nonnegative_int(payload.get("sampleCount", 0), "sample_count")
    else:
        for field in ("evaluationId", "metricId", "metricVersion", "unit", "observationWindow"):
            _require(isinstance(payload.get(field), str) and payload[field], "evaluation_identity")
        _require(isinstance(payload.get("formulaInputs"), dict), "evaluation_formula_inputs")
        _require(isinstance(payload.get("interpretationLimits"), list), "evaluation_interpretation_limits")
        _nonnegative_int(payload.get("sampleCount", 0), "sample_count")


def _insert_domain_row(conn: sqlite3.Connection, experience_id: str, domain: str, payload: dict[str, Any]) -> None:
    if domain == "experience_knowledge":
        conn.execute(
            """INSERT INTO v269b_experience_knowledge(
               experience_id,condition_key,metric,direction,category,knowledge_type
               ) VALUES(?,?,?,?,?,?)""",
            (experience_id, payload.get("conditionKey"), payload.get("metric"), payload.get("direction"), payload.get("category"), payload["knowledgeType"]),
        )
    elif domain == "decision_patterns":
        conn.execute(
            """INSERT INTO v269b_decision_patterns(
               experience_id,decision_action_key,condition_key,metric,direction,category,
               decision_pattern,graph_value_score,graph_value_metric_version,sample_count
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                experience_id, payload.get("decisionActionKey"), payload.get("conditionKey"), payload.get("metric"),
                payload.get("direction"), payload.get("category"), payload["decisionPattern"], payload.get("graphValueScore"),
                payload.get("graphValueMetricVersion"), payload.get("sampleCount", 0),
            ),
        )
    elif domain == "strategy_outcomes":
        conn.execute(
            """INSERT INTO v269b_strategy_outcomes(
               experience_id,decision_action_key,plan_action_key,category,strategy_type,
               baseline,expected,actual,prediction_error,sample_count
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                experience_id, payload["decisionActionKey"], payload["planActionKey"], payload.get("category"), payload["strategyType"],
                json.dumps(payload["baseline"], ensure_ascii=False, sort_keys=True),
                json.dumps(payload["expected"], ensure_ascii=False, sort_keys=True),
                json.dumps(payload.get("actual"), ensure_ascii=False, sort_keys=True) if payload.get("actual") is not None else None,
                payload.get("predictionError"), payload.get("sampleCount", 0),
            ),
        )
    elif domain == "operation_patterns":
        rollback = payload.get("rollbackOccurred")
        conn.execute(
            """INSERT INTO v269b_operation_patterns(
               experience_id,plan_action_key,platform,execution_type,completion_status,
               rollback_occurred,sample_count
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                experience_id, payload["planActionKey"], payload.get("platform"), payload["executionType"], payload.get("completionStatus"),
                None if rollback is None else int(bool(rollback)), payload.get("sampleCount", 0),
            ),
        )
    else:
        conn.execute(
            """INSERT INTO v269b_evaluation_results(
               experience_id,subject_experience_id,evaluation_id,metric_id,metric_version,
               numerator,denominator,value,unit,observation_window,sample_count,
               missing_reason,formula_inputs,interpretation_limits
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                experience_id, payload.get("subjectExperienceId"), payload["evaluationId"], payload["metricId"], payload["metricVersion"],
                payload.get("numerator"), payload.get("denominator"), payload.get("value"), payload["unit"], payload["observationWindow"],
                payload.get("sampleCount", 0), payload.get("missingReason"),
                json.dumps(payload["formulaInputs"], ensure_ascii=False, sort_keys=True),
                json.dumps(payload["interpretationLimits"], ensure_ascii=False, sort_keys=True),
            ),
        )


def record_experience(
    *,
    source: dict[str, Any],
    domain: str,
    applicability: dict[str, Any],
    payload: dict[str, Any],
    supersedes_experience_id: str | None = None,
) -> dict[str, Any]:
    """Write a seed or candidate record. B has no API that can write enabled status."""
    ensure_experience_store()
    normalized_source = _normalize_source(source)
    _require(isinstance(applicability, dict), "applicability_object")
    _validate_domain_payload(domain, payload)
    lifecycle_status = SEED_STATUS if normalized_source["sourceType"] == "seed" else RUNTIME_STATUS
    material = {
        "sourceHash": digest(normalized_source),
        "domain": domain,
        "applicability": applicability,
        "payload": payload,
        "sourceVersion": normalized_source["sourceVersion"],
    }
    idempotency_key = digest(material)
    experience_id = "V269B-EXP-" + idempotency_key[-20:].upper()
    now = _now()
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        source_id, source_hit = _ensure_source(conn, normalized_source)
        existing = conn.execute(
            "SELECT * FROM v269b_experience_items WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            conn.commit()
            return experience_view(experience_id=str(existing["experience_id"]), idempotent_hit=True)
        if supersedes_experience_id:
            prior = conn.execute(
                "SELECT lifecycle_status FROM v269b_experience_items WHERE experience_id=?",
                (supersedes_experience_id,),
            ).fetchone()
            _require(prior is not None, "superseded_experience_unknown")
        conn.execute(
            """INSERT INTO v269b_experience_items(
               experience_id,source_id,domain,lifecycle_status,applicability,payload,
               idempotency_key,supersedes_experience_id,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                experience_id, source_id, domain, lifecycle_status,
                json.dumps(applicability, ensure_ascii=False, sort_keys=True),
                json.dumps(payload, ensure_ascii=False, sort_keys=True), idempotency_key,
                supersedes_experience_id, now, now,
            ),
        )
        _insert_domain_row(conn, experience_id, domain, payload)
        conn.commit()
    view = experience_view(experience_id=experience_id)
    view["sourceIdempotentHit"] = source_hit
    return view


def experience_view(*, experience_id: str, idempotent_hit: bool = False) -> dict[str, Any]:
    ensure_experience_store()
    with repo.connect() as conn:
        row = conn.execute(
            """SELECT i.*,s.source_task_id,s.decision_graph_hash,s.plan_graph_hash,
                      s.operation_graph_hash,s.graph_contract_version,s.evaluation_version,
                      s.evidence_refs,s.business_scope,s.source_type,s.source_version,s.source_hash
               FROM v269b_experience_items i
               JOIN v269b_experience_sources s ON s.source_id=i.source_id
               WHERE i.experience_id=?""",
            (experience_id,),
        ).fetchone()
    _require(row is not None, "experience_not_found")
    material = {
        "schema": "experience.record.v269b.v1",
        "version": VERSION,
        "experienceId": row["experience_id"],
        "sourceId": row["source_id"],
        "sourceTaskId": row["source_task_id"],
        "domain": row["domain"],
        "lifecycleStatus": row["lifecycle_status"],
        "applicability": json.loads(row["applicability"]),
        "payload": json.loads(row["payload"]),
        "graphIdentity": {
            "DecisionGraphHash": row["decision_graph_hash"],
            "PlanGraphHash": row["plan_graph_hash"],
            "OperationGraphHash": row["operation_graph_hash"],
            "contractVersion": row["graph_contract_version"],
        },
        "evaluationVersion": row["evaluation_version"],
        "evidenceRefs": json.loads(row["evidence_refs"]),
        "businessScope": json.loads(row["business_scope"]),
        "sourceType": row["source_type"],
        "sourceVersion": row["source_version"],
        "sourceHash": row["source_hash"],
        "idempotencyKey": row["idempotency_key"],
        "supersedesExperienceId": row["supersedes_experience_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "idempotentHit": bool(idempotent_hit),
    }
    return {**material, "receiptHash": digest(material)}


def import_seed(path: Path | None = None) -> dict[str, Any]:
    seed_path = path or SEED_PATH
    seed = _json_object(seed_path)
    _require(seed.get("schema") == "experience.seed.v269b.v1", "seed_schema")
    records = seed.get("records")
    _require(isinstance(records, list), "seed_records")
    imported: list[str] = []
    hits = 0
    for record in records:
        _require(isinstance(record, dict) and set(record) == {"source", "experience"}, "seed_record_shape")
        exp = record["experience"]
        result = record_experience(
            source=record["source"],
            domain=exp["domain"],
            applicability=exp.get("applicability") or {},
            payload=exp["payload"],
        )
        imported.append(result["experienceId"])
        hits += int(result["idempotentHit"])
    material = {
        "schema": "experience.seed.import.receipt.v269b.v1",
        "version": VERSION,
        "seedVersion": seed.get("seedVersion"),
        "seedHash": file_digest(seed_path),
        "experienceIds": sorted(imported),
        "idempotentHits": hits,
        "officialRetrievalEligible": False,
    }
    return {**material, "receiptHash": digest(material)}


def knowledge_head(domains: Iterable[str]) -> str:
    selected = sorted(set(domains))
    _require(selected and set(selected) <= DOMAINS, "knowledge_head_domains")
    ensure_experience_store()
    placeholders = ",".join("?" for _ in selected)
    with repo.connect() as conn:
        rows = conn.execute(
            f"""SELECT experience_id,domain,payload,updated_at
                FROM v269b_experience_items
                WHERE lifecycle_status='enabled' AND domain IN ({placeholders})
                ORDER BY domain,experience_id""",
            tuple(selected),
        ).fetchall()
    material = [
        {
            "experienceId": row["experience_id"],
            "domain": row["domain"],
            "payloadHash": digest(json.loads(row["payload"])),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
    return digest({"version": VERSION, "domains": selected, "enabled": material})


def backup_experience_store(destination: Path | None = None) -> dict[str, Any]:
    ensure_experience_store()
    backup_dir = repo.LOG_DIR / "experience-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = destination or backup_dir / ("v269b-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite3")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with repo.connect() as source_conn:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    material = {
        "schema": "experience.backup.receipt.v269b.v1",
        "version": VERSION,
        "path": str(target),
        "contentHash": file_digest(target),
    }
    return {**material, "receiptHash": digest(material)}


def restore_experience_store(source: Path, *, explicit_operator_intent: bool = False) -> dict[str, Any]:
    _require(explicit_operator_intent, "restore_requires_explicit_operator_intent")
    source = Path(source)
    _require(source.is_file(), "restore_source_missing")
    source_conn = sqlite3.connect(source)
    try:
        with repo.connect() as target_conn:
            source_conn.backup(target_conn)
    finally:
        source_conn.close()
    material = {
        "schema": "experience.restore.receipt.v269b.v1",
        "version": VERSION,
        "sourcePath": str(source),
        "sourceHash": file_digest(source),
        "explicitOperatorIntent": True,
    }
    return {**material, "receiptHash": digest(material)}
