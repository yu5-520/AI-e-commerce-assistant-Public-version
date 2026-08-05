"""V22.2.5 truth-preserving pre-Agent station queue.

The queue stops after product-level signal admission. A real station failure is a
queue failure, never a completed business stage. Successful business output is
stored once in Artifact Hub and the resulting ``ART-`` reference becomes the next
station's input.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.pipeline_gate_service import record_stage_gate
from src.services.pipeline_item_service import (
    PIPELINE_ITEM_VERSION,
    build_item_envelope,
    pipeline_item_summary,
    upsert_pipeline_item,
)
from src.services.station_business_artifact_service import record_business_station_output
from src.services.station_contract_service import run_station_contract

STATION_QUEUE_VERSION = "22.2.5"
TASK_GENERATION_SEQUENCE = [
    ("report_receive_station", "report_received"),
    ("report_schema_station", "report_schema_mapped"),
    ("report_fact_station", "report_facts_ready"),
    ("product_master_station", "product_master_ready"),
    ("product_metric_snapshot_station", "product_metric_snapshot_ready"),
    ("full_product_bundle_station", "full_product_bundle_ready"),
    ("bundle_validation_station", "bundle_validation_ready"),
    ("product_signal_admission_station", "product_signal_admitted"),
]
REMOVED_DOWNSTREAM_STATIONS = [
    "product_judgment_agent_station",
    "product_judgment_package_station",
    "rag_permission_context_station",
    "action_parameter_enrichment_station",
    "action_plan_judgment_agent_station",
    "task_mapping_agent_station",
    "task_pool_admission_station",
    "frontend_read_model_station",
    "task_pool_acceptance_station",
]
STATION_INDEX = {station: index for index, (station, _stage) in enumerate(TASK_GENERATION_SEQUENCE)}
STATION_PRIORITY = {station: index * 10 + 10 for index, (station, _stage) in enumerate(TASK_GENERATION_SEQUENCE)}
FAST_LANE_STATIONS: set[str] = set()
ITEM_COLUMNS = {
    "item_id": "TEXT",
    "product_id": "TEXT",
    "store_id": "TEXT",
    "signal_id": "TEXT",
    "package_id": "TEXT",
    "action_family": "TEXT",
    "priority_score": "INTEGER DEFAULT 50",
    "idempotency_key": "TEXT",
    "dependency_ref": "TEXT",
    "micro_batch_id": "TEXT",
}


class StationRunRejected(RuntimeError):
    def __init__(self, station_id: str, run: Dict[str, Any]) -> None:
        self.station_id = station_id
        self.run = run
        adapter_error = run.get("adapterError")
        output_contract = run.get("outputContract") if isinstance(run.get("outputContract"), dict) else {}
        input_contract = run.get("inputContract") if isinstance(run.get("inputContract"), dict) else {}
        missing = output_contract.get("missing") or input_contract.get("missing") or []
        message = (
            str(adapter_error)
            if adapter_error
            else f"station_run_rejected:{station_id}:{run.get('status')}:"
            + ",".join(str(value) for value in missing)
        )
        super().__init__(message)


def now_iso() -> str:
    return datetime.now().isoformat()


def _job_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def _priority_for(station_id: str, default: int = 50) -> int:
    return int(STATION_PRIORITY.get(station_id, default))


def ensure_queue_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                job_id TEXT PRIMARY KEY,
                system_type TEXT NOT NULL,
                tenant_id TEXT,
                actor_user_id TEXT,
                data_version TEXT,
                status TEXT NOT NULL,
                current_station TEXT,
                input_ref TEXT,
                output_ref TEXT,
                payload TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS station_queue (
                station_job_id TEXT PRIMARY KEY,
                parent_job_id TEXT NOT NULL,
                system_type TEXT NOT NULL,
                station_id TEXT NOT NULL,
                stage TEXT,
                data_version TEXT,
                status TEXT NOT NULL,
                priority INTEGER DEFAULT 50,
                input_ref TEXT,
                output_ref TEXT,
                payload TEXT,
                attempt_count INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                locked_by TEXT,
                locked_until TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "pipeline_jobs",
            {
                "system_type": "TEXT",
                "tenant_id": "TEXT",
                "actor_user_id": "TEXT",
                "data_version": "TEXT",
                "status": "TEXT",
                "current_station": "TEXT",
                "input_ref": "TEXT",
                "output_ref": "TEXT",
                "payload": "TEXT",
                "error_message": "TEXT",
                "updated_at": "TEXT",
            },
        )
        ensure_columns(
            conn,
            "station_queue",
            {
                "parent_job_id": "TEXT",
                "system_type": "TEXT",
                "station_id": "TEXT",
                "stage": "TEXT",
                "data_version": "TEXT",
                "status": "TEXT",
                "priority": "INTEGER DEFAULT 50",
                "input_ref": "TEXT",
                "output_ref": "TEXT",
                "payload": "TEXT",
                "attempt_count": "INTEGER DEFAULT 0",
                "max_attempts": "INTEGER DEFAULT 3",
                "locked_by": "TEXT",
                "locked_until": "TEXT",
                "error_message": "TEXT",
                "updated_at": "TEXT",
                **ITEM_COLUMNS,
            },
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_status ON pipeline_jobs(system_type, status, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_version ON pipeline_jobs(data_version, system_type, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_station_queue_status ON station_queue(system_type, status, priority, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_station_queue_parent ON station_queue(parent_job_id, station_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_station_queue_item ON station_queue(data_version, item_id, station_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_station_queue_idempotency ON station_queue(idempotency_key)")
        for removed in REMOVED_DOWNSTREAM_STATIONS:
            conn.execute(
                "UPDATE station_queue SET status='disabled', error_message='V22.2.5 station removed from pre-Agent queue' WHERE station_id=? AND status IN ('queued','retry','running')",
                (removed,),
            )
        for station_id, priority in STATION_PRIORITY.items():
            conn.execute(
                "UPDATE station_queue SET priority=? WHERE station_id=? AND status IN ('queued','retry')",
                (priority, station_id),
            )
        conn.commit()


def _row_to_job(row: Any) -> Dict[str, Any]:
    return {
        "version": STATION_QUEUE_VERSION,
        "jobId": row["job_id"],
        "systemType": row["system_type"],
        "tenantId": row["tenant_id"],
        "actorUserId": row["actor_user_id"],
        "dataVersion": row["data_version"],
        "status": row["status"],
        "currentStation": row["current_station"],
        "inputRef": row["input_ref"],
        "outputRef": row["output_ref"],
        "payload": loads(row["payload"]),
        "errorMessage": row["error_message"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _row_to_station(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    envelope = payload.get("pipelineItemEnvelope") if isinstance(payload, dict) and isinstance(payload.get("pipelineItemEnvelope"), dict) else None
    if not envelope and row["item_id"]:
        envelope = build_item_envelope(
            data_version=row["data_version"],
            item_id=row["item_id"],
            product_id=row["product_id"],
            store_id=row["store_id"],
            signal_id=row["signal_id"],
            package_id=row["package_id"],
            action_family=row["action_family"],
            input_ref=row["input_ref"],
            output_ref=row["output_ref"],
            stage=row["stage"],
        )
    return {
        "version": STATION_QUEUE_VERSION,
        "stationJobId": row["station_job_id"],
        "parentJobId": row["parent_job_id"],
        "systemType": row["system_type"],
        "stationId": row["station_id"],
        "stage": row["stage"],
        "dataVersion": row["data_version"],
        "status": row["status"],
        "priority": row["priority"],
        "fastLane": False,
        "inputRef": row["input_ref"],
        "outputRef": row["output_ref"],
        "payload": payload,
        "pipelineItemEnvelope": envelope,
        "itemId": row["item_id"],
        "productId": row["product_id"],
        "storeId": row["store_id"],
        "signalId": row["signal_id"],
        "packageId": row["package_id"],
        "actionFamily": row["action_family"],
        "priorityScore": row["priority_score"],
        "idempotencyKey": row["idempotency_key"],
        "dependencyRef": row["dependency_ref"],
        "microBatchId": row["micro_batch_id"],
        "attemptCount": row["attempt_count"],
        "maxAttempts": row["max_attempts"],
        "lockedBy": row["locked_by"],
        "lockedUntil": row["locked_until"],
        "errorMessage": row["error_message"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _envelope_from_body(
    body: Dict[str, Any],
    *,
    data_version: str | None,
    station_id: str,
    stage: str,
    input_ref: str | None,
) -> Dict[str, Any]:
    raw = body.get("pipelineItemEnvelope") if isinstance(body.get("pipelineItemEnvelope"), dict) else {}
    return build_item_envelope(
        data_version=raw.get("dataVersion") or data_version,
        item_id=raw.get("itemId"),
        product_id=raw.get("productId") or body.get("productId"),
        store_id=raw.get("storeId") or body.get("storeId"),
        signal_id=raw.get("signalId") or body.get("signalId"),
        package_id=raw.get("packageId") or body.get("packageId"),
        decision_id=raw.get("decisionId") or body.get("decisionId"),
        task_id=raw.get("taskId") or body.get("taskId"),
        action_family=raw.get("actionFamily") or body.get("actionFamily"),
        route=raw.get("route") or body.get("route"),
        input_ref=raw.get("inputRef") or input_ref,
        output_ref=raw.get("outputRef"),
        stage=raw.get("stage") or stage or station_id,
        artifact_refs=raw.get("artifactRefs") if isinstance(raw.get("artifactRefs"), dict) else {},
    )


def _insert_station_job(
    conn: Any,
    *,
    parent_job_id: str,
    system_type: str,
    station_id: str,
    stage: str,
    data_version: str | None,
    actor_user_id: str | None,
    input_ref: str | None,
    payload: Dict[str, Any] | None = None,
    priority: int | None = None,
) -> str:
    station_job_id = _job_id("SQ")
    body = dict(payload or {})
    body.setdefault("dataVersion", data_version)
    body.setdefault("userId", actor_user_id)
    body.setdefault("source", "station_queue")
    envelope = _envelope_from_body(
        body,
        data_version=data_version,
        station_id=station_id,
        stage=stage,
        input_ref=input_ref,
    )
    body["pipelineItemEnvelope"] = envelope
    selected_priority = _priority_for(station_id, priority if priority is not None else 50)
    priority_score = int(body.get("priorityScore") or envelope.get("priority") or selected_priority)
    idempotency_key = body.get("idempotencyKey") or envelope.get("idempotencyKey")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO station_queue (
            station_job_id,parent_job_id,system_type,station_id,stage,data_version,
            status,priority,input_ref,output_ref,payload,attempt_count,max_attempts,
            item_id,product_id,store_id,signal_id,package_id,action_family,
            priority_score,idempotency_key,dependency_ref,micro_batch_id,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,'queued',?,?,NULL,?,0,3,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            station_job_id,
            parent_job_id,
            system_type,
            station_id,
            stage,
            data_version,
            int(selected_priority),
            input_ref,
            dumps(body),
            envelope.get("itemId"),
            envelope.get("productId"),
            envelope.get("storeId"),
            envelope.get("signalId"),
            envelope.get("packageId"),
            envelope.get("actionFamily"),
            priority_score,
            idempotency_key,
            body.get("dependencyRef") or input_ref,
            body.get("microBatchId"),
            now,
            now,
        ),
    )
    return station_job_id


def enqueue_task_generation(
    data_version: str | None,
    *,
    actor_user_id: str | None = None,
    input_ref: str | None = None,
    source: str = "import_completed",
    force: bool = True,
    priority: int = 10,
) -> Dict[str, Any]:
    ensure_queue_tables()
    raw_ref = input_ref or f"raw_report:{data_version or 'latest'}"
    seed_envelope = build_item_envelope(
        data_version=data_version,
        item_id=f"PI-BATCH-{data_version or 'latest'}",
        input_ref=raw_ref,
        stage="batch_created",
    )
    seed = upsert_pipeline_item(
        seed_envelope,
        stage="batch_created",
        status="running",
        priority=100,
        output_ref=raw_ref,
        payload={
            "version": STATION_QUEUE_VERSION,
            "source": "enqueue_task_generation",
            "dataVersion": data_version,
            "legacySignalPoolSeeded": False,
        },
    )
    now = now_iso()
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT * FROM pipeline_jobs
            WHERE system_type='task_generation'
              AND COALESCE(data_version,'')=COALESCE(?, '')
              AND status IN ('queued','running')
            ORDER BY updated_at DESC LIMIT 1
            """,
            (data_version,),
        ).fetchone()
        if existing:
            return {
                "version": STATION_QUEUE_VERSION,
                "queued": False,
                "idempotentHit": True,
                "job": _row_to_job(existing),
                "pipelineItemSeed": seed,
                "rule": "One pre-Agent job already exists for this business dataVersion.",
            }
        job_id = _job_id("JOB-TASKGEN")
        first_station, first_stage = TASK_GENERATION_SEQUENCE[0]
        envelope = build_item_envelope(
            data_version=data_version,
            item_id=f"PI-BATCH-{data_version or 'latest'}",
            input_ref=raw_ref,
            stage=first_stage,
            artifact_refs=seed.get("artifactRefs") or {},
        )
        payload = {
            "version": STATION_QUEUE_VERSION,
            "pipelineItemVersion": PIPELINE_ITEM_VERSION,
            "source": source,
            "force": force,
            "dataVersion": data_version,
            "actorUserId": actor_user_id,
            "sequence": [station for station, _ in TASK_GENERATION_SEQUENCE],
            "removedDownstreamStations": REMOVED_DOWNSTREAM_STATIONS,
            "pipelineItemEnvelope": envelope,
            "boundary": "Queue stops after Artifact signal fan-out; Agent1 is only a product-level pipeline item worker.",
        }
        conn.execute(
            """
            INSERT INTO pipeline_jobs (
                job_id,system_type,actor_user_id,data_version,status,current_station,
                input_ref,payload,created_at,updated_at
            ) VALUES (?, 'task_generation', ?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                actor_user_id,
                data_version,
                first_station,
                raw_ref,
                dumps(payload),
                now,
                now,
            ),
        )
        station_job_id = _insert_station_job(
            conn,
            parent_job_id=job_id,
            system_type="task_generation",
            station_id=first_station,
            stage=first_stage,
            data_version=data_version,
            actor_user_id=actor_user_id,
            input_ref=raw_ref,
            payload={
                "dataVersion": data_version,
                "userId": actor_user_id,
                "force": force,
                "source": source,
                "maxSignals": 160,
                "pipelineItemEnvelope": envelope,
            },
            priority=priority,
        )
        conn.commit()
        job = conn.execute("SELECT * FROM pipeline_jobs WHERE job_id=?", (job_id,)).fetchone()
    record_stage_gate(
        data_version=data_version,
        stage="task_generation_queued",
        status="queued",
        input_payload={"source": source, "inputRef": raw_ref, "pipelineItemEnvelope": envelope},
        output_payload={
            "jobId": job_id,
            "stationJobId": station_job_id,
            "sequence": [station for station, _ in TASK_GENERATION_SEQUENCE],
        },
        user_id=actor_user_id,
        upstream_stage="report_received",
        output_ref=f"pipeline_job:{job_id}",
    )
    return {
        "version": STATION_QUEUE_VERSION,
        "queued": True,
        "job": _row_to_job(job),
        "stationJobId": station_job_id,
        "status": "queued",
        "pipelineItemSeed": seed,
        "rule": "Pre-Agent queue ends after product signalRef items are created.",
    }


def _claim_next_station(system_type: str = "task_generation", *, worker_id: str = "manual-worker") -> Dict[str, Any] | None:
    ensure_queue_tables()
    now = now_iso()
    lock_until = (datetime.now() + timedelta(minutes=10)).isoformat()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM station_queue
            WHERE system_type=?
              AND status IN ('queued','retry')
              AND station_id NOT IN ({})
              AND (locked_until IS NULL OR locked_until < ?)
            ORDER BY priority ASC, priority_score ASC, created_at ASC LIMIT 1
            """.format(",".join(["?"] * len(REMOVED_DOWNSTREAM_STATIONS))),
            (system_type, *REMOVED_DOWNSTREAM_STATIONS, now),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE station_queue SET status='running',locked_by=?,locked_until=?,attempt_count=attempt_count+1,updated_at=? WHERE station_job_id=?",
            (worker_id, lock_until, now, row["station_job_id"]),
        )
        conn.execute(
            "UPDATE pipeline_jobs SET status='running',current_station=?,updated_at=? WHERE job_id=?",
            (row["station_id"], now, row["parent_job_id"]),
        )
        conn.commit()
        claimed = conn.execute("SELECT * FROM station_queue WHERE station_job_id=?", (row["station_job_id"],)).fetchone()
    return _row_to_station(claimed)


