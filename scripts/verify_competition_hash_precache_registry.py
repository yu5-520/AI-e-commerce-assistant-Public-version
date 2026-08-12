#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _json(path: str) -> dict[str, Any]:
    value = json.loads(_read(path))
    if not isinstance(value, dict):
        raise SystemExit(f"json_object_required:{path}")
    return value


def _hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    findings: list[str] = []
    overlay = _json("config/competition_hash_precache_registry_v1.json")
    root = _json("config/runtime_contract_lineage_registry_v1.json")
    service = _read("src/services/competition_hash_precache_registry_v1_service.py")
    bridge = _read("src/services/competition_evidence_v215_runtime_service.py")

    if overlay.get("mode") != "fail_closed":
        findings.append("precache_registry_not_fail_closed")
    if overlay.get("rootRegistry") != "config/runtime_contract_lineage_registry_v1.json":
        findings.append("precache_registry_root_binding_missing")

    layers = overlay.get("layers") if isinstance(overlay.get("layers"), list) else []
    levels = [str(item.get("level") or "") for item in layers if isinstance(item, dict)]
    if levels != ["L0", "L1", "L2", "L3", "L4", "L5"]:
        findings.append("precache_layer_order_invalid")

    classification = overlay.get("classification") if isinstance(overlay.get("classification"), dict) else {}
    if (classification.get("semantic_cache_key") or {}).get("crossRunReusable") is not True:
        findings.append("semantic_cache_key_not_cross_run_reusable")
    for name in ("content_fingerprint", "execution_identity", "immutable_artifact_reference"):
        if (classification.get(name) or {}).get("crossRunReusable") is not False:
            findings.append(f"strict_classification_cross_run_reuse_forbidden:{name}")

    root_fields = root.get("fields") if isinstance(root.get("fields"), dict) else {}
    for canonical_id in (
        "canonical.set_snapshot_hash",
        "product.product_snapshot_hash",
        "evidence.input_hash",
        "signal.signal_ref",
    ):
        if canonical_id not in root_fields:
            findings.append(f"required_root_hash_field_missing:{canonical_id}")

    required_service_literals = (
        'HASH_PRECACHE_VERSION = "1.0.0"',
        'HASH_PRECACHE_TABLE = "competition_hash_precache_v1"',
        'HASH_PRECACHE_ARTIFACT_TYPE = "competition.pre_agent_semantic_cache"',
        '"productSnapshotHash"',
        '"setSnapshotHash"',
        '"evidenceInputHash"',
        'def build_pre_agent_hashes(',
        'def lookup_pre_agent_cache(',
        'def store_pre_agent_cache(',
        'expected_type=HASH_PRECACHE_ARTIFACT_TYPE',
    )
    for literal in required_service_literals:
        if literal not in service:
            findings.append(f"precache_service_literal_missing:{literal}")

    required_bridge_literals = (
        "build_pre_agent_hashes",
        "lookup_pre_agent_cache",
        "store_pre_agent_cache",
        '"semanticCacheHit": True',
        '"currentArtifactRebindRequired": True',
        '"strictEvidenceInputHashChanged": False',
        "_rebind_cached_package(",
        "signal_snapshot._evidence_identity(",
    )
    for literal in required_bridge_literals:
        if literal not in bridge:
            findings.append(f"evidence_bridge_precache_literal_missing:{literal}")

    for forbidden in (
        "ThreadPoolExecutor",
        "asyncio.gather",
        "secondWorkerAllowed=True",
        "requestCacheEnabled=True",
    ):
        if forbidden in service or forbidden in bridge:
            findings.append(f"precache_forbidden_runtime_change:{forbidden}")

    invariants = set(str(value) for value in overlay.get("invariants") or [])
    for required in (
        "strict_runtime_hash_definitions_unchanged",
        "semantic_cache_hit_never_reuses_old_current_artifact_ref",
        "semantic_cache_hit_must_preserve_current_data_version_lineage",
        "business_metric_change_must_change_semantic_hash",
        "contract_or_policy_change_must_change_semantic_hash",
        "frontend_contract_unchanged",
        "worker_count_unchanged",
        "provider_configuration_unchanged",
    ):
        if required not in invariants:
            findings.append(f"precache_invariant_missing:{required}")

    material = {
        "schema": "competition.hash_precache.verification.v1",
        "overlayVersion": overlay.get("version"),
        "rootRegistryVersion": root.get("version"),
        "levels": levels,
        "semanticExclusionCount": len(overlay.get("semanticExclusions") or []),
        "strictRootFieldsVerified": 4,
        "findings": findings,
    }
    report = {
        **material,
        "verified": not findings,
        "verificationHash": _hash(material),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
