from __future__ import annotations

import json

from src.services.v26_field_authority_contract_service import (
    FieldAuthorityViolation,
    V26FieldAuthorityContract,
)


def expect_block(fn, code_prefix: str) -> str:
    try:
        fn()
    except FieldAuthorityViolation as exc:
        text = str(exc)
        if not text.startswith(code_prefix):
            raise AssertionError(f"unexpected authority failure: {text}; expected prefix={code_prefix}") from exc
        return text
    raise AssertionError(f"expected V26 authority block: {code_prefix}")


def run() -> dict:
    authority = V26FieldAuthorityContract()
    receipt = authority.receipt()

    # Information Authority: model-side actors cannot promote guessed values into FACT.
    information_block = expect_block(
        lambda: authority.assert_write("agent1", "snapshot.roas", 1.23),
        "v26_field_write_forbidden:agent1:snapshot.roas",
    )
    authority.assert_write("system", "snapshot.roas", 1.23)

    # Invocation Authority: model-side actors cannot modify the system call topology.
    invocation_block = expect_block(
        lambda: authority.assert_write("agent3", "system_stage.call_graph", {}),
        "v26_field_write_forbidden:agent3:system_stage.call_graph",
    )
    stage_block = expect_block(
        lambda: authority.assert_stage_write("agent3", "systemStage"),
        "v26_system_stage_write_forbidden:agent3",
    )

    # Temporal / revision Authority: models cannot author canonical revision identity.
    temporal_block = expect_block(
        lambda: authority.assert_write("agent1", "revision.revision_hash", "sha256:fixture"),
        "v26_field_write_forbidden:agent1:revision.revision_hash",
    )
    authority.assert_write("system", "revision.revision_hash", "sha256:fixture")

    # Positive owner checks ensure the contract is not a block-everything gate.
    authority.assert_write("agent1", "judgement.reasoning", "fixture reasoning")
    authority.assert_write("agent2", "plan.daily_budget", 100.0)
    authority.assert_write("agent3", "operation.summary", "fixture operation")
    authority.assert_stage_write("agent3", "operationStage")

    return {
        "v26_adapter_smoke": "PASS",
        "field_authority_version": receipt["version"],
        "registered_header_count": receipt["registeredHeaderCount"],
        "information_authority": "PASS",
        "invocation_authority": "PASS",
        "temporal_revision_authority": "PASS",
        "positive_owner_paths": "PASS",
        "blocked_examples": {
            "information": information_block,
            "invocation": invocation_block,
            "system_stage": stage_block,
            "temporal": temporal_block,
        },
        "note": "Smoke validates existing V26 contract boundaries only; it does not yet claim full P3.1 pilot readiness."
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