def _next_station_for(station_id: str) -> tuple[str, str] | None:
    index = STATION_INDEX.get(station_id)
    if index is None or index + 1 >= len(TASK_GENERATION_SEQUENCE):
        return None
    return TASK_GENERATION_SEQUENCE[index + 1]


def _body_for_station(job: Dict[str, Any], station_job: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(station_job.get("payload") or {})
    payload.setdefault("dataVersion", station_job.get("dataVersion"))
    payload.setdefault("userId", job.get("actorUserId"))
    payload.setdefault("force", True)
    payload.setdefault("source", "station_queue")
    payload.setdefault("maxSignals", 160)
    payload.setdefault("limit", 160)
    if station_job.get("pipelineItemEnvelope"):
        payload["pipelineItemEnvelope"] = station_job["pipelineItemEnvelope"]
    input_ref = station_job.get("inputRef")
    ref_key = {
        "report_schema_station": "rawReportRef",
        "report_fact_station": "reportSchemaMappingRef",
        "product_master_station": "factRef",
        "product_metric_snapshot_station": "productMasterRef",
        "full_product_bundle_station": "productMetricSnapshotRef",
        "bundle_validation_station": "fullProductBundleRef",
        "product_signal_admission_station": "validatedBundleRef",
    }.get(station_job.get("stationId"))
    if ref_key:
        payload[ref_key] = input_ref
    return payload


def _should_stream_to_next(station_id: str, output: Dict[str, Any]) -> bool:
    if station_id == "bundle_validation_station":
        return int(output.get("bundleCount") or 0) > 0
    return True


def _compact_output(output: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "version",
        "stationId",
        "dataVersion",
        "businessOutputType",
        "rowCount",
        "headerCount",
        "productFactCount",
        "productMasterCount",
        "productMetricSnapshotCount",
        "productSignalPackageCount",
        "productSignalCount",
        "bundleCount",
        "validationStatus",
        "fullSignalCount",
        "qualifiedSignalCount",
        "candidateProductCount",
        "admittedSignalCount",
        "observedSignalCount",
        "agent1PendingItemCount",
        "contractValidation",
    ]
    return {key: output.get(key) for key in keys if output.get(key) is not None}


def _failure_status(station_job: Dict[str, Any]) -> str:
    return "retry" if int(station_job.get("attemptCount") or 0) < int(station_job.get("maxAttempts") or 3) else "failed"


def run_next_station_job(*, worker_id: str = "manual-worker", system_type: str = "task_generation") -> Dict[str, Any]:
    station_job = _claim_next_station(system_type=system_type, worker_id=worker_id)
    if not station_job:
        return {"version": STATION_QUEUE_VERSION, "ran": False, "status": "empty", "message": "No queued pre-Agent station job."}
    ensure_queue_tables()
    with connect() as conn:
        job_row = conn.execute("SELECT * FROM pipeline_jobs WHERE job_id=?", (station_job["parentJobId"],)).fetchone()
    job = _row_to_job(job_row)
    body = _body_for_station(job, station_job)
    station_id = station_job["stationId"]
    gate_already_recorded = False
    try:
        run = run_station_contract(station_id, body, diagnostic=False)
        gate_already_recorded = True
        if run.get("ok") is not True or run.get("status") != "completed":
            raise StationRunRejected(station_id, run)
        output = run.get("output") if isinstance(run.get("output"), dict) else {}
        item_state = record_business_station_output(
            station_id=station_id,
            data_version=job.get("dataVersion"),
            output=output,
            upstream_envelope=body.get("pipelineItemEnvelope"),
            input_ref=station_job.get("inputRef"),
        )
        artifact_ref = str(item_state.get("payloadArtifactRef") or "")
        if not artifact_ref.startswith("ART-"):
            raise RuntimeError(f"station_business_artifact_missing:{station_id}")
        now = now_iso()
        next_station = _next_station_for(station_id)
        inserted_next_id = None
        with connect() as conn:
            conn.execute(
                "UPDATE station_queue SET status='completed',output_ref=?,payload=?,locked_until=NULL,error_message=NULL,updated_at=? WHERE station_job_id=?",
                (
                    artifact_ref,
                    dumps(
                        {
                            "body": body,
                            "stationRun": {
                                "stationId": station_id,
                                "status": "completed",
                                "artifactRef": artifact_ref,
                                "outputSummary": _compact_output(output),
                            },
                            "pipelineItemEnvelope": body.get("pipelineItemEnvelope"),
                        }
                    ),
                    now,
                    station_job["stationJobId"],
                ),
            )
            if next_station and _should_stream_to_next(station_id, output):
                next_id, next_stage = next_station
                upstream = body.get("pipelineItemEnvelope") if isinstance(body.get("pipelineItemEnvelope"), dict) else {}
                next_envelope = build_item_envelope(
                    data_version=job.get("dataVersion"),
                    item_id=upstream.get("itemId"),
                    product_id=upstream.get("productId"),
                    store_id=upstream.get("storeId"),
                    signal_id=upstream.get("signalId"),
                    package_id=upstream.get("packageId"),
                    action_family=upstream.get("actionFamily"),
                    input_ref=artifact_ref,
                    stage=next_stage,
                    artifact_refs=item_state.get("artifactRefs") or {},
                )
                inserted_next_id = _insert_station_job(
                    conn,
                    parent_job_id=job["jobId"],
                    system_type=job["systemType"],
                    station_id=next_id,
                    stage=next_stage,
                    data_version=job.get("dataVersion"),
                    actor_user_id=job.get("actorUserId"),
                    input_ref=artifact_ref,
                    payload={
                        "dataVersion": job.get("dataVersion"),
                        "userId": job.get("actorUserId"),
                        "force": True,
                        "source": "station_queue",
                        "maxSignals": 160,
                        "pipelineItemEnvelope": next_envelope,
                    },
                    priority=_priority_for(next_id),
                )
                conn.execute(
                    "UPDATE pipeline_jobs SET status='running',current_station=?,output_ref=?,error_message=NULL,updated_at=? WHERE job_id=?",
                    (next_id, artifact_ref, now, job["jobId"]),
                )
            else:
                conn.execute(
                    "UPDATE pipeline_jobs SET status='completed',current_station=?,output_ref=?,error_message=NULL,updated_at=? WHERE job_id=?",
                    (station_id, artifact_ref, now, job["jobId"]),
                )
            conn.commit()
        return {
            "version": STATION_QUEUE_VERSION,
            "ran": True,
            "status": "completed",
            "stationJobId": station_job["stationJobId"],
            "stationId": station_id,
            "dataVersion": job.get("dataVersion"),
            "outputRef": artifact_ref,
            "nextStation": next_station[0] if next_station and inserted_next_id else None,
            "insertedNextStationJobId": inserted_next_id,
            "pipelineItemState": item_state,
            "output": _compact_output(output),
            "runtimeReceiptStoredAsBusinessArtifact": False,
            "duplicateCompletedGateWritten": False,
            "rule": "Only a completed real station and validated business Artifact can advance the queue.",
        }
    except Exception as exc:
        now = now_iso()
        status = _failure_status(station_job)
        with connect() as conn:
            conn.execute(
                "UPDATE station_queue SET status=?,locked_until=NULL,error_message=?,updated_at=? WHERE station_job_id=?",
                (status, str(exc), now, station_job["stationJobId"]),
            )
            conn.execute(
                "UPDATE pipeline_jobs SET status=?,error_message=?,updated_at=? WHERE job_id=?",
                ("failed" if status == "failed" else "running", str(exc), now, job["jobId"]),
            )
            conn.commit()
        if not gate_already_recorded:
            record_stage_gate(
                data_version=job.get("dataVersion"),
                stage=station_job.get("stage") or station_id,
                status=status,
                input_payload={
                    "stationJobId": station_job["stationJobId"],
                    "pipelineItemEnvelope": station_job.get("pipelineItemEnvelope"),
                },
                output_payload={},
                user_id=job.get("actorUserId"),
                error_message=str(exc),
                output_ref=station_job.get("inputRef"),
            )
        return {
            "version": STATION_QUEUE_VERSION,
            "ran": True,
            "status": status,
            "stationJobId": station_job["stationJobId"],
            "stationId": station_id,
            "error": str(exc),
            "businessArtifactWritten": False,
            "nextStationCreated": False,
            "rule": "Station failure remains a failure and cannot become a business Artifact or downstream station.",
        }


def queue_summary(data_version: str | None = None, *, limit: int = 50) -> Dict[str, Any]:
    ensure_queue_tables()
    where: List[str] = []
    params: List[Any] = []
    if data_version:
        where.append("data_version=?")
        params.append(data_version)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect() as conn:
        jobs = conn.execute(f"SELECT * FROM pipeline_jobs{clause} ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
        stations = conn.execute(f"SELECT * FROM station_queue{clause} ORDER BY priority ASC,updated_at DESC LIMIT ?", (*params, limit)).fetchall()
    by_status: Dict[str, int] = {}
    by_station_status: Dict[str, int] = {}
    for row in stations:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        key = f"{row['station_id']}:{row['status']}"
        by_station_status[key] = by_station_status.get(key, 0) + 1
    return {
        "version": STATION_QUEUE_VERSION,
        "mode": "artifact_truth_pre_agent_queue",
        "pipelineItemVersion": PIPELINE_ITEM_VERSION,
        "jobCount": len(jobs),
        "stationJobCount": len(stations),
        "stationByStatus": by_status,
        "stationByStationStatus": by_station_status,
        "pipelineItems": pipeline_item_summary(data_version=data_version, limit=limit),
        "removedDownstreamStations": REMOVED_DOWNSTREAM_STATIONS,
        "priorities": STATION_PRIORITY,
        "sequence": [station for station, _ in TASK_GENERATION_SEQUENCE],
        "jobs": [_row_to_job(row) for row in jobs],
        "stationJobs": [_row_to_station(row) for row in stations],
        "legacySignalPoolSeeded": False,
        "stringFallbackRefAllowed": False,
        "duplicateCompletedGateAllowed": False,
        "rule": "Queue ends after signalRef fan-out; Agent1 and later stages are product-level workers only.",
    }
