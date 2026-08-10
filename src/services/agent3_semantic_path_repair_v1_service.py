"""Agent3 exact-JSON-path semantic contract repair.

The system validator remains the authority.  This repair layer only runs when that
validator reports machine-readable semantic violations and it may mutate only the
exact offending paths.  ``executionSteps`` stays authoritative; generated
``operatorActionSteps`` is never directly repaired.
"""
from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Dict, Iterable, List, Tuple

from src.services import agent3_sop_core_v225_service as core
from src.services.llm_gateway_v196_service import call_json

AGENT3_SEMANTIC_PATH_REPAIR_VERSION = "23.2.19"
_REPAIR_PREFIXES = (
    "agent3_sop_cross_family_contamination:",
    "agent3_system_fact_converted_to_action:",
)
_ALLOWED_ROOTS = {
    "executionSteps",
    "decisionBranches",
    "submissionEvidence",
    "stopConditions",
    "rollbackConditions",
    "crossDepartmentActions",
}
_PATH_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split())[:limit]


def classify_contract_violations(values: Any) -> List[str]:
    classes: List[str] = []
    for raw in _arr(values):
        value = _text(raw, 4000)
        if value.startswith("agent3_sop_cross_family_contamination:"):
            classes.append("cross_family_contamination")
        elif value.startswith("agent3_system_fact_converted_to_action:"):
            classes.append("system_fact_converted_to_action")
    return list(dict.fromkeys(classes))


def _tokens(path: str) -> List[Any]:
    if not path.startswith("$."):
        raise ValueError(f"agent3_semantic_repair_path_invalid:{path}")
    result: List[Any] = []
    for name, index in _PATH_TOKEN_RE.findall(path[1:]):
        result.append(name if name else int(index))
    if not result:
        raise ValueError(f"agent3_semantic_repair_path_empty:{path}")
    return result


def _root(path: str) -> str:
    values = _tokens(path)
    return str(values[0]) if values else ""


def _get(value: Any, path: str) -> Any:
    current = value
    for token in _tokens(path):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                raise KeyError(path)
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                raise KeyError(path)
            current = current[token]
    return current


def _set(value: Any, path: str, replacement: Any) -> None:
    tokens = _tokens(path)
    current = value
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                raise KeyError(path)
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                raise KeyError(path)
            current = current[token]
    final = tokens[-1]
    if isinstance(final, int):
        if not isinstance(current, list) or final >= len(current):
            raise KeyError(path)
        current[final] = replacement
    else:
        if not isinstance(current, dict) or final not in current:
            raise KeyError(path)
        current[final] = replacement


def _operator_projection_source(path: str) -> str | None:
    match = re.fullmatch(r"\$\.operatorActionSteps\[([0-9]+)\]", path)
    if not match:
        return None
    return f"$.executionSteps[{int(match.group(1))}].instruction"


def extract_repairable_paths(values: Any) -> List[str]:
    """Extract validator-declared paths and map derived operator projection paths."""

    result: List[str] = []
    for raw in _arr(values):
        text = _text(raw, 12000)
        prefix = next((item for item in _REPAIR_PREFIXES if text.startswith(item)), None)
        if not prefix:
            continue
        suffix = text[len(prefix) :]
        for path in (item.strip() for item in suffix.split(",")):
            if not path.startswith("$."):
                continue
            projected = _operator_projection_source(path)
            canonical = projected or path
            try:
                root = _root(canonical)
            except ValueError:
                continue
            if root not in _ALLOWED_ROOTS:
                continue
            if canonical not in result:
                result.append(canonical)
    return result


