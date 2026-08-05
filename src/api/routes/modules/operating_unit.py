"""Operating unit module route.

V14.8 rule: normal GET is a read path. It reads an existing snapshot only and does
not rebuild operating objects, product snapshots, task generation, RAG or Agent.
Explicit POST /snapshot/rebuild remains the compute endpoint.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query, Request

from src.services.account_service import current_user, user_id_from_headers
from src.services.operating_unit_snapshot_service import OPERATING_UNIT_SNAPSHOT_VERSION, get_operating_unit_snapshot, materialize_operating_unit_snapshot
from src.services.pipeline_gate_service import PIPELINE_GATE_VERSION, stage_summary

router = APIRouter()
OPERATING_UNIT_VERSION = "14.8.0"


def _viewer(request: Request) -> tuple[str, Dict[str, Any]]:
    user_id = user_id_from_headers(request.headers)
    return user_id, current_user(user_id)


@router.get("/operating-unit")
def operating_unit(request: Request, data_version: str | None = Query(default=None, alias="dataVersion")) -> Dict[str, Any]:
    user_id, user = _viewer(request)
    snapshot = get_operating_unit_snapshot(user_id=user_id, data_version=data_version, allow_build=False)
    snapshot["version"] = OPERATING_UNIT_VERSION
    snapshot["snapshotVersion"] = OPERATING_UNIT_SNAPSHOT_VERSION
    snapshot["pipelineGateVersion"] = PIPELINE_GATE_VERSION
    snapshot["viewer"] = {"id": user.get("id"), "roleId": user.get("roleId"), "roleName": user.get("roleName")}
    snapshot["readMode"] = "snapshot_only_no_build"
    snapshot["canonicalReadModelEntry"] = "/api/view/stores"
    snapshot["forbiddenRuntimeStages"] = ["materialize_operating_unit_snapshot", "projected_products", "projected_traffic", "projection_summary", "dataset_rows", "rag_retrieval", "llm_generation", "run_agent_judgment_station"]
    snapshot["rule"] = "V14.8：经营页GET只读已有快照；缺快照返回空状态，不在页面切换时同步构建。"
    return snapshot


@router.post("/operating-unit/snapshot/rebuild")
def rebuild_operating_unit_snapshot(request: Request, data_version: str | None = Query(default=None, alias="dataVersion")) -> Dict[str, Any]:
    user_id, user = _viewer(request)
    snapshot = materialize_operating_unit_snapshot(user_id=user_id, data_version=data_version, force=True)
    snapshot["version"] = OPERATING_UNIT_VERSION
    snapshot["snapshotVersion"] = OPERATING_UNIT_SNAPSHOT_VERSION
    snapshot["pipelineGateVersion"] = PIPELINE_GATE_VERSION
    snapshot["viewer"] = {"id": user.get("id"), "roleId": user.get("roleId"), "roleName": user.get("roleName")}
    snapshot["readMode"] = "explicit_snapshot_rebuilt"
    snapshot["rule"] = "V14.8：这是显式计算接口，不允许被前端普通页面切换自动调用。"
    return snapshot


@router.get("/operating-unit/pipeline/stages")
def operating_unit_pipeline_stages(request: Request, data_version: str | None = Query(default=None, alias="dataVersion")) -> Dict[str, Any]:
    user_id, _ = _viewer(request)
    return stage_summary(data_version=data_version, user_id=user_id, limit=80)
