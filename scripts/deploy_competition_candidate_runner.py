#!/usr/bin/env python3
"""Run the ECS candidate smoke in a production-disjoint runner directory.

The wrapper never scans permission-protected production paths for Python. The
workflow must provide one already verified Python 3.11.9 virtual-environment
executable, and that exact path is preserved so its site-packages remain active.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import deploy_competition_candidate as candidate_module  # noqa: E402


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


def strict_explicit_runtime(explicit: str | None) -> list[Path]:
    if not explicit:
        raise CandidateBoundaryError("EXPLICIT_RUNTIME_PYTHON_REQUIRED")
    path = Path(explicit).expanduser().absolute()
    try:
        is_file = path.is_file()
        executable = os.access(path, os.X_OK)
    except OSError as exc:
        raise CandidateBoundaryError(
            f"EXPLICIT_RUNTIME_PYTHON_UNREADABLE:{path}:{type(exc).__name__}:{exc}"
        ) from exc
    if not is_file or not executable:
        raise CandidateBoundaryError(f"EXPLICIT_RUNTIME_PYTHON_INVALID:{path}")
    return [path]


def inspect_explicit_runtime(path: Path) -> dict[str, Any]:
    command = [
        str(path),
        "-c",
        (
            "import json,platform,sys;"
            "import fastapi,uvicorn,sqlalchemy,pydantic,openpyxl;"
            "print(json.dumps({"
            "'pythonVersion':platform.python_version(),"
            "'executable':sys.executable,"
            "'prefix':sys.prefix,"
            "'basePrefix':sys.base_prefix,"
            "'fastapi':fastapi.__version__,"
            "'uvicorn':uvicorn.__version__,"
            "'sqlalchemy':sqlalchemy.__version__,"
            "'pydantic':pydantic.__version__,"
            "'openpyxl':openpyxl.__version__},sort_keys=True));"
            "raise SystemExit(0 if sys.version_info[:3]==(3,11,9) "
            "and sys.prefix != sys.base_prefix else 1)"
        ),
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = (result.stdout or result.stderr or "").strip()
    try:
        details = json.loads(output.splitlines()[-1]) if output else {}
    except json.JSONDecodeError:
        details = {"raw": output[:2000]}
    return {
        "path": str(path),
        "usable": result.returncode == 0,
        "returnCode": result.returncode,
        **details,
    }


def augment_boundary_report(
    report: dict[str, Any],
    *,
    candidate_base: Path,
    production_root: Path,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    report["candidateBase"] = str(candidate_base.resolve(strict=False))
    report["productionRoot"] = str(production_root.resolve(strict=False))
    report["productionBoundaryDisjoint"] = True
    report["productionCurrentObservationBefore"] = before
    report["productionCurrentObservationAfter"] = after
    report["productionCurrentObservationUnchanged"] = before == after
    report["productionCurrentReadable"] = bool(
        before.get("readable") and after.get("readable")
    )
    report["productionCurrentUnchanged"] = before == after
    report["productionServiceRestarted"] = False
    report["productionSymlinkSwitched"] = False
    report["productionEnvironmentLoaded"] = False
    report["productionDatabaseReused"] = False
    report["candidateExecutedAsRoot"] = (
        os.geteuid() == 0 if hasattr(os, "geteuid") else False
    )
    report["runtimeSelectionMode"] = "explicit_verified_venv_only"
    if report["candidateExecutedAsRoot"]:
        report.setdefault("errors", []).append("CANDIDATE_MUST_NOT_RUN_AS_ROOT")
        report["verified"] = False
    if before != after:
        report.setdefault("errors", []).append(
            "PRODUCTION_CURRENT_OBSERVATION_CHANGED"
        )
        report["verified"] = False
    return report


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
            f"CANDIDATE_AND_PRODUCTION_ROOTS_NOT_DISJOINT:"
            f"{candidate_base}:{production_root}"
        )
    candidate_base.mkdir(parents=True, exist_ok=True)
    strict_runtime = strict_explicit_runtime(str(runtime_python))[0]
    runtime_probe = inspect_explicit_runtime(strict_runtime)
    if runtime_probe.get("usable") is not True:
        raise CandidateBoundaryError(
            "EXPLICIT_RUNTIME_VERIFICATION_FAILED:"
            + json.dumps(runtime_probe, ensure_ascii=False)
        )
    before = observe_current(production_root)

    original_selector = candidate_module.candidate_python_paths
    original_inspector = candidate_module.inspect_runtime_python
    candidate_module.candidate_python_paths = (
        lambda _deploy_root, explicit: strict_explicit_runtime(explicit)
    )
    candidate_module.inspect_runtime_python = inspect_explicit_runtime
    failure: Exception | None = None
    report: dict[str, Any]
    try:
        report = candidate_module.deploy_candidate(
            archive=archive,
            deploy_root=candidate_base,
            expected_source_commit=source_commit,
            preferred_port=port,
            explicit_python=str(strict_runtime),
            attestation_path=attestation,
            startup_timeout=startup_timeout,
        )
        report["explicitRuntimeProbe"] = runtime_probe
    except Exception as exc:
        failure = exc
        if attestation.is_file():
            loaded = json.loads(attestation.read_text(encoding="utf-8"))
            report = loaded if isinstance(loaded, dict) else {}
        else:
            report = {
                "schema": "competition.ecs_candidate_smoke.v1",
                "verified": False,
                "sourceCommit": source_commit,
                "errors": [],
            }
        report["explicitRuntimeProbe"] = runtime_probe
        report.setdefault("errors", []).append(
            f"CANDIDATE_START_FAILED:{type(exc).__name__}:{exc}"
        )
        report["verified"] = False
    finally:
        candidate_module.candidate_python_paths = original_selector
        candidate_module.inspect_runtime_python = original_inspector

    after = observe_current(production_root)
    augment_boundary_report(
        report,
        candidate_base=candidate_base,
        production_root=production_root,
        before=before,
        after=after,
    )
    write_json(attestation, report)

    candidate_root = report.get("candidateRoot")
    if candidate_root:
        try:
            write_json(
                Path(str(candidate_root)) / "candidate-attestation.json",
                report,
            )
        except OSError:
            pass

    if failure is not None or report.get("verified") is not True:
        raise CandidateBoundaryError(
            "CANDIDATE_FAILED:"
            + json.dumps(report.get("errors") or [], ensure_ascii=False)
        ) from failure
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a production-disjoint ECS candidate smoke."
    )
    parser.add_argument("archive")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--candidate-base",
        default=(
            "/opt/actions-runner-public/competition-candidates/"
            "ai-ecommerce-assistant-public"
        ),
    )
    parser.add_argument(
        "--production-root",
        default="/opt/ai-ecommerce-assistant",
    )
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
        runtime_python=Path(args.runtime_python).expanduser().absolute(),
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
            "competition candidate boundary runner failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
