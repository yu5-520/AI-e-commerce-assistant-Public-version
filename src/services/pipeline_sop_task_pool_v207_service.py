"""Compatibility shim for the removed V20.10 SOP/Task Pool worker.

The current implementation lives in pipeline_sop_task_pool_v2010_service. This
module contains no database reads, writes or fallback runtime.
"""

from __future__ import annotations

from src.services.pipeline_sop_task_pool_v2010_service import (
    DEFAULT_POOL_BATCH_SIZE,
    DEFAULT_SOP_BATCH_SIZE,
    PIPELINE_SOP_TASK_POOL_VERSION,
    SOP_MAPPED_STAGE,
    SOP_READY_STAGE,
    TASK_ADMITTED_STAGE,
    pending_sop_item_count,
    pending_task_pool_item_count,
    run_sop_mapping_microbatch_v206,
    run_task_pool_admission_microbatch_v207,
    seed_sop_items_from_agent2_plans,
    task_mapping_agent_station_v206,
    task_pool_admission_station_v207,
)

LEGACY_PIPELINE_SOP_TASK_POOL_VERSION = "20.10-removed"

__all__ = [
    "DEFAULT_POOL_BATCH_SIZE",
    "DEFAULT_SOP_BATCH_SIZE",
    "LEGACY_PIPELINE_SOP_TASK_POOL_VERSION",
    "PIPELINE_SOP_TASK_POOL_VERSION",
    "SOP_MAPPED_STAGE",
    "SOP_READY_STAGE",
    "TASK_ADMITTED_STAGE",
    "pending_sop_item_count",
    "pending_task_pool_item_count",
    "run_sop_mapping_microbatch_v206",
    "run_task_pool_admission_microbatch_v207",
    "seed_sop_items_from_agent2_plans",
    "task_mapping_agent_station_v206",
    "task_pool_admission_station_v207",
]
