from src.services import canonical_product_trend_v2_service as bridge


def _product(object_id, store_id, product_id, sku_id, date, payment, roi, conversion):
    return {
        "objectId": object_id,
        "storeId": store_id,
        "productId": product_id,
        "skuId": sku_id,
        "profileSnapshot": {
            "objectId": object_id,
            "storeId": store_id,
            "productId": product_id,
            "skuId": sku_id,
            "title": "通勤防泼水背包",
            "platform": "天猫",
        },
        "metricSnapshot": {
            "metricDate": date,
            "paymentAmount": payment,
            "roi": roi,
            "conversionRate": conversion,
            "refundRate": 0.03,
            "sourceDataVersions": [],
        },
    }


def test_canonical_history_returns_same_operating_unit_across_data_versions(monkeypatch):
    object_id = "product::tianmao::TB-SH-001::P10004::SKU10004-A"
    sibling_id = "product::jingdong::JD-SH-002::P10004::SKU10004-B"
    versions = [
        ("DV-3", "2026-07-02", 4116.42, 3.35, 0.0427),
        ("DV-2", "2026-06-28", 3929.31, 3.35, 0.0426),
        ("DV-1", "2026-06-25", 3555.09, 3.35, 0.0438),
    ]
    snapshots = {}
    for version, date, payment, roi, conversion in versions:
        snapshots[version] = {
            "snapshotId": f"SNAP-{version}",
            "dataVersion": version,
            "createdAt": f"{date}T23:10:00",
            "updatedAt": f"{date}T23:10:00",
            "products": [
                _product(object_id, "TB-SH-001", "P10004", "SKU10004-A", date, payment, roi, conversion),
                _product(sibling_id, "JD-SH-002", "P10004", "SKU10004-B", date, payment * 2, roi + 1, conversion + 0.01),
            ],
        }

    monkeypatch.setattr(
        bridge,
        "product_snapshot_history",
        lambda limit=120: [
            {"dataVersion": version, "snapshotId": f"SNAP-{version}", "createdAt": snapshots[version]["createdAt"]}
            for version, *_ in versions
        ],
    )
    monkeypatch.setattr(
        bridge,
        "get_product_snapshot",
        lambda data_version=None, user_id=None: snapshots.get(data_version),
    )
    bridge._CACHE.clear()

    trend = bridge.read_canonical_product_trend(
        object_id,
        store_id="TB-SH-001",
        user_id="operator",
    )

    assert trend["ready"] is True
    assert trend["snapshotAuthority"] == "canonical_product_snapshot_sets_v1"
    assert trend["legacySnapshotFallbackUsed"] is False
    assert trend["observationSummary"]["validSnapshotCount"] == 3
    assert [item["businessDate"] for item in trend["recentSnapshots"]] == [
        "2026-06-25",
        "2026-06-28",
        "2026-07-02",
    ]
    assert [item["dataVersion"] for item in trend["recentSnapshots"]] == ["DV-1", "DV-2", "DV-3"]
    assert [item["metrics"]["paymentAmount"] for item in trend["recentSnapshots"]] == [
        3555.09,
        3929.31,
        4116.42,
    ]
    assert trend["product"]["storeId"] == "TB-SH-001"
    assert trend["product"]["skuId"] == "SKU10004-A"


def test_canonical_history_does_not_fabricate_snapshots(monkeypatch):
    monkeypatch.setattr(bridge, "product_snapshot_history", lambda limit=120: [])
    monkeypatch.setattr(bridge, "get_product_snapshot", lambda data_version=None, user_id=None: None)
    bridge._CACHE.clear()

    trend = bridge.read_canonical_product_trend("missing", store_id="TB-SH-001")

    assert trend["ready"] is False
    assert trend["recentSnapshots"] == []
    assert trend["observationSummary"]["validSnapshotCount"] == 0
    assert trend["snapshotAuthority"] == "canonical_product_snapshot_sets_v1"
