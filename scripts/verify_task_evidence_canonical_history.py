#!/usr/bin/env python3
"""Static fail-closed gate for the canonical task-evidence history repair."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load(path: str):
    return json.loads(read(path))


def require(condition: bool, message: str, findings: list[str]) -> None:
    if not condition:
        findings.append(message)


def main() -> int:
    findings: list[str] = []
    governance = load("governance/task_evidence_canonical_history_repair_v1.json")
    registry = load("config/runtime_contract_lineage_registry_v1.json")
    adapter = read("src/services/task_evidence_canonical_history_v1_service.py")
    installer = read("src/services/task_evidence_canonical_history_install_v1_service.py")
    bootstrap = read("src/__init__.py")
    task_detail = read("src/services/task_detail_snapshot_v2024_service.py")

    require(
        governance.get("runtimeContractLineageRegistryVersion") == registry.get("version"),
        "governance registry version must equal unified runtime contract lineage registry",
        findings,
    )
    require(
        "canonical product snapshot 是商品事实唯一根" in str(registry.get("description") or ""),
        "unified registry must keep canonical product snapshot as the single product fact root",
        findings,
    )
    globals_ = set(registry.get("globalInvariants") or [])
    require(
        "competition_evidence_runtime_never_scans_90_complete_canonical_snapshots" in globals_,
        "bounded evidence-history invariant missing from unified registry",
        findings,
    )

    require("system_product_snapshots_v14" not in adapter, "canonical adapter references retired legacy table", findings)
    for token in (
        "current_competition_history_epoch",
        "_history_metadata",
        "_history_fingerprint",
        "_slim_snapshot_for_product",
        "source_data_version_not_in_current_history_epoch",
        "wholeSnapshotRetention",
    ):
        require(token in adapter, f"canonical adapter missing required token: {token}", findings)

    for token in (
        "evidence._snapshot_rows = _retired_legacy_snapshot_rows",
        "task_bounded_canonical_product_snapshots",
        "22.4.0-task-evidence-canonical-v1",
        "22.4.0-canonical-history-v1",
        "legacySnapshotFallbackUsed=False",
    ):
        require(token in installer, f"canonical installer missing required token: {token}", findings)

    v22_call = bootstrap.find("install_v22_runtime()")
    repair_call = bootstrap.find("install_task_evidence_canonical_history_v1()")
    require(v22_call >= 0, "V22 installer call missing from bootstrap", findings)
    require(repair_call > v22_call, "canonical task-evidence repair must install after V22", findings)

    require(
        'str(row["source_version"] or "") == TASK_DETAIL_SNAPSHOT_VERSION' in task_detail,
        "task-detail stale-version read-through guard missing",
        findings,
    )
    require(
        'SELECT task_id,status,workflow_status,payload,updated_at FROM task_status' in task_detail,
        "task-detail rebuild authority must remain task_status",
        findings,
    )

    forbidden = set(governance.get("forbiddenSnapshotAuthorities") or [])
    require(
        "system_product_snapshots_v14" in forbidden,
        "governance must explicitly forbid the retired task-evidence snapshot authority",
        findings,
    )
    invariants = set(governance.get("invariants") or [])
    for expected in (
        "canonical_product_snapshot_is_single_task_evidence_history_root",
        "explicit_task_dataVersion_missing_from_current_epoch_fails_closed",
        "whole_multi_product_history_is_never_retained_for_task_evidence",
        "two_or_more_valid_frozen_observations_are_still_required_for_execution",
        "old_task_detail_materializations_become_stale_without_deleting_task_hash_or_canonical_facts",
    ):
        require(expected in invariants, f"repair governance invariant missing: {expected}", findings)

    material = "\n".join(
        [
            json.dumps(governance, ensure_ascii=False, sort_keys=True),
            adapter,
            installer,
            bootstrap,
        ]
    )
    verification_hash = "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    result = {
        "schema": "task.evidence.canonical_history.verification.v1",
        "verified": not findings,
        "findings": findings,
        "registryVersion": registry.get("version"),
        "projectionVersion": governance.get("projection", {}).get("version"),
        "snapshotAuthority": governance.get("allowedSnapshotAuthority"),
        "legacyAuthorityForbidden": "system_product_snapshots_v14" in forbidden,
        "verificationHash": verification_hash,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
