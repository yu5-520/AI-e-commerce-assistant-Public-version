"""V23.2.3 fail-closed verification for approved pull-request code changes."""
from __future__ import annotations

import copy
import fnmatch
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

from .change_program import verify_changed_path_scope
from .compile_registry import sha256_value, verify_committed_manifest
from .completeness_report import build_completeness_report, git_changed_paths
from .module_contracts import build_module_contracts
from .registry_migration import registry_file_hashes, require_registry_migration_plan

VERSION = "23.2.3"


class PostCodegenGateError(RuntimeError):
    pass


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strings(values: Iterable[Any]) -> List[str]:
    return sorted({str(v).strip().replace("\\", "/") for v in values if str(v).strip()})


def _hash(value: Mapping[str, Any]) -> str:
    return sha256_value(dict(value))


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _patterns(request: Mapping[str, Any]) -> List[str]:
    explicit = _strings(request.get("allowedTestPatterns") or [])
    if explicit:
        return explicit
    module_id = str(request.get("moduleId") or "")
    token = module_id.replace("_runtime", "").replace("_projection", "").replace("_", "*")
    result = [f"tests/test_*{token}*.py"] if token else []
    if module_id in {"registry_compiler", "release_governance"}:
        result.extend(
            [
                "tests/test_v23_1_*.py",
                "tests/test_v23_2_*.py",
                "tests/test_v24_*.py",
            ]
        )
    return _strings(result)


