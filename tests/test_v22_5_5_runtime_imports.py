from __future__ import annotations


def test_active_runtime_modules_import_without_patch_installation() -> None:
    from src.services.agent_runtime_hard_interface_v2255_service import (
        AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        EXECUTION_LOCK_CONTRACT,
        agent_runtime_hard_interface_status,
    )
    from src.services.station_agent_worker_v225_service import (
        STATION_AGENT_WORKER_VERSION,
        worker_config,
    )

    assert AGENT_RUNTIME_HARD_INTERFACE_VERSION == "22.5.5"
    assert STATION_AGENT_WORKER_VERSION == "22.5.5"
    assert EXECUTION_LOCK_CONTRACT == "one_problem_one_action_one_owner_one_target"
    assert agent_runtime_hard_interface_status()["runtimeMonkeyPatchRequired"] is False
    assert worker_config()["executionLockContract"] == EXECUTION_LOCK_CONTRACT
