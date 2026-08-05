from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/v23_registry_runtime.json"
MODULES = ROOT / "contracts/registry/modules.json"
FRONTEND_PATHS = [
    "config/v23_registry_runtime.json",
    "src/api/routes/frontend_views.py",
    "src/services/frontend_view_artifact_v2259_service.py",
    "src/services/public_task_dto_service.py",
    "web_demo/index.html",
    "web_demo/bootstrap.js",
    "web_demo/core/router.js",
    "web_demo/core/task-read-model-v2082.js",
    "web_demo/modules/task-report/page.js",
    "web_demo/loading-ui.css",
]
UNCHANGED_PATHS = [
    path
    for path in FRONTEND_PATHS
    if path
    not in {
        "config/v23_registry_runtime.json",
        "src/services/public_task_dto_service.py",
    }
]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_frontend_view_runtime_scope_is_exact() -> None:
    config = _read(CONFIG)
    module = config["modules"]["frontend_view"]
    assert module["implementationPaths"] == FRONTEND_PATHS
    assert module["runner"] == "src.services.frontend_view_artifact_v2259_service:materialize_frontend_views_v2259"
    assert module["schemaIds"] == [
        "frontend_view.module.v2259",
        "frontend_view.manifest.v2259",
    ]
    assert "frontend_view" in config["requiredModules"]
    assert config["frontendViewRuntimeScopeVersion"] == "23.2.12"


def test_runner_matches_registry_truth() -> None:
    config = _read(CONFIG)
    registry = _read(MODULES)
    definition = next(item for item in registry["modules"] if item["moduleId"] == "frontend_view")
    assert config["modules"]["frontend_view"]["runner"] == definition["runner"]


def test_v23_2_12_does_not_change_other_frontend_runtime_files() -> None:
    for path in UNCHANGED_PATHS:
        working = subprocess.check_output(["git", "hash-object", path], cwd=ROOT, text=True).strip()
        main_bytes = subprocess.check_output(["git", "show", f"origin/main:{path}"], cwd=ROOT)
        main_hash = subprocess.check_output(["git", "hash-object", "--stdin"], cwd=ROOT, input=main_bytes).decode().strip()
        assert working == main_hash, path
