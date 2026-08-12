"""Competition-safe V21.5 operating-evidence bridge with unified semantic precache.

The strict current-run Evidence identity remains unchanged:
canonical hashes -> evidenceInputHash -> bundle ART -> validated ART -> signal ART.

A parallel semantic identity is registered only for deterministic compute reuse.  It
excludes runtime-only identities such as dataVersion/snapshotId/historyEpoch/Artifact
refs and binds the business projection plus compute-contract versions.  A hit restores
only the business Evidence body, then rebuilds the current exact Evidence identity and
current signal/package identities before downstream quality/admission creates current
Artifacts.  Old current-run refs are never reused.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services import product_signal_snapshot_service as signal_snapshot
from src.services import product_signal_admission_v197_service as admission
from src.services.canonical_product_trend_v2_service import current_competition_history_epoch
from src.services.competition_hash_precache_registry_v1_service import (
    HASH_PRECACHE_VERSION,
    build_pre_agent_hashes,
    compute_contract_hash,
    lookup_pre_agent_cache,
    semantic_hash,
    store_pre_agent_cache,
)
from src.services.system_product_snapshot_service import (
    get_product_snapshot,
    materialize_system_product_snapshot,
)

BRIDGE_VERSION = "competitionEvidence.v21_5_hash_bridge.v2-semantic-precache"

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


def _compute_contract(v215_version: str) -> Dict[str, Any]:
    return {
        "hashPrecacheRegistryVersion": HASH_PRECACHE_VERSION,
        "canonicalProductSnapshotSchemaVersion": "canonicalProductSnapshot.v1",
        "evidenceInputContract": signal_snapshot.EVIDENCE_INPUT_CONTRACT,
        "evidenceBuilderVersion": signal_snapshot.PRODUCT_SIGNAL_SNAPSHOT_VERSION,
        "evidenceContract": signal_snapshot.EVIDENCE_CONTRACT,
        "evidenceContractVersion": v215_version,
        # Admission itself still runs on every current execution so it can create new
        # current signal Artifacts.  Its policy version is nevertheless in the cache
        # contract so a policy change invalidates the pre-Agent semantic result.
        "admissionPolicyVersion": "artifact_signal_admission_v225:22.5.12",
        "comparableHistoryLimit": signal_snapshot.MAX_COMPETITION_COMPARABLE_HISTORY,
    }


def _prepare_current_identity(
    data_version: str | None,
    *,
    user_id: str | None,
    contract: Dict[str, Any],
) -> Dict[str, Any]:
    current = get_product_snapshot(data_version)
    if not current:
        materialize_system_product_snapshot(
            data_version=data_version,
            user_id=user_id,
            force=False,
        )
        current = get_product_snapshot(data_version) or {}
    if not current or not current.get("snapshotId"):
        raise RuntimeError(
            f"canonical_product_snapshot_missing_before_semantic_precache:{data_version or 'latest'}"
        )
    epoch = current_competition_history_epoch()
    current_observation = signal_snapshot._store_observation(current)
    history, baseline, previous_identities = signal_snapshot._history_for_evidence(
        current,
        data_version,
        epoch,
    )
    exact_identity = signal_snapshot._evidence_identity(
        current_observation,
        previous_identities,
        epoch,
    )
    hashes = build_pre_agent_hashes(
        current_observation,
        history,
        contract=contract,
    )
    return {
        "current": current,
        "currentObservation": current_observation,
        "history": history,
        "baseline": baseline,
        "exactIdentity": exact_identity,
        "hashes": hashes,
        "contractHash": compute_contract_hash(contract),
    }


def _source_versions(data_version: str | None, history: List[Dict[str, Any]]) -> List[str]:
    values = [
        _text(item.get("dataVersion"))
        for item in reversed(history)
        if _text(item.get("dataVersion"))
    ]
    if _text(data_version):
        values.append(_text(data_version))
    return list(dict.fromkeys(values))


def _current_product(
    current: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any] | None:
    key = _text(package.get("entityId"))
    indexed = _index_products(current)
    if key and key in indexed:
        return indexed[key]
    product_id = _text(package.get("productId"))
    store_id = _text(package.get("storeId"))
    return next(
        (
            item
            for item in indexed.values()
            if _text(item.get("productId")) == product_id
            and _text(item.get("storeId")) == store_id
        ),
        None,
    )


def _rebind_cached_package(
    cached: Dict[str, Any],
    *,
    data_version: str | None,
    current: Dict[str, Any],
    history: List[Dict[str, Any]],
    exact_identity: Dict[str, Any],
) -> Dict[str, Any]:
    package = deepcopy(cached)
    product = _current_product(current, package)
    package_semantic_hash = semantic_hash(
        cached,
        namespace="product_signal_business_body",
        contract={"version": BRIDGE_VERSION},
    )
    product_id = _text(package.get("productId"))
    store_id = _text(package.get("storeId")) or "GLOBAL"
    identity_seed = f"semantic_rebind|{data_version or 'latest'}|{store_id}|{product_id}|{package_semantic_hash}"
    bundle_id = signal_snapshot._bundle_id(identity_seed)
    signal_id = signal_snapshot._signal_id(identity_seed)
    package_id = bundle_id.replace("FPB-", "PKG-")
    versions = _source_versions(data_version, history)

    package["dataVersion"] = data_version
    package["signalId"] = signal_id
    package["bundleId"] = bundle_id
    package["packageId"] = package_id
    package.update(exact_identity)
    package["semanticCacheRebound"] = True
    package["cachedBusinessSemanticHash"] = package_semantic_hash
    package["hashPrecacheVersion"] = HASH_PRECACHE_VERSION
    if product:
        product_hash = product.get("productSnapshotHash") or product.get("snapshotHash")
        package["productSnapshotHash"] = product_hash
        package["parentSnapshotHash"] = product_hash

    metric = package.get("metricLayer")
    if isinstance(metric, dict):
        metric["sourceDataVersions"] = [_text(data_version)] if _text(data_version) else []
    cross = package.get("crossValidation")
    if isinstance(cross, dict):
        cross["sourceDataVersions"] = versions
        cross["sourceVersionCount"] = len(versions)
    lineage = package.get("sourceLineageValidation")
    if isinstance(lineage, dict):
        lineage["dataVersions"] = versions

    agent_package = package.get("agentProductSnapshotPackage")
    if isinstance(agent_package, dict):
        agent_package["bundleId"] = bundle_id
        agent_package["evidenceInputHash"] = exact_identity.get("evidenceInputHash")
        agent_package["historyEpochId"] = exact_identity.get("historyEpochId")
        agent_package["evidenceContract"] = exact_identity.get("evidenceContract")
        agent_package["evidenceVersion"] = exact_identity.get("evidenceVersion")
        agent_metric = agent_package.get("metricLayer")
        if isinstance(agent_metric, dict):
            agent_metric["sourceDataVersions"] = [_text(data_version)] if _text(data_version) else []
        agent_cross = agent_package.get("crossValidation")
        if isinstance(agent_cross, dict):
            agent_cross["sourceDataVersions"] = versions
            agent_cross["sourceVersionCount"] = len(versions)
    return package


def _persist_restored_snapshot(
    *,
    data_version: str | None,
    prepared: Dict[str, Any],
    cached_body: Dict[str, Any],
    cache_ref: str,
    v215: Any,
) -> Dict[str, Any]:
    current = prepared["current"]
    history = prepared["history"]
    baseline = prepared["baseline"]
    exact_identity = prepared["exactIdentity"]
    hashes = prepared["hashes"]
    cached_signals = cached_body.get("signals") or cached_body.get("productSignalPackages") or []
    packages = [
        _rebind_cached_package(
            item,
            data_version=data_version,
            current=current,
            history=history,
            exact_identity=exact_identity,
        )
        for item in cached_signals
        if isinstance(item, dict)
    ]
    previous = history[0] if history else None
    snapshot_id = signal_snapshot.signal_snapshot_id_for(data_version)
    result = {
        "version": v215.V215_VERSION,
        "evidenceBuilderVersion": signal_snapshot.PRODUCT_SIGNAL_SNAPSHOT_VERSION,
        "signalSnapshotId": snapshot_id,
        "dataVersion": data_version,
        "businessDataVersion": data_version,
        "stationId": "product_signal_snapshot_station",
        "contract": "fullProductBundle",
        "evidenceInputContract": signal_snapshot.EVIDENCE_INPUT_CONTRACT,
        **exact_identity,
        **hashes,
        "productSnapshotId": current.get("snapshotId"),
        "previousSnapshotId": previous.get("snapshotId") if previous else None,
        "previousDataVersion": previous.get("dataVersion") if previous else None,
        "baselineNoPrevious": bool(baseline.get("baselineNoPrevious")),
        "baseline": baseline,
        "productSnapshotCount": current.get("productCount") or len(current.get("products") or []),
        "productSignalPackageCount": len(packages),
        "productSignalCount": len(packages),
        "signals": packages,
        "productSignalPackages": packages,
        "reportBatchId": v215._batch_for_data_version(connect, loads, data_version),
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": v215.V215_VERSION,
        "windowPolicy": {
            "recentDirectReports": min(3, len(history) + 1),
            "mediumTrendReports": 10,
            "longTrendReports": 30,
            "historyCandidateLimit": 8,
            "comparableHistoryLimit": 2,
            "actualComparableHistoryCount": len(history),
            "runtimeHistoryMode": "competition_semantic_hash_precache_hit",
            "wholeSnapshotRetention": False,
            "timeBasis": "current_epoch_active_import_order_and_report_business_date",
            "rule": "Semantic cache hit restores only business Evidence; current strict identity is rebound before downstream admission.",
        },
        "semanticCacheHit": True,
        "semanticCacheSourceRef": cache_ref,
        "semanticCacheBusinessBodyOnly": True,
        "currentArtifactRebindRequired": True,
        "v215CompatibilityBridge": BRIDGE_VERSION,
        "rule": (
            "Cross-run semantic precache reused the deterministic business Evidence body; "
            "current evidenceInputHash/dataVersion/signal identities were regenerated and current Signal ART is still created by admission."
        ),
    }
    now = signal_snapshot.now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO product_signal_snapshots_v14
            (signal_snapshot_id,data_version,product_snapshot_id,previous_snapshot_id,signal_count,payload,created_at,updated_at,evidence_input_hash,history_epoch_id,current_snapshot_hash,previous_snapshot_hashes,cache_mode)
            VALUES (?,?,?,?,?,?,COALESCE((SELECT created_at FROM product_signal_snapshots_v14 WHERE signal_snapshot_id=?),?),?,?,?,?,?,?)
            """,
            (
                snapshot_id,
                data_version,
                result["productSnapshotId"],
                result["previousSnapshotId"],
                len(packages),
                dumps(result),
                snapshot_id,
                now,
                now,
                exact_identity["evidenceInputHash"],
                exact_identity["historyEpochId"],
                exact_identity["currentProductSetHash"],
                dumps(exact_identity["previousProductSetHashes"]),
                "competition_semantic_hash_precache_hit",
            ),
        )
        conn.commit()
    return result


