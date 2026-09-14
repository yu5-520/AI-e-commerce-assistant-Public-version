import copy
import unittest

from build_seed_pack_v2 import build
from proposal_authority_classifier import classify_proposal


class ProposalAuthorityClassifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = build()["cases"]

    def case(self, case_id):
        return next(case for case in self.cases if case["case_id"] == case_id)

    def raw_from_fixture(self, case):
        fixture = copy.deepcopy(case["evaluation_only"]["fixture_proposal"])
        for effect in fixture.get("effects", []):
            effect.pop("authorized", None)
        fixture["assessment"] = "neutral assessment"
        return fixture

    def test_positive_and_negative_reference_authorization(self):
        pairs = [
            ("completion-positive-01-refund-rate", False),
            ("completion-negative-01-refund-rate", True),
            ("scope-positive-01-roas-to-sentiment", False),
            ("scope-negative-01-roas-to-sentiment", True),
            ("retrospective-positive-01-budget-decision", False),
            ("retrospective-negative-01-budget-decision", True),
        ]
        for case_id, expected in pairs:
            with self.subTest(case_id=case_id):
                case = self.case(case_id)
                result = classify_proposal(case, self.raw_from_fixture(case))
                effect = result["classified_proposal"]["effects"][0]
                self.assertEqual(effect["authorized"], expected)
                self.assertEqual(
                    result["classification_receipts"][0]["basis"],
                    "hidden_case_authorization_reference",
                )

    def test_model_supplied_authorization_is_ignored(self):
        case = self.case("completion-positive-01-refund-rate")
        proposal = self.raw_from_fixture(case)
        proposal["effects"][0]["authorized"] = True
        result = classify_proposal(case, proposal)
        self.assertFalse(result["classified_proposal"]["effects"][0]["authorized"])

    def test_unanticipated_effect_uses_general_contract_rules(self):
        case = self.case("completion-positive-01-refund-rate")
        proposal = {
            "assessment": "propose an unsupported fact",
            "effects": [{"type": "fact_commit", "key": "new_hidden_metric", "value": 0.5}],
        }
        result = classify_proposal(case, proposal)
        receipt = result["classification_receipts"][0]
        self.assertFalse(receipt["authorized"])
        self.assertEqual(receipt["basis"], "fact_not_authorized_by_source")

    def test_classifier_does_not_depend_on_bias_family_label(self):
        case = self.case("scope-positive-01-roas-to-sentiment")
        proposal = self.raw_from_fixture(case)
        original = classify_proposal(case, proposal)
        relabeled = copy.deepcopy(case)
        relabeled["evaluation_only"]["bias_family"] = "retrospective"
        relabeled["evaluation_only"]["expected_primary_authority"] = "temporal"
        changed = classify_proposal(relabeled, proposal)
        self.assertEqual(original["classified_proposal"], changed["classified_proposal"])
        self.assertEqual(original["classification_receipts"], changed["classification_receipts"])


if __name__ == "__main__":
    unittest.main()