def build_test_plan(
    program: Mapping[str, Any],
    root: Path | None = None,
    *,
    changed_paths: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """Resolve module tests, preferring changed targeted tests over broad historical globs."""

    repository = (root or repository_root()).resolve()
    changed_tests = {
        path
        for path in _strings(changed_paths or [])
        if path.startswith("tests/") and (repository / path).is_file()
    }
    requests = [
        *(program.get("codegenRequests") or []),
        *(program.get("verificationRequests") or []),
    ]
    modules: List[Dict[str, Any]] = []
    tests: Set[str] = set()
    missing: List[str] = []
    for request in requests:
        module_id = str(request.get("moduleId") or "")
        patterns = _patterns(request)
        all_matched: Set[str] = set()
        for pattern in patterns:
            all_matched.update(
                path.relative_to(repository).as_posix()
                for path in repository.glob(pattern)
                if path.is_file() and repository in path.resolve().parents
            )
        targeted = {
            path
            for path in changed_tests
            if any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
        }
        selected = targeted or all_matched
        if module_id and not selected:
            missing.append(module_id)
        tests.update(selected)
        modules.append(
            {
                "moduleId": module_id,
                "requestId": str(request.get("requestId") or ""),
                "editAllowed": bool(request.get("editAllowed", True)),
                "patterns": patterns,
                "selectionMode": "changed_targeted" if targeted else "historical_pattern_fallback",
                "changedTargetedTests": sorted(targeted),
                "allMatchedTests": sorted(all_matched),
                "matchedTests": sorted(selected),
            }
        )
    return {
        "schema": "self_update.test_plan.v1",
        "version": VERSION,
        "programHash": program.get("programHash"),
        "changedTestPaths": sorted(changed_tests),
        "modules": modules,
        "tests": sorted(tests),
        "missingModuleTestCoverage": sorted(set(missing)),
        "ready": bool(tests) and not missing,
    }


def execute_test_plan(
    plan: Mapping[str, Any],
    root: Path | None = None,
    *,
    timeout_seconds: int = 1200,
) -> Dict[str, Any]:
    repository = (root or repository_root()).resolve()
    tests = _strings(plan.get("tests") or [])
    missing = _strings(plan.get("missingModuleTestCoverage") or [])
    command = [sys.executable, "-m", "pytest", *tests, "-q"] if tests and not missing else []
    if not command:
        result = {
            "status": "FAIL",
            "passed": False,
            "reason": "module_test_coverage_missing" if missing else "no_tests_resolved",
            "command": command,
            "returnCode": None,
            "stdout": "",
            "stderr": "",
        }
    else:
        try:
            completed = subprocess.run(
                command,
                cwd=repository,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            result = {
                "status": "PASS" if completed.returncode == 0 else "FAIL",
                "passed": completed.returncode == 0,
                "reason": "tests_passed" if completed.returncode == 0 else "tests_failed",
                "command": command,
                "returnCode": completed.returncode,
                "stdout": completed.stdout[-120000:],
                "stderr": completed.stderr[-120000:],
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "status": "FAIL",
                "passed": False,
                "reason": "tests_timed_out",
                "command": command,
                "returnCode": None,
                "stdout": str(exc.stdout or "")[-120000:],
                "stderr": str(exc.stderr or "")[-120000:],
            }
    material = {
        "programHash": plan.get("programHash"),
        "tests": tests,
        "missing": missing,
        **result,
    }
    return {
        "schema": "self_update.test_report.v1",
        "version": VERSION,
        "testPlan": dict(plan),
        **result,
        "testReportHash": _hash(material),
    }


def _scope_program(program: Mapping[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(dict(program))
    patterns = set(_strings(value.get("allowedPatterns") or []))
    patterns.update(
        {
            "contracts/requirements/**",
            "contracts/approvals/**",
            "contracts/changes/**",
            "outputs/change-transactions/**",
            "release/notes/**",
        }
    )
    if {"registry_compiler", "release_governance"} & set(
        _strings(value.get("directModules") or [])
    ):
        patterns.update(
            {
                ".github/workflows/v23.1-*.yml",
                "GOVERNANCE_VERSION.md",
                "docs/V23.1*.md",
                "docs/V23.2*.md",
                "docs/V24*.md",
                "contracts/self_update/**",
                "tests/test_v24_*.py",
            }
        )
    value["allowedPatterns"] = sorted(patterns)
    return value


def _path_hints(program: Mapping[str, Any], paths: Sequence[str]) -> Dict[str, List[str]]:
    manifest = dict(program.get("generatedChangeManifest") or {})
    hints: Dict[str, Set[str]] = {
        str(path): {str(module) for module in modules}
        for path, modules in dict(manifest.get("pathModuleHints") or {}).items()
    }
    for request in program.get("codegenRequests") or []:
        module_id = str(request.get("moduleId") or "")
        allowed = set(_strings(request.get("allowedPaths") or []))
        patterns = _patterns(request)
        for path in paths:
            if path in allowed or any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
                hints.setdefault(path, set()).add(module_id)
    return {path: sorted(modules) for path, modules in sorted(hints.items())}


def build_completeness_gate_report(
    transaction: Mapping[str, Any],
    program: Mapping[str, Any],
    paths: Sequence[str],
    root: Path | None = None,
) -> Dict[str, Any]:
    repository = (root or repository_root()).resolve()
    migration_plan = program.get("registryMigrationPlan")
    migration_targets: Set[str] = set()
    migration_changed: Set[str] = set()
    if isinstance(migration_plan, Mapping):
        plan = require_registry_migration_plan(migration_plan)
        migration_targets = set(_strings(plan["targetModules"]))
        migration_changed = set(_strings(paths)) & set(_strings(plan["allowedRegistryPaths"]))

    manifest = copy.deepcopy(dict(program.get("generatedChangeManifest") or {}))
    approval = {
        "status": "APPROVED",
        "approvedBy": str(dict(transaction.get("approval") or {}).get("approvedBy") or ""),
        "approvedAt": str(dict(transaction.get("approval") or {}).get("approvedAt") or ""),
        "semanticReviewRequired": True,
    }
    if isinstance(migration_plan, Mapping):
        approval["migrationPlanHash"] = str(migration_plan.get("migrationPlanHash") or "")
    manifest.update(
        {
            "version": VERSION,
            "changedPaths": _strings(paths),
            "pathModuleHints": _path_hints(program, paths),
            "approval": approval,
        }
    )
    report = dict(
        build_completeness_report(
            manifest,
            repository,
            changed_paths_override=paths,
        )
    )
    changed = set(_strings(paths))
    evidence: Dict[str, List[str]] = {}
    for request in program.get("codegenRequests") or []:
        module_id = str(request.get("moduleId") or "")
        owned = changed & set(_strings(request.get("allowedPaths") or []))
        if module_id in migration_targets:
            owned.update(migration_changed)
        evidence[module_id] = sorted(owned)
    missing = sorted(module_id for module_id, owned in evidence.items() if not owned)
    passed = bool(report.get("softGatePassed")) and not missing
    material = {
        "programHash": program.get("programHash"),
        "paths": _strings(paths),
        "evidence": evidence,
        "missing": missing,
    }
    report.update(
        {
            "schema": "self_update.completeness_gate_report.v1",
            "version": VERSION,
            "status": "PASS" if passed else "FAIL",
            "passed": passed,
            "implementationEvidence": evidence,
            "missingDirectImplementationEvidence": missing,
            "completenessGateHash": _hash(material),
        }
    )
    return report


def _contracts_at_ref(repository: Path, ref: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v2323-base-") as temp:
        worktree = Path(temp).resolve()
        completed = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), ref],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise PostCodegenGateError(
                "base_worktree_checkout_failed:"
                + (completed.stderr or completed.stdout).strip()
            )
        try:
            return build_module_contracts(worktree)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )


def _migration_snapshot_at_ref(
    repository: Path, ref: str, registry_paths: Sequence[str]
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v2323-migration-base-") as temp:
        worktree = Path(temp).resolve()
        completed = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), ref],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise PostCodegenGateError(
                "migration_base_worktree_checkout_failed:"
                + (completed.stderr or completed.stdout).strip()
            )
        try:
            return {
                "contracts": build_module_contracts(worktree),
                "manifestVerification": verify_committed_manifest(worktree),
                "registryFileHashes": registry_file_hashes(worktree, registry_paths),
            }
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )


