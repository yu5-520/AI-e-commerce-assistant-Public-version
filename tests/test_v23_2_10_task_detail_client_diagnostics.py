from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web_demo/index.html"
BOOTSTRAP = ROOT / "web_demo/bootstrap.js"
READ_MODEL = ROOT / "web_demo/core/task-read-model-v2082.js"
ROUTER = ROOT / "web_demo/core/router.js"
REPORT_PAGE = ROOT / "web_demo/modules/task-report/page.js"
CSS = ROOT / "web_demo/loading-ui.css"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cache_contract_and_asset_versions_are_current() -> None:
    index = _text(INDEX)
    bootstrap = _text(BOOTSTRAP)
    assert 'const version = "23.2.11"' in index
    assert 'key.startsWith("task-detail-snapshot-")' in index
    assert "?v=22.5.6" not in index
    assert "?v=22.5.9" not in index
    assert 'const ASSET_VERSION = "23.2.11"' in bootstrap
    assert 'installTaskDetailPayloadGuardV23211' in bootstrap


def test_task_detail_forces_network_and_exposes_safe_diagnostics() -> None:
    model = _text(READ_MODEL)
    page = _text(REPORT_PAGE)
    assert 'DETAIL_CACHE_PREFIX = "task-detail-snapshot-v23210:"' in model
    assert "function clearTaskDetailCache" in model
    assert 'stage: "task_detail_http"' in model
    assert 'stage: "task_detail_json"' in model
    assert 'stage: "task_detail_ready_check"' in model
    assert 'stage: "normalize_task_detail"' in model
    assert "clearTaskDetailCache, annotateTaskDetailError" in model
    assert "forceNetwork: true" in page
    assert "timeoutMs: 7000" in page
    assert '"task_report_load"' in page


def test_router_shows_only_whitelisted_safe_fields_and_clears_retry_cache() -> None:
    router = _text(ROUTER)
    for field in (
        "route", "taskId", "stage", "requestPath", "httpStatus",
        "responseReady", "errorName", "errorMessage", "timestamp",
    ):
        assert f'"{field}"' in router or f"{field}:" in router
    assert "window.__LAST_ROUTE_ERROR__" in router
    assert 'frontend-last-route-error-v23210' in router
    assert "data-router-copy" in router
    assert "clearTaskDetailCache?.(diagnostic.taskId)" in router
    assert "error.stack" not in router
    assert "请求头" not in router
    assert "Cookie" not in router
    assert "authorization" not in router.lower()
    assert "cookie" not in router.lower()


def test_task_detail_diagnostic_styles_support_ipad_layout() -> None:
    css = _text(CSS)
    assert ".route-error-diagnostics" in css
    assert ".route-error-copy-row" in css
    assert "overflow-wrap: anywhere" in css
    assert "@media (max-width: 620px)" in css


def test_changed_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        return
    for path in (BOOTSTRAP, READ_MODEL, ROUTER, REPORT_PAGE):
        completed = subprocess.run([node, "--check", str(path)], cwd=ROOT, capture_output=True, text=True)
        assert completed.returncode == 0, f"{path}: {completed.stderr}"
