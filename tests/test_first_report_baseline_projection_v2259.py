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


def test_pipeline_live_current_projection_closes_when_reset_has_no_active_dataversion(monkeypatch):
    from src.services import pipeline_live_read_model_v225_service as live

    monkeypatch.setattr(live, "_active_report_data_version", lambda: None)
    monkeypatch.setattr(
        live.base,
        "read_pipeline_live_model",
        lambda **_: {
            "dataVersion": "DV-STALE-HISTORY",
            "summary": {
                "totalItems": 30,
                "productCount": 30,
                "productTotal": 30,
                "canonicalProductCount": 30,
                "baselineEstablished": 30,
            },
            "stages": [
                {
                    "label": "Agent1 研判",
                    "queued": 2,
                    "completed": 3,
                    "current": {"queued": 2, "completed": 3},
                }
            ],
            "items": [{"productId": "OLD-P1"}],
        },
    )

    result = live.read_pipeline_live_model()
    summary = result["summary"]

    assert result["dataVersion"] is None
    assert result["activeDataVersion"] is None
    assert result["activeDataVersionGate"] == "closed_no_active_import_runtime"
    assert summary["productTotal"] == 0
    assert summary["productCount"] == 0
    assert summary["totalItems"] == 0
    assert summary["canonicalProductCount"] == 0
    assert summary["baselineEstablished"] == 0
    assert summary["agent1Current"] == 0
    assert result["items"] == []
    assert result["stages"][0]["queued"] == 0
    assert result["stages"][0]["completed"] == 0
    assert result["stages"][0]["current"]["queued"] == 0


def test_pipeline_live_current_projection_binds_to_active_import_runtime(monkeypatch):
    from src.services import pipeline_live_read_model_v225_service as live

    calls = []
    monkeypatch.setattr(live, "_active_report_data_version", lambda: "DV-ACTIVE")

    def fake_read_pipeline_live_model(**kwargs):
        calls.append(kwargs)
        return {
            "dataVersion": kwargs.get("data_version"),
            "summary": {"productTotal": 30},
            "stages": [],
            "items": [],
        }

    monkeypatch.setattr(live.base, "read_pipeline_live_model", fake_read_pipeline_live_model)

    result = live.read_pipeline_live_model("DV-STALE-REQUEST")

    assert calls == [{"data_version": "DV-ACTIVE", "limit": 80}]
    assert result["dataVersion"] == "DV-ACTIVE"
    assert result["activeDataVersion"] == "DV-ACTIVE"
    assert result["activeDataVersionGate"] == "open_active_import_runtime"
    assert result["requestedDataVersion"] == "DV-STALE-REQUEST"
    assert result["summary"]["productTotal"] == 30


def test_baseline_bundle_contract_accepts_zero_signal_before_transport_artifact_exists():
    from src.services.station_contract_service import validate_contract_payload

    payload = {
        "productSignalPackageCount": 0,
        "baselineProductBundleCount": 30,
        "signalEligibility": False,
        "baselineGate": "closed_before_signal_engine",
        "contractValidation": {
            "ok": True,
            "status": "passed",
            "packageCount": 30,
            "signalContractRequired": False,
        },
        "outputRef": "business_output_pending_artifact:full_product_bundle:DV-FIRST",
    }
    check = validate_contract_payload(
        "full_product_bundle_station",
        payload,
        direction="output",
    )

    assert check["status"] == "passed"
    assert check["missing"] == []
    assert "fullProductBundleRef" not in check["required"]
    assert "baselineProductBundleCount" in check["required"]


def test_baseline_validation_contract_accepts_zero_validated_signal_before_transport_artifact_exists():
    from src.services.station_contract_service import validate_contract_payload

    payload = {
        "bundleCount": 30,
        "baselineProductBundleCount": 30,
        "validatedSignalCount": 0,
        "validationStatus": "passed",
        "signalEligibility": False,
        "baselineGate": "closed_before_signal_engine",
        "contractValidation": {
            "ok": True,
            "status": "passed",
            "packageCount": 30,
            "signalContractRequired": False,
        },
        "outputRef": "business_output_pending_artifact:bundle_validation:DV-FIRST",
    }
    check = validate_contract_payload(
        "bundle_validation_station",
        payload,
        direction="output",
    )

    assert check["status"] == "passed"
    assert check["missing"] == []
    assert "validatedBundleRef" not in check["required"]
    assert "validatedSignalCount" in check["required"]


def test_baseline_admission_contract_accepts_zero_signal_and_agent1_without_admission_ref():
    from src.services.station_contract_service import validate_contract_payload

    payload = {
        "businessOutputType": "baseline_history_gate_closed",
        "validatedBundleArtifactRef": "ART-VALIDATED-BASELINE",
        "signalEligibility": False,
        "baselineGate": "closed_before_signal_engine",
        "fullSignalCount": 0,
        "generatedSignalCount": 0,
        "qualifiedSignalCount": 0,
        "candidateProductCount": 0,
        "admittedSignalCount": 0,
        "observedSignalCount": 0,
        "agent1PendingItemCount": 0,
        "outputRef": "business_output_pending_artifact:baseline_admission:DV-FIRST",
    }
    check = validate_contract_payload(
        "product_signal_admission_station",
        payload,
        direction="output",
    )

    assert check["status"] == "passed"
    assert check["missing"] == []
    assert "admissionRef" not in check["required"]
    assert "agent1PendingItemCount" in check["required"]


def test_bundle_contract_still_fails_closed_when_business_evidence_validation_is_missing():
    from src.services.station_contract_service import validate_contract_payload

    payload = {
        "productSignalPackageCount": 0,
        "baselineProductBundleCount": 30,
        "signalEligibility": False,
        "baselineGate": "closed_before_signal_engine",
        "outputRef": "business_output_pending_artifact:full_product_bundle:DV-FIRST",
    }
    check = validate_contract_payload(
        "full_product_bundle_station",
        payload,
        direction="output",
    )

    assert check["status"] == "failed"
    assert check["missing"] == ["contractValidation"]
