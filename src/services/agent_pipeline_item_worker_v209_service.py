"""Compatibility shim for the removed V20.10 Agent pipeline worker.

All runtime behavior delegates to the single V20.28 semantic worker. This module
must not implement scheduling, select the newest dataVersion or import the old SOP
worker.
"""

from __future__ import annotations

from src.services.agent_pipeline_item_worker_v2010_service import (
    AGENT1_COMPLETED_STAGE,
    AGENT1_OUTPUT_INVALID_STAGE,
    AGENT_PIPELINE_ITEM_WORKER_VERSION,
    DEFAULT_ACTION_PACK_BATCH_SIZE,
    DEFAULT_AGENT2_BATCH_SIZE,
    DEFAULT_POOL_BATCH_SIZE,
    DEFAULT_SOP_BATCH_SIZE,
    agent_pipeline_status,
    latest_data_version,
    recover_version_only_action_pack_invalid,
    run_agent_pipeline_loop,
    run_agent_pipeline_tick,
    seed_action_pack_from_agent1_items,
)

LEGACY_AGENT_PIPELINE_ITEM_WORKER_VERSION = "20.10-removed"

__all__ = [
    "AGENT1_COMPLETED_STAGE",
    "AGENT1_OUTPUT_INVALID_STAGE",
    "AGENT_PIPELINE_ITEM_WORKER_VERSION",
    "DEFAULT_ACTION_PACK_BATCH_SIZE",
    "DEFAULT_AGENT2_BATCH_SIZE",
    "DEFAULT_POOL_BATCH_SIZE",
    "DEFAULT_SOP_BATCH_SIZE",
    "LEGACY_AGENT_PIPELINE_ITEM_WORKER_VERSION",
    "agent_pipeline_status",
    "latest_data_version",
    "recover_version_only_action_pack_invalid",
    "run_agent_pipeline_loop",
    "run_agent_pipeline_tick",
    "seed_action_pack_from_agent1_items",
]
