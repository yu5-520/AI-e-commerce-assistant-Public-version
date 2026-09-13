"""V26.9.B versioned Evaluation Plane.

Metrics are vectors, never a hidden composite score. Every result carries the exact
metric contract version, formula inputs, missing reason and interpretation limits.
No metric is allowed to claim causal attribution to an Agent or RAG.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any

from src.repositories.sqlite_repository import connect
from src.services import v269_experience_store_service as store
from src.services import v269_semantic_graph_service as graphs

VERSION = "26.9.B.1"
ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config/v269b_evaluation_contract.json"


class EvaluationPlaneError(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise EvaluationPlaneError("v269b_evaluation_" + reason)


def contract() -> dict[str, Any]:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), "contract_object")
    _require(value.get("version") == VERSION, "contract_version")
    _require(value.get("noCompositeScore") is True, "composite_score_forbidden")
    _require(value.get("causalAttributionForbidden") is True, "causal_attribution_guard")
    _require(isinstance(value.get("metrics"), dict) and value["metrics"], "metrics_required")
    return value


def _number(value: Any, name: str) -> float:
    _require(type(value) in (int, float) and math.isfinite(value), name + "_numeric")
    return float(value)


def _nonnegative_count(value: Any, name: str) -> float:
    number = _number(value, name)
    _require(number >= 0 and float(number).is_integer(), name + "_count")
    return number


def _window(raw: str, inputs: dict[str, Any]) -> str:
    mapping = {
        "PlanAction.reviewWindow": "reviewWindow",
        "execution_window": "executionWindow",
        "task_lifecycle": "taskLifecycleWindow",
        "source_defined": "observationWindow",
        "review_defined": "reviewWindow",
        "label_source_defined": "labelWindow",
        "evaluation_defined": "evaluationWindow",
    }
    key = mapping.get(raw)
    value = inputs.get(key) if key else None
    if value is None:
        return raw
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _missing(metric_id: str, spec: dict[str, Any], inputs: dict[str, Any], reason: str) -> dict[str, Any]:
    formula_inputs = {key: deepcopy(inputs.get(key)) for key in spec.get("requiredInputs") or []}
    return {
        "metricId": metric_id,
        "metricVersion": spec["version"],
        "definition": spec["definition"],
        "formula": spec["formula"],
        "numerator": None,
        "denominator": None,
        "value": None,
        "unit": spec["unit"],
        "observationWindow": _window(spec["observationWindow"], inputs),
        "sampleCount": 0,
        "missingReason": reason,
        "formulaInputs": formula_inputs,
        "supports": deepcopy(spec.get("supports") or []),
        "interpretationLimits": deepcopy(spec.get("doesNotSupport") or []),
    }


def _ratio(metric_id: str, spec: dict[str, Any], inputs: dict[str, Any], *, require_source: str | None = None) -> dict[str, Any]:
    required = spec["requiredInputs"]
    if any(inputs.get(key) is None for key in required[:2]):
        return _missing(metric_id, spec, inputs, spec["missingRules"][0])
    if require_source:
        source = inputs.get(require_source)
        if not isinstance(source, str) or not source.strip():
            return _missing(metric_id, spec, inputs, spec["missingRules"][0])
        _require(source not in {"agent1_self", "model_self", "self"}, "self_evaluation_forbidden")
    numerator = _nonnegative_count(inputs[required[0]], required[0])
    denominator = _nonnegative_count(inputs[required[1]], required[1])
    if denominator == 0:
        return _missing(metric_id, spec, inputs, spec["missingRules"][-1])
    _require(numerator <= denominator, "ratio_numerator_exceeds_denominator")
    result = _missing(metric_id, spec, inputs, "")
    result.update({
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator,
        "sampleCount": int(denominator),
        "missingReason": None,
    })
    return result


def _evaluate_metric(metric_id: str, spec: dict[str, Any], inputs: dict[str, Any], epsilon: float) -> dict[str, Any]:
    calculator = spec["calculator"]
    if calculator == "label_ratio":
        return _ratio(metric_id, spec, inputs, require_source="labelSource")
    if calculator == "external_value_ratio":
        return _ratio(metric_id, spec, inputs, require_source="evaluationSource")
    if calculator == "count_ratio":
        return _ratio(metric_id, spec, inputs)
    if calculator == "relative_prediction_accuracy":
        if inputs.get("actualValue") is None:
            return _missing(metric_id, spec, inputs, "ACTUAL_NOT_OBSERVED")
        expected = _number(inputs.get("expectedValue"), "expectedValue")
        actual = _number(inputs.get("actualValue"), "actualValue")
        if abs(expected) <= epsilon:
            return _missing(metric_id, spec, inputs, "EXPECTED_VALUE_NEAR_ZERO")
        error = abs(actual - expected)
        result = _missing(metric_id, spec, inputs, "")
        result.update({
            "numerator": error,
            "denominator": abs(expected),
            "value": max(0.0, 1.0 - error / abs(expected)),
            "sampleCount": 1,
            "missingReason": None,
        })
        return result
    if calculator in {"expected_delta", "actual_delta", "delta_realization_rate"}:
        if inputs.get("baselineValue") is None:
            return _missing(metric_id, spec, inputs, "BASELINE_MISSING")
        baseline = _number(inputs.get("baselineValue"), "baselineValue")
        if calculator == "expected_delta":
            if inputs.get("expectedValue") is None:
                return _missing(metric_id, spec, inputs, "EXPECTED_VALUE_MISSING")
            expected = _number(inputs.get("expectedValue"), "expectedValue")
            value = expected - baseline
            result = _missing(metric_id, spec, inputs, "")
            result.update({"numerator": value, "value": value, "unit": str(inputs.get("metricUnit") or "metric_native"), "sampleCount": 1, "missingReason": None})
            return result
        if inputs.get("actualValue") is None:
            return _missing(metric_id, spec, inputs, "ACTUAL_NOT_OBSERVED")
        actual = _number(inputs.get("actualValue"), "actualValue")
        actual_delta = actual - baseline
        if calculator == "actual_delta":
            result = _missing(metric_id, spec, inputs, "")
            result.update({"numerator": actual_delta, "value": actual_delta, "unit": str(inputs.get("metricUnit") or "metric_native"), "sampleCount": 1, "missingReason": None})
            return result
        if inputs.get("expectedValue") is None:
            return _missing(metric_id, spec, inputs, "EXPECTED_VALUE_MISSING")
        expected = _number(inputs.get("expectedValue"), "expectedValue")
        expected_delta = expected - baseline
        if abs(expected_delta) <= epsilon:
            return _missing(metric_id, spec, inputs, "EXPECTED_DELTA_NEAR_ZERO")
        result = _missing(metric_id, spec, inputs, "")
        result.update({
            "numerator": actual_delta,
            "denominator": expected_delta,
            "value": actual_delta / expected_delta,
            "sampleCount": 1,
            "missingReason": None,
        })
        return result
    if calculator == "observed_outcome":
        if inputs.get("observedValue") is None:
            return _missing(metric_id, spec, inputs, "OUTCOME_NOT_OBSERVED")
        source = inputs.get("observationSource")
        _require(isinstance(source, str) and source, "outcome_source_required")
        value = _number(inputs.get("observedValue"), "observedValue")
        result = _missing(metric_id, spec, inputs, "")
        result.update({"numerator": value, "value": value, "unit": str(inputs.get("metricUnit") or "metric_native"), "sampleCount": 1, "missingReason": None})
        return result
    raise EvaluationPlaneError("v269b_evaluation_calculator_unknown:" + str(calculator))


def evaluate_execution_result(
    source: dict[str, Any],
    inputs: dict[str, Any],
    *,
    subject_experience_id: str | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    """Build the complete evaluation vector; missing metrics stay explicit, never invented."""
    _require(isinstance(inputs, dict), "inputs_object")
    cfg = contract()
    epsilon = float(cfg.get("epsilon") or 1e-9)
    metrics: list[dict[str, Any]] = []
    for metric_id, spec in sorted(cfg["metrics"].items()):
        _require(isinstance(spec, dict), "metric_spec")
        result = _evaluate_metric(metric_id, spec, inputs, epsilon)
        evaluation_material = {
            "sourceHash": store.digest(store._normalize_source(source)),
            "subjectExperienceId": subject_experience_id,
            "metricId": metric_id,
            "metricVersion": result["metricVersion"],
            "formulaInputs": result["formulaInputs"],
            "observationWindow": result["observationWindow"],
        }
        result["evaluationId"] = "V269B-EVAL-" + store.digest(evaluation_material)[-20:].upper()
        result["subjectExperienceId"] = subject_experience_id
        metrics.append(result)
    vector_material = {
        "schema": "evaluation.vector.v269b.v1",
        "version": VERSION,
        "contractHash": store.file_digest(CONTRACT_PATH),
        "sourceHash": store.digest(store._normalize_source(source)),
        "subjectExperienceId": subject_experience_id,
        "metrics": metrics,
        "compositeScore": None,
        "causalAttribution": None,
    }
    vector = {**vector_material, "vectorHash": store.digest(vector_material)}
    if persist:
        receipts = []
        for metric in metrics:
            payload = {
                "subjectExperienceId": subject_experience_id,
                "evaluationId": metric["evaluationId"],
                "metricId": metric["metricId"],
                "metricVersion": metric["metricVersion"],
                "numerator": metric["numerator"],
                "denominator": metric["denominator"],
                "value": metric["value"],
                "unit": metric["unit"],
                "observationWindow": metric["observationWindow"],
                "sampleCount": metric["sampleCount"],
                "missingReason": metric["missingReason"],
                "formulaInputs": metric["formulaInputs"],
                "interpretationLimits": metric["interpretationLimits"],
            }
            recorded = store.record_experience(
                source=source,
                domain="evaluation_results",
                applicability={"metricId": metric["metricId"], "metricVersion": metric["metricVersion"]},
                payload=payload,
            )
            receipts.append(recorded["receiptHash"])
        vector["persistenceReceipts"] = receipts
    return vector


def _review_source(
    package: dict[str, Any],
    *,
    target_source_content_hash: str,
    review_receipt: dict[str, Any],
) -> dict[str, Any]:
    receipt_hash = str(review_receipt.get("receiptHash") or "")
    _require(receipt_hash.startswith("sha256:"), "review_receipt_hash")
    evidence_refs = sorted(set(
        [str(ref) for ref in package.get("evidenceRefs") or [] if isinstance(ref, str) and ref]
        + [target_source_content_hash, receipt_hash]
    ))
    source = {
        "sourceTaskId": str(package.get("taskId") or package.get("packageId") or ""),
        "decisionGraphHash": package["DecisionGraph"]["graphHash"],
        "planGraphHash": package["PlanGraph"]["graphHash"],
        "operationGraphHash": package["OperationGraph"]["graphHash"],
        "graphContractVersion": str(package.get("semanticContractVersion") or "26.9.0"),
        "evaluationVersion": VERSION,
        "evidenceRefs": evidence_refs,
        "businessScope": {
            "storeId": package.get("storeId"),
            "productId": package.get("productId"),
        },
        "sourceType": "runtime",
        "sourceVersion": "v269.system_review:" + receipt_hash,
    }
    store._normalize_source(source)
    return source


def record_system_review_candidate(
    package: dict[str, Any],
    *,
    target_facts: dict[str, Any],
    target_source_content_hash: str,
    review_receipt: dict[str, Any],
    review_status: str,
) -> dict[str, Any]:
    """Persist post-review candidates without granting promotion or execution authority."""
    _require(review_status in {"SETTLED", "ADJUSTMENT_REQUIRED"}, "review_status")
    _require(isinstance(target_facts, dict), "target_facts")
    decision_nodes = graphs.index(package.get("DecisionGraph"))
    plan_nodes = graphs.index(package.get("PlanGraph"))
    operation_nodes = graphs.index(package.get("OperationGraph"))
    source = _review_source(
        package,
        target_source_content_hash=target_source_content_hash,
        review_receipt=review_receipt,
    )

    strategy_ids: list[str] = []
    operation_ids: list[str] = []
    vector_hashes: list[str] = []
    evaluation_receipts: list[str] = []

    for plan_key, node in sorted(plan_nodes.items()):
        decision = decision_nodes.get(node.get("decisionActionRef")) or {}
        action_family = str(node.get("actionFamily") or decision.get("actionFamily") or "")
        action_type = str(decision.get("actionType") or "")
        _require(bool(action_family and action_type), "plan_decision_semantics")
        actual = {
            metric: deepcopy(target_facts[metric])
            for metric in node.get("affectedMetrics") or []
            if isinstance(target_facts.get(metric), dict)
        }
        errors = []
        for metric, observation in actual.items():
            expected = (node.get("expectedOutcome") or {}).get(metric) or {}
            if type(expected.get("expectedValue")) in (int, float) and type(observation.get("value")) in (int, float):
                errors.append(abs(float(expected["expectedValue"]) - float(observation["value"])))
        strategy_payload = {
            "decisionActionKey": node["decisionActionRef"],
            "decisionAction": {"actionType": action_type, "actionFamily": action_family},
            "planActionKey": plan_key,
            "planAction": {"actionFamily": action_family},
            "category": graphs.contract()["actionFamilyDomains"].get(action_family),
            "strategyType": action_family,
            "baseline": deepcopy(node.get("baseline") or {}),
            "expected": deepcopy(node.get("expectedOutcome") or {}),
            "actual": actual or None,
            "predictionError": (sum(errors) / len(errors)) if errors else None,
            "sampleCount": len(actual),
        }
        strategy = store.record_experience(
            source=source,
            domain="strategy_outcomes",
            applicability={
                "actionFamily": action_family,
                "reviewStatus": review_status,
                "affectedMetrics": deepcopy(node.get("affectedMetrics") or []),
            },
            payload=strategy_payload,
        )
        strategy_ids.append(strategy["experienceId"])

        for metric in node.get("affectedMetrics") or []:
            baseline = (node.get("baseline") or {}).get(metric) or {}
            expected = (node.get("expectedOutcome") or {}).get(metric) or {}
            observed = target_facts.get(metric) if isinstance(target_facts.get(metric), dict) else {}
            inputs = {
                "baselineValue": baseline.get("value"),
                "expectedValue": expected.get("expectedValue"),
                "actualValue": observed.get("value"),
                "metricUnit": baseline.get("unit") or observed.get("unit"),
                "reviewWindow": deepcopy(node.get("reviewWindow")),
                "observationSource": observed.get("sourceRef") or target_source_content_hash,
                "observedValue": observed.get("value"),
            }
            vector = evaluate_execution_result(
                source,
                inputs,
                subject_experience_id=strategy["experienceId"],
                persist=True,
            )
            vector_hashes.append(vector["vectorHash"])
            evaluation_receipts.extend(vector.get("persistenceReceipts") or [])

    for stage_key, stage in sorted(operation_nodes.items()):
        refs = stage.get("planActionRefs") or []
        for plan_ref in refs:
            plan = plan_nodes.get(plan_ref) or {}
            action_family = str(plan.get("actionFamily") or "")
            operations = (((plan.get("parameters") or {}).get("operationPlan") or {}).get("operations") or [])
            execution_types = sorted({
                str(item.get("operationType") or "").strip()
                for item in operations if isinstance(item, dict) and str(item.get("operationType") or "").strip()
            }) or ["operation_stage"]
            for execution_type in execution_types:
                operation = store.record_experience(
                    source=source,
                    domain="operation_patterns",
                    applicability={
                        "actionFamily": action_family,
                        "reviewStatus": review_status,
                    },
                    payload={
                        "planActionKey": plan_ref,
                        "planAction": {"actionFamily": action_family},
                        "platform": (package.get("BusinessFacts") or {}).get("productIdentity", {}).get("platform"),
                        "executionType": execution_type,
                        "completionStatus": review_status,
                        "rollbackOccurred": None,
                        "sampleCount": 1,
                    },
                )
                operation_ids.append(operation["experienceId"])

    material = {
        "schema": "evaluation.system_review_candidate_receipt.v269b.v1",
        "version": VERSION,
        "sourceHash": store.digest(store._normalize_source(source)),
        "reviewStatus": review_status,
        "strategyExperienceIds": sorted(set(strategy_ids)),
        "operationExperienceIds": sorted(set(operation_ids)),
        "evaluationVectorHashes": sorted(set(vector_hashes)),
        "evaluationPersistenceReceipts": sorted(set(evaluation_receipts)),
        "promotionPerformed": False,
        "knowledgeHeadMutated": False,
    }
    return {**material, "receiptHash": store.digest(material)}


def read_task_evaluation_evidence(task_id: str, *, limit: int = 200) -> dict[str, Any]:
    """Read persisted evaluation records for SOP display; never recompute on GET."""
    task = str(task_id or "").strip()
    body: dict[str, Any] = {
        "schema": "evaluation.task_evidence.v269b.v1",
        "version": VERSION,
        "taskId": task,
        "status": "NOT_RECORDED",
        "contractHash": store.file_digest(CONTRACT_PATH),
        "items": [],
        "sourceReceipts": [],
        "missingMetricCount": 0,
        "sampleCount": 0,
        "invalidRecordCount": 0,
        "compositeScore": None,
        "causalAttribution": None,
        "recomputedOnRead": False,
        "promotionPerformed": False,
        "knowledgeHeadMutated": False,
    }
    if not task:
        return {**body, "receiptHash": store.digest(body)}
    cfg = contract()
    try:
        with connect() as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                    "('v269b_experience_sources','v269b_experience_items','v269b_evaluation_results')"
                )
            }
            if tables != {'v269b_experience_sources','v269b_experience_items','v269b_evaluation_results'}:
                return {**body, "receiptHash": store.digest(body)}
            rows = conn.execute(
                """SELECT r.evaluation_id,r.metric_id,r.metric_version,r.numerator,r.denominator,
                          r.value,r.unit,r.observation_window,r.sample_count,r.missing_reason,
                          r.formula_inputs,r.interpretation_limits,i.experience_id,i.lifecycle_status,
                          s.source_hash,s.source_version,s.evaluation_version,s.evidence_refs,s.source_type
                   FROM v269b_evaluation_results r
                   JOIN v269b_experience_items i ON i.experience_id=r.experience_id
                   JOIN v269b_experience_sources s ON s.source_id=i.source_id
                   WHERE s.source_task_id=?
                   ORDER BY r.metric_id ASC,r.evaluation_id ASC LIMIT ?""",
                (task, max(1, min(500, int(limit)))),
            ).fetchall()
    except Exception:
        body["status"] = "READ_UNAVAILABLE"
        return {**body, "receiptHash": store.digest(body)}

    source_map: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    invalid = 0
    for row in rows:
        metric_id = str(row["metric_id"] or "")
        spec = (cfg.get("metrics") or {}).get(metric_id)
        if not isinstance(spec, dict) or str(row["metric_version"] or "") != str(spec.get("version") or ""):
            invalid += 1
            continue
        try:
            formula_inputs = json.loads(row["formula_inputs"] or "{}")
            limits = json.loads(row["interpretation_limits"] or "[]")
            evidence_refs = json.loads(row["evidence_refs"] or "[]")
        except Exception:
            invalid += 1
            continue
        if not isinstance(formula_inputs, dict) or not isinstance(limits, list) or not isinstance(evidence_refs, list):
            invalid += 1
            continue
        source_hash = str(row["source_hash"] or "")
        source_map[source_hash] = {
            "sourceHash": source_hash,
            "sourceVersion": row["source_version"],
            "evaluationVersion": row["evaluation_version"],
            "sourceType": row["source_type"],
            "evidenceRefs": evidence_refs,
        }
        items.append({
            "experienceId": row["experience_id"],
            "evaluationId": row["evaluation_id"],
            "metricId": metric_id,
            "metricVersion": row["metric_version"],
            "definition": spec.get("definition"),
            "formula": spec.get("formula"),
            "numerator": row["numerator"],
            "denominator": row["denominator"],
            "value": row["value"],
            "unit": row["unit"],
            "observationWindow": row["observation_window"],
            "sampleCount": int(row["sample_count"] or 0),
            "missingReason": row["missing_reason"],
            "formulaInputs": formula_inputs,
            "supports": deepcopy(spec.get("supports") or []),
            "interpretationLimits": limits,
            "lifecycleStatus": row["lifecycle_status"],
            "sourceHash": source_hash,
        })
    body["items"] = items
    body["sourceReceipts"] = [source_map[key] for key in sorted(source_map)]
    body["missingMetricCount"] = sum(item.get("missingReason") is not None for item in items)
    body["sampleCount"] = sum(int(item.get("sampleCount") or 0) for item in items)
    body["invalidRecordCount"] = invalid
    body["status"] = "INVALID_EVIDENCE" if invalid else ("RECORDED" if items else "NOT_RECORDED")
    return {**body, "receiptHash": store.digest(body)}
