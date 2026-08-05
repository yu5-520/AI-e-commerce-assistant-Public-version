from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load(relative: str, module_name: str) -> ModuleType:
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lineage = _load(
    "src/services/runtime_lineage_z1_1_service.py",
    "_test_z1_runtime_lineage",
)
server_identity = _load(
    "src/services/server_runtime_identity_z1_service.py",
    "_test_z1_server_runtime_identity",
)


def test_z1_runtime_lineage_is_complete_and_fail_closed() -> None:
    report = lineage.build_runtime_lineage_report(ROOT)

    assert report["version"] == lineage.RUNTIME_LINEAGE_VERSION == "Z1.0.4"
    assert report["verified"] is True, report["findings"]
    assert report["findings"] == []
    assert report["databaseMutated"] is False
    assert report["providerCallsExecuted"] == 0
    assert set(report["requiredKinds"]) == {
        "http",
        "runner",
        "worker",
        "scheduler",
        "cli",
        "server",
    }
    assert set(report["actualKinds"]) == set(report["requiredKinds"])
    assert report["counts"]["runner"] == 15
    assert report["counts"]["worker"] == 1
    assert report["counts"]["scheduler"] == 1
    assert report["counts"]["cli"] == 5
    assert report["counts"]["server"] == 1
    assert report["counts"]["http"] > 100


def test_z1_business_interface_sources_equal_mounted_fastapi_routers() -> None:
    report = lineage.build_runtime_lineage_report(ROOT)
    evidence = report["httpEvidence"]

    assert evidence["registeredRouterModules"] == evidence["mountedRouterModules"]
    assert evidence["legacyInterfacesMissingFromRuntime"] == []
    assert "modules" in evidence["packageRouterFiles"]
    assert len(evidence["packageRouterFiles"]["modules"]) >= 10

    http_entries = [
        entry for entry in report["entries"] if entry.get("kind") == "http"
    ]
    assert any(
        entry["method"] == "GET" and entry["path"] == "/api/version"
        for entry in http_entries
    )
    assert any(
        entry["method"] == "GET" and entry["path"] == "/api/health"
        for entry in http_entries
    )
    assert any(
        entry["method"] == "GET" and entry["path"] == "/api/modules/dashboard"
        for entry in http_entries
    )
    assert all(entry.get("errorOwner") for entry in http_entries)
    assert all(
        entry.get("errorPathMode")
        in {"explicit_http_exception", "fastapi_exception_boundary"}
        for entry in http_entries
    )
    assert all(not str(entry["sourcePath"]).startswith("/") for entry in http_entries)


def test_z1_active_module_projection_equals_active_registry() -> None:
    report = lineage.build_runtime_lineage_report(ROOT)
    evidence = report["runnerEvidence"]

    active = evidence["activeRegistryModules"]
    assert active == evidence["registeredActiveModules"]
    assert active == evidence["requiredRuntimeProjectionModules"]
    assert set(active).issubset(set(evidence["runtimeProjectionModules"]))

    runner_entries = [
        entry for entry in report["entries"] if entry.get("kind") == "runner"
    ]
    assert len(runner_entries) == len(active) == 15
    assert all(entry["validation"]["exists"] is True for entry in runner_entries)
    assert all(entry["validation"]["symbolExists"] is True for entry in runner_entries)
    assert all(entry["runtimeProjectionRequired"] is True for entry in runner_entries)
    assert all(
        entry["entry"] == entry["runtimeProjectionRunner"]
        for entry in runner_entries
    )


def test_z1_server_runtime_identity_is_canonical(monkeypatch) -> None:
    monkeypatch.delenv("AI_RUNTIME_APPLICATION_ID", raising=False)
    monkeypatch.delenv("AI_RUNTIME_ENTRY", raising=False)
    monkeypatch.delenv("AI_RUNTIME_LINEAGE_VERSION", raising=False)

    identity = server_identity.server_runtime_identity()

    assert identity["applicationId"] == server_identity.CANONICAL_APPLICATION_ID
    assert identity["asgiEntry"] == server_identity.CANONICAL_ASGI_ENTRY
    assert identity["runtimeLineageVersion"] == (
        server_identity.CANONICAL_RUNTIME_LINEAGE_VERSION
    )
    assert identity["applicationIdMatch"] is True
    assert identity["asgiEntryMatch"] is True
    assert identity["runtimeLineageVersionMatch"] is True
    assert identity["verified"] is True
    assert identity["databaseMutated"] is False
    assert identity["providerCallsExecuted"] == 0


def test_z1_registration_contract_covers_error_paths_and_server_entries() -> None:
    registration = json.loads(
        (ROOT / "contracts/runtime/active-runtime-registration.json").read_text(
            encoding="utf-8"
        )
    )
    acceptance = registration["lineageAcceptance"]
    application = registration["application"]
    worker = registration["nonHttp"]["workers"][0]

    assert registration["version"] == "Z1.0.4"
    assert acceptance["requireEveryRuntimeErrorOwner"] is True
    assert acceptance["requireNoUnregisteredRuntimeSource"] is True
    assert acceptance["databaseMutationAllowed"] is False
    assert acceptance["providerCallsAllowed"] is False
    assert application["startupScript"] == "scripts/start_server_z1.sh"
    assert application["deploymentScript"] == "scripts/deploy_release_z1.sh"
    assert application["serviceIdentityMode"] == (
        "listener_pid_systemd_cgroup_and_runtime_root"
    )
    assert worker["stableFacade"].endswith("station_agent_worker_v2255_service")
    assert worker["activeImplementation"].endswith(
        "station_agent_worker_v2259_service"
    )
    assert worker["startEntry"].startswith(worker["activeImplementation"] + ":")
