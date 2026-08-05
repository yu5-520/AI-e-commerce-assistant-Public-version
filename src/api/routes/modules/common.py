"""Shared helpers for modular product routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException

from src.services.module_data_service import find_by_id


def find_or_404(collection: List[Dict[str, Any]], item_id: str, label: str) -> Dict[str, Any]:
    item = find_by_id(collection, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return item
