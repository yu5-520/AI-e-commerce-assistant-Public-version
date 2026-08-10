from __future__ import annotations

import unittest
from unittest.mock import patch


class Agent1ExactHashNativeNormalizerTests(unittest.TestCase):
    def _product(self, *, execution_id: str = "EXE-P10007-TB", content_hash: str = "sha256:INPUT-P10007-TB") -> dict:
        return {
            "dataVersion": "DV-20260810",
            "correlationId": "TB-SH-001:P10007:PSIG-TEST",
            "storeId": "TB-SH-001",
            "productId": "P10007",
            "signalId": "PSIG-TEST",
            "_hashExecution": {
                "itemExecutionId": execution_id,
                "executionHash": "sha256:EXEC-P10007-TB",
                "inputArtifactRef": "ART-INPUT-P10007-TB",
                "inputContentHash": content_hash,
                "inputSchema": "agent_input.agent1.v3",
                "projectionVersion": "22.5.8",
            },
        }

    def _raw_act(self, *, execution_id: str = "EXE-P10007-TB", content_hash: str = "sha256:INPUT-P10007-TB") -> dict:
        return {
            "itemExecutionId": execution_id,
            "inputContentHash": content_hash,
            # Deliberately wrong human-readable identity. Exact hash identity must own
            # the binding and project canonical identity from the input Artifact.
            "correlationId": "WRONG:IDENTITY",
            "storeId": "WRONG-STORE",
            "productId": "WRONG-PRODUCT",
            "signalId": "WRONG-SIGNAL",
            "metricCode": "inventory_pressure",
            "severity": "high",
            "confidence": 0.91,
            "decisionType": "act",
            "decisionHint": "action_candidate",
            "finding": "库存承接能力不足，需要跨部门补货协同。",
            "coreProblem": "库存承接不足",
            "facts": [{"factRef": "F1", "role": "evidence", "text": "库存压力持续上升"}],
            "decisionSummary": "建立补货协同动作。",
            "selectedOperatingRoute": "supply_chain_urgency",
            "selectedActionFamilyHint": "inventory_replenishment",
            "evidenceStatus": "sufficient",
            "primaryProblemNode": "库存承接不足",
            "primaryAction": "补货协同",
            "primaryExecutionTarget": {
                "targetType": "inventory_plan",
                "targetId": "P10007",
                "owner": "warehouse",
            },
            "primaryOwner": "warehouse",
            "decisiveFacts": ["库存压力持续上升"],
            "supportingCoordination": ["运营提供销量趋势"],
            "missingEvidence": [],
        }

    def test_exact_hash_match_survives_unknown_legacy_action_family(self) -> None:
        from src.services import real_product_judgment_agent_v2259_service as core

        product = self._product()
        raw = self._raw_act()

        # The legacy full normalizer is the historical breakpoint. It must not be
        # invoked after exact execution identity has already matched.
        with patch.object(
            core.legacy,
            "_normalize_judgments",
            side_effect=AssertionError("legacy full normalizer must not own exact-hash result"),
        ):
            normalized, diagnostics = core._normalize_judgments(
                {"judgments": [raw]},
                [product],
                "DV-20260810",
            )

        self.assertEqual(len(normalized), 1)
        item = normalized[0]
        self.assertEqual(item["itemExecutionId"], "EXE-P10007-TB")
        self.assertEqual(item["inputContentHash"], "sha256:INPUT-P10007-TB")
        self.assertEqual(item["storeId"], "TB-SH-001")
        self.assertEqual(item["productId"], "P10007")
        self.assertEqual(item["signalId"], "PSIG-TEST")
        self.assertEqual(item["selectedActionFamilyHint"], "inventory_replenishment")
        self.assertEqual(item["selectedOperatingRoute"], "supply_chain_urgency")
        self.assertEqual(item["decisionType"], "act")
        self.assertTrue(item["taskAdmissionAllowed"])
        self.assertTrue(item["hashIdentityMatched"])
        self.assertFalse(item["fallbackIdentityMatchingUsed"])
        self.assertFalse(item["legacyActionFamilyWhitelistAllowed"])
        self.assertEqual(item["identityResolution"]["mode"], "itemExecutionId+inputContentHash")

        self.assertEqual(diagnostics["exactHashMatchedCount"], 1)
        self.assertEqual(diagnostics["normalizedJudgmentCount"], 1)
        self.assertEqual(diagnostics["missingItemExecutionIds"], [])
        self.assertFalse(diagnostics["legacyIdentityRematchAllowed"])
        self.assertFalse(diagnostics["legacyActionFamilyWhitelistAllowed"])
        self.assertFalse(diagnostics["legacyNormalizerDeletionAllowed"])

    def test_hash_mismatch_still_fails_closed_before_business_normalization(self) -> None:
        from src.services import real_product_judgment_agent_v2259_service as core

        product = self._product()
        raw = self._raw_act(content_hash="sha256:WRONG-HASH")
        normalized, diagnostics = core._normalize_judgments(
            {"judgments": [raw]},
            [product],
            "DV-20260810",
        )

        self.assertEqual(normalized, [])
        self.assertEqual(diagnostics["exactHashMatchedCount"], 0)
        self.assertEqual(diagnostics["missingItemExecutionIds"], ["EXE-P10007-TB"])
        self.assertEqual(len(diagnostics["inputContentHashMismatches"]), 1)
        mismatch = diagnostics["inputContentHashMismatches"][0]
        self.assertEqual(mismatch["itemExecutionId"], "EXE-P10007-TB")
        self.assertEqual(mismatch["expectedInputContentHash"], "sha256:INPUT-P10007-TB")
        self.assertEqual(mismatch["returnedInputContentHash"], "sha256:WRONG-HASH")

    def test_decision_alias_can_be_canonicalized_without_reowning_identity(self) -> None:
        from src.services import real_product_judgment_agent_v2259_service as core

        product = self._product()
        raw = self._raw_act()
        raw["decisionType"] = "action"

        normalized, diagnostics = core._normalize_judgments(
            {"judgments": [raw]},
            [product],
            "DV-20260810",
        )

        self.assertEqual(len(normalized), 1)
        item = normalized[0]
        self.assertEqual(item["decisionType"], "act")
        self.assertEqual(item["rawDecisionType"], "action")
        self.assertIn("decision_alias_normalized:action->act", item["normalizationWarnings"])
        self.assertEqual(diagnostics["decisionAliasNormalizedCount"], 1)
        self.assertEqual(item["storeId"], "TB-SH-001")
        self.assertEqual(item["productId"], "P10007")

    def test_same_product_three_stores_remain_three_exact_execution_items(self) -> None:
        from src.services import real_product_judgment_agent_v2259_service as core

        products = []
        raws = []
        for suffix, store in (("TB", "TB-SH-001"), ("JD", "JD-SH-002"), ("DY", "DY-SH-003")):
            execution_id = f"EXE-P10007-{suffix}"
            content_hash = f"sha256:INPUT-P10007-{suffix}"
            product = self._product(execution_id=execution_id, content_hash=content_hash)
            product["storeId"] = store
            product["correlationId"] = f"{store}:P10007:PSIG-{suffix}"
            product["signalId"] = f"PSIG-{suffix}"
            raw = self._raw_act(execution_id=execution_id, content_hash=content_hash)
            raw["selectedActionFamilyHint"] = f"future_capability_{suffix.lower()}"
            raws.append(raw)
            products.append(product)

        normalized, diagnostics = core._normalize_judgments(
            {"judgments": list(reversed(raws))},
            products,
            "DV-20260810",
        )

        self.assertEqual(len(normalized), 3)
        by_execution = {item["itemExecutionId"]: item for item in normalized}
        self.assertEqual(by_execution["EXE-P10007-TB"]["storeId"], "TB-SH-001")
        self.assertEqual(by_execution["EXE-P10007-JD"]["storeId"], "JD-SH-002")
        self.assertEqual(by_execution["EXE-P10007-DY"]["storeId"], "DY-SH-003")
        self.assertEqual(diagnostics["exactHashMatchedCount"], 3)
        self.assertEqual(diagnostics["normalizedJudgmentCount"], 3)
        self.assertEqual(diagnostics["missingItemExecutionIds"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
