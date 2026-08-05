"""Product module router."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.routes.modules import dashboard, operating_unit, product, product_detail_v2256, competitor, listing, traffic, inventory, aftersales
from src.api.routes.modules import report_v5 as report
from src.api.routes.modules import rag_memory, feedback_flywheel, log, todo, neural_operating

router = APIRouter(prefix="/api/modules", tags=["modules"])
for module in [dashboard, operating_unit, product, product_detail_v2256, competitor, listing, traffic, inventory, aftersales, report, rag_memory, feedback_flywheel, log, todo, neural_operating]:
    router.include_router(module.router)
