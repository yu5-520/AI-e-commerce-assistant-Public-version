"""V20.28 compatibility wrapper for the current Action Pack worker.

The historical module name is retained for imports only. It does not read legacy
package tables or seed a shadow Agent2 queue.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services.action_pack_core_v20_service import (
    ACTION_PACK_CORE_VERSION,
    ACTION_PARAMETER_ENRICHMENT_VERSION,
    HIGH_RISK_ACTIONS,
    action_parameter_enrichment_station_core,
    build_activity_parameter_pack,
    build_conversion_parameter_pack,
    build_parameter_packs,
    build_roas_parameter_pack,
    build_title_image_parameter_pack,
    compose_parameterized_sop,
    enrich_package_with_action_parameters,
    install_action_pack_core,
    select_action_parameter_pack,
)
from src.services.agent_rag_context_v2028_service import AGENT_RAG_CONTEXT_VERSION
from src.services.route_action_department_matrix_v1915_service import MATRIX_DISPATCH_VERSION


def action_parameter_enrichment_station_v1914(
    data_version: str | None,
    **kwargs: Any,
) -> Dict[str, Any]:
    result = action_parameter_enrichment_station_core(data_version=data_version, **kwargs)
    result.update(
        {
            "version": ACTION_PARAMETER_ENRICHMENT_VERSION,
            "matrixDispatchVersion": MATRIX_DISPATCH_VERSION,
            "actionPackCoreVersion": ACTION_PACK_CORE_VERSION,
            "ragContextVersion": AGENT_RAG_CONTEXT_VERSION,
            "legacyPackageSeedDisabled": True,
            "runtimeSource": "pipeline_items.agent1_completed",
            "adapterMode": "v20_28_current_action_pack_with_dynamic_rag",
            "rule": "V20.28 compatibility wrapper delegates to the current pipeline-item Action Pack; no legacy package-table handoff is allowed.",
        }
    )
    return result


# Historical function name retained as an alias to the same current implementation.
action_parameter_enrichment_station_v199 = action_parameter_enrichment_station_v1914
