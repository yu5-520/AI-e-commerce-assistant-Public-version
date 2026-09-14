from __future__ import annotations

import json
from pathlib import Path

from build_seed_pack import canonical_hash, completion_cases, retrospective_cases, scope_cases


OUT = Path(__file__).with_name("seed_pack_v1.json")

EXPLICIT_LEAKAGE_TOKENS = (
    "completion bias",
    "scope bias",
    "retrospective bias",
    "reality bias",
    "authority penetration",
    "information_authority",
    "invocation_authority",
    "temporal_authority",
    "matched authority",
    "unmatched authority",
    "expected_primary_authority",
    "bias_family",
)


def build():
    cases = completion_cases() + scope_cases() + retrospective_cases()
    ids = [case["case_id"] for case in cases]
    assert len(cases) == 51, len(cases)
    assert len(ids) == len(set(ids)), "duplicate case_id"

    counts = {}
    for case in cases:
        ev = case["evaluation_only"]
        key = (ev["bias_family"], ev["case_kind"])
        counts[key] = counts.get(key, 0) + 1

        # Only explicit theory/condition labels are forbidden in model-visible content.
        # Natural-language words such as 'completion record' are allowed.
        rendered = json.dumps(case["model_view"], ensure_ascii=False).lower()
        for forbidden in EXPLICIT_LEAKAGE_TOKENS:
            assert forbidden not in rendered, (case["case_id"], forbidden)

        assert "evaluation_only" not in case["model_view"]
        assert "fixture_proposal" not in case["model_view"]

    expected = {
        (bias, kind): number
        for bias in ("completion", "scope", "retrospective")
        for kind, number in (
            ("positive", 6),
            ("negative", 6),
            ("counterfactual", 3),
            ("calibration", 2),
        )
    }
    assert counts == expected, (counts, expected)

    pack = {
        "schema": "reality-bias.seed-pack.v1",
        "version": "P3.1-seed-v1",
        "case_count": len(cases),
        "design": {
            "per_bias": {
                "positive": 6,
                "negative": 6,
                "counterfactual": 3,
                "calibration": 2,
            },
            "model_view_evaluation_separated": True,
            "explicit_label_leakage_check": True,
            "generated_from": "build_seed_pack_v2.py",
        },
        "cases": cases,
    }
    pack["seed_pack_hash"] = canonical_hash(pack)
    return pack


if __name__ == "__main__":
    pack = build()
    OUT.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": pack["case_count"],
        "seed_pack_hash": pack["seed_pack_hash"],
        "output": str(OUT),
        "explicit_label_leakage_check": "PASS",
    }, ensure_ascii=False, indent=2, sort_keys=True))
