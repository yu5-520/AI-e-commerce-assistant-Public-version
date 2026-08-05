from __future__ import annotations

import src  # noqa: F401

from src.services.task_metric_evidence_projection_v2178_service import (
    TASK_METRIC_EVIDENCE_PROJECTION_VERSION,
    build_task_metric_evidence_projection,
)


def _snapshots() -> list[dict]:
    return [
        {
            "snapshotId": "SNAP-0628",
            "dataVersion": "DV-0628",
            "createdAt": "2026-06-28T23:00:00",
            "products": [
                {
                    "objectId": "PI-1",
                    "productId": "P-1",
                    "storeId": "S-1",
                    "metricDate": "2026-06-28",
                    "metricSnapshot": {
                        "paymentAmount": 1501.56,
                        "roi": 2.66,
                        "adSpend": 563.74,
                        "availableDays": 8,
                        "clickRate": 0.0177,
                        "conversionRate": 0.03,
                        "refundRate": 0,
                    },
                }
            ],
        },
        {
            "snapshotId": "SNAP-0625",
            "dataVersion": "DV-0625",
            "createdAt": "2026-06-25T23:00:00",
            "products": [
                {
                    "objectId": "PI-1",
                    "productId": "P-1",
                    "storeId": "S-1",
                    "metricDate": "2026-06-25",
                    "metricSnapshot": {
                        "paymentAmount": 1376.43,
                        "roi": 2.85,
                        "adSpend": 482.96,
                        "availableDays": 11,
                        "clickRate": 0.0191,
                        "conversionRate": 0.034,
                        "refundRate": 0,
                    },
                }
            ],
        },
    ]


def _task() -> dict:
    return {
        "taskId": "LT-EVIDENCE-1",
        "dataVersion": "DV-0628",
        "createdAt": "2026-06-29T00:00:00",
        "actionFamily": "roas_scale",
        "productIdentity": {
            "objectId": "PI-1",
            "productId": "P-1",
            "storeId": "S-1",
        },
        "metricDigest": {
            "version": "21.7.7",
            "actionFamily": "roas_scale",
            "current": {
                "gmv": 1501.56,
                "currentROI": 2.66,
                "spend": 563.74,
                "inventoryDays": 8,
            },
            "recentFiveOrLatestFacts": [
                {"metric": "支付金额", "previous": 1376.43, "current": 1501.56, "changeRate": 0.091},
                {"metric": "ROI", "previous": 2.85, "current": 2.66, "changeRate": -0.067},
                {"metric": "广告消耗", "previous": 482.96, "current": 563.74, "changeRate": 0.167},
                {"metric": "可售天数", "previous": 11, "current": 8, "changeRate": -0.273},
            ],
        },
        "operatorJudgmentView": {
            "judgmentBasis": [
                "销售增长但可售天数快速下降，存在断货风险",
                "当前投放效率健康，应由仓储确认补货计划后决定是否放大投放",
            ]
        },
        "agent2ActionPlan": {
            "actionFamily": "roas_scale",
            "reviewMetrics": ["ROI", "支付金额", "广告消耗"],
        },
    }


def test_task_projection_keeps_only_referenced_metric_codes() -> None:
    projection = build_task_metric_evidence_projection(_task(), snapshots=_snapshots())

    assert projection["version"] == TASK_METRIC_EVIDENCE_PROJECTION_VERSION
    assert projection["evidenceStatus"] == "ready"
    assert projection["taskExecutableFromEvidence"] is True
    assert projection["referencedMetricCodes"] == [
        "paymentAmount",
        "roi",
        "adSpend",
        "availableDays",
    ]
    assert "clickRate" not in projection["referencedMetricCodes"]
    assert "conversionRate" not in projection["referencedMetricCodes"]
    assert "refundRate" not in projection["referencedMetricCodes"]
    assert projection["referenceWindow"]["snapshotCount"] == 2
    assert projection["referenceWindow"]["startBusinessDate"] == "2026-06-25"
    assert projection["referenceWindow"]["endBusinessDate"] == "2026-06-28"
    assert projection["recentSnapshots"][1]["changes"]["paymentAmount"] > 0
    assert projection["recentSnapshots"][1]["changes"]["roi"] < 0
    usage = {item["code"]: item["taskUsage"] for item in projection["metricDefinitions"]}
    assert usage["roi"] == "判断投放效率和最低安全线"
    assert usage["availableDays"] == "确认库存能够承接测试周期"


def test_empty_dynamic_changes_is_not_called_a_baseline() -> None:
    task = _task()
    task["dynamicMetricChanges"] = []
    task["metricDigest"] = {}
    task["operatorJudgmentView"] = {}
    task["agent2ActionPlan"] = {"actionFamily": "roas_scale", "reviewMetrics": []}

    projection = build_task_metric_evidence_projection(task, snapshots=_snapshots())

    assert projection["evidenceStatus"] == "evidence_missing"
    assert projection["taskExecutableFromEvidence"] is False
    assert projection["reason"] == "task_referenced_metric_codes_missing"


def test_task_data_version_freezes_out_later_product_observations() -> None:
    later = {
        "snapshotId": "SNAP-0701",
        "dataVersion": "DV-0701",
        "createdAt": "2026-07-01T23:00:00",
        "products": [
            {
                "objectId": "PI-1",
                "productId": "P-1",
                "storeId": "S-1",
                "metricDate": "2026-07-01",
                "metricSnapshot": {"paymentAmount": 9999, "roi": 9.9, "adSpend": 1, "availableDays": 30},
            }
        ],
    }
    projection = build_task_metric_evidence_projection(_task(), snapshots=[later, *_snapshots()])

    assert projection["referenceWindow"]["endBusinessDate"] == "2026-06-28"
    assert all(item["businessDate"] != "2026-07-01" for item in projection["recentSnapshots"])
    assert projection["recentSnapshots"][-1]["metrics"]["paymentAmount"] == 1501.56
