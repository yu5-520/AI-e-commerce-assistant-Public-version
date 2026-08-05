"""Compile the approved-scope physical-prune candidates without deleting files."""
from __future__ import annotations

import ast
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set

from tools.contest_governance.scope_unregistered_fields import (
    _closure,
    _dependencies,
    _registered_seeds,
)


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _runner_path(runner: str) -> str:
    module, separator, _symbol = str(runner or "").partition(":")
    return "/".join(module.split(".")) + ".py" if separator and module else ""


def _all_python_importers(root: Path, candidates: Set[str]) -> Dict[str, List[str]]:
    result: MutableMapping[str, List[str]] = defaultdict(list)
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        for dependency in _dependencies(root, relative):
            if dependency in candidates:
                result[dependency].append(relative)
    return {key: sorted(set(value)) for key, value in sorted(result.items())}


def run(root: Path, output_dir: Path) -> Dict[str, Any]:
    root = root.resolve()
    application = _read(output_dir / "module-isolation-application.json")
    transaction = _read(output_dir / "module-isolation-transaction.json")
    selection = _read(output_dir / "contest-selection-manifest.json")
    policy = _read(root / "governance/contest/isolation-policy.json")
    modules_document = _read(root / "contracts/registry/modules.json")

    if application.get("state") != "LOGICAL_ISOLATION_APPLIED":
        raise RuntimeError("LOGICAL_ISOLATION_NOT_APPLIED")
    if application.get("nextState") != "PRUNE_CANDIDATE_COMPILATION_READY":
        raise RuntimeError("PRUNE_CANDIDATE_COMPILATION_NOT_AUTHORIZED")

    candidate_paths = sorted(set(str(value) for value in application.get("simulationExcludePaths") or []))
    if candidate_paths != sorted(set(str(value) for value in transaction.get("simulationExcludePaths") or [])):
        raise RuntimeError("CANDIDATE_PATH_SET_DRIFT")
    candidate_set = set(candidate_paths)

    seeds = _registered_seeds(selection)
    closures = {}
    origins = {}
    for classification in ("KEEP_CORE", "KEEP_SUPPORT", "ISOLATE"):
        closure, origin = _closure(root, seeds[classification])
        closures[classification] = closure
        origins[classification] = origin

    module_claims: MutableMapping[str, List[str]] = defaultdict(list)
    module_states: Dict[str, str] = {}
    for raw in modules_document.get("modules") or []:
        if not isinstance(raw, dict):
            continue
        module_id = str(raw.get("moduleId") or "")
        path = _runner_path(str(raw.get("runner") or ""))
        if path in candidate_set:
            module_claims[path].append(module_id)
            module_states[module_id] = str(raw.get("status") or raw.get("activationState") or "")

    importers = _all_python_importers(root, candidate_set)
    isolated_modules = set(str(value) for value in application.get("isolatedModuleIds") or [])
    policy_preserve = set(str(value) for value in policy.get("physicalPathPolicy", {}).get("preserveUntilPhysicalPruneApproval") or [])

    candidates: List[Dict[str, Any]] = []
    authorized: List[str] = []
    blocked: List[str] = []
    for path in candidate_paths:
        claimants = sorted(set(module_claims.get(path) or []))
        importer_paths = sorted(importers.get(path) or [])
        importer_scopes = {
            importer: sorted(
                classification
                for classification, paths in closures.items()
                if importer in paths
            )
            for importer in importer_paths
        }
        keep_importers = sorted(
            importer
            for importer, scopes in importer_scopes.items()
            if "KEEP_CORE" in scopes or "KEEP_SUPPORT" in scopes
        )
        isolate_importers = sorted(
            importer for importer, scopes in importer_scopes.items() if "ISOLATE" in scopes
        )
        registry_claims_preserved = bool(claimants)
        all_claimants_isolated = bool(claimants) and set(claimants).issubset(isolated_modules)
        blockers: List[str] = []
        if registry_claims_preserved:
            blockers.append("PRESERVED_REGISTRY_RUNNER_IDENTITIES")
        if keep_importers:
            blockers.append("KEEP_CLOSURE_IMPORTS_CANDIDATE")
        if path in policy_preserve:
            blockers.append("ISOLATION_POLICY_PRESERVE_UNTIL_PRUNE_APPROVAL")
        if not (root / path).is_file():
            blockers.append("CANDIDATE_FILE_MISSING")
        physically_authorized = not blockers
        if physically_authorized:
            authorized.append(path)
        else:
            blocked.append(path)
        candidates.append({
            "path": path,
            "pathExists": (root / path).is_file(),
            "registryClaimingModuleIds": claimants,
            "registryClaimantStates": {module_id: module_states.get(module_id) for module_id in claimants},
            "allRegistryClaimantsLogicallyIsolated": all_claimants_isolated,
            "registryClaimsPreserved": registry_claims_preserved,
            "importerPaths": importer_paths,
            "importerScopes": importer_scopes,
            "keepClosureImporterPaths": keep_importers,
            "isolateImporterPaths": isolate_importers,
            "inKeepCoreClosure": path in closures["KEEP_CORE"],
            "inKeepSupportClosure": path in closures["KEEP_SUPPORT"],
            "inIsolateClosure": path in closures["ISOLATE"],
            "policyPreserveUntilApproval": path in policy_preserve,
            "blockers": blockers,
            "physicalDeletionAuthorized": physically_authorized,
            "decision": "REMOVE_CANDIDATE_AUTHORIZED" if physically_authorized else "REMOVE_CANDIDATE_BLOCKED",
        })

    material = {
        "schema": "contest.physical_prune_candidate_manifest.v1",
        "state": "PRUNE_CANDIDATES_COMPILED_PHYSICAL_DELETE_BLOCKED" if blocked else "PRUNE_CANDIDATES_READY_FOR_APPROVAL",
        "mode": "report_only",
        "sourceIsolationApplicationHash": application.get("applicationHash"),
        "registryRootHash": application.get("registryRootHash"),
        "graphHash": application.get("graphHash"),
        "selectionHash": application.get("selectionHash"),
        "candidatePaths": candidate_paths,
        "authorizedDeletePaths": authorized,
        "blockedCandidatePaths": blocked,
        "candidates": candidates,
        "summary": {
            "candidateCount": len(candidate_paths),
            "authorizedDeleteCount": len(authorized),
            "blockedCandidateCount": len(blocked),
            "preservedRegistryClaimCount": sum(len(item["registryClaimingModuleIds"]) for item in candidates),
            "keepClosureImporterCount": sum(len(item["keepClosureImporterPaths"]) for item in candidates),
        },
        "nextRequiredTransaction": (
            "RETIRE_OR_EXTERNALIZE_ISOLATED_REGISTRY_RUNNER_IDENTITIES_BEFORE_PHYSICAL_DELETE"
            if blocked else "HUMAN_APPROVAL_REQUIRED_BEFORE_PHYSICAL_DELETE"
        ),
        "physicalDeletionExecuted": False,
        "physicalFilesMoved": 0,
        "mainMutated": False,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "promotionExecuted": False,
        "publicReleaseExecuted": False,
    }
    report = {**material, "candidateManifestHash": _hash(material)}
    receipt_material = {
        "schema": "contest.physical_prune_candidate_receipt.v1",
        "state": report["state"],
        "candidateManifestHash": report["candidateManifestHash"],
        "registryRootHash": report["registryRootHash"],
        "graphHash": report["graphHash"],
        "selectionHash": report["selectionHash"],
        "candidateCount": len(candidate_paths),
        "authorizedDeleteCount": len(authorized),
        "blockedCandidateCount": len(blocked),
        "physicalDeletionExecuted": False,
        "mainMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }
    receipt = {**receipt_material, "receiptHash": _hash(receipt_material)}
    _write(output_dir / "physical-prune-candidate-manifest.json", report)
    _write(output_dir / "physical-prune-candidate-receipt.json", receipt)
    return {
        "state": report["state"],
        "candidateManifestHash": report["candidateManifestHash"],
        "receiptHash": receipt["receiptHash"],
        "summary": report["summary"],
        "nextRequiredTransaction": report["nextRequiredTransaction"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = Path.cwd().resolve()
    result = run(root, root / "governance/contest/generated")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
