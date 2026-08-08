"""Deterministic three-sheet ERA sample workbook rows for competition downloads.

The evaluator sample keeps the recovered ERA operating-unit semantics:
- 3 stores x 10 global products = 30 operating product units per period;
- the same 30 operating units recur across 3 business dates;
- three selected operating units carry deterministic conversion / observe / scale
  trajectories while the remaining operating units stay near-stable.

This module owns row generation only. XLSX serialization and hash sealing remain in
``competition_sample_report_service`` / ``competition_sample_asset_service``.
"""
from __future__ import annotations

from typing import Any, Dict, List

ERA_SAMPLE_PAYLOAD_VERSION = "2.0.0"
SAMPLE_SHEET_ORDER = ("商品经营明细", "店铺经营汇总", "流量来源明细")

PERIOD_DATES = {
    1: "2026-06-25",
    2: "2026-06-28",
    3: "2026-07-02",
}

STORES = (
    {"platform": "天猫", "storeId": "TB-SH-001", "storeName": "天猫旗舰店", "factor": 1.00},
    {"platform": "京东", "storeId": "JD-SH-002", "storeName": "京东自营店", "factor": 0.92},
    {"platform": "抖音", "storeId": "DY-SH-003", "storeName": "抖音官方店", "factor": 1.08},
)

PRODUCTS = (
    ("P10001", "轻薄防晒外套", "服饰内衣", "防晒衣", "春夏新品", 129.0, 58.0, 420),
    ("P10002", "冰丝休闲长裤", "服饰内衣", "休闲裤", "春夏新品", 109.0, 43.0, 360),
    ("P10003", "速干运动T恤", "运动户外", "运动T恤", "常规款", 89.0, 31.0, 520),
    ("P10004", "通勤防泼水背包", "箱包配饰", "双肩包", "常规款", 199.0, 86.0, 180),
    ("P10005", "女士轻便凉鞋", "鞋靴", "凉鞋", "季节款", 139.0, 62.0, 210),
    ("P10006", "男士透气跑鞋", "鞋靴", "跑鞋", "常规款", 269.0, 128.0, 150),
    ("P10007", "家用小型除湿机", "家用电器", "生活电器", "常规款", 399.0, 245.0, 95),
    ("P10008", "厨房多功能收纳架", "家居日用", "厨房收纳", "常规款", 79.0, 25.0, 640),
    ("P10009", "儿童防滑拖鞋", "母婴亲子", "童鞋", "季节款", 59.0, 18.0, 760),
    ("P10010", "户外折叠露营椅", "运动户外", "露营装备", "季节款", 169.0, 74.0, 240),
)

PRODUCT_HEADERS = (
    "序号", "统计日期", "平台", "店铺ID", "店铺名称", "商品ID", "SKU ID", "商品名称",
    "商品状态", "一级类目", "二级类目", "商品标签", "曝光量", "访客数", "浏览量",
    "点击人数", "点击率", "加购人数", "收藏人数", "支付买家数", "支付订单数", "支付件数",
    "支付金额", "支付转化率", "客单价", "商品成本金额", "毛利金额", "毛利率",
    "退款订单数", "退款金额", "退款率", "广告消耗", "广告点击数", "广告成交数", "ROI",
    "自然流量访客数", "付费流量访客数", "库存数量", "可售天数", "更新时间",
)

STORE_HEADERS = (
    "序号", "统计日期", "平台", "店铺ID", "店铺名称", "访客数", "浏览量", "支付买家数",
    "支付订单数", "支付件数", "支付金额", "支付转化率", "客单价", "退款订单数",
    "退款金额", "退款率", "广告消耗", "ROI", "自然流量访客数", "付费流量访客数",
    "在售商品数", "库存总量", "更新时间",
)

TRAFFIC_HEADERS = (
    "序号", "统计日期", "平台", "店铺ID", "店铺名称", "商品ID", "SKU ID", "商品名称",
    "流量来源", "访客数", "浏览量", "点击人数", "点击率", "支付买家数", "支付金额",
    "支付转化率", "广告消耗", "ROI", "更新时间",
)

TRAFFIC_SOURCES = (
    ("自然搜索", 0.28, False),
    ("推荐流量", 0.24, False),
    ("付费广告", 0.22, True),
    ("店铺访问", 0.16, False),
    ("其他", 0.10, False),
)

SIGNAL_SERIES = {
    # conversion repair: one operating unit only
    ("TB-SH-001", "P10001"): {
        "conversion": (0.052, 0.035, 0.028),
        "roi": (3.20, 2.55, 2.08),
    },
    # observe terminal: one operating unit only
    ("JD-SH-002", "P10005"): {
        "conversion": (0.0480, 0.0470, 0.0475),
        "roi": (2.42, 2.46, 2.44),
    },
    # scale: one operating unit only
    ("DY-SH-003", "P10010"): {
        "conversion": (0.046, 0.051, 0.054),
        "roi": (3.85, 4.25, 4.62),
    },
}


