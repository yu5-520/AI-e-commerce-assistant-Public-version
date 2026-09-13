"""V26.9.A production System Review and lifecycle boundary tests."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.repositories import sqlite_repository as repo
from src.services import task_lifecycle_state_machine_service as lifecycle
from src.services import v269_semantic_graph_service as graphs
from src.services import v269_system_review_service as review
from tests.test_v22_4_v269_production_admission import graph_case


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "LOG_DIR", tmp_path)
    monkeypatch.setattr(repo, "DB_PATH", tmp_path / "v269-review.sqlite3")
    monkeypatch.setattr(repo, "_WAL_INITIALIZED", False)
    review.ensure_review_tables()
    return tmp_path


def _register(package, task_id: str, *, baseline_hash: str, registered_at: int = 1000):
    with patch.object(review, "_baseline_source_hash", return_value=baseline_hash):
        with repo.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            value = review.register_review_in_conn(
                conn,
                task={"taskId": task_id},
                decision=package,
                chain={},
                frozen_at_millis=registered_at,
            )
            conn.commit()
    return value


def _candidate(value: float):
    return {
        "storeId": "store-1",
        "productId": "product-1",
        "BusinessFacts": {
            "factValues": {
                "fact:metric:roas": {"value": value, "unit": "ratio"},
            }
        },
    }


def _receipt(package, *, task_id: str, target_value: float, decision: str = "SETTLED"):
    target = {
        "roas": {
            "value": target_value,
            "unit": "ratio",
            "sourceRef": "fact:metric:roas",
        }
    }
    value = {
        "schema": review.JAVA_RECEIPT_SCHEMA,
        "version": review.VERSION,
        "taskId": task_id,
        "productId": package["productId"],
        "DecisionGraphHash": package["DecisionGraph"]["graphHash"],
        "PlanGraphHash": package["PlanGraph"]["graphHash"],
        "OperationGraphHash": package["OperationGraph"]["graphHash"],
        "targetFactsHash": graphs.digest(target),
        "aggregateDecision": decision,
        "revisionDirective": {},
        "rootBound": True,
        "productionMutationPerformed": False,
        "ragFeedbackPerformed": False,
    }
    value["receiptHash"] = graphs.digest(value)
    return value


def test_review_registration_is_closed_until_execution_and_rejects_stale_target(isolated_db):
    package, _ = graph_case()
    baseline_hash = "sha256:" + "a" * 64
    registration = _register(package, "TASK-1", baseline_hash=baseline_hash)
    assert registration["status"] == "AWAITING_EXECUTION"
    assert review.pending_review_for_scope("store-1", "product-1") == []

    with patch.object(review, "_epoch_millis", return_value=2000):
        pending = review.mark_review_pending("TASK-1")
    assert pending["status"] == "PENDING"
    assert pending["reviewWindowStartMillis"] == 2000

    same_base = review.observe_candidate_facts(
        _candidate(2.0), source_content_hash=baseline_hash, observed_at_millis=3000
    )
    assert same_base["status"] == "NO_PENDING_REVIEW"

    stale = review.observe_candidate_facts(
        _candidate(2.0),
        source_content_hash="sha256:" + "b" * 64,
        observed_at_millis=1999,
    )
    assert stale["status"] == "TARGET_NOT_AFTER_EXECUTION"


def test_real_target_consumes_verified_java_receipt_and_never_calls_legacy_recap(isolated_db):
    package, _ = graph_case()
    _register(package, "TASK-2", baseline_hash="sha256:" + "c" * 64)
    with patch.object(review, "_epoch_millis", return_value=2000):
        review.mark_review_pending("TASK-2")
    java_receipt = _receipt(package, task_id="TASK-2", target_value=2.0)

    with patch.object(review, "_call_java", return_value=java_receipt), patch(
        "src.services.task_lifecycle_state_machine_service.transition_lifecycle_task",
        return_value={"ok": True, "action": "system_review_settled"},
    ) as transition:
        result = review.observe_candidate_facts(
            _candidate(2.0),
            source_content_hash="sha256:" + "d" * 64,
            observed_at_millis=86_500_000,
        )
    assert result["status"] == "SETTLED"
    assert result["ragFeedbackPerformed"] is False
    transition.assert_called_once()
    assert transition.call_args.args[1] == "system_review_settled"
    assert "beforeMetrics" not in transition.call_args.kwargs["payload"]
    assert "afterMetrics" not in transition.call_args.kwargs["payload"]


def test_java_unavailable_is_fail_closed_not_python_fallback(isolated_db):
    package, _ = graph_case()
    _register(package, "TASK-3", baseline_hash="sha256:" + "e" * 64)
    with patch.object(review, "_epoch_millis", return_value=2000):
        review.mark_review_pending("TASK-3")
    with patch.object(review, "_call_java", side_effect=RuntimeError("java_down")):
        result = review.observe_candidate_facts(
            _candidate(2.0),
            source_content_hash="sha256:" + "f" * 64,
            observed_at_millis=86_500_000,
        )
    assert result["status"] == "REVIEW_UNAVAILABLE"
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT status,review_receipt FROM v269_system_reviews WHERE task_id='TASK-3'"
        ).fetchone()
    assert row["status"] == "REVIEW_UNAVAILABLE"
    assert row["review_receipt"] is None


def test_v269_submit_opens_system_review_without_old_recap_or_rag_path():
    task = {
        "id": "TASK-V269",
        "taskId": "TASK-V269",
        "semanticContractVersion": "26.9.0",
        "taskGenerationMode": "v269_canonical_graph_lifecycle",
        "sourceModule": "v269_production_admission_service",
        "status": "处理中",
        "workflowStatus": "处理中",
        "displayStatus": "处理中",
        "taskLayer": "operator_execution",
        "actionAuthorization": {"decision": "auto_execute"},
    }
    patch_value = lifecycle._status_patch(task, "submit", "operator", {})
    assert patch_value["workflowStatus"] == "等待系统评审"
    assert patch_value["lifecycleStage"] == "system_review_pending"
    assert patch_value["systemReviewStatus"] == "PENDING"

    with patch.object(lifecycle, "_find_primary_task", return_value=(task, {})), patch(
        "src.services.v269_system_review_service.mark_review_pending",
        return_value={"ok": True, "status": "PENDING"},
    ) as mark_pending, patch.object(lifecycle, "handle_manager_reviewed") as legacy_review, patch.object(
        lifecycle, "complete_recap_and_create_rag_candidate"
    ) as legacy_rag:
        assert lifecycle._apply_orchestrator("TASK-V269", "submit", "operator", {}) is None
    mark_pending.assert_called_once_with("TASK-V269")
    legacy_review.assert_not_called()
    legacy_rag.assert_not_called()


def test_java_root_bound_endpoint_returns_settled_and_fail_closed_revision(tmp_path):
    java = shutil.which("java")
    if not java:
        pytest.skip("Java runtime unavailable")

    package, _ = graph_case()
    request = {
        "schema": "v269.system_review.request.v1",
        "taskId": "TASK-JAVA",
        "productId": package["productId"],
        "semanticContractHash": package["PlanGraph"]["contractHash"],
        "DecisionGraph": package["DecisionGraph"],
        "PlanGraph": package["PlanGraph"],
        "OperationGraph": package["OperationGraph"],
        "baselineFacts": package["factValues"],
        "targetFacts": {"roas": {"value": 2.0, "unit": "ratio", "sourceRef": "fact:metric:roas"}},
        "frozenAtMillis": 1000,
        "observedAtMillis": 86_500_000,
        "successfulNodeHashes": [],
    }
    input_path = tmp_path / "request.json"
    input_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    harness = tmp_path / "V269EndpointProbe.java"
    harness.write_text(
        '''package com.zcentury.v24;
import java.nio.file.*;
import java.util.*;
public final class V269EndpointProbe {
 public static void main(String[] args) throws Exception {
  Path state=Path.of(args[1]);
  new AuthorityGenerationStore(state).status();
  var request=Json.object(Json.parse(Files.readString(Path.of(args[0]))));
  var settled=V269SystemReviewEndpoint.evaluate(request);
  if(!"SETTLED".equals(settled.get("aggregateDecision")))throw new IllegalStateException("expected_settled:"+settled);
  if(!Boolean.TRUE.equals(settled.get("rootBound")))throw new IllegalStateException("root_not_bound");
  if(Boolean.TRUE.equals(settled.get("productionMutationPerformed")))throw new IllegalStateException("mutation_performed");
  if(Boolean.TRUE.equals(settled.get("ragFeedbackPerformed")))throw new IllegalStateException("rag_feedback_performed");
  var breached=Json.object(Json.parse(Json.canonical(request)));
  breached.put("targetFacts",Map.of("roas",Map.of("value",0.0,"unit","ratio","sourceRef","fact:metric:roas")));
  var adjusted=V269SystemReviewEndpoint.evaluate(breached);
  if(!"ADJUSTMENT_REQUIRED".equals(adjusted.get("aggregateDecision")))throw new IllegalStateException("expected_adjustment:"+adjusted);
  var directive=Json.object(adjusted.get("revisionDirective"));
  if(!"FULL_GRAPH_COMPATIBILITY".equals(directive.get("scopeMode")))throw new IllegalStateException("missing_fail_closed_full_scope:"+directive);
  System.out.println(Json.canonical(Map.of("settled",settled,"adjusted",adjusted)));
 }
}''',
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[1]
    sources = sorted((root / "java-control-plane/src/main/java/com/zcentury/v24").glob("*.java"))
    build = tmp_path / "classes"
    build.mkdir()
    compile_result = subprocess.run(
        [java, "-m", "jdk.compiler/com.sun.tools.javac.Main", "--release", "17", "-d", str(build), *[str(path) for path in sources], str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    env = dict(os.environ)
    env["V24_AUTHORITY_GENERATION_STATE"] = str(tmp_path / "authority-generation.json")
    result = subprocess.run(
        [java, "-cp", str(build), "com.zcentury.v24.V269EndpointProbe", str(input_path), env["V24_AUTHORITY_GENERATION_STATE"]],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["settled"]["schema"] == review.JAVA_RECEIPT_SCHEMA
    assert value["adjusted"]["revisionDirective"]["scopeMode"] == "FULL_GRAPH_COMPATIBILITY"
