from src.services import canonical_product_snapshot_v215_bridge_service as bridge
from src.services import product_signal_snapshot_service as signal_snapshot
from src.services import system_product_snapshot_service as product_snapshot


def test_v215_bridge_replaces_legacy_snapshot_materializer(monkeypatch):
    legacy = lambda *args, **kwargs: {"legacy": True}
    monkeypatch.setattr(product_snapshot, "materialize_system_product_snapshot", legacy)
    monkeypatch.setattr(signal_snapshot, "materialize_system_product_snapshot", legacy)

    installed = bridge.install_canonical_product_snapshot_v215_bridge()

    assert installed["installed"] is True
    assert installed["legacySnapshotTableRead"] is False
    assert installed["legacySnapshotTableWrite"] is False
    assert product_snapshot.materialize_system_product_snapshot is bridge.materialize_canonical_product_snapshot_v215
    assert signal_snapshot.materialize_system_product_snapshot is bridge.materialize_canonical_product_snapshot_v215


def test_v215_bridge_materializes_only_through_canonical_root(monkeypatch):
    calls = []

    def fake_materialize(data_version=None, *, user_id=None, force=True):
        calls.append((data_version, user_id, force))
        return {
            "dataVersion": data_version,
            "productCount": 2,
            "productSnapshotRef": f"canonical_product_snapshot:{data_version}",
            "outputRef": f"canonical_product_snapshot:{data_version}",
        }

    monkeypatch.setattr(bridge.canonical, "materialize_canonical_product_snapshot", fake_materialize)
    monkeypatch.setattr(bridge, "_batch_id", lambda data_version: f"RB:{data_version}")

    result = bridge.materialize_canonical_product_snapshot_v215(
        "DV-TEST",
        user_id="competition_operator",
        force=True,
    )

    assert calls == [("DV-TEST", "competition_operator", True)]
    assert result["productSnapshotRef"] == "canonical_product_snapshot:DV-TEST"
    assert result["reportBatchId"] == "RB:DV-TEST"
    assert result["canonicalProductSnapshot"] is True
    assert result["legacySnapshotTableRead"] is False
    assert result["legacySnapshotTableWrite"] is False
