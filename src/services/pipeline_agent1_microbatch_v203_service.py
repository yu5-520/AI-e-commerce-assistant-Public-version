"""V21.3 compatibility shim for the removed V20.9.4 Agent1 worker.

The historical implementation and its dual-agent table writes are deleted.
All callers are redirected to the single V20.28 semantic pipeline-item worker.
This module must not contain business logic or read legacy judgment tables.
"""

from __future__ import annotations

from src.services.pipeline_agent1_microbatch_v20101_service import (
    AGENT1_COMPLETED_STAGE,
    AGENT1_FAILED_STAGE,
    AGENT1_OUTPUT_INVALID_STAGE,
    AGENT1_PENDING_STAGE,
    AGENT1_RUNNING_STAGE,
    DEFAULT_AGENT1_MICRO_BATCH_SIZE,
    OBSERVED_STAGE,
    pending_agent1_item_count,
    run_agent1_microbatch_loop_v20101,
    run_agent1_microbatch_v20101,
    seed_agent1_pipeline_items_from_admission,
)

PIPELINE_AGENT1_MICROBATCH_VERSION = "21.3-compat"


def run_agent1_microbatch_v203(*args, **kwargs):
    return run_agent1_microbatch_v20101(*args, **kwargs)


def run_agent1_microbatch_loop_v203(*args, **kwargs):
    return run_agent1_microbatch_loop_v20101(*args, **kwargs)


__all__ = [
    "AGENT1_COMPLETED_STAGE",
    "AGENT1_FAILED_STAGE",
    "AGENT1_OUTPUT_INVALID_STAGE",
    "AGENT1_PENDING_STAGE",
    "AGENT1_RUNNING_STAGE",
    "DEFAULT_AGENT1_MICRO_BATCH_SIZE",
    "OBSERVED_STAGE",
    "PIPELINE_AGENT1_MICROBATCH_VERSION",
    "pending_agent1_item_count",
    "run_agent1_microbatch_v203",
    "run_agent1_microbatch_loop_v203",
    "seed_agent1_pipeline_items_from_admission",
]
