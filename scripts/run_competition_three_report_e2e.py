#!/usr/bin/env python3
"""Run the fixed three-report scenario against an isolated packaged candidate.

The test proves the public product chain, not model quality:

fixed reports -> import -> evidence -> Agent1 -> Agent2 -> Agent3 -> deterministic
mapping -> task admission -> frontend read models.

Agent calls are served by the deterministic contract fixture provider. The final
competition evidence must additionally contain a separate real Bailian/Qwen run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from deploy_competition_candidate import (  # noqa: E402
    make_application_read_only,
    make_candidate_writable_boundaries,
    safe_extract,
)
from verify_competition_runtime_package import verify_archive  # noqa: E402


SCHEMA = "competition.three_report_e2e.v1"
USER_HEADERS = {"X-User-Id": "U001"}


class ThreeReportE2EError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def choose_port(preferred: int) -> int:
    for port in range(preferred, preferred + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                handle.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise ThreeReportE2EError(f"NO_FREE_PORT:{preferred}-{preferred + 29}")


def http_json(
    method: str,
    url: str,
    *,
    payload: Any | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    body = None
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(
        url,
        data=body,
        method=method.upper(),
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type.lower():
                return response.status, json.loads(raw.decode("utf-8"))
            return response.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read(2_000_000)
        try:
            detail: Any = json.loads(raw.decode("utf-8"))
        except Exception:
            detail = raw.decode("utf-8", errors="replace")
        raise ThreeReportE2EError(
            f"HTTP_ERROR:{method}:{url}:{exc.code}:{detail}"
        ) from exc
    except Exception as exc:
        raise ThreeReportE2EError(
            f"HTTP_REQUEST_FAILED:{method}:{url}:{type(exc).__name__}:{exc}"
        ) from exc


def wait_http(base_url: str, path: str, process: subprocess.Popen[Any], timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ThreeReportE2EError(f"PROCESS_EXITED:{process.returncode}:{path}")
        try:
            status, payload = http_json("GET", base_url + path, timeout=3.0)
            if 200 <= status < 300:
                return payload
        except Exception as exc:
            last_error = f"{type(exc).__name__}:{exc}"
        time.sleep(0.7)
    raise ThreeReportE2EError(f"HTTP_TIMEOUT:{path}:{last_error}")


def stop_process(process: subprocess.Popen[Any] | None) -> dict[str, Any]:
    if process is None:
        return {"notStarted": True}
    if process.poll() is not None:
        return {"alreadyExited": True, "returnCode": process.returncode}
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"alreadyExited": True, "returnCode": process.poll()}
    try:
        process.wait(timeout=20)
        return {"terminated": True, "returnCode": process.returncode}
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        return {"killed": True, "returnCode": process.returncode}


def read_scenario(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ThreeReportE2EError("SCENARIO_OBJECT_REQUIRED")
    reports = value.get("reports")
    if not isinstance(reports, list) or len(reports) != 3:
        raise ThreeReportE2EError("EXACTLY_THREE_REPORTS_REQUIRED")
    return value


def recursive_list_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return 0
    for key in ("tasks", "items", "rows", "records", "data"):
        nested = value.get(key)
        if isinstance(nested, list):
            return len(nested)
        if isinstance(nested, dict):
            count = recursive_list_count(nested)
            if count:
                return count
    return 0


def query_runtime_database(db_path: Path, latest_version: str) -> dict[str, Any]:
    if not db_path.is_file():
        raise ThreeReportE2EError(f"RUNTIME_DATABASE_MISSING:{db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        stages: list[dict[str, Any]] = []
        family_counts: list[dict[str, Any]] = []
        pipeline_rows: list[dict[str, Any]] = []
        if "pipeline_items" in tables:
            stages = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT current_stage,status,COUNT(*) AS count
                    FROM pipeline_items
                    WHERE data_version=?
                    GROUP BY current_stage,status
                    ORDER BY current_stage,status
                    """,
                    (latest_version,),
                ).fetchall()
            ]
            family_counts = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT COALESCE(action_family,'') AS actionFamily,COUNT(*) AS count
                    FROM pipeline_items
                    WHERE data_version=?
                    GROUP BY COALESCE(action_family,'')
                    ORDER BY actionFamily
                    """,
                    (latest_version,),
                ).fetchall()
            ]
            pipeline_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT item_id,product_id,store_id,current_stage,status,
                           action_family,input_ref,output_ref,artifact_refs_json
                    FROM pipeline_items
                    WHERE data_version=?
                    ORDER BY product_id,item_id
                    """,
                    (latest_version,),
                ).fetchall()
            ]
        llm_audit: list[dict[str, Any]] = []
        if "llm_inference_audit_v211" in tables:
            llm_audit = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT stage,provider,model,status,
                           SUM(provider_call_executed) AS providerCalls,
                           SUM(local_replay) AS localReplays,
                           COUNT(*) AS auditRows
                    FROM llm_inference_audit_v211
                    GROUP BY stage,provider,model,status
                    ORDER BY stage,status
                    """
                ).fetchall()
            ]
        task_tables = sorted(
            table
            for table in tables
            if "task" in table.lower() and not table.startswith("sqlite_")
        )
        task_table_counts: dict[str, int] = {}
        for table in task_tables:
            try:
                count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                task_table_counts[table] = int(count or 0)
            except sqlite3.DatabaseError:
                continue
        return {
            "databasePath": str(db_path),
            "tables": sorted(tables),
            "pipelineStages": stages,
            "pipelineActionFamilies": family_counts,
            "pipelineItems": pipeline_rows,
            "llmAudit": llm_audit,
            "taskTableCounts": task_table_counts,
        }
    finally:
        connection.close()


def stage_count(database: Mapping[str, Any], stage: str) -> int:
    return sum(
        int(item.get("count") or 0)
        for item in database.get("pipelineStages") or []
        if isinstance(item, dict) and item.get("current_stage") == stage
    )


def provider_stats(url: str) -> dict[str, Any]:
    _, value = http_json("GET", url + "/stats", timeout=5.0)
    if not isinstance(value, dict):
        raise ThreeReportE2EError("PROVIDER_STATS_OBJECT_REQUIRED")
    return value


def run_e2e(
    *,
    archive: Path,
    source_commit: str,
    scenario_path: Path,
    candidate_base: Path,
    runtime_python: Path,
    tool_python: Path,
    app_port: int,
    provider_port: int,
    output_path: Path,
    startup_timeout: float,
    max_ticks: int,
) -> dict[str, Any]:
    started_at = time.time()
    verification = verify_archive(archive, source_commit)
    scenario = read_scenario(scenario_path)
    scenario_hash = "sha256:" + hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    candidate_id = f"{source_commit[:12]}-{scenario_hash.removeprefix('sha256:')[:12]}"
    candidate_root = candidate_base / candidate_id
    app_root = candidate_root / "app"
    state_root = candidate_root / "state"
    evidence_root = candidate_root / "evidence"
    fixture_log = evidence_root / "fixture-provider.log"
    app_log = evidence_root / "candidate-app.log"
    fixture_evidence = evidence_root / "fixture-provider-evidence.json"
    database_path = state_root / "logs" / "product_workbench.sqlite3"
    app_process: subprocess.Popen[Any] | None = None
    provider_process: subprocess.Popen[Any] | None = None
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "verified": False,
        "mode": "fixed_reports_with_deterministic_contract_fixture",
        "modelQualityProof": False,
        "realBailianRunStillRequired": True,
        "sourceCommit": source_commit,
        "scenarioId": scenario.get("scenarioId"),
        "scenarioHash": scenario_hash,
        "candidateId": candidate_id,
        "candidateRoot": str(candidate_root),
        "archiveVerification": verification,
        "errors": [],
    }
    try:
        shutil.rmtree(candidate_root, ignore_errors=True)
        app_root.mkdir(parents=True, exist_ok=True)
        safe_extract(archive, app_root)
        state_root.mkdir(parents=True, exist_ok=True)
        evidence_root.mkdir(parents=True, exist_ok=True)
        make_candidate_writable_boundaries(app_root, state_root)
        make_application_read_only(app_root)

        provider_port = choose_port(provider_port)
        app_port = choose_port(app_port)
        provider_url = f"http://127.0.0.1:{provider_port}"
        app_url = f"http://127.0.0.1:{app_port}"
        report["providerUrl"] = provider_url
        report["appUrl"] = app_url

        with fixture_log.open("w", encoding="utf-8") as fixture_handle:
            provider_process = subprocess.Popen(
                [
                    str(tool_python),
                    str(SCRIPT_DIR / "competition_contract_fixture_provider.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(provider_port),
                    "--evidence",
                    str(fixture_evidence),
                ],
                cwd=state_root,
                stdout=fixture_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        wait_http(provider_url, "/health", provider_process, startup_timeout)

        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(app_root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "AI_RELEASE_ROOT": str(app_root),
                "AI_RELEASE_MANIFEST": str(state_root / "no-legacy-release-manifest.json"),
                "AI_RELEASE_REQUIRED": "0",
                "ARTIFACT_ROOT": str(state_root / "data" / "artifacts"),
                "LLM_ENABLED": "true",
                "LLM_PROVIDER": "openai_compatible",
                "LLM_API_KEY": "competition-contract-fixture-key",
                "LLM_BASE_URL": provider_url + "/v1",
                "LLM_MODEL": "competition-contract-fixture-v1",
                "LLM_ENABLE_THINKING": "false",
                "PRODUCT_JUDGMENT_AGENT_PROVIDER": "openai_compatible",
                "PRODUCT_JUDGMENT_AGENT_API_KEY": "competition-contract-fixture-key",
                "PRODUCT_JUDGMENT_AGENT_BASE_URL": provider_url + "/v1",
                "PRODUCT_JUDGMENT_AGENT_MODEL": "competition-contract-fixture-v1",
                "PRODUCT_JUDGMENT_AGENT_ENABLE_THINKING": "false",
                "ACTION_PLAN_AGENT_PROVIDER": "openai_compatible",
                "ACTION_PLAN_AGENT_API_KEY": "competition-contract-fixture-key",
                "ACTION_PLAN_AGENT_BASE_URL": provider_url + "/v1",
                "ACTION_PLAN_AGENT_MODEL": "competition-contract-fixture-v1",
                "ACTION_PLAN_AGENT_ENABLE_THINKING": "false",
                "TASK_MAPPING_AGENT_PROVIDER": "openai_compatible",
                "TASK_MAPPING_AGENT_API_KEY": "competition-contract-fixture-key",
                "TASK_MAPPING_AGENT_BASE_URL": provider_url + "/v1",
                "TASK_MAPPING_AGENT_MODEL": "competition-contract-fixture-v1",
                "TASK_MAPPING_AGENT_ENABLE_THINKING": "false",
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(app_port),
                "APP_WORKERS": "1",
                "APP_RELOAD": "false",
            }
        )
        for name in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
            "QWEN_API_KEY",
            "DEEPSEEK_API_KEY",
            "DATABASE_URL",
            "SQLALCHEMY_DATABASE_URI",
            "REDIS_URL",
        ):
            environment.pop(name, None)

        with app_log.open("w", encoding="utf-8") as app_handle:
            app_process = subprocess.Popen(
                [
                    str(runtime_python),
                    "-m",
                    "uvicorn",
                    "src.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(app_port),
                    "--workers",
                    "1",
                ],
                cwd=state_root,
                env=environment,
                stdout=app_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        health = wait_http(app_url, "/api/health", app_process, startup_timeout)
        report["health"] = health
        http_json(
            "POST",
            app_url + "/api/system/reset-runtime-data?confirm=true&include_audit_logs=true&scope=demo",
            headers=USER_HEADERS,
        )

        imports: list[dict[str, Any]] = []
        latest_version = ""
        for index, raw_report in enumerate(scenario["reports"], 1):
            if not isinstance(raw_report, dict):
                raise ThreeReportE2EError(f"REPORT_OBJECT_REQUIRED:{index}")
            payload = {
                "datasetName": scenario.get("datasetName") or "products",
                "sourceSystem": scenario.get("sourceSystem") or "competition_fixture",
                "rows": raw_report.get("rows"),
                "reportProfile": {
                    "scenarioId": scenario.get("scenarioId"),
                    "reportId": raw_report.get("reportId"),
                    "period": raw_report.get("period"),
                    "fixture": True,
                },
            }
            _, imported = http_json(
                "POST",
                app_url + "/api/data/import/confirm",
                payload=payload,
                headers=USER_HEADERS,
                timeout=60.0,
            )
            if not isinstance(imported, dict) or imported.get("ok") is not True:
                raise ThreeReportE2EError(f"IMPORT_FAILED:{index}:{imported}")
            latest_version = str(imported.get("dataVersion") or "")
            if not latest_version:
                versions = imported.get("dataVersions")
                latest_version = str(versions[-1]) if isinstance(versions, list) and versions else ""
            if not latest_version:
                raise ThreeReportE2EError(f"IMPORT_DATA_VERSION_MISSING:{index}")
            imports.append(
                {
                    "reportId": raw_report.get("reportId"),
                    "period": raw_report.get("period"),
                    "dataVersion": latest_version,
                    "rowCount": imported.get("rowCount"),
                    "taskGenerationStatus": imported.get("taskGenerationStatus"),
                }
            )
            time.sleep(1.05)
        report["imports"] = imports
        report["latestDataVersion"] = latest_version

        ticks: list[dict[str, Any]] = []
        idle_streak = 0
        for index in range(1, max_ticks + 1):
            _, tick = http_json(
                "POST",
                app_url + "/api/system/run-agent-pipeline-tick?limit=8",
                headers=USER_HEADERS,
                timeout=240.0,
            )
            tick = tick if isinstance(tick, dict) else {"value": tick}
            ticks.append(
                {
                    "tick": index,
                    "ran": tick.get("ran"),
                    "selectedStage": tick.get("selectedStage"),
                    "selectedDataVersion": tick.get("selectedDataVersion")
                    or tick.get("dataVersion"),
                    "status": tick.get("status"),
                    "error": tick.get("error"),
                }
            )
            idle_streak = idle_streak + 1 if tick.get("ran") is not True else 0
            if idle_streak >= 4:
                break
            time.sleep(0.18)
        report["ticks"] = ticks

        _, pipeline_status = http_json(
            "GET",
            app_url
            + "/api/system/agent-pipeline-status?"
            + urllib.parse.urlencode({"dataVersion": latest_version}),
            headers=USER_HEADERS,
        )
        _, pipeline_live = http_json(
            "GET",
            app_url
            + "/api/view/pipeline-live?"
            + urllib.parse.urlencode({"dataVersion": latest_version}),
            headers=USER_HEADERS,
        )
        _, tasks = http_json("GET", app_url + "/api/view/tasks", headers=USER_HEADERS)
        _, products = http_json("GET", app_url + "/api/view/products", headers=USER_HEADERS)
        _, acceptance = http_json(
            "GET", app_url + "/api/view/task-pool-acceptance", headers=USER_HEADERS
        )
        report["views"] = {
            "pipelineStatus": pipeline_status,
            "pipelineLive": pipeline_live,
            "taskCount": recursive_list_count(tasks),
            "productCount": recursive_list_count(products),
            "taskPoolAcceptance": acceptance,
        }

        before_replay = provider_stats(provider_url)
        replay_ticks: list[dict[str, Any]] = []
        for index in range(1, 6):
            _, tick = http_json(
                "POST",
                app_url + "/api/system/run-agent-pipeline-tick?limit=8",
                headers=USER_HEADERS,
                timeout=120.0,
            )
            replay_ticks.append(
                {
                    "tick": index,
                    "ran": tick.get("ran") if isinstance(tick, dict) else None,
                    "selectedStage": tick.get("selectedStage") if isinstance(tick, dict) else None,
                }
            )
        after_replay = provider_stats(provider_url)
        report["replayCheck"] = {
            "before": before_replay,
            "after": after_replay,
            "additionalProviderCalls": int(after_replay.get("callCount") or 0)
            - int(before_replay.get("callCount") or 0),
            "ticks": replay_ticks,
        }

        stop_process(app_process)
        app_process = None
        stop_process(provider_process)
        provider_process = None

        database = query_runtime_database(database_path, latest_version)
        report["databaseEvidence"] = database
        observed_count = stage_count(database, "observed_soft_gate")
        admitted_count = stage_count(database, "task_admitted")
        mapped_count = stage_count(database, "task_mapped")
        agent3_ready_count = stage_count(database, "agent3_sop_ready")
        action_families = {
            str(item.get("actionFamily") or "")
            for item in database.get("pipelineActionFamilies") or []
            if isinstance(item, dict) and int(item.get("count") or 0) > 0
        }
        expected = scenario.get("expected") if isinstance(scenario.get("expected"), dict) else {}
        required_families = {str(item) for item in expected.get("requiredActionFamilies") or []}
        provider_call_count = int(before_replay.get("callCount") or 0)
        assertions = {
            "threeReportsImported": len(imports) == 3,
            "latestVersionPresent": bool(latest_version),
            "providerCalled": provider_call_count >= 3,
            "agent1ProviderCalled": int(_dict(before_replay.get("stageCounts")).get("product_judgment_agent") or 0) >= 1,
            "agent2ProviderCalled": int(_dict(before_replay.get("stageCounts")).get("action_plan_judgment_agent") or 0) >= 1,
            "agent3ProviderCalled": int(_dict(before_replay.get("stageCounts")).get("agent3_sop_agent") or 0) >= 1,
            "observationTerminalPresent": observed_count >= int(expected.get("minimumObservedProducts") or 1),
            "taskMappedOrAdmitted": (mapped_count + admitted_count) >= int(expected.get("minimumAdmittedTasks") or 1),
            "agent3ReadyOrTaskAdmitted": (agent3_ready_count + admitted_count) >= 1,
            "requiredActionFamiliesPresent": required_families.issubset(action_families),
            "taskViewReturnsItems": int(report["views"]["taskCount"] or 0) >= 1,
            "productViewReturnsItems": int(report["views"]["productCount"] or 0) >= 3,
            "terminalReticksDoNotCallProvider": report["replayCheck"]["additionalProviderCalls"] == 0,
            "fixtureClearlyLabeled": before_replay.get("mode")
            == "deterministic_contract_fixture_not_model_quality_proof",
        }
        report["assertions"] = assertions
        failed = [key for key, passed in assertions.items() if passed is not True]
        if failed:
            raise ThreeReportE2EError("E2E_ASSERTIONS_FAILED:" + ",".join(failed))

        report["completedAtEpoch"] = time.time()
        report["durationSeconds"] = round(report["completedAtEpoch"] - started_at, 3)
        report["verificationHash"] = "sha256:" + hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in report.items()
                    if key not in {"verified", "verificationHash", "errors"}
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        report["verified"] = True
        write_json(evidence_root / "three-report-e2e-attestation.json", report)
        write_json(output_path, report)
        return report
    except Exception as exc:
        report.setdefault("errors", []).append(f"{type(exc).__name__}:{exc}")
        report["appProcessStop"] = stop_process(app_process)
        report["providerProcessStop"] = stop_process(provider_process)
        report["completedAtEpoch"] = time.time()
        report["durationSeconds"] = round(report["completedAtEpoch"] - started_at, 3)
        if app_log.is_file():
            report["appLogTail"] = app_log.read_text(
                encoding="utf-8", errors="replace"
            )[-12_000:]
        if fixture_log.is_file():
            report["fixtureLogTail"] = fixture_log.read_text(
                encoding="utf-8", errors="replace"
            )[-8_000:]
        try:
            if database_path.is_file() and report.get("latestDataVersion"):
                report["databaseEvidence"] = query_runtime_database(
                    database_path, str(report["latestDataVersion"])
                )
        except Exception as database_exc:
            report["databaseEvidenceError"] = (
                f"{type(database_exc).__name__}:{database_exc}"
            )
        write_json(evidence_root / "three-report-e2e-attestation.json", report)
        write_json(output_path, report)
        raise


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fixed three-report competition E2E.")
    parser.add_argument("archive")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--scenario",
        default="fixtures/competition/three_report_scenario.json",
    )
    parser.add_argument(
        "--candidate-base",
        default="/opt/actions-runner-public/competition-e2e/ai-ecommerce-assistant-public",
    )
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--tool-python", default=sys.executable)
    parser.add_argument("--app-port", type=int, default=39280)
    parser.add_argument("--provider-port", type=int, default=39180)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--max-ticks", type=int, default=100)
    parser.add_argument(
        "--output",
        default="dist/competition-three-report-e2e/three-report-e2e-attestation.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_e2e(
        archive=Path(args.archive).expanduser().resolve(),
        source_commit=args.source_commit.strip(),
        scenario_path=Path(args.scenario).expanduser().resolve(),
        candidate_base=Path(args.candidate_base).expanduser(),
        runtime_python=Path(args.runtime_python).expanduser().resolve(),
        tool_python=Path(args.tool_python).expanduser().resolve(),
        app_port=args.app_port,
        provider_port=args.provider_port,
        output_path=Path(args.output).expanduser().resolve(),
        startup_timeout=args.startup_timeout,
        max_ticks=args.max_ticks,
    )
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "sourceCommit": report["sourceCommit"],
                "scenarioId": report["scenarioId"],
                "latestDataVersion": report["latestDataVersion"],
                "providerCalls": report["replayCheck"]["before"]["callCount"],
                "additionalProviderCallsAfterTerminal": report["replayCheck"]["additionalProviderCalls"],
                "taskCount": report["views"]["taskCount"],
                "verificationHash": report["verificationHash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition three-report E2E failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
