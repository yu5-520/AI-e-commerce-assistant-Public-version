from src.services.canonical_product_snapshot_service import (
    agent_projection,
    build_canonical_product_snapshot_item,
    detail_projection,
)


def _sample_product():
    return {
        "objectId": "taobao::STORE-1::P-100::SKU-1",
        "productId": "P-100",
        "skuId": "SKU-1",
        "storeId": "STORE-1",
        "storeName": "脱敏店铺",
        "platform": "taobao",
        "title": "脱敏商品",
        "verticalCategory": "家居用品",
        "productRole": "hero",
        "lifecycleStage": "growth",
        "paymentAmount": 12800,
        "roi": 3.2,
        "roas": 3.2,
        "adSpend": 4000,
        "clickRate": 0.08,
        "conversionRate": 0.05,
        "refundRate": 0.02,
        "inventory": 320,
        "metricDate": "2026-07-02",
        "reportDate": "2026-07-02",
        "sourceDataVersions": ["DV-20260702"],
        "sourceDatasets": ["product_metrics"],
        "sourceReportRefs": ["report:masked-20260702"],
        "permissionStampId": "PMS-TEST-001",
        "productMetricFacts": [
            {
                "factId": "FACT-PAYMENT-001",
                "sourceHash": "sha256:fact-payment",
                "sourceRowId": "ROW-1",
                "level": "product",
                "metricName": "paymentAmount",
                "sourceReportRef": "report:masked-20260702",
            },
            {
                "factId": "FACT-ROI-001",
                "sourceHash": "sha256:fact-roi",
                "sourceRowId": "ROW-1",
                "level": "product",
                "metricName": "roi",
                "sourceReportRef": "report:masked-20260702",
            },
        ],
        "trafficSourceFacts": [
            {
                "factId": "FACT-TRAFFIC-001",
                "sourceHash": "sha256:fact-traffic",
                "sourceRowId": "ROW-9",
                "level": "traffic_source",
                "metricName": "visitors",
                "sourceReportRef": "report:masked-20260702",
            }
        ],
        "metricFactSummary": {"paymentAmount": {"count": 1}, "roi": {"count": 1}},
    }


def test_product_snapshot_hash_is_deterministic():
    first = build_canonical_product_snapshot_item(_sample_product(), "DV-20260702")
    second = build_canonical_product_snapshot_item(_sample_product(), "DV-20260702")

    assert first["productSnapshotHash"] == second["productSnapshotHash"]
    assert first["snapshotHash"] == first["productSnapshotHash"]
    assert first["parentSnapshotHash"] == first["productSnapshotHash"]


def test_agent_and_detail_projection_share_one_parent_hash():
    product = build_canonical_product_snapshot_item(_sample_product(), "DV-20260702")
    agent = agent_projection(product)
    detail = detail_projection(product)

    assert agent["parentSnapshotHash"] == product["productSnapshotHash"]
    assert detail["parentSnapshotHash"] == product["productSnapshotHash"]
    assert agent["productSnapshotHash"] == detail["productSnapshotHash"]
    assert agent["projectionHash"] != detail["detailProjectionHash"]


def test_fact_ids_and_fact_hashes_have_separate_namespaces():
    product = build_canonical_product_snapshot_item(_sample_product(), "DV-20260702")

    assert product["factRefs"] == ["FACT-PAYMENT-001", "FACT-ROI-001", "FACT-TRAFFIC-001"]
    assert product["factHashRefs"] == [
        "sha256:fact-payment",
        "sha256:fact-roi",
        "sha256:fact-traffic",
    ]
    assert product["factContract"]["usesMetricFactIds"] is True
    assert product["factContract"]["factRefs"] == product["factRefs"]


def test_legacy_product_snapshot_contract_survives_on_canonical_item():
    product = build_canonical_product_snapshot_item(_sample_product(), "DV-20260702")

    assert product["productId"] == "P-100"
    assert product["storeId"] == "STORE-1"
    assert product["platform"] == "taobao"
    assert product["productRole"] == "hero"
    assert product["metricSnapshot"]["roi"] == 3.2
    assert product["sourceDataVersion"] == "DV-20260702"
    assert product["sourceDataset"] == "product_metrics"
    assert product["sourceReportRef"] == "report:masked-20260702"
    assert product["permissionStampId"] == "PMS-TEST-001"
    assert product["permissionGateStatus"] == "passed"
    assert product["permissionRequired"] is True
    assert product["factContract"]["contract"] == "productSnapshot.factContract.v1"


def test_projection_hash_changes_when_fact_changes():
    first_item = _sample_product()
    second_item = _sample_product()
    second_item["paymentAmount"] = 13200

    first = build_canonical_product_snapshot_item(first_item, "DV-20260702")
    second = build_canonical_product_snapshot_item(second_item, "DV-20260702")

    assert first["productSnapshotHash"] != second["productSnapshotHash"]
    assert agent_projection(first)["projectionHash"] != agent_projection(second)["projectionHash"]
    assert detail_projection(first)["detailProjectionHash"] != detail_projection(second)["detailProjectionHash"]
