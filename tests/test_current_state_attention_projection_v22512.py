from __future__ import annotations


def _row(*, stage: str, status: str, updated_at: str, product_id: str = "P10006") -> dict:
    return {
        "item_id": f"ITEM-{stage}-{updated_at}",
        "data_version": "DV-CURRENT",
        "store_id": "S1",
        "product_id": product_id,
        "signal_id": f"SIG-{product_id}",
        "current_stage": stage,
        "status": status,
        "updated_at": updated_at,
        "retry_count": 0,
        "action_family": None,
    }


def _base_result() -> dict:
    return {
        "dataVersion": "DV-CURRENT",
        "summary": {"productTotal": 30, "observed": 1, "agent1Current": 0},
        "stages": [],
        "items": [
            {
                "productId": "P10006",
                "currentStage": "agent1_running",
                "stageLabel": "Agent1运行",
                "bucket": "running",
            }
        ],
    }


def test_current_observation_never_falls_back_to_older_agent1_history(monkeypatch):
    from src.services import pipeline_live_read_model_v225_service as live

    rows = [
        _row(stage="observed_soft_gate", status="observed", updated_at="2026-08-10T13:25:00"),
        _row(stage="agent1_running", status="running", updated_at="2026-08-10T13:20:00"),
    ]
    monkeypatch.setattr(live, "_active_report_data_version", lambda: "DV-CURRENT")
    monkeypatch.setattr(live.base, "_current_rows", lambda _dv: list(rows))
    monkeypatch.setattr(live.base, "read_pipeline_live_model", lambda **_: _base_result())

    result = live.read_pipeline_live_model("DV-STALE-REQUEST")

    assert result["items"] == []
    assert result["summary"]["observed"] == 1
    assert result["summary"]["agent1Current"] == 0
    assert result["attentionHistoryFallbackAllowed"] is False
    assert result["attentionIdentity"] == "dataVersion+storeId+productId"
    assert result["attentionStateAuthority"] == "latest_pipeline_item_row_per_current_product"


def test_latest_agent1_row_remains_visible_when_it_is_really_current(monkeypatch):
    from src.services import pipeline_live_read_model_v225_service as live

    rows = [
        _row(stage="agent1_running", status="running", updated_at="2026-08-10T13:25:00"),
        _row(stage="observed_soft_gate", status="observed", updated_at="2026-08-10T13:20:00"),
    ]
    monkeypatch.setattr(live, "_active_report_data_version", lambda: "DV-CURRENT")
    monkeypatch.setattr(live.base, "_current_rows", lambda _dv: list(rows))
    monkeypatch.setattr(live.base, "read_pipeline_live_model", lambda **_: _base_result())

    result = live.read_pipeline_live_model()

    assert len(result["items"]) == 1
    assert result["items"][0]["productId"] == "P10006"
    assert result["items"][0]["currentStage"] == "agent1_running"
    assert result["items"][0]["stageLabel"] == "Agent1运行"


def test_latest_identity_resolution_is_per_store_and_product(monkeypatch):
    from src.services import pipeline_live_read_model_v225_service as live

    rows = [
        _row(stage="observed_soft_gate", status="observed", updated_at="2026-08-10T13:25:00", product_id="P10006"),
        _row(stage="agent1_running", status="running", updated_at="2026-08-10T13:24:00", product_id="P10007"),
        _row(stage="agent1_running", status="running", updated_at="2026-08-10T13:20:00", product_id="P10006"),
    ]
    monkeypatch.setattr(live.base, "_current_rows", lambda _dv: list(rows))

    latest = live._latest_current_rows("DV-CURRENT")
    identities = {live.base._identity(row): row["current_stage"] for row in latest}

    assert identities["S1::P10006"] == "observed_soft_gate"
    assert identities["S1::P10007"] == "agent1_running"
    assert len(latest) == 2


def test_reset_closes_attention_even_if_legacy_base_contains_stale_items(monkeypatch):
    from src.services import pipeline_live_read_model_v225_service as live

    monkeypatch.setattr(live, "_active_report_data_version", lambda: None)
    monkeypatch.setattr(live.base, "read_pipeline_live_model", lambda **_: _base_result())

    result = live.read_pipeline_live_model()

    assert result["dataVersion"] is None
    assert result["items"] == []
    assert result["summary"]["observed"] == 0
    assert result["summary"]["agent1Current"] == 0
    assert result["attentionHistoryFallbackAllowed"] is False
