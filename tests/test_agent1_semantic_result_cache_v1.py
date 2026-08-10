from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from src.services import agent_token_runtime_hash_exact_v2259_service as runtime


def _envelope(*, metric_current: int = 120, data_version: str = "DV-1") -> dict:
    lineage = {
        "version": "22.5.8",
        "status": "complete",
        "sourceVersionCount": 3,
        "sourceDatasetCount": 3,
        "businessDateCount": 3,
        "sourceRecordCount": 30,
        "sourceArtifactCount": 1,
        "contentHashVerified": True,
        "sourceIdentityComplete": True,
        "dataVersions": [data_version, "DV-0"],
        "businessDates": ["2026-07-01", "2026-07-02", "2026-07-03"],
        "sourceArtifactRefs": ["ART-SOURCE-A"],
        "sourceContentHash": "source-hash-a",
        "blockingFactors": [],
        "derivedFromImmutableLineage": True,
    }
    return {
        "schema": "agent_input.agent1.v3",
        "projectionVersion": "22.5.8",
        "sourceArtifactRefs": ["ART-SOURCE-A"],
        "sourceContentHash": "source-hash-a",
        "projectedContentHash": "not-used-by-semantic-builder",
        "payload": {
            "productId": "P10007",
            "storeId": "STORE-A",
            "signalId": "SIG-A",
            "correlationId": "ITEM-A",
            "dataVersion": data_version,
            "productIdentity": {
                "productId": "P10007",
                "storeId": "STORE-A",
                "platform": "tb",
                "verticalCategory": "beauty",
            },
            "profileLayer": {
                "productId": "P10007",
                "storeId": "STORE-A",
                "platform": "tb",
                "verticalCategory": "beauty",
            },
            "snapshotLayer": {
                "fieldSignals": [
                    {
                        "metricCode": "gmv",
                        "previous": 100,
                        "current": metric_current,
                        "changeRatio": (metric_current - 100) / 100,
                    }
                ],
                "fieldSignalCount": 1,
                "semanticContinuity": True,
            },
            "metricLayer": {"gmv": metric_current},
            "trendContext": {
                "businessDates": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "trendSemanticVersion": "22.5.8",
            },
            "sourceLineageValidation": lineage,
            "crossValidation": {"metricConsistent": True},
            "factLayerValidation": {"passed": True},
            "dataFingerprint": "business-fingerprint-1",
            "diagnosticRag": {"policy": "diagnose-before-action"},
            "inputContract": {
                "schema": "agent_input.agent1.v3",
                "version": "22.5.8",
                "projectionVersion": "22.5.8",
                "sourceRef": "ART-SOURCE-A",
                "sourceContentHash": "source-hash-a",
                "sourceLineageHash": "lineage-hash-a",
                "trendSemanticVersion": "22.5.8",
                "policyContextHash": "policy-hash-1",
                "semanticContinuity": True,
                "completeFieldSignalTransport": True,
                "trendContextTransport": True,
                "sourceLineageTransport": True,
                "fallbackAllowed": False,
                "fullSignalReadAllowed": False,
            },
        },
        "projectionAudit": {"projectedChars": 5000},
        "hardInterface": {"enabled": True, "fallbackAllowed": False},
    }


def _descriptor(**overrides) -> dict:
    value = {
        "stage": "product_judgment_agent",
        "itemExecutionId": "EXE-CURRENT",
        "executionHash": "execution-current",
        "inputArtifactRef": "ART-INPUT-CURRENT",
        "inputContentHash": "input-hash-current",
        "inputSchema": "agent_input.agent1.v3",
        "projectionVersion": "22.5.8",
        "promptVersion": "22.5.9",
        "policyHash": "policy-hash-1",
        "provider": "bailian",
        "model": "qwen-model-a",
        "generationParametersHash": "generation-hash-1",
        "tenantId": "TENANT-A",
        "storeId": "STORE-A",
        "productId": "P10007",
        "dataVersion": "DV-1",
    }
    value.update(overrides)
    return value


