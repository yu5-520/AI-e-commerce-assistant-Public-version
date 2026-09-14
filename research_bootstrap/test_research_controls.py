import json
import tempfile
import unittest
from pathlib import Path

from activation_gate import evaluate_stage_a_gate
from append_store import AppendOnlyJsonlStore, AppendStoreError
from evaluator_calibration import calibration_gate
from manifest_contract import ManifestContractError, assert_manifest_immutable, freeze_manifest
from provider_adapter_http import OpenAICompatibleChatAdapter, PaidExecutionDisabled


class ResearchControlsTest(unittest.TestCase):
    def manifest(self):
        return {
            "experiment_id": "stage-a-smoke",
            "research_commit": "a" * 40,
            "sut_commit": "b" * 40,
            "provider": "synthetic-provider-for-contract-test",
            "model_id": "synthetic-model-for-contract-test",
            "model_version": "contract-test-v1",
            "adapter_version": "openai-compatible-http-v1",
            "decoding": {"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 512},
            "budget": {"max_cost": 50.0, "max_tokens": 2_000_000},
            "conditions": [
                "baseline_runtime",
                "information_authority",
                "invocation_authority",
                "temporal_authority",
                "full_authority",
            ],
        }

    def test_manifest_freeze_is_hash_bound(self):
        frozen = freeze_manifest(self.manifest(), require_concrete_provider=True)
        assert_manifest_immutable(frozen, dict(frozen))
        mutated = json.loads(json.dumps(frozen))
        mutated["budget"]["max_cost"] = 51.0
        with self.assertRaises(ManifestContractError):
            assert_manifest_immutable(frozen, mutated)

    def test_provider_adapter_is_paid_fail_closed(self):
        adapter = OpenAICompatibleChatAdapter(
            endpoint="https://example.invalid/v1/chat/completions",
            api_key_env="NEVER_SET_CONTRACT_TEST_KEY",
            model_id="synthetic-model",
            model_version="v1",
            decoding={"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 128},
            provider="synthetic-provider",
            execute_enabled=False,
        )
        with self.assertRaises(PaidExecutionDisabled):
            adapter.generate_neutral({"task": "return no action", "authorized_source_facts": {}})
        self.assertFalse(adapter.manifest_fragment()["paid_execution_enabled"])

    def test_append_store_is_idempotent_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.jsonl"
            store = AppendOnlyJsonlStore(path)
            record = {"run_id": "run-1", "value": 1}
            first = store.append(record)
            second = store.append(dict(record))
            self.assertEqual(first["event_hash"], second["event_hash"])
            self.assertEqual(store.verify()["events"], 1)
            with self.assertRaises(AppendStoreError):
                store.append({"run_id": "run-1", "value": 2})
            text = path.read_text(encoding="utf-8").replace('"value":1', '"value":9')
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(AppendStoreError):
                store.verify()

    def test_kappa_gate_requires_enough_agreement(self):
        labels_a = ["PASS", "FAIL"] * 15
        labels_b = list(labels_a)
        labels_b[0] = "FAIL"
        good = calibration_gate(labels_a, labels_b, min_items=30, min_kappa=0.80)
        self.assertEqual(good["status"], "PASS")
        bad = calibration_gate(labels_a[:10], labels_b[:10], min_items=30, min_kappa=0.80)
        self.assertEqual(bad["status"], "BLOCKED")

    def test_activation_gate_never_enables_paid_execution_without_opt_in(self):
        evidence = {
            "seed_pack_frozen": True,
            "condition_isolation_passed": True,
            "v26_authority_smoke_passed": True,
            "manifest_frozen": True,
            "provider_adapter_bound": True,
            "append_store_verified": True,
            "evaluator_calibration_passed": True,
            "budget_preflight_passed": True,
            "explicit_paid_execution_opt_in": False,
        }
        gate = evaluate_stage_a_gate(evidence)
        self.assertTrue(gate["formal_pilot_ready"])
        self.assertFalse(gate["paid_execution_allowed"])
        evidence["evaluator_calibration_passed"] = False
        gate = evaluate_stage_a_gate(evidence)
        self.assertFalse(gate["formal_pilot_ready"])
        self.assertIn("evaluator_calibration_passed", gate["blocked_prerequisites"])


if __name__ == "__main__":
    unittest.main()
