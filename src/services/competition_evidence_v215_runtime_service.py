"""Competition-safe V21.5 operating-evidence bridge.

This module is imported by the V22 runtime *before* V21.5 installs its historical
monkey patches.  It therefore captures the hash-precache Evidence materializer as the
immutable competition core, then reinstalls one bounded V21.5 cross-validation wrapper
after V21.5 has patched the public symbol.

The wrapper preserves the V21.5 ``operatingEvidenceGraph.v1`` decision semantics but
never calls ``product_snapshot_history(..., limit=90)`` and never reconstructs an
Evidence bundle from complete historical canonical payloads.  It reads only the compact
observation rows already selected by the Evidence input hash (maximum two comparable
history reports), applies V21.5 cross-validation to those bounded observations, and
persists the enriched snapshot under the same ``evidenceInputHash``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services import product_signal_snapshot_service as signal_snapshot
from src.services import product_signal_admission_v197_service as admission

BRIDGE_VERSION = "competitionEvidence.v21_5_hash_bridge.v1"

# Capture before ``v215_report_batch_evidence_service.install_v215_runtime`` rewires
# signal_snapshot.materialize_product_signal_snapshot.
_HASH_PRECACHE_MATERIALIZER = signal_snapshot.materialize_product_signal_snapshot


def _text(value: Any) -> str:
    return str(value or "").strip()


def _object_key(item: Dict[str, Any]) -> str:
    return str(
        item.get("objectId")
        or f"{item.get('storeId') or 'GLOBAL'}::{item.get('productId') or item.get('id')}::{item.get('skuId') or 'NO-SKU'}"
    )


def _index_products(snapshot: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    return {
        _object_key(item): item
        for item in (snapshot or {}).get("products") or []
        if isinstance(item, dict)
    }


def _compact_history_by_hashes(set_hashes: List[str]) -> List[Dict[str, Any]]:
    wanted = [value for value in set_hashes[:2] if _text(value)]
    if not wanted:
        return []
    history: List[Dict[str, Any]] = []
    for set_hash in wanted:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT payload,observation_hash
                FROM competition_evidence_observation_v1
                WHERE set_snapshot_hash=?
                ORDER BY julianday(updated_at) DESC,rowid DESC
                LIMIT 1
                """,
                (set_hash,),
            ).fetchone()
        if not row:
            raise RuntimeError(f"competition_evidence_observation_missing:{set_hash}")
        payload = loads(row["payload"])
        if not isinstance(payload, dict):
            raise RuntimeError(f"competition_evidence_observation_invalid:{set_hash}")
        if _text(payload.get("observationHash")) != _text(row["observation_hash"]):
            raise RuntimeError(f"competition_evidence_observation_hash_mismatch:{set_hash}")
        history.append(payload)
    return history


