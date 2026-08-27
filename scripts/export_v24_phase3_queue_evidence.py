#!/usr/bin/env python3
"""Export the current Python queue/runtime shape without importing the application.

Phase3 uses source/AST evidence because importing src executes runtime bootstrap. The
result freezes the exact migration baseline: one worker, downstream stage barrier,
pipeline_items multiplexing, and process-wide generation reset barrier.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PIPELINE_ITEM = ROOT / "src/services/pipeline_item_service.py"
ACTIVE_AGENT = ROOT / "src/services/agent_runtime_hard_interface_v22515_service.py"
WORKER = ROOT / "src/services/station_agent_worker_v22515_service.py"
GENERATION = ROOT / "src/services/runtime_generation_barrier_v1_service.py"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def literal_assignment(path: Path, name: str) -> Any:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise RuntimeError(f"assignment_not_found:{path}:{name}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def build() -> dict[str, Any]:
    pipeline_text = PIPELINE_ITEM.read_text(encoding="utf-8")
    agent_text = ACTIVE_AGENT.read_text(encoding="utf-8")
    worker_text = WORKER.read_text(encoding="utf-8")
    generation_text = GENERATION.read_text(encoding="utf-8")

    stage_order = literal_assignment(PIPELINE_ITEM, "STAGE_ORDER")
    station_mapping = literal_assignment(PIPELINE_ITEM, "STATION_TO_ITEM_STAGE")

    required_pipeline_columns = [
        "item_id TEXT PRIMARY KEY",
        "current_stage TEXT",
        "status TEXT",
        "priority INTEGER",
        "payload TEXT",
        "artifact_refs_json TEXT",
        "payload_artifact_ref TEXT",
    ]
    for marker in required_pipeline_columns:
        require(marker in pipeline_text, f"pipeline_item_column_missing:{marker}")

    require("agent1_blocked_by_downstream" in agent_text, "active_downstream_barrier_marker_missing")
    require("higher_priority_pending" in agent_text, "active_stage_priority_marker_missing")
    require("secondWorkerAllowed=False" in worker_text, "single_worker_marker_missing")
    require(
        "one_complete_worker_iteration_and_reset_share_exclusive_generation_barrier" in worker_text,
        "global_generation_barrier_marker_missing",
    )
    require("_EXECUTION_BARRIER = threading.RLock()" in generation_text, "process_generation_lock_missing")
    require("runtime_execution_guard" in generation_text, "runtime_execution_guard_missing")

    material: dict[str, Any] = {
        "schema": "v24.phase3.python_queue_baseline.v1",
        "version": "24.15.0-phase3.1",
        "productionQueueWriteAuthority": "PYTHON_UNCHANGED",
        "legacyBaseline": {
            "singleWorkerOwnership": True,
            "secondWorkerAllowed": False,
            "globalGenerationBarrier": True,
            "agent1BlockedByDownstream": True,
            "stagePriorityScheduler": True,
            "pipelineItemsMultiplexesStateAndQueue": True,
            "pipelineItemsCarriesPayloadAndArtifactRefs": True,
        },
        "stageOrder": stage_order,
        "stationToItemStage": station_mapping,
        "sourceHashes": {
            "pipelineItemService": sha256_file(PIPELINE_ITEM),
            "activeAgentRuntime": sha256_file(ACTIVE_AGENT),
            "stationWorker": sha256_file(WORKER),
            "generationBarrier": sha256_file(GENERATION),
        },
        "migrationTarget": {
            "pipelineItem": "business_state_only",
            "stageJob": "execution_work_only",
            "artifact": "immutable_io_only",
            "outbox": "transactional_handoff_intent",
            "stageQueues": ["AGENT1", "AGENT2", "AGENT3"],
        },
    }
    material["evidenceHash"] = sha256_value(material)
    return material


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = build()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical(value) + "\n", encoding="utf-8")
    print(canonical({
        "verified": True,
        "evidenceHash": value["evidenceHash"],
        "stageCount": len(value["stageOrder"]),
        "singleWorkerOwnership": value["legacyBaseline"]["singleWorkerOwnership"],
        "agent1BlockedByDownstream": value["legacyBaseline"]["agent1BlockedByDownstream"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
