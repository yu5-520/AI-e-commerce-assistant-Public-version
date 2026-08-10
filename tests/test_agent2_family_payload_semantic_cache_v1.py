from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services import agent_token_runtime_v22520_service as runtime
from src.services import agent_token_runtime_v225_service as active_facade


def _descriptor(**overrides) -> dict:
    value = {
        "stage": runtime.AGENT2_EXACT_OUTPUT_STAGE,
        "itemExecutionId": "EXE-A2-CURRENT",
        "executionHash": "execution-a2-current",
        "inputArtifactRef": "ART-A2-INPUT-CURRENT",
        "inputContentHash": "input-a2-current",
        "inputSchema": "agent_input.agent2.v1",
        "projectionVersion": "22.5.14",
        "promptVersion": runtime.AGENT_TOKEN_RUNTIME_VERSION,
        "policyHash": "policy-a2-1",
        "provider": "bailian",
        "model": "qwen-a",
        "generationParametersHash": "generation-a2-1",
        "packageId": "PKG-CURRENT",
        "storeId": "STORE-A",
        "productId": "P10007",
        "dataVersion": "DV-CURRENT",
        "actionFamily": "title_image_test",
        "semanticCacheEligible": True,
        "semanticHash": "semantic-a2-1",
        "semanticCacheContractVersion": runtime.AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
    }
    value.update(overrides)
    return value


def _package(**overrides) -> dict:
    value = {
        "packageId": "PKG-CURRENT",
        "productId": "P10007",
        "storeId": "STORE-A",
        "dataVersion": "DV-CURRENT",
        "lockedActionFamily": "title_image_test",
        "actionParameterPack": {"testWindowDays": [3, 7]},
        "verticalActionRag": {
            "approvedCaseIds": ["CASE-1"],
            "agentInstruction": "compare creative directions",
        },
        "diagnosticExtensions": {},
    }
    value.update(overrides)
    return value


def _envelope(package: dict | None = None, *, runtime_mode: str | None = None) -> dict:
    value = {
        "schema": "agent_input.agent2.v1",
        "projectionVersion": "22.5.14",
        "payload": package or _package(),
        "projectionAudit": {},
    }
    if runtime_mode:
        value["projectionAudit"] = {
            "runtimeExecution": {"executionMode": runtime_mode}
        }
    return value


def _compact(package: dict) -> dict:
    return {
        "packageId": package.get("packageId"),
        "productId": package.get("productId"),
        "storeId": package.get("storeId"),
        "immutableContext": {
            "primaryAction": "optimize creative",
            "primaryOwner": "operator",
        },
        "actionContext": {
            "actionParameterPack": package.get("actionParameterPack"),
            "verticalActionRag": package.get("verticalActionRag"),
            "metric": package.get("metric", 120),
        },
        "familyContract": {
            "contractId": "title_image_test.v1",
            "outputField": "familyPayload",
        },
    }


