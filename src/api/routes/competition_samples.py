"""Competition evaluator sample-report download routes."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from src.services.competition_sample_report_service import (
    build_competition_sample_xlsx,
    sample_report_filename,
)

router = APIRouter(prefix="/sample-reports", tags=["competition-samples"])
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{period}.xlsx")
def download_competition_sample_report(period: int) -> Response:
    try:
        payload = build_competition_sample_xlsx(period)
        filename = sample_report_filename(period)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="仅提供第1期、第2期、第3期脱敏报表。") from exc
    encoded = quote(filename)
    return Response(
        content=payload,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "Cache-Control": "no-store",
            "X-Competition-Sample-Period": str(period),
        },
    )
