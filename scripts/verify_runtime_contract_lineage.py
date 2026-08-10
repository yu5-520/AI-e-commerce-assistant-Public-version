#!/usr/bin/env python3
"""Verify unified field/interface ownership without importing business runtime.

Stdlib only. Intended for ECS/CI environments and deliberately does not create,
activate or install a virtual environment. Canonical ownership may be declared at
module level for semantic fields or at module:symbol level for executable interfaces.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, Iterable

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config/runtime_contract_lineage_registry_v1.json"
PROJECTION = ROOT / "config/v23_registry_runtime.json"


class VerificationError(RuntimeError):
    pass


def read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VerificationError(f"json_object_required:{path}")
    return value


def module_path(module: str) -> Path:
    path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
    if not path.is_file():
        raise VerificationError(f"owner_module_missing:{module}:{path}")
    return path


def owner_parts(owner: str) -> tuple[str, str]:
    raw = str(owner or "").strip()
    if not raw:
        raise VerificationError(f"owner_invalid:{owner}")
    module, sep, symbol = raw.partition(":")
    if not module:
        raise VerificationError(f"owner_invalid:{owner}")
    if sep and not symbol:
        raise VerificationError(f"owner_symbol_empty:{owner}")
    return module, symbol if sep else ""


def top_level_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    result.add(target.id)
    return result


def function_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = int(node.lineno) - 1
            end = int(getattr(node, "end_lineno", node.lineno))
            return "\n".join(lines[start:end])
    raise VerificationError(f"function_missing:{path}:{name}")


def assert_owner(owner: str) -> None:
    module, symbol = owner_parts(owner)
    path = module_path(module)
    if symbol and symbol not in top_level_symbols(path):
        raise VerificationError(f"owner_symbol_missing:{owner}")


def iter_registry_owners(registry: Dict[str, Any]) -> Iterable[str]:
    for section in ("fields", "interfaces"):
        values = registry.get(section) or {}
        if not isinstance(values, dict):
            raise VerificationError(f"registry_section_invalid:{section}")
        for record in values.values():
            if isinstance(record, dict) and record.get("owner"):
                yield str(record["owner"])


def verify() -> Dict[str, Any]:
    registry = read_object(REGISTRY)
    projection = read_object(PROJECTION)
    findings: list[str] = []

    if registry.get("schema") != "runtime.contract_lineage.registry.v1":
        findings.append("contract_lineage_registry_schema_invalid")
    if registry.get("mode") != "fail_closed":
        findings.append("contract_lineage_registry_not_fail_closed")
    if projection.get("runtimeContractLineageRegistryPath") != REGISTRY.relative_to(ROOT).as_posix():
        findings.append("runtime_projection_contract_lineage_registry_path_mismatch")
    if projection.get("runtimeContractLineageRegistryVersion") != registry.get("version"):
        findings.append("runtime_projection_contract_lineage_registry_version_mismatch")
    if projection.get("agent3SystemConstraintVersion") != "23.2.18":
        findings.append("agent3_system_constraint_registry_version_stale")

    for owner in sorted(set(iter_registry_owners(registry))):
        try:
            assert_owner(owner)
        except Exception as exc:
            findings.append(str(exc))

    guard_path = ROOT / "src/services/runtime_contract_guard_v1_service.py"
    try:
        strict = function_source(guard_path, "strict_descriptor_for_raw")
        if "itemExecutionId" not in strict or "inputContentHash" not in strict:
            findings.append("strict_hash_matcher_missing_exact_identity_fields")
        if "storeId" in strict or "productId" in strict:
            findings.append("strict_hash_matcher_contains_product_store_fallback")
    except Exception as exc:
        findings.append(str(exc))

    facade = (ROOT / "src/services/agent_token_runtime_v225_service.py").read_text(encoding="utf-8")
    guard_pos = facade.find("install_runtime_contract_guards()")
    agent3_import_pos = facade.find("agent3_runtime_v23215_service")
    if guard_pos < 0:
        findings.append("active_token_facade_runtime_guard_missing")
    if agent3_import_pos >= 0 and (guard_pos < 0 or guard_pos > agent3_import_pos):
        findings.append("runtime_guard_must_install_before_agent3_runtime_import")
    if "install_agent3_semantic_path_repair" not in facade:
        findings.append("active_token_facade_semantic_path_repair_missing")
    if "core.AGENT3_SOP_CORE_VERSION = AGENT3_SEMANTIC_PATH_REPAIR_VERSION" not in facade:
        findings.append("agent3_execution_identity_not_rotated_for_repair_contract")

    repair_path = ROOT / "src/services/agent3_semantic_path_repair_v1_service.py"
    repair_text = repair_path.read_text(encoding="utf-8")
    for required in (
        "agent3_sop_cross_family_contamination:",
        "agent3_system_fact_converted_to_action:",
        "core._normalize_sop",
        "operatorActionStepsGeneratedBySystem",
        "sameValidatorReexecuted",
    ):
        if required not in repair_text:
            findings.append(f"semantic_path_repair_contract_missing:{required}")

    contract_text = (ROOT / "src/services/agent_runtime_contract_v225_service.py").read_text(encoding="utf-8")
    for required in (
        "providerDeclaredStatus",
        "systemContractViolations",
        "pipelineAdmissionErrors",
        "agent2DraftExecutionProof.alias_conflict",
        "agent3Sop.actionFamily_matches_lockedActionFamily",
    ):
        if required not in contract_text:
            findings.append(f"completed_contract_canonical_field_missing:{required}")

    transport_text = (ROOT / "src/services/artifact_transport_service.py").read_text(encoding="utf-8")
    for required in (
        '"agent2_draft_ready": "agent2DraftRef"',
        '"agent3_sop_running": "agent3SopRuntimeReceiptRef"',
        '"agent3_sop_ready": "agent3SopRef"',
        '"agent3_sop_output_invalid": "agent3SopFailureRef"',
        '"agent3_sop_failed": "agent3SopFailureRef"',
    ):
        if required not in transport_text:
            findings.append(f"artifact_stage_ref_missing:{required}")

    owner = (
        "src.services.hash_directed_artifact_runtime_v2259_service:"
        "ensure_hash_directed_runtime_tables"
    )
    interface = (registry.get("interfaces") or {}).get("runtime.hash.ensure_tables") or {}
    if interface.get("owner") != owner:
        findings.append("hash_table_interface_owner_mismatch")

    repaired_files = [
        "config/runtime_contract_lineage_registry_v1.json",
        "config/v23_registry_runtime.json",
        "src/services/runtime_contract_guard_v1_service.py",
        "src/services/agent3_semantic_path_repair_v1_service.py",
        "src/services/agent_token_runtime_v225_service.py",
        "src/services/agent_runtime_contract_v225_service.py",
        "src/services/artifact_transport_service.py",
    ]
    missing_files = [path for path in repaired_files if not (ROOT / path).is_file()]
    findings.extend(f"repair_file_missing:{path}" for path in missing_files)

    report = {
        "schema": "runtime.contract_lineage.verification.v1",
        "version": registry.get("version"),
        "verified": not findings,
        "fieldCount": len(registry.get("fields") or {}),
        "interfaceCount": len(registry.get("interfaces") or {}),
        "repairClassCount": len(registry.get("repairClasses") or {}),
        "repairedFiles": repaired_files,
        "findings": findings,
        "virtualEnvironmentRequired": False,
    }
    return report


def main() -> int:
    report = verify()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
