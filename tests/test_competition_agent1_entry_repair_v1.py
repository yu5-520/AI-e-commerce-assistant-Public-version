from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services import competition_signal_handoff_service as handoff
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_SCHEMA,
    AGENT1_MAX_ITEM_CHARS,
    AGENT1_PROJECTION_DEDUPE_REVISION,
    AGENT1_INPUT_PROJECTION_VERSION,
    build_projection_envelope,
    content_hash,
    validate_agent_input_envelope,
)


class CompetitionAgent1EntryRepairTest(unittest.TestCase):
    def test_handoff_discovers_only_admission_qualified_signals(self) -> None:
        captured = {}

        def _list_signals(*, data_version=None, status=None, limit=200):
            captured.update(
                dataVersion=data_version,
                status=status,
                limit=limit,
            )
            return {"signals": []}

        with patch.object(handoff, "list_signals", side_effect=_list_signals):
            result = handoff.seed_competition_signal_handoff("DV-QUALIFIED")

        self.assertEqual(captured["dataVersion"], "DV-QUALIFIED")
        self.assertEqual(captured["status"], "admitted_for_judgment")
        self.assertEqual(
            result["admissionQualifiedStatus"],
            "admitted_for_judgment",
        )
        self.assertEqual(result["formalSignalCount"], 0)
        self.assertEqual(result["providerCallsExecuted"], 0)

    def test_oversized_agent1_projection_is_deduped_without_raising_budget(self) -> None:
        lineage = {
            "version": AGENT1_INPUT_PROJECTION_VERSION,
            "status": "complete",
            "sourceVersionCount": 2,
            "sourceDatasetCount": 2,
            "businessDateCount": 2,
            "sourceRecordCount": 60,
            "sourceArtifactCount": 1,
            "contentHashVerified": True,
            "sourceIdentityComplete": True,
            "dataVersions": ["DV-OLD", "DV-NEW"],
            "sourceArtifactRefs": ["ART-SIGNAL"],
            "sourceContentHash": "sha256:signal",
            "blockingFactors": [],
            "derivedFromImmutableLineage": True,
        }
        signals = [
            {
                "metricCode": f"metric{i:02d}",
                "metricName": f"Metric {i:02d}",
                "previous": i,
                "current": i + 1,
                "latest": i + 1,
                "changeRatio": 0.1,
                "changeRate": 0.1,
                "changeVsPrevious": 1,
                "meaningfulChange": True,
                "signalStrength": "high",
                "signalType": "trend",
                "direction": "up",
                "sampleCount": 2,
                "windows": {"blob": "W" * 500},
                "reason": "R" * 500,
            }
            for i in range(24)
        ]
        features = {
            f"metric{i:02d}": {
                "metricCode": f"metric{i:02d}",
                "current": i + 1,
                "previous": i,
                "previousDelta": 0.1,
                "mom": 0.1,
                "yoy": 0.1,
                "slope5": 0.1,
                "slope10": 0.1,
                "slope30": 0.1,
                "volatility10": 0.02,
                "streakDirection": "up",
                "streakLength": 2,
                "sampleCount": 2,
                "sampleConfidence": 0.8,
                "duplicateBlob": "T" * 800,
            }
            for i in range(24)
        }
        decision = {
            "hypothesisCode": "growth_opportunity",
            "hypothesisLabel": "增长机会",
            "status": "confirmed",
            "severity": 70,
            "confidence": 72,
            "businessImpact": 70,
            "urgency": 70,
            "actionIntensity": "L3",
            "primaryEvidence": {"metricCode": "metric00", "direction": "up"},
            "relatedEvidence": [
                {"metricCode": f"metric{i:02d}", "blob": "E" * 500}
                for i in range(12)
            ],
            "independentEvidenceGroups": ["sales", "traffic"],
            "conflictEvidenceGroups": [],
            "temporalConfirmationCount": 4,
        }
        payload = {
            "productId": "P0001",
            "storeId": "S001",
            "signalId": "PSIGV-1",
            "dataVersion": "DV-NEW",
            "productIdentity": {"productId": "P0001", "storeId": "S001"},
            "snapshotLayer": {
                "fieldSignals": signals,
                "fieldSignalCount": len(signals),
                "semanticContinuity": True,
            },
            "metricLayer": {f"metric{i:02d}": i for i in range(48)},
            "trendContext": {
                "timeSeriesFeatures": features,
                "historicalTrendSummary": [{"blob": "H" * 800} for _ in range(20)],
                "recentFiveOrLatestFacts": [{"blob": "F" * 800} for _ in range(20)],
            },
            "sourceLineageValidation": lineage,
            "strongRelations": [{"blob": "S" * 800} for _ in range(20)],
            "crossValidation": {
                "version": "21.5.0",
                "contract": "operatingEvidenceGraph.v1",
                "decision": decision,
                "hypotheses": [dict(decision, blob="X" * 1000) for _ in range(20)],
                "timeSeriesFeatures": features,
                "changedMetricCount": 24,
                "abnormalMetricCount": 24,
            },
            "factLayerValidation": {"ok": True, "blob": "V" * 4000},
            "diagnosticRag": {"policy": "P" * 4000},
            "inputContract": {
                "schema": AGENT1_INPUT_SCHEMA,
                "projectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
                "sourceRef": "ART-SIGNAL",
                "sourceContentHash": "sha256:signal",
                "sourceLineageHash": content_hash(lineage),
                "trendSemanticVersion": AGENT1_INPUT_PROJECTION_VERSION,
                "policyContextHash": "sha256:policy",
                "semanticContinuity": True,
                "completeFieldSignalTransport": True,
                "trendContextTransport": True,
                "sourceLineageTransport": True,
                "fallbackAllowed": False,
                "fullSignalReadAllowed": False,
            },
        }

        envelope = build_projection_envelope(
            schema=AGENT1_INPUT_SCHEMA,
            payload=payload,
            source_artifact_refs=["ART-SIGNAL"],
            source_content_hash="sha256:signal",
        )
        validation = validate_agent_input_envelope(
            envelope,
            expected_schema=AGENT1_INPUT_SCHEMA,
        )
        audit = envelope["projectionAudit"]["deterministicProjectionDedupe"]

        self.assertIs(validation["ok"], True)
        self.assertLessEqual(validation["projectedChars"], AGENT1_MAX_ITEM_CHARS)
        self.assertEqual(audit["revision"], AGENT1_PROJECTION_DEDUPE_REVISION)
        self.assertIs(audit["applied"], True)
        self.assertIs(audit["itemCharBudgetChanged"], False)
        self.assertEqual(audit["fieldSignalCountBefore"], 24)
        self.assertEqual(audit["fieldSignalCountAfter"], 24)
        self.assertNotIn("hypotheses", envelope["payload"]["crossValidation"])
        self.assertNotIn("timeSeriesFeatures", envelope["payload"]["crossValidation"])
        self.assertEqual(AGENT1_MAX_ITEM_CHARS, 22_000)


if __name__ == "__main__":
    unittest.main()