def _r(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _product_records(period: int) -> List[Dict[str, Any]]:
    if period not in PERIOD_DATES:
        raise ValueError("competition_sample_period_not_found")
    date = PERIOD_DATES[period]
    rows: List[Dict[str, Any]] = []
    seq = 0
    for store_idx, store in enumerate(STORES):
        for product_idx, product in enumerate(PRODUCTS):
            seq += 1
            pid, title, category1, category2, tag, price, unit_cost, base_stock = product
            store_factor = float(store["factor"])
            stable_wave = (0.99, 1.00, 1.01)[period - 1]
            visitors = max(80, int((260 + product_idx * 42 + store_idx * 19) * store_factor * stable_wave))
            exposure = int(visitors * (9.6 + (product_idx % 4) * 0.5))
            click_rate = _r(0.017 + (product_idx % 4) * 0.0012 + store_idx * 0.0004, 6)
            clicks = max(1, int(exposure * click_rate))
            series = SIGNAL_SERIES.get((str(store["storeId"]), pid))
            if series:
                conversion = float(series["conversion"][period - 1])
                roi = float(series["roi"][period - 1])
            else:
                conversion = _r((0.032 + (product_idx % 5) * 0.0035 + store_idx * 0.0015) * stable_wave, 6)
                roi = _r((2.55 + (product_idx % 4) * 0.24 + store_idx * 0.08) * stable_wave, 4)
            buyers = max(1, int(round(visitors * conversion)))
            orders = buyers + (1 if (product_idx + store_idx + period) % 4 == 0 else 0)
            pieces = orders + (product_idx % 3)
            payment = _r(pieces * price, 2)
            cost_total = _r(pieces * unit_cost, 2)
            gross_profit = _r(payment - cost_total, 2)
            gross_margin = _r(gross_profit / payment if payment else 0.0, 6)
            refund_orders = 1 if (product_idx + store_idx + period) % 13 == 0 else 0
            refund_amount = _r(refund_orders * price, 2)
            refund_rate = _r(refund_orders / max(1, orders), 6)
            ad_spend = _r(max(80.0, payment / max(1.4, roi)), 2)
            ad_clicks = max(1, int(clicks * 0.42))
            ad_orders = max(1, int(round(orders * 0.38)))
            organic_visitors = max(1, int(visitors * 0.64))
            paid_visitors = max(1, visitors - organic_visitors)
            inventory = max(0, int(base_stock * store_factor - (period - 1) * pieces * 1.5))
            available_days = _r(inventory / max(1, pieces), 1)
            rows.append({
                "序号": seq, "统计日期": date, "平台": store["platform"],
                "店铺ID": store["storeId"], "店铺名称": store["storeName"], "商品ID": pid,
                "SKU ID": f"SKU{pid[1:]}-{chr(65 + store_idx)}", "商品名称": title, "商品状态": "在售",
                "一级类目": category1, "二级类目": category2, "商品标签": tag,
                "曝光量": exposure, "访客数": visitors, "浏览量": int(exposure * 1.21),
                "点击人数": clicks, "点击率": click_rate, "加购人数": max(1, int(clicks * 0.31)),
                "收藏人数": max(1, int(clicks * 0.18)), "支付买家数": buyers, "支付订单数": orders,
                "支付件数": pieces, "支付金额": payment, "支付转化率": conversion,
                "客单价": _r(payment / max(1, buyers), 2), "商品成本金额": cost_total,
                "毛利金额": gross_profit, "毛利率": gross_margin, "退款订单数": refund_orders,
                "退款金额": refund_amount, "退款率": refund_rate, "广告消耗": ad_spend,
                "广告点击数": ad_clicks, "广告成交数": ad_orders, "ROI": roi,
                "自然流量访客数": organic_visitors, "付费流量访客数": paid_visitors,
                "库存数量": inventory, "可售天数": available_days, "更新时间": f"{date} 23:00:00",
            })
    return rows


def _store_records(period: int, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    date = PERIOD_DATES[period]
    rows: List[Dict[str, Any]] = []
    for seq, store in enumerate(STORES, 1):
        items = [item for item in products if item["店铺ID"] == store["storeId"]]
        visitors = sum(int(item["访客数"]) for item in items)
        payment = sum(float(item["支付金额"]) for item in items)
        buyers = sum(int(item["支付买家数"]) for item in items)
        orders = sum(int(item["支付订单数"]) for item in items)
        pieces = sum(int(item["支付件数"]) for item in items)
        refunds = sum(int(item["退款订单数"]) for item in items)
        refund_amount = sum(float(item["退款金额"]) for item in items)
        ad_spend = sum(float(item["广告消耗"]) for item in items)
        rows.append({
            "序号": seq, "统计日期": date, "平台": store["platform"], "店铺ID": store["storeId"],
            "店铺名称": store["storeName"], "访客数": visitors, "浏览量": sum(int(item["浏览量"]) for item in items),
            "支付买家数": buyers, "支付订单数": orders, "支付件数": pieces, "支付金额": _r(payment, 2),
            "支付转化率": _r(buyers / max(1, visitors), 6), "客单价": _r(payment / max(1, buyers), 2),
            "退款订单数": refunds, "退款金额": _r(refund_amount, 2),
            "退款率": _r(refunds / max(1, orders), 6), "广告消耗": _r(ad_spend, 2),
            "ROI": _r(payment / max(1.0, ad_spend), 4),
            "自然流量访客数": sum(int(item["自然流量访客数"]) for item in items),
            "付费流量访客数": sum(int(item["付费流量访客数"]) for item in items),
            "在售商品数": len(items), "库存总量": sum(int(item["库存数量"]) for item in items),
            "更新时间": f"{date} 23:00:00",
        })
    return rows


def _traffic_records(period: int, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    date = PERIOD_DATES[period]
    rows: List[Dict[str, Any]] = []
    seq = 0
    for item in products:
        remaining_visitors = int(item["访客数"])
        remaining_payment = float(item["支付金额"])
        remaining_buyers = int(item["支付买家数"])
        for source_idx, (source, share, paid) in enumerate(TRAFFIC_SOURCES):
            seq += 1
            is_last = source_idx == len(TRAFFIC_SOURCES) - 1
            visitors = remaining_visitors if is_last else max(0, int(item["访客数"] * share))
            payment = remaining_payment if is_last else _r(item["支付金额"] * share, 2)
            buyers = remaining_buyers if is_last else max(0, int(round(item["支付买家数"] * share)))
            remaining_visitors -= visitors
            remaining_payment = _r(remaining_payment - payment, 2)
            remaining_buyers -= buyers
            exposure = max(visitors, int(visitors * 8.5))
            clicks = max(0, int(exposure * float(item["点击率"])))
            spend = _r(float(item["广告消耗"]) if paid else 0.0, 2)
            roi = _r(payment / spend, 4) if spend > 0 else None
            rows.append({
                "序号": seq, "统计日期": date, "平台": item["平台"], "店铺ID": item["店铺ID"],
                "店铺名称": item["店铺名称"], "商品ID": item["商品ID"], "SKU ID": item["SKU ID"],
                "商品名称": item["商品名称"], "流量来源": source, "访客数": visitors,
                "浏览量": exposure, "点击人数": clicks, "点击率": item["点击率"],
                "支付买家数": max(0, buyers), "支付金额": _r(payment, 2),
                "支付转化率": _r(max(0, buyers) / max(1, visitors), 6),
                "广告消耗": spend, "ROI": roi, "更新时间": f"{date} 23:00:00",
            })
    return rows


def build_competition_era_workbook_rows(period: int) -> Dict[str, List[List[Any]]]:
    products = _product_records(period)
    stores = _store_records(period, products)
    traffic = _traffic_records(period, products)
    datasets = {
        "商品经营明细": (PRODUCT_HEADERS, products),
        "店铺经营汇总": (STORE_HEADERS, stores),
        "流量来源明细": (TRAFFIC_HEADERS, traffic),
    }
    return {
        sheet: [
            list(headers),
            *[[record.get(header) for header in headers] for record in records],
        ]
        for sheet, (headers, records) in datasets.items()
    }


def sample_contract(period: int) -> Dict[str, Any]:
    rows = build_competition_era_workbook_rows(period)
    product_rows = rows["商品经营明细"][1:]
    product_header = rows["商品经营明细"][0]
    store_idx = product_header.index("店铺ID")
    product_idx = product_header.index("商品ID")
    sku_idx = product_header.index("SKU ID")
    operating_units = {
        (row[store_idx], row[product_idx], row[sku_idx])
        for row in product_rows
    }
    global_products = {row[product_idx] for row in product_rows}
    return {
        "period": period,
        "businessDate": PERIOD_DATES[period],
        "sheetDataRows": {sheet: len(values) - 1 for sheet, values in rows.items()},
        "operatingProductUnitCount": len(operating_units),
        "globalProductCount": len(global_products),
        "storeCount": len(STORES),
        "signalOperatingUnits": [
            {"storeId": store_id, "productId": product_id}
            for store_id, product_id in SIGNAL_SERIES
        ],
    }


__all__ = [
    "ERA_SAMPLE_PAYLOAD_VERSION",
    "SAMPLE_SHEET_ORDER",
    "PERIOD_DATES",
    "STORES",
    "PRODUCTS",
    "SIGNAL_SERIES",
    "build_competition_era_workbook_rows",
    "sample_contract",
]
