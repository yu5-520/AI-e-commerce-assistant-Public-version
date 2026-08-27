#!/usr/bin/env python3
"""Export static V24.16-V24.17 migration evidence without importing app runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "src" / "api" / "routes" / "frontend_views.py"
SERVICE = ROOT / "src" / "services" / "frontend_view_artifact_v2259_service.py"
CLIENT = ROOT / "web_demo" / "core" / "hash-view-client-v2259.js"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def source_commit() -> str | None:
    marker = ROOT / ".v24-phase4-source-commit"
    if marker.is_file():
        value = marker.read_text(encoding="utf-8").strip()
        return value or None
    return None


def build() -> dict[str, Any]:
    route = ROUTE.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    client = CLIENT.read_text(encoding="utf-8")

    head_route = '@router.get("/head/{view_key}")' in route
    route_materializes = "materialize_if_missing=True" in route
    service_materializes = (
        "if materialize_if_missing and needs_materialization:" in service
        and "return materialize_frontend_views_v2259(" in service
    )
    sqlite_head = "CREATE TABLE IF NOT EXISTS frontend_view_head_v2259" in service
    runtime_hash = "runtimeStateHash" in service and "sha256:" in service
    atomic_publish = "frontend_view_head_atomic_publish_failed" in service

    browser_head_nonce = 'query.set("_headNonce", String(Date.now()));' in client
    browser_no_store = 'cache: "no-store"' in client and '"Cache-Control": "no-cache"' in client
    browser_local_hash_cache = (
        "localStorage.getItem(storageKey(hash))" in client
        and "localStorage.setItem(storageKey(hash)" in client
        and "document?.contentHash !== expectedHash" in client
    )
    manifest_hash = "manifestHash" in client
    module_hash = "module.contentHash" in client
    event_source_present = "EventSource(" in client or "new EventSource" in client

    require(head_route, "frontend_head_route_not_found")
    require(route_materializes, "frontend_head_route_materialize_flag_not_found")
    require(service_materializes, "frontend_head_get_materialization_path_not_found")
    require(sqlite_head, "frontend_head_sqlite_authority_not_found")
    require(runtime_hash, "frontend_runtime_hash_contract_not_found")
    require(atomic_publish, "frontend_atomic_publish_guard_not_found")
    require(browser_head_nonce and browser_no_store, "browser_mutable_head_refresh_contract_not_found")
    require(browser_local_hash_cache and manifest_hash and module_hash, "browser_immutable_hash_cache_not_found")
    require(not event_source_present, "unexpected_existing_event_source_found")

    evidence: dict[str, Any] = {
        "schema": "v24.phase4_frontend_baseline.evidence.v1",
        "version": "24.17.0",
        "verified": True,
        "sourceCommit": source_commit(),
        "productionViewAuthority": "PYTHON_SQLITE",
        "browserRuntime": "JAVASCRIPT",
        "headRoute": "/api/view/head/{view_key}",
        "headGetMayMaterialize": route_materializes and service_materializes,
        "headGetPureRead": False,
        "runtimeStateHashPresent": runtime_hash,
        "existingAtomicHeadPublish": atomic_publish,
        "browserHeadNoStore": browser_head_nonce and browser_no_store,
        "browserImmutableHashCache": browser_local_hash_cache and manifest_hash and module_hash,
        "browserManifestHashPresent": manifest_hash,
        "browserModuleContentHashPresent": module_hash,
        "browserEventSourcePresent": event_source_present,
        "existingSseAuthority": "NONE",
        "migrationNeed": {
            "headReadMustBecomePure": True,
            "projectionMustMoveOffReadPath": True,
            "publishNeedsVersionCas": True,
            "publishNeedsGenerationFence": True,
            "headChangeNeedsEvent": True,
            "browserShouldFetchChangedHashesOnly": True,
        },
        "sourceFiles": {
            "route": {"path": str(ROUTE.relative_to(ROOT)), "sha256": file_hash(ROUTE)},
            "service": {"path": str(SERVICE.relative_to(ROOT)), "sha256": file_hash(SERVICE)},
            "client": {"path": str(CLIENT.relative_to(ROOT)), "sha256": file_hash(CLIENT)},
        },
    }
    evidence["evidenceHash"] = sha256_value(evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/v24-java-phase4/frontend-baseline-evidence.json")
    args = parser.parse_args()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = build()
    output.write_text(canonical(evidence) + "\n", encoding="utf-8")
    print(canonical({
        "verified": evidence["verified"],
        "headGetMayMaterialize": evidence["headGetMayMaterialize"],
        "browserImmutableHashCache": evidence["browserImmutableHashCache"],
        "browserEventSourcePresent": evidence["browserEventSourcePresent"],
        "evidenceHash": evidence["evidenceHash"],
    }))


if __name__ == "__main__":
    main()
