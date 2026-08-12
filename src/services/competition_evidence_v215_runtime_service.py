"""Competition-safe V21.5 operating-evidence bridge with semantic precache.

The original V21.5 competition bridge remains the execution order authority: first the
registered V18.7 bounded Evidence materializer produces the current exact Evidence
identity, then this bridge applies V21.5 cross-validation. A parallel semantic cache is
consulted only after the exact current Evidence boundary exists, so cache support can
never replace or reorder the Station progression that already passed the autonomous
worker gate.

Strict lineage remains:
canonical hashes -> evidenceInputHash -> bundle ART -> validated ART -> signal ART.
Semantic cache hits reuse only a business body; current dataVersion, strict Evidence
identity, product snapshot hashes and signal/package identities are rebound before the
normal downstream quality/admission path creates current Artifacts.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services import product_signal_snapshot_service as signal_snapshot
from src.services import product_signal_admission_v197_service as admission
from src.services.competition_hash_precache_registry_v1_service import (
    HASH_PRECACHE_VERSION,
    build_pre_agent_hashes,
    compute_contract_hash,
    lookup_pre_agent_cache,
    semantic_hash,
    store_pre_agent_cache,
)

# Keep the already-registered bridge identity stable. Semantic precache is an additive
# capability, not a replacement runtime owner.
BRIDGE_VERSION = "competitionEvidence.v21_5_hash_bridge.v1"
SEMANTIC_PRECACHE_VERSION = "competitionEvidence.semantic_precache.v1"

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
        "semanticPrecacheVersion": SEMANTIC_PRECACHE_VERSION,
        "canonicalProductSnapshotSchemaVersion": "canonicalProductSnapshot.v1",
        "evidenceInputContract": signal_snapshot.EVIDENCE_INPUT_CONTRACT,
        "evidenceBuilderVersion": signal_snapshot.PRODUCT_SIGNAL_SNAPSHOT_VERSION,
        "evidenceContract": signal_snapshot.EVIDENCE_CONTRACT,
        "evidenceContractVersion": v215_version,
        "admissionPolicyVersion": "artifact_signal_admission_v225:22.5.12",
        "comparableHistoryLimit": signal_snapshot.MAX_COMPETITION_COMPARABLE_HISTORY,
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
    indexed = _index_products(current)
    key = _text(package.get("entityId"))
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


def _exact_identity(result: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "evidenceInputHash",
        "historyEpochId",
        "currentProductSetHash",
        "currentObservationHash",
        "previousProductSetHashes",
        "previousObservationHashes",
        "evidenceCacheMode",
        "historyScanMode",
        "wholeSnapshotRetention",
    )
    return {key: result.get(key) for key in keys if result.get(key) is not None}


def _rebind_cached_package(
    cached: Dict[str, Any],
    *,
    data_version: str | None,
    current: Dict[str, Any],
    history: List[Dict[str, Any]],
    exact_identity: Dict[str, Any],
    contract: Dict[str, Any],
) -> Dict[str, Any]:
    package = deepcopy(cached)
    product = _current_product(current, package)
    package_semantic_hash = semantic_hash(
        cached,
        namespace="v215_product_signal_business_body",
        contract=contract,
    )
    product_id = _text(package.get("productId"))
    store_id = _text(package.get("storeId")) or "GLOBAL"
    identity_seed = (
        f"semantic_rebind|{data_version or 'latest'}|{store_id}|"
        f"{product_id}|{package_semantic_hash}"
    )
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
    package["semanticPrecacheVersion"] = SEMANTIC_PRECACHE_VERSION

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
        agent_package["evidenceContract"] = "operatingEvidenceGraph.v1"
        agent_package["evidenceVersion"] = contract.get("evidenceContractVersion")
        agent_metric = agent_package.get("metricLayer")
        if isinstance(agent_metric, dict):
            agent_metric["sourceDataVersions"] = [_text(data_version)] if _text(data_version) else []
        agent_cross = agent_package.get("crossValidation")
        if isinstance(agent_cross, dict):
            agent_cross["sourceDataVersions"] = versions
            agent_cross["sourceVersionCount"] = len(versions)
    return package


def _persist_result(result: Dict[str, Any], packages: List[Dict[str, Any]]) -> None:
    snapshot_id = result.get("signalSnapshotId") or signal_snapshot.signal_snapshot_id_for(
        result.get("dataVersion")
    )
    evidence_hash = _text(result.get("evidenceInputHash"))
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


def _restore_cached_result(
    base_result: Dict[str, Any],
    *,
    cached_body: Dict[str, Any],
    cache_ref: str,
    current: Dict[str, Any],
    history: List[Dict[str, Any]],
    hashes: Dict[str, Any],
    contract: Dict[str, Any],
    contract_hash: str,
    v215: Any,
) -> Dict[str, Any]:
    exact = _exact_identity(base_result)
    cached_signals = cached_body.get("signals") or cached_body.get("productSignalPackages") or []
    packages = [
        _rebind_cached_package(
            item,
            data_version=base_result.get("dataVersion"),
            current=current,
            history=history,
            exact_identity=exact,
            contract=contract,
        )
        for item in cached_signals
        if isinstance(item, dict)
    ]
    result = dict(base_result)
    builder_version = result.get("version")
    result["version"] = v215.V215_VERSION
    result["evidenceBuilderVersion"] = builder_version
    result["businessDataVersion"] = result.get("dataVersion")
    result["reportBatchId"] = v215._batch_for_data_version(
        connect,
        loads,
        result.get("dataVersion"),
    )
    result["evidenceContract"] = "operatingEvidenceGraph.v1"
    result["evidenceVersion"] = v215.V215_VERSION
    result["signals"] = packages
    result["productSignalPackages"] = packages
    result["productSignalPackageCount"] = len(packages)
    result["productSignalCount"] = len(packages)
    result["v215CompatibilityBridge"] = BRIDGE_VERSION
    result["semanticPrecacheVersion"] = SEMANTIC_PRECACHE_VERSION
    result.update(hashes)
    result["computeContractHash"] = contract_hash
    result["semanticCacheHit"] = True
    result["semanticCacheSourceRef"] = cache_ref
    result["semanticCacheBusinessBodyOnly"] = True
    result["currentArtifactRebindRequired"] = True
    result["preAgentCacheStatus"] = "hit"
    result["windowPolicy"] = {
        "recentDirectReports": min(3, len(history) + 1),
        "mediumTrendReports": 10,
        "longTrendReports": 30,
        "historyCandidateLimit": 8,
        "comparableHistoryLimit": 2,
        "actualComparableHistoryCount": len(history),
        "runtimeHistoryMode": "competition_semantic_hash_precache_hit",
        "wholeSnapshotRetention": False,
        "timeBasis": "current_epoch_active_import_order_and_report_business_date",
        "rule": (
            "Semantic cache reused only V21.5 business Evidence; current exact Evidence "
            "identity was first materialized by the registered baseline runtime."
        ),
    }
    result["rule"] = (
        "V21.5 business Evidence body came from the registered semantic cache; current "
        "strict evidenceInputHash/dataVersion/product hashes and signal identities were rebound."
    )
    _persist_result(result, packages)
    return result


def _cache_business_body(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema": "competition.v215_evidence_business_body.v1",
        "signals": result.get("productSignalPackages") or result.get("signals") or [],
        "baselineNoPrevious": bool(result.get("baselineNoPrevious")),
        "evidenceContract": result.get("evidenceContract"),
        "evidenceVersion": result.get("evidenceVersion"),
        "rule": (
            "Business-only body. Runtime/import/strict identities are removed again by "
            "competition_hash_precache_registry_v1 before Artifact storage."
        ),
    }


def materialize_signal_snapshot_v215_hash(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
) -> Dict[str, Any]:
    """Preserve baseline progression, then apply V21.5 semantic cache/cross-validation."""
    del force
    from src.services import v215_report_batch_evidence_service as v215

    # Keep this call first. The autonomous production chain already proves this
    # registered materializer advances Station state correctly.
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
    contract = _compute_contract(v215.V215_VERSION)
    hashes = build_pre_agent_hashes(current, history, contract=contract)
    contract_hash = compute_contract_hash(contract)

    cache = lookup_pre_agent_cache(hashes, contract_hash=contract_hash)
    if cache.get("hit") is True:
        body = cache.get("body") if isinstance(cache.get("body"), dict) else {}
        cached_business = body.get("businessBody") if isinstance(body.get("businessBody"), dict) else {}
        return _restore_cached_result(
            result,
            cached_body=cached_business,
            cache_ref=str(cache.get("artifactRef") or ""),
            current=current,
            history=history,
            hashes=hashes,
            contract=contract,
            contract_hash=contract_hash,
            v215=v215,
        )

    # Cache miss: original bounded V21.5 bridge logic.
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
        "rule": (
            "10/30 are evidence window labels only; competition runtime uses at most "
            "two comparable compact historical observations."
        ),
    }
    result["signals"] = packages
    result["productSignalPackages"] = packages
    result["v215CompatibilityBridge"] = BRIDGE_VERSION
    result["semanticPrecacheVersion"] = SEMANTIC_PRECACHE_VERSION
    result.update(hashes)
    result["computeContractHash"] = contract_hash
    result["semanticCacheHit"] = False
    result["preAgentCacheStatus"] = "miss_stored"
    result["rule"] = (
        "V21.5 operatingEvidenceGraph semantics run over the exact competition "
        "evidenceInputHash; the deterministic business body is additionally indexed "
        "by a cross-run semantic hash."
    )

    _persist_result(result, packages)
    cache_receipt = store_pre_agent_cache(
        hashes,
        contract_hash=contract_hash,
        business_body=_cache_business_body(result),
        metadata={
            "sourceDataVersion": resolved,
            "evidenceInputHash": evidence_hash,
            "bridgeVersion": BRIDGE_VERSION,
            "semanticPrecacheVersion": SEMANTIC_PRECACHE_VERSION,
            "routing": (
                "baseline_exact_evidence_first_then_semantic_hit_or_v215_compute_then_normal_admission"
            ),
        },
    )
    result["semanticCacheArtifactRef"] = cache_receipt.get("artifactRef")
    return result


def install_competition_evidence_v215_runtime() -> Dict[str, Any]:
    """Install bounded V21.5 Evidence plus additive semantic precache."""
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
        "semanticPrecache": True,
        "semanticPrecacheVersion": SEMANTIC_PRECACHE_VERSION,
        "semanticHashRegistryVersion": HASH_PRECACHE_VERSION,
        "strictEvidenceInputHashChanged": False,
        "currentSignalArtifactRebindRequired": True,
        "stationProgressionOrderChanged": False,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": "21.5.0",
    }


__all__ = [
    "BRIDGE_VERSION",
    "SEMANTIC_PRECACHE_VERSION",
    "materialize_signal_snapshot_v215_hash",
    "install_competition_evidence_v215_runtime",
]
