"""Competition evaluator sample-report download routes."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from src.services.competition_sample_asset_service import get_competition_sample_asset

router = APIRouter(prefix="/sample-reports", tags=["competition-samples"])
logger = logging.getLogger(__name__)


@router.get("/{period}.xlsx")
def download_competition_sample_report(period: int) -> Response:
    try:
        asset = get_competition_sample_asset(period)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="仅提供第1期、第2期、第3期脱敏报表。") from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="评委样例资产完整性校验失败，请联系系统管理员。",
        ) from exc

    payload = bytes(asset["content"])
    filename = str(asset["filename"])
    encoded = quote(filename)
    logger.info(
        "competition_sample_asset_download period=%s sha256=%s byte_size=%s asset_id=%s",
        asset["period"],
        asset["contentSha256"],
        asset["byteSize"],
        asset["assetId"],
    )
    return Response(
        content=payload,
        media_type=str(asset["mimeType"]),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "Cache-Control": "no-store",
            "X-Competition-Sample-Period": str(asset["period"]),
            "X-Competition-Sample-SHA256": str(asset["contentSha256"]),
            "X-Competition-Sample-Asset": str(asset["assetId"]),
            "X-Competition-Sample-Storage": "sqlite-blob",
        },
    )
