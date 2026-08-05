from __future__ import annotations

from src.services.product_trend_read_model_v217_service import (
    PRODUCT_TREND_READ_MODEL_VERSION,
    build_product_trend_projection,
)


def _product(date: str, *, payment: float | None = None, roi: float | None = None, inventory: int | None = None):
    metrics = {
        "metricDate": date,
        "sourceDataVersions": [f"DV-{date}"],
    }
    if payment is not None:
        metrics["paymentAmount"] = payment
    if roi is not None:
        metrics["roi"] = roi
    if inventory is not None:
        metrics["inventory"] = inventory
    return {
        "objectId": "TB::STORE-1::P-1::SKU-1",
        "productId": "P-1",
        "skuId": "SKU-1",
        "storeId": "STORE-1",
        "storeName": "测试店铺",
        "platform": "天猫",
        "title": "测试商品",
        "metricDate": date,
        "metricSnapshot": metrics,
    }


def _snapshot(index: int, date: str, products):
    return {
        "snapshotId": f"SNAP-{index}",
        "dataVersion": f"DV-{index}",
        "createdAt": f"{date}T08:00:00",
        "updatedAt": f"{date}T08:00:01",
        "products": products,
    }


def test_product_trend_uses_effective_product_observations_only():
    snapshots = [
        _snapshot(8, "2026-01-07", [_product("2026-01-07", payment=170, roi=3.1, inventory=70)]),
        _snapshot(7, "2026-01-06", [_product("2026-01-06", payment=160, roi=3.0, inventory=76)]),
        _snapshot(6, "2026-01-05", [_product("2026-01-05", payment=150)]),
        _snapshot(5, "2026-01-05", [_product("2026-01-05", roi=2.8, inventory=81)]),
        _snapshot(4, "2026-01-04", [_product("2026-01-04", payment=130, roi=2.6, inventory=90)]),
        _snapshot(3, "2026-01-03", [{"objectId": "OTHER", "productId": "OTHER", "storeId": "STORE-1", "metricSnapshot": {"paymentAmount": 999}}]),
        _snapshot(2, "2026-01-02", [_product("2026-01-02", payment=120, roi=2.4, inventory=96)]),
        _snapshot(1, "2026-01-01", [_product("2026-01-01", payment=100, roi=2.0, inventory=100)]),
    ]

    result = build_product_trend_projection(
        snapshots,
        "TB::STORE-1::P-1::SKU-1",
        store_id="STORE-1",
    )

    assert result["version"] == PRODUCT_TREND_READ_MODEL_VERSION
    assert result["ready"] is True
    assert result["observationSummary"]["validSnapshotCount"] == 6
    assert result["observationSummary"]["recentWindowSize"] == 5
    assert result["observationSummary"]["missingReportMeansZero"] is False

    dates = [item["businessDate"] for item in result["recentSnapshots"]]
    assert dates == ["2026-01-02", "2026-01-04", "2026-01-05", "2026-01-06", "2026-01-07"]
    assert "2026-01-03" not in dates

    merged = next(item for item in result["recentSnapshots"] if item["businessDate"] == "2026-01-05")
    assert merged["metrics"]["paymentAmount"] == 150
    assert merged["metrics"]["roi"] == 2.8
    assert merged["metrics"]["inventory"] == 81
    assert len(merged["sourceDataVersions"]) >= 2

    payment = result["metricTrends"]["paymentAmount"]
    assert payment["current"] == 170
    assert payment["previous"] == 160
    assert payment["previousDelta"] == 0.0625
    assert payment["sampleCount"] == 6
    assert payment["slope5"] is not None

    assert result["inventoryBoundary"] == "inventory_and_available_days_are_capacity_facts_not_operating_action_families"


def test_product_trend_does_not_emit_zero_for_missing_metric():
    snapshots = [
        _snapshot(2, "2026-02-02", [_product("2026-02-02", payment=200)]),
        _snapshot(1, "2026-02-01", [_product("2026-02-01", roi=2.5)]),
    ]

    result = build_product_trend_projection(snapshots, "P-1", store_id="STORE-1")
    first, second = result["recentSnapshots"]

    assert "paymentAmount" not in first["metrics"]
    assert "roi" not in second["metrics"]
    assert first["metrics"]["roi"] == 2.5
    assert second["metrics"]["paymentAmount"] == 200
