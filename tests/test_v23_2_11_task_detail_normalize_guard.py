from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "web_demo/index.html"
READ_MODEL = ROOT / "web_demo/core/task-read-model-v2082.js"
BOOTSTRAP = ROOT / "web_demo/bootstrap.js"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_bootstrap_installs_guard_before_router_start() -> None:
    index = _text(INDEX)
    bootstrap = _text(BOOTSTRAP)
    assert index.index("/web_demo/core/task-read-model-v2082.js?v=23.2.11") < index.index("/web_demo/bootstrap.js?v=23.2.11")
    assert bootstrap.index("installTaskDetailPayloadGuardV23211") < bootstrap.index("AppRouter.start()")


def test_guard_contract_is_narrow_and_removes_internal_marker() -> None:
    guard = _text(BOOTSTRAP)
    assert 'const VERSION = "23.2.11"' in guard
    assert '/^\\/api\\/view\\/tasks\\/[^/]+$/' in guard
    assert "response?.ok" in guard
    assert "payload.ready === false" in guard
    assert "taskExecutableFromEvidence: false" in guard
    assert "replaceMarkedProjection(report)" in guard
    assert "replaceMarkedProjection(report.taskDetailReport)" in guard
    assert "replaceMarkedProjection(report.relatedTask)" in guard
    assert "diagnosticStatus <= 0" in guard
    assert "errorStatus <= 0" in guard
    assert "delete error.frontendDiagnostic.httpStatus" in guard
    assert "delete error.httpStatus" in guard


def test_missing_projection_can_be_normalized_by_real_read_model() -> None:
    node = shutil.which("node")
    if not node:
        return
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");

        class StorageMock {{
          constructor() {{ Object.defineProperty(this, "_data", {{ value: new Map(), enumerable: false }}); }}
          getItem(key) {{ return this._data.has(String(key)) ? this._data.get(String(key)) : null; }}
          setItem(key, value) {{ this._data.set(String(key), String(value)); }}
          removeItem(key) {{ this._data.delete(String(key)); }}
        }}

        global.window = globalThis;
        global.location = {{ href: "http://localhost/" }};
        global.localStorage = new StorageMock();
        global.sessionStorage = new StorageMock();
        global.CustomEvent = class CustomEvent {{
          constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }}
        }};
        global.dispatchEvent = () => true;
        global.addEventListener = () => {{}};
        global.document = {{
          getElementById: () => null,
          querySelectorAll: () => [],
        }};
        global.AppRouter = {{
          registerLazy: () => {{}},
          start: () => {{}},
          routeFromHash: () => "task-report",
          navigate: () => {{}},
          schedule: () => {{}},
        }};
        global.AppShell = {{ escape: (value) => String(value ?? "") }};
        global.AppApi = {{
          getCurrentUserId: () => "U001",
          status: {{ source: "unknown", failures: [] }},
          accounts: async () => ({{ users: [], currentUser: null }}),
        }};
        window.AppApi = global.AppApi;

        const payload = {{
          ready: true,
          taskId: "LT-V23211-001",
          title: "旧任务详情",
          taskStatus: "待接收",
          item: {{
            taskId: "LT-V23211-001",
            relatedTask: {{
              taskId: "LT-V23211-001",
              title: "旧任务详情",
              status: "待接收",
              taskPlan: {{ title: "执行计划" }},
            }},
            taskDetailReport: {{
              title: "旧任务详情",
              taskPlan: {{ title: "执行计划" }},
            }},
          }},
        }};

        global.fetch = async () => new Response(JSON.stringify(payload), {{
          status: 200,
          headers: {{ "content-type": "application/json" }},
        }});

        vm.runInThisContext(fs.readFileSync({json.dumps(str(READ_MODEL))}, "utf8"), {{ filename: "task-read-model-v2082.js" }});
        vm.runInThisContext(fs.readFileSync({json.dumps(str(BOOTSTRAP))}, "utf8"), {{ filename: "bootstrap.js" }});

        (async () => {{
          const report = await window.AppApi.taskReport("LT-V23211-001", {{ forceNetwork: true, timeoutMs: 1000 }});
          const serialized = JSON.stringify(report);
          console.log(JSON.stringify({{
            projection: report.taskMetricEvidenceProjection,
            reportProjection: report.taskDetailReport.taskMetricEvidenceProjection,
            relatedProjection: report.relatedTask.taskMetricEvidenceProjection,
            status: report.taskEvidenceStatus,
            executable: report.taskEvidenceExecutable,
            blocked: report.evidenceExecutionBlocked,
            version: report.frontendTaskReadModelVersion,
            relatedVersion: report.relatedTask.frontendTaskReadModelVersion,
            markerLeaked: serialized.includes("__taskDetailProjectionMissingV23211"),
          }}));
        }})().catch((error) => {{
          console.error(error);
          process.exit(1);
        }});
        """
    )
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["projection"] == {}
    assert result["reportProjection"] == {}
    assert result["relatedProjection"] == {}
    assert result["status"] == "evidence_missing"
    assert result["executable"] is False
    assert result["blocked"] is True
    assert result["version"] == "23.2.11"
    assert result["relatedVersion"] == "23.2.11"
    assert result["markerLeaked"] is False


def test_guard_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        return
    completed = subprocess.run([node, "--check", str(BOOTSTRAP)], cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