def build_registry_module_receipt(
    program: Mapping[str, Any],
    test_report: Mapping[str, Any],
    completeness_report: Mapping[str, Any],
    *,
    base_ref: str,
    root: Path | None = None,
) -> Dict[str, Any]:
    repository = (root or repository_root()).resolve()
    migration_plan = program.get("registryMigrationPlan")
    migration_mode = isinstance(migration_plan, Mapping)

    registry_paths: List[str] = []
    migration_targets: List[str] = []
    plan: Dict[str, Any] | None = None
    if migration_mode:
        plan = require_registry_migration_plan(migration_plan)
        registry_paths = _strings(plan["allowedRegistryPaths"])
        migration_targets = _strings(plan["targetModules"])
        base_snapshot = _migration_snapshot_at_ref(repository, base_ref, registry_paths)
        before_set = dict(base_snapshot["contracts"])
        base_manifest = dict(base_snapshot["manifestVerification"])
        before_registry_hashes = dict(base_snapshot["registryFileHashes"])
    else:
        before_set = _contracts_at_ref(repository, base_ref)
        base_manifest = {}
        before_registry_hashes = {}

    after_set = build_module_contracts(repository)
    before = dict(before_set.get("moduleContracts") or {})
    after = dict(after_set.get("moduleContracts") or {})
    direct = _strings(program.get("directModules") or [])
    transitive = _strings(program.get("transitiveModules") or [])
    tested = {
        str(item.get("moduleId") or "")
        for item in dict(test_report.get("testPlan") or {}).get("modules") or []
        if item.get("matchedTests")
    }
    receipts: List[Dict[str, Any]] = []
    failures: List[str] = []
    for module_id in [*direct, *transitive]:
        old = str(dict(before.get(module_id) or {}).get("moduleContractHash") or "")
        new = str(dict(after.get(module_id) or {}).get("moduleContractHash") or "")
        editable = module_id in direct
        changed = bool(old and new and old != new)
        module_passed = bool(
            old
            and new
            and module_id in tested
            and test_report.get("passed") is True
            and (changed if editable else not changed)
        )
        if not module_passed:
            failures.append(module_id)
        receipts.append(
            {
                "moduleId": module_id,
                "editAllowed": editable,
                "moduleContractHashBefore": old,
                "moduleContractHashAfter": new,
                "moduleContractHashChanged": changed,
                "testVerified": module_id in tested,
                "status": "PASS" if module_passed else "FAIL",
            }
        )

    root_before = str(before_set.get("moduleContractRootHash") or "")
    root_after = str(after_set.get("moduleContractRootHash") or "")
    base_registry_root = str(before_set.get("registryRootHash") or "")
    head_registry_root = str(after_set.get("registryRootHash") or "")

    migration_evidence: Dict[str, Any] = {
        "migrationMode": migration_mode,
        "baseRegistryRootHash": base_registry_root,
        "headRegistryRootHash": head_registry_root,
        "registryRootChanged": bool(
            base_registry_root and head_registry_root and base_registry_root != head_registry_root
        ),
        "manifestVerified": None,
        "migrationPlanHash": None,
        "allowedRegistryPaths": registry_paths,
        "changedRegistryPaths": [],
        "registryFileHashChanges": {},
        "targetModules": migration_targets,
        "targetModulesChanged": [],
        "introducedTargetModules": [],
        "introducedTargetModuleChecks": {},
        "unexpectedChangedModules": [],
        "migrationErrors": [],
    }

    if migration_mode and plan is not None:
        head_manifest = verify_committed_manifest(repository)
        after_registry_hashes = registry_file_hashes(repository, registry_paths)
        file_changes = {
            path: {
                "before": before_registry_hashes.get(path),
                "after": after_registry_hashes.get(path),
                "changed": before_registry_hashes.get(path) != after_registry_hashes.get(path),
            }
            for path in registry_paths
        }
        changed_registry_paths = sorted(
            path for path, item in file_changes.items() if item["changed"]
        )
        target_changed = sorted(
            module_id
            for module_id in migration_targets
            if str(dict(before.get(module_id) or {}).get("moduleContractHash") or "")
            != str(dict(after.get(module_id) or {}).get("moduleContractHash") or "")
        )
        introduced_targets = sorted(
            module_id for module_id in migration_targets if module_id not in before
        )
        introduced_checks: Dict[str, Dict[str, Any]] = {}
        unsafe_introduced: List[str] = []
        missing_introduced: List[str] = []
        for module_id in introduced_targets:
            contract = dict(after.get(module_id) or {})
            definition = dict(contract.get("definition") or {})
            present = bool(contract.get("moduleContractHash"))
            registered_only = bool(
                definition.get("status") == "REGISTERED_ONLY"
                and definition.get("activationState") == "REGISTERED_ONLY"
                and definition.get("runtimeBindingEnabled") is False
                and list(definition.get("upstream") or []) == []
                and list(definition.get("downstream") or []) == []
            )
            introduced_checks[module_id] = {
                "presentAfterMigration": present,
                "status": definition.get("status"),
                "activationState": definition.get("activationState"),
                "runtimeBindingEnabled": definition.get("runtimeBindingEnabled"),
                "activeUpstream": list(definition.get("upstream") or []),
                "activeDownstream": list(definition.get("downstream") or []),
                "registeredOnlyBoundaryPassed": registered_only,
            }
            if not present:
                missing_introduced.append(module_id)
            elif not registered_only:
                unsafe_introduced.append(module_id)

        all_changed_modules = {
            module_id
            for module_id in set(before) | set(after)
            if str(dict(before.get(module_id) or {}).get("moduleContractHash") or "")
            != str(dict(after.get(module_id) or {}).get("moduleContractHash") or "")
        }
        allowed_changed_modules = set(direct) | set(migration_targets)
        unexpected_changed_modules = sorted(all_changed_modules - allowed_changed_modules)
        errors: List[str] = []
        if base_manifest.get("verified") is not True:
            errors.append("BASE_REGISTRY_MANIFEST_INVALID")
        if head_manifest.get("verified") is not True:
            errors.append("HEAD_REGISTRY_MANIFEST_INVALID")
        if base_registry_root != plan["baseRegistryRootHash"]:
            errors.append("STALE_APPROVAL:baseRegistryRootHash")
        if base_registry_root != program.get("baseRegistryRootHash"):
            errors.append("PROGRAM_BASE_REGISTRY_ROOT_MISMATCH")
        if not changed_registry_paths:
            errors.append("REGISTRY_ROOT_MIGRATION_EMPTY")
        if base_registry_root == head_registry_root:
            errors.append("REGISTRY_ROOT_UNCHANGED")
        missing_target_changes = sorted(set(migration_targets) - set(target_changed))
        if missing_target_changes:
            errors.extend(
                f"REGISTRY_TARGET_MODULE_UNCHANGED:{module_id}"
                for module_id in missing_target_changes
            )
        if missing_introduced:
            errors.extend(
                f"REGISTRY_INTRODUCED_MODULE_MISSING:{module_id}"
                for module_id in missing_introduced
            )
        if unsafe_introduced:
            errors.extend(
                f"REGISTRY_INTRODUCED_MODULE_NOT_REGISTERED_ONLY:{module_id}"
                for module_id in unsafe_introduced
            )
        if unexpected_changed_modules:
            errors.extend(
                f"UNAPPROVED_MODULE_CONTRACT_CHANGED:{module_id}"
                for module_id in unexpected_changed_modules
            )
        migration_evidence.update(
            {
                "manifestVerified": bool(
                    base_manifest.get("verified") is True
                    and head_manifest.get("verified") is True
                ),
                "migrationPlanHash": plan["migrationPlanHash"],
                "changedRegistryPaths": changed_registry_paths,
                "registryFileHashChanges": file_changes,
                "targetModulesChanged": target_changed,
                "introducedTargetModules": introduced_targets,
                "introducedTargetModuleChecks": introduced_checks,
                "unexpectedChangedModules": unexpected_changed_modules,
                "migrationErrors": errors,
            }
        )
        passed = bool(
            not failures
            and not errors
            and test_report.get("passed") is True
            and completeness_report.get("passed") is True
            and root_before
            and root_after
            and root_before != root_after
        )
    else:
        passed = bool(
            not failures
            and test_report.get("passed") is True
            and completeness_report.get("passed") is True
            and root_before
            and root_after
            and root_before != root_after
            and head_registry_root == program.get("registryRootHash")
        )

    material = {
        "programHash": program.get("programHash"),
        "baseRef": base_ref,
        "registryRootHash": head_registry_root,
        "moduleContractRootHashBefore": root_before,
        "moduleContractRootHashAfter": root_after,
        "moduleReceipts": receipts,
        "failedModules": failures,
        "migrationEvidence": migration_evidence,
        "testReportHash": test_report.get("testReportHash"),
        "completenessGateHash": completeness_report.get("completenessGateHash"),
    }
    return {
        "schema": "self_update.registry_module_receipt.v1",
        "version": VERSION,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        **material,
        **migration_evidence,
        "moduleContractRootHashChanged": bool(
            root_before and root_after and root_before != root_after
        ),
        "registryReceiptHash": _hash(material),
    }


