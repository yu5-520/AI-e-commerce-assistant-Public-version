from src.services import station_alignment_v225_service as station


def test_full_bundle_binding_propagates_canonical_hash_into_agent_package(monkeypatch):
    product_hash = "sha256:canonical-product-001"
    monkeypatch.setattr(
        station,
        "get_product_snapshot",
        lambda data_version=None: {
            "dataVersion": data_version,
            "products": [
                {
                    "objectId": "taobao::STORE-1::P-100::SKU-1",
                    "productId": "P-100",
                    "productSnapshotHash": product_hash,
                    "factRefs": ["FACT-1"],
                    "factHashRefs": ["sha256:fact-1"],
                    "sourceArtifactRefs": ["report:masked-1"],
                    "profileSnapshot": {"skuId": "SKU-1"},
                }
            ],
        },
    )

    source = {
        "productSignalPackages": [
            {
                "entityId": "taobao::STORE-1::P-100::SKU-1",
                "productId": "P-100",
                "agentProductSnapshotPackage": {"contract": "fullProductBundle"},
            }
        ]
    }

    bound = station._bind_canonical_lineage("DV-1", source)
    package = bound["productSignalPackages"][0]

    assert package["productSnapshotHash"] == product_hash
    assert package["parentSnapshotHash"] == product_hash
    assert package["factRefs"] == ["FACT-1"]
    assert package["factHashRefs"] == ["sha256:fact-1"]
    assert package["agentProductSnapshotPackage"]["productSnapshotHash"] == product_hash
    assert package["agentProductSnapshotPackage"]["parentSnapshotHash"] == product_hash
    assert bound["canonicalLineage"]["complete"] is True
    assert bound["canonicalLineage"]["missingPackageCount"] == 0


def test_full_bundle_binding_marks_missing_lineage_without_rebuilding_facts(monkeypatch):
    monkeypatch.setattr(station, "get_product_snapshot", lambda data_version=None: {"products": []})

    source = {
        "signals": [
            {
                "entityId": "unknown-product",
                "productId": "P-X",
                "agentProductSnapshotPackage": {"contract": "fullProductBundle"},
            }
        ]
    }

    bound = station._bind_canonical_lineage("DV-X", source)
    package = bound["productSignalPackages"][0]

    assert package["canonicalLineageMissing"] is True
    assert "productSnapshotHash" not in package
    assert bound["canonicalLineage"]["complete"] is False
    assert bound["canonicalLineage"]["missingPackageCount"] == 1
