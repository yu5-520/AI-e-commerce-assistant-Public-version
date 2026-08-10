from __future__ import annotations

from copy import deepcopy

from src.services import product_snapshot_lineage_service as lineage


def _product(product_id: str = "P-1", sku_id: str = "SKU-1", snapshot_hash: str = "sha256:good"):
    registry_key = f"tmall::STORE-1::{product_id}::{sku_id}"
    return {
        "dataVersion": "DV-1",
        "objectId": registry_key,
        "productId": product_id,
        "storeId": "STORE-1",
        "productSnapshotHash": snapshot_hash,
        "snapshotHash": snapshot_hash,
        "profileSnapshot": {
            "objectId": registry_key,
            "productId": product_id,
            "storeId": "STORE-1",
            "storeName": "测试店铺",
            "platform": "tmall",
            "skuId": sku_id,
            "title": f"商品 {product_id}",
        },
        "metricSnapshot": {
            "paymentAmount": 100,
            "roi": 2.0,
            "roas": 2.0,
            "adSpend": 50,
            "inventory": 20,
        },
    }


def _snapshot_set(products=None):
    return {
        "dataVersion": "DV-1",
        "setSnapshotHash": "sha256:set-1",
        "products": products or [_product()],
    }


def _patch_snapshot(monkeypatch, snapshot_set):
    monkeypatch.setattr(lineage, "_target_snapshot", lambda data_version: snapshot_set)
    monkeypatch.setattr(lineage, "_snapshot_sets_for_exact_hash", lambda data_version: [snapshot_set])
    monkeypatch.setattr(
        lineage,
        "detail_projection",
        lambda product: {
            **deepcopy(product.get("profileSnapshot") or {}),
            **deepcopy(product.get("metricSnapshot") or {}),
            "dataVersion": product.get("dataVersion"),
            "productSnapshotHash": product.get("productSnapshotHash"),
            "snapshotHash": product.get("snapshotHash"),
            "objectId": product.get("objectId"),
            "productId": product.get("productId"),
            "storeId": product.get("storeId"),
        },
    )


def test_bound_hash_is_strict_and_never_falls_back_to_identity(monkeypatch):
    snapshot_set = _snapshot_set()
    _patch_snapshot(monkeypatch, snapshot_set)

    result = lineage.resolve_product_snapshot(
        product_snapshot_hash="sha256:missing",
        product_id="P-1",
        store_id="STORE-1",
        data_version="DV-1",
        allow_legacy_identity_migration=True,
    )

    assert result["ready"] is False
    assert result["status"] == "lineage_broken"
    assert result["reason"] == "bound_product_snapshot_hash_not_found"
    assert result["strictHash"] is True
    assert result["legacyIdentityMigration"] is False


def test_legacy_task_without_hash_migrates_once_to_canonical_identity(monkeypatch):
    snapshot_set = _snapshot_set()
    _patch_snapshot(monkeypatch, snapshot_set)

    result = lineage.resolve_product_snapshot(
        product_id="SKU-1",
        store_id="STORE-1",
        data_version="DV-1",
        allow_legacy_identity_migration=True,
    )

    assert result["ready"] is True
    assert result["matchMode"] == "legacy_identity_migration"
    assert result["productSnapshotHash"] == "sha256:good"
    assert result["productRegistryKey"] == "tmall::STORE-1::P-1::SKU-1"


def test_task_detail_binds_product_snapshot_and_recovers_only_frozen_sop(monkeypatch):
    snapshot_set = _snapshot_set()
    _patch_snapshot(monkeypatch, snapshot_set)
    task = {
        "taskId": "TASK-1",
        "dataVersion": "DV-1",
        "productIdentity": {"productId": "P-1", "storeId": "STORE-1", "skuId": "SKU-1"},
        "taskDetailReport": {
            "operatorExecutionSop": ["检查当前投放", "按授权范围调整", "记录回流结果"],
        },
    }

    result = lineage.bind_task_product_lineage(task)

    assert result["productSnapshotHash"] == "sha256:good"
    assert result["productRegistryKey"] == "tmall::STORE-1::P-1::SKU-1"
    assert result["productSnapshot"]["productId"] == "P-1"
    assert result["operatorExecutionSop"] == ["检查当前投放", "按授权范围调整", "记录回流结果"]
    assert result["productSnapshotLineage"]["legacyIdentityMigration"] is True


def test_product_view_reads_all_canonical_products_not_signal_subset(monkeypatch):
    snapshot_set = _snapshot_set(
        [
            _product("P-1", "SKU-1", "sha256:p1"),
            _product("P-2", "SKU-2", "sha256:p2"),
            _product("P-3", "SKU-3", "sha256:p3"),
        ]
    )
    _patch_snapshot(monkeypatch, snapshot_set)

    result = lineage.read_canonical_product_views(data_version="DV-1", limit=100)

    assert result["ready"] is True
    assert result["count"] == 3
    assert {item["productId"] for item in result["items"]} == {"P-1", "P-2", "P-3"}
    assert all(item["productSnapshotHash"] for item in result["items"])
