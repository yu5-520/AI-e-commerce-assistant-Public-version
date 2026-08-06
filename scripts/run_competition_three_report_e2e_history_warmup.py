#!/usr/bin/env python3
"""Warm historical reports before running the fixed three-report E2E.

The first two reports must pass the eight pre-Agent stations so the third report
receives real lineage/trend context. They are then closed as history-only versions
before Agent1 is allowed to execute. The third report remains the single business
version and is drained through Agent1 -> Agent2 -> Agent3 -> task mapping.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_competition_three_report_e2e as base  # noqa: E402
import run_competition_three_report_e2e_compat as compat  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    args = base.parse_args(argv)
    scenario_path = Path(args.scenario).expanduser().resolve()
    scenario_digest = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    candidate_id = f"{args.source_commit.strip()[:12]}-{scenario_digest[:12]}"
    database_path = (
        Path(args.candidate_base).expanduser()
        / candidate_id
        / "state"
        / "logs"
        / "product_workbench.sqlite3"
    )

    os.environ["STATION_QUEUE_WORKER_ENABLED"] = "false"
    os.environ["AGENT_PIPELINE_ITEM_WORKER_ENABLED"] = "true"
    os.environ["STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK"] = "12"
    base.SCRIPT_DIR = SCRIPTS_DIR / "competition_e2e_compat_runtime"
    base.query_runtime_database = compat.query_runtime_database

    original_http_json = base.http_json
    import_count = 0
    history_warmups: list[dict[str, Any]] = []

    def controlled_http_json(*request_args: Any, **request_kwargs: Any) -> tuple[int, Any]:
        nonlocal import_count
        status, response = original_http_json(*request_args, **request_kwargs)
        method = str(
            request_args[0] if request_args else request_kwargs.get("method") or ""
        )
        url = str(
            request_args[1] if len(request_args) > 1 else request_kwargs.get("url") or ""
        )
        if method.upper() != "POST" or "/api/data/import/confirm" not in url:
            return status, response

        import_count += 1
        if import_count > 2 or not isinstance(response, dict):
            return status, response
        version = str(response.get("dataVersion") or "")
        if not version:
            raise base.ThreeReportE2EError(
                f"HISTORY_IMPORT_DATA_VERSION_MISSING:{import_count}"
            )

        app_url = url.split("/api/data/import/confirm", 1)[0]
        _tick_status, tick = original_http_json(
            "POST",
            app_url + "/api/system/run-agent-pipeline-tick?limit=8",
            headers=base.USER_HEADERS,
            timeout=240.0,
        )
        if not isinstance(tick, dict) or tick.get("ran") is not True:
            raise base.ThreeReportE2EError(
                f"HISTORY_PRE_AGENT_WARMUP_FAILED:{version}:{tick}"
            )
        compat._history_only(database_path, version)
        history_warmups.append(
            {
                "dataVersion": version,
                "importIndex": import_count,
                "preAgentTickRan": True,
                "historyOnlyApplied": True,
                "rule": "eight_pre_agent_stations_completed_before_agent1_suppression",
            }
        )
        return status, response

    base.http_json = controlled_http_json
    try:
        return base.main(argv)
    finally:
        output = Path(args.output).expanduser().resolve()
        if output.is_file():
            try:
                import json

                report = json.loads(output.read_text(encoding="utf-8"))
                if isinstance(report, dict):
                    report["historyWarmups"] = history_warmups
                    output.write_text(
                        json.dumps(
                            report,
                            ensure_ascii=False,
                            sort_keys=True,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
            except Exception:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition three-report history warmup failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
