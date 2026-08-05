"""Fresh approval runner for the V23.2 repository self-update loop."""
from __future__ import annotations

import json
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping

from .change_program import approve_transaction, compile_change_program, persist_change_program
from .compile_registry import sha256_value
from .registry_migration import validate_registry_migration_plan
from .requirement_resolver import SELF_UPDATE_VERSION, persist_resolution, resolve_requirement


class ApprovalRunnerError(RuntimeError):
    """Raised when a committed approval no longer matches repository truth."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ApprovalRunnerError(f"approval_json_read_failed:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise ApprovalRunnerError(f"approval_json_object_required:{path}")
    return value


@contextmanager
def _repository_at_ref(repository: Path, ref: str) -> Iterator[Path]:
    """Materialize one immutable base revision for migration compilation."""

    with tempfile.TemporaryDirectory(prefix="v2323-approval-base-") as temp:
        worktree = Path(temp).resolve()
        completed = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), str(ref)],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ApprovalRunnerError(
                "approval_base_worktree_failed:"
                + (completed.stderr or completed.stdout).strip()
            )
        try:
            yield worktree
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )


def validate_approval_descriptor(approval: Mapping[str, Any]) -> Dict[str, Any]:
    errors: list[str] = []
    if approval.get("schema") != "self_update.approval.v1":
        errors.append("approval_schema_invalid")
    if str(approval.get("version") or "") != SELF_UPDATE_VERSION:
        errors.append("approval_version_invalid")
    requirement_path = str(approval.get("requirementPath") or "").strip().replace("\\", "/")
    if not requirement_path.startswith("contracts/requirements/") or not requirement_path.endswith(".json"):
        errors.append("approval_requirement_path_invalid")
    for key in (
        "approvedBy",
        "approvedAt",
        "approvedRequirementIrHash",
        "approvedImpactHash",
        "approvedRegistryRootHash",
    ):
        if not str(approval.get(key) or "").strip():
            errors.append(f"approval_field_required:{key}")
    impact_bundle_hash = str(approval.get("approvedImpactBundleHash") or "").strip()
    if impact_bundle_hash and not impact_bundle_hash.startswith("sha256:"):
        errors.append("approval_impact_bundle_hash_invalid")

    migration_plan = None
    raw_plan = approval.get("registryMigrationPlan")
    if raw_plan is not None:
        validation = validate_registry_migration_plan(raw_plan if isinstance(raw_plan, Mapping) else {})
        if validation["valid"] is not True:
            errors.extend(validation["errors"])
        else:
            migration_plan = dict(validation["plan"])
            approved_root = str(approval.get("approvedRegistryRootHash") or "").strip()
            if approved_root != migration_plan["baseRegistryRootHash"]:
                errors.append("registry_migration_base_root_approval_mismatch")

    normalized: Dict[str, Any] = {
        "schema": "self_update.approval.v1",
        "version": SELF_UPDATE_VERSION,
        "requirementPath": requirement_path,
        "approvedBy": str(approval.get("approvedBy") or "").strip(),
        "approvedAt": str(approval.get("approvedAt") or "").strip(),
        "approvedRequirementIrHash": str(approval.get("approvedRequirementIrHash") or "").strip(),
        "approvedImpactHash": str(approval.get("approvedImpactHash") or "").strip(),
        "approvedRegistryRootHash": str(approval.get("approvedRegistryRootHash") or "").strip(),
    }
    if impact_bundle_hash:
        normalized["approvedImpactBundleHash"] = impact_bundle_hash
    if migration_plan is not None:
        normalized["registryMigrationPlan"] = migration_plan
    return {
        "schema": "self_update.approval.validation.v1",
        "version": SELF_UPDATE_VERSION,
        "valid": not errors,
        "errors": errors,
        "approval": normalized,
    }


def _approval_identity_against_repository(
    *,
    normalized: Mapping[str, Any],
    requirement: Mapping[str, Any],
    compilation_root: Path,
    base_ref: str | None,
) -> Dict[str, Any]:
    transaction = resolve_requirement(requirement, compilation_root)
    from tools.self_update.impact_bundle import build_impact_bundle

    impact_bundle = build_impact_bundle(requirement, compilation_root)
    expected = {
        "approvedRequirementIrHash": str(transaction.get("requirementIrHash") or ""),
        "approvedImpactHash": str(transaction.get("impactHash") or ""),
        "approvedRegistryRootHash": str(transaction.get("registryRootHash") or ""),
        "approvedImpactBundleHash": str(impact_bundle.get("impactBundleHash") or ""),
    }
    supplied = {
        key: str(normalized.get(key) or "")
        for key in expected
    }
    matches = {key: supplied[key] == value for key, value in expected.items()}
    material = {
        "requirementPath": normalized.get("requirementPath"),
        "baseRef": str(base_ref or ""),
        "migrationMode": isinstance(normalized.get("registryMigrationPlan"), Mapping),
        "expected": expected,
        "supplied": supplied,
        "matches": matches,
        "transactionState": transaction.get("state"),
        "impactBundleState": impact_bundle.get("state"),
    }
    return {
        "schema": "self_update.approval_identity_plan.v1",
        "version": SELF_UPDATE_VERSION,
        **material,
        "approvalIdentityPlanHash": sha256_value(material),
        "readyForApprovalRefresh": bool(
            transaction.get("state") == "WAITING_FOR_USER_APPROVAL"
            and impact_bundle.get("state") == "RESOLVED"
        ),
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }


def resolve_approval_identity(
    approval: Mapping[str, Any],
    root: Path | None = None,
    *,
    base_ref: str | None = None,
) -> Dict[str, Any]:
    """Emit the exact hashes an approval must bind before compilation.

    A registry migration is resolved against the immutable PR base revision while the
    Requirement IR is read from the current branch. This lets CI publish fresh approval
    identities even when a committed descriptor is intentionally stale and blocked.
    """

    repository = (root or repository_root()).resolve()
    validation = validate_approval_descriptor(approval)
    if validation["valid"] is not True:
        raise ApprovalRunnerError("approval_invalid:" + ",".join(validation["errors"]))
    normalized = dict(validation["approval"])
    requirement_path = (repository / normalized["requirementPath"]).resolve()
    if repository not in requirement_path.parents:
        raise ApprovalRunnerError("approval_requirement_outside_repository")
    requirement = _read_object(requirement_path)

    migration_plan = normalized.get("registryMigrationPlan")
    if isinstance(migration_plan, Mapping) and base_ref:
        with _repository_at_ref(repository, base_ref) as base_repository:
            return _approval_identity_against_repository(
                normalized=normalized,
                requirement=requirement,
                compilation_root=base_repository,
                base_ref=base_ref,
            )

    return _approval_identity_against_repository(
        normalized=normalized,
        requirement=requirement,
        compilation_root=repository,
        base_ref=None,
    )


def persist_approval_identity(
    plan: Mapping[str, Any], output_directory: Path, root: Path | None = None
) -> str:
    repository = (root or repository_root()).resolve()
    target = output_directory if output_directory.is_absolute() else repository / output_directory
    target = target.resolve()
    if target != repository and repository not in target.parents:
        raise ApprovalRunnerError("approval_identity_output_outside_repository")
    target.mkdir(parents=True, exist_ok=True)
    path = target / "approval-identity-plan.json"
    path.write_text(
        json.dumps(dict(plan), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path.relative_to(repository).as_posix()


def _compile_against_repository(
    *,
    normalized: Mapping[str, Any],
    requirement: Mapping[str, Any],
    compilation_root: Path,
) -> Dict[str, Any]:
    transaction = resolve_requirement(requirement, compilation_root)
    if transaction.get("state") != "WAITING_FOR_USER_APPROVAL":
        raise ApprovalRunnerError(
            f"approval_requirement_not_approvable:{transaction.get('state')}"
        )
    expected = {
        "approvedRequirementIrHash": transaction.get("requirementIrHash"),
        "approvedImpactHash": transaction.get("impactHash"),
        "approvedRegistryRootHash": transaction.get("registryRootHash"),
    }
    for key, value in expected.items():
        if normalized.get(key) != value:
            raise ApprovalRunnerError(f"STALE_APPROVAL:{key}")

    approved_impact_bundle_hash = str(
        normalized.get("approvedImpactBundleHash") or ""
    ).strip()
    impact_bundle = None
    if approved_impact_bundle_hash:
        # Lazy import avoids changing the V23.1 bootstrap path for historical descriptors.
        from tools.self_update.impact_bundle import build_impact_bundle

        impact_bundle = build_impact_bundle(requirement, compilation_root)
        if impact_bundle.get("state") != "RESOLVED":
            raise ApprovalRunnerError(
                f"approval_impact_bundle_not_resolved:{impact_bundle.get('state')}"
            )
        if impact_bundle.get("impactBundleHash") != approved_impact_bundle_hash:
            raise ApprovalRunnerError("STALE_APPROVAL:approvedImpactBundleHash")

    approved_transaction = approve_transaction(
        transaction,
        approved_by=str(normalized["approvedBy"]),
        approved_at=str(normalized["approvedAt"]),
    )
    if approved_impact_bundle_hash:
        approved_transaction["approvedImpactBundleHash"] = approved_impact_bundle_hash

    migration_plan = normalized.get("registryMigrationPlan")
    if isinstance(migration_plan, Mapping):
        plan = dict(migration_plan)
        approved_transaction["registryMigrationPlan"] = plan
        approved_transaction["approval"]["registryMigrationPlan"] = plan
        approved_transaction["approvalHash"] = sha256_value(
            approved_transaction["approval"]
        )

    program = compile_change_program(approved_transaction, compilation_root)
    return {
        "schema": "self_update.approved_compilation.v1",
        "version": SELF_UPDATE_VERSION,
        "approval": dict(normalized),
        "impactBundle": impact_bundle,
        "transaction": approved_transaction,
        "program": program,
    }


def compile_approved_requirement(
    approval: Mapping[str, Any],
    root: Path | None = None,
    *,
    base_ref: str | None = None,
) -> Dict[str, Any]:
    repository = (root or repository_root()).resolve()
    validation = validate_approval_descriptor(approval)
    if validation["valid"] is not True:
        raise ApprovalRunnerError("approval_invalid:" + ",".join(validation["errors"]))
    normalized = dict(validation["approval"])
    requirement_path = (repository / normalized["requirementPath"]).resolve()
    if repository not in requirement_path.parents:
        raise ApprovalRunnerError("approval_requirement_outside_repository")
    requirement = _read_object(requirement_path)

    migration_plan = normalized.get("registryMigrationPlan")
    if isinstance(migration_plan, Mapping) and base_ref:
        with _repository_at_ref(repository, base_ref) as base_repository:
            return _compile_against_repository(
                normalized=normalized,
                requirement=requirement,
                compilation_root=base_repository,
            )

    return _compile_against_repository(
        normalized=normalized,
        requirement=requirement,
        compilation_root=repository,
    )


def persist_approved_compilation(
    bundle: Mapping[str, Any], output_directory: Path, root: Path | None = None
) -> Dict[str, Any]:
    repository = (root or repository_root()).resolve()
    target = output_directory if output_directory.is_absolute() else repository / output_directory
    target = target.resolve()
    if target != repository and repository not in target.parents:
        raise ApprovalRunnerError("approval_output_outside_repository")
    target.mkdir(parents=True, exist_ok=True)
    transaction = dict(bundle.get("transaction") or {})
    program = dict(bundle.get("program") or {})
    persist_resolution(transaction, target, repository)
    approved_path = target / "transaction-approved.json"
    approved_path.write_text(
        json.dumps(transaction, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    impact_bundle = bundle.get("impactBundle")
    impact_bundle_path = None
    if isinstance(impact_bundle, dict):
        impact_bundle_path = target / "impact-bundle-approved.json"
        impact_bundle_path.write_text(
            json.dumps(impact_bundle, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
    persisted_program = persist_change_program(program, target, repository)
    result = {
        "approvedTransaction": approved_path.relative_to(repository).as_posix(),
        **persisted_program,
    }
    if impact_bundle_path is not None:
        result["approvedImpactBundle"] = impact_bundle_path.relative_to(repository).as_posix()
    return result


__all__ = [
    "ApprovalRunnerError",
    "compile_approved_requirement",
    "persist_approved_compilation",
    "persist_approval_identity",
    "resolve_approval_identity",
    "validate_approval_descriptor",
]
