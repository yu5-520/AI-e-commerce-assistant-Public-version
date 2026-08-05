from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "src" / "services"

CURRENT_FILES = [
    "route_action_department_matrix_v1915_service.py",
    "agent_runtime_contract_v2010_service.py",
    "agent_runtime_contract_v2141_service.py",
    "action_pack_core_v20_service.py",
    "action_plan_ir_v214_service.py",
    "agent2_provenance_v2141_service.py",
    "agent2_runtime_resilience_v2143_service.py",
    "agent2_action_plan_core_v20_service.py",
    "real_product_judgment_agent_v196_service.py",
    "pipeline_agent1_microbatch_v20101_service.py",
    "pipeline_action_microbatch_v205_service.py",
    "agent_pipeline_item_worker_v2010_service.py",
    "agent_pipeline_governance_v213_service.py",
    "pipeline_runtime_recovery_v2028_service.py",
    "pipeline_runtime_recovery_v2143_service.py",
    "sop_builder_core_v20_service.py",
    "pipeline_sop_task_pool_v2010_service.py",
    "action_authority_v21_service.py",
    "action_authority_v214_service.py",
    "task_pool_admission_core_v20_service.py",
    "task_detail_snapshot_v2024_service.py",
    "task_authority_decision_v21_service.py",
    "pending_authority_migration_v21_service.py",
    "station_queue_worker_service.py",
    "station_adapter_service.py",
    "station_registry_service.py",
]


def source(name: str) -> str:
    return (SERVICES / name).read_text(encoding="utf-8")


def test_current_agent_mainline_files_are_valid_python() -> None:
    for name in CURRENT_FILES:
        ast.parse(source(name), filename=name)


def test_v214_runtime_contract_preserves_semantic_agent_chain() -> None:
    semantic = source("agent_runtime_contract_v2010_service.py")
    runtime = source("agent_runtime_contract_v2141_service.py")
    task_pool = source("task_pool_admission_core_v20_service.py")
    authority = source("action_authority_v214_service.py")
    assert 'AGENT_RUNTIME_CONTRACT_VERSION = "20.28"' in semantic
    assert 'SOURCE_PIPELINE_ITEMS_ONLY = "pipeline_items.payload_only"' in semantic
    assert 'AGENT_RUNTIME_CONTRACT_VERSION = "21.4.1"' in runtime
    assert 'TASK_POOL_ADMISSION_CORE_VERSION = "21.4.1"' in task_pool
    assert 'ACTION_AUTHORITY_VERSION = "21.4.0"' in authority
    assert "rejected_by_semantic_decision_contract" in task_pool
    assert "rejected_by_v21_4_authorization_contract" in task_pool
    assert "rejected_by_agent2_item_proof_gate" in task_pool
    assert "apply_authorization_to_decision" in task_pool
    assert "single_adjustment_limit_exceeded" in authority
    assert "daily_adjustment_limit_exceeded" in authority
    assert "rolling_24h_limit_exceeded" in authority
    assert "target_roas_below_safety_floor" in authority
    assert "genericAdjustmentRateUsedAsBudget" in authority
    assert "familyNameUsedAsDirection" in authority
    assert "sopSource_v20_27" not in task_pool


def test_agent1_canonical_identity_binding_is_active() -> None:
    agent1 = source("real_product_judgment_agent_v196_service.py")
    assert 'REAL_PRODUCT_AGENT_V196_VERSION = "20.28"' in agent1
    assert "def _correlation_id" in agent1
    assert '"correlationId": _correlation_id(bundle)' in agent1
    assert "canonicalProductId" in agent1
    assert "uniqueProductAlias" in agent1


def test_agent2_item_provenance_operation_plan_and_resilience_are_active() -> None:
    agent2 = source("agent2_action_plan_core_v20_service.py")
    provenance = source("agent2_provenance_v2141_service.py")
    operation = source("action_plan_ir_v214_service.py")
    resilience = source("agent2_runtime_resilience_v2143_service.py")
    pipeline = source("pipeline_action_microbatch_v205_service.py")
    detail = source("task_detail_snapshot_v2024_service.py")
    assert 'AGENT2_ACTION_PLAN_CORE_VERSION = "21.4.1"' in agent2
    assert 'AGENT2_PROVENANCE_VERSION = "21.4.1"' in provenance
    assert 'ACTION_PLAN_IR_VERSION = "21.4.0"' in operation
    assert 'AGENT2_LEASE_VERSION = "21.4.2"' in resilience
    assert 'AGENT2_FAILURE_GOVERNANCE_VERSION = "21.4.3"' in resilience
    assert 'AGENT2_DEAD_LETTER_STAGE = "agent2_dead_letter"' in resilience
    assert "recover_stale_agent2_claims" in resilience
    assert "schedule_agent2_failure" in resilience
    assert "itemProvenance" in provenance
    assert "replayFingerprint" in provenance
    assert "genericAdjustmentRateUsedAsBudget" in operation
    assert 'PIPELINE_ACTION_MICROBATCH_VERSION = "21.4.3"' in pipeline
    assert 'AGENT2_OUTPUT_INVALID_STAGE = "agent2_output_invalid"' in pipeline
    assert "STAGE_ORDER.setdefault(AGENT2_OUTPUT_INVALID_STAGE, 77)" in pipeline
    assert "claim_agent2_items" in pipeline
    assert "retryScheduledItemCount" in pipeline
    assert "deadLetteredItemCount" in pipeline
    assert 'TASK_DETAIL_SNAPSHOT_VERSION = "21.4.0"' in detail
    assert '"authorizationDecision": authorization' in detail
    assert '"operationPlan": operation_plan' in detail
    assert '"agent2ExecutionProof": proof' in detail


def test_runtime_breakpoint_recovery_is_wired_to_governed_scheduler() -> None:
    legacy_recovery = source("pipeline_runtime_recovery_v2028_service.py")
    recovery = source("pipeline_runtime_recovery_v2143_service.py")
    worker = source("station_queue_worker_service.py")
    governance = source("agent_pipeline_governance_v213_service.py")
    assert 'PIPELINE_RUNTIME_RECOVERY_VERSION = "20.28"' in legacy_recovery
    assert 'PIPELINE_RUNTIME_RECOVERY_VERSION = "21.4.3"' in recovery
    assert "recover_stale_agent2_claims" in recovery
    assert "recover_pipeline_runtime_breakpoints" in worker
    assert 'STATION_QUEUE_WORKER_VERSION = "21.3"' in worker
    assert "select_runnable_data_version" in worker
    assert "force_new_snapshot=False" in worker
    assert 'AGENT_PIPELINE_GOVERNANCE_VERSION = "21.3"' in governance
    assert "oldest_at ASC" in governance
    assert "recover_stale_agent2_claims" in governance
    assert "retry_after_due_only" in governance


def test_current_cores_do_not_restore_legacy_runtime() -> None:
    forbidden = {
        "action_pack_core_v20_service.py": ["action_parameter_enrichment_v199_service", "product_judgment_packages_v15"],
        "agent2_action_plan_core_v20_service.py": ["action_plan_judgment_agent_v1913_service", "product_judgment_packages_v15"],
        "task_pool_admission_core_v20_service.py": ["task_pool_admission_bridge_v199_service"],
        "pipeline_agent1_microbatch_v203_service.py": ["dual_agent_product_task_service", "agent_product_judgments_v15", "_save_raw_judgments"],
    }
    for name, markers in forbidden.items():
        text = source(name)
        for marker in markers:
            assert marker not in text, f"{name} still depends on {marker}"
