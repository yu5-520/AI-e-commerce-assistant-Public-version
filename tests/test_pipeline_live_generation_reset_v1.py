from __future__ import annotations

import unittest
from unittest.mock import patch


class PipelineResetEmptyGenerationTests(unittest.TestCase):
    def test_reset_empty_does_not_touch_legacy_latest_or_history_reader(self) -> None:
        from src.services import pipeline_live_read_model_v225_service as live

        generation = {
            "generationSeq": 8,
            "generationHash": "sha256:g8",
            "state": "empty",
            "activeDataVersion": None,
        }
        with patch.object(live, "_runtime_generation", return_value=generation), patch.object(
            live, "_active_report_data_version", return_value=None
        ), patch.object(
            live.base,
            "read_pipeline_live_model",
            side_effect=AssertionError("historical reader must not be called after reset"),
        ):
            result = live.read_pipeline_live_model()

        self.assertIsNone(result["dataVersion"])
        self.assertEqual(result["summary"]["productTotal"], 0)
        self.assertEqual(result["summary"]["taskAdmitted"], 0)
        self.assertEqual(result["items"], [])
        self.assertIs(result["historicalReaderInvoked"], False)
        self.assertIs(result["crossGenerationLastGoodFallbackAllowed"], False)
        self.assertEqual(result["runtimeGenerationHash"], "sha256:g8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
