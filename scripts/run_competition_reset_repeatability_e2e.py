#!/usr/bin/env python3
"""Prove same-process Reset repeatability for the fixed three-report scenario.

This gate complements the existing Competition Three Report E2E. It launches one
precise packaged candidate, runs the same three reports to terminal task admission,
resets that *same process*, and runs the exact same reports again. The two clean runs
must have different Runtime Generation hashes but the same task count and the same
TaskSetSemanticHash.

Model calls use the existing deterministic contract fixture. This proves runtime
isolation/repeatability, not model quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
for path in (str(SCRIPT_DIR), str(ROOT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import run_competition_three_report_e2e as base  # noqa: E402
from deploy_competition_candidate import (  # noqa: E402
    make_application_read_only,
    make_candidate_writable_boundaries,
    safe_extract,
)
from src.services.repeatability_contract_v1_service import (  # noqa: E402
    compare_repeatability,
    task_set_semantic_hash,
)
from verify_competition_runtime_package import verify_archive  # noqa: E402

SCHEMA = "competition.reset_repeatability_e2e.v1"


class ResetRepeatabilityError(RuntimeError):
    pass


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _task_payloads(database_path: Path, data_version: str) -> List[Dict[str, Any]]:
    if not database_path.is_file():
        raise ResetRepeatabilityError(f"DATABASE_MISSING:{database_path}")
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_pool_entries' LIMIT 1"
        ).fetchone()
        if not exists:
            return []
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(task_pool_entries)").fetchall()
        }
        params: List[Any] = []
        where = ""
        if "data_version" in columns:
            where = " WHERE data_version=?"
            params.append(data_version)
        order = "updated_at" if "updated_at" in columns else "rowid"
        rows = conn.execute(
            f"SELECT * FROM task_pool_entries{where} ORDER BY {order} ASC",
            tuple(params),
        ).fetchall()
        result: List[Dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            payload: Any = None
            for key in ("payload", "task_payload", "task_json", "snapshot"):
                if key in row and row.get(key) not in (None, ""):
                    payload = row.get(key)
                    break
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = None
            # Prefer the persisted business payload. Row-level admission identities are
            # intentionally excluded from the semantic fingerprint when payload exists.
            if isinstance(payload, dict):
                result.append(payload)
            else:
                result.append(row)
        return result
    finally:
        conn.close()


def _runtime_meta(database_path: Path) -> Dict[str, str]:
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_meta' LIMIT 1"
        ).fetchone()
        if not exists:
            return {}
        rows = conn.execute("SELECT key,value FROM runtime_meta").fetchall()
        return {str(row["key"]): str(row["value"] or "") for row in rows}
    finally:
        conn.close()


def _empty_projection_assertions(value: Any) -> Dict[str, bool]:
    payload = _dict(value)
    summary = _dict(payload.get("summary"))
    return {
        "dataVersionEmpty": payload.get("dataVersion") in (None, ""),
        "activeDataVersionEmpty": payload.get("activeDataVersion") in (None, ""),
        "productTotalZero": int(summary.get("productTotal") or 0) == 0,
        "taskAdmittedZero": int(summary.get("taskAdmitted") or 0) == 0,
        "itemsEmpty": not bool(payload.get("items")),
        "historyReaderNotInvoked": payload.get("historicalReaderInvoked") is False,
        "crossGenerationFallbackForbidden": payload.get("crossGenerationLastGoodFallbackAllowed") is False,
    }


def _reset(app_url: str, database_path: Path, *, label: str) -> Dict[str, Any]:
    _, response = base.http_json(
        "POST",
        app_url + "/api/system/reset-runtime-data?confirm=true&include_audit_logs=true&scope=demo",
        headers=base.USER_HEADERS,
        timeout=120.0,
    )
    response = _dict(response)
    generation = _dict(response.get("runtimeGeneration"))
    if response.get("ok") is not True:
        raise ResetRepeatabilityError(f"RESET_NOT_OK:{label}:{response}")
    if generation.get("state") != "empty":
        raise ResetRepeatabilityError(f"RESET_NOT_EMPTY:{label}:{generation}")
    generation_hash = str(generation.get("generationHash") or "")
    if not generation_hash.startswith("sha256:"):
        raise ResetRepeatabilityError(f"RESET_GENERATION_HASH_MISSING:{label}:{generation}")

    _, live = base.http_json(
        "GET",
        app_url + "/api/view/pipeline-live?limit=100",
        headers=base.USER_HEADERS,
        timeout=20.0,
    )
    empty_assertions = _empty_projection_assertions(live)
    if not all(empty_assertions.values()):
        raise ResetRepeatabilityError(
            f"RESET_EMPTY_PROJECTION_FAILED:{label}:"
            + json.dumps(empty_assertions, ensure_ascii=False, sort_keys=True)
        )
    meta = _runtime_meta(database_path)
    if meta.get("runtime_generation_hash") != generation_hash:
        raise ResetRepeatabilityError(
            f"RESET_META_HASH_MISMATCH:{label}:{meta.get('runtime_generation_hash')}:{generation_hash}"
        )
    return {
        "label": label,
        "generation": generation,
        "generationHash": generation_hash,
        "generationSeq": generation.get("generationSeq"),
        "emptyProjection": live,
        "emptyAssertions": empty_assertions,
        "runtimeMeta": {
            key: meta.get(key)
            for key in (
                "runtime_generation_seq",
                "runtime_generation_hash",
                "runtime_generation_state",
                "runtime_generation_active_data_version",
                "latest_demo_reset_scope",
            )
        },
    }


def _import_three_reports(app_url: str, scenario: Mapping[str, Any]) -> List[Dict[str, Any]]:
    imports: List[Dict[str, Any]] = []
    for index, raw in enumerate(scenario.get("reports") or [], 1):
        if not isinstance(raw, dict):
            raise ResetRepeatabilityError(f"REPORT_OBJECT_REQUIRED:{index}")
        payload = {
            "datasetName": scenario.get("datasetName") or "products",
            "sourceSystem": scenario.get("sourceSystem") or "competition_fixture",
            "rows": raw.get("rows"),
            "reportProfile": {
                "scenarioId": scenario.get("scenarioId"),
                "reportId": raw.get("reportId"),
                "period": raw.get("period"),
                "fixture": True,
            },
        }
        _, imported = base.http_json(
            "POST",
            app_url + "/api/data/import/confirm",
            payload=payload,
            headers=base.USER_HEADERS,
            timeout=60.0,
        )
        imported = _dict(imported)
        if imported.get("ok") is not True:
            raise ResetRepeatabilityError(f"IMPORT_FAILED:{index}:{imported}")
        data_version = str(imported.get("dataVersion") or "")
        if not data_version:
            versions = imported.get("dataVersions")
            data_version = str(versions[-1] or "") if isinstance(versions, list) and versions else ""
        if not data_version:
            raise ResetRepeatabilityError(f"IMPORT_DATA_VERSION_MISSING:{index}")
        imports.append(
            {
                "reportId": raw.get("reportId"),
                "period": raw.get("period"),
                "dataVersion": data_version,
                "rowCount": imported.get("rowCount"),
            }
        )
        # Preserve the exact ordering/date identity behavior of the existing full E2E.
        time.sleep(1.05)
    return imports


def _drain_worker(app_url: str, max_ticks: int) -> List[Dict[str, Any]]:
    ticks: List[Dict[str, Any]] = []
    idle_streak = 0
    for index in range(1, max_ticks + 1):
        _, tick = base.http_json(
            "POST",
            app_url + "/api/system/run-agent-pipeline-tick?limit=8",
            headers=base.USER_HEADERS,
            timeout=240.0,
        )
        tick = _dict(tick)
        ticks.append(
            {
                "tick": index,
                "ran": tick.get("ran"),
                "selectedStage": tick.get("selectedStage"),
                "selectedDataVersion": tick.get("selectedDataVersion") or tick.get("dataVersion"),
                "status": tick.get("status"),
                "error": tick.get("error"),
            }
        )
        idle_streak = idle_streak + 1 if tick.get("ran") is not True else 0
        if idle_streak >= 4:
            break
        time.sleep(0.18)
    return ticks


def _run_once(
    *,
    run_index: int,
    app_url: str,
    scenario: Mapping[str, Any],
    database_path: Path,
    max_ticks: int,
) -> Dict[str, Any]:
    reset = _reset(app_url, database_path, label=f"run_{run_index}_pre_reset")
    imports = _import_three_reports(app_url, scenario)
    latest_version = imports[-1]["dataVersion"]
    ticks = _drain_worker(app_url, max_ticks)
    _, tasks_view = base.http_json(
        "GET", app_url + "/api/view/tasks", headers=base.USER_HEADERS, timeout=30.0
    )
    _, live = base.http_json(
        "GET", app_url + "/api/view/pipeline-live?limit=100", headers=base.USER_HEADERS, timeout=30.0
    )
    payloads = _task_payloads(database_path, latest_version)
    fingerprint = task_set_semantic_hash(
        data_version=latest_version,
        tasks=payloads,
    )
    task_view_count = base.recursive_list_count(tasks_view)
    if fingerprint.get("taskCount") != task_view_count:
        raise ResetRepeatabilityError(
            f"TASK_COUNT_AUTHORITY_MISMATCH:run={run_index}:pool={fingerprint.get('taskCount')}:view={task_view_count}"
        )
    if int(fingerprint.get("taskCount") or 0) < 1:
        raise ResetRepeatabilityError(f"NO_TASKS_CREATED:run={run_index}")
    generation = _dict(live).get("runtimeGeneration")
    generation = _dict(generation)
    if str(generation.get("generationHash") or "") != reset["generationHash"]:
        raise ResetRepeatabilityError(
            f"ACTIVE_GENERATION_CHANGED_WITHOUT_RESET:run={run_index}:"
            f"{reset['generationHash']}:{generation.get('generationHash')}"
        )
    return {
        "runIndex": run_index,
        "preReset": reset,
        "imports": imports,
        "latestDataVersion": latest_version,
        "ticks": ticks,
        "taskViewCount": task_view_count,
        "taskFingerprint": fingerprint,
        "pipelineLive": live,
        "runtimeGeneration": generation,
    }


def _environment(app_root: Path, state_root: Path, provider_url: str, app_port: int) -> Dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(app_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "AI_RELEASE_ROOT": str(app_root),
            "AI_RELEASE_MANIFEST": str(state_root / "no-legacy-release-manifest.json"),
            "AI_RELEASE_REQUIRED": "0",
            "ARTIFACT_ROOT": str(state_root / "data" / "artifacts"),
            "STATION_QUEUE_WORKER_ENABLED": "false",
            "AGENT_PIPELINE_ITEM_WORKER_ENABLED": "true",
            "STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK": "12",
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
    return environment


def run_repeatability(
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
) -> Dict[str, Any]:
    started = time.time()
    verification = verify_archive(archive, source_commit)
    scenario = base.read_scenario(scenario_path)
    scenario_hash = "sha256:" + hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    candidate_id = f"repeatability-{source_commit[:12]}-{scenario_hash.removeprefix('sha256:')[:12]}"
    candidate_root = candidate_base / candidate_id
    app_root = candidate_root / "app"
    state_root = candidate_root / "state"
    evidence_root = candidate_root / "evidence"
    database_path = state_root / "logs" / "product_workbench.sqlite3"
    app_log = evidence_root / "candidate-app.log"
    provider_log = evidence_root / "fixture-provider.log"
    provider_evidence = evidence_root / "fixture-provider-evidence.json"
    app_process: subprocess.Popen[Any] | None = None
    provider_process: subprocess.Popen[Any] | None = None
    report: Dict[str, Any] = {
        "schema": SCHEMA,
        "verified": False,
        "sourceCommit": source_commit,
        "scenarioHash": scenario_hash,
        "candidateId": candidate_id,
        "archiveVerification": verification,
        "mode": "same_process_two_clean_runs_with_deterministic_contract_fixture",
        "modelQualityProof": False,
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

        provider_port = base.choose_port(provider_port)
        app_port = base.choose_port(app_port)
        provider_url = f"http://127.0.0.1:{provider_port}"
        app_url = f"http://127.0.0.1:{app_port}"
        report["providerUrl"] = provider_url
        report["appUrl"] = app_url

        with provider_log.open("w", encoding="utf-8") as handle:
            provider_process = subprocess.Popen(
                [
                    str(tool_python),
                    str(SCRIPT_DIR / "competition_contract_fixture_provider.py"),
                    "--host", "127.0.0.1",
                    "--port", str(provider_port),
                    "--evidence", str(provider_evidence),
                ],
                cwd=state_root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        base.wait_http(provider_url, "/health", provider_process, startup_timeout)

        environment = _environment(app_root, state_root, provider_url, app_port)
        with app_log.open("w", encoding="utf-8") as handle:
            app_process = subprocess.Popen(
                [
                    str(runtime_python), "-m", "uvicorn", "src.api.main:app",
                    "--host", "127.0.0.1", "--port", str(app_port), "--workers", "1",
                ],
                cwd=state_root,
                env=environment,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        report["health"] = base.wait_http(app_url, "/api/health", app_process, startup_timeout)

        first = _run_once(
            run_index=1,
            app_url=app_url,
            scenario=scenario,
            database_path=database_path,
            max_ticks=max_ticks,
        )
        second = _run_once(
            run_index=2,
            app_url=app_url,
            scenario=scenario,
            database_path=database_path,
            max_ticks=max_ticks,
        )
        comparison = compare_repeatability(
            first["taskFingerprint"],
            second["taskFingerprint"],
        )
        generation_distinct = (
            first["preReset"]["generationHash"] != second["preReset"]["generationHash"]
        )
        report["runs"] = [first, second]
        report["comparison"] = {
            **comparison,
            "runtimeGenerationHashDifferent": generation_distinct,
            "firstGenerationHash": first["preReset"]["generationHash"],
            "secondGenerationHash": second["preReset"]["generationHash"],
        }
        report["assertions"] = {
            "twoCleanRunsCompleted": len(report["runs"]) == 2,
            "runtimeGenerationRotated": generation_distinct,
            "taskCountStable": comparison.get("taskCountMatch") is True,
            "taskSetSemanticHashStable": comparison.get("taskSetSemanticHashMatch") is True,
            "repeatabilityPassed": comparison.get("passed") is True,
            "bothRunsProducedTasks": all(
                int(run["taskFingerprint"].get("taskCount") or 0) >= 1
                for run in report["runs"]
            ),
            "bothResetsVerifiedEmpty": all(
                all(run["preReset"]["emptyAssertions"].values())
                for run in report["runs"]
            ),
        }
        failed = [key for key, value in report["assertions"].items() if value is not True]
        if failed:
            raise ResetRepeatabilityError("REPEATABILITY_ASSERTIONS_FAILED:" + ",".join(failed))

        report["verified"] = True
        report["durationSeconds"] = round(time.time() - started, 3)
        report["verificationHash"] = "sha256:" + hashlib.sha256(
            json.dumps(
                {key: value for key, value in report.items() if key not in {"verified", "verificationHash", "errors"}},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        _write_json(output_path, report)
        _write_json(evidence_root / "reset-repeatability-e2e.json", report)
        return report
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}:{exc}")
        report["durationSeconds"] = round(time.time() - started, 3)
        if app_log.is_file():
            report["appLogTail"] = app_log.read_text(encoding="utf-8", errors="replace")[-12000:]
        _write_json(output_path, report)
        raise
    finally:
        base.stop_process(app_process)
        base.stop_process(provider_process)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run same-process Reset repeatability E2E.")
    parser.add_argument("archive")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--scenario", default="fixtures/competition/three_report_scenario.json")
    parser.add_argument("--candidate-base", required=True)
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--tool-python", required=True)
    parser.add_argument("--app-port", type=int, default=39380)
    parser.add_argument("--provider-port", type=int, default=39480)
    parser.add_argument("--startup-timeout", type=float, default=150.0)
    parser.add_argument("--max-ticks", type=int, default=120)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_repeatability(
        archive=Path(args.archive).expanduser().resolve(),
        source_commit=args.source_commit.strip(),
        scenario_path=Path(args.scenario).expanduser().resolve(),
        candidate_base=Path(args.candidate_base).expanduser().resolve(),
        runtime_python=Path(args.runtime_python).expanduser().resolve(),
        tool_python=Path(args.tool_python).expanduser().resolve(),
        app_port=args.app_port,
        provider_port=args.provider_port,
        output_path=Path(args.output).expanduser().resolve(),
        startup_timeout=args.startup_timeout,
        max_ticks=max(1, args.max_ticks),
    )
    print(
        json.dumps(
            {
                "verified": report.get("verified"),
                "taskCount": _dict(report.get("runs", [{}])[0].get("taskFingerprint") if report.get("runs") else {}).get("taskCount"),
                "taskSetSemanticHash": _dict(report.get("runs", [{}])[0].get("taskFingerprint") if report.get("runs") else {}).get("taskSetSemanticHash"),
                "comparison": report.get("comparison"),
                "verificationHash": report.get("verificationHash"),
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
        print(f"reset repeatability e2e failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
