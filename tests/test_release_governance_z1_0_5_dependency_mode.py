from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> dict:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _verifier_module():
    path = ROOT / ".z/tools/verify_dependency_mode.py"
    spec = importlib.util.spec_from_file_location("z_dependency_mode_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_mode_receipt_is_exact_and_side_effect_free() -> None:
    result = _verifier_module().verify(check_receipt=True)

    assert result["verified"] is True
    assert result["findings"] == []
    assert result["embeddedGenericSourceRemoved"] is True
    assert result["productRuntimeChanged"] is False
    assert result["serverBindingChanged"] is False
    assert result["databaseMutated"] is False
    assert result["providerCallsExecuted"] == 0
    assert result["ecsMutated"] is False
    assert result["secondZRuntimeStarted"] is False


def test_generic_source_is_absent_and_product_adapter_is_retained() -> None:
    manifest = _read(".z/dependency-manifest.json")
    embedded = manifest["embeddedGenericSource"]
    for relative in embedded["forbiddenImplementationPaths"]:
        assert not (ROOT / relative).exists(), relative
    for relative in embedded["removedProtocolRoots"]:
        assert not (ROOT / relative).exists(), relative
    for relative in embedded["allowedShimPaths"]:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "tools.z_adapter" in text
    for relative in (
        ".z/adapter/adapter-manifest.json",
        ".z/z.lock.json",
        ".z/install-manifest.json",
        "contracts/self_update/product-capabilities.json",
        "migration/Z1.0.5_SOURCE_MANIFEST.json",
    ):
        assert (ROOT / relative).is_file(), relative


def test_sealed_runtime_registration_is_preserved_and_paths_are_shims() -> None:
    registration = _read("contracts/runtime/active-runtime-registration.json")
    entries = {
        item["runtimeId"]: item
        for item in registration["nonHttp"]["cliEntries"]
    }
    expected = {
        "registry_compile_cli": (
            "tools.registry_compiler.compile_registry:main",
            "tools/registry_compiler/compile_registry.py",
        ),
        "requirement_self_update_cli": (
            "tools.registry_compiler.v231_self_update:main",
            "tools/registry_compiler/v231_self_update.py",
        ),
        "repository_self_update_cli": (
            "tools.self_update.cli:main",
            "tools/self_update/cli.py",
        ),
    }
    for runtime_id, (entry, relative) in expected.items():
        assert entries[runtime_id]["entry"] == entry
        assert "tools.z_adapter" in (ROOT / relative).read_text(encoding="utf-8")
    assert registration["application"]["asgiEntry"] == "src.api.main:app"
    assert registration["lineageAcceptance"]["databaseMutationAllowed"] is False
    assert registration["lineageAcceptance"]["providerCallsAllowed"] is False


def test_dependency_is_exact_and_has_no_embedded_fallback() -> None:
    manifest = _read(".z/dependency-manifest.json")
    dependency = manifest["dependency"]
    assert dependency["repository"] == "yu5-520/Z-Century"
    assert dependency["releaseRef"] == "release/z1.0.5-bootstrap-source"
    assert dependency["sourceCommit"] == (
        "c924e646673a41857cca5e39650e022f9ce8c0a5"
    )
    assert dependency["mutableReferenceAllowed"] is False
    assert dependency["fallbackToEmbeddedSourceAllowed"] is False
    assert manifest["runtimeBoundary"]["secondZRuntimeAuthorized"] is False
    assert manifest["rollbackCommit"] == (
        "79d4034ae3b395edaf8318829529189c785668a5"
    )
