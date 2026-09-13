#!/usr/bin/env python3
"""Compile an exact V26 update plan from registry responsibility nodes.

This is the missing front half of the self-update framework:

    failure / requirement
        -> registry language (module ids)
        -> registered implementation paths / runners / contracts
        -> policy-bounded editable paths
        -> required verification gates

The compiler never searches by filename similarity and never expands a change beyond
registered module ownership plus explicit evidence paths that are admitted by the
selected update policy. If another file is needed, the update request or registry must
be changed first and the plan recompiled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "z.update_locator.plan.v1"
REQUEST_SCHEMA = "z.update_request.v1"


class UpdateLocatorError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise UpdateLocatorError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def text_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise UpdateLocatorError(f"STRING_LIST_REQUIRED:{field}")
    return sorted(set(value))


def policy_allows(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


def runner_path(runner: str) -> str | None:
    if not isinstance(runner, str) or ":" not in runner:
        return None
    module = runner.split(":", 1)[0].strip()
    if not module:
        return None
    return module.replace(".", "/") + ".py"


def related_modules(modules: dict[str, Any], selected: set[str]) -> list[dict[str, Any]]:
    selected_fields: set[str] = set()
    selected_schemas: set[str] = set()
    selected_paths: set[str] = set()
    for module_id in selected:
        module = modules[module_id]
        selected_fields.update(text_list(module.get("fieldIds"), f"{module_id}.fieldIds"))
        selected_schemas.update(text_list(module.get("schemaIds"), f"{module_id}.schemaIds"))
        selected_paths.update(text_list(module.get("implementationPaths"), f"{module_id}.implementationPaths"))

    result: list[dict[str, Any]] = []
    for module_id, module in sorted(modules.items()):
        if module_id in selected:
            continue
        fields = set(text_list(module.get("fieldIds"), f"{module_id}.fieldIds"))
        schemas = set(text_list(module.get("schemaIds"), f"{module_id}.schemaIds"))
        paths = set(text_list(module.get("implementationPaths"), f"{module_id}.implementationPaths"))
        shared_fields = sorted(fields & selected_fields)
        shared_schemas = sorted(schemas & selected_schemas)
        shared_paths = sorted(paths & selected_paths)
        if shared_fields or shared_schemas or shared_paths:
            result.append(
                {
                    "moduleId": module_id,
                    "sharedFieldIds": shared_fields,
                    "sharedSchemaIds": shared_schemas,
                    "sharedImplementationPaths": shared_paths,
                }
            )
    return result


def compile_plan(
    *,
    registry: dict[str, Any],
    policy: dict[str, Any],
    request: dict[str, Any],
    profile_name: str | None = None,
) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        raise UpdateLocatorError("UPDATE_REQUEST_SCHEMA_MISMATCH")
    modules = registry.get("modules")
    if not isinstance(modules, dict) or not modules:
        raise UpdateLocatorError("REGISTRY_MODULES_REQUIRED")

    profiles = policy.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise UpdateLocatorError("POLICY_PROFILES_REQUIRED")
    selected_profile = profile_name or request.get("policyProfile") or policy.get("defaultProfile")
    if selected_profile not in profiles:
        raise UpdateLocatorError(f"POLICY_PROFILE_UNKNOWN:{selected_profile}")
    profile = profiles[selected_profile]
    if not isinstance(profile, dict):
        raise UpdateLocatorError("POLICY_PROFILE_OBJECT_REQUIRED")

    allowed_modules = set(text_list(profile.get("allowedRegistryModules"), "allowedRegistryModules"))
    prefixes = text_list(profile.get("allowedRuntimePathPrefixes"), "allowedRuntimePathPrefixes")
    targets = request.get("targets")
    if not isinstance(targets, list) or not targets:
        raise UpdateLocatorError("UPDATE_TARGETS_REQUIRED")

    selected_modules: set[str] = set()
    evidence_paths: set[str] = set()
    target_records: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise UpdateLocatorError(f"UPDATE_TARGET_OBJECT_REQUIRED:{index}")
        module_ids = text_list(target.get("registryModules"), f"targets[{index}].registryModules")
        if not module_ids:
            raise UpdateLocatorError(f"TARGET_MODULE_REQUIRED:{index}")
        unknown = sorted(module_id for module_id in module_ids if module_id not in modules)
        if unknown:
            raise UpdateLocatorError("REGISTRY_MODULE_UNKNOWN:" + ",".join(unknown))
        forbidden_modules = sorted(module_id for module_id in module_ids if module_id not in allowed_modules)
        if forbidden_modules:
            raise UpdateLocatorError("REGISTRY_MODULE_NOT_ALLOWED:" + ",".join(forbidden_modules))
        selected_modules.update(module_ids)

        target_evidence = text_list(target.get("evidencePaths"), f"targets[{index}].evidencePaths")
        denied_evidence = sorted(path for path in target_evidence if not policy_allows(path, prefixes))
        if denied_evidence:
            raise UpdateLocatorError("EVIDENCE_PATH_NOT_ALLOWED:" + ",".join(denied_evidence))
        evidence_paths.update(target_evidence)
        target_records.append(
            {
                "targetId": str(target.get("targetId") or f"target-{index+1}"),
                "failureClass": str(target.get("failureClass") or "UNSPECIFIED"),
                "registryModules": module_ids,
                "evidencePaths": target_evidence,
            }
        )

    module_records: list[dict[str, Any]] = []
    registered_paths: set[str] = set()
    runners: set[str] = set()
    schema_ids: set[str] = set()
    field_ids: set[str] = set()
    for module_id in sorted(selected_modules):
        module = modules[module_id]
        paths = text_list(module.get("implementationPaths"), f"{module_id}.implementationPaths")
        registered_paths.update(paths)
        schema_ids.update(text_list(module.get("schemaIds"), f"{module_id}.schemaIds"))
        field_ids.update(text_list(module.get("fieldIds"), f"{module_id}.fieldIds"))
        runner = str(module.get("runner") or "")
        if runner:
            runners.add(runner)
            resolved = runner_path(runner)
            if resolved:
                registered_paths.add(resolved)
        module_records.append(
            {
                "moduleId": module_id,
                "runner": runner or None,
                "implementationPaths": paths,
                "fieldIds": text_list(module.get("fieldIds"), f"{module_id}.fieldIds"),
                "schemaIds": text_list(module.get("schemaIds"), f"{module_id}.schemaIds"),
            }
        )

    editable_registered = sorted(path for path in registered_paths if policy_allows(path, prefixes))
    denied_registered = sorted(path for path in registered_paths if not policy_allows(path, prefixes))
    editable_paths = sorted(set(editable_registered) | evidence_paths)

    workflow_gates = sorted(
        path for path in prefixes if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
    )
    if any(module.startswith("agent") or module in {"task_mapping", "task_pool"} for module in selected_modules):
        candidate = ".github/workflows/v269-a-three-report-candidate.yml"
        if policy_allows(candidate, prefixes) and candidate not in workflow_gates:
            workflow_gates.append(candidate)
            workflow_gates.sort()

    material = {
        "schema": SCHEMA,
        "requestId": str(request.get("requestId") or ""),
        "policyProfile": selected_profile,
        "registryVersion": registry.get("version"),
        "registryRootHash": registry.get("registryRootHash"),
        "targets": target_records,
        "selectedModules": module_records,
        "editablePaths": editable_paths,
        "deniedRegisteredPaths": denied_registered,
        "relatedModules": related_modules(modules, selected_modules),
        "requiredGates": workflow_gates,
        "runners": sorted(runners),
        "fieldIds": sorted(field_ids),
        "schemaIds": sorted(schema_ids),
        "rules": {
            "filenameSimilaritySearchAllowed": False,
            "unplannedFileMutationAllowed": False,
            "scopeExpansionRequiresRecompile": True,
            "registryIsFileAuthority": True,
            "policyIsMutationBoundary": True,
        },
    }
    return {**material, "planHash": digest(material)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="config/v23_registry_runtime.json")
    parser.add_argument("--policy", default="governance/v26-field-authority-update-policy.json")
    parser.add_argument("--request", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry = read_object(Path(args.registry))
    policy = read_object(Path(args.policy))
    request = read_object(Path(args.request))
    plan = compile_plan(
        registry=registry,
        policy=policy,
        request=request,
        profile_name=args.profile,
    )
    write_json(Path(args.output), plan)
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
