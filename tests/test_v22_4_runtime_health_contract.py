from __future__ import annotations

from pathlib import Path

from src.api.main import app

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WRAPPER = ROOT / "scripts" / "deploy_release.sh"
DEPLOY_CORE = ROOT / "src" / "deployment" / "deploy_release_core_v22516.sh"


def _query_parameter(path: str, name: str) -> dict:
    operation = app.openapi()["paths"][path]["get"]
    for parameter in operation.get("parameters") or []:
        if parameter.get("name") == name:
            return parameter
    raise AssertionError(f"missing query parameter: {path}:{name}")


def test_release_identity_defaults_to_startup_verified_fast_path() -> None:
    parameter = _query_parameter("/api/system/release-identity", "verifyContent")
    assert parameter["required"] is False
    assert parameter["schema"]["default"] is False

    source = (ROOT / "src" / "api" / "routes" / "system.py").read_text(encoding="utf-8")
    assert "release_identity(verify_content=verifyContent)" in source
    assert 'verificationDepth="content" if verifyContent else "startup_verified_cache"' in source
    assert "contentVerificationRequested=verifyContent" in source


def test_deployment_retries_cached_release_and_deep_data_identity_checks() -> None:
    wrapper = DEPLOY_WRAPPER.read_text(encoding="utf-8")
    source = DEPLOY_CORE.read_text(encoding="utf-8")

    assert DEPLOY_CORE.relative_to(ROOT).as_posix() in wrapper
    assert "fetch_json_with_retry()" in source
    assert "AI_RELEASE_IDENTITY_ATTEMPTS:-6" in source
    assert "AI_RELEASE_IDENTITY_TIMEOUT:-20" in source
    assert "AI_DATA_IDENTITY_ATTEMPTS:-3" in source
    assert "AI_DATA_IDENTITY_TIMEOUT:-180" in source
    assert "/api/system/release-identity?verifyContent=false" in source
    assert "/api/system/data-identity?contentHash=false" in source
    assert 'v.get("verificationDepth")=="startup_verified_cache"' in source
    assert 'v.get("contentVerificationRequested") is False' in source
    assert '--max-time 12 "http://127.0.0.1:${PORT}/api/system/release-identity"' not in source
    assert '--max-time 12 "http://127.0.0.1:${PORT}/api/system/data-identity"' not in source


def test_deep_release_verification_remains_available() -> None:
    parameter = _query_parameter("/api/system/release-identity", "verifyContent")
    description = str(parameter.get("description") or parameter["schema"].get("description") or "")
    assert "Recompute every sealed file hash" in description

    startup = (ROOT / "src" / "api" / "main.py").read_text(encoding="utf-8")
    identity = (ROOT / "src" / "services" / "release_identity_service.py").read_text(
        encoding="utf-8"
    )
    assert "assert_release_identity()" in startup
    assert "identity = release_identity(verify_content=True)" in identity
