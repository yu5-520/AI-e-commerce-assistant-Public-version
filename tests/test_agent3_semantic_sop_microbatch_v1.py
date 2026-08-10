from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services import agent3_runtime_v23215_service as runtime
from src.services import agent_runtime_contract_v225_service as runtime_contract
from src.services import agent_token_runtime_v225_service as active_facade


def _package(package_id: str = "PKG-1", *, metric: int = 120, company_case: str = "CASE-1") -> dict:
    return {
        "packageId": package_id,
        "dataVersion": "DV-1",
        "productId": "P10007",
        "storeId": "STORE-A",
        "lockedActionFamily": "title_image_test",
        "agent2ActionDraft": {
            "draftStatus": "draft_ready",
            "actionFamily": "title_image_test",
            "familyPayload": {"metric": metric},
        },
        "companyOperatingPolicySnapshot": {"managementStyle": "evidence-first"},
        "companySopRagSnapshot": {"approvedCaseIds": [company_case]},
        "approvalPolicySnapshot": {"approvalRequiredAbove": 1000},
        "brandStyleSnapshot": {"tone": "direct"},
        "inputContract": {"schema": "agent_input.agent3_sop.v1"},
    }


def _envelope(package: dict) -> dict:
    return {
        "schema": "agent_input.agent3_sop.v1",
        "projectionVersion": "22.5.0",
        "payload": package,
    }


def _descriptor(**overrides) -> dict:
    value = {
        "stage": runtime.AGENT3_STAGE,
        "itemExecutionId": "EXE-A3-CURRENT",
        "executionHash": "execution-a3-current",
        "inputArtifactRef": "ART-A3-INPUT-CURRENT",
        "inputContentHash": "input-a3-current",
        "inputSchema": "agent_input.agent3_sop.v1",
        "projectionVersion": "22.5.0",
        "promptVersion": "23.2.15",
        "policyHash": "policy-a3-1",
        "provider": "bailian",
        "model": "qwen-a",
        "generationParametersHash": "generation-a3-1",
        "storeId": "STORE-A",
        "productId": "P10007",
        "dataVersion": "DV-CURRENT",
        "semanticHash": "semantic-a3-1",
        "semanticCacheEligible": True,
        "batchCompatibilityHash": "compat-a3-1",
    }
    value.update(overrides)
    return value


def _compiled(package: dict) -> dict:
    return {
        "packageId": package.get("packageId"),
        "dataVersion": package.get("dataVersion"),
        "productId": package.get("productId"),
        "storeId": package.get("storeId"),
        "lockedActionFamily": package.get("lockedActionFamily"),
        "actionSources": {
            "agent2ActionDraft": package.get("agent2ActionDraft"),
        },
        "companyContext": {
            "approvedCaseIds": package.get("companySopRagSnapshot", {}).get("approvedCaseIds"),
            "managementStyle": package.get("companyOperatingPolicySnapshot", {}).get("managementStyle"),
            "brandStyle": package.get("brandStyleSnapshot"),
            "approvalPolicy": package.get("approvalPolicySnapshot"),
        },
        "allowedActionTypes": ["creative_brief", "result_review"],
        "requiredActionTypeGroups": [["creative_brief"], ["result_review"]],
        "forbiddenActions": ["inventory_coordination"],
        "outputStepContract": {"minExecutionSteps": 3},
        "allowedStopConditionTypes": ["metric_guardrail"],
        "allowedRollbackConditionTypes": ["restore_previous_asset"],
        "auxiliaryConditionContract": {"structured": True},
        "systemConstraintContract": {"version": "23.2.15"},
    }


