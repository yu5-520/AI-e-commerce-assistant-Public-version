"""V21.6 Agent2 experiment-permission overlay.

Agent2 must turn the locked action family into a concrete experiment whose
budget, traffic, duration and mainline scope stay inside the upstream permission.

Version ownership rule:
- ``AGENT2_ACTION_PLAN_CORE_VERSION`` belongs to the V21.4.1 core module.
- ``AGENT2_EXPERIMENT_POLICY_VERSION`` belongs to this V21.6.0 overlay.
An overlay may replace callable behavior, but must never overwrite the core
component version because provenance, replay and deployment checks depend on
those versions remaining independently addressable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

V216_AGENT2_POLICY_VERSION = "21.6.0"


def _ratio(value: Any) -> float | None:
    if value in {None, "", "—"}:
        return None
    try:
        text = str(value).strip().replace("%", "")
        number = float(text)
        return number / 100 if abs(number) > 1 else number
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value in {None, "", "—"}:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _collect_values(
    obj: Any,
    tokens: tuple[str, ...],
    parser: Any,
) -> List[float]:
    values: List[float] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower = str(key).lower()
            if any(token in lower for token in tokens):
                number = parser(value)
                if number is not None:
                    values.append(number)
            values.extend(_collect_values(value, tokens, parser))
    elif isinstance(obj, list):
        for value in obj[:30]:
            values.extend(_collect_values(value, tokens, parser))
    return values


def _permission_violations(
    raw: Dict[str, Any],
    policy: Dict[str, Any],
) -> List[str]:
    if not policy:
        return ["experiment_policy_missing"]
    violations: List[str] = []
    mode = str(raw.get("operationMode") or "").strip().lower()
    expected_mode = str(policy.get("experimentMode") or "").strip().lower()
    mainline_allowed = bool(policy.get("mainlineMutationAllowed"))

    if not mainline_allowed and expected_mode in {
        "isolated_test",
        "directional_test",
        "formal_optimization_test",
    }:
        allowed_modes = {
            "isolated_test",
            "directional_test",
            "formal_optimization_test",
        }
        if mode not in allowed_modes:
            violations.append("operation_mode_must_be_isolated_or_test")

    traffic_ceiling = _ratio(policy.get("trafficShareCeiling"))
    if traffic_ceiling is not None:
        traffic_values = _collect_values(
            raw,
            ("trafficshare", "traffic_share", "flowshare", "流量占比"),
            _ratio,
        )
        if any(value > traffic_ceiling + 1e-9 for value in traffic_values):
            violations.append("traffic_share_exceeds_ceiling")

    budget_ceiling = _ratio(policy.get("budgetChangeCeiling"))
    if budget_ceiling is not None:
        budget_values = _collect_values(
            raw,
            (
                "budgetchangerate",
                "budget_change_rate",
                "budgetchange",
                "budget_change",
                "预算调整比例",
                "预算变化比例",
            ),
            _ratio,
        )
        if any(value > budget_ceiling + 1e-9 for value in budget_values):
            violations.append("budget_change_exceeds_ceiling")

    duration_limit = _number(policy.get("durationHours")) or 0.0
    duration_values = _collect_values(
        raw,
        ("durationhours", "duration_hours", "测试时长小时"),
        _number,
    )
    if duration_limit and any(
        value > duration_limit for value in duration_values
    ):
        violations.append("duration_exceeds_permission")

    return list(dict.fromkeys(violations))


def install_v216_agent2_policy() -> None:
    from src.services import agent2_action_plan_core_v20_service as agent2

    if getattr(agent2, "_V216_AGENT2_POLICY_INSTALLED", False):
        return

    original_compact = agent2._compact_package
    original_build_messages = agent2._build_messages
    original_normalize_plan = agent2._normalize_plan

    def compact_package_v216(package: Dict[str, Any]) -> Dict[str, Any]:
        compact = original_compact(package)
        cross = (
            package.get("crossValidation")
            if isinstance(package.get("crossValidation"), dict)
            else {}
        )
        maturity = (
            cross.get("observationMaturity")
            if isinstance(cross.get("observationMaturity"), dict)
            else package.get("observationMaturity")
            if isinstance(package.get("observationMaturity"), dict)
            else {}
        )
        policy = (
            cross.get("experimentPolicy")
            if isinstance(cross.get("experimentPolicy"), dict)
            else package.get("experimentPolicy")
            if isinstance(package.get("experimentPolicy"), dict)
            else {}
        )
        return {
            **compact,
            "observationMaturity": maturity,
            "experimentPolicy": policy,
            "experimentPermissionContract": "operatingExperimentPermission.v1",
        }

    def build_messages_v216(
        data_version: str | None,
        packages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        messages, payload = original_build_messages(data_version, packages)
        messages[0]["content"] += (
            "V21.6追加硬约束：每个package包含experimentPolicy。你必须严格执行其"
            "experimentMode、targetObject、actionIntensity、trafficShareCeiling、"
            "budgetChangeCeiling、durationHours和mainlineMutationAllowed。"
            "当mainlineMutationAllowed=false时，禁止直接修改主链接、主计划或整体预算；"
            "标题主图必须新建测试链接，ROAS必须新建独立投放计划，活动或转化动作必须"
            "使用次链接/小流量隔离测试。不得输出核查、复查、确认信息或等待人工确认。"
            "operatorActionSteps必须是直接可执行动作，并写清测试变量、锁定变量、成功条件、"
            "回滚条件和升级条件。任何计划都不得突破流量和预算上限。"
        )
        payload["experimentPolicyVersion"] = V216_AGENT2_POLICY_VERSION
        return messages, payload

    def normalize_plan_v216(
        raw: Dict[str, Any],
        package: Dict[str, Any],
        proof: Dict[str, Any],
    ) -> Dict[str, Any]:
        plan = original_normalize_plan(raw, package, proof)
        cross = (
            package.get("crossValidation")
            if isinstance(package.get("crossValidation"), dict)
            else {}
        )
        maturity = (
            cross.get("observationMaturity")
            if isinstance(cross.get("observationMaturity"), dict)
            else package.get("observationMaturity")
            if isinstance(package.get("observationMaturity"), dict)
            else {}
        )
        policy = (
            cross.get("experimentPolicy")
            if isinstance(cross.get("experimentPolicy"), dict)
            else package.get("experimentPolicy")
            if isinstance(package.get("experimentPolicy"), dict)
            else {}
        )
        violations = _permission_violations(raw, policy)
        plan["observationMaturity"] = maturity
        plan["experimentPolicy"] = policy
        plan["experimentPolicyVersion"] = V216_AGENT2_POLICY_VERSION
        plan["experimentPermissionApplied"] = bool(policy)
        plan["experimentPermissionViolations"] = violations
        plan["experimentPermissionStatus"] = (
            "passed" if not violations else "rejected"
        )
        if violations:
            plan["actionPlanStatus"] = "conflict_requires_rejudgment"
            plan["conflictReason"] = (
                "Agent2 plan exceeds V21.6 experiment permission: "
                + ",".join(violations)
            )
            plan["taskAdmissionAllowed"] = False
        return plan

    agent2._compact_package = compact_package_v216
    agent2._build_messages = build_messages_v216
    agent2._normalize_plan = normalize_plan_v216
    agent2.AGENT2_EXPERIMENT_POLICY_VERSION = V216_AGENT2_POLICY_VERSION
    agent2._V216_AGENT2_POLICY_INSTALLED = True


__all__ = [
    "V216_AGENT2_POLICY_VERSION",
    "_permission_violations",
    "install_v216_agent2_policy",
]
