import copy
import unittest

from core import CONDITIONS, DummyModelAdapter, MockSUT, assert_no_condition_leakage, assert_paired_isolation, policy_for_condition, sha256_json
from fixtures import CASES
from model_adapter import FixtureNeutralAdapter, assert_model_view_clean, freeze_generation_record
from run_identity import assert_resume_compatible, build_run_identity
from smoke import matched_condition


class BootstrapResearchHarnessTest(unittest.TestCase):
    def setUp(self):
        self.model = DummyModelAdapter()
        self.sut = MockSUT()

    def test_positive_selective_authority_structure(self):
        for case in [c for c in CASES if c["case_kind"] == "positive"]:
            proposal = self.model.generate(case)["structured_output"]
            paired = [self.sut.replay(case, proposal, condition) for condition in CONDITIONS]
            assert_paired_isolation(paired)
            by_condition = {item["condition"]: item for item in paired}
            matched = matched_condition(case["bias_family"])
            self.assertTrue(by_condition["baseline_runtime"]["realized"])
            self.assertFalse(by_condition[matched]["realized"])
            self.assertFalse(by_condition["full_authority"]["realized"])
            for condition in CONDITIONS:
                if condition not in {"baseline_runtime", matched, "full_authority"}:
                    self.assertTrue(by_condition[condition]["realized"])

    def test_negative_authorized_action_not_blocked(self):
        for case in [c for c in CASES if c["case_kind"] == "negative"]:
            proposal = self.model.generate(case)["structured_output"]
            replay = self.sut.replay(case, proposal, matched_condition(case["bias_family"]))
            self.assertFalse(replay["generated"])
            self.assertFalse(replay["blocked"])

    def test_condition_does_not_modify_frozen_proposal(self):
        case = next(c for c in CASES if c["case_kind"] == "positive")
        proposal = self.model.generate(case)["structured_output"]
        frozen = copy.deepcopy(proposal)
        for condition in CONDITIONS:
            self.sut.replay(case, proposal, condition)
            self.assertEqual(proposal, frozen)

    def test_sanitized_evidence_has_no_condition_leakage(self):
        case = next(c for c in CASES if c["case_kind"] == "positive")
        proposal = self.model.generate(case)["structured_output"]
        for condition in CONDITIONS:
            replay = self.sut.replay(case, proposal, condition)
            assert_no_condition_leakage(replay["sanitized_evidence"])

    def test_neutral_adapter_receives_model_view_only(self):
        model_view = {
            "domain": "ecommerce",
            "task_contract": {"goal": "assess budget"},
            "authorized_source_facts": {"roas": 1.2},
            "pre_state": {},
            "task": "Return one assessment.",
        }
        assert_model_view_clean(model_view)
        adapter = FixtureNeutralAdapter()
        response = adapter.generate_neutral(model_view)
        frozen = freeze_generation_record(case_id="case-1", model_view=model_view, response=response)
        self.assertEqual(frozen["model_input_hash"], sha256_json(model_view))
        self.assertEqual(frozen["proposal_hash"], sha256_json({"effects": []}))
        with self.assertRaises(AssertionError):
            adapter.generate_neutral({**model_view, "bias_family": "completion"})

    def test_run_identity_resume_is_fail_closed(self):
        condition = "information_authority"
        authority_hash = sha256_json(policy_for_condition(condition))
        first = build_run_identity(
            experiment_id="exp-1",
            case_id="case-1",
            generation_record_hash="sha256:generation",
            condition=condition,
            sut_commit="f9e131b",
            authority_policy_hash=authority_hash,
        )
        second = build_run_identity(
            experiment_id="exp-1",
            case_id="case-1",
            generation_record_hash="sha256:generation",
            condition=condition,
            sut_commit="f9e131b",
            authority_policy_hash=authority_hash,
        )
        assert_resume_compatible(first, second)
        changed = dict(second)
        changed["run_id"] = "sha256:different"
        with self.assertRaises(RuntimeError):
            assert_resume_compatible(first, changed)


if __name__ == "__main__":
    unittest.main()
