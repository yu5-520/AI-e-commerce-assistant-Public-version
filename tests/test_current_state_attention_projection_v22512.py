from __future__ import annotations

import unittest
from unittest.mock import patch


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


class CurrentStateAttentionProjectionTests(unittest.TestCase):
    def test_current_observation_never_falls_back_to_older_agent1_history(self) -> None:
        from src.services import pipeline_live_read_model_v225_service as live

        rows = [
            _row(stage="observed_soft_gate", status="observed", updated_at="2026-08-10T13:25:00"),
            _row(stage="agent1_running", status="running", updated_at="2026-08-10T13:20:00"),
        ]
        with patch.object(live, "_active_report_data_version", return_value="DV-CURRENT"), patch.object(
            live.base, "_current_rows", side_effect=lambda _dv: list(rows)
        ), patch.object(live.base, "read_pipeline_live_model", side_effect=lambda **_: _base_result()):
            result = live.read_pipeline_live_model("DV-STALE-REQUEST")

        self.assertEqual(result["items"], [])
        self.assertEqual(result["summary"]["observed"], 1)
        self.assertEqual(result["summary"]["agent1Current"], 0)
        self.assertIs(result["attentionHistoryFallbackAllowed"], False)
        self.assertEqual(result["attentionIdentity"], "dataVersion+storeId+productId")
        self.assertEqual(
            result["attentionStateAuthority"],
            "latest_pipeline_item_row_per_current_product",
        )

    def test_latest_agent1_row_remains_visible_when_it_is_really_current(self) -> None:
        from src.services import pipeline_live_read_model_v225_service as live

        rows = [
            _row(stage="agent1_running", status="running", updated_at="2026-08-10T13:25:00"),
            _row(stage="observed_soft_gate", status="observed", updated_at="2026-08-10T13:20:00"),
        ]
        with patch.object(live, "_active_report_data_version", return_value="DV-CURRENT"), patch.object(
            live.base, "_current_rows", side_effect=lambda _dv: list(rows)
        ), patch.object(live.base, "read_pipeline_live_model", side_effect=lambda **_: _base_result()):
            result = live.read_pipeline_live_model()

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["productId"], "P10006")
        self.assertEqual(result["items"][0]["currentStage"], "agent1_running")
        self.assertEqual(result["items"][0]["stageLabel"], "Agent1运行")

    def test_latest_identity_resolution_is_per_store_and_product(self) -> None:
        from src.services import pipeline_live_read_model_v225_service as live

        rows = [
            _row(
                stage="observed_soft_gate",
                status="observed",
                updated_at="2026-08-10T13:25:00",
                product_id="P10006",
            ),
            _row(
                stage="agent1_running",
                status="running",
                updated_at="2026-08-10T13:24:00",
                product_id="P10007",
            ),
            _row(
                stage="agent1_running",
                status="running",
                updated_at="2026-08-10T13:20:00",
                product_id="P10006",
            ),
        ]
        with patch.object(live.base, "_current_rows", side_effect=lambda _dv: list(rows)):
            latest = live._latest_current_rows("DV-CURRENT")

        identities = {live.base._identity(row): row["current_stage"] for row in latest}
        self.assertEqual(identities["S1::P10006"], "observed_soft_gate")
        self.assertEqual(identities["S1::P10007"], "agent1_running")
        self.assertEqual(len(latest), 2)

    def test_reset_closes_attention_even_if_legacy_base_contains_stale_items(self) -> None:
        from src.services import pipeline_live_read_model_v225_service as live

        with patch.object(live, "_active_report_data_version", return_value=None), patch.object(
            live.base, "read_pipeline_live_model", side_effect=lambda **_: _base_result()
        ):
            result = live.read_pipeline_live_model()

        self.assertIsNone(result["dataVersion"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["summary"]["observed"], 0)
        self.assertEqual(result["summary"]["agent1Current"], 0)
        self.assertIs(result["attentionHistoryFallbackAllowed"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