def _repair_payload(
    package: Dict[str, Any],
    sop: Dict[str, Any],
    violations: List[str],
    paths: List[str],
) -> Dict[str, Any]:
    compiled = core.compile_agent3_provider_package(package)
    current_values: Dict[str, Any] = {}
    for path in paths:
        try:
            current_values[path] = _get(sop, path)
        except KeyError:
            current_values[path] = None
    return {
        "version": AGENT3_SEMANTIC_PATH_REPAIR_VERSION,
        "repairType": "agent3_exact_json_path_semantic_repair",
        "packageId": package.get("packageId") or package.get("itemId"),
        "productId": package.get("productId"),
        "storeId": package.get("storeId"),
        "lockedActionFamily": compiled.get("lockedActionFamily"),
        "allowedActionTypes": compiled.get("allowedActionTypes") or [],
        "requiredActionTypeGroups": compiled.get("requiredActionTypeGroups") or [],
        "forbiddenActions": compiled.get("forbiddenActions") or [],
        "constraints": compiled.get("constraints") or {},
        "systemCompletedFacts": compiled.get("systemCompletedFacts") or {},
        "systemConstraintContract": compiled.get("systemConstraintContract") or {},
        "validatorViolations": violations,
        "allowedRepairPaths": paths,
        "currentValues": current_values,
        "immutableContract": {
            "onlyAllowedRepairPathsMayChange": True,
            "operatorActionStepsGeneratedBySystem": True,
            "actionFamilyMayNotChange": True,
            "packageIdentityMayNotChange": True,
            "noNewExecutionObjectIds": True,
            "validatorMustPassAfterRepair": True,
        },
    }


def _repair_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    prompt = (
        "你是Agent3字段级语义修复器。系统Validator已经给出精确非法JSON路径。"
        "只能修复allowedRepairPaths列出的路径，禁止新增路径、禁止修改动作族、商品/店铺/package身份，"
        "禁止输出operatorActionSteps；该字段由系统从executionSteps[*].instruction确定性生成。"
        "lockedActionFamily是唯一动作族真值。删除跨动作域内容，并删除把systemCompletedFacts重新写成人工动作的内容。"
        "必须保留当前业务目标，只把非法字段改写为属于lockedActionFamily且可执行的内容。"
        "只返回严格JSON对象：{\"repair\":{\"packageId\":\"...\",\"patches\":[{\"path\":\"$.x\",\"value\":...}]}}。"
    )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        },
    ]


def _apply_exact_patches(
    sop: Dict[str, Any],
    repair_payload: Dict[str, Any],
    *,
    package_id: str,
    allowed_paths: List[str],
) -> Tuple[Dict[str, Any], List[str]]:
    repair = _dict(repair_payload.get("repair"))
    if _text(repair.get("packageId"), 220) != _text(package_id, 220):
        raise ValueError("agent3_semantic_repair_package_mismatch")
    patches = _arr(repair.get("patches"))
    if not patches:
        raise ValueError("agent3_semantic_repair_patches_missing")
    allowed = set(allowed_paths)
    seen: set[str] = set()
    patched = copy.deepcopy(sop)
    applied: List[str] = []
    for raw in patches:
        item = _dict(raw)
        path = _text(item.get("path"), 1000)
        if not path or path not in allowed:
            raise ValueError(f"agent3_semantic_repair_path_not_allowed:{path}")
        if path in seen:
            raise ValueError(f"agent3_semantic_repair_duplicate_path:{path}")
        seen.add(path)
        if _root(path) == "operatorActionSteps":
            raise ValueError("agent3_semantic_repair_operator_projection_forbidden")
        # The exact path must already exist.  This prevents the repair call from
        # broadening the SOP structure or inventing a new execution surface.
        _get(patched, path)
        _set(patched, path, item.get("value"))
        applied.append(path)
    if set(applied) != allowed:
        missing = sorted(allowed.difference(applied))
        raise ValueError(
            "agent3_semantic_repair_incomplete_patch_set:" + ",".join(missing[:12])
        )
    return patched, applied


def _provider_call_summary(usage: Dict[str, Any], package_id: str) -> Dict[str, Any]:
    return {
        "callType": "semantic_path_repair",
        "provider": usage.get("provider"),
        "model": usage.get("model"),
        "providerRequestId": usage.get("providerRequestId"),
        "providerCallExecuted": bool(usage.get("providerCallExecuted")),
        "actualCalls": 1 if usage.get("providerCallExecuted") else 0,
        "inputTokens": int(usage.get("input") or 0),
        "outputTokens": int(usage.get("output") or 0),
        "reasoningTokens": int(usage.get("reasoningTokens") or 0),
        "inputFingerprint": usage.get("inputFingerprint"),
        "projectionVersion": usage.get("projectionVersion"),
        "gatewayVersion": usage.get("gatewayVersion"),
        "batchSize": 1,
        "packageIds": [package_id],
    }


