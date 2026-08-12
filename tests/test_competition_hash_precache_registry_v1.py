from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.services.competition_hash_precache_registry_v1_service import (
    build_pre_agent_hashes,
    canonical_semantic_hash,
    semantic_projection,
)


ROOT = Path(__file__).resolve().parents[1]


def _snapshot(data_version: str, amount: float, strict_suffix: str) -> dict:
    return {
        "snapshotId": f"SNAP-{strict_suffix}",
        "dataVersion": data_version,
        "setSnapshotHash": f"sha256:set-{strict_suffix}",
        "products": [
            {
                "objectId": "tmall::S1::P10001::SKU1",
                "productId": "P10001",
                "storeId": "S1",
                "productSnapshotHash": f"sha256:product-{strict_suffix}",
                "snapshotHash": f"sha256:product-{strict_suffix}",
                "profileSnapshot": {
                    "objectId": "tmall::S1::P10001::SKU1",
                    "productId": "P10001",
                    "storeId": "S1",
                    "skuId": "SKU1",
                    "platform": "tmall",
                    "title": "same product",
                    "metricDate": "2026-07-02",
                    "permissionStampId": f"PERM-{strict_suffix}",
                },
                "metricSnapshot": {
                    "productId": "P10001",
                    "storeId": "S1",
                    "paymentAmount": amount,
                    "roas": 3.2,
                    "metricDate": "2026-07-02",
                    "sourceDataVersions": [data_version],
                    "sourceContentHash": f"sha256:source-{strict_suffix}",
                    "metricFacts": [
                        {
                            "factId": f"FACT-{strict_suffix}",
                            "metricFactId": f"MF-{strict_suffix}",
                            "sourceRowId": f"ROW-{strict_suffix}",
                            "sourceHash": f"sha256:row-{strict_suffix}",
                            "metricName": "paymentAmount",
                            "value": amount,
                            "level": "product",
                        }
                    ],
                },
                "sourceDataVersions": [data_version],
                "sourceReportRefs": [f"ART-REPORT-{strict_suffix}"],
                "sourceRef": f"runtime-source:{strict_suffix}",
                "factRefs": [f"FACT-{strict_suffix}"],
                "factHashRefs": [f"sha256:fact-{strict_suffix}"],
                "metricLineage": [
                    {
                        "factId": f"FACT-{strict_suffix}",
                        "sourceRowId": f"ROW-{strict_suffix}",
                        "factHash": f"sha256:fact-{strict_suffix}",
                        "metricName": "paymentAmount",
                    }
                ],
            }
        ],
    }


class CompetitionHashPrecacheRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = {
            "canonicalProductSnapshotSchemaVersion": "canonicalProductSnapshot.v1",
            "evidenceInputContract": "competition.evidenceInput.v1",
            "evidenceContractVersion": "21.5.0",
            "admissionPolicyVersion": "artifact_signal_admission_v225:22.5.12",
            "hashPrecacheRegistryVersion": "1.0.0",
        }

    def test_execution_and_import_identity_do_not_change_business_semantic_hash(self) -> None:
        first = _snapshot("DV-A", 100.0, "A")
        second = _snapshot("DV-B", 100.0, "B")
        self.assertEqual(
            canonical_semantic_hash(first, contract=self.contract),
            canonical_semantic_hash(second, contract=self.contract),
        )

    def test_business_metric_change_changes_semantic_hash(self) -> None:
        first = _snapshot("DV-A", 100.0, "A")
        changed = _snapshot("DV-B", 101.0, "B")
        self.assertNotEqual(
            canonical_semantic_hash(first, contract=self.contract),
            canonical_semantic_hash(changed, contract=self.contract),
        )

    def test_contract_change_invalidates_pre_agent_hash(self) -> None:
        current = _snapshot("DV-B", 100.0, "B")
        history = [_snapshot("DV-A", 80.0, "A")]
        one = build_pre_agent_hashes(current, history, contract=self.contract)
        changed_contract = {**self.contract, "evidenceContractVersion": "21.5.1"}
        two = build_pre_agent_hashes(current, history, contract=changed_contract)
        self.assertNotEqual(one["preAgentComputeHash"], two["preAgentComputeHash"])

    def test_strict_hashes_current_refs_and_import_ids_are_removed_from_cache_body(self) -> None:
        projected = semantic_projection(
            {
                "dataVersion": "DV-X",
                "signalId": "SIG-X",
                "reportBatchId": "BATCH-X",
                "productSnapshotHash": "sha256:strict",
                "evidenceInputHash": "sha256:evidence",
                "artifactRefs": {"signalRef": "ART-X"},
                "factId": "FACT-X",
                "sourceRowId": "ROW-X",
                "sourceHash": "sha256:row-x",
                "sourceRef": "runtime-source-x",
                "productId": "P10001",
                "metricLayer": {"paymentAmount": 100.0},
            }
        )
        for key in (
            "dataVersion",
            "signalId",
            "reportBatchId",
            "productSnapshotHash",
            "evidenceInputHash",
            "artifactRefs",
            "factId",
            "sourceRowId",
            "sourceHash",
            "sourceRef",
        ):
            self.assertNotIn(key, projected)
        self.assertEqual(projected["productId"], "P10001")
        self.assertEqual(projected["metricLayer"]["paymentAmount"], 100.0)

    def test_registry_layers_and_classification_are_fail_closed(self) -> None:
        registry = json.loads(
            (ROOT / "config/competition_hash_precache_registry_v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(registry["mode"], "fail_closed")
        self.assertEqual(
            [layer["level"] for layer in registry["layers"]],
            ["L0", "L1", "L2", "L3", "L4", "L5"],
        )
        self.assertTrue(registry["classification"]["semantic_cache_key"]["crossRunReusable"])
        self.assertFalse(registry["classification"]["execution_identity"]["crossRunReusable"])
        for required in ("factId", "sourceRowId", "sourceHash", "productSnapshotHash"):
            self.assertIn(required, registry["semanticExclusions"])
        self.assertIn("strict_runtime_hash_definitions_unchanged", registry["invariants"])


if __name__ == "__main__":
    unittest.main()
