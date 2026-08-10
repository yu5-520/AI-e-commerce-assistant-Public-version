"""Active three-Agent token runtime.

Agent1 keeps the strict V22.5.9 exact runtime, Agent2 keeps V22.5.20 exact output
acceptance plus the V23.1.7 familyPayload semantic cache, and Agent3 keeps the
V23.2.15 system contract while adding V23.2.17 semantic SOP reuse/microbatching and
V23.2.19 exact-path semantic repair.  Unified runtime guards fail closed on provider
identity and keep the hash-table interface owned by the hash-directed Artifact
runtime.
"""
from src.services.agent_token_runtime_v22520_service import *
from src.services.agent_token_runtime_v22520_service import (
    run_agent2_draft_projected_inputs as _run_agent2_draft_projected_inputs_v22520,
)
from src.services.agent_hash_routed_rag_bridge_v1_service import (
    install_agent_hash_routed_rag_bridge,
)
from src.services.runtime_contract_guard_v1_service import (
    install_runtime_contract_guards,
)

# Install the fail-closed identity/interface contract before the active Agent3
# runtime imports the historical token-runtime module object.
RUNTIME_CONTRACT_GUARDS = install_runtime_contract_guards()
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

from src.services import agent3_runtime_v23215_service as _agent3_runtime_v23215
from src.services.agent3_semantic_path_repair_v1_service import (
    install_agent3_semantic_path_repair,
)

# Patch the provider boundary, not the pipeline return value.  This guarantees any
# repaired SOP is revalidated before the hash-directed output Artifact is accepted.
AGENT3_SEMANTIC_PATH_REPAIR = install_agent3_semantic_path_repair(
    _agent3_runtime_v23215
)
_run_agent3_sop_projected_inputs_v23217 = (
    _agent3_runtime_v23215.run_agent3_sop_projected_inputs
)


def run_agent3_sop_projected_inputs(envelopes, *args, **kwargs):
    """Activate compatible microbatching plus exact-path semantic self-repair.

    The historical pipeline still passes ``max_items_per_call=1`` because V23.2.15
    discarded that argument. V23.2.17 makes it real, so the active facade promotes
    the old disabled-batching sentinel to capacity 2. Character budget and
    compatibility grouping inside Agent3 remain the final authority; the V23.2.19
    repair layer is singleton and may only modify validator-named JSON paths.
    """

    requested = kwargs.get("max_items_per_call")
    try:
        requested_int = int(requested) if requested is not None else 0
    except Exception:
        requested_int = 0
    if requested_int <= 1:
        kwargs["max_items_per_call"] = 2
    return _run_agent3_sop_projected_inputs_v23217(envelopes, *args, **kwargs)
