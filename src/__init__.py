"""AI + RPA + ERP + CRM e-commerce workflow MVP package.

V22.3.0 keeps one governed runtime and hard semantic interfaces between Artifact
Transport, Agent execution and Token Runtime. Full signal/capability Artifacts are
audit sources; model execution consumes only agent1InputRef or agent2InputRef.

The package bootstrap may bind deterministic storage, evidence, producer handoff
and read-model adapters. It must never replace Agent1, Agent2 or the hard pipeline
tick at import time.
"""

from src.services.v22_runtime_service import install_v22_runtime
from src.services.agent1_dual_channel_contract_service import (
    bind_agent1_dual_channel_contract,
)
from src.services.pipeline_reference_runtime_service import (
    bind_pipeline_reference_runtime,
)
from src.services.station_truth_contract_v225_service import (
    bind_station_truth_contract,
)
from src.services.hard_interface_bridge_v2301_service import (
    bind_hard_interface_bridge_v2301,
)
from src.services.task_evidence_canonical_history_install_v1_service import (
    install_task_evidence_canonical_history_v1,
)

# bind_end_to_end_agent_flow is retired as an Agent runtime overlay. Its evidence
# admission and layered read-model functions are exposed only through the V22.3
# non-Agent bridge above.

install_v22_runtime()
# The canonical task-evidence adapter is deliberately installed after V22.  It does
# not replace Agent/Task admission logic; it only rebinds the task-detail evidence
# history read source to the already-registered canonical product history authority.
install_task_evidence_canonical_history_v1()
bind_agent1_dual_channel_contract()
bind_pipeline_reference_runtime()
bind_station_truth_contract()
bind_hard_interface_bridge_v2301()
