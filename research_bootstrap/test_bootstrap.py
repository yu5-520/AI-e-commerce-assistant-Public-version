import copy
import unittest

from core import CONDITIONS, DummyModelAdapter, MockSUT, assert_no_condition_leakage, assert_paired_isolation
from fixtures import CASES
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


if __name__ == "__main__":
    unittest.main()
