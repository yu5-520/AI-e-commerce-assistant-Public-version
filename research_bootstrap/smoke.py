from __future__ import annotations

import json

from core import CONDITIONS, DummyModelAdapter, MockSUT, assert_no_condition_leakage, assert_paired_isolation, cost_preflight
from fixtures import CASES


def matched_condition(bias_family: str) -> str:
    return {
        "completion": "information_authority",
        "scope": "invocation_authority",
        "retrospective": "temporal_authority",
    }[bias_family]


def run() -> dict:
    model = DummyModelAdapter()
    sut = MockSUT()
    evidence = []

    positive_cases = [case for case in CASES if case["case_kind"] == "positive"]
    negative_cases = [case for case in CASES if case["case_kind"] == "negative"]

    for case in positive_cases:
        generation = model.generate(case)
        proposal = generation["structured_output"]
        paired = [sut.replay(case, proposal, condition) for condition in CONDITIONS]
        assert_paired_isolation(paired)
        for item in paired:
            assert_no_condition_leakage(item["sanitized_evidence"])

        baseline = next(item for item in paired if item["condition"] == "baseline_runtime")
        matched = next(item for item in paired if item["condition"] == matched_condition(case["bias_family"]))
        full = next(item for item in paired if item["condition"] == "full_authority")
        unmatched = [
            item for item in paired
            if item["condition"] not in {"baseline_runtime", matched["condition"], "full_authority"}
        ]

        assert baseline["generated"] is True
        assert baseline["realized"] is True
        assert matched["generated"] is True
        assert matched["realized"] is False
        assert matched["blocked"] is True
        assert full["realized"] is False
        assert all(item["realized"] is True for item in unmatched)
        evidence.extend(paired)

    for case in negative_cases:
        generation = model.generate(case)
        proposal = generation["structured_output"]
        condition = matched_condition(case["bias_family"])
        replay = sut.replay(case, proposal, condition)
        assert replay["generated"] is False
        assert replay["realized"] is False
        assert replay["blocked"] is False, "authorized negative case was falsely blocked"
        assert_no_condition_leakage(replay["sanitized_evidence"])
        evidence.append(replay)

    budget = cost_preflight(
        estimated_tokens=198 * 8_000,
        price_per_million_tokens=20.0,
        max_tokens=2_000_000,
        max_cost=50.0,
    )
    assert budget["allowed"] is True

    return {
        "pilot_ready_bootstrap": True,
        "sut": "mock-only",
        "formal_pilot_ready": False,
        "positive_cases_checked": len(positive_cases),
        "negative_cases_checked": len(negative_cases),
        "paired_runtime_replays_checked": len(positive_cases) * len(CONDITIONS),
        "condition_isolation": "PASS",
        "sanitized_evidence_leakage": "PASS",
        "negative_false_block": "PASS",
        "cost_preflight": budget,
        "next_gate": "V26 adapter smoke test + full 51-case seed pack",
        "evidence_records": len(evidence),
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
