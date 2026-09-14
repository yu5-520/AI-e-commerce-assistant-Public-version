from __future__ import annotations

import json

from build_seed_pack_v2 import build
from core import CONDITIONS, assert_paired_isolation
from smoke import matched_condition
from v26_evidence_adapter import V26AuthorityEvidenceAdapter


def run():
    pack = build()
    sut = V26AuthorityEvidenceAdapter()
    positives = [c for c in pack["cases"] if c["evaluation_only"]["case_kind"] == "positive"]
    negatives = [c for c in pack["cases"] if c["evaluation_only"]["case_kind"] == "negative"]
    blocked = 0
    false_blocks = 0
    replay_count = 0

    for case in positives:
        proposal = case["evaluation_only"]["fixture_proposal"]
        paired = [sut.replay(case, proposal, condition) for condition in CONDITIONS]
        replay_count += len(paired)
        assert_paired_isolation(paired)
        by_condition = {item["condition"]: item for item in paired}
        matched = matched_condition(case["evaluation_only"]["bias_family"])
        if not by_condition[matched]["blocked"] or by_condition[matched]["realized"]:
            raise RuntimeError(f"matched_authority_failed:{case['case_id']}")
        if by_condition["full_authority"]["realized"]:
            raise RuntimeError(f"full_authority_failed:{case['case_id']}")
        blocked += 1

    for case in negatives:
        proposal = case["evaluation_only"]["fixture_proposal"]
        matched = matched_condition(case["evaluation_only"]["bias_family"])
        replay = sut.replay(case, proposal, matched)
        replay_count += 1
        if replay["blocked"]:
            false_blocks += 1

    if false_blocks:
        raise RuntimeError(f"negative_false_blocks:{false_blocks}")

    return {
        "status": "PASS",
        "sut_mode": sut.sut_mode,
        "empirical_model_evidence": False,
        "field_authority_version": sut.receipt["version"],
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "matched_positive_blocks": blocked,
        "negative_false_blocks": false_blocks,
        "total_contract_replays": replay_count,
        "note": "Fixture proposals validate selective V26 authority behavior only; no paid model generation or full app runtime is used.",
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
