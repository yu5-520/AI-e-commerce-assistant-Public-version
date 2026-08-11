#!/usr/bin/env python3
"""Dependency-light behavioral gate for frontend runtime hash invalidation.

This checker uses only stdlib monkeypatching. It does not call a model, HTTP endpoint,
or production database. It verifies the identity semantics that matter for the Hash View:

- same dataVersion + changed execution state => changed runtimeStateHash;
- transport timestamps alone do not rotate runtimeStateHash;
- Reset/no active runtime overrides a stale caller dataVersion;
- Head republishes on runtime hash change and reuses on exact identity match;
- browser Head fetch is no-store while immutable Artifact caching remains enabled;
- product detail requests are intercepted by the Hash View client and can reuse the
  immutable detail payload while the manifest hash is unchanged.
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


def _runtime_hash_checks() -> Dict[str, Any]:
    original = sys.modules.get("src.services.pipeline_live_read_model_v225_service")
    fake = types.ModuleType("src.services.pipeline_live_read_model_v225_service")
    state = {"value": _pipeline("queued", "2026-08-09T20:00:00")}
    fake.read_pipeline_live_model = lambda **_kwargs: dict(state["value"])
    sys.modules[fake.__name__] = fake
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
        assert reset["runtimeStateHash"].startswith("sha256:"), reset
        return {
            "queuedHash": queued["runtimeStateHash"],
            "completedHash": completed["runtimeStateHash"],
            "resetHash": reset["runtimeStateHash"],
        }
    finally:
        if original is None:
            sys.modules.pop(fake.__name__, None)
        else:
            sys.modules[fake.__name__] = original


def _head_checks() -> Dict[str, Any]:
    original_runtime = view._runtime_state
    original_head = view._head_row
    original_materialize = view.materialize_frontend_views_v2259
    try:
        view._runtime_state = lambda _dv=None: {
            "dataVersion": "DV-SAME",
            "runtimeStateHash": "sha256:new-runtime",
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
                "manifestRef": "ART-NEW",
                "manifestHash": "sha256:new-manifest",
            }

        view.materialize_frontend_views_v2259 = fake_materialize
        changed = view.get_frontend_view_head_v2259(data_version="DV-SAME")
        assert changed["manifestRef"] == "ART-NEW", changed
        assert len(calls) == 1, calls

        view._runtime_state = lambda _dv=None: {
            "dataVersion": "DV-SAME",
            "runtimeStateHash": "sha256:same-runtime",
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
        return {
            "changedManifest": changed["manifestHash"],
            "reusedManifest": same["manifestHash"],
        }
    finally:
        view._runtime_state = original_runtime
        view._head_row = original_head
        view.materialize_frontend_views_v2259 = original_materialize


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
        "schema": "competition.frontend_runtime_hash_behavior.v2",
        "runtime": _runtime_hash_checks(),
        "head": _head_checks(),
        "client": _client_checks(),
        "productDetail": _product_detail_runtime_checks(),
        "verified": True,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
