from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services import agent_runtime_hard_interface_v22515_service as runtime


def _candidate(item_id: str) -> dict:
    return {"item_id": item_id}


def _envelope(chars: int) -> dict:
    return {
        "schema": "agent_input.agent1.v3",
        "projectionAudit": {"projectedChars": chars},
        "payload": {"productId": "P"},
    }


class Agent1ReadyFirstRuntimeTest(unittest.TestCase):
    def test_less_than_capacity_runs_immediately(self) -> None:
        items = [_candidate("I1"), _candidate("I2"), _candidate("I3")]
        with (
            patch.object(runtime.pipeline_agent1_core, "_pending_items", side_effect=lambda dv, limit: items[:limit]),
            patch.object(runtime.pipeline_agent1_core, "pending_agent1_item_count", return_value=3),
            patch.object(runtime, "build_operating_policy_context", return_value={}),
            patch.object(runtime, "ensure_agent1_input_ref", side_effect=lambda item, policy_context: f"ART-{item['item_id']}"),
            patch.object(runtime, "resolve_agent_input_ref", side_effect=lambda ref, expected_schema: _envelope(10_000)),
        ):
            plan = runtime.plan_agent1_ready_first_batch("DV-1", batch_size=8)

        self.assertEqual(plan["capacityItems"], 8)
        self.assertEqual(plan["candidateWindowCount"], 3)
        self.assertEqual(plan["selectedItemCount"], 3)
        self.assertEqual(plan["remainingReadyCount"], 0)
        self.assertIs(plan["waitForFullCapacity"], False)
        self.assertEqual(plan["providerCallsExecuted"], 0)

    def test_char_budget_selects_only_first_provider_subbatch(self) -> None:
        items = [_candidate(f"I{i}") for i in range(1, 6)]
        chars_by_ref = {
            "ART-I1": 20_000,
            "ART-I2": 20_000,
            "ART-I3": 20_000,
            "ART-I4": 20_000,
            "ART-I5": 5_000,
        }
        with (
            patch.object(runtime.pipeline_agent1_core, "_pending_items", side_effect=lambda dv, limit: items[:limit]),
            patch.object(runtime.pipeline_agent1_core, "pending_agent1_item_count", return_value=5),
            patch.object(runtime, "build_operating_policy_context", return_value={}),
            patch.object(runtime, "ensure_agent1_input_ref", side_effect=lambda item, policy_context: f"ART-{item['item_id']}"),
            patch.object(
                runtime,
                "resolve_agent_input_ref",
                side_effect=lambda ref, expected_schema: _envelope(chars_by_ref[ref]),
            ),
        ):
            plan = runtime.plan_agent1_ready_first_batch("DV-2", batch_size=8)

        self.assertEqual(plan["selectedItemCount"], 3)
        self.assertEqual(plan["selectedProjectedChars"], 60_000)
        self.assertEqual(plan["selectedItemIds"], ["I1", "I2", "I3"])
        self.assertEqual(plan["remainingReadyCount"], 2)
        self.assertEqual(
            plan["inspectedPrefix"][-1]["stopReason"],
            "batch_char_budget_would_be_exceeded",
        )

    def test_wrapper_passes_selected_prefix_size_to_existing_hard_runtime(self) -> None:
        captured = {}

        def _legacy(data_version, *, user_id=None, batch_size=8):
            captured["dataVersion"] = data_version
            captured["batchSize"] = batch_size
            return {"ran": True, "claimedItemCount": batch_size}

        with (
            patch.object(
                runtime,
                "plan_agent1_ready_first_batch",
                return_value={
                    "selectedItemCount": 3,
                    "policy": runtime.AGENT1_READY_FIRST_POLICY,
                },
            ),
            patch.object(runtime.legacy, "run_agent1_microbatch_hard", side_effect=_legacy),
        ):
            result = runtime.run_agent1_ready_first_microbatch_hard("DV-3", batch_size=8)

        self.assertEqual(captured, {"dataVersion": "DV-3", "batchSize": 3})
        self.assertEqual(result["effectiveClaimBatchSize"], 3)
        self.assertEqual(result["configuredBatchCapacity"], 8)
        self.assertEqual(result["claimScope"], "current_provider_subbatch_only")
        self.assertIs(result["secondWorkerCreated"], False)
        self.assertIs(result["providerConfigurationChanged"], False)
        self.assertIs(result["exactHashRuntimeChanged"], False)

    def test_prepare_failure_stays_in_selected_prefix(self) -> None:
        items = [_candidate("BAD"), _candidate("GOOD")]

        def _ensure(item, policy_context):
            if item["item_id"] == "BAD":
                raise RuntimeError("projection_failed")
            return "ART-GOOD"

        with (
            patch.object(runtime.pipeline_agent1_core, "_pending_items", side_effect=lambda dv, limit: items[:limit]),
            patch.object(runtime.pipeline_agent1_core, "pending_agent1_item_count", return_value=2),
            patch.object(runtime, "build_operating_policy_context", return_value={}),
            patch.object(runtime, "ensure_agent1_input_ref", side_effect=_ensure),
            patch.object(runtime, "resolve_agent_input_ref", side_effect=lambda ref, expected_schema: _envelope(10_000)),
        ):
            plan = runtime.plan_agent1_ready_first_batch("DV-4", batch_size=8)

        self.assertEqual(plan["selectedItemCount"], 2)
        self.assertEqual(plan["prepareFailureItemIds"], ["BAD"])
        self.assertEqual(plan["selectedItemIds"], ["BAD", "GOOD"])

    def test_ready_first_does_not_replace_registered_agent1_stage_owner(self) -> None:
        binding = runtime.active_agent1_runtime_binding()

        self.assertIs(binding["matched"], True)
        self.assertEqual(
            binding["agent1StageOwner"],
            "src.services.agent_runtime_hard_interface_v2257_service",
        )
        self.assertEqual(
            binding["tokenRuntimeOwner"],
            "src.services.agent_token_runtime_hash_exact_v2259_service",
        )
        self.assertEqual(binding["agent1ReadyFirstRuntimeVersion"], "23.1.5")
        self.assertIs(binding["secondWorkerCreated"], False)


if __name__ == "__main__":
    unittest.main()