class Agent1SemanticResultCacheTest(unittest.TestCase):
    def test_semantic_hash_ignores_execution_transport_identity(self) -> None:
        first = _envelope(data_version="DV-1")
        second = copy.deepcopy(first)
        second["sourceArtifactRefs"] = ["ART-SOURCE-B"]
        second["sourceContentHash"] = "source-hash-b"
        second["payload"]["correlationId"] = "ITEM-B"
        second["payload"]["signalId"] = "SIG-B"
        second["payload"]["dataVersion"] = "DV-2"
        second["payload"]["sourceLineageValidation"]["dataVersions"] = ["DV-2", "DV-1"]
        second["payload"]["sourceLineageValidation"]["sourceArtifactRefs"] = ["ART-SOURCE-B"]
        second["payload"]["sourceLineageValidation"]["sourceContentHash"] = "source-hash-b"
        second["payload"]["inputContract"]["sourceRef"] = "ART-SOURCE-B"
        second["payload"]["inputContract"]["sourceContentHash"] = "source-hash-b"
        second["payload"]["inputContract"]["sourceLineageHash"] = "lineage-hash-b"

        first_identity = runtime.build_agent1_semantic_identity(first, _descriptor())
        second_identity = runtime.build_agent1_semantic_identity(
            second,
            _descriptor(
                itemExecutionId="EXE-SECOND",
                executionHash="execution-second",
                inputArtifactRef="ART-INPUT-SECOND",
                inputContentHash="input-hash-second",
                dataVersion="DV-2",
            ),
        )

        self.assertEqual(first_identity["semanticHash"], second_identity["semanticHash"])
        self.assertNotEqual("execution-current", "execution-second")
        self.assertFalse(first_identity["crossProductReuseAllowed"])

    def test_semantic_hash_changes_when_business_metric_changes(self) -> None:
        first = runtime.build_agent1_semantic_identity(
            _envelope(metric_current=120),
            _descriptor(),
        )
        second = runtime.build_agent1_semantic_identity(
            _envelope(metric_current=150),
            _descriptor(),
        )

        self.assertNotEqual(first["semanticInputHash"], second["semanticInputHash"])
        self.assertNotEqual(first["semanticHash"], second["semanticHash"])

    def test_semantic_hash_changes_with_runtime_contract(self) -> None:
        envelope = _envelope()
        baseline = runtime.build_agent1_semantic_identity(envelope, _descriptor())
        model_changed = runtime.build_agent1_semantic_identity(
            envelope,
            _descriptor(model="qwen-model-b"),
        )
        policy_changed = runtime.build_agent1_semantic_identity(
            envelope,
            _descriptor(policyHash="policy-hash-2"),
        )
        generation_changed = runtime.build_agent1_semantic_identity(
            envelope,
            _descriptor(generationParametersHash="generation-hash-2"),
        )

        self.assertNotEqual(baseline["semanticHash"], model_changed["semanticHash"])
        self.assertNotEqual(baseline["semanticHash"], policy_changed["semanticHash"])
        self.assertNotEqual(baseline["semanticHash"], generation_changed["semanticHash"])

    def test_rebind_uses_current_exact_identity_and_keeps_business_body(self) -> None:
        cached = {
            "dataVersion": "DV-OLD",
            "correlationId": "ITEM-OLD",
            "productId": "P10007",
            "storeId": "STORE-A",
            "signalId": "SIG-OLD",
            "itemExecutionId": "EXE-OLD",
            "executionHash": "execution-old",
            "inputArtifactRef": "ART-INPUT-OLD",
            "inputContentHash": "input-hash-old",
            "outputArtifactRef": "ART-OUT-OLD",
            "rawBatchOutputRef": "ART-RAW-OLD",
            "signal": {"dataVersion": "DV-OLD"},
            "agent1ApiCallCount": 1,
            "decisionType": "act",
            "finding": "GMV increased and stock pressure requires action",
            "selectedActionFamilyHint": "inventory_replenishment",
            "agent1DecisionIR": {"decisionType": "act", "coreProblem": "stock pressure"},
            "identityResolution": {"mode": "old"},
        }
        descriptor = _descriptor(
            semanticHash="semantic-1",
            itemExecutionId="EXE-NEW",
            executionHash="execution-new",
            inputArtifactRef="ART-INPUT-NEW",
            inputContentHash="input-hash-new",
            dataVersion="DV-NEW",
        )
        product = {
            "dataVersion": "DV-NEW",
            "correlationId": "ITEM-NEW",
            "productId": "P10007",
            "storeId": "STORE-A",
            "signalId": "SIG-NEW",
            "metricLayer": {"gmv": 120},
        }
        source = {
            "execution": {"execution_hash": "execution-old"},
            "outputArtifactRef": "ART-OUT-OLD",
        }

        rebound = runtime._rebind_semantic_output(
            cached,
            descriptor=descriptor,
            product=product,
            source=source,
        )

        self.assertEqual(rebound["finding"], cached["finding"])
        self.assertEqual(rebound["decisionType"], "act")
        self.assertEqual(rebound["itemExecutionId"], "EXE-NEW")
        self.assertEqual(rebound["executionHash"], "execution-new")
        self.assertEqual(rebound["inputArtifactRef"], "ART-INPUT-NEW")
        self.assertEqual(rebound["inputContentHash"], "input-hash-new")
        self.assertEqual(rebound["correlationId"], "ITEM-NEW")
        self.assertEqual(rebound["signalId"], "SIG-NEW")
        self.assertEqual(rebound["signal"], product)
        self.assertEqual(rebound["agent1ApiCallCount"], 0)
        self.assertTrue(rebound["semanticResultCacheHit"])
        self.assertTrue(rebound["cachedOutputRebound"])
        self.assertEqual(rebound["semanticCacheSourceExecutionHash"], "execution-old")
        self.assertEqual(rebound["semanticCacheSourceOutputRef"], "ART-OUT-OLD")

    def test_semantic_hit_skips_provider_and_accepts_current_execution(self) -> None:
        envelope = _envelope(data_version="DV-NEW")
        product = {
            "dataVersion": "DV-NEW",
            "correlationId": "ITEM-NEW",
            "productId": "P10007",
            "storeId": "STORE-A",
            "signalId": "SIG-NEW",
            "metricLayer": {"gmv": 120},
        }
        descriptor = _descriptor(
            itemExecutionId="EXE-NEW",
            executionHash="execution-new",
            inputArtifactRef="ART-INPUT-NEW",
            inputContentHash="input-hash-new",
            dataVersion="DV-NEW",
            semanticHash="semantic-1",
            semanticInputHash="semantic-input-1",
            semanticContractHash="semantic-contract-1",
            semanticCacheContractVersion=runtime.AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        )
        entry = {
            "envelope": envelope,
            "product": product,
            "descriptor": descriptor,
            "claim": {},
        }
        semantic_source = {
            "execution": {"execution_hash": "execution-old"},
            "outputArtifactRef": "ART-OUT-OLD",
            "outputContentHash": "old-output-hash",
            "output": {
                "output": {
                    "dataVersion": "DV-OLD",
                    "correlationId": "ITEM-OLD",
                    "productId": "P10007",
                    "storeId": "STORE-A",
                    "signalId": "SIG-OLD",
                    "itemExecutionId": "EXE-OLD",
                    "executionHash": "execution-old",
                    "inputArtifactRef": "ART-INPUT-OLD",
                    "inputContentHash": "input-hash-old",
                    "decisionType": "observe",
                    "decisionHint": "observe_only",
                    "finding": "semantic finding",
                    "agent1ApiCallCount": 1,
                }
            },
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
                    "model": "qwen-model-a",
                    "thinkingEnabled": False,
                    "thinkingBudget": None,
                },
            ),
            patch.object(runtime, "_entry", return_value=entry),
            patch.object(runtime, "accepted_execution", return_value=None),
            patch.object(runtime, "claim_execution", return_value={"status": "claimed", "claimId": "CLAIM-NEW"}),
            patch.object(runtime, "_accepted_semantic_execution", return_value=semantic_source),
            patch.object(
                runtime,
                "_store_semantic_rebound_output",
                return_value={"artifactId": "ART-OUT-NEW", "contentHash": "new-output-hash"},
            ),
            patch.object(runtime, "complete_execution", side_effect=_complete),
            patch.object(runtime, "_provider_batch", side_effect=AssertionError("provider must not be called on semantic hit")),
        ):
            judgments, summary = runtime.run_agent1_projected_inputs(
                [envelope],
                data_version="DV-NEW",
                max_items_per_call=8,
            )

        self.assertEqual(len(judgments), 1)
        self.assertEqual(judgments[0]["itemExecutionId"], "EXE-NEW")
        self.assertEqual(judgments[0]["executionHash"], "execution-new")
        self.assertEqual(judgments[0]["outputArtifactRef"], "ART-OUT-NEW")
        self.assertEqual(judgments[0]["agent1ApiCallCount"], 0)
        self.assertTrue(judgments[0]["semanticResultCacheHit"])
        self.assertTrue(judgments[0]["cachedOutputRebound"])
        self.assertEqual(summary["actualCalls"], 0)
        self.assertEqual(summary["providerStatus"], "semantic_cache_replay")
        self.assertEqual(summary["semanticResultCacheHitCount"], 1)
        self.assertEqual(summary["semanticResultCacheMissCount"], 0)
        self.assertEqual(summary["providerBatchCount"], 0)
        self.assertEqual(len(completion_calls), 1)
        self.assertEqual(completion_calls[0][0]["executionHash"], "execution-new")
        self.assertEqual(completion_calls[0][1]["raw_batch_output_ref"], None)

    def test_exact_execution_replay_has_priority_over_semantic_lookup(self) -> None:
        envelope = _envelope()
        descriptor = _descriptor(
            semanticHash="semantic-1",
            semanticCacheContractVersion=runtime.AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        )
        entry = {
            "envelope": envelope,
            "product": envelope["payload"],
            "descriptor": descriptor,
            "claim": {},
        }
        exact_replay = {
            "execution": {"raw_batch_output_ref": "ART-RAW-EXACT"},
            "outputArtifactRef": "ART-OUT-EXACT",
            "outputContentHash": "exact-output-hash",
            "output": {
                "output": {
                    "decisionType": "observe",
                    "finding": "exact replay",
                    "itemExecutionId": "EXE-CURRENT",
                    "inputContentHash": "input-hash-current",
                }
            },
        }

        with (
            patch.object(runtime, "assert_agent_input_envelope", return_value={"ok": True}),
            patch.object(runtime, "provider_runtime_config", return_value={"provider": "bailian", "model": "qwen-model-a"}),
            patch.object(runtime, "_entry", return_value=entry),
            patch.object(runtime, "accepted_execution", return_value=exact_replay),
            patch.object(runtime, "_accepted_semantic_execution", side_effect=AssertionError("semantic lookup must not run after exact replay")),
            patch.object(runtime, "_provider_batch", side_effect=AssertionError("provider must not run after exact replay")),
        ):
            judgments, summary = runtime.run_agent1_projected_inputs(
                [envelope],
                data_version="DV-1",
            )

        self.assertEqual(len(judgments), 1)
        self.assertEqual(judgments[0]["resultOrigin"], "accepted_execution_artifact")
        self.assertFalse(judgments[0]["cachedOutputRebound"])
        self.assertEqual(summary["acceptedExecutionReplayCount"], 1)
        self.assertEqual(summary["semanticResultCacheHitCount"], 0)
        self.assertEqual(summary["providerBatchCount"], 0)


if __name__ == "__main__":
    unittest.main()
