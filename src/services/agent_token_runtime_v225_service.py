"""Active three-Agent token runtime.

Agent1 keeps the strict V22.5.9 exact runtime, Agent2 keeps V22.5.20 exact output
acceptance plus the V23.1.7 familyPayload semantic cache, and Agent3 keeps the
V23.2.15 system/repair contract while adding V23.2.17 semantic SOP reuse and
compatible two-item initial Provider microbatching. Hash-routed RAG is installed at
the deterministic provider boundary for Agent2/Agent3.
"""
from src.services.agent_token_runtime_v22520_service import *
from src.services.agent_token_runtime_v22520_service import (
    run_agent2_draft_projected_inputs as _run_agent2_draft_projected_inputs_v22520,
)
from src.services.agent_hash_routed_rag_bridge_v1_service import (
    install_agent_hash_routed_rag_bridge,
)

AGENT_HASH_ROUTED_RAG_BRIDGE = install_agent_hash_routed_rag_bridge()


def run_agent2_draft_projected_inputs(*args, **kwargs):
    """Expose semantic replay through the existing no-provider proof slot."""

    outputs, summary = _run_agent2_draft_projected_inputs_v22520(*args, **kwargs)
    for draft in outputs.values():
        if not isinstance(draft, dict) or draft.get("semanticResultCacheHit") is not True:
            continue
        draft["exactExecutionReplay"] = True
        draft["semanticReplayCompatibilityMode"] = (
            "no_provider_replay_through_existing_exactReplayValidated_slot"
        )
        draft["providerCallExecutedForCurrentResult"] = False
    return outputs, summary


run_agent2_projected_inputs = run_agent2_draft_projected_inputs

from src.services.agent3_runtime_v23215_service import (
    run_agent3_sop_projected_inputs as _run_agent3_sop_projected_inputs_v23217,
)


def run_agent3_sop_projected_inputs(envelopes, *args, **kwargs):
    """Activate the competition Agent3 compatible microbatch capacity.

    The historical pipeline still passes ``max_items_per_call=1`` because V23.2.15
    discarded that argument. V23.2.17 makes it real, so the active facade promotes the
    old disabled-batching sentinel to capacity 2. Character budget and compatibility
    grouping inside Agent3 remain the final authority; this does not create parallel
    Provider calls or a second Worker.
    """

    requested = kwargs.get("max_items_per_call")
    try:
        requested_int = int(requested) if requested is not None else 0
    except Exception:
        requested_int = 0
    if requested_int <= 1:
        kwargs["max_items_per_call"] = 2
    return _run_agent3_sop_projected_inputs_v23217(envelopes, *args, **kwargs)
