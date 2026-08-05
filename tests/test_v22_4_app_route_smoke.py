from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_routes_load_in_clean_python_process() -> None:
    code = r'''
import json
from src.api.main import api_version, app
from src.api.routes import system as system_routes

expected_system_paths = {
    "/api/system/release-identity",
    "/api/system/data-identity",
}
app_paths = sorted((app.openapi().get("paths") or {}).keys())
system_paths = sorted(
    getattr(route, "path", "")
    for route in system_routes.router.routes
)
value = api_version()
result = {
    "appModule": app.__module__,
    "appPaths": app_paths,
    "systemModule": system_routes.__file__,
    "systemPrefix": system_routes.router.prefix,
    "systemPaths": system_paths,
    "version": value.get("version"),
    "runtimeMode": value.get("runtimeMode"),
    "releaseIdentitySchema": (value.get("releaseIdentity") or {}).get("schema"),
}
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
assert system_routes.router.prefix == "/api/system", result
assert expected_system_paths <= set(app_paths), result
assert expected_system_paths <= set(system_paths), result
assert "/release-identity" not in system_paths, result
assert "/data-identity" not in system_paths, result
assert result["version"] == "22.4.0", result
assert result["runtimeMode"] == "single_release_sealed_runtime", result
assert result["releaseIdentitySchema"] == "release.identity.v1", result
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["AI_RELEASE_REQUIRED"] = "0"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert completed.returncode == 0, (
        "clean-process FastAPI route smoke test failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["appModule"] == "fastapi.applications"
    assert payload["systemModule"].endswith("src/api/routes/system.py")
    assert payload["systemPrefix"] == "/api/system"