def _current_compact_observation(result: Dict[str, Any]) -> Dict[str, Any]:
    set_hash = _text(result.get("currentProductSetHash"))
    observation_hash = _text(result.get("currentObservationHash"))
    if not set_hash.startswith("sha256:") or not observation_hash.startswith("sha256:"):
        raise RuntimeError("competition_evidence_current_hash_identity_missing")
    with connect() as conn:
        row = conn.execute(
            """
            SELECT payload,observation_hash
            FROM competition_evidence_observation_v1
            WHERE set_snapshot_hash=?
            ORDER BY julianday(updated_at) DESC,rowid DESC
            LIMIT 1
            """,
            (set_hash,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"competition_evidence_current_observation_missing:{set_hash}")
    payload = loads(row["payload"])
    if not isinstance(payload, dict):
        raise RuntimeError(f"competition_evidence_current_observation_invalid:{set_hash}")
    if _text(row["observation_hash"]) != observation_hash:
        raise RuntimeError(f"competition_evidence_current_observation_hash_mismatch:{set_hash}")
    return payload


def _history_items_for_product(
    current_item: Dict[str, Any],
    history_indexes: List[Dict[str, Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    key = _object_key(current_item)
    product_id = _text(current_item.get("productId"))
    store_id = _text(current_item.get("storeId"))
    found: List[Dict[str, Any]] = []
    for index in history_indexes:
        item = index.get(key)
        if item is None:
            item = next(
                (
                    candidate
                    for candidate in index.values()
                    if _text(candidate.get("productId")) == product_id
                    and _text(candidate.get("storeId")) == store_id
                ),
                None,
            )
        if item is not None:
            found.append(item)
    return found


def materialize_signal_snapshot_v215_hash(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
) -> Dict[str, Any]:
    """Apply V21.5 Evidence semantics to the bounded hash-precache runtime."""
    del force
    from src.services import v215_report_batch_evidence_service as v215

    result = _HASH_PRECACHE_MATERIALIZER(
        data_version=data_version,
        user_id=user_id,
        force=False,
    )
    resolved = result.get("dataVersion") or data_version
    evidence_hash = _text(result.get("evidenceInputHash"))
    if not evidence_hash.startswith("sha256:"):
        raise RuntimeError("competition_evidence_input_hash_missing_before_v215_overlay")
    if result.get("evidenceCacheMode") != "competition_hash_precache":
        raise RuntimeError("competition_evidence_cache_mode_invalid_before_v215_overlay")
    if result.get("wholeSnapshotRetention") is not False:
        raise RuntimeError("competition_evidence_whole_snapshot_retention_forbidden")

    current = _current_compact_observation(result)
    previous_hashes = [
        _text(value)
        for value in (result.get("previousProductSetHashes") or [])
        if _text(value)
    ][:2]
    history = _compact_history_by_hashes(previous_hashes)
    current_index = _index_products(current)
    history_indexes = [_index_products(snapshot) for snapshot in history]
    packages = result.get("productSignalPackages") or result.get("signals") or []

    for package in packages:
        if not isinstance(package, dict):
            continue
        key = _text(package.get("entityId"))
        current_item = current_index.get(key)
        if current_item is None:
            product_id = _text(package.get("productId"))
            store_id = _text(package.get("storeId"))
            current_item = next(
                (
                    item
                    for item in current_index.values()
                    if _text(item.get("productId")) == product_id
                    and _text(item.get("storeId")) == store_id
                ),
                None,
            )
        if not current_item:
            continue
        history_items = _history_items_for_product(current_item, history_indexes)
        cross = v215.build_cross_validation(current_item, history_items)
        package["crossValidation"] = cross
        package["timeSeriesFeatures"] = cross["timeSeriesFeatures"]
        package["operatingHypotheses"] = cross["hypotheses"]
        package["operatingDecision"] = cross["decision"]
        package["evidenceInputHash"] = evidence_hash
        package["historyEpochId"] = result.get("historyEpochId")
        package["evidenceContract"] = "operatingEvidenceGraph.v1"
        package["evidenceVersion"] = v215.V215_VERSION
        package["signalStrength"] = (
            "high"
            if cross["decision"].get("status") == "confirmed"
            and int(cross["decision"].get("confidence") or 0) >= 75
            else "medium"
            if cross["decision"].get("status") == "confirmed"
            else "low"
            if cross["decision"].get("status") in {"conflict", "buffered"}
            else "normal"
        )
        agent_package = package.get("agentProductSnapshotPackage")
        if isinstance(agent_package, dict):
            agent_package["crossValidation"] = cross
            agent_package["timeSeriesFeatures"] = cross["timeSeriesFeatures"]
            agent_package["operatingDecision"] = cross["decision"]
            agent_package["evidenceInputHash"] = evidence_hash
            agent_package["historyEpochId"] = result.get("historyEpochId")
            agent_package["evidenceContract"] = "operatingEvidenceGraph.v1"
            agent_package["evidenceVersion"] = v215.V215_VERSION

    builder_version = result.get("version")
    result["version"] = v215.V215_VERSION
    result["evidenceBuilderVersion"] = builder_version
    result["businessDataVersion"] = resolved
    result["reportBatchId"] = v215._batch_for_data_version(connect, loads, resolved)
    result["baselineNoPrevious"] = bool(result.get("baselineNoPrevious"))
    result["evidenceContract"] = "operatingEvidenceGraph.v1"
    result["evidenceVersion"] = v215.V215_VERSION
    result["windowPolicy"] = {
        "recentDirectReports": min(3, len(history) + 1),
        "mediumTrendReports": 10,
        "longTrendReports": 30,
        "historyCandidateLimit": 8,
        "comparableHistoryLimit": 2,
        "actualComparableHistoryCount": len(history),
        "runtimeHistoryMode": "competition_hash_precache",
        "wholeSnapshotRetention": False,
        "timeBasis": "current_epoch_active_import_order_and_report_business_date",
        "rule": "10/30 are evidence window labels only; competition runtime uses at most two comparable compact historical observations.",
    }
    result["signals"] = packages
    result["productSignalPackages"] = packages
    result["v215CompatibilityBridge"] = BRIDGE_VERSION
    result["rule"] = (
        "V21.5 operatingEvidenceGraph semantics run over the exact competition evidenceInputHash "
        "and at most two compact comparable observations; full historical snapshot rescans are forbidden."
    )

    snapshot_id = result.get("signalSnapshotId") or signal_snapshot.signal_snapshot_id_for(resolved)
    with connect() as conn:
        conn.execute(
            """
            UPDATE product_signal_snapshots_v14
            SET payload=?,signal_count=?,updated_at=datetime('now')
            WHERE signal_snapshot_id=? AND evidence_input_hash=?
            """,
            (dumps(result), len(packages), snapshot_id, evidence_hash),
        )
        conn.commit()
    return result


def install_competition_evidence_v215_runtime() -> Dict[str, Any]:
    """Replace the legacy V21.5 90-snapshot wrapper with the bounded hash bridge."""
    signal_snapshot.materialize_product_signal_snapshot = materialize_signal_snapshot_v215_hash
    admission.materialize_product_signal_snapshot = materialize_signal_snapshot_v215_hash
    return {
        "version": BRIDGE_VERSION,
        "installed": True,
        "signalSnapshotOwner": "competition_evidence_hash_precache_plus_v21_5_cross_validation",
        "historyCandidateLimit": 8,
        "comparableHistoryLimit": 2,
        "wholeSnapshotRetention": False,
        "legacyNinetySnapshotScan": False,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": "21.5.0",
    }


__all__ = [
    "BRIDGE_VERSION",
    "materialize_signal_snapshot_v215_hash",
    "install_competition_evidence_v215_runtime",
]
