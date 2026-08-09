#!/usr/bin/env python3
"""Verify the ERA 10x3 operating-unit and canonical-history recovery contract.

This is the dedicated behavior evidence for REC-001. The three-report Agent fixture is
intentionally only three signal products, so it must never be used to claim the 30-unit
ERA inventory contract. This probe reads the real competition sample generators and the
canonical trend bridge without mutating the runtime database.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Sequence

from openpyxl import load_workbook

from src.services import canonical_product_trend_v2_service as trend_bridge
from src.services.competition_era_sample_payload_v2 import sample_contract
from src.services.competition_sample_report_service import (
    SAMPLE_REPORTS,
    SAMPLE_SHEET_ORDER,
    build_competition_sample_xlsx,
)

SCHEMA = "competition.era_recovery_contract.v1"
EXPECTED_SHEET_ROWS = {
    "商品经营明细": 30,
    "店铺经营汇总": 3,
    "流量来源明细": 150,
}


def _hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _product(
    object_id: str,
    store_id: str,
    product_id: str,
    sku_id: str,
    date: str,
    payment: float,
    roi: float,
    conversion: float,
) -> dict[str, Any]:
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


def _sample_period_probe(period: int) -> dict[str, Any]:
    first = build_competition_sample_xlsx(period)
    second = build_competition_sample_xlsx(period)
    workbook = load_workbook(io.BytesIO(first), read_only=True, data_only=True)
    sheet_names = list(workbook.sheetnames)
    product_rows = list(workbook["商品经营明细"].iter_rows(values_only=True))
    headers = list(product_rows[0])
    store_idx = headers.index("店铺ID")
    product_idx = headers.index("商品ID")
    sku_idx = headers.index("SKU ID")
    operating_units = {
        (row[store_idx], row[product_idx], row[sku_idx]) for row in product_rows[1:]
    }
    global_products = {row[product_idx] for row in product_rows[1:]}
    stores = {row[store_idx] for row in product_rows[1:]}
    contract = sample_contract(period)
    actual_sheet_rows = {
        name: max(0, int(workbook[name].max_row or 0) - 1) for name in SAMPLE_SHEET_ORDER
    }
    assertions = {
        "byteDeterministic": first == second,
        "xlsxMagic": first.startswith(b"PK"),
        "threeSheets": sheet_names == list(SAMPLE_SHEET_ORDER),
        "sheetRows": actual_sheet_rows == EXPECTED_SHEET_ROWS,
        "operatingUnits30": len(operating_units) == 30,
        "globalProducts10": len(global_products) == 10,
        "stores3": len(stores) == 3,
        "contractOperatingUnits30": contract.get("operatingProductUnitCount") == 30,
        "contractGlobalProducts10": contract.get("globalProductCount") == 10,
        "contractStores3": contract.get("storeCount") == 3,
        "contractRows183": sum(int(value) for value in actual_sheet_rows.values()) == 183,
        "signalFixtureStill3Only": len(SAMPLE_REPORTS.get(period) or []) == 3,
        "signalFixtureDoesNotOwnInventory": contract.get("operatingProductUnitCount")
        > len(SAMPLE_REPORTS.get(period) or []),
    }
    return {
        "period": period,
        "verified": all(assertions.values()),
        "xlsxSha256": "sha256:" + hashlib.sha256(first).hexdigest(),
        "byteSize": len(first),
        "sheetNames": sheet_names,
        "sheetRows": actual_sheet_rows,
        "operatingUnitCount": len(operating_units),
        "globalProductCount": len(global_products),
        "storeCount": len(stores),
        "signalFixtureCount": len(SAMPLE_REPORTS.get(period) or []),
        "assertions": assertions,
    }


def _canonical_history_probe() -> dict[str, Any]:
    object_id = "product::tianmao::TB-SH-001::P10004::SKU10004-A"
    sibling_id = "product::jingdong::JD-SH-002::P10004::SKU10004-B"
    versions = [
        ("DV-3", "2026-07-02", 4116.42, 3.35, 0.0427),
        ("DV-2", "2026-06-28", 3929.31, 3.35, 0.0426),
        ("DV-1", "2026-06-25", 3555.09, 3.35, 0.0438),
    ]
    snapshots: dict[str, dict[str, Any]] = {}
    for version, date, payment, roi, conversion in versions:
        snapshots[version] = {
            "snapshotId": f"SNAP-{version}",
            "dataVersion": version,
            "createdAt": f"{date}T23:10:00",
            "updatedAt": f"{date}T23:10:00",
            "products": [
                _product(
                    object_id,
                    "TB-SH-001",
                    "P10004",
                    "SKU10004-A",
                    date,
                    payment,
                    roi,
                    conversion,
                ),
                _product(
                    sibling_id,
                    "JD-SH-002",
                    "P10004",
                    "SKU10004-B",
                    date,
                    payment * 2,
                    roi + 1,
                    conversion + 0.01,
                ),
            ],
        }

    original_history = trend_bridge.product_snapshot_history
    original_get = trend_bridge.get_product_snapshot
    try:
        trend_bridge.product_snapshot_history = lambda limit=120: [
            {
                "dataVersion": version,
                "snapshotId": f"SNAP-{version}",
                "createdAt": snapshots[version]["createdAt"],
            }
            for version, *_ in versions
        ]
        trend_bridge.get_product_snapshot = (
            lambda data_version=None, user_id=None: snapshots.get(data_version)
        )
        trend_bridge._CACHE.clear()
        trend = trend_bridge.read_canonical_product_trend(
            object_id,
            store_id="TB-SH-001",
            user_id="competition-recovery-probe",
        )

        trend_bridge.product_snapshot_history = lambda limit=120: []
        trend_bridge.get_product_snapshot = lambda data_version=None, user_id=None: None
        trend_bridge._CACHE.clear()
        missing = trend_bridge.read_canonical_product_trend(
            "missing",
            store_id="TB-SH-001",
            user_id="competition-recovery-probe",
        )
    finally:
        trend_bridge.product_snapshot_history = original_history
        trend_bridge.get_product_snapshot = original_get
        trend_bridge._CACHE.clear()

    recent = trend.get("recentSnapshots") if isinstance(trend, dict) else []
    recent = recent if isinstance(recent, list) else []
    assertions = {
        "ready": trend.get("ready") is True,
        "canonicalAuthority": trend.get("snapshotAuthority")
        == "canonical_product_snapshot_sets_v1",
        "legacyFallbackDisabled": trend.get("legacySnapshotFallbackUsed") is False,
        "threeValidSnapshots": _dict(trend.get("observationSummary")).get(
            "validSnapshotCount"
        )
        == 3,
        "threeDataVersions": [item.get("dataVersion") for item in recent]
        == ["DV-1", "DV-2", "DV-3"],
        "sameStoreOnly": _dict(trend.get("product")).get("storeId") == "TB-SH-001",
        "sameSkuOnly": _dict(trend.get("product")).get("skuId") == "SKU10004-A",
        "noFabricatedMissingSnapshots": missing.get("recentSnapshots") == []
        and _dict(missing.get("observationSummary")).get("validSnapshotCount") == 0,
    }
    return {
        "verified": all(assertions.values()),
        "objectId": object_id,
        "dataVersions": [item.get("dataVersion") for item in recent],
        "businessDates": [item.get("businessDate") for item in recent],
        "storeId": _dict(trend.get("product")).get("storeId"),
        "skuId": _dict(trend.get("product")).get("skuId"),
        "assertions": assertions,
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_report() -> dict[str, Any]:
    periods = [_sample_period_probe(period) for period in (1, 2, 3)]
    history = _canonical_history_probe()
    assertions = {
        "allThreeSamplePeriodsVerified": all(item["verified"] for item in periods),
        "sameOperatingUnitShapeEveryPeriod": len(
            {
                (
                    item["operatingUnitCount"],
                    item["globalProductCount"],
                    item["storeCount"],
                )
                for item in periods
            }
        )
        == 1,
        "canonicalHistoryVerified": history.get("verified") is True,
    }
    material = {
        "schema": SCHEMA,
        "periods": periods,
        "canonicalHistory": history,
        "assertions": assertions,
    }
    return {
        **material,
        "verified": all(assertions.values()),
        "eraRecoveryHash": _hash(material),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify ERA 10x3 sample inventory and canonical history recovery."
    )
    parser.add_argument(
        "--output",
        default="dist/competition-three-report-e2e/era-recovery-contract.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report()
    _write(Path(args.output), report)
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "eraRecoveryHash": report["eraRecoveryHash"],
                "periods": [
                    {
                        "period": item["period"],
                        "operatingUnitCount": item["operatingUnitCount"],
                        "globalProductCount": item["globalProductCount"],
                        "storeCount": item["storeCount"],
                        "signalFixtureCount": item["signalFixtureCount"],
                    }
                    for item in report["periods"]
                ],
                "canonicalHistoryVerified": report["canonicalHistory"]["verified"],
                "canonicalDataVersions": report["canonicalHistory"]["dataVersions"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
