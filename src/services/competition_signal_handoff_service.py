"""Competition-only direct handoff from formal signal artifacts to Agent1 pending items.

This is deliberately a thin bridge, not a second workflow engine.  The formal
``product_signal_pool_v15`` signal artifact remains the immutable source of truth;
this module only projects its registered identity into ``pipeline_items`` while
preserving ``signalRef`` as a hard Artifact reference.  The existing hard Agent
runtime continues to own Agent1/2/3 execution, task mapping and task admission.

The bridge is idempotent by the existing deterministic pipeline item id and a
separate handoff hash.  It never regenerates product identity, never reads a
"latest" fallback when a dataVersion is supplied, and fails closed when the
formal signal Artifact reference is absent or invalid.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.artifact_transport_service import validate_artifact
from src.services.pipeline_item_service import (
    STAGE_ORDER,
    build_item_envelope,
    ensure_pipeline_item_tables,
    make_item_id,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.signal_pool_service import list_signals

COMPETITION_SIGNAL_HANDOFF_VERSION = "1.0.0"
AGENT1_PENDING_STAGE = "agent1_pending"
FORMAL_SIGNAL_TABLE = "product_signal_pool_v15"


class CompetitionSignalHandoffError(RuntimeError):
    pass


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _table_exists(table: str) -> bool:
    with connect() as conn:
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        )


def _signal_ref(signal: Dict[str, Any]) -> str:
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else {}
    refs = signal.get("artifactRefs") if isinstance(signal.get("artifactRefs"), dict) else {}
    payload_refs = payload.get("artifactRefs") if isinstance(payload.get("artifactRefs"), dict) else {}
    return _text(
        signal.get("signalRef")
        or signal.get("signal_ref")
        or refs.get("signalRef")
        or payload_refs.get("signalRef")
    )


def _identity(signal: Dict[str, Any]) -> Dict[str, str]:
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
    product_identity = payload.get("productIdentity") if isinstance(payload.get("productIdentity"), dict) else {}
    product_id = _text(
        signal.get("productId")
        or signal.get("entityId")
        or payload.get("productId")
        or product_identity.get("productId")
    )
    store_id = _text(
        signal.get("storeId")
        or payload.get("storeId")
        or product_identity.get("storeId")
    )
    signal_id = _text(
        signal.get("signalId")
        or signal.get("signal_id")
        or payload.get("signalId")
        or payload.get("signal_id")
    )
    registry_key = _text(
        signal.get("productRegistryKey")
        or payload.get("productRegistryKey")
        or product_identity.get("productRegistryKey")
    )
    return {
        "productId": product_id,
        "storeId": store_id,
        "signalId": signal_id,
        "productRegistryKey": registry_key,
    }


def _existing_item(item_id: str) -> Dict[str, Any]:
    ensure_pipeline_item_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
    return dict(row) if row else {}


def _validate_signal_ref(signal_ref: str) -> Dict[str, Any]:
    if not signal_ref.startswith("ART-"):
        return {"ok": False, "status": "formal_signal_ref_missing"}
    validation = validate_artifact(signal_ref)
    if validation.get("ok") is not True:
        return {
            "ok": False,
            "status": validation.get("status") or "formal_signal_ref_invalid",
            "detail": validation,
        }
    return {"ok": True, "status": "verified", "detail": validation}


def seed_competition_signal_handoff(
    data_version: str,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
    """Project formal pending signals directly into the registered Agent1 queue.

    No Provider is called here.  Existing items at Agent1 or any later stage are
    left untouched.  This makes repeated worker ticks safe and keeps the old
    station queue out of the competition critical path without deleting it.
    """

    data_version = _text(data_version)
    if not data_version:
        raise CompetitionSignalHandoffError("data_version_required")

    response = list_signals(
        data_version=data_version,
        status="pending_rag_agent",
        limit=max(1, min(1000, int(limit or 500))),
    )
    signals = [item for item in response.get("signals") or [] if isinstance(item, dict)]

    seeded = 0
    reused = 0
    blocked: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []

    for signal in signals:
        identity = _identity(signal)
        signal_ref = _signal_ref(signal)
        validation = _validate_signal_ref(signal_ref)
        if validation.get("ok") is not True:
            blocked.append(
                {
                    "signalId": identity.get("signalId"),
                    "productId": identity.get("productId"),
                    "reason": validation.get("status"),
                }
            )
            continue
        if not identity.get("productId") or not identity.get("signalId"):
            blocked.append(
                {
                    "signalRef": signal_ref,
                    "reason": "formal_signal_identity_missing",
                    "productId": identity.get("productId"),
                    "signalId": identity.get("signalId"),
                }
            )
            continue

        item_id = make_item_id(
            data_version,
            identity.get("productId"),
            identity.get("signalId"),
        )
        existing = _existing_item(item_id)
        existing_stage = _text(existing.get("current_stage"))
        if existing and STAGE_ORDER.get(existing_stage, 0) >= STAGE_ORDER[AGENT1_PENDING_STAGE]:
            reused += 1
            items.append(
                {
                    "itemId": item_id,
                    "signalRef": signal_ref,
                    "stage": existing_stage,
                    "status": existing.get("status"),
                    "idempotentHit": True,
                }
            )
            continue

        handoff_material = {
            "dataVersion": data_version,
            "signalRef": signal_ref,
            "signalId": identity.get("signalId"),
            "productId": identity.get("productId"),
            "storeId": identity.get("storeId"),
            "productRegistryKey": identity.get("productRegistryKey"),
            "target": "agent1_pending",
        }
        handoff_hash = _canonical_hash(handoff_material)
        artifact_refs = {"signalRef": signal_ref}
        envelope = build_item_envelope(
            data_version=data_version,
            item_id=item_id,
            product_id=identity.get("productId"),
            store_id=identity.get("storeId") or None,
            signal_id=identity.get("signalId"),
            input_ref=signal_ref,
            output_ref=f"competition_handoff:{handoff_hash}",
            stage=AGENT1_PENDING_STAGE,
            artifact_refs=artifact_refs,
        )
        payload = {
            "schema": "competition.signal_handoff.v1",
            "version": COMPETITION_SIGNAL_HANDOFF_VERSION,
            **handoff_material,
            "handoffHash": handoff_hash,
            "artifactRefs": artifact_refs,
            "source": "competition_signal_handoff_service",
            "legacyStationQueueRequired": False,
            "identityRegenerated": False,
            "providerCallsExecuted": 0,
        }
        envelope = upsert_pipeline_item(
            envelope,
            stage=AGENT1_PENDING_STAGE,
            status="queued",
            priority=max(1, min(100, 100 - int(signal.get("score") or signal.get("admissionScore") or 0))),
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        record_pipeline_item_event(
            envelope,
            station_id="competition_signal_handoff",
            stage=AGENT1_PENDING_STAGE,
            status="queued",
            input_ref=signal_ref,
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        seeded += 1
        items.append(
            {
                "itemId": item_id,
                "signalRef": signal_ref,
                "handoffHash": handoff_hash,
                "stage": AGENT1_PENDING_STAGE,
                "status": "queued",
                "idempotentHit": False,
            }
        )

    return {
        "schema": "competition.signal_handoff.receipt.v1",
        "version": COMPETITION_SIGNAL_HANDOFF_VERSION,
        "dataVersion": data_version,
        "formalSignalCount": len(signals),
        "seededCount": seeded,
        "idempotentCount": reused,
        "blockedCount": len(blocked),
        "blocked": blocked,
        "items": items,
        "providerCallsExecuted": 0,
        "legacyStationQueueRequired": False,
        "rule": "formal signalRef is preserved as the exact immutable Agent1 source Artifact",
    }


def ready_data_versions(*, limit: int = 4) -> List[str]:
    """Return only versions that already have formal pending signal-pool rows."""

    if not _table_exists(FORMAL_SIGNAL_TABLE):
        return []
    with connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({FORMAL_SIGNAL_TABLE})").fetchall()
        }
        if "data_version" not in columns:
            return []
        where = ""
        params: List[Any] = []
        if "status" in columns:
            where = "WHERE status=?"
            params.append("pending_rag_agent")
        rows = conn.execute(
            f"SELECT data_version, MAX(rowid) AS rid FROM {FORMAL_SIGNAL_TABLE} {where} "
            "GROUP BY data_version ORDER BY rid ASC LIMIT ?",
            (*params, max(1, min(20, int(limit or 4)))),
        ).fetchall()
    return [_text(row["data_version"]) for row in rows if _text(row["data_version"])]


def seed_ready_competition_handoffs(*, limit_versions: int = 4) -> Dict[str, Any]:
    versions = ready_data_versions(limit=limit_versions)
    receipts = [seed_competition_signal_handoff(version) for version in versions]
    return {
        "schema": "competition.signal_handoff.batch_receipt.v1",
        "version": COMPETITION_SIGNAL_HANDOFF_VERSION,
        "dataVersions": versions,
        "versionCount": len(versions),
        "seededCount": sum(int(item.get("seededCount") or 0) for item in receipts),
        "idempotentCount": sum(int(item.get("idempotentCount") or 0) for item in receipts),
        "blockedCount": sum(int(item.get("blockedCount") or 0) for item in receipts),
        "receipts": receipts,
        "providerCallsExecuted": 0,
    }


__all__ = [
    "COMPETITION_SIGNAL_HANDOFF_VERSION",
    "CompetitionSignalHandoffError",
    "seed_competition_signal_handoff",
    "seed_ready_competition_handoffs",
    "ready_data_versions",
]
