from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src" / "services" / "agent_token_runtime_v22520_service.py"
ALIAS = ROOT / "src" / "services" / "agent_token_runtime_v225_service.py"
BRIDGE = ROOT / "src" / "services" / "agent2_hash_proof_bridge_v22520_service.py"
RECOVERY = ROOT / "src" / "services" / "agent2_runtime_v22520_service.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_changed_python_sources_parse() -> None:
    for path in (RUNTIME, ALIAS, BRIDGE, RECOVERY):
        ast.parse(_read(path), filename=str(path))


def test_active_alias_routes_agent2_to_v22520() -> None:
    source = _read(ALIAS)
    assert "agent_token_runtime_v22520_service import *" in source
    assert "agent_token_runtime_hash_exact_v2259_service import *" not in source


def test_agent2_acceptance_is_exact_hash_not_package_only() -> None:
    source = _read(RUNTIME)
    for marker in (
        'AGENT_TOKEN_RUNTIME_VERSION = "22.5.20"',
        'exactOutputIdentity"] = "itemExecutionId+inputContentHash"',
        'raw_id == expected_id and raw_hash == expected_hash',
        'packageIdAcceptanceAllowed=False',
        'artifact_type=AGENT2_EXACT_OUTPUT_TYPE',
        'raw_batch_output_ref=raw_ref',
        'store_raw_batch_output(',
        'call_json_exact_artifact(',
    ):
        assert marker in source
    assert "drafts.get(package_id)" not in source
    assert 'return exact[0], "exact"' in source
    assert 'return None, "contract_invalid_identity"' in source


def test_singleton_retry_is_only_for_true_missing() -> None:
    source = _read(RUNTIME)
    assert 'if outcome == "true_missing":' in source
    assert 'retryMode": "singleton_true_missing"' in source
    assert "agent2_exact_hash_output_contract_invalid" in source
    assert "agent2_exact_output_missing_after_singleton_retry" in source
    assert source.count("retry_attempt=1") == 1


def test_history_discovery_keeps_legacy_hash_validator_as_authority() -> None:
    source = _read(BRIDGE)
    for marker in (
        "legacy.resolve_agent2_hash_execution_for_input(candidate_ref)",
        '"agent2_hash_historical_execution_ambiguous"',
        "hashValidationRelaxed=False",
        'recoveryMode="historical_exact_input"',
        'for key in ("packageId", "storeId", "productId", "dataVersion", "actionFamily")',
    ):
        assert marker in source
    assert "status='accepted'" not in source  # accepted lookup stays inside legacy strict validator


def test_recovery_never_reruns_upstream_stages() -> None:
    source = _read(RECOVERY)
    for marker in (
        '"agent1Rerun": False',
        '"actionPackRerun": False',
        '"agent2InputProjectionRerun": False',
        '_NO_PLAN_MARKER = "agent2_draft_returned_no_plan"',
        "reconcile_agent2_hash_proof_dead_letters_v22520",
        "requeue_agent2_true_missing_dead_letters_v22520",
        "repair_agent2_dead_letters_v22520",
    ):
        assert marker in source
    assert "run_agent1" not in source
    assert "ensure_agent2_draft_input_ref" not in source
