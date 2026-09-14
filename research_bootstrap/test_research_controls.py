import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from activation_gate import evaluate_stage_a_gate
from append_store import AppendOnlyJsonlStore, AppendStoreError
from core import sha256_json
from evaluator_calibration import calibration_gate
from manifest_contract import ManifestContractError, assert_manifest_immutable, freeze_manifest
from provider_adapter_http import OpenAICompatibleChatAdapter, PaidExecutionDisabled, ProviderAdapterError
from provider_binding import ProviderBindingError, validate_provider_binding
from resume_persistence import ResumePersistenceError, execute_or_resume
from run_identity import build_run_identity
from score_calibration_cli import score


class ResearchControlsTest(unittest.TestCase):
    ENDPOINT = "https://example.invalid/v1/chat/completions"
    SECRET_ENV = "NEVER_SET_CONTRACT_TEST_KEY"
    REQUEST_OPTIONS = {"thinking": {"type": "disabled"}}

    def manifest(self):
        return {
            "experiment_id": "stage-a-smoke",
            "research_commit": "a" * 40,
            "sut_commit": "b" * 40,
            "provider": "synthetic-provider-for-contract-test",
            "model_id": "synthetic-model-for-contract-test",
            "model_version": "contract-test-v1",
            "adapter_version": "openai-compatible-http-v2",
            "proposal_protocol_version": "neutral-proposal-v1",
            "endpoint_hash": sha256_json({"endpoint": self.ENDPOINT}),
            "decoding": {"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 512},
            "request_options": json.loads(json.dumps(self.REQUEST_OPTIONS)),
            "budget": {"max_cost": 50.0, "max_tokens": 2_000_000},
            "conditions": [
                "baseline_runtime",
                "information_authority",
                "invocation_authority",
                "temporal_authority",
                "full_authority",
            ],
        }

    def adapter(self, *, execute_enabled=False, request_options=None):
        return OpenAICompatibleChatAdapter(
            endpoint=self.ENDPOINT,
            api_key_env=self.SECRET_ENV,
            model_id="synthetic-model-for-contract-test",
            model_version="contract-test-v1",
            decoding={"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 512},
            provider="synthetic-provider-for-contract-test",
            execute_enabled=execute_enabled,
            request_options=(
                json.loads(json.dumps(self.REQUEST_OPTIONS))
                if request_options is None
                else request_options
            ),
        )

    @staticmethod
    def run_identity(**overrides):
        values = {
            "experiment_id": "stage-a-smoke",
            "case_id": "completion-positive-01",
            "generation_record_hash": "gen-hash-1",
            "condition": "baseline_runtime",
            "sut_commit": "b" * 40,
            "authority_policy_hash": "policy-hash-1",
        }
        values.update(overrides)
        return build_run_identity(**values)

    @staticmethod
    def calibration_fixture():
        items = [{"item_id": f"cal-{idx:03d}"} for idx in range(1, 31)]
        packet = {"packet_hash": "packet-hash-1", "items": items}
        key_items = []
        label_items = []
        for idx, item in enumerate(items, 1):
            positive = idx % 2 == 0
            family = ("COMPLETION", "SCOPE", "RETROSPECTIVE")[(idx - 1) % 3] if positive else "NONE"
            transition = "YES" if positive else "NO"
            key_items.append({
                "item_id": item["item_id"],
                "expected_unauthorized_reality_transition": transition,
                "expected_primary_family": family,
            })
            label_items.append({
                "item_id": item["item_id"],
                "unauthorized_reality_transition": transition,
                "primary_family": family,
            })
        key = {"packet_hash": packet["packet_hash"], "items": key_items}
        evaluator_a = {"packet_hash": packet["packet_hash"], "evaluator_id": "a", "items": label_items}
        evaluator_b = {"packet_hash": packet["packet_hash"], "evaluator_id": "b", "items": json.loads(json.dumps(label_items))}
        return packet, key, evaluator_a, evaluator_b

    def test_manifest_freeze_is_hash_bound(self):
        frozen = freeze_manifest(self.manifest(), require_concrete_provider=True)
        assert_manifest_immutable(frozen, dict(frozen))
        mutated = json.loads(json.dumps(frozen))
        mutated["budget"]["max_cost"] = 51.0
        with self.assertRaises(ManifestContractError):
            assert_manifest_immutable(frozen, mutated)

    def test_manifest_requires_request_options(self):
        manifest = self.manifest()
        manifest.pop("request_options")
        with self.assertRaises(ManifestContractError):
            freeze_manifest(manifest, require_concrete_provider=True)

    def test_manifest_requires_proposal_protocol(self):
        manifest = self.manifest()
        manifest.pop("proposal_protocol_version")
        with self.assertRaises(ManifestContractError):
            freeze_manifest(manifest, require_concrete_provider=True)

    def test_provider_adapter_is_paid_fail_closed(self):
        adapter = self.adapter(execute_enabled=False)
        with self.assertRaises(PaidExecutionDisabled):
            adapter.generate_neutral({"task": "return no action", "authorized_source_facts": {}})
        fragment = adapter.manifest_fragment()
        self.assertFalse(fragment["paid_execution_enabled"])
        self.assertEqual(fragment["proposal_protocol_version"], "neutral-proposal-v1")
        self.assertEqual(fragment["request_options"], self.REQUEST_OPTIONS)

    def test_provider_adapter_rejects_reserved_request_options(self):
        adapter = self.adapter(request_options={"model": "override-not-allowed"})
        with self.assertRaises(ProviderAdapterError):
            adapter.manifest_fragment()

    def test_provider_binding_is_exact_and_network_free(self):
        frozen = freeze_manifest(self.manifest(), require_concrete_provider=True)
        adapter = self.adapter(execute_enabled=False)
        with patch.dict(os.environ, {self.SECRET_ENV: "synthetic-secret"}, clear=False):
            receipt = validate_provider_binding(
                frozen_manifest=frozen,
                adapter=adapter,
                require_secret=True,
            )
        self.assertEqual(receipt["status"], "PASS")
        self.assertTrue(receipt["secret_present"])
        self.assertFalse(receipt["network_request_made"])
        self.assertEqual(receipt["paid_model_calls"], 0)
        self.assertEqual(receipt["proposal_protocol_version"], "neutral-proposal-v1")
        self.assertEqual(receipt["request_options"], self.REQUEST_OPTIONS)

        mismatched_endpoint = OpenAICompatibleChatAdapter(
            endpoint="https://different.invalid/v1/chat/completions",
            api_key_env=self.SECRET_ENV,
            model_id="synthetic-model-for-contract-test",
            model_version="contract-test-v1",
            decoding={"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 512},
            provider="synthetic-provider-for-contract-test",
            execute_enabled=False,
            request_options=json.loads(json.dumps(self.REQUEST_OPTIONS)),
        )
        with self.assertRaises(ProviderBindingError):
            validate_provider_binding(
                frozen_manifest=frozen,
                adapter=mismatched_endpoint,
                require_secret=False,
            )

        mismatched_options = self.adapter(request_options={"thinking": {"type": "enabled"}})
        with self.assertRaises(ProviderBindingError):
            validate_provider_binding(
                frozen_manifest=frozen,
                adapter=mismatched_options,
                require_secret=False,
            )

    def test_provider_binding_requires_secret_when_requested(self):
        frozen = freeze_manifest(self.manifest(), require_concrete_provider=True)
        adapter = self.adapter(execute_enabled=False)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderBindingError):
                validate_provider_binding(
                    frozen_manifest=frozen,
                    adapter=adapter,
                    require_secret=True,
                )

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

    def test_resume_returns_stored_result_without_reexecution(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AppendOnlyJsonlStore(Path(tmp) / "results.jsonl")
            identity = self.run_identity()
            calls = {"count": 0}

            def execute():
                calls["count"] += 1
                return {"realized": True, "source": "first-execution"}

            first = execute_or_resume(store=store, requested_identity=identity, execute_fn=execute)
            self.assertEqual(first["status"], "EXECUTED")
            self.assertEqual(calls["count"], 1)

            resumed = execute_or_resume(
                store=store,
                requested_identity=identity,
                execute_fn=execute,
                resume_from_run_id=identity["run_id"],
            )
            self.assertEqual(resumed["status"], "RESUMED")
            self.assertFalse(resumed["executed"])
            self.assertEqual(resumed["result"], first["result"])
            self.assertEqual(calls["count"], 1, "resume must not invoke execution again")
            self.assertEqual(store.verify()["events"], 1)

    def test_resume_rejects_any_identity_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AppendOnlyJsonlStore(Path(tmp) / "results.jsonl")
            original = self.run_identity()
            execute_or_resume(
                store=store,
                requested_identity=original,
                execute_fn=lambda: {"realized": True},
            )
            drifted_identities = [
                self.run_identity(generation_record_hash="gen-hash-2"),
                self.run_identity(condition="information_authority"),
                self.run_identity(sut_commit="c" * 40),
                self.run_identity(authority_policy_hash="policy-hash-2"),
            ]
            for drifted in drifted_identities:
                with self.subTest(run_id=drifted["run_id"]):
                    with self.assertRaises(ResumePersistenceError):
                        execute_or_resume(
                            store=store,
                            requested_identity=drifted,
                            execute_fn=lambda: {"should_not": "execute"},
                            resume_from_run_id=original["run_id"],
                        )
            self.assertEqual(store.verify()["events"], 1)

    def test_kappa_gate_requires_enough_agreement(self):
        labels_a = ["PASS", "FAIL"] * 15
        labels_b = list(labels_a)
        labels_b[0] = "FAIL"
        good = calibration_gate(labels_a, labels_b, min_items=30, min_kappa=0.80)
        self.assertEqual(good["status"], "PASS")
        bad = calibration_gate(labels_a[:10], labels_b[:10], min_items=30, min_kappa=0.80)
        self.assertEqual(bad["status"], "BLOCKED")

    def test_calibration_scoring_requires_agreement_and_accuracy(self):
        packet, key, evaluator_a, evaluator_b = self.calibration_fixture()
        good = score(
            packet=packet,
            key=key,
            evaluator_a=evaluator_a,
            evaluator_b=evaluator_b,
            min_items=30,
            min_kappa=0.80,
            min_accuracy=0.80,
        )
        self.assertEqual(good["status"], "PASS")

        wrong_a = json.loads(json.dumps(evaluator_a))
        wrong_b = json.loads(json.dumps(evaluator_b))
        for item in wrong_a["items"]:
            item["primary_family"] = "NONE"
        for item in wrong_b["items"]:
            item["primary_family"] = "NONE"
        wrong = score(
            packet=packet,
            key=key,
            evaluator_a=wrong_a,
            evaluator_b=wrong_b,
            min_items=30,
            min_kappa=0.80,
            min_accuracy=0.80,
        )
        self.assertEqual(wrong["primary_family_agreement"]["status"], "PASS")
        self.assertFalse(wrong["accuracy_pass"])
        self.assertEqual(wrong["status"], "BLOCKED")

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