class Agent3SemanticSopMicrobatchTest(unittest.TestCase):
    def test_semantic_hash_ignores_package_and_execution_identity(self) -> None:
        first = _package("PKG-1")
        second = _package("PKG-2")
        second["dataVersion"] = "DV-2"
        with patch.object(runtime.core, "compile_agent3_provider_package", side_effect=_compiled):
            a = runtime.build_agent3_semantic_identity(
                _envelope(first),
                _descriptor(
                    executionHash="EXH-1",
                    inputArtifactRef="ART-I1",
                    inputContentHash="IH-1",
                    dataVersion="DV-1",
                ),
                first,
            )
            b = runtime.build_agent3_semantic_identity(
                _envelope(second),
                _descriptor(
                    executionHash="EXH-2",
                    inputArtifactRef="ART-I2",
                    inputContentHash="IH-2",
                    dataVersion="DV-2",
                ),
                second,
            )
        self.assertEqual(a["semanticHash"], b["semanticHash"])
        self.assertTrue(a["packageAndExecutionIdentityExcluded"])
        self.assertFalse(a["crossProductReuseAllowed"])

    def test_business_rag_and_model_changes_invalidate_semantic_hash(self) -> None:
        baseline = _package(metric=120, company_case="CASE-1")
        metric_changed = _package(metric=160, company_case="CASE-1")
        rag_changed = _package(metric=120, company_case="CASE-2")
        with patch.object(runtime.core, "compile_agent3_provider_package", side_effect=_compiled):
            base = runtime.build_agent3_semantic_identity(_envelope(baseline), _descriptor(), baseline)
            metric = runtime.build_agent3_semantic_identity(
                _envelope(metric_changed), _descriptor(), metric_changed
            )
            rag = runtime.build_agent3_semantic_identity(_envelope(rag_changed), _descriptor(), rag_changed)
            model = runtime.build_agent3_semantic_identity(
                _envelope(baseline), _descriptor(model="qwen-b"), baseline
            )
        self.assertNotEqual(base["semanticHash"], metric["semanticHash"])
        self.assertNotEqual(base["semanticHash"], rag["semanticHash"])
        self.assertNotEqual(base["semanticHash"], model["semanticHash"])

    def test_two_compatible_items_share_one_initial_provider_call(self) -> None:
        packages = [_package("PKG-1"), _package("PKG-2")]
        envelopes = [_envelope(item) for item in packages]
        provider_calls = []

        def _call_json(**kwargs):
            provider_calls.append(kwargs)
            return (
                {
                    "sops": [
                        {"packageId": "PKG-1", "sopStatus": "sop_ready"},
                        {"packageId": "PKG-2", "sopStatus": "sop_ready"},
                    ]
                },
                {
                    "provider": "bailian",
                    "model": "qwen-a",
                    "providerRequestId": "REQ-A3-BATCH",
                    "providerCallExecuted": True,
                    "input": 100,
                    "output": 40,
                    "reasoningTokens": 0,
                    "inputFingerprint": "fp-a3-batch",
                },
            )

        def _normalized(raw, package, proof):
            return {
                "packageId": package["packageId"],
                "productId": package["productId"],
                "storeId": package["storeId"],
                "actionFamily": package["lockedActionFamily"],
                "sopStatus": "sop_ready",
                "semanticContractMissing": [],
                "contractValidation": {"passed": True, "missing": [], "repairableAuxiliaryOnly": False},
                "agent3ExecutionProof": proof,
            }

        with (
            patch.object(runtime, "assert_agent_input_envelope", side_effect=lambda value, **_: value),
            patch.object(runtime, "split_envelopes_by_budget", return_value=[envelopes]),
            patch.object(runtime, "_provider_compatibility_material", return_value={"family": "title_image_test"}),
            patch.object(runtime.core, "_build_messages", return_value=([{"role": "user", "content": "{}"}], {"packages": packages})),
            patch.object(runtime, "call_json", side_effect=_call_json),
            patch.object(runtime.core, "_normalize_sop", side_effect=_normalized),
            patch.object(runtime.core, "repairable_agent3_auxiliary_missing", return_value=[]),
        ):
            outputs, summary = runtime.run_agent3_sop_provider_isolated(
                envelopes,
                data_version="DV-1",
                max_items_per_call=2,
            )

        self.assertEqual(len(provider_calls), 1)
        self.assertEqual(set(outputs), {"PKG-1", "PKG-2"})
        self.assertEqual(summary["actualCalls"], 1)
        self.assertEqual(summary["initialProviderBatchCount"], 1)
        self.assertTrue(summary["dynamicMicrobatchEnabled"])

    def test_semantic_hit_skips_provider_and_creates_current_execution_output(self) -> None:
        package = _package("PKG-CURRENT")
        envelope = _envelope(package)
        descriptor = _descriptor()
        entry = {
            "envelope": envelope,
            "package": package,
            "descriptor": descriptor,
            "claim": {},
        }
        source = {
            "execution": {"execution_hash": "execution-a3-source"},
            "outputArtifactRef": "ART-A3-SOURCE",
            "sop": {"sopStatus": "sop_ready"},
        }
        proof = runtime._semantic_execution_proof(
            descriptor,
            package_id="PKG-CURRENT",
            source_execution_hash="execution-a3-source",
            source_output_ref="ART-A3-SOURCE",
        )
        rebound = {
            "packageId": "PKG-CURRENT",
            "productId": "P10007",
            "storeId": "STORE-A",
            "actionFamily": "title_image_test",
            "sopStatus": "sop_ready",
            "semanticContractMissing": [],
            "contractValidation": {"passed": True, "missing": []},
            "agent3ExecutionProof": proof,
            "semanticResultCacheHit": True,
            "semanticReplayValidated": True,
            "semanticCacheSourceExecutionHash": "execution-a3-source",
            "semanticCacheSourceOutputRef": "ART-A3-SOURCE",
            "agent3ApiCallCount": 0,
        }
        complete_calls = []

        def _complete(current_descriptor, **kwargs):
            complete_calls.append((current_descriptor, kwargs))
            return {
                "outputArtifactRef": kwargs["output_artifact_ref"],
                "outputContentHash": kwargs["output_content_hash"],
            }

        with (
            patch.object(runtime, "assert_agent_input_envelope", side_effect=lambda value, **_: value),
            patch.object(runtime, "_entry", return_value=entry),
            patch.object(runtime.hash_runtime, "accepted_execution", return_value=None),
            patch.object(runtime.hash_runtime, "claim_execution", return_value={"status": "claimed", "claimId": "CLAIM-A3"}),
            patch.object(runtime, "_accepted_semantic_sop", return_value=source),
            patch.object(runtime, "_rebind_semantic_sop", return_value=rebound),
            patch.object(runtime, "_store_semantic_rebound_output", return_value={"artifactId": "ART-A3-CURRENT", "contentHash": "current-hash"}),
            patch.object(runtime.hash_runtime, "complete_execution", side_effect=_complete),
            patch.object(
                runtime.hash_runtime,
                "_decorate_output",
                side_effect=lambda output, **kwargs: {
                    **output,
                    "itemExecutionId": descriptor["itemExecutionId"],
                    "executionHash": descriptor["executionHash"],
                    "inputArtifactRef": descriptor["inputArtifactRef"],
                    "inputContentHash": descriptor["inputContentHash"],
                    "outputArtifactRef": kwargs["output_artifact_ref"],
                    "outputContentHash": kwargs["output_content_hash"],
                },
            ),
            patch.object(runtime, "run_agent3_sop_provider_isolated", side_effect=AssertionError("provider must not run on semantic hit")),
        ):
            outputs, summary = runtime.run_agent3_sop_projected_inputs(
                [envelope],
                data_version="DV-CURRENT",
                max_items_per_call=2,
            )

        sop = outputs["PKG-CURRENT"]
        self.assertTrue(sop["semanticResultCacheHit"])
        self.assertTrue(sop["semanticReplayValidated"])
        self.assertEqual(sop["executionHash"], "execution-a3-current")
        self.assertEqual(sop["outputArtifactRef"], "ART-A3-CURRENT")
        self.assertEqual(sop["agent3ApiCallCount"], 0)
        self.assertEqual(summary["actualCalls"], 0)
        self.assertEqual(summary["providerStatus"], "semantic_cache_replay")
        self.assertEqual(summary["semanticSopCacheHitCount"], 1)
        self.assertEqual(summary["providerBatchCount"], 0)
        self.assertEqual(len(complete_calls), 1)

    def test_agent3_contract_accepts_semantic_replay_without_faking_provider(self) -> None:
        proof = {
            "resultMatched": True,
            "providerCallExecuted": False,
            "providerRequestId": None,
            "exactReplayValidated": False,
            "semanticReplayValidated": True,
            "semanticCallId": "A3CALL-SEMANTIC",
            "fallbackUsed": False,
            "passed": True,
        }
        self.assertTrue(runtime_contract._valid_agent3_execution_proof(proof))
        self.assertFalse(proof["providerCallExecuted"])

    def test_active_facade_promotes_legacy_singleton_sentinel_to_two(self) -> None:
        captured = {}

        def _run(envelopes, *args, **kwargs):
            captured.update(kwargs)
            return {}, {"actualCalls": 0}

        with patch.object(active_facade, "_run_agent3_sop_projected_inputs_v23217", side_effect=_run):
            active_facade.run_agent3_sop_projected_inputs(
                [{"payload": {"packageId": "PKG-1"}}, {"payload": {"packageId": "PKG-2"}}],
                data_version="DV-1",
                max_items_per_call=1,
            )
        self.assertEqual(captured["max_items_per_call"], 2)


if __name__ == "__main__":
    unittest.main()
