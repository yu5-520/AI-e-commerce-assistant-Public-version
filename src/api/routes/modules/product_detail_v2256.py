"""V22.5.6 composite product-detail read route.

The product list stays lightweight. Product detail is now a content-addressed read:
- current canonical productSnapshotHash identifies the product fact version;
- canonical history set hashes identify the trend-history version;
- the composite detail is stored as one immutable Artifact;
- repeated page opens resolve that Artifact instead of rescanning history.

A cache miss uses the memory-bounded canonical trend bridge, which scans one snapshot
row at a time and retains only the requested product.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from src.repositories.sqlite_repository import connect
from src.services.artifact_transport_service import resolve_artifact, store_artifact, validate_artifact
from src.services.canonical_product_trend_v2_service import read_canonical_product_trend
from src.services.competition_operator_context_service import user_id_from_headers

router = APIRouter()
PRODUCT_DETAIL_COMPOSITE_VERSION = "22.5.6-hash-cache-v1"
PRODUCT_DETAIL_ARTIFACT_TYPE = "frontend_product_detail.hash.v1"
PRODUCT_DETAIL_CACHE_TABLE = "frontend_product_detail_hash_cache_v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _aliases(item: Dict[str, Any]) -> set[str]:
    position = item.get("productPosition") if isinstance(item.get("productPosition"), dict) else {}
    values = {
        item.get("id"),
        item.get("objectId"),
        item.get("archiveId"),
        item.get("productId"),
        item.get("rawProductId"),
        item.get("skuId"),
        position.get("productId"),
        position.get("skuId"),
    }
    return {_text(value) for value in values if _text(value)}


def _ensure_cache_table() -> None:
    with connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PRODUCT_DETAIL_CACHE_TABLE} (
                cache_key TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                store_id TEXT,
                product_snapshot_hash TEXT,
                history_identity_hash TEXT NOT NULL,
                artifact_ref TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                data_version TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_product_detail_hash_cache_lookup_v1 "
            f"ON {PRODUCT_DETAIL_CACHE_TABLE}(product_id,store_id,updated_at)"
        )
        conn.commit()


def _history_identity() -> str:
    """Hash canonical history metadata only; never deserialize snapshot payloads."""
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_product_snapshot_sets_v1' LIMIT 1"
        ).fetchone()
        if not exists:
            return "sha256:" + hashlib.sha256(b"no-canonical-history").hexdigest()
        rows = conn.execute(
            """
            SELECT snapshot_id,set_snapshot_hash,updated_at
            FROM canonical_product_snapshot_sets_v1
            ORDER BY created_at DESC
            LIMIT 120
            """
        ).fetchall()
    material = "|".join(
        f"{row['snapshot_id']}:{row['set_snapshot_hash']}:{row['updated_at']}"
        for row in rows
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _product_snapshot_hash(item: Dict[str, Any]) -> str:
    lineage = item.get("canonicalLineage") if isinstance(item.get("canonicalLineage"), dict) else {}
    return _text(
        item.get("productSnapshotHash")
        or item.get("snapshotHash")
        or lineage.get("productSnapshotHash")
        or item.get("parentSnapshotHash")
        or item.get("detailProjectionHash")
    )


def _cache_key(
    *,
    product_id: str,
    store_id: str | None,
    product_snapshot_hash: str,
    history_identity_hash: str,
    user_id: str | None,
) -> str:
    material = "|".join(
        [
            PRODUCT_DETAIL_COMPOSITE_VERSION,
            _text(user_id) or "competition_operator",
            _text(store_id) or "GLOBAL",
            _text(product_id),
            product_snapshot_hash or "NO_PRODUCT_HASH",
            history_identity_hash,
        ]
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _read_cached(cache_key: str) -> Dict[str, Any] | None:
    _ensure_cache_table()
    with connect() as conn:
        row = conn.execute(
            f"SELECT artifact_ref,content_hash FROM {PRODUCT_DETAIL_CACHE_TABLE} WHERE cache_key=? LIMIT 1",
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    artifact_ref = _text(row["artifact_ref"])
    if not artifact_ref.startswith("ART-"):
        return None
    validation = validate_artifact(artifact_ref)
    if validation.get("ok") is not True:
        return None
    value = resolve_artifact(artifact_ref)
    if not isinstance(value, dict) or value.get("cacheKey") != cache_key:
        return None
    return {
        **value,
        "cacheState": "artifact_hit",
        "detailArtifactRef": artifact_ref,
        "detailContentHash": validation.get("contentHash") or row["content_hash"],
    }


def _store_cached(
    *,
    cache_key: str,
    payload: Dict[str, Any],
    product_id: str,
    store_id: str | None,
    product_snapshot_hash: str,
    history_identity_hash: str,
    data_version: str | None,
    user_id: str | None,
) -> Dict[str, Any]:
    artifact_value = {
        **payload,
        "cacheKey": cache_key,
        "productSnapshotHash": product_snapshot_hash,
        "historyIdentityHash": history_identity_hash,
        "immutableProductDetail": True,
    }
    artifact = store_artifact(
        artifact_type=PRODUCT_DETAIL_ARTIFACT_TYPE,
        value=artifact_value,
        schema_version=PRODUCT_DETAIL_COMPOSITE_VERSION,
        tenant_id=user_id or "competition_operator",
        store_id=store_id,
        product_id=product_id,
        data_version=data_version,
        created_by="product_detail_v2256_hash_cache",
        metadata={
            "cacheKey": cache_key,
            "productSnapshotHash": product_snapshot_hash,
            "historyIdentityHash": history_identity_hash,
            "readMode": "content_addressed_product_detail",
        },
    )
    artifact_ref = _text(artifact.get("artifactId"))
    content_hash = _text(artifact.get("contentHash"))
    now = datetime.now().isoformat()
    _ensure_cache_table()
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO {PRODUCT_DETAIL_CACHE_TABLE} (
                cache_key,product_id,store_id,product_snapshot_hash,
                history_identity_hash,artifact_ref,content_hash,data_version,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cache_key) DO UPDATE SET
                artifact_ref=excluded.artifact_ref,
                content_hash=excluded.content_hash,
                updated_at=excluded.updated_at
            """,
            (
                cache_key,
                product_id,
                store_id,
                product_snapshot_hash,
                history_identity_hash,
                artifact_ref,
                content_hash,
                data_version,
                now,
            ),
        )
        conn.commit()
    return {
        **artifact_value,
        "cacheState": "artifact_created",
        "detailArtifactRef": artifact_ref,
        "detailContentHash": content_hash,
    }


