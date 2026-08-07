#!/usr/bin/env python3
"""Run the judge-facing XLSX reports through the isolated runtime with real Bailian/Qwen.

This is a model-execution evidence job, not a deterministic CI substitute. It proves:
judge XLSX -> official upload preview/confirm -> evidence -> Agent1 -> Agent2 ->
Agent3 -> task admission, while the candidate remains disjoint from production state.

Only aggregate provider/audit metadata and hashes are written to the attestation.
No API key, prompt, raw model response, uploaded row payload, or production database is
persisted in the published evidence.
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
import urllib.error
import urllib.parse
import urllib.request
import uuid
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
from run_competition_three_report_e2e import (  # noqa: E402
    USER_HEADERS,
    choose_port,
    http_json,
    recursive_list_count,
    stop_process,
    wait_http,
)
from verify_competition_runtime_package import verify_archive  # noqa: E402

SCHEMA = "competition.qwen_live_evidence.v1"
DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_REPORTS = (
    "AI经营参谋_脱敏样例_第1期.xlsx",
    "AI经营参谋_脱敏样例_第2期.xlsx",
    "AI经营参谋_脱敏样例_第3期.xlsx",
)
EXPECTED_AGENT_STAGES = (
    "product_judgment_agent",
    "action_plan_judgment_agent",
    "agent3_sop_agent",
)
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class QwenLiveEvidenceError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def multipart_json(
    url: str,
    *,
    file_path: Path,
    fields: Mapping[str, str],
    headers: Mapping[str, str] | None = None,
    timeout: float = 120.0,
) -> tuple[int, Any]:
    boundary = "----competition-qwen-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
                f"Content-Type: {XLSX_CONTENT_TYPE}\r\n\r\n"
            ).encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        url,
        data=b"".join(chunks),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **dict(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            return response.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read(2_000_000)
        try:
            detail: Any = json.loads(raw.decode("utf-8"))
        except Exception:
            detail = raw.decode("utf-8", errors="replace")
        raise QwenLiveEvidenceError(
            f"MULTIPART_HTTP_ERROR:{file_path.name}:{exc.code}:{detail}"
        ) from exc
    except Exception as exc:
        raise QwenLiveEvidenceError(
            f"MULTIPART_REQUEST_FAILED:{file_path.name}:{type(exc).__name__}:{exc}"
        ) from exc


def _compact_upload_meta(value: Any) -> dict[str, Any]:
    meta = value if isinstance(value, dict) else {}
    return {
        "format": meta.get("format"),
        "sheetCount": meta.get("sheetCount"),
        "totalRows": meta.get("totalRows"),
        "sheetNames": sorted(str(item) for item in (meta.get("sheetNames") or [])),
    }


def _data_version(value: Any) -> str:
    item = value if isinstance(value, dict) else {}
    if item.get("dataVersion"):
        return str(item["dataVersion"])
    versions = item.get("dataVersions")
    if isinstance(versions, list) and versions:
        return str(versions[-1])
    return ""


def read_external_interface_contract(app_root: Path) -> dict[str, Any]:
    path = app_root / "config" / "external_interface_registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    interfaces = registry.get("interfaces") if isinstance(registry, dict) else {}
    model = (
        interfaces.get("model.inference.aliyun_bailian")
        if isinstance(interfaces, dict)
        else None
    )
    if not isinstance(model, dict):
        raise QwenLiveEvidenceError("BAILIAN_INTERFACE_REGISTRY_ENTRY_MISSING")
    allowed_hosts = [str(item) for item in (model.get("allowedHosts") or [])]
    contract = {
        "registryVersion": registry.get("version"),
        "registryHash": registry.get("registryHash"),
        "interfaceId": "model.inference.aliyun_bailian",
        "provider": model.get("provider"),
        "bindingPresent": model.get("bindingPresent"),
        "executionEnabled": model.get("executionEnabled"),
        "competitionStatus": model.get("competitionStatus"),
        "allowedHosts": allowed_hosts,
        "contractHash": model.get("contractHash"),
        "credentialSourceNames": sorted(str(item) for item in (model.get("credentialSource") or [])),
    }
    if contract["provider"] != "aliyun_bailian":
        raise QwenLiveEvidenceError("BAILIAN_INTERFACE_PROVIDER_MISMATCH")
    if contract["bindingPresent"] is not True or contract["executionEnabled"] is not True:
        raise QwenLiveEvidenceError("BAILIAN_INTERFACE_NOT_EXECUTABLE")
    if "dashscope.aliyuncs.com" not in allowed_hosts:
        raise QwenLiveEvidenceError("DASHSCOPE_HOST_NOT_REGISTERED")
    return contract


def query_safe_database_evidence(db_path: Path, latest_version: str) -> dict[str, Any]:
    if not db_path.is_file():
        raise QwenLiveEvidenceError(f"RUNTIME_DATABASE_MISSING:{db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        pipeline: list[dict[str, Any]] = []
        if "pipeline_items" in tables:
            pipeline = [
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
        audits: list[dict[str, Any]] = []
        request_ids: list[str] = []
        if "llm_inference_audit_v211" in tables:
            audits = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT stage,provider,model,status,
                           SUM(provider_call_executed) AS providerCalls,
                           SUM(local_replay) AS localReplays,
                           COUNT(*) AS auditRows,
                           SUM(input_tokens) AS inputTokens,
                           SUM(output_tokens) AS outputTokens,
                           SUM(reasoning_tokens) AS reasoningTokens,
                           SUM(latency_ms) AS latencyMs,
                           COUNT(DISTINCT CASE
                               WHEN provider_request_id IS NOT NULL AND provider_request_id != ''
                               THEN provider_request_id END) AS providerRequestIds
                    FROM llm_inference_audit_v211
                    GROUP BY stage,provider,model,status
                    ORDER BY stage,provider,model,status
                    """
                ).fetchall()
            ]
            request_ids = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT provider_request_id
                    FROM llm_inference_audit_v211
                    WHERE provider_request_id IS NOT NULL AND provider_request_id != ''
                    ORDER BY provider_request_id
                    """
                ).fetchall()
            ]
        return {
            "pipelineStageCounts": pipeline,
            "llmAudit": audits,
            "providerRequestIdCount": len(set(request_ids)),
            "providerRequestIdSetHash": sha256_bytes(
                canonical_bytes(sorted(set(request_ids)))
            ),
        }
    finally:
        connection.close()


def _stage_count(evidence: Mapping[str, Any], stage: str) -> int:
    return sum(
        int(item.get("count") or 0)
        for item in (evidence.get("pipelineStageCounts") or [])
        if isinstance(item, dict) and item.get("current_stage") == stage
    )


def _provider_rows(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (evidence.get("llmAudit") or [])
        if isinstance(item, dict) and int(item.get("providerCalls") or 0) > 0
    ]


def run_live(
    *,
    archive: Path,
    source_commit: str,
    report_dir: Path,
    report_names: Sequence[str],
    candidate_base: Path,
    runtime_python: Path,
    app_port: int,
    output_path: Path,
    startup_timeout: float,
    max_ticks: int,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    started_at = time.time()
    if not api_key.strip():
        raise QwenLiveEvidenceError("COMPETITION_BAILIAN_API_KEY_REQUIRED")
    verification = verify_archive(archive, source_commit)
    reports = [report_dir / name for name in report_names]
    missing = [path.name for path in reports if not path.is_file()]
    if missing:
        raise QwenLiveEvidenceError("REPORT_FILES_MISSING:" + ",".join(missing))
    sample_records = [
        {"index": index, "filename": path.name, "sha256": sha256_file(path), "size": path.stat().st_size}
        for index, path in enumerate(reports, 1)
    ]
    sample_set_hash = sha256_bytes(canonical_bytes(sample_records))
    candidate_id = f"qwen-{source_commit[:12]}-{sample_set_hash[-12:]}"
    candidate_root = candidate_base / candidate_id
    app_root = candidate_root / "app"
    state_root = candidate_root / "state"
    app_log = state_root / "evidence" / "candidate-app.log"
    database_path = state_root / "logs" / "product_workbench.sqlite3"
    app_process: subprocess.Popen[Any] | None = None
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "verified": False,
        "mode": "judge_xlsx_real_aliyun_bailian_qwen",
        "modelQualityProof": True,
        "realBailianRunStillRequired": False,
        "sourceCommit": source_commit,
        "candidateId": candidate_id,
        "sampleReports": sample_records,
        "sampleSetHash": sample_set_hash,
        "archiveVerification": verification,
        "credential": {"configured": True, "published": False, "sourceValuePersisted": False},
        "productionBoundary": {
            "productionEnvironmentLoaded": False,
            "productionDatabaseReused": False,
            "productionServiceRestarted": False,
            "productionSymlinkSwitched": False,
        },
        "errors": [],
    }
    try:
        shutil.rmtree(candidate_root, ignore_errors=True)
        app_root.mkdir(parents=True, exist_ok=True)
        safe_extract(archive, app_root)
        state_root.mkdir(parents=True, exist_ok=True)
        app_log.parent.mkdir(parents=True, exist_ok=True)
        make_candidate_writable_boundaries(app_root, state_root)
        make_application_read_only(app_root)
        report["externalInterfaceContract"] = read_external_interface_contract(app_root)

        app_port = choose_port(app_port)
        app_url = f"http://127.0.0.1:{app_port}"
        report["loopbackPort"] = app_port

        environment = os.environ.copy()
        for name in (
            "BAILIAN_API_KEY",
            "DASHSCOPE_API_KEY",
            "QWEN_API_KEY",
            "DEEPSEEK_API_KEY",
            "LLM_API_KEY",
            "DATABASE_URL",
            "SQLALCHEMY_DATABASE_URI",
            "REDIS_URL",
            "PRODUCT_JUDGMENT_AGENT_API_KEY",
            "ACTION_PLAN_AGENT_API_KEY",
            "TASK_MAPPING_AGENT_API_KEY",
            "PRODUCT_JUDGMENT_AGENT_BASE_URL",
            "ACTION_PLAN_AGENT_BASE_URL",
            "TASK_MAPPING_AGENT_BASE_URL",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "PYTHONPATH": str(app_root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "AI_RELEASE_ROOT": str(app_root),
                "AI_RELEASE_MANIFEST": str(state_root / "no-legacy-release-manifest.json"),
                "AI_RELEASE_REQUIRED": "0",
                "ARTIFACT_ROOT": str(state_root / "data" / "artifacts"),
                "LLM_ENABLED": "true",
                "LLM_PROVIDER": "aliyun_bailian",
                "DASHSCOPE_API_KEY": api_key,
                "QWEN_MODEL": model,
                "LLM_MODEL": model,
                "LLM_ENABLE_THINKING": "false",
                "PRODUCT_JUDGMENT_AGENT_PROVIDER": "aliyun_bailian",
                "PRODUCT_JUDGMENT_AGENT_MODEL": model,
                "PRODUCT_JUDGMENT_AGENT_ENABLE_THINKING": "false",
                "ACTION_PLAN_AGENT_PROVIDER": "aliyun_bailian",
                "ACTION_PLAN_AGENT_MODEL": model,
                "ACTION_PLAN_AGENT_ENABLE_THINKING": "false",
                "TASK_MAPPING_AGENT_PROVIDER": "aliyun_bailian",
                "TASK_MAPPING_AGENT_MODEL": model,
                "TASK_MAPPING_AGENT_ENABLE_THINKING": "false",
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(app_port),
                "APP_WORKERS": "1",
                "APP_RELOAD": "false",
            }
        )

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
        report["healthContract"] = {
            "ok": isinstance(health, dict) and health.get("ok") is not False,
            "responseHash": sha256_bytes(canonical_bytes(health)),
        }
        http_json(
            "POST",
            app_url + "/api/system/reset-runtime-data?confirm=true&include_audit_logs=true&scope=demo",
            headers=USER_HEADERS,
            timeout=60.0,
        )

        imports: list[dict[str, Any]] = []
        latest_version = ""
        for index, path in enumerate(reports, 1):
            fields = {"dataset_name": "auto", "source_system": "competition_evaluator_xlsx"}
            _, preview = multipart_json(
                app_url + "/api/data/upload/preview",
                file_path=path,
                fields=fields,
                headers=USER_HEADERS,
                timeout=120.0,
            )
            _, confirmed = multipart_json(
                app_url + "/api/data/upload/confirm",
                file_path=path,
                fields={**fields, "auto_create_tasks": "false"},
                headers=USER_HEADERS,
                timeout=180.0,
            )
            if not isinstance(confirmed, dict) or confirmed.get("ok") is not True:
                raise QwenLiveEvidenceError(f"UPLOAD_CONFIRM_FAILED:{index}")
            latest_version = _data_version(confirmed)
            if not latest_version:
                raise QwenLiveEvidenceError(f"UPLOAD_DATA_VERSION_MISSING:{index}")
            imports.append(
                {
                    "index": index,
                    "filename": path.name,
                    "previewHash": sha256_bytes(canonical_bytes(preview)),
                    "previewUploadMeta": _compact_upload_meta(
                        preview.get("uploadMeta") if isinstance(preview, dict) else None
                    ),
                    "confirmHash": sha256_bytes(canonical_bytes(confirmed)),
                    "confirmUploadMeta": _compact_upload_meta(confirmed.get("uploadMeta")),
                    "dataVersion": latest_version,
                    "rowCount": confirmed.get("rowCount"),
                    "taskGenerationStatus": confirmed.get("taskGenerationStatus"),
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
                app_url + "/api/system/run-agent-pipeline-tick?limit=4",
                headers=USER_HEADERS,
                timeout=300.0,
            )
            item = tick if isinstance(tick, dict) else {}
            ticks.append(
                {
                    "tick": index,
                    "ran": item.get("ran"),
                    "selectedStage": item.get("selectedStage"),
                    "status": item.get("status"),
                    "errorPresent": bool(item.get("error")),
                }
            )
            idle_streak = idle_streak + 1 if item.get("ran") is not True else 0
            if idle_streak >= 4:
                break
            time.sleep(0.25)
        report["tickSummary"] = {
            "count": len(ticks),
            "ranCount": sum(1 for item in ticks if item.get("ran") is True),
            "idleTerminalReached": idle_streak >= 4,
            "stageSequence": [str(item.get("selectedStage") or "") for item in ticks if item.get("ran") is True],
            "errorTicks": sum(1 for item in ticks if item.get("errorPresent")),
        }

        _, tasks = http_json("GET", app_url + "/api/view/tasks", headers=USER_HEADERS, timeout=60.0)
        _, products = http_json("GET", app_url + "/api/view/products", headers=USER_HEADERS, timeout=60.0)
        _, pipeline_status = http_json(
            "GET",
            app_url + "/api/system/agent-pipeline-status?" + urllib.parse.urlencode({"dataVersion": latest_version}),
            headers=USER_HEADERS,
            timeout=60.0,
        )
        report["viewEvidence"] = {
            "taskCount": recursive_list_count(tasks),
            "productCount": recursive_list_count(products),
            "pipelineStatusHash": sha256_bytes(canonical_bytes(pipeline_status)),
        }

        stop_process(app_process)
        app_process = None
        database = query_safe_database_evidence(database_path, latest_version)
        report["databaseEvidence"] = database
        provider_rows = _provider_rows(database)
        stage_provider_calls = {
            stage: sum(
                int(item.get("providerCalls") or 0)
                for item in provider_rows
                if item.get("stage") == stage
                and item.get("provider") == "aliyun_bailian"
                and item.get("status") == "provider_succeeded"
            )
            for stage in EXPECTED_AGENT_STAGES
        }
        total_provider_calls = sum(int(item.get("providerCalls") or 0) for item in provider_rows)
        non_bailian_calls = sum(
            int(item.get("providerCalls") or 0)
            for item in provider_rows
            if item.get("provider") != "aliyun_bailian"
        )
        models = sorted({str(item.get("model") or "") for item in provider_rows if item.get("model")})
        admitted = _stage_count(database, "task_admitted")
        mapped = _stage_count(database, "task_mapped")
        agent3_ready = _stage_count(database, "agent3_sop_ready")
        assertions = {
            "threeJudgeXlsxUploaded": len(imports) == 3,
            "allUploadsParsedAsXlsx": all(
                (item.get("confirmUploadMeta") or {}).get("format") == "xlsx"
                for item in imports
            ),
            "allUploadsExposeThreeOrMoreSheets": all(
                int((item.get("confirmUploadMeta") or {}).get("sheetCount") or 0) >= 3
                for item in imports
            ),
            "latestDataVersionPresent": bool(latest_version),
            "registeredBailianInterfaceUsed": non_bailian_calls == 0 and total_provider_calls >= 3,
            "agent1RealProviderSucceeded": stage_provider_calls["product_judgment_agent"] >= 1,
            "agent2RealProviderSucceeded": stage_provider_calls["action_plan_judgment_agent"] >= 1,
            "agent3RealProviderSucceeded": stage_provider_calls["agent3_sop_agent"] >= 1,
            "providerRequestIdsObserved": int(database.get("providerRequestIdCount") or 0) >= 3,
            "qwenModelObserved": bool(models) and all(model_name.lower().startswith("qwen") for model_name in models),
            "taskMappedOrAdmitted": mapped + admitted >= 1,
            "agent3ReadyOrTaskAdmitted": agent3_ready + admitted >= 1,
            "taskViewReturnsItems": int(report["viewEvidence"]["taskCount"] or 0) >= 1,
            "productViewReturnsItems": int(report["viewEvidence"]["productCount"] or 0) >= 3,
            "pipelineReachedIdleTerminal": report["tickSummary"]["idleTerminalReached"] is True,
            "noTickErrors": report["tickSummary"]["errorTicks"] == 0,
            "productionBoundaryDisjoint": all(
                value is False for value in report["productionBoundary"].values()
            ),
        }
        report["providerEvidence"] = {
            "provider": "aliyun_bailian",
            "models": models,
            "stageProviderCalls": stage_provider_calls,
            "totalProviderCalls": total_provider_calls,
            "nonBailianProviderCalls": non_bailian_calls,
            "providerRequestIdCount": database.get("providerRequestIdCount"),
            "providerRequestIdSetHash": database.get("providerRequestIdSetHash"),
        }
        report["assertions"] = assertions
        failed = [name for name, passed in assertions.items() if passed is not True]
        if failed:
            raise QwenLiveEvidenceError("LIVE_ASSERTIONS_FAILED:" + ",".join(failed))

        report["completedAtEpoch"] = time.time()
        report["durationSeconds"] = round(report["completedAtEpoch"] - started_at, 3)
        report["verificationHash"] = sha256_bytes(
            canonical_bytes(
                {
                    key: value
                    for key, value in report.items()
                    if key not in {"verified", "verificationHash", "errors"}
                }
            )
        )
        report["verified"] = True
        write_json(output_path, report)
        return report
    except Exception as exc:
        report.setdefault("errors", []).append(f"{type(exc).__name__}:{exc}")
        report["processStop"] = stop_process(app_process)
        report["completedAtEpoch"] = time.time()
        report["durationSeconds"] = round(report["completedAtEpoch"] - started_at, 3)
        try:
            if database_path.is_file() and report.get("latestDataVersion"):
                report["databaseEvidence"] = query_safe_database_evidence(
                    database_path, str(report["latestDataVersion"])
                )
        except Exception as database_exc:
            report["databaseEvidenceError"] = f"{type(database_exc).__name__}:{database_exc}"
        write_json(output_path, report)
        raise
    finally:
        stop_process(app_process)
        shutil.rmtree(candidate_root, ignore_errors=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect real Bailian/Qwen competition evidence.")
    parser.add_argument("archive")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--report-dir", default="web_demo/sample-data")
    parser.add_argument("--report", action="append", dest="reports")
    parser.add_argument(
        "--candidate-base",
        default="/opt/actions-runner-public/competition-qwen-live/ai-ecommerce-assistant-public",
    )
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--app-port", type=int, default=39380)
    parser.add_argument("--startup-timeout", type=float, default=150.0)
    parser.add_argument("--max-ticks", type=int, default=80)
    parser.add_argument("--model", default=os.getenv("COMPETITION_QWEN_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = str(os.getenv("COMPETITION_BAILIAN_API_KEY") or "").strip()
    try:
        result = run_live(
            archive=Path(args.archive).resolve(),
            source_commit=str(args.source_commit),
            report_dir=Path(args.report_dir).resolve(),
            report_names=tuple(args.reports or DEFAULT_REPORTS),
            candidate_base=Path(args.candidate_base).resolve(),
            runtime_python=Path(args.runtime_python).resolve(),
            app_port=int(args.app_port),
            output_path=Path(args.output).resolve(),
            startup_timeout=float(args.startup_timeout),
            max_ticks=int(args.max_ticks),
            model=str(args.model),
            api_key=api_key,
        )
    except Exception as exc:
        print(f"COMPETITION_QWEN_LIVE_EVIDENCE_FAILED={type(exc).__name__}:{exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "verified": result["verified"],
                "sourceCommit": result["sourceCommit"],
                "provider": result["providerEvidence"]["provider"],
                "models": result["providerEvidence"]["models"],
                "stageProviderCalls": result["providerEvidence"]["stageProviderCalls"],
                "taskCount": result["viewEvidence"]["taskCount"],
                "verificationHash": result["verificationHash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
