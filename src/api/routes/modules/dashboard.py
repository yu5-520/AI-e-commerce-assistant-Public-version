"""Dashboard module route."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

from src.services.account_service import user_id_from_headers
from src.services.dashboard_service import get_dashboard_summary

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request) -> Dict[str, Any]:
    return get_dashboard_summary(user_id_from_headers(request.headers))
