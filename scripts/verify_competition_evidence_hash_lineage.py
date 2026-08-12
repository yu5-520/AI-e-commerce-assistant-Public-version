#!/usr/bin/env python3
"""Dependency-light architecture gate for competition Evidence hash lineage.

This checker does not access the production database or call a provider. It proves that
TARGET source keeps the competition Evidence path hash-directed and that Unified
Registry, selected V23 runtime projection, V21.5 compatibility binding, station Artifact
contracts and active Signal Admission describe the same single execution chain.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _json(path: str) -> dict[str, Any]:
    value = json.loads(_read(path))
    assert isinstance(value, dict), path
    return value


def _python(path: str) -> str:
    source = _read(path)
    ast.parse(source, filename=path)
    return source


def _called_names(source: str) -> set[str]:
    """Return actual Python call targets, excluding comments/docstrings/literals."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            names.add(fn.id)
        elif isinstance(fn, ast.Attribute):
            names.add(fn.attr)
    return names


def _service_checks() -> dict[str, Any]:
    snapshot = _python("src/services/product_signal_snapshot_service.py")
    bridge = _python("src/services/competition_evidence_v215_runtime_service.py")
    v22 = _python("src/services/v22_runtime_service.py")
    station = _python("src/services/station_alignment_v225_service.py")
    business = _python("src/services/station_business_artifact_service.py")
    admission = _python("src/services/artifact_signal_admission_v225_service.py")

    snapshot_required = [
        'PRODUCT_SIGNAL_SNAPSHOT_VERSION = "18.7-competition-hash-cache"',
        'EVIDENCE_INPUT_CONTRACT = "competition.evidenceInput.v1"',
        'EVIDENCE_CONTRACT = "operatingEvidenceGraph.v1"',
        'EVIDENCE_CONTRACT_VERSION = "21.5.0"',
        'EVIDENCE_CACHE_MODE = "competition_hash_precache"',
        'OBSERVATION_TABLE = "competition_evidence_observation_v1"',
        'MAX_COMPETITION_COMPARABLE_HISTORY = 2',
        'current_competition_history_epoch',
        'evidenceInputHash',
        'currentObservationHash',
        'previousObservationHashes',
        'epoch_active_import_metadata_then_compact_observation_cache',
        'wholeSnapshotRetention',
        'idempotentHit',
        'materialize_system_product_snapshot(data_version=data_version, user_id=user_id, force=False)',
    ]
    bridge_required = [
        'BRIDGE_VERSION = "competitionEvidence.v21_5_hash_bridge.v1"',
        '_HASH_PRECACHE_MATERIALIZER = signal_snapshot.materialize_product_signal_snapshot',
        'competition_evidence_observation_v1',
        'previousProductSetHashes',
        '[:2]',
        'v215.build_cross_validation',
        'operatingEvidenceGraph.v1',
        'evidenceInputHash',
        'wholeSnapshotRetention',
        'competition_hash_precache',
        'signal_snapshot.materialize_product_signal_snapshot = materialize_signal_snapshot_v215_hash',
        'admission.materialize_product_signal_snapshot = materialize_signal_snapshot_v215_hash',
    ]
    v22_required = [
        'from src.services import competition_evidence_v215_runtime_service as competition_evidence_v215',
        'report_evidence.install_v215_runtime()',
        'competition_evidence_v215.install_competition_evidence_v215_runtime()',
    ]
    station_required = [
        'STATION_ALIGNMENT_V225_VERSION = "22.2.7"',
        'evidenceInputHash',
        'historyEpochId',
        'currentProductSetHash',
        'competition_hash_precache',
        'wholeSnapshotRetention',
        'fullProductBundleRef',
        'force=False',
    ]
    business_required = [
        'STATION_BUSINESS_ARTIFACT_VERSION = "22.2.7"',
        '_require_evidence_hash_identity',
        'evidenceInputHash=sha256',
        'maxComparableHistory<=2',
        'fullProductBundleRef=ART',
        'validatedBundleArtifactRef=ART',
        'evidenceFullProductBundleRef',
        'evidenceValidatedBundleRef',
    ]
    admission_required = [
        'ARTIFACT_SIGNAL_ADMISSION_VERSION = "22.5.12"',
        'validatedBundleRef',
        'evidenceInputHash',
        'historyEpochId',
        'parent_refs=[source_artifact_ref]',
        'artifact_refs={"signalRef": signal_ref, "validatedBundleRef": source_artifact_ref}',
        'legacySignalPoolRead',
    ]

    missing = {
        "snapshot": [item for item in snapshot_required if item not in snapshot],
        "bridge": [item for item in bridge_required if item not in bridge],
        "v22": [item for item in v22_required if item not in v22],
        "station": [item for item in station_required if item not in station],
        "business": [item for item in business_required if item not in business],
        "admission": [item for item in admission_required if item not in admission],
    }
    assert not any(missing.values()), missing

    assert v22.index('report_evidence.install_v215_runtime()') < v22.index(
        'competition_evidence_v215.install_competition_evidence_v215_runtime()'
    ), "competition Evidence bridge must replace the V21.5 historical wrapper after V21.5 installs"

    forbidden_snapshot = [
        "product_snapshot_history(data_version, limit=90)",
        "product_snapshot_history(data_version,limit=90)",
        "materialize_system_product_snapshot(data_version=data_version, user_id=user_id, force=True)",
        "product_snapshot_history(limit=90)",
    ]
    forbidden_station = [
        "force=force,",
        "materialize_product_signal_snapshot(\n        data_version=data_version,\n        user_id=user_id,\n        force=True",
    ]
    bridge_calls = _called_names(bridge)
    forbidden_bridge_calls = sorted(
        {"product_snapshot_history", "_comparable_history"}.intersection(bridge_calls)
    )
    forbidden_bridge_literals = [
        literal
        for literal in ('historyCandidateLimit": 90', "comparable[:30]")
        if literal in bridge
    ]
    stale = {
        "snapshot": [item for item in forbidden_snapshot if item in snapshot],
        "bridge": [*forbidden_bridge_calls, *forbidden_bridge_literals],
        "station": [item for item in forbidden_station if item in station],
    }
    assert not any(stale.values()), stale
    return {
        "snapshotRequiredCount": len(snapshot_required),
        "bridgeRequiredCount": len(bridge_required),
        "v22BindingRequiredCount": len(v22_required),
        "stationRequiredCount": len(station_required),
        "businessRequiredCount": len(business_required),
        "admissionRequiredCount": len(admission_required),
        "forbiddenRuntimeRescanHits": stale,
        "legacyV215WrapperActive": False,
        "bridgeActualCallTargets": sorted(bridge_calls),
    }


