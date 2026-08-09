#!/usr/bin/env python3
"""Prove page-like autonomous single-worker handoff without manual pipeline ticks.

This probe launches the precise competition package with the deterministic contract
provider, imports the fixed three-report scenario, and never calls
/api/system/run-agent-pipeline-tick. It verifies two independent handoffs:

1. first import -> product read model, proving the background worker leaves
   report_received and processes pre-Agent stations without a fixed long poll;
2. imports -> Agent1 provider call, proving the same single worker reaches the Agent
   mainline automatically.

The provider is deterministic, so this is orchestration evidence, not model-quality
proof. Real Bailian/Qwen evidence remains a separate gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_competition_three_report_e2e as base  # noqa: E402
from deploy_competition_candidate import (  # noqa: E402
    make_application_read_only,
    make_candidate_writable_boundaries,
    safe_extract,
)
from verify_competition_runtime_package import verify_archive  # noqa: E402

SCHEMA = "competition.autonomous_worker_handoff.v1"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _poll(
    predicate,
    *,
    timeout: float,
    interval: float = 0.4,
) -> tuple[bool, float, Any]:
    started = time.monotonic()
    last: Any = None
    while time.monotonic() - started < timeout:
        last = predicate()
        if last:
            return True, round(time.monotonic() - started, 6), last
        time.sleep(interval)
    return False, round(time.monotonic() - started, 6), last


def _provider_calls(provider_url: str) -> dict[str, Any]:
    try:
        stats = base.provider_stats(provider_url)
    except Exception:
        return {}
    return stats if isinstance(stats, dict) else {}


def _product_count(app_url: str) -> int:
    try:
        _, products = base.http_json(
            "GET", app_url + "/api/view/products", headers=base.USER_HEADERS, timeout=5.0
        )
        return int(base.recursive_list_count(products) or 0)
    except Exception:
        return 0


def run_probe(
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
    pre_agent_timeout: float,
    agent_timeout: float,
) -> dict[str, Any]:
    verify_archive(archive, source_commit)
    scenario = base.read_scenario(scenario_path)
    scenario_hash = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    candidate_id = f"auto-{source_commit[:12]}-{scenario_hash[:10]}"
    candidate_root = candidate_base / candidate_id
    app_root = candidate_root / "app"
    state_root = candidate_root / "state"
    evidence_root = candidate_root / "evidence"
    app_log = evidence_root / "candidate-app.log"
    provider_log = evidence_root / "fixture-provider.log"
    provider_evidence = evidence_root / "fixture-provider-evidence.json"
    app_process: subprocess.Popen[Any] | None = None
    provider_process: subprocess.Popen[Any] | None = None

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "sourceCommit": source_commit,
        "scenarioId": scenario.get("scenarioId"),
        "candidateId": candidate_id,
        "manualPipelineTickCalls": 0,
        "modelQualityProof": False,
        "realBailianRunStillRequired": True,
        "verified": False,
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

        compat_provider = (
            SCRIPTS
            / "competition_e2e_compat_runtime"
            / "competition_contract_fixture_provider.py"
        )
        with provider_log.open("w", encoding="utf-8") as handle:
            provider_process = subprocess.Popen(
                [
                    str(tool_python),
                    str(compat_provider),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(provider_port),
                    "--evidence",
                    str(provider_evidence),
                ],
                cwd=state_root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        base.wait_http(provider_url, "/health", provider_process, startup_timeout)

        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(app_root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "AI_RELEASE_ROOT": str(app_root),
                "AI_RELEASE_MANIFEST": str(state_root / "no-legacy-release-manifest.json"),
                "AI_RELEASE_REQUIRED": "0",
                "ARTIFACT_ROOT": str(state_root / "data" / "artifacts"),
                "STATION_QUEUE_WORKER_ENABLED": "true",
                "STATION_QUEUE_WORKER_INTERVAL": "1",
                "STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK": "12",
                "AGENT_PIPELINE_ITEM_WORKER_ENABLED": "true",
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

        with app_log.open("w", encoding="utf-8") as handle:
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
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

        health = base.wait_http(app_url, "/api/health", app_process, startup_timeout)
        report["health"] = health
        background = health.get("backgroundWorker") if isinstance(health, dict) else {}
        report["backgroundWorkerConfig"] = background if isinstance(background, dict) else {}
        base.http_json(
            "POST",
            app_url
            + "/api/system/reset-runtime-data?confirm=true&include_audit_logs=true&scope=demo",
            headers=base.USER_HEADERS,
            timeout=60.0,
        )

        imports: list[dict[str, Any]] = []
        first_import_started = time.monotonic()
        for index, raw_report in enumerate(scenario["reports"], 1):
            payload = {
                "datasetName": scenario.get("datasetName") or "products",
                "sourceSystem": scenario.get("sourceSystem") or "competition_fixture",
                "rows": raw_report.get("rows"),
                "reportProfile": {
                    "scenarioId": scenario.get("scenarioId"),
                    "reportId": raw_report.get("reportId"),
                    "period": raw_report.get("period"),
                    "fixture": True,
                    "autonomousWorkerProbe": True,
                },
            }
            _, imported = base.http_json(
                "POST",
                app_url + "/api/data/import/confirm",
                payload=payload,
                headers=base.USER_HEADERS,
                timeout=60.0,
            )
            if not isinstance(imported, dict) or imported.get("ok") is not True:
                raise base.ThreeReportE2EError(f"AUTONOMOUS_IMPORT_FAILED:{index}:{imported}")
            data_version = str(imported.get("dataVersion") or "")
            if not data_version:
                versions = imported.get("dataVersions")
                data_version = str(versions[-1]) if isinstance(versions, list) and versions else ""
            imports.append(
                {
                    "index": index,
                    "reportId": raw_report.get("reportId"),
                    "dataVersion": data_version,
                }
            )
            if index == 1:
                product_ok, product_seconds, product_count = _poll(
                    lambda: (_product_count(app_url) or 0),
                    timeout=pre_agent_timeout,
                )
                report["preAgentAutonomousHandoff"] = {
                    "verified": product_ok and int(product_count or 0) >= 3,
                    "seconds": product_seconds,
                    "productCount": int(product_count or 0),
                    "thresholdSeconds": pre_agent_timeout,
                    "manualTickCalls": 0,
                }
            # Let the same worker naturally finish the current oldest version before
            # the next historical observation arrives. No manual tick is issued.
            time.sleep(1.25)

        report["imports"] = imports
        provider_ok, provider_seconds, provider_stats = _poll(
            lambda: (
                (lambda stats: stats if int(stats.get("callCount") or 0) > 0 else None)(
                    _provider_calls(provider_url)
                )
            ),
            timeout=agent_timeout,
        )
        report["agentAutonomousHandoff"] = {
            "verified": provider_ok,
            "secondsFromPollingStart": provider_seconds,
            "secondsFromFirstImport": round(time.monotonic() - first_import_started, 6),
            "thresholdSeconds": agent_timeout,
            "providerCallCount": int(_dict(provider_stats).get("callCount") or 0),
            "stageCounts": _dict(_dict(provider_stats).get("stageCounts")),
            "manualTickCalls": 0,
        }

        latest_version = imports[-1]["dataVersion"] if imports else ""
        pipeline_status: Any = {}
        if latest_version:
            try:
                _, pipeline_status = base.http_json(
                    "GET",
                    app_url
                    + "/api/system/agent-pipeline-status?"
                    + urllib.parse.urlencode({"dataVersion": latest_version}),
                    headers=base.USER_HEADERS,
                    timeout=10.0,
                )
            except Exception as exc:
                pipeline_status = {"probeError": f"{type(exc).__name__}:{exc}"}
        report["pipelineStatus"] = pipeline_status

        assertions = {
            "workerEnabledByEnv": _dict(background).get("enabledByEnv") is True,
            "agentPipelineEnabled": _dict(background).get("agentPipelineEnabled") is True,
            "preAgentLeavesReportReceivedAutomatically": _dict(
                report.get("preAgentAutonomousHandoff")
            ).get("verified")
            is True,
            "agentProviderReachedAutomatically": _dict(
                report.get("agentAutonomousHandoff")
            ).get("verified")
            is True,
            "noManualTick": report.get("manualPipelineTickCalls") == 0,
            "singleProcessApp": environment.get("APP_WORKERS") == "1",
        }
        report["assertions"] = assertions
        failed = [key for key, value in assertions.items() if value is not True]
        if failed:
            raise base.ThreeReportE2EError(
                "AUTONOMOUS_WORKER_ASSERTIONS_FAILED:" + ",".join(failed)
            )
        material = {
            key: value
            for key, value in report.items()
            if key not in {"verified", "verificationHash", "errors"}
        }
        report["verificationHash"] = "sha256:" + hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        report["verified"] = True
        _write(output_path, report)
        _write(evidence_root / "autonomous-worker-attestation.json", report)
        return report
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}:{exc}")
        _write(output_path, report)
        raise
    finally:
        report["appProcessStop"] = base.stop_process(app_process)
        report["providerProcessStop"] = base.stop_process(provider_process)
        if report.get("verified"):
            _write(output_path, report)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--scenario", default="fixtures/competition/three_report_scenario.json")
    parser.add_argument("--candidate-base", required=True)
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--tool-python", required=True)
    parser.add_argument("--app-port", type=int, default=39480)
    parser.add_argument("--provider-port", type=int, default=39400)
    parser.add_argument("--startup-timeout", type=float, default=150.0)
    parser.add_argument("--pre-agent-timeout", type=float, default=30.0)
    parser.add_argument("--agent-timeout", type=float, default=90.0)
    parser.add_argument(
        "--output",
        default="dist/competition-autonomous-worker/autonomous-worker-attestation.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_probe(
        archive=Path(args.archive).resolve(),
        source_commit=str(args.source_commit),
        scenario_path=Path(args.scenario).resolve(),
        candidate_base=Path(args.candidate_base).resolve(),
        runtime_python=Path(args.runtime_python).resolve(),
        tool_python=Path(args.tool_python).resolve(),
        app_port=args.app_port,
        provider_port=args.provider_port,
        output_path=Path(args.output).resolve(),
        startup_timeout=args.startup_timeout,
        pre_agent_timeout=args.pre_agent_timeout,
        agent_timeout=args.agent_timeout,
    )
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "sourceCommit": report["sourceCommit"],
                "preAgentAutonomousHandoff": report.get("preAgentAutonomousHandoff"),
                "agentAutonomousHandoff": report.get("agentAutonomousHandoff"),
                "manualPipelineTickCalls": report["manualPipelineTickCalls"],
                "verificationHash": report.get("verificationHash"),
                "realBailianRunStillRequired": report["realBailianRunStillRequired"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
