#!/usr/bin/env python3
"""Dependency-light behavioral gate for frontend runtime/hash lineage.

This checker uses only stdlib monkeypatching/static inspection. It does not call a model,
HTTP endpoint, or production database. It verifies the identity semantics that matter for
the Hash View, including the canonical product single-root repair:

- same dataVersion + changed execution state => changed runtimeStateHash;
- transport timestamps alone do not rotate runtimeStateHash;
- same dataVersion + changed canonical setSnapshotHash => changed runtimeStateHash;
- Reset/no active runtime overrides a stale caller dataVersion and cannot read history;
- Head republishes on runtime hash change and reuses on exact identity match;
- Hash View products module is built from canonical product snapshot projection only;
- canonical set hash -> runtime hash -> products module Artifact -> manifest hash/ref is
  explicitly registered in Unified Registry / Runtime Projection;
- browser Head fetch is no-store while immutable Artifact caching remains enabled;
- product detail requests keep the separate immutable detail hash-cache path.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services import frontend_view_artifact_v2259_service as view


def _pipeline(status: str, updated_at: str) -> Dict[str, Any]:
    return {
        "dataVersion": "DV-SAME",
        "activeDataVersion": "DV-SAME",
        "activeDataVersionGate": "open_active_import_runtime",
        "batchState": {
            "jobId": "JOB-1",
            "status": status,
            "currentStation": "report_receive_station" if status == "queued" else "report_schema_station",
            "updatedAt": updated_at,
        },
        "summary": {"productTotal": 30, "failed": 0},
        "stages": [
            {
                "label": "数据中台",
                "queued": 1 if status == "queued" else 0,
                "completed": 0 if status == "queued" else 1,
                "updatedAt": updated_at,
            }
        ],
        "items": [],
    }


def _canonical_identity(data_version: str | None, set_hash: str | None) -> Dict[str, Any]:
    if not data_version:
        return {
            "ready": False,
            "dataVersion": None,
            "snapshotId": None,
            "setSnapshotHash": None,
            "productCount": 0,
            "authority": "canonical_product_snapshot_sets_v1",
        }
    return {
        "ready": bool(set_hash),
        "dataVersion": data_version,
        "snapshotId": f"CANONICAL-PRODUCT-SNAPSHOT-{data_version}" if set_hash else None,
        "setSnapshotHash": set_hash,
        "productCount": 30 if set_hash else 0,
        "authority": "canonical_product_snapshot_sets_v1",
    }


def _runtime_hash_checks() -> Dict[str, Any]:
    original_pipeline = sys.modules.get("src.services.pipeline_live_read_model_v225_service")
    original_canonical = view._canonical_product_runtime_identity
    fake = types.ModuleType("src.services.pipeline_live_read_model_v225_service")
    state = {"value": _pipeline("queued", "2026-08-09T20:00:00")}
    canonical = {"hash": "sha256:canonical-a"}
    fake.read_pipeline_live_model = lambda **_kwargs: dict(state["value"])
    sys.modules[fake.__name__] = fake
    view._canonical_product_runtime_identity = lambda data_version: _canonical_identity(
        data_version,
        canonical["hash"] if data_version else None,
    )
    try:
        queued = view._runtime_state("DV-SAME")
        state["value"] = _pipeline("completed", "2026-08-09T20:00:01")
        completed = view._runtime_state("DV-SAME")
        assert queued["dataVersion"] == completed["dataVersion"] == "DV-SAME"
        assert queued["runtimeStateHash"] != completed["runtimeStateHash"], (
            queued["runtimeStateHash"],
            completed["runtimeStateHash"],
        )

        state["value"] = _pipeline("queued", "2026-08-09T20:10:00")
        timestamp_only = view._runtime_state("DV-SAME")
        assert queued["runtimeStateHash"] == timestamp_only["runtimeStateHash"], (
            queued["runtimeStateHash"],
            timestamp_only["runtimeStateHash"],
        )

        canonical["hash"] = "sha256:canonical-b"
        canonical_changed = view._runtime_state("DV-SAME")
        assert canonical_changed["runtimeStateHash"] != queued["runtimeStateHash"], (
            queued["runtimeStateHash"],
            canonical_changed["runtimeStateHash"],
        )
        assert canonical_changed["canonicalProductSetSnapshotHash"] == "sha256:canonical-b"

        state["value"] = {
            "dataVersion": None,
            "activeDataVersion": None,
            "activeDataVersionGate": "closed_no_active_import_runtime",
            "batchState": {},
            "summary": {"productTotal": 0, "failed": 0},
            "stages": [],
            "items": [],
        }
        reset = view._runtime_state("DV-STALE-BROWSER")
        assert reset["dataVersion"] is None, reset
        assert reset["identity"]["activeDataVersionGate"] == "closed_no_active_import_runtime", reset
        assert reset["identity"]["canonicalProductSnapshot"]["setSnapshotHash"] is None, reset
        assert reset["runtimeStateHash"].startswith("sha256:"), reset
        return {
            "queuedHash": queued["runtimeStateHash"],
            "completedHash": completed["runtimeStateHash"],
            "canonicalChangedHash": canonical_changed["runtimeStateHash"],
            "canonicalSetSnapshotHash": canonical_changed["canonicalProductSetSnapshotHash"],
            "resetHash": reset["runtimeStateHash"],
        }
    finally:
        view._canonical_product_runtime_identity = original_canonical
        if original_pipeline is None:
            sys.modules.pop(fake.__name__, None)
        else:
            sys.modules[fake.__name__] = original_pipeline


def _head_checks() -> Dict[str, Any]:
    original_runtime = view._runtime_state
    original_head = view._head_row
    original_materialize = view.materialize_frontend_views_v2259
    try:
        view._runtime_state = lambda _dv=None: {
            "dataVersion": "DV-SAME",
            "runtimeStateHash": "sha256:new-runtime",
            "canonicalProductSetSnapshotHash": "sha256:canonical-new",
            "identity": {},
            "pipeline": {},
        }
        view._head_row = lambda _scope: {
            "data_version": "DV-SAME",
            "runtime_state_hash": "sha256:old-runtime",
            "manifest_ref": "ART-OLD",
            "manifest_hash": "sha256:old-manifest",
            "status": "ready",
        }
        calls = []

        def fake_materialize(**kwargs: Any) -> Dict[str, Any]:
            calls.append(kwargs)
            return {
                "status": "ready",
                "dataVersion": kwargs["runtime_state"]["dataVersion"],
                "runtimeStateHash": kwargs["runtime_state"]["runtimeStateHash"],
                "canonicalProductSetSnapshotHash": kwargs["runtime_state"].get("canonicalProductSetSnapshotHash"),
                "manifestRef": "ART-NEW",
                "manifestHash": "sha256:new-manifest",
            }

        view.materialize_frontend_views_v2259 = fake_materialize
        changed = view.get_frontend_view_head_v2259(data_version="DV-SAME")
        assert changed["manifestRef"] == "ART-NEW", changed
        assert changed["canonicalProductSetSnapshotHash"] == "sha256:canonical-new", changed
        assert len(calls) == 1, calls

        view._runtime_state = lambda _dv=None: {
            "dataVersion": "DV-SAME",
            "runtimeStateHash": "sha256:same-runtime",
            "canonicalProductSetSnapshotHash": "sha256:canonical-same",
            "identity": {},
            "pipeline": {},
        }
        view._head_row = lambda _scope: {
            "data_version": "DV-SAME",
            "runtime_state_hash": "sha256:same-runtime",
            "pending_runtime_state_hash": None,
            "pending_data_version": None,
            "manifest_ref": "ART-SAME",
            "manifest_hash": "sha256:same-manifest",
            "status": "ready",
            "error": None,
            "updated_at": "2026-08-09T20:00:00",
        }

        def forbidden_materialize(**_kwargs: Any) -> Dict[str, Any]:
            raise AssertionError("unchanged Head identity must reuse immutable manifest")

        view.materialize_frontend_views_v2259 = forbidden_materialize
        same = view.get_frontend_view_head_v2259(data_version="DV-SAME")
        assert same["manifestRef"] == "ART-SAME", same
        assert same["runtimeStateHash"] == same["observedRuntimeStateHash"] == "sha256:same-runtime", same
        assert same["observedCanonicalProductSetSnapshotHash"] == "sha256:canonical-same", same
        return {
            "changedManifest": changed["manifestHash"],
            "reusedManifest": same["manifestHash"],
        }
    finally:
        view._runtime_state = original_runtime
        view._head_row = original_head
        view.materialize_frontend_views_v2259 = original_materialize


def _canonical_product_hash_lineage_checks() -> Dict[str, Any]:
    service = (ROOT / "src/services/frontend_view_artifact_v2259_service.py").read_text(encoding="utf-8")
    registry = json.loads((ROOT / "config/runtime_contract_lineage_registry_v1.json").read_text(encoding="utf-8"))
    runtime = json.loads((ROOT / "config/v23_registry_runtime.json").read_text(encoding="utf-8"))

    required_service = [
        'FRONTEND_VIEW_ARTIFACT_VERSION = "22.5.12"',
        "from src.services.system_product_snapshot_service import read_canonical_product_views",
        "lambda: read_canonical_product_views(data_version=data_version, limit=300)",
        "canonicalProductSetSnapshotHash",
        "canonicalProductSnapshot",
        "sourceSnapshotHash",
        "signalAdmissionIndependent",
    ]
    missing_service = [literal for literal in required_service if literal not in service]
    assert not missing_service, missing_service
    assert "lambda: read_product_views(data_version=data_version" not in service

    required_fields = {
        "canonical.set_snapshot_hash",
        "product.product_registry_key",
        "product.product_snapshot_hash",
        "frontend.runtime_state_hash",
        "frontend.module_business_hash",
        "frontend.module_content_hash",
        "frontend.module_artifact_ref",
        "frontend.manifest_hash",
        "frontend.manifest_artifact_ref",
    }
    fields = set((registry.get("fields") or {}).keys())
    assert required_fields <= fields, sorted(required_fields - fields)

    required_interfaces = {
        "canonical.product.view.project",
        "frontend.products.module.materialize",
        "frontend.manifest.publish",
        "frontend.hash.products.read",
    }
    interfaces = set((registry.get("interfaces") or {}).keys())
    assert required_interfaces <= interfaces, sorted(required_interfaces - interfaces)

    edge_keys = {
        (str(item.get("from")), str(item.get("to")), str(item.get("type")))
        for item in registry.get("lineageEdges") or []
        if isinstance(item, dict)
    }
    required_edges = {
        ("canonical.product.view.project", "frontend.products.module.materialize", "INTERFACE_HANDOFF"),
        ("canonical.set_snapshot_hash", "frontend.runtime_state_hash", "HASH_IDENTITY_INPUT"),
        ("canonical.set_snapshot_hash", "frontend.module_business_hash", "CONTENT_HASH_INPUT"),
        ("frontend.module_business_hash", "frontend.module_content_hash", "ARTIFACT_HASH_DERIVATION"),
        ("frontend.module_content_hash", "frontend.manifest_hash", "MANIFEST_HASH_INPUT"),
        ("frontend.runtime_state_hash", "frontend.manifest_hash", "MANIFEST_HASH_INPUT"),
        ("frontend.manifest_artifact_ref", "frontend.hash.products.read", "EXACT_REFERENCE_TRANSFER"),
        ("frontend.module_artifact_ref", "frontend.hash.products.read", "EXACT_REFERENCE_TRANSFER"),
    }
    assert required_edges <= edge_keys, sorted(required_edges - edge_keys)

    frontend_module = (runtime.get("modules") or {}).get("frontend_view") or {}
    runtime_fields = set(frontend_module.get("fieldIds") or [])
    assert required_fields <= runtime_fields, sorted(required_fields - runtime_fields)
    runtime_paths = set(frontend_module.get("implementationPaths") or [])
    required_paths = {
        "src/services/canonical_product_snapshot_service.py",
        "src/services/system_product_snapshot_service.py",
        "src/services/frontend_view_artifact_v2259_service.py",
        "web_demo/core/hash-view-client-v2259.js",
    }
    assert required_paths <= runtime_paths, sorted(required_paths - runtime_paths)
    assert runtime.get("runtimeContractLineageRegistryVersion") == "2026.08.11.3", runtime
    assert runtime.get("frontendViewRuntimeScopeVersion") == "23.2.13", runtime

    source_identity = view._module_source_identity(
        "products",
        {
            "currentDataVersion": "DV-SAME",
            "setSnapshotHash": "sha256:set",
            "count": 30,
        },
    )
    assert source_identity == {
        "authority": "canonical_product_snapshot_service",
        "dataVersion": "DV-SAME",
        "setSnapshotHash": "sha256:set",
        "productCount": 30,
        "signalAdmissionIndependent": True,
    }, source_identity

    return {
        "registryVersion": registry.get("version"),
        "runtimeScopeVersion": runtime.get("frontendViewRuntimeScopeVersion"),
        "requiredFieldCount": len(required_fields),
        "requiredInterfaceCount": len(required_interfaces),
        "requiredEdgeCount": len(required_edges),
        "canonicalProductCountProbe": source_identity["productCount"],
        "signalAdmissionIndependent": source_identity["signalAdmissionIndependent"],
    }


def _client_checks() -> Dict[str, Any]:
    source = (ROOT / "web_demo/core/hash-view-client-v2259.js").read_text(encoding="utf-8")
    required = [
        'const VERSION = "22.5.11";',
        'cache: "no-store"',
        "_headNonce",
        "runtimeStateHash",
        "manifestHash",
        "readImmutable(expectedHash)",
        "product-detail-artifact:",
        "/api/modules/product-detail-v2256/",
        "browserCacheState: \"hash_hit\"",
        "detailContentHash",
        "detailArtifactRef",
        "nativeFetch",
        'api.productView = (params = {}) => moduleView("products"',
    ]
    missing = [literal for literal in required if literal not in source]
    assert not missing, missing
    assert 'const VERSION = "22.5.9";' not in source
    assert 'const VERSION = "22.5.10";' not in source
    return {"requiredLiteralCount": len(required), "missing": missing}


def _product_detail_runtime_checks() -> Dict[str, Any]:
    route = (ROOT / "src/api/routes/modules/product_detail_v2256.py").read_text(encoding="utf-8")
    trend = (ROOT / "src/services/canonical_product_trend_v2_service.py").read_text(encoding="utf-8")
    route_required = [
        "frontend_product_detail.hash.v1",
        "detailArtifactRef",
        "detailContentHash",
        "historyIdentityHash",
        "content_addressed_product_detail",
    ]
    trend_required = [
        "metadata_then_single_row_single_product",
        "wholeSnapshotRetention",
        "SELECT payload FROM canonical_product_snapshot_sets_v1 WHERE snapshot_id=? LIMIT 1",
        "_slim_product",
    ]
    missing_route = [literal for literal in route_required if literal not in route]
    missing_trend = [literal for literal in trend_required if literal not in trend]
    assert not missing_route, missing_route
    assert not missing_trend, missing_trend
    assert "product_snapshot_history(limit=limit)" not in trend
    assert "get_product_snapshot(data_version=data_version" not in trend
    return {
        "routeRequired": len(route_required),
        "trendRequired": len(trend_required),
        "missingRoute": missing_route,
        "missingTrend": missing_trend,
    }


def main() -> int:
    report = {
        "schema": "competition.frontend_runtime_hash_behavior.v3",
        "runtime": _runtime_hash_checks(),
        "head": _head_checks(),
        "canonicalProducts": _canonical_product_hash_lineage_checks(),
        "client": _client_checks(),
        "productDetail": _product_detail_runtime_checks(),
        "verified": True,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
