#!/usr/bin/env python3
"""Production-worker driver for the Runtime Generation repeatability E2E.

The base probe intentionally exposes its candidate environment builder as a module
function. This driver keeps every assertion and both same-process Reset runs unchanged,
but restores the competition app's real single background Station/Agent Worker instead
of disabling it. The original Competition Three Report E2E also uses this production
worker mode.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
for path in (str(SCRIPT_DIR), str(ROOT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import run_competition_reset_repeatability_e2e as probe  # noqa: E402

_ORIGINAL_ENVIRONMENT = probe._environment


def _production_worker_environment(app_root, state_root, provider_url, app_port):
    environment = _ORIGINAL_ENVIRONMENT(app_root, state_root, provider_url, app_port)
    environment["STATION_QUEUE_WORKER_ENABLED"] = "true"
    environment["AGENT_PIPELINE_ITEM_WORKER_ENABLED"] = "true"
    environment["STATION_QUEUE_WORKER_INTERVAL"] = "1"
    environment["STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK"] = "12"
    environment["RUNTIME_GENERATION_REPEATABILITY_E2E"] = "true"
    return environment


def main(argv: Sequence[str] | None = None) -> int:
    probe._environment = _production_worker_environment
    try:
        return probe.main(argv)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "verified": False,
                    "driver": "production_single_worker",
                    "error": f"{type(exc).__name__}:{exc}",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
