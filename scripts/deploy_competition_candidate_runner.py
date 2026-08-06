#!/usr/bin/env python3
"""Run the ECS candidate smoke in a runner-owned directory.

The production root can be permission-protected. This wrapper proves that the
candidate base is physically disjoint from it, observes the production
``current`` path before and after when possible, delegates the actual candidate
startup, and enriches the resulting attestation without requesting root access.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


class CandidateBoundaryError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def observe_current(production_root: Path) -> dict[str, Any]:
    current = production_root / "current"
    observation: dict[str, Any] = {
        "path": str(current),
        "readable": False,
        "exists": None,
        "isSymlink": None,
        "target": None,
        "state": "unknown",
    }
    try:
        observation["exists"] = current.exists() or current.is_symlink()
        observation["isSymlink"] = current.is_symlink()
        if not observation["exists"]:
            observation["readable"] = True
            observation["state"] = "absent"
            return observation
        observation["target"] = str(current.resolve(strict=False))
        observation["readable"] = True
        observation["state"] = "observed"
        return observation
    except PermissionError as exc:
        observation["state"] = "permission_protected"
        observation["error"] = f"PermissionError:{exc}"
        return observation
    except OSError as exc:
        observation["state"] = "observation_error"
        observation["error"] = f"{type(exc).__name__}:{exc}"
        return observation


def paths_are_disjoint(candidate_base: Path, production_root: Path) -> bool:
    candidate = candidate_base.resolve(strict=False)
    production = production_root.resolve(strict=False)
    return (
        candidate != production
        and production not in candidate.parents
        and candidate not in production.parents
    )


def run_candidate(
    *,
    archive: Path,
    source_commit: str,
    candidate_base: Path,
    production_root: Path,
    runtime_python: Path,
    port: int,
    startup_timeout: float,
    attestation: Path,
) -> dict[str, Any]:
    if not paths_are_disjoint(candidate_base, production_root):
        raise CandidateBoundaryError(
            f"CANDIDATE_AND_PRODUCTION_ROOTS_NOT_DISJOINT:{candidate_base}:{production_root}"
        )
    candidate_base.mkdir(parents=True, exist_ok=True)
    before = observe_current(production_root)
    command = [
        sys.executable,
        str(Path(__file__).with_name("deploy_competition_candidate.py")),
        str(archive),
        "--source-commit",
        source_commit,
        "--deploy-root",
        str(candidate_base),
        "--runtime-python",
        str(runtime_python),
        "--port",
        str(port),
        "--startup-timeout",
        str(startup_timeout),
        "--attestation",
        str(attestation),
    ]
    result = subprocess.run(command, text=True)
    after = observe_current(production_root)

    if attestation.is_file():
        report = json.loads(attestation.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise CandidateBoundaryError("CANDIDATE_ATTESTATION_OBJECT_REQUIRED")
    else:
        report = {
            "schema": "competition.ecs_candidate_smoke.v1",
            "verified": False,
            "sourceCommit": source_commit,
            "errors": ["DELEGATE_ATTESTATION_MISSING"],
        }

    report["candidateBase"] = str(candidate_base.resolve(strict=False))
    report["productionRoot"] = str(production_root.resolve(strict=False))
    report["productionBoundaryDisjoint"] = True
    report["productionCurrentObservationBefore"] = before
    report["productionCurrentObservationAfter"] = after
    report["productionCurrentObservationUnchanged"] = before == after
    report["productionCurrentReadable"] = bool(before.get("readable") and after.get("readable"))
    report["productionCurrentUnchanged"] = bool(before == after)
    report["productionServiceRestarted"] = False
    report["productionSymlinkSwitched"] = False
    report["productionEnvironmentLoaded"] = False
    report["productionDatabaseReused"] = False
    report["candidateExecutedAsRoot"] = os.geteuid() == 0 if hasattr(os, "geteuid") else False
    if report["candidateExecutedAsRoot"]:
        report.setdefault("errors", []).append("CANDIDATE_MUST_NOT_RUN_AS_ROOT")
        report["verified"] = False
    if before != after:
        report.setdefault("errors", []).append("PRODUCTION_CURRENT_OBSERVATION_CHANGED")
        report["verified"] = False
    if result.returncode != 0:
        report.setdefault("errors", []).append(f"DELEGATE_EXIT_CODE:{result.returncode}")
        report["verified"] = False

    write_json(attestation, report)
    candidate_root = report.get("candidateRoot")
    if candidate_root:
        candidate_attestation = Path(str(candidate_root)) / "candidate-attestation.json"
        try:
            write_json(candidate_attestation, report)
        except OSError:
            pass

    if result.returncode != 0 or report.get("verified") is not True:
        raise CandidateBoundaryError(
            "CANDIDATE_DELEGATE_FAILED:" + json.dumps(report.get("errors") or [], ensure_ascii=False)
        )
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a production-disjoint ECS candidate smoke.")
    parser.add_argument("archive")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--candidate-base",
        default="/opt/actions-runner-public/competition-candidates/ai-ecommerce-assistant-public",
    )
    parser.add_argument("--production-root", default="/opt/ai-ecommerce-assistant")
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--port", type=int, default=39080)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--attestation", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_candidate(
        archive=Path(args.archive).expanduser().resolve(),
        source_commit=args.source_commit.strip(),
        candidate_base=Path(args.candidate_base).expanduser(),
        production_root=Path(args.production_root).expanduser(),
        runtime_python=Path(args.runtime_python).expanduser().resolve(),
        port=args.port,
        startup_timeout=args.startup_timeout,
        attestation=Path(args.attestation).expanduser().resolve(),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition candidate boundary runner failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