def verify_post_codegen(
    transaction: Mapping[str, Any],
    program: Mapping[str, Any],
    *,
    base_ref: str,
    head_ref: str = "HEAD",
    root: Path | None = None,
    timeout_seconds: int = 1200,
) -> Dict[str, Any]:
    repository = (root or repository_root()).resolve()
    diff = git_changed_paths(repository, base_ref=base_ref, head_ref=head_ref)
    if diff.get("resolved") is not True:
        raise PostCodegenGateError(f"git_changed_paths_failed:{diff.get('error')}")
    paths = _strings(diff.get("paths") or [])
    if not paths:
        raise PostCodegenGateError("post_codegen_changed_paths_empty")
    scope = verify_changed_path_scope(_scope_program(program), paths, repository)
    test_plan = build_test_plan(program, repository, changed_paths=paths)
    tests = execute_test_plan(
        test_plan,
        repository,
        timeout_seconds=timeout_seconds,
    )
    completeness = build_completeness_gate_report(
        transaction,
        program,
        paths,
        repository,
    )
    receipt = build_registry_module_receipt(
        program,
        tests,
        completeness,
        base_ref=base_ref,
        root=repository,
    )
    passed = all(
        report.get("passed") is True
        for report in (scope, tests, completeness, receipt)
    )
    verified = copy.deepcopy(dict(transaction))
    if passed:
        next_state = "VERIFIED"
    elif scope.get("passed") is not True:
        next_state = "SCOPE_EXPANSION_REQUIRED"
    elif receipt.get("migrationMode") is True:
        next_state = "REGISTRY_MIGRATION_INVALID"
    else:
        next_state = "APPROVED"
    verified["state"] = next_state
    verified["programHash"] = program.get("programHash")
    verified["verifiedAt"] = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    verified["verification"] = {
        "status": "PASS" if passed else "FAIL",
        "scopeVerificationHash": scope.get("scopeVerificationHash"),
        "testReportHash": tests.get("testReportHash"),
        "completenessGateHash": completeness.get("completenessGateHash"),
        "registryReceiptHash": receipt.get("registryReceiptHash"),
    }
    return {
        "schema": "self_update.post_codegen_verification.v1",
        "version": VERSION,
        "passed": passed,
        "changedPaths": paths,
        "verifiedTransaction": verified,
        "scopeReport": scope,
        "testReport": tests,
        "completenessReport": completeness,
        "registryReceipt": receipt,
    }


