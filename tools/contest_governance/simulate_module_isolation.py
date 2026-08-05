"""Simulate REGISTERED_ONLY module isolation in a detached Git worktree.

The source branch is never physically modified. Only paths declared by the sealed
isolation transaction are removed from the temporary worktree. Core/support Runner
integrity, graph identity, selection identity, and layered-root composition are then
revalidated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set


def _read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


_SIMULATION_OUTPUT_PATHS = {
    "governance/contest/generated/module-isolation-simulation.json",
    "governance/contest/generated/module-isolation-simulation-receipt.json",
}


def _status_without_declared_outputs(root: Path) -> str:
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=no")
    retained: List[str] = []
    for line in status.splitlines():
        path_text = line[3:] if len(line) >= 4 else ""
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        if path_text in _SIMULATION_OUTPUT_PATHS:
            continue
        retained.append(line)
    return "\n".join(retained)


def _module_ids(selection: Mapping[str, Any], classification: str) -> Set[str]:
    return {
        str(value)
        for value in (selection.get("classifications") or {}).get(classification) or []
        if str(value)
    }


def _runner_drift_modules(audit: Mapping[str, Any]) -> Set[str]:
    return {
        str(item.get("moduleId") or "")
        for item in audit.get("runnerDrift") or []
        if isinstance(item, dict) and str(item.get("moduleId") or "")
    }


def _verify_keep_runner_audits(
    audit: Mapping[str, Any],
    keep_modules: Set[str],
) -> Dict[str, Any]:
    module_audits = dict(audit.get("moduleAudits") or {})
    failures: List[Dict[str, Any]] = []
    for module_id in sorted(keep_modules):
        item = dict(module_audits.get(module_id) or {})
        if not item:
            failures.append({"moduleId": module_id, "reason": "MODULE_AUDIT_MISSING"})
            continue
        if item.get("runnerFileExists") is not True:
            failures.append({"moduleId": module_id, "reason": "RUNNER_FILE_MISSING"})
        if item.get("runnerSymbolExists") is not True:
            failures.append({"moduleId": module_id, "reason": "RUNNER_SYMBOL_MISSING"})
    return {
        "verified": not failures,
        "checkedModuleIds": sorted(keep_modules),
        "failures": failures,
    }


def run(root: Path, generated_dir: Path) -> Dict[str, Any]:
    root = root.resolve()
    generated_dir = generated_dir.resolve()
    transaction = _read_object(generated_dir / "module-isolation-transaction.json")
    receipt = _read_object(generated_dir / "module-isolation-receipt.json")
    baseline_selection = _read_object(generated_dir / "contest-selection-manifest.json")

    excluded_paths = [str(value) for value in receipt.get("simulationExcludePaths") or []]
    expected_paths = [
        "tools/registry_compiler/v24_identity_catalog.py",
        "tools/registry_compiler/v24_task_blueprint_compiler.py",
    ]
    if excluded_paths != expected_paths:
        raise RuntimeError(f"SIMULATION_PATH_SET_MISMATCH:{excluded_paths}")

    isolate_modules = set(str(value) for value in transaction.get("isolateModuleIds") or [])
    expected_isolate_modules = _module_ids(baseline_selection, "ISOLATE")
    if isolate_modules != expected_isolate_modules:
        raise RuntimeError(
            "ISOLATE_MODULE_SET_MISMATCH:"
            f"{sorted(isolate_modules)}:{sorted(expected_isolate_modules)}"
        )

    keep_modules = _module_ids(baseline_selection, "KEEP_CORE") | _module_ids(
        baseline_selection, "KEEP_SUPPORT"
    )
    core_physical_paths = {
        str(item.get("path") or "")
        for item in baseline_selection.get("physicalPathReview") or []
        if isinstance(item, dict)
        and item.get("classification") in {"KEEP_CORE", "KEEP_SUPPORT"}
    }
    excluded_keep_overlap = sorted(core_physical_paths.intersection(excluded_paths))

    excluded_blobs = {
        path: _git(root, "rev-parse", f"HEAD:{path}") for path in excluded_paths
    }
    source_commit = _git(root, "rev-parse", "HEAD")
    original_status = _status_without_declared_outputs(root)

    temporary_parent = Path(tempfile.mkdtemp(prefix="contest-isolation-"))
    simulation_root = temporary_parent / "worktree"
    simulated_output = simulation_root / "governance/contest/simulation-generated"

    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(simulation_root), "HEAD"],
            cwd=root,
            check=True,
        )
        for relative in excluded_paths:
            path = simulation_root / relative
            if not path.is_file():
                raise RuntimeError(f"SIMULATION_EXCLUDE_PATH_MISSING:{relative}")
            path.unlink()

        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(simulation_root)
        completed = subprocess.run(
            [
                sys.executable,
                str(simulation_root / "tools/contest_governance/run_selection.py"),
                "--root",
                str(simulation_root),
                "--request",
                "governance/contest/selection-request.json",
                "--output",
                "governance/contest/simulation-generated",
                "--z-source",
                str(
                    simulation_root
                    / "governance/contest/z-interface/Z1.0.5"
                ),
            ],
            cwd=simulation_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        simulated_selection = _read_object(
            simulated_output / "contest-selection-manifest.json"
        )
        simulated_audit = _read_object(simulated_output / "repository-audit.json")
        simulated_snapshot = _read_object(simulated_output / "registry-snapshot.json")

        drift_modules = _runner_drift_modules(simulated_audit)
        unexpected_drift_modules = sorted(drift_modules - isolate_modules)
        expected_isolate_drift_modules = sorted(drift_modules & isolate_modules)
        keep_runner_check = _verify_keep_runner_audits(simulated_audit, keep_modules)

        assertions = {
            "graphHashStable": simulated_selection.get("graphHash")
            == baseline_selection.get("graphHash"),
            "selectionHashStable": simulated_selection.get("selectionHash")
            == baseline_selection.get("selectionHash"),
            "classificationsStable": simulated_selection.get("classifications")
            == baseline_selection.get("classifications"),
            "safeKeepModulesStable": simulated_selection.get("safeKeepModuleIds")
            == baseline_selection.get("safeKeepModuleIds"),
            "noRunnerDriftOutsideIsolateSet": unexpected_drift_modules == [],
            "keepRunnerEvidenceVerified": keep_runner_check["verified"] is True,
            "rootCompositionVerified": simulated_snapshot.get("rootEquivalent") is True,
            "excludedPathsOutsideKeepClosure": excluded_keep_overlap == [],
        }
        if not all(assertions.values()):
            raise RuntimeError(
                "ISOLATION_SIMULATION_ASSERTION_FAILED:"
                + json.dumps(
                    {
                        "assertions": assertions,
                        "unexpectedRunnerDriftModuleIds": unexpected_drift_modules,
                        "keepRunnerCheck": keep_runner_check,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        material = {
            "schema": "contest.module_isolation_simulation.v2",
            "mode": "detached_worktree_report_only",
            "sourceCommit": source_commit,
            "transactionHash": transaction.get("transactionHash"),
            "excludedPaths": excluded_paths,
            "excludedPathBlobs": excluded_blobs,
            "isolateModuleIds": sorted(isolate_modules),
            "keepModuleIds": sorted(keep_modules),
            "baselineGraphHash": baseline_selection.get("graphHash"),
            "simulatedGraphHash": simulated_selection.get("graphHash"),
            "baselineSelectionHash": baseline_selection.get("selectionHash"),
            "simulatedSelectionHash": simulated_selection.get("selectionHash"),
            "simulatedRunnerDriftModuleIds": sorted(drift_modules),
            "expectedIsolateRunnerDriftModuleIds": expected_isolate_drift_modules,
            "unexpectedRunnerDriftModuleIds": unexpected_drift_modules,
            "keepRunnerCheck": keep_runner_check,
            "assertions": assertions,
            "selectionStdout": completed.stdout,
            "state": "ISOLATION_SIMULATION_VERIFIED_PHYSICAL_CHANGE_NOT_AUTHORIZED",
            "businessRuntimeMutated": False,
            "databaseMutated": False,
            "providerCallsExecuted": 0,
            "analysisBranchFilesDeleted": 0,
            "mainMutated": False,
            "physicalDeletionExecuted": False,
        }
        report = {**material, "simulationHash": _canonical_hash(material)}
        receipt_material = {
            "schema": "contest.module_isolation_simulation_receipt.v2",
            "simulationHash": report["simulationHash"],
            "transactionHash": transaction.get("transactionHash"),
            "graphHash": simulated_selection.get("graphHash"),
            "selectionHash": simulated_selection.get("selectionHash"),
            "unexpectedRunnerDriftCount": len(unexpected_drift_modules),
            "states": [
                "DETACHED_WORKTREE_CREATED",
                "ISOLATE_PATHS_EXCLUDED",
                "GRAPH_RECOMPUTED",
                "SELECTION_RECOMPUTED",
                "CORE_SUPPORT_RUNNER_GATES_VERIFIED",
                "LAYERED_ROOT_COMPOSITION_VERIFIED",
                "SIMULATION_VERIFIED",
            ],
            "businessRuntimeMutated": False,
            "databaseMutated": False,
            "providerCallsExecuted": 0,
            "analysisBranchFilesDeleted": 0,
            "mainMutated": False,
            "physicalDeletionExecuted": False,
        }
        simulation_receipt = {
            **receipt_material,
            "receiptHash": _canonical_hash(receipt_material),
        }
        _write_json(generated_dir / "module-isolation-simulation.json", report)
        _write_json(
            generated_dir / "module-isolation-simulation-receipt.json",
            simulation_receipt,
        )
        return {
            "simulationHash": report["simulationHash"],
            "receiptHash": simulation_receipt["receiptHash"],
            "assertions": assertions,
            "simulatedRunnerDriftModuleIds": sorted(drift_modules),
            "unexpectedRunnerDriftModuleIds": unexpected_drift_modules,
        }
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(simulation_root)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(temporary_parent, ignore_errors=True)
        final_status = _status_without_declared_outputs(root)
        if final_status != original_status:
            raise RuntimeError(
                f"SOURCE_WORKTREE_STATUS_CHANGED:{original_status!r}:{final_status!r}"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Simulate REGISTERED_ONLY module isolation in a detached worktree."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated", default="governance/contest/generated")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).resolve()
    result = run(root, (root / args.generated).resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
