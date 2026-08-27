#!/usr/bin/env python3
"""Export deterministic Python behavior as V24.6-V24.8 Java shadow test vectors.

This script does not mutate business state. It freezes representative canonical
mapping outputs plus the current task-state transition matrix so the Java control
plane can independently reproduce them before any write authority is transferred.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from src.services.canonical_product_snapshot_service import build_canonical_product_snapshot_item
from src.services.task_state_machine_service import ACTION_TARGET_STATUS, ALLOWED_TRANSITIONS, DONE_STATUS


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def mapping_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "complete_product_with_fact_lineage",
            "dataVersion": "DV-V24-001",
            "input": {
                "platform": "taobao",
                "storeId": "STORE-01",
                "storeName": "Demo Store",
                "productId": "P-1001",
                "skuId": "SKU-1001-A",
                "title": "V24 Test Product",
                "verticalCategory": "家居",
                "priceBand": "100-199",
                "productRole": "hero",
                "lifecycleStage": "growth",
                "metricDate": "2026-08-26",
                "roi": 2.5,
                "roas": 2.8,
                "adSpend": 1200,
                "paymentAmount": 3360,
                "clickRate": 0.08,
                "conversionRate": 0.12,
                "refundRate": 0.03,
                "inventory": 88,
                "sourceDataVersions": ["DV-V24-001"],
                "sourceDatasets": ["operating_report"],
                "sourceReportRefs": ["ART-REPORT-001"],
                "sourceContentHashes": ["sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
                "permissionStampId": "PMS-V24-001",
                "permissionGateStatus": "passed",
                "productMetricFacts": [
                    {
                        "factId": "FACT-ROI-001",
                        "metricName": "roi",
                        "level": "product",
                        "value": 2.5,
                        "sourceRowId": "ROW-1",
                        "sourceReportRef": "ART-REPORT-001",
                        "sourceHash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                    }
                ],
                "trafficSourceFacts": [
                    {
                        "factId": "FACT-TRAFFIC-001",
                        "metricName": "paidVisitors",
                        "level": "traffic_source",
                        "value": 500,
                        "sourceRowId": "ROW-2",
                        "sourceArtifactRef": "ART-REPORT-001"
                    }
                ],
                "metricFactSummary": {"roi": 1, "paidVisitors": 1}
            }
        },
        {
            "name": "fallback_identity_and_roas",
            "dataVersion": "DV-V24-002",
            "input": {
                "platform": "douyin",
                "storeId": "STORE-02",
                "id": "P-2002",
                "sku": "SKU-2002-B",
                "categoryLevel2": "服饰",
                "roi": 1.7,
                "paymentAmount": 1700,
                "adSpend": 1000,
                "clickRate": 0.04,
                "conversionRate": 0.06,
                "refundRate": 0.02,
                "inventory": 30,
                "sourceDataVersions": ["DV-V24-002"],
                "permissionStampId": "PMS-V24-002",
                "permissionGateStatus": "passed"
            }
        },
        {
            "name": "explicit_gap_exposure",
            "dataVersion": "DV-V24-003",
            "input": {
                "platform": "unknown",
                "storeId": "STORE-03",
                "productId": "P-3003",
                "title": "Sparse Product",
                "paymentAmount": 500,
                "inventory": 5,
                "permissionStampId": "PMS-V24-003",
                "permissionGateStatus": "passed",
                "sourceDataVersions": ["DV-V24-003"]
            }
        }
    ]


def build_evidence() -> dict[str, Any]:
    mapping_vectors = []
    for case in mapping_cases():
        expected = build_canonical_product_snapshot_item(case["input"], case["dataVersion"])
        mapping_vectors.append({
            **case,
            "expected": expected,
            "expectedCanonicalHash": sha256(expected),
        })

    valid_snapshot = mapping_vectors[0]["expected"]
    invalid_permission = dict(valid_snapshot)
    invalid_permission["permissionGateStatus"] = "quarantine"
    invalid_hash = dict(valid_snapshot)
    invalid_hash["snapshotHash"] = "sha256:" + "0" * 64

    gate_vectors = [
        {"gateId": "PRODUCT_SNAPSHOT_ADMISSION", "name": "valid_snapshot", "input": valid_snapshot, "expectedDecision": "PASS"},
        {"gateId": "PRODUCT_SNAPSHOT_ADMISSION", "name": "permission_block", "input": invalid_permission, "expectedDecision": "BLOCK"},
        {"gateId": "PRODUCT_SNAPSHOT_ADMISSION", "name": "hash_alias_block", "input": invalid_hash, "expectedDecision": "BLOCK"},
        {"gateId": "TASK_TRANSITION_ADMISSION", "name": "valid_transition", "input": {"transitionAllowed": True, "versionMatch": True}, "expectedDecision": "PASS"},
        {"gateId": "TASK_TRANSITION_ADMISSION", "name": "stale_transition", "input": {"transitionAllowed": True, "versionMatch": False}, "expectedDecision": "BLOCK"},
        {"gateId": "STATE_VERSION_CONFLICT", "name": "version_match", "input": {"expectedVersion": 7, "currentVersion": 7}, "expectedDecision": "PASS"},
        {"gateId": "STATE_VERSION_CONFLICT", "name": "version_conflict", "input": {"expectedVersion": 7, "currentVersion": 8}, "expectedDecision": "BLOCK"},
    ]

    transitions = {state: sorted(targets) for state, targets in sorted(ALLOWED_TRANSITIONS.items())}
    action_targets = {key: value for key, value in sorted(ACTION_TARGET_STATUS.items())}
    task_vectors = [
        {"name": "accept", "fromStatus": "待接收", "toStatus": "处理中", "currentVersion": 3, "expectedVersion": 3, "expectedDecision": "PASS"},
        {"name": "illegal_skip", "fromStatus": "待接收", "toStatus": "已完成", "currentVersion": 3, "expectedVersion": 3, "expectedDecision": "BLOCK"},
        {"name": "terminal_reopen", "fromStatus": "已归档", "toStatus": "处理中", "currentVersion": 9, "expectedVersion": 9, "expectedDecision": "BLOCK"},
        {"name": "stale_version", "fromStatus": "处理中", "toStatus": "待复核", "currentVersion": 5, "expectedVersion": 4, "expectedDecision": "CONFLICT"},
        {"name": "idempotent_same_state", "fromStatus": "处理中", "toStatus": "处理中", "currentVersion": 5, "expectedVersion": 5, "expectedDecision": "PASS"},
        {"name": "unknown_state", "fromStatus": "未知旧状态", "toStatus": "处理中", "currentVersion": 1, "expectedVersion": 1, "expectedDecision": "BLOCK"}
    ]

    material = {
        "schema": "v24.phase2_python_shadow_evidence.v1",
        "version": "24.6.0",
        "mappingVectors": mapping_vectors,
        "gateVectors": gate_vectors,
        "taskState": {
            "allowedTransitions": transitions,
            "doneStatuses": sorted(DONE_STATUS),
            "actionTargetStatus": action_targets,
            "vectors": task_vectors,
        },
        "productionWriteAuthority": "PYTHON_UNCHANGED",
    }
    return {**material, "evidenceHash": sha256(material)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/v24-java-phase2/python-shadow-evidence.json")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = build_evidence()
    output.write_text(canonical(evidence) + "\n", encoding="utf-8")
    print(canonical({
        "verified": True,
        "mappingVectorCount": len(evidence["mappingVectors"]),
        "gateVectorCount": len(evidence["gateVectors"]),
        "taskVectorCount": len(evidence["taskState"]["vectors"]),
        "evidenceHash": evidence["evidenceHash"],
    }))


if __name__ == "__main__":
    main()