def repair_normalized_sop(
    *,
    data_version: str | None,
    package: Dict[str, Any],
    normalized: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    validation = _dict(normalized.get("contractValidation"))
    violations = [
        _text(item, 12000)
        for item in _arr(validation.get("missing"))
        if _text(item, 12000)
    ]
    classes = classify_contract_violations(violations)
    paths = extract_repairable_paths(violations)
    if not classes or not paths:
        return normalized, {
            "attempted": False,
            "applied": False,
            "reason": "no_exact_path_semantic_violation",
        }

    package_id = _text(package.get("packageId") or package.get("itemId"), 220)
    request_payload = _repair_payload(package, normalized, violations, paths)
    messages = _repair_messages(request_payload)
    response, usage = call_json(
        stage="task_mapping_agent",
        prompt_version=f"{core.AGENT3_SOP_CORE_VERSION}.semantic-path-repair.v1",
        messages=messages,
        temperature=0.05,
        timeout_seconds=int(os.getenv("AGENT3_SEMANTIC_PATH_REPAIR_TIMEOUT", "180")),
        cache_payload=request_payload,
        cache_enabled=False,
    )
    usage = _dict(usage)
    audit: Dict[str, Any] = {
        "version": AGENT3_SEMANTIC_PATH_REPAIR_VERSION,
        "attempted": True,
        "applied": False,
        "maxAttempts": 1,
        "failureClasses": classes,
        "originalViolations": violations,
        "requestedPaths": paths,
        "providerRequestId": usage.get("providerRequestId"),
        "providerCallExecuted": bool(usage.get("providerCallExecuted")),
        "fallbackAllowed": False,
    }
    if not usage.get("providerCallExecuted") or not usage.get("providerRequestId"):
        audit["failureCode"] = "agent3_semantic_path_repair_provider_proof_invalid"
        return normalized, {**audit, "providerCall": _provider_call_summary(usage, package_id)}

    try:
        patched, applied = _apply_exact_patches(
            normalized,
            _dict(response),
            package_id=package_id,
            allowed_paths=paths,
        )
    except Exception as exc:
        audit["failureCode"] = str(exc)[:600]
        return normalized, {**audit, "providerCall": _provider_call_summary(usage, package_id)}

    # Re-run from the provider-declared/evaluated status instead of carrying the
    # previous system downgrade (sop_missing_data) into validation.
    provider_declared_status = _text(
        normalized.get("providerDeclaredStatus")
        or validation.get("evaluatedStatus")
        or normalized.get("sopStatus"),
        100,
    )
    patched["sopStatus"] = provider_declared_status
    patched["semanticContractMissing"] = []
    patched.pop("contractValidation", None)
    proof = _dict(normalized.get("agent3ExecutionProof"))
    repaired = core._normalize_sop(patched, package, proof)
    final_validation = _dict(repaired.get("contractValidation"))
    final_violations = _arr(final_validation.get("missing"))
    passed = bool(
        final_validation.get("passed") is True
        and repaired.get("sopStatus") in {core.SOP_READY, core.SOP_REQUIRES_APPROVAL}
    )
    audit.update(
        applied=passed,
        repairedPaths=applied,
        immutablePathPolicyPassed=True,
        finalViolations=final_violations,
        providerDeclaredStatus=provider_declared_status,
        sameValidatorReexecuted=True,
    )
    repaired.update(
        providerDeclaredStatus=provider_declared_status,
        systemContractViolations=final_violations,
        systemContractPassed=bool(final_validation.get("passed") is True),
        agent3SemanticPathRepair=audit,
        agent3ApiCallCount=int(normalized.get("agent3ApiCallCount") or 1) + 1,
        agent3SemanticCacheSeedEligible=False,
        fallbackAllowed=False,
    )
    return (repaired if passed else normalized), {
        **audit,
        "providerCall": _provider_call_summary(usage, package_id),
    }


def install_agent3_semantic_path_repair(runtime_module: Any) -> Dict[str, Any]:
    """Patch the active Agent3 provider boundary before Artifact acceptance.

    ``run_agent3_sop_projected_inputs`` in the active runtime resolves
    ``_provider_batch_once`` through its module globals at call time, therefore this
    wrapper executes before the hash-directed output Artifact is stored/accepted.
    """

    if getattr(runtime_module, "_SEMANTIC_PATH_REPAIR_V1_INSTALLED", False):
        return {
            "version": AGENT3_SEMANTIC_PATH_REPAIR_VERSION,
            "installed": True,
            "idempotentHit": True,
        }
    original = runtime_module._provider_batch_once

    def wrapped_provider_batch_once(
        envelopes: List[Dict[str, Any]],
        *,
        data_version: str | None,
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        outputs, summary = original(envelopes, data_version=data_version)
        packages = {
            _text(_dict(envelope.get("payload")).get("packageId") or _dict(envelope.get("payload")).get("itemId"), 220): dict(_dict(envelope.get("payload")))
            for envelope in envelopes
        }
        provider_calls = list(_arr(summary.get("providerCalls")))
        attempts = applied = 0
        for package_id, current in list(outputs.items()):
            package = packages.get(_text(package_id, 220))
            if not package or not isinstance(current, dict):
                continue
            repaired, audit = repair_normalized_sop(
                data_version=data_version,
                package=package,
                normalized=current,
            )
            if audit.get("attempted") is True:
                attempts += 1
                provider_call = _dict(audit.get("providerCall"))
                if provider_call:
                    provider_calls.append(provider_call)
            if audit.get("applied") is True:
                applied += 1
                outputs[package_id] = repaired
            elif audit.get("attempted") is True:
                failed = dict(current)
                failed["providerDeclaredStatus"] = (
                    _dict(current.get("contractValidation")).get("evaluatedStatus")
                    or current.get("sopStatus")
                )
                failed["systemContractViolations"] = _arr(
                    _dict(current.get("contractValidation")).get("missing")
                )
                failed["systemContractPassed"] = False
                failed["agent3SemanticPathRepair"] = {
                    key: value
                    for key, value in audit.items()
                    if key != "providerCall"
                }
                outputs[package_id] = failed

        summary = dict(summary)
        summary["providerCalls"] = provider_calls
        summary["semanticPathRepairVersion"] = AGENT3_SEMANTIC_PATH_REPAIR_VERSION
        summary["semanticPathRepairAttempts"] = attempts
        summary["semanticPathRepairApplied"] = applied
        summary["semanticPathRepairMaxAttemptsPerItem"] = 1
        summary["actualCalls"] = sum(int(_dict(item).get("actualCalls") or 0) for item in provider_calls)
        summary["inputTokens"] = sum(int(_dict(item).get("inputTokens") or 0) for item in provider_calls)
        summary["outputTokens"] = sum(int(_dict(item).get("outputTokens") or 0) for item in provider_calls)
        summary["reasoningTokens"] = sum(int(_dict(item).get("reasoningTokens") or 0) for item in provider_calls)
        return outputs, summary

    runtime_module._provider_batch_once = wrapped_provider_batch_once
    runtime_module._SEMANTIC_PATH_REPAIR_V1_INSTALLED = True
    return {
        "version": AGENT3_SEMANTIC_PATH_REPAIR_VERSION,
        "installed": True,
        "providerBoundaryPatchedBeforeArtifactAcceptance": True,
        "maxAttemptsPerItem": 1,
        "operatorActionStepsDirectRepairAllowed": False,
        "sameValidatorRequired": True,
        "fallbackAllowed": False,
    }


__all__ = [
    "AGENT3_SEMANTIC_PATH_REPAIR_VERSION",
    "classify_contract_violations",
    "extract_repairable_paths",
    "repair_normalized_sop",
    "install_agent3_semantic_path_repair",
]