class Agent2FamilyPayloadSemanticCacheTest(unittest.TestCase):
    def test_semantic_hash_ignores_package_and_execution_identity(self) -> None:
        first = _package(packageId="PKG-1", dataVersion="DV-1")
        second = _package(packageId="PKG-2", dataVersion="DV-2")
        with patch.object(runtime, "_compact_package", side_effect=_compact):
            a = runtime.build_agent2_semantic_identity(
                _envelope(first),
                _descriptor(
                    packageId="PKG-1",
                    dataVersion="DV-1",
                    executionHash="EXH-1",
                    inputArtifactRef="ART-I1",
                    inputContentHash="IH-1",
                ),
                first,
            )
            b = runtime.build_agent2_semantic_identity(
                _envelope(second),
                _descriptor(
                    packageId="PKG-2",
                    dataVersion="DV-2",
                    executionHash="EXH-2",
                    inputArtifactRef="ART-I2",
                    inputContentHash="IH-2",
                ),
                second,
            )

        self.assertEqual(a["semanticHash"], b["semanticHash"])
        self.assertTrue(a["packageIdExcluded"])
        self.assertFalse(a["crossProductReuseAllowed"])

    def test_semantic_hash_changes_with_business_or_contract_semantics(self) -> None:
        baseline_package = _package(metric=120)
        changed_package = _package(metric=160)
        with patch.object(runtime, "_compact_package", side_effect=_compact):
            baseline = runtime.build_agent2_semantic_identity(
                _envelope(baseline_package),
                _descriptor(),
                baseline_package,
            )
            business_changed = runtime.build_agent2_semantic_identity(
                _envelope(changed_package),
                _descriptor(),
                changed_package,
            )
            model_changed = runtime.build_agent2_semantic_identity(
                _envelope(baseline_package),
                _descriptor(model="qwen-b"),
                baseline_package,
            )
            policy_changed = runtime.build_agent2_semantic_identity(
                _envelope(baseline_package),
                _descriptor(policyHash="policy-a2-2"),
                baseline_package,
            )

        self.assertNotEqual(baseline["semanticHash"], business_changed["semanticHash"])
        self.assertNotEqual(baseline["semanticHash"], model_changed["semanticHash"])
        self.assertNotEqual(baseline["semanticHash"], policy_changed["semanticHash"])

    def test_repair_and_regeneration_are_not_cache_eligible(self) -> None:
        repair_package = _package(
            diagnosticExtensions={
                "agent2ContractRepair": {
                    "attemptNo": 1,
                    "missing": ["agent2_title_image_full_title_missing"],
                }
            }
        )
        self.assertFalse(
            runtime._semantic_cache_eligible(_envelope(repair_package), repair_package)
        )
        regular = _package()
        self.assertFalse(
            runtime._semantic_cache_eligible(
                _envelope(
                    regular,
                    runtime_mode="provider_regeneration_after_invalid_replay",
                ),
                regular,
            )
        )
        self.assertTrue(runtime._semantic_cache_eligible(_envelope(regular), regular))

    def test_semantic_hit_skips_provider_and_completes_current_execution(self) -> None:
        envelope = _envelope()
        descriptor = _descriptor()
        entry = {
            "envelope": envelope,
            "package": envelope["payload"],
            "descriptor": descriptor,
            "claim": {},
        }
        semantic_source = {
            "execution": {"execution_hash": "execution-a2-source"},
            "outputArtifactRef": "ART-A2-SOURCE",
            "outputContentHash": "source-output-hash",
            "familyPayload": {"directions": [{"fullTitle": "A"}]},
        }
        rebound = {
            "packageId": "PKG-CURRENT",
            "productId": "P10007",
            "storeId": "STORE-A",
            "actionFamily": "title_image_test",
            "draftStatus": "draft_ready",
            "familyPayload": {"directions": [{"fullTitle": "A"}]},
            "semanticContractMissing": [],
            "semanticResultCacheHit": True,
            "cachedOutputRebound": True,
            "semanticCacheSourceExecutionHash": "execution-a2-source",
            "semanticCacheSourceOutputRef": "ART-A2-SOURCE",
            "agent2ApiCallCount": 0,
        }
        completion_calls = []

        def _complete(current_descriptor, **kwargs):
            completion_calls.append((current_descriptor, kwargs))
            return {
                "status": "accepted",
                "outputArtifactRef": kwargs["output_artifact_ref"],
                "outputContentHash": kwargs["output_content_hash"],
            }

        with (
            patch.object(runtime, "assert_agent_input_envelope", return_value={"ok": True}),
            patch.object(
                runtime,
                "provider_runtime_config",
                return_value={
                    "provider": "bailian",
                    "model": "qwen-a",
                    "thinkingEnabled": False,
                    "thinkingBudget": None,
                },
            ),
            patch.object(runtime, "_entry", return_value=entry),
            patch.object(runtime, "accepted_execution", return_value=None),
            patch.object(
                runtime,
                "claim_execution",
                return_value={"status": "claimed", "claimId": "CLAIM-A2-CURRENT"},
            ),
            patch.object(runtime, "_accepted_semantic_family_payload", return_value=semantic_source),
            patch.object(runtime, "_rebind_semantic_family_payload", return_value=rebound),
            patch.object(
                runtime,
                "_store_semantic_rebound_output",
                return_value={"artifactId": "ART-A2-CURRENT", "contentHash": "current-output-hash"},
            ),
            patch.object(runtime, "complete_execution", side_effect=_complete),
            patch.object(runtime, "_execute_batch", side_effect=AssertionError("provider must not run on semantic hit")),
        ):
            outputs, summary = runtime.run_agent2_draft_projected_inputs(
                [envelope],
                data_version="DV-CURRENT",
                max_items_per_call=5,
            )

        draft = outputs["PKG-CURRENT"]
        self.assertTrue(draft["semanticResultCacheHit"])
        self.assertTrue(draft["cachedOutputRebound"])
        self.assertEqual(draft["itemExecutionId"], "EXE-A2-CURRENT")
        self.assertEqual(draft["executionHash"], "execution-a2-current")
        self.assertEqual(draft["inputArtifactRef"], "ART-A2-INPUT-CURRENT")
        self.assertEqual(draft["outputArtifactRef"], "ART-A2-CURRENT")
        self.assertEqual(draft["agent2ApiCallCount"], 0)
        self.assertEqual(summary["actualCalls"], 0)
        self.assertEqual(summary["providerStatus"], "semantic_cache_replay")
        self.assertEqual(summary["semanticFamilyPayloadCacheHitCount"], 1)
        self.assertEqual(summary["providerBatchCount"], 0)
        self.assertEqual(len(completion_calls), 1)
        self.assertEqual(completion_calls[0][0]["executionHash"], "execution-a2-current")
        self.assertIsNone(completion_calls[0][1]["raw_batch_output_ref"])

    def test_exact_execution_replay_precedes_semantic_lookup(self) -> None:
        envelope = _envelope()
        descriptor = _descriptor()
        entry = {
            "envelope": envelope,
            "package": envelope["payload"],
            "descriptor": descriptor,
            "claim": {},
        }
        exact_replay = {
            "execution": {"raw_batch_output_ref": "ART-A2-RAW"},
            "outputArtifactRef": "ART-A2-EXACT",
            "outputContentHash": "exact-hash",
            "output": {
                "output": {
                    "packageId": "PKG-CURRENT",
                    "draftStatus": "draft_ready",
                    "familyPayload": {"directions": [{"fullTitle": "exact"}]},
                }
            },
        }
        with (
            patch.object(runtime, "assert_agent_input_envelope", return_value={"ok": True}),
            patch.object(runtime, "provider_runtime_config", return_value={"provider": "bailian", "model": "qwen-a"}),
            patch.object(runtime, "_entry", return_value=entry),
            patch.object(runtime, "accepted_execution", return_value=exact_replay),
            patch.object(runtime, "_accepted_semantic_family_payload", side_effect=AssertionError("semantic lookup must not run after exact replay")),
            patch.object(runtime, "_execute_batch", side_effect=AssertionError("provider must not run after exact replay")),
        ):
            outputs, summary = runtime.run_agent2_draft_projected_inputs(
                [envelope],
                data_version="DV-CURRENT",
            )

        self.assertEqual(outputs["PKG-CURRENT"]["outputArtifactRef"], "ART-A2-EXACT")
        self.assertTrue(outputs["PKG-CURRENT"]["exactExecutionReplay"])
        self.assertFalse(outputs["PKG-CURRENT"]["semanticResultCacheHit"])
        self.assertEqual(summary["exactExecutionReplayCount"], 1)
        self.assertEqual(summary["semanticFamilyPayloadCacheHitCount"], 0)

    def test_active_facade_maps_semantic_hit_to_existing_no_provider_proof_slot(self) -> None:
        semantic_draft = {
            "packageId": "PKG-CURRENT",
            "semanticResultCacheHit": True,
            "cachedOutputRebound": True,
            "semanticCacheSourceExecutionHash": "execution-a2-source",
        }
        with patch.object(
            active_facade,
            "_run_agent2_draft_projected_inputs_v22520",
            return_value=({"PKG-CURRENT": semantic_draft}, {"actualCalls": 0}),
        ):
            outputs, _ = active_facade.run_agent2_draft_projected_inputs([], data_version="DV")

        draft = outputs["PKG-CURRENT"]
        self.assertTrue(draft["exactExecutionReplay"])
        self.assertTrue(draft["semanticResultCacheHit"])
        self.assertFalse(draft["providerCallExecutedForCurrentResult"])
        self.assertEqual(
            draft["semanticReplayCompatibilityMode"],
            "no_provider_replay_through_existing_exactReplayValidated_slot",
        )


if __name__ == "__main__":
    unittest.main()