def _cache_business_body(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema": "competition.v215_evidence_business_body.v1",
        "signals": result.get("productSignalPackages") or result.get("signals") or [],
        "baselineNoPrevious": bool(result.get("baselineNoPrevious")),
        "evidenceContract": result.get("evidenceContract"),
        "evidenceVersion": result.get("evidenceVersion"),
        "rule": "Business-only body; runtime identities are removed by the unified semantic cache registry before storage.",
    }


def materialize_signal_snapshot_v215_hash(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
) -> Dict[str, Any]:
    """Apply V21.5 Evidence semantics with cross-run semantic precache first."""
    del force
    from src.services import v215_report_batch_evidence_service as v215

    contract = _compute_contract(v215.V215_VERSION)
    prepared = _prepare_current_identity(
        data_version,
        user_id=user_id,
        contract=contract,
    )
    cache = lookup_pre_agent_cache(
        prepared["hashes"],
        contract_hash=prepared["contractHash"],
    )
    if cache.get("hit") is True:
        body = cache.get("body") if isinstance(cache.get("body"), dict) else {}
        business_body = body.get("businessBody") if isinstance(body.get("businessBody"), dict) else {}
        restored = _persist_restored_snapshot(
            data_version=data_version,
            prepared=prepared,
            cached_body=business_body,
            cache_ref=str(cache.get("artifactRef") or ""),
            v215=v215,
        )
        restored["computeContractHash"] = prepared["contractHash"]
        restored["preAgentCacheStatus"] = "hit"
        return restored

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
    result.update(prepared["hashes"])
    result["computeContractHash"] = prepared["contractHash"]
    result["semanticCacheHit"] = False
    result["preAgentCacheStatus"] = "miss_stored"
    result["rule"] = (
        "V21.5 operatingEvidenceGraph semantics run over the exact competition evidenceInputHash; "
        "the resulting deterministic business body is additionally indexed by a cross-run semantic hash."
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

    cache_receipt = store_pre_agent_cache(
        prepared["hashes"],
        contract_hash=prepared["contractHash"],
        business_body=_cache_business_body(result),
        metadata={
            "sourceDataVersion": resolved,
            "evidenceInputHash": evidence_hash,
            "bridgeVersion": BRIDGE_VERSION,
            "routing": "restore_business_body_then_current_identity_rebind_then_normal_admission",
        },
    )
    result["semanticCacheArtifactRef"] = cache_receipt.get("artifactRef")
    return result


def install_competition_evidence_v215_runtime() -> Dict[str, Any]:
    """Install bounded Evidence + unified semantic-hash precache in the existing owner."""
    signal_snapshot.materialize_product_signal_snapshot = materialize_signal_snapshot_v215_hash
    admission.materialize_product_signal_snapshot = materialize_signal_snapshot_v215_hash
    return {
        "version": BRIDGE_VERSION,
        "installed": True,
        "signalSnapshotOwner": "competition_evidence_semantic_hash_precache_plus_exact_lineage_rebind",
        "historyCandidateLimit": 8,
        "comparableHistoryLimit": 2,
        "wholeSnapshotRetention": False,
        "legacyNinetySnapshotScan": False,
        "semanticPrecache": True,
        "semanticPrecacheVersion": HASH_PRECACHE_VERSION,
        "strictEvidenceInputHashChanged": False,
        "currentSignalArtifactRebindRequired": True,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": "21.5.0",
    }


__all__ = [
    "BRIDGE_VERSION",
    "materialize_signal_snapshot_v215_hash",
    "install_competition_evidence_v215_runtime",
]