def _registry_checks() -> dict[str, Any]:
    registry = _json("config/runtime_contract_lineage_registry_v1.json")
    runtime = _json("config/v23_registry_runtime.json")
    governance = _json("governance/runtime_contract_lineage_repair_policy.json")

    assert registry.get("version") == "2026.08.12.2", registry.get("version")
    required_fields = {
        "canonical.set_snapshot_hash",
        "product.product_snapshot_hash",
        "evidence.history_epoch_id",
        "evidence.current_set_snapshot_hash",
        "evidence.current_observation_hash",
        "evidence.previous_set_snapshot_hashes",
        "evidence.previous_observation_hashes",
        "evidence.contract_version",
        "evidence.input_hash",
        "evidence.full_product_bundle_ref",
        "evidence.validated_bundle_ref",
        "signal.signal_ref",
        "artifact.content_hash",
    }
    fields = set((registry.get("fields") or {}).keys())
    assert required_fields <= fields, sorted(required_fields - fields)

    required_interfaces = {
        "evidence.snapshot.precache",
        "evidence.cross_validation.overlay",
        "evidence.full_product_bundle.materialize",
        "evidence.full_product_bundle.artifact",
        "evidence.bundle.validate",
        "evidence.validated_bundle.artifact",
        "evidence.signal.admission",
        "competition.signal.handoff",
        "agent1.input.project",
    }
    interfaces = set((registry.get("interfaces") or {}).keys())
    assert required_interfaces <= interfaces, sorted(required_interfaces - interfaces)

    edge_keys = {
        (str(item.get("from")), str(item.get("to")), str(item.get("type")))
        for item in registry.get("lineageEdges") or []
        if isinstance(item, dict)
    }
    required_edges = {
        ("canonical.set_snapshot_hash", "evidence.current_set_snapshot_hash", "EXACT_HASH_TRANSFER"),
        ("evidence.history_epoch_id", "evidence.input_hash", "HASH_IDENTITY_INPUT"),
        ("evidence.current_set_snapshot_hash", "evidence.input_hash", "HASH_IDENTITY_INPUT"),
        ("evidence.current_observation_hash", "evidence.input_hash", "HASH_IDENTITY_INPUT"),
        ("evidence.contract_version", "evidence.input_hash", "HASH_IDENTITY_INPUT"),
        ("evidence.snapshot.precache", "evidence.cross_validation.overlay", "INTERFACE_HANDOFF"),
        ("evidence.input_hash", "evidence.cross_validation.overlay", "EXACT_HASH_TRANSFER"),
        ("evidence.cross_validation.overlay", "evidence.full_product_bundle.materialize", "INTERFACE_HANDOFF"),
        ("evidence.full_product_bundle_ref", "evidence.bundle.validate", "EXACT_REFERENCE_TRANSFER"),
        ("evidence.validated_bundle_ref", "evidence.signal.admission", "EXACT_REFERENCE_TRANSFER"),
        ("evidence.validated_bundle_ref", "signal.signal_ref", "PARENT_REFERENCE"),
        ("evidence.signal.admission", "signal.signal_ref", "DERIVES_IMMUTABLE_ARTIFACT"),
        ("signal.signal_ref", "competition.signal.handoff", "EXACT_REFERENCE_TRANSFER"),
    }
    assert required_edges <= edge_keys, sorted(required_edges - edge_keys)

    globals_ = set(registry.get("globalInvariants") or [])
    required_globals = {
        "competition_evidence_runtime_never_scans_90_complete_canonical_snapshots",
        "competition_evidence_never_force_rebuilds_existing_canonical_snapshot",
        "competition_evidence_max_comparable_history_is_two",
        "competition_v22_runtime_replaces_legacy_v215_90_snapshot_wrapper_before_station_execution",
        "competition_v215_cross_validation_reads_only_evidence_hash_selected_compact_observations",
        "validatedBundleRef_is_exact_parent_of_active_competition_signal_ART",
        "evidenceInputHash_is_preserved_through_v215_overlay_bundle_validation_and_signal_admission",
        "competition_signal_handoff_does_not_create_second_worker_or_queue",
    }
    assert required_globals <= globals_, sorted(required_globals - globals_)

    assert runtime.get("runtimeContractLineageRegistryVersion") == "2026.08.12.2", runtime
    assert runtime.get("evidenceHashRuntimeScopeVersion") == "23.2.15", runtime
    assert "signal_admission" in set(runtime.get("requiredModules") or []), runtime
    signal_module = (runtime.get("modules") or {}).get("signal_admission") or {}
    runtime_fields = set(signal_module.get("fieldIds") or [])
    assert required_fields <= runtime_fields, sorted(required_fields - runtime_fields)
    expected_runner = "src.services.artifact_signal_admission_v225_service:product_signal_admission_station_v225"
    assert signal_module.get("runner") == expected_runner, signal_module.get("runner")
    runtime_paths = set(signal_module.get("implementationPaths") or [])
    required_paths = {
        "src/services/canonical_product_snapshot_service.py",
        "src/services/canonical_product_trend_v2_service.py",
        "src/services/product_signal_snapshot_service.py",
        "src/services/v215_report_batch_evidence_service.py",
        "src/services/competition_evidence_v215_runtime_service.py",
        "src/services/v22_runtime_service.py",
        "src/services/station_alignment_v225_service.py",
        "src/services/station_business_artifact_service.py",
        "src/services/artifact_transport_service.py",
        "src/services/artifact_signal_admission_v225_service.py",
    }
    assert required_paths <= runtime_paths, sorted(required_paths - runtime_paths)

    profile = ((governance.get("profiles") or {}).get("runtime_contract_lineage_repair") or {})
    assert governance.get("version") == "2026.08.12.2", governance.get("version")
    allowed_paths = set(profile.get("allowedRuntimePathPrefixes") or [])
    assert required_paths <= allowed_paths, sorted(required_paths - allowed_paths)
    assert "signal_admission" in set(profile.get("allowedRegistryModules") or []), profile

    contract = registry["fields"]["evidence.contract_version"]
    invariants = set(contract.get("invariants") or [])
    assert "contract_is_operatingEvidenceGraph.v1" in invariants, invariants
    assert "version_is_21.5.0" in invariants, invariants
    assert "compatibility_broadening_forbidden" in invariants, invariants

    return {
        "registryVersion": registry.get("version"),
        "evidenceFieldCount": len(required_fields),
        "evidenceInterfaceCount": len(required_interfaces),
        "requiredEdgeCount": len(required_edges),
        "runtimeModule": "signal_admission",
        "runtimeRunner": expected_runner,
        "runtimeScopeVersion": runtime.get("evidenceHashRuntimeScopeVersion"),
        "v215BridgePathRegistered": "src/services/competition_evidence_v215_runtime_service.py" in runtime_paths,
        "v215OverlayInterfaceRegistered": "evidence.cross_validation.overlay" in interfaces,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceContractVersion": "21.5.0",
    }


def main() -> int:
    report = {
        "schema": "competition.evidence_hash_lineage.verification.v4",
        "services": _service_checks(),
        "registry": _registry_checks(),
        "verified": True,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
