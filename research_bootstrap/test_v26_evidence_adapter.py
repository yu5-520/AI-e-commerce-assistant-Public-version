import unittest

from build_seed_pack_v2 import build
from core import CONDITIONS, assert_no_condition_leakage, assert_paired_isolation
from smoke import matched_condition
from v26_evidence_adapter import V26AuthorityEvidenceAdapter


class V26EvidenceAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = build()
        cls.sut = V26AuthorityEvidenceAdapter()

    def test_all_positive_cases_show_selective_v26_authority(self):
        positives = [c for c in self.pack["cases"] if c["evaluation_only"]["case_kind"] == "positive"]
        self.assertEqual(len(positives), 18)
        for case in positives:
            proposal = case["evaluation_only"]["fixture_proposal"]
            paired = [self.sut.replay(case, proposal, condition) for condition in CONDITIONS]
            assert_paired_isolation(paired)
            for item in paired:
                assert_no_condition_leakage(item["sanitized_evidence"])
                self.assertEqual(item["sut_mode"], "v26.1-contract-probe")
                self.assertFalse(item["empirical_model_evidence"])
            by_condition = {item["condition"]: item for item in paired}
            matched = matched_condition(case["evaluation_only"]["bias_family"])
            self.assertTrue(by_condition["baseline_runtime"]["realized"])
            self.assertFalse(by_condition[matched]["realized"])
            self.assertTrue(by_condition[matched]["blocked"])
            self.assertFalse(by_condition["full_authority"]["realized"])
            for condition in CONDITIONS:
                if condition not in {"baseline_runtime", matched, "full_authority"}:
                    self.assertTrue(by_condition[condition]["realized"])

    def test_all_negative_cases_pass_real_v26_owner_path(self):
        negatives = [c for c in self.pack["cases"] if c["evaluation_only"]["case_kind"] == "negative"]
        self.assertEqual(len(negatives), 18)
        for case in negatives:
            proposal = case["evaluation_only"]["fixture_proposal"]
            condition = matched_condition(case["evaluation_only"]["bias_family"])
            replay = self.sut.replay(case, proposal, condition)
            self.assertFalse(replay["generated"])
            self.assertFalse(replay["blocked"])
            self.assertTrue(replay["committed"])
            probes = replay["raw_evidence"]["v26_probe_receipt"]["probes"]
            self.assertTrue(probes)
            self.assertEqual(probes[0]["probe_mode"], "real-v26-owner-path")


if __name__ == "__main__":
    unittest.main()
