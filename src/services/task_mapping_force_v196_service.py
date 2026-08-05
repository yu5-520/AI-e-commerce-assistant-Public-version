"""Legacy V19.6 task mapping station removed in V19.7.

Use task_mapping_force_v197_service.task_mapping_agent_station_v197. This module
is import-safe only; execution raises to expose accidental old-route rollback.
"""

from __future__ import annotations

from typing import Any, Dict

TASK_MAPPING_FORCE_V196_VERSION = "LEGACY_REMOVED_IN_19.7"


def task_mapping_agent_station_v196(data_version: str | None, **_: Any) -> Dict[str, Any]:
    raise RuntimeError("LEGACY_V196_TASK_MAPPING_STATION_REMOVED_IN_V19_7")
