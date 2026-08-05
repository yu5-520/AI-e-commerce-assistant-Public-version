"""Read-only neural operating projection route."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

from src.services.account_service import user_id_from_headers
from src.services.frontend_read_model_service import read_dashboard_view, read_task_views
from src.services.neural_operating_read_model_v218_service import (
    build_neural_operating_projection,
)

router = APIRouter()


@router.get("/neural-operating")
def neural_operating(request: Request) -> Dict[str, Any]:
    return build_neural_operating_projection(
        user_id_from_headers(request.headers),
        tasks=read_task_views(limit=200).get("items") or [],
        dashboard=read_dashboard_view(),
    )
