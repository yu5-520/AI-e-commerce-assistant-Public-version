#!/usr/bin/env python3
"""Deterministic OpenAI-compatible provider for competition structural E2E.

This provider is intentionally **not** presented as Bailian or as a model-quality
proof. It exists only to prove that the public runtime can move fixed reports
through the exact Agent contracts, Hash replay, deterministic task mapping and
frontend read models without a hidden fallback. A separate real Bailian run is
required for final competition evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "competition.contract_fixture_provider.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _last_user_payload(body: Mapping[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or item.get("content") or "")
                if isinstance(item, dict)
                else str(item)
                for item in content
            )
        try:
            parsed = json.loads(str(content or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _agent1(payload: dict[str, Any]) -> dict[str, Any]:
    judgments: list[dict[str, Any]] = []
    for item in _list(payload.get("products")):
        if not isinstance(item, dict):
            continue
        product_id = _text(item.get("productId"))
        store_id = _text(item.get("storeId")) or "COMP-STORE-1"
        correlation_id = _text(item.get("correlationId"))
        signal_id = item.get("signalId")
        common = {
            "correlationId": correlation_id,
            "productId": product_id,
            "storeId": store_id,
            "signalId": signal_id,
            "metricCode": "all_metrics",
            "confidence": 0.93,
            "facts": [
                {
                    "factRef": "F1",
                    "role": "evidence",
                    "text": "固定三报表中的连续趋势已通过经营证据层交叉验证。",
                }
            ],
            "causalHypotheses": [
                {
                    "hypothesis": "核心经营指标变化来自当前商品链路中的主要承接节点。",
                    "supportingFacts": ["F1"],
                }
            ],
            "rejectedHypotheses": [
                {
                    "hypothesis": "仅由单次数据抖动造成。",
                    "reason": "连续三期趋势不支持该解释。",
                }
            ],
            "alternatives": [],
            "preconditions": ["仅操作当前店铺与当前商品"],
            "missingEvidence": [],
            "ragProof": {
                "usedCaseIds": [],
                "rejectedCaseIds": [],
                "reason": "合同夹具不注入历史结论。",
            },
            "excludedActions": ["跨店铺操作", "无数据依据的多动作并发"],
            "companyHooks": [],
        }
        if product_id == "COMP-P-OBSERVE":
            judgments.append(
                {
                    **common,
                    "severity": "normal",
                    "decisionHint": "observe_only",
                    "decisionType": "observe",
                    "finding": "主要指标处于稳定区间。",
                    "coreProblem": "当前没有足够证据触发经营动作。",
                    "decisionSummary": "保持观察，不制造无效任务。",
                    "selectedOperatingRoute": "observe",
                    "selectedActionFamilyHint": None,
                    "actionIntent": None,
                    "riskBoundaries": ["不得为了测试链路而生成虚假任务"],
                    "requiredActionData": [],
                    "capacityConstraints": [],
                }
            )
            continue
        if product_id == "COMP-P-SCALE":
            judgments.append(
                {
                    **common,
                    "severity": "high",
                    "decisionHint": "risk_candidate",
                    "decisionType": "act",
                    "finding": "ROAS连续上升且转化同步改善，具备受控增投条件。",
                    "coreProblem": "当前预算未充分承接已验证的高效流量。",
                    "decisionSummary": "锁定受控ROAS增投，禁止同时改创意与活动。",
                    "selectedOperatingRoute": "paid_traffic_operation",
                    "selectedActionFamilyHint": "roas_scale",
                    "actionIntent": "在风险边界内逐步增加有效投放规模。",
                    "riskBoundaries": ["单次预算增幅不超过20%", "保留回滚基线"],
                    "requiredActionData": ["当前预算", "当前出价", "最近三期ROAS"],
                    "capacityConstraints": ["库存仅作为承接条件，不作为ROAS判断依据"],
                }
            )
            continue
        judgments.append(
            {
                **common,
                "severity": "high",
                "decisionHint": "risk_candidate",
                "decisionType": "act",
                "finding": "流量和点击稳定，但支付转化率连续下降。",
                "coreProblem": "商品详情页首屏信任承接持续减弱。",
                "decisionSummary": "锁定详情页承接修复，不改变价格和投放。",
                "selectedOperatingRoute": "conversion_operation",
                "selectedActionFamilyHint": "conversion_repair",
                "actionIntent": "通过单变量页面修复验证转化损耗节点。",
                "riskBoundaries": ["不修改商品价格", "不同时修改广告计划"],
                "requiredActionData": ["详情页当前版本", "支付转化率基线"],
                "capacityConstraints": [],
            }
        )
    return {"judgments": judgments}


def _agent2_family_payload(family: str, package: dict[str, Any]) -> dict[str, Any]:
    product_id = _text(package.get("productId"))
    if family == "roas_scale":
        return {
            "operations": [
                {
                    "operationType": "budget_adjustment",
                    "target": {"targetType": "product", "targetId": product_id},
                    "direction": "increase",
                    "currentValue": "current_budget",
                    "targetValue": "+15%",
                    "parameterRange": {"minimum": "+10%", "maximum": "+20%"},
                    "rollback": "ROAS跌破执行前基线时恢复原预算",
                }
            ],
            "validationMetrics": ["ROAS", "支付转化率", "消耗"],
            "riskBoundaries": ["预算单次增幅不超过20%"],
        }
    if family == "title_image_test":
        return {
            "directions": [
                {
                    "fullTitle": "核心卖点明确的测试标题A",
                    "mainImageStructure": {"headline": "核心卖点", "proof": "场景证明"},
                    "testFocusWords": ["核心卖点", "使用场景"],
                    "platformFit": "符合平台标题与主图规范",
                    "differenceFromOthers": "强调单一核心卖点",
                },
                {
                    "fullTitle": "场景利益明确的测试标题B",
                    "mainImageStructure": {"headline": "场景利益", "proof": "产品细节"},
                    "testFocusWords": ["场景利益", "产品细节"],
                    "platformFit": "保持平台合规与移动端可读性",
                    "differenceFromOthers": "强调场景化利益表达",
                },
            ]
        }
    return {
        "repairDetail": "重组详情页首屏卖点与信任证明，只改变承接内容，不改变价格和投放。",
        "parameterRanges": {"testDays": [3, 5], "singleVariable": True},
        "validationMetrics": ["支付转化率", "收藏加购率"],
        "riskBoundaries": ["不修改商品价格", "不修改广告预算"],
        "supportingCoordination": [],
    }


def _agent2(payload: dict[str, Any]) -> dict[str, Any]:
    family = _text(payload.get("lockedActionFamily"))
    plans: list[dict[str, Any]] = []
    for package in _list(payload.get("packages")):
        if not isinstance(package, dict):
            continue
        plans.append(
            {
                "packageId": package.get("packageId"),
                "productId": package.get("productId"),
                "storeId": package.get("storeId"),
                "familyPayload": _agent2_family_payload(family, package),
                "missingData": [],
                "conflictReasons": [],
            }
        )
    return {"plans": plans}


def _step_text(action_type: str, family: str) -> tuple[str, str, str]:
    if action_type in {"page_audit", "problem_localization"}:
        return (
            "运营专员",
            "核对系统冻结证据与当前详情页，定位首屏承接损耗节点。",
            "形成一个有截图和指标引用的问题定位记录。",
        )
    if action_type in {"content_restructure", "trust_repair", "detail_consistency_check"}:
        return (
            "运营专员",
            "按Agent2草案重组当前商品详情页首屏卖点与信任证明，不修改价格和投放。",
            "完成页面修改并通过内容一致性检查。",
        )
    if action_type in {"experiment_control", "result_review"}:
        return (
            "数据分析",
            "运行单变量验证并对比执行前后的支付转化率与收藏加购率。",
            "形成带数据版本的3日验证结论。",
        )
    if action_type == "plan_audit":
        return (
            "运营专员",
            "核对当前计划、预算、出价和系统冻结的ROAS基线。",
            "确认一个可调整计划及其原始参数。",
        )
    if action_type in {"budget_adjustment", "bid_adjustment", "schedule_adjustment", "audience_adjustment", "plan_split"}:
        return (
            "运营专员",
            "按草案对当前商品关联计划执行一次受控参数调整，单次幅度不超过约定边界。",
            "完成参数修改并保留平台操作凭证。",
        )
    if action_type == "result_review":
        return (
            "数据分析",
            "在验证周期结束后对比ROAS、消耗和转化率。",
            "形成继续、暂停或回滚的结论。",
        )
    return (
        "运营专员",
        f"执行{family}合同允许的{action_type}动作。",
        "提交可验证的完成凭证。",
    )


def _required_action_types(package: dict[str, Any]) -> list[str]:
    groups = _list(package.get("requiredActionTypeGroups"))
    selected: list[str] = []
    for group in groups:
        candidates = [str(item) for item in _list(group) if item]
        if candidates:
            selected.append(candidates[0])
    allowed = [str(item) for item in _list(package.get("allowedActionTypes")) if item]
    while len(selected) < 3:
        candidate = next((item for item in allowed if item not in selected and item != "rollback"), None)
        if not candidate:
            break
        selected.append(candidate)
    return selected[: max(3, len(selected))]


def _agent3(payload: dict[str, Any]) -> dict[str, Any]:
    sops: list[dict[str, Any]] = []
    for package in _list(payload.get("packages")):
        if not isinstance(package, dict):
            continue
        family = _text(package.get("lockedActionFamily"))
        product_id = _text(package.get("productId"))
        store_id = _text(package.get("storeId"))
        execution_steps: list[dict[str, Any]] = []
        for index, action_type in enumerate(_required_action_types(package), 1):
            role, instruction, completion = _step_text(action_type, family)
            execution_steps.append(
                {
                    "stepId": f"STEP-{index}",
                    "actionFamily": family,
                    "actionType": action_type,
                    "executionObject": {
                        "targetType": "product",
                        "targetId": product_id,
                        "storeId": store_id,
                    },
                    "executorRole": role,
                    "instruction": instruction,
                    "deadline": f"T+{index * 4}小时",
                    "completionCriteria": completion,
                }
            )
        stop_types = [str(item) for item in _list(package.get("allowedStopConditionTypes")) if item]
        rollback_types = [str(item) for item in _list(package.get("allowedRollbackConditionTypes")) if item]
        stop_type = stop_types[0] if stop_types else "metric_guardrail"
        rollback_type = rollback_types[0] if rollback_types else "restore_previous_state"
        title = (
            f"受控增投 {product_id} 并验证ROAS"
            if family == "roas_scale"
            else f"修复 {product_id} 详情页首屏承接"
        )
        metrics = ["ROAS", "支付转化率", "消耗"] if family == "roas_scale" else ["支付转化率", "收藏加购率"]
        sops.append(
            {
                "packageId": package.get("packageId"),
                "productId": product_id,
                "storeId": store_id,
                "actionFamily": family,
                "sopStatus": "sop_ready",
                "finalTaskTitle": title,
                "executionObjective": "在锁定动作族、对象和权限边界内完成一次可回滚的经营验证。",
                "executionSteps": execution_steps,
                "decisionBranches": [],
                "submissionEvidence": [
                    {
                        "title": "执行凭证",
                        "requiredFields": ["before", "after", "dataVersion"],
                    }
                ],
                "crossDepartmentActions": [],
                "approvalFlow": {"approvalRequired": False},
                "reviewMetrics": metrics,
                "verificationPeriod": "3天",
                "stopConditions": [
                    {
                        "conditionId": "STOP-1",
                        "actionFamily": family,
                        "conditionType": stop_type,
                        "condition": "核心验证指标连续两个观察点低于执行前基线。",
                        "responseAction": "暂停当前动作并进入复盘。",
                        "evidenceRequired": ["指标截图", "数据版本"],
                    }
                ],
                "rollbackConditions": [
                    {
                        "conditionId": "ROLLBACK-1",
                        "actionFamily": family,
                        "conditionType": rollback_type,
                        "condition": "已触发停止条件且负责人确认回滚。",
                        "rollbackAction": "恢复执行前页面或计划参数。",
                        "evidenceRequired": ["回滚前后截图", "平台操作记录"],
                    }
                ],
                "reviewCycle": ["3天", "7天"],
                "companyStyleReason": "遵循唯一主动作、单变量验证、证据可追溯和可回滚原则。",
                "ragUsedCaseIds": [],
                "ragRejectedCaseIds": [],
                "ragApplicationReason": "本次仅验证合同主链，不伪造企业历史经验。",
                "semanticContractMissing": [],
            }
        )
    return {"sops": sops}


def _repair(payload: dict[str, Any]) -> dict[str, Any]:
    family = _text(payload.get("lockedActionFamily"))
    stop_types = [str(item) for item in _list(payload.get("allowedStopConditionTypes")) if item]
    rollback_types = [str(item) for item in _list(payload.get("allowedRollbackConditionTypes")) if item]
    return {
        "repair": {
            "packageId": payload.get("packageId"),
            "stopConditions": [
                {
                    "conditionId": "STOP-REPAIR-1",
                    "actionFamily": family,
                    "conditionType": stop_types[0] if stop_types else "metric_guardrail",
                    "condition": "核心指标低于基线。",
                    "responseAction": "暂停并复盘。",
                    "evidenceRequired": ["指标证据"],
                }
            ],
            "rollbackConditions": [
                {
                    "conditionId": "ROLLBACK-REPAIR-1",
                    "actionFamily": family,
                    "conditionType": rollback_types[0] if rollback_types else "restore_previous_state",
                    "condition": "停止条件已确认。",
                    "rollbackAction": "恢复执行前状态。",
                    "evidenceRequired": ["回滚凭证"],
                }
            ],
        }
    }


def response_payload(request_body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = _last_user_payload(request_body)
    if payload.get("repairType") == "agent3_auxiliary_condition_repair":
        return "agent3_auxiliary_repair", _repair(payload)
    if isinstance(payload.get("products"), list):
        return "product_judgment_agent", _agent1(payload)
    packages = payload.get("packages")
    if isinstance(packages, list) and (
        payload.get("systemConstraintVersion") or payload.get("schema") == "agent3.sop.v1"
    ):
        return "agent3_sop_agent", _agent3(payload)
    if isinstance(packages, list):
        return "action_plan_judgment_agent", _agent2(payload)
    raise ValueError("fixture_provider_unrecognized_request")


class FixtureState:
    def __init__(self, evidence_path: Path | None = None) -> None:
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.call_count = 0
        self.stage_counts: dict[str, int] = {}
        self.request_hashes: list[str] = []
        self.evidence_path = evidence_path

    def record(self, stage: str, request_body: Mapping[str, Any]) -> int:
        digest = hashlib.sha256(
            json.dumps(request_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.lock:
            self.call_count += 1
            self.stage_counts[stage] = self.stage_counts.get(stage, 0) + 1
            self.request_hashes.append(digest)
            call_number = self.call_count
            self.write_evidence()
            return call_number

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "schema": SCHEMA,
                "mode": "deterministic_contract_fixture_not_model_quality_proof",
                "callCount": self.call_count,
                "stageCounts": dict(sorted(self.stage_counts.items())),
                "requestHashes": list(self.request_hashes),
                "startedAtEpoch": self.started_at,
            }

    def write_evidence(self) -> None:
        if not self.evidence_path:
            return
        snapshot = {
            "schema": SCHEMA,
            "mode": "deterministic_contract_fixture_not_model_quality_proof",
            "callCount": self.call_count,
            "stageCounts": dict(sorted(self.stage_counts.items())),
            "requestHashes": list(self.request_hashes),
            "startedAtEpoch": self.started_at,
        }
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "CompetitionContractFixture/1.0"

    @property
    def state(self) -> FixtureState:
        return self.server.fixture_state  # type: ignore[attr-defined]

    def _write_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-request-id", f"FIXTURE-{int(time.time() * 1000)}")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in {"", "/health"}:
            self._write_json(200, {"ok": True, **self.state.snapshot()})
            return
        if self.path.rstrip("/") == "/stats":
            self._write_json(200, self.state.snapshot())
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.endswith("/chat/completions"):
            self._write_json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            request_body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(request_body, dict):
                raise ValueError("request_body_must_be_object")
            stage, result = response_payload(request_body)
            call_number = self.state.record(stage, request_body)
            content = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self._write_json(
                200,
                {
                    "id": f"fixture-call-{call_number}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": request_body.get("model") or "competition-contract-fixture",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": max(1, len(json.dumps(request_body, ensure_ascii=False)) // 4),
                        "completion_tokens": max(1, len(content) // 4),
                        "total_tokens": max(2, (len(json.dumps(request_body, ensure_ascii=False)) + len(content)) // 4),
                    },
                },
            )
        except Exception as exc:
            self._write_json(
                400,
                {"error": {"type": type(exc).__name__, "message": str(exc)}},
            )

    def log_message(self, format: str, *args: Any) -> None:
        return


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic competition contract fixture provider.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39180)
    parser.add_argument("--evidence", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    state = FixtureState(Path(args.evidence).resolve() if args.evidence else None)
    server = ThreadingHTTPServer((args.host, args.port), FixtureHandler)
    server.fixture_state = state  # type: ignore[attr-defined]
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "listening": f"http://{args.host}:{args.port}",
                "mode": "deterministic_contract_fixture_not_model_quality_proof",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        state.write_evidence()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
