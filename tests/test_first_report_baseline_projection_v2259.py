from __future__ import annotations


def test_first_report_baseline_does_not_require_signal_contract(monkeypatch):
    from src.services import station_alignment_v225_service as station

    data_version = "DV-FIRST-REPORT"
    canonical = {
        "dataVersion": data_version,
        "productCount": 1,
        "products": [
            {
                "objectId": "TB::S1::P1::SKU1",
                "productId": "P1",
                "storeId": "S1",
                "platform": "TB",
                "productSnapshotHash": "sha256:canonical-p1",
                "profileSnapshot": {
                    "objectId": "TB::S1::P1::SKU1",
                    "productId": "P1",
                    "storeId": "S1",
                    "platform": "TB",
                    "skuId": "SKU1",
                    "title": "Product 1",
                },
                "metricSnapshot": {"paymentAmount": 100.0},
                "factRefs": ["FACT-1"],
                "factHashRefs": ["sha256:fact-1"],
                "sourceArtifactRefs": ["ART-REPORT-1"],
            }
        ],
    }

    monkeypatch.setattr(station, "get_product_snapshot", lambda _dv: canonical)
    monkeypatch.setattr(
        station,
        "is_first_report_baseline",
        lambda _dv: {
            "isFirstReportBaseline": True,
            "baselineNoPrevious": True,
            "reason": "first active report",
        },
    )
    monkeypatch.setattr(
        station.signal_snapshot_service,
        "materialize_product_signal_snapshot",
        lambda **_: {
            "dataVersion": data_version,
            "baselineNoPrevious": True,
            "productSignalPackages": [],
            "signals": [],
        },
    )

    first = station.full_product_bundle_station(data_version)

    assert first["businessOutputType"] == "baseline_product_bundle"
    assert first["baselineProductBundleCount"] == 1
    assert first["productSignalPackageCount"] == 0
    assert first["generatedSignalCount"] == 0
    assert first["signalEligibility"] is False
    assert first["baselineGate"] == "closed_before_signal_engine"
    assert first["contractValidation"]["status"] == "passed"
    assert first["contractValidation"]["signalContractRequired"] is False
    assert len(first["baselineProductBundles"]) == 1

    monkeypatch.setattr(station, "_resolve_business_artifact", lambda _ref: first)
    validated = station.bundle_validation_station(
        data_version,
        full_product_bundle_ref="ART-BASELINE",
    )

    assert validated["businessOutputType"] == "validated_baseline_product_bundle"
    assert validated["bundleCount"] == 1
    assert validated["baselineProductBundleCount"] == 1
    assert validated["validatedSignalCount"] == 0
    assert validated["validationStatus"] == "passed"
    assert validated["signalEligibility"] is False
    assert validated["baselineGate"] == "closed_before_signal_engine"
    assert validated["productSignalPackages"] == []


def test_pipeline_live_product_total_comes_from_canonical_inventory(monkeypatch):
    from src.services import pipeline_live_read_model_v2258_service as live

    monkeypatch.setattr(
        live.legacy,
        "read_pipeline_live_model",
        lambda **_: {
            "dataVersion": "DV-FIRST-REPORT",
            "baselineOnly": True,
            "summary": {},
            "stages": [],
            "batchState": {"status": "completed"},
            "items": [],
        },
    )
    monkeypatch.setattr(live, "_current_rows", lambda _dv: [])
    monkeypatch.setattr(live, "_canonical_product_count", lambda _dv: 30)

    result = live.read_pipeline_live_model("DV-FIRST-REPORT")
    summary = result["summary"]

    assert summary["productTotal"] == 30
    assert summary["productCount"] == 30
    assert summary["totalItems"] == 30
    assert summary["canonicalProductCount"] == 30
    assert summary["baselineEstablished"] == 30
    assert summary["productFailed"] == 0
    assert summary["batchFailed"] == 0
    assert summary["agent1Current"] == 0
    assert result["productTruthSource"].startswith("canonical_product_snapshot")
    assert "Signal/Agent" in result["rule"]
