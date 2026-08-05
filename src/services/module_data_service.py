"""Runtime-empty module data boundary for V5.

V5 keeps the original module navigation and module functions, but removes MVP-stage
business fallback content from the product runtime. Business records must now come
from report imports and module projections instead of hard-coded demo rows.

Examples / fixtures may still be used by tests and explicit mock import actions,
but these constants must stay empty in normal module APIs.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

PRODUCTS: List[Dict[str, Any]] = []
COMPETITORS: List[Dict[str, Any]] = []
LISTINGS: List[Dict[str, Any]] = []
TRAFFIC: List[Dict[str, Any]] = []

REPORT_GROUPS: List[Dict[str, Any]] = [
    {
        "title": "ERP 报表",
        "reports": [
            {"id": "products", "name": "商品报表", "source": "ERP", "status": "待导入", "count": "0 条", "desc": "商品、库存、成本、售价、毛利率"},
            {"id": "orders", "name": "订单报表", "source": "ERP", "status": "待导入", "count": "0 条", "desc": "订单金额、发货状态、下单时间"},
            {"id": "inventory", "name": "库存报表", "source": "ERP", "status": "待导入", "count": "0 条", "desc": "库存数量、预警、补货状态"},
        ],
    },
    {
        "title": "CRM 报表",
        "reports": [
            {"id": "refunds", "name": "退款报表", "source": "CRM", "status": "待导入", "count": "0 条", "desc": "退款原因、售后状态、责任归因"},
            {"id": "customers", "name": "客户报表", "source": "CRM", "status": "待导入", "count": "0 条", "desc": "客户来源、复购、风险标记"},
        ],
    },
]

REPORT_DETAILS: Dict[str, Dict[str, Any]] = {}


def clone(value: Any) -> Any:
    return deepcopy(value)


def all_reports() -> List[Dict[str, Any]]:
    return [report for group in REPORT_GROUPS for report in group["reports"]]


def find_by_id(collection: List[Dict[str, Any]], item_id: str) -> Dict[str, Any] | None:
    return next((item for item in collection if item.get("id") == item_id), None)