def render_verification_summary(bundle: Mapping[str, Any]) -> str:
    tx = dict(bundle.get("verifiedTransaction") or {})
    receipt = dict(bundle.get("registryReceipt") or {})
    reports = [
        ("Patch Scope", bundle.get("scopeReport") or {}),
        ("Tests", bundle.get("testReport") or {}),
        ("Completeness", bundle.get("completenessReport") or {}),
        ("Registry / Module Receipt", receipt),
    ]
    lines = [
        f"# Post-Codegen 验证：{tx.get('requirementId')}",
        "",
        f"状态：`{tx.get('state')}`",
        "",
        "| 验证门 | 状态 |",
        "|---|---|",
    ]
    lines.extend(
        f"| {name} | {dict(report).get('status')} |"
        for name, report in reports
    )
    lines.extend(["", f"- programHash: `{tx.get('programHash')}`"])
    if receipt.get("migrationMode") is True:
        lines.extend(
            [
                f"- baseRegistryRootHash: `{receipt.get('baseRegistryRootHash')}`",
                f"- headRegistryRootHash: `{receipt.get('headRegistryRootHash')}`",
                f"- migrationPlanHash: `{receipt.get('migrationPlanHash')}`",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def persist_post_codegen_verification(
    bundle: Mapping[str, Any],
    output_directory: Path,
    root: Path | None = None,
) -> Dict[str, str]:
    repository = (root or repository_root()).resolve()
    target = (
        output_directory
        if output_directory.is_absolute()
        else repository / output_directory
    ).resolve()
    if target != repository and repository not in target.parents:
        raise PostCodegenGateError("post_codegen_output_outside_repository")
    target.mkdir(parents=True, exist_ok=True)
    files = {
        "verifiedTransaction": "transaction-verified.json",
        "scopeReport": "patch-scope-report.json",
        "testReport": "test-report.json",
        "completenessReport": "completeness-report.json",
        "registryReceipt": "registry-receipt.json",
    }
    result: Dict[str, str] = {}
    for key, name in files.items():
        path = target / name
        _write(path, dict(bundle.get(key) or {}))
        result[key] = path.relative_to(repository).as_posix()
    summary = target / "verification-summary.md"
    summary.write_text(render_verification_summary(bundle), encoding="utf-8")
    result["verificationSummary"] = summary.relative_to(repository).as_posix()
    return result


__all__ = [
    "PostCodegenGateError",
    "build_test_plan",
    "execute_test_plan",
    "build_completeness_gate_report",
    "build_registry_module_receipt",
    "verify_post_codegen",
    "persist_post_codegen_verification",
    "render_verification_summary",
]
