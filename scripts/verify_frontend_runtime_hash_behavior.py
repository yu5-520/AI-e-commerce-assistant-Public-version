#!/usr/bin/env python3
"""Dependency-light behavioral gate for frontend runtime/hash lineage.

The frontend contract is unchanged. This verifier derives the expected unified-runtime
registry version from the registry itself instead of freezing a historical version
literal, so additive backend hash-cache work cannot fail a frontend gate merely because
the root registry version advanced.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _reset(service: Any) -> None:
    service._HEAD_CACHE.clear()
    service._MODULE_CACHE.clear()


def _runtime_checks() -> tuple[dict[str, Any], list[str]]:
    from src.services import frontend_view_artifact_v2259_service as service

    findings: list[str] = []
    queued = {
        "version": "22.5.9",
        "dataVersion": "DV-HASH-1",
        "products": 30,
        "signals": 8,
        "currentStageCounts": {"agent1_pending": 8},
        "activeTaskCount": 0,
        "finalizedTaskCount": 0,
        "canonicalProductSetSnapshotHash": "sha256:canonical-a",
        "reportImportOrderHash": "sha256:report-order-a",
        "reportCount": 2,
    }
    completed = {
        **queued,
        "currentStageCounts": {"task_pool": 3, "observed_soft_gate": 5},
        "activeTaskCount": 3,
    }
    canonical_changed = {
        **queued,
        "canonicalProductSetSnapshotHash": "sha256:canonical-b",
    }
    timestamp_only = {
        **queued,
        "createdAt": "2099-01-01T00:00:00",
        "updatedAt": "2099-01-01T00:01:00",
        "database": {"pageCount": 99, "stateHash": "volatile"},
    }

    queued_hash = service._runtime_state_hash(queued)
    completed_hash = service._runtime_state_hash(completed)
    timestamp_hash = service._runtime_state_hash(timestamp_only)
    canonical_hash = service._runtime_state_hash(canonical_changed)
    if queued_hash == completed_hash:
        findings.append("runtime_state_hash_did_not_change_for_stage_progress")
    if queued_hash != timestamp_hash:
        findings.append("runtime_state_hash_changed_for_timestamp_only_change")
    if queued_hash == canonical_hash:
        findings.append("runtime_state_hash_did_not_change_for_canonical_snapshot_change")

    _reset(service)
    payloads = [queued, completed]
    materialize_calls = {"count": 0}
    original_live = service.pipeline_live_snapshot
    original_materialize = service._materialize_modules

    def fake_live(*_: Any, **__: Any) -> dict[str, Any]:
        index = min(fake_live.calls, len(payloads) - 1)
        fake_live.calls += 1
        return payloads[index]

    fake_live.calls = 0

    def wrapped_materialize(snapshot: dict[str, Any], canonical: str) -> dict[str, Any]:
        materialize_calls["count"] += 1
        return original_materialize(snapshot, canonical)

    service.pipeline_live_snapshot = fake_live
    service._materialize_modules = wrapped_materialize
    try:
        head1 = service.materialize_frontend_view_artifacts(user_id=None, force=False)
        head2 = service.materialize_frontend_view_artifacts(user_id=None, force=False)
    finally:
        service.pipeline_live_snapshot = original_live
        service._materialize_modules = original_materialize

    if head1.get("runtimeStateHash") == head2.get("runtimeStateHash"):
        findings.append("runtime_head_did_not_refresh_on_state_change")
    if materialize_calls["count"] != 1:
        findings.append("runtime_only_change_rebuilt_immutable_modules")
    if head1.get("manifestHash") != head2.get("manifestHash"):
        findings.append("runtime_only_change_changed_manifest_hash")

    return {
        "queuedHash": queued_hash,
        "completedHash": completed_hash,
        "timestampOnlyHash": timestamp_hash,
        "canonicalChangedHash": canonical_hash,
        "canonicalSetSnapshotHash": canonical_changed["canonicalProductSetSnapshotHash"],
        "queuedArtifactRef": head1.get("artifactRef"),
        "completedArtifactRef": head2.get("artifactRef"),
        "manifestHash": head2.get("manifestHash"),
        "materializeCalls": materialize_calls["count"],
    }, findings


def _head_checks() -> tuple[dict[str, Any], list[str]]:
    from src.services import frontend_view_artifact_v2259_service as service

    findings: list[str] = []
    _reset(service)
    original_live = service.pipeline_live_snapshot
    original_materialize = service._materialize_modules
    materialize_calls = {"count": 0}
    states = [
        {
            "version": "22.5.9",
            "dataVersion": "DV-HASH-1",
            "products": 30,
            "signals": 8,
            "currentStageCounts": {"agent1_pending": 8},
            "activeTaskCount": 0,
            "finalizedTaskCount": 0,
            "canonicalProductSetSnapshotHash": "sha256:canonical-a",
            "reportImportOrderHash": "sha256:report-order-a",
            "reportCount": 2,
        },
        {
            "version": "22.5.9",
            "dataVersion": "DV-HASH-1",
            "products": 30,
            "signals": 8,
            "currentStageCounts": {"task_pool": 3, "observed_soft_gate": 5},
            "activeTaskCount": 3,
            "finalizedTaskCount": 0,
            "canonicalProductSetSnapshotHash": "sha256:canonical-a",
            "reportImportOrderHash": "sha256:report-order-a",
            "reportCount": 2,
        },
    ]

    def fake_live(*_: Any, **__: Any) -> dict[str, Any]:
        index = min(fake_live.calls, len(states) - 1)
        fake_live.calls += 1
        return states[index]

    fake_live.calls = 0

    def wrapped_materialize(snapshot: dict[str, Any], canonical: str) -> dict[str, Any]:
        materialize_calls["count"] += 1
        return original_materialize(snapshot, canonical)

    service.pipeline_live_snapshot = fake_live
    service._materialize_modules = wrapped_materialize
    try:
        changed = service.materialize_frontend_view_artifacts(user_id=None, force=False)
        reused = service.materialize_frontend_view_artifacts(user_id=None, force=False)
    finally:
        service.pipeline_live_snapshot = original_live
        service._materialize_modules = original_materialize

    if changed.get("runtimeStateHash") == reused.get("runtimeStateHash"):
        findings.append("head_check_runtime_state_not_changed")
    if changed.get("manifestHash") != reused.get("manifestHash"):
        findings.append("head_check_manifest_changed_for_runtime_only_progress")
    if materialize_calls["count"] != 1:
        findings.append("head_check_immutable_modules_not_reused")

    return {
        "changedManifest": changed.get("manifestHash"),
        "reusedManifest": reused.get("runtimeStateHash"),
        "changedRuntimeStateHash": changed.get("runtimeStateHash"),
        "reusedRuntimeStateHash": reused.get("runtimeStateHash"),
        "materializeCalls": materialize_calls["count"],
    }, findings


def _canonical_product_checks() -> tuple[dict[str, Any], list[str]]:
    registry = json.loads(
        (ROOT / "config" / "runtime_contract_lineage_registry_v1.json").read_text(
            encoding="utf-8"
        )
    )
    runtime = json.loads(
        (ROOT / "config" / "v23_registry_runtime.json").read_text(encoding="utf-8")
    )
    findings: list[str] = []
    fields = registry.get("fields") if isinstance(registry.get("fields"), dict) else {}
    interfaces = registry.get("interfaces") if isinstance(registry.get("interfaces"), dict) else {}
    edges = registry.get("hashLineage") if isinstance(registry.get("hashLineage"), list) else []

    required_fields = {
        "canonical.set_snapshot_hash",
        "entity.data_version",
        "product.product_registry_key",
        "product.product_snapshot_hash",
        "product.source_report_refs",
        "product.source_data_versions",
        "product.fact_refs",
        "product.fact_hash_refs",
        "product.permission_stamp_id",
    }
    missing_fields = sorted(required_fields - set(fields))
    if missing_fields:
        findings.append("canonical_product_registry_fields_missing:" + ",".join(missing_fields))

    required_interfaces = {
        "canonical_product_snapshot.materialize",
        "canonical_product_snapshot.read_set",
        "canonical_product_snapshot.read_product",
        "frontend_hash_view.read_products",
    }
    missing_interfaces = sorted(required_interfaces - set(interfaces))
    if missing_interfaces:
        findings.append(
            "canonical_product_registry_interfaces_missing:" + ",".join(missing_interfaces)
        )

    required_edges = {
        ("field:canonical.set_snapshot_hash", "interface:frontend_hash_view.read_products"),
        ("field:product.product_snapshot_hash", "interface:frontend_hash_view.read_products"),
        ("field:product.product_registry_key", "interface:frontend_hash_view.read_products"),
        ("field:product.permission_stamp_id", "interface:frontend_hash_view.read_products"),
    }
    actual_edges = {
        (str(edge.get("from") or ""), str(edge.get("to") or ""))
        for edge in edges
        if isinstance(edge, dict)
    }
    missing_edges = sorted(required_edges - actual_edges)
    if missing_edges:
        findings.append(
            "canonical_product_hash_lineage_edges_missing:"
            + ",".join(f"{source}->{target}" for source, target in missing_edges)
        )

    root_version = str(registry.get("version") or "")
    runtime_version = str(runtime.get("runtimeContractLineageRegistryVersion") or "")
    if runtime_version != root_version:
        findings.append(
            f"runtime_contract_lineage_registry_version_not_current:{runtime_version}:{root_version}"
        )
    if not bool(runtime.get("runtimeContractLineageVerified")):
        findings.append("runtime_contract_lineage_not_verified")

    return {
        "requiredFieldCount": len(required_fields),
        "requiredInterfaceCount": len(required_interfaces),
        "registeredFieldCount": len(fields),
        "registeredInterfaceCount": len(interfaces),
        "registryVersion": root_version,
        "runtimeRegistryVersion": runtime_version,
        "signalAdmissionIndependent": True,
        "missingFields": missing_fields,
        "missingInterfaces": missing_interfaces,
        "missingEdges": missing_edges,
    }, findings


def _client_checks() -> tuple[dict[str, Any], list[str]]:
    client_path = ROOT / "web_demo" / "core" / "hash-view-client-v2259.js"
    source = client_path.read_text(encoding="utf-8")
    required = [
        "cache: 'no-store'",
        "fetchHead",
        "fetchModuleArtifact",
        "fetchProductDetailArtifact",
        "fetchHashViewProducts",
        "fetchHashViewProductDetail",
        "dataVersion",
        "setSnapshotHash",
        "productRegistryKey",
        "productSnapshotHash",
        "productDetailRef",
        "productDetailContentHash",
        "frontend.product_detail.v2259",
        "reloadForHead",
        "moduleContentHash",
        "localStorage",
        "manifestHash",
        "runtimeStateHash",
    ]
    missing = [value for value in required if value not in source]
    findings = ["client_contract_missing:" + value for value in missing]
    return {
        "path": str(client_path.relative_to(ROOT)),
        "requiredLiteralCount": len(required),
        "missing": missing,
    }, findings


def _product_detail_checks() -> tuple[dict[str, Any], list[str]]:
    route_path = ROOT / "src" / "api" / "routes" / "modules" / "product_detail_v2256.py"
    frontend_path = ROOT / "src" / "api" / "routes" / "frontend_views.py"
    route_source = route_path.read_text(encoding="utf-8")
    frontend_source = frontend_path.read_text(encoding="utf-8")
    findings: list[str] = []
    route_required = [
        "materialize_product_detail_artifact",
        "artifactId",
        "contentHash",
        "productSnapshotHash",
        "productRegistryKey",
        "sourceDataVersions",
        "factRefs",
        "factHashRefs",
        "permissionStampId",
        "readMode",
    ]
    frontend_required = [
        "productDetailRef",
        "productDetailContentHash",
        "frontend.product_detail.v2259",
        "setSnapshotHash",
        "productSnapshotHash",
        "productRegistryKey",
    ]
    route_missing = [value for value in route_required if value not in route_source]
    frontend_missing = [value for value in frontend_required if value not in frontend_source]
    findings.extend("product_detail_route_contract_missing:" + value for value in route_missing)
    findings.extend("frontend_product_detail_contract_missing:" + value for value in frontend_missing)
    return {
        "routePath": str(route_path.relative_to(ROOT)),
        "frontendPath": str(frontend_path.relative_to(ROOT)),
        "routeMissing": route_missing,
        "frontendMissing": frontend_missing,
    }, findings


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="frontend-runtime-hash-") as temp:
        db_path = Path(temp) / "runtime.sqlite3"
        os.environ["PRODUCT_DB_PATH"] = str(db_path)
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        runtime, runtime_findings = _runtime_checks()
        head, head_findings = _head_checks()
        canonical_products, canonical_findings = _canonical_product_checks()
        client, client_findings = _client_checks()
        product_detail, product_detail_findings = _product_detail_checks()

    findings = [
        *runtime_findings,
        *head_findings,
        *canonical_findings,
        *client_findings,
        *product_detail_findings,
    ]
    report = {
        "schema": "competition.frontend_runtime_hash_behavior.v4",
        "version": "4.0",
        "verified": not findings,
        "findings": findings,
        "runtime": runtime,
        "head": head,
        "canonicalProducts": canonical_products,
        "client": client,
        "productDetail": product_detail,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
