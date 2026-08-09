from __future__ import annotations

from pathlib import Path


def _pipeline(*, status: str, updated_at: str = "2026-08-09T20:00:00") -> dict:
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


def test_same_dataversion_runtime_transition_changes_runtime_state_hash(monkeypatch):
    from src.services import frontend_view_artifact_v2259_service as view
    from src.services import pipeline_live_read_model_v225_service as pipeline

    state = {"value": _pipeline(status="queued")}
    monkeypatch.setattr(
        pipeline,
        "read_pipeline_live_model",
        lambda **_: dict(state["value"]),
    )

    first = view._runtime_state("DV-SAME")
    state["value"] = _pipeline(status="completed")
    second = view._runtime_state("DV-SAME")

    assert first["dataVersion"] == "DV-SAME"
    assert second["dataVersion"] == "DV-SAME"
    assert first["runtimeStateHash"] != second["runtimeStateHash"]


def test_volatile_timestamp_does_not_change_runtime_state_hash(monkeypatch):
    from src.services import frontend_view_artifact_v2259_service as view
    from src.services import pipeline_live_read_model_v225_service as pipeline

    state = {"value": _pipeline(status="queued", updated_at="2026-08-09T20:00:00")}
    monkeypatch.setattr(
        pipeline,
        "read_pipeline_live_model",
        lambda **_: dict(state["value"]),
    )

    first = view._runtime_state("DV-SAME")
    state["value"] = _pipeline(status="queued", updated_at="2026-08-09T20:01:00")
    second = view._runtime_state("DV-SAME")

    assert first["runtimeStateHash"] == second["runtimeStateHash"]


def test_reset_runtime_invalidates_stale_requested_dataversion(monkeypatch):
    from src.services import frontend_view_artifact_v2259_service as view
    from src.services import pipeline_live_read_model_v225_service as pipeline

    monkeypatch.setattr(
        pipeline,
        "read_pipeline_live_model",
        lambda **_: {
            "dataVersion": None,
            "activeDataVersion": None,
            "activeDataVersionGate": "closed_no_active_import_runtime",
            "batchState": {},
            "summary": {"productTotal": 0, "failed": 0},
            "stages": [],
            "items": [],
        },
    )

    state = view._runtime_state("DV-STALE-BROWSER")

    assert state["dataVersion"] is None
    assert state["identity"]["activeDataVersionGate"] == "closed_no_active_import_runtime"
    assert state["runtimeStateHash"].startswith("sha256:")


def test_head_republishes_when_same_dataversion_runtime_hash_changes(monkeypatch):
    from src.services import frontend_view_artifact_v2259_service as view

    monkeypatch.setattr(
        view,
        "_runtime_state",
        lambda _dv=None: {
            "dataVersion": "DV-SAME",
            "runtimeStateHash": "sha256:new-runtime",
            "identity": {},
            "pipeline": {},
        },
    )
    monkeypatch.setattr(
        view,
        "_head_row",
        lambda _scope: {
            "data_version": "DV-SAME",
            "runtime_state_hash": "sha256:old-runtime",
            "manifest_ref": "ART-OLD",
            "manifest_hash": "sha256:old-manifest",
            "status": "ready",
        },
    )
    calls = []

    def fake_materialize(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ready",
            "dataVersion": kwargs["runtime_state"]["dataVersion"],
            "runtimeStateHash": kwargs["runtime_state"]["runtimeStateHash"],
            "manifestRef": "ART-NEW",
            "manifestHash": "sha256:new-manifest",
        }

    monkeypatch.setattr(view, "materialize_frontend_views_v2259", fake_materialize)

    head = view.get_frontend_view_head_v2259(data_version="DV-SAME")

    assert head["manifestRef"] == "ART-NEW"
    assert head["runtimeStateHash"] == "sha256:new-runtime"
    assert len(calls) == 1


def test_head_reuses_manifest_when_data_and_runtime_hash_are_unchanged(monkeypatch):
    from src.services import frontend_view_artifact_v2259_service as view

    monkeypatch.setattr(
        view,
        "_runtime_state",
        lambda _dv=None: {
            "dataVersion": "DV-SAME",
            "runtimeStateHash": "sha256:same-runtime",
            "identity": {},
            "pipeline": {},
        },
    )
    monkeypatch.setattr(
        view,
        "_head_row",
        lambda _scope: {
            "data_version": "DV-SAME",
            "runtime_state_hash": "sha256:same-runtime",
            "pending_runtime_state_hash": None,
            "pending_data_version": None,
            "manifest_ref": "ART-SAME",
            "manifest_hash": "sha256:same-manifest",
            "status": "ready",
            "error": None,
            "updated_at": "2026-08-09T20:00:00",
        },
    )

    def should_not_materialize(**_kwargs):
        raise AssertionError("unchanged runtime hash must reuse current immutable manifest")

    monkeypatch.setattr(view, "materialize_frontend_views_v2259", should_not_materialize)

    head = view.get_frontend_view_head_v2259(data_version="DV-SAME")

    assert head["manifestRef"] == "ART-SAME"
    assert head["runtimeStateHash"] == "sha256:same-runtime"
    assert head["observedRuntimeStateHash"] == "sha256:same-runtime"
    assert head["displayMode"] == "current"


def test_hash_view_client_fetches_mutable_head_no_store_and_keeps_immutable_hash_cache():
    source = Path("web_demo/core/hash-view-client-v2259.js").read_text(encoding="utf-8")

    assert 'const VERSION = "22.5.10";' in source
    assert 'cache: "no-store"' in source
    assert '"_headNonce"' in source or "_headNonce" in source
    assert "runtimeStateHash" in source
    assert "manifestHash" in source
    assert "readImmutable(expectedHash)" in source
    assert 'const VERSION = "22.5.9";' not in source