@router.get("/product-detail-v2256/{product_id}")
def product_detail_v2256(
    request: Request,
    product_id: str,
    store_id: str | None = Query(default=None, alias="storeId"),
) -> Dict[str, Any]:
    # Delayed import avoids creating a second product router authority.
    from src.api.routes.modules.product import product_items

    user_id = user_id_from_headers(request.headers)
    wanted = _text(product_id)
    candidates = product_items(user_id, store_id=store_id)
    matched = next(
        (
            item
            for item in candidates
            if wanted in _aliases(item)
            and (not store_id or _text(item.get("storeId")) == _text(store_id))
        ),
        None,
    )
    if not matched:
        raise HTTPException(status_code=404, detail="product not found in composite product detail projection")

    raw_product_id = _text(matched.get("productId") or matched.get("rawProductId") or wanted)
    resolved_store_id = _text(matched.get("storeId") or store_id) or None
    trend_lookup_id = _text(matched.get("objectId") or matched.get("id") or raw_product_id)
    product_hash = _product_snapshot_hash(matched)
    history_hash = _history_identity()
    cache_key = _cache_key(
        product_id=trend_lookup_id,
        store_id=resolved_store_id,
        product_snapshot_hash=product_hash,
        history_identity_hash=history_hash,
        user_id=user_id,
    )

    cached = _read_cached(cache_key)
    if cached is not None:
        return cached

    trend = read_canonical_product_trend(
        trend_lookup_id,
        store_id=resolved_store_id,
        user_id=user_id,
    )
    latest_snapshot = (trend.get("recentSnapshots") or [])[-1] if trend.get("recentSnapshots") else None
    payload = {
        "version": PRODUCT_DETAIL_COMPOSITE_VERSION,
        "ready": True,
        "item": matched,
        "trend": trend,
        "latestSnapshot": latest_snapshot,
        "dataCompleteness": trend.get("observationSummary") or {},
        "sourceLineage": {
            "productProjection": "canonical_product_snapshot_service.list_product_details",
            "productArchive": "modules.product.product_items",
            "recentTrend": "canonical_product_trend_v2_service",
            "trendAlgorithm": "product_trend_read_model_v217_service",
            "snapshotAuthority": "canonical_product_snapshot_sets_v1",
            "detailCache": PRODUCT_DETAIL_ARTIFACT_TYPE,
            "compactListReadModelUsedAsDetail": False,
        },
        "readRule": "Product detail resolves one immutable content-addressed Artifact. History is scanned only on cache miss and never retains whole snapshot sets.",
    }
    return _store_cached(
        cache_key=cache_key,
        payload=payload,
        product_id=raw_product_id,
        store_id=resolved_store_id,
        product_snapshot_hash=product_hash,
        history_identity_hash=history_hash,
        data_version=_text(matched.get("dataVersion")) or None,
        user_id=user_id,
    )
