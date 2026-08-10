from __future__ import annotations

from src.services import task_snapshot_station_service as guard


def test_active_data_version_materializes_before_strict_bind(monkeypatch):
    events = []

    monkeypatch.setattr(guard, "_active_data_version_exists", lambda data_version: data_version == "dv-current")

    def fake_materialize(data_version, *, user_id=None, force=True):
        events.append(("materialize", data_version, user_id, force))
        return {"dataVersion": data_version}

    def fake_bind(task):
        events.append(("bind", task.get("dataVersion")))
        result = dict(task)
        result["productSnapshotHash"] = "hash-current"
        result["productSnapshotLineage"] = {"ready": True, "status": "resolved"}
        return result

    monkeypatch.setattr(guard, "materialize_system_product_snapshot", fake_materialize)
    monkeypatch.setattr(guard, "bind_task_product_lineage", fake_bind)

    result = guard._prepare_task_product_lineage(
        {"dataVersion": "dv-current", "productRegistryKey": "sku-1"},
        user_id="operator-1",
    )

    assert events == [
        ("materialize", "dv-current", "operator-1", False),
        ("bind", "dv-current"),
    ]
    assert result["productSnapshotHash"] == "hash-current"
    assert result["productSnapshotLineage"]["writeBarrier"] == "canonical_snapshot_before_task_timestamp"


def test_inactive_data_version_cannot_resurrect_stale_canonical_lineage(monkeypatch):
    monkeypatch.setattr(guard, "_active_data_version_exists", lambda _data_version: False)

    def forbidden_materialize(*_args, **_kwargs):
        raise AssertionError("inactive dataVersion must not be materialized")

    def forbidden_bind(*_args, **_kwargs):
        raise AssertionError("inactive dataVersion must not resolve from stale canonical rows")

    monkeypatch.setattr(guard, "materialize_system_product_snapshot", forbidden_materialize)
    monkeypatch.setattr(guard, "bind_task_product_lineage", forbidden_bind)

    result = guard._prepare_task_product_lineage(
        {
            "dataVersion": "dv-stale",
            "productSnapshotHash": "hash-stale",
            "productSnapshot": {"gmv": 999},
        }
    )

    assert result["productSnapshotHash"] == "hash-stale"
    assert result["productSnapshot"] == {}
    assert result["productSnapshotStatus"] == "lineage_broken"
    assert result["productSnapshotLineage"]["ready"] is False
    assert result["productSnapshotLineage"]["reason"] == "task_data_version_not_active"
    assert result["productSnapshotLineage"]["strictHash"] is True


def test_missing_data_version_keeps_existing_strict_binding_behavior(monkeypatch):
    events = []

    monkeypatch.setattr(
        guard,
        "materialize_system_product_snapshot",
        lambda *_args, **_kwargs: events.append("materialize"),
    )

    def fake_bind(task):
        events.append("bind")
        return {**task, "productSnapshotLineage": {"ready": False, "status": "unbound"}}

    monkeypatch.setattr(guard, "bind_task_product_lineage", fake_bind)

    result = guard._prepare_task_product_lineage({"productRegistryKey": "sku-1"})

    assert events == ["bind"]
    assert result["productSnapshotLineage"]["status"] == "unbound"
