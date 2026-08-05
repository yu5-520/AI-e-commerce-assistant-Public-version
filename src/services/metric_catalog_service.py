"""V12 metric catalog and display helpers.

The catalog is the stable bridge between messy report fields and the system's
product/store/traffic facts.  It intentionally does not create tasks.  It only
normalizes field names, values, units, and product定位 fields so downstream code
can read data without relying on long product titles.
"""

from __future__ import annotations

import re
from hashlib import sha1
from typing import Any, Dict, Iterable, List

CATALOG_VERSION = "12.0.0"

METRIC_ALIASES: Dict[str, List[str]] = {
    "inventory_qty": ["inventory_qty", "stock", "available_stock", "current_stock", "库存", "库存数量", "商品库存", "可售库存", "当前库存", "可用库存"],
    "sellable_days": ["sellable_days", "可售天数", "库存可售天数", "可售周期"],
    "avg_order_value": ["avg_order_value", "客单价", "平均客单价", "件单价", "成交均价"],
    "sale_price": ["sale_price", "售价", "销售价", "活动价", "成交价", "商品售价", "标价"],
    "product_cost_amount": ["product_cost_amount", "商品成本金额", "成本金额", "成本", "成本价", "采购价", "供货价", "商品成本"],
    "payment_amount": ["payment_amount", "actual_paid", "revenue", "支付金额", "成交金额", "销售额", "GMV", "gmv", "实付金额", "买家实付"],
    "gross_profit_amount": ["gross_profit_amount", "毛利金额", "毛利", "利润金额"],
    "gross_margin_rate": ["gross_margin_rate", "gross_margin", "毛利率", "商品毛利率", "利润率"],
    "visitor_count": ["visitor_count", "visitors", "traffic", "访客数", "访问量", "UV", "uv"],
    "page_view_count": ["page_view_count", "浏览量", "PV", "pv", "页面浏览量"],
    "click_user_count": ["click_user_count", "click_users", "点击人数", "点击用户数"],
    "click_count": ["click_count", "clicks", "点击量", "点击数", "广告点击数"],
    "click_rate": ["click_rate", "ctr", "CTR", "点击率"],
    "payment_buyer_count": ["payment_buyer_count", "支付买家数", "成交买家数", "购买人数"],
    "payment_order_count": ["payment_order_count", "paid_orders", "支付订单数", "成交订单数", "订单数"],
    "payment_unit_count": ["payment_unit_count", "paid_units", "支付件数", "成交件数", "销售件数", "销量"],
    "payment_conversion_rate": ["payment_conversion_rate", "conversion_rate", "支付转化率", "成交转化率", "转化率", "CVR", "cvr"],
    "refund_order_count": ["refund_order_count", "退款订单数", "退款笔数", "售后订单数", "售后次数", "退款次数"],
    "refund_amount": ["refund_amount", "退款金额", "退款额", "售后金额", "退货金额"],
    "refund_rate": ["refund_rate", "退款率", "退货率", "售后率"],
    "ad_spend": ["ad_spend", "广告消耗", "广告花费", "推广花费", "投放花费", "投放消耗", "消耗"],
    "ad_click_count": ["ad_click_count", "广告点击数", "推广点击数"],
    "ad_order_count": ["ad_order_count", "广告成交数", "推广成交数", "广告成交订单数"],
    "roi": ["roi", "ROI", "投产", "投产比", "投入产出比", "广告ROI", "推广ROI"],
    "organic_visitor_count": ["organic_visitor_count", "自然流量访客数", "自然访客数", "自然流量"],
    "paid_visitor_count": ["paid_visitor_count", "付费流量访客数", "付费访客数", "付费流量"],
}

IDENTITY_ALIASES: Dict[str, List[str]] = {
    "platform": ["platform", "平台", "渠道", "来源平台"],
    "store_id": ["store_id", "storeId", "店铺ID", "店铺id", "店铺编号", "店铺编码"],
    "store_name": ["store_name", "store", "storeName", "店铺", "店铺名称", "店铺名", "门店", "店名"],
    "product_id": ["product_id", "productId", "商品ID", "商品id", "商品编码", "商品编号", "商家编码", "平台商品ID", "平台商品id", "宝贝ID", "宝贝id", "货号", "款号"],
    "sku_id": ["sku_id", "skuId", "SKU ID", "SKU_ID", "SKU", "sku", "sku编码", "SKU编码", "规格编码", "平台SKU", "平台SKU ID"],
    "erp_product_code": ["erp_product_code", "ERP商品编码", "ERP编码", "内部商品编码", "公司商品编码", "商品主档编码"],
    "product_link": ["product_link", "link", "url", "商品链接", "平台链接", "链接", "宝贝链接"],
    "product_name": ["product_name", "商品名称", "商品名", "商品标题", "标题", "宝贝标题", "品名"],
    "category_l1": ["category_l1", "一级类目", "一级分类", "大类目"],
    "category_l2": ["category_l2", "二级类目", "二级分类", "子类目"],
    "product_tag": ["product_tag", "商品标签", "标签", "商品状态"],
    "stat_date": ["stat_date", "统计日期", "日期", "数据日期", "报表日期"],
    "traffic_source": ["traffic_source", "流量来源", "渠道来源", "来源", "流量渠道"],
}

RATE_FIELDS = {"gross_margin_rate", "click_rate", "payment_conversion_rate", "refund_rate"}
MONEY_FIELDS = {"avg_order_value", "sale_price", "product_cost_amount", "payment_amount", "gross_profit_amount", "refund_amount", "ad_spend"}
COUNT_FIELDS = {"inventory_qty", "visitor_count", "page_view_count", "click_user_count", "click_count", "payment_buyer_count", "payment_order_count", "payment_unit_count", "refund_order_count", "ad_click_count", "ad_order_count", "organic_visitor_count", "paid_visitor_count"}


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-—/\\（）()\[\]【】:*：%]+", "", text)


def _alias_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for canonical, aliases in {**METRIC_ALIASES, **IDENTITY_ALIASES}.items():
        for alias in [canonical, *aliases]:
            index[normalize_header(alias)] = canonical
    return index

ALIAS_INDEX = _alias_index()


def canonical_field(header: Any) -> str | None:
    return ALIAS_INDEX.get(normalize_header(header))


def pick(row: Dict[str, Any], canonical: str, default: Any = None) -> Any:
    candidates = []
    if canonical in METRIC_ALIASES:
        candidates = [canonical, *METRIC_ALIASES[canonical]]
    elif canonical in IDENTITY_ALIASES:
        candidates = [canonical, *IDENTITY_ALIASES[canonical]]
    else:
        candidates = [canonical]
    for field in candidates:
        if row.get(field) not in {None, ""}:
            return row.get(field)
    # final fallback: canonicalize current row headers
    for key, value in row.items():
        if canonical_field(key) == canonical and value not in {None, ""}:
            return value
    return default


def as_float(value: Any, default: float | None = None) -> float | None:
    if value in {None, ""}:
        return default
    text = str(value).replace(",", "").replace("¥", "").strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def as_rate(value: Any, default: float | None = None) -> float | None:
    if value in {None, ""}:
        return default
    text = str(value).replace(",", "").replace("¥", "").strip()
    percent = text.endswith("%")
    if percent:
        text = text[:-1]
    try:
        number = float(text)
    except (TypeError, ValueError):
        return default
    return number / 100 if percent else number


def format_number(value: float | None, default: str = "—") -> str:
    if value is None:
        return default
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def format_rate(value: float | None, default: str = "—") -> str:
    if value is None:
        return default
    return f"{value:.2%}"


def format_money(value: float | None, default: str = "—") -> str:
    if value is None:
        return default
    return f"¥{value:.2f}"


def metric_value(row: Dict[str, Any], metric_code: str) -> float | None:
    value = pick(row, metric_code)
    if metric_code in RATE_FIELDS:
        return as_rate(value)
    return as_float(value)


def format_metric(metric_code: str, value: Any, default: str = "—") -> str:
    number = value if isinstance(value, (int, float)) else metric_value({metric_code: value}, metric_code)
    if number is None:
        return default
    if metric_code in RATE_FIELDS:
        return format_rate(float(number), default)
    if metric_code in MONEY_FIELDS:
        return format_money(float(number), default)
    return format_number(float(number), default)


def display_short_title(row: Dict[str, Any], fallback: str = "导入商品") -> str:
    title = str(pick(row, "product_name", fallback) or fallback).strip()
    return title[:16]


def product_identity(row: Dict[str, Any]) -> Dict[str, str | None]:
    platform = str(pick(row, "platform", "导入平台") or "导入平台").strip()
    store_id = str(pick(row, "store_id", "") or "").strip() or None
    store_name = str(pick(row, "store_name", "") or "").strip() or None
    product_id = str(pick(row, "product_id", "") or "").strip() or None
    sku_id = str(pick(row, "sku_id", "") or "").strip() or None
    erp_code = str(pick(row, "erp_product_code", "") or "").strip() or None
    product_link = str(pick(row, "product_link", "") or "").strip() or None
    return {
        "platform": platform,
        "storeId": store_id,
        "storeName": store_name,
        "productId": product_id,
        "skuId": sku_id,
        "erpProductCode": erp_code,
        "productLink": product_link,
        "statDate": str(pick(row, "stat_date", "") or "").strip() or None,
    }


def stable_code(prefix: str, *parts: Any) -> str:
    source = "::".join(str(part or "") for part in parts)
    digest = sha1(source.encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


def system_codes(row: Dict[str, Any]) -> Dict[str, str | None]:
    identity = product_identity(row)
    store_key = identity.get("storeId") or identity.get("storeName") or "GLOBAL"
    product_key = identity.get("productId") or identity.get("erpProductCode") or identity.get("productLink") or display_short_title(row)
    sku_key = identity.get("skuId") or product_key
    return {
        "systemStoreCode": stable_code("STORE", identity.get("platform"), store_key),
        "systemSpuCode": stable_code("SPU", product_key),
        "systemLinkCode": stable_code("LINK", identity.get("platform"), store_key, product_key),
        "systemSkuCode": stable_code("SKU", identity.get("platform"), store_key, product_key, sku_key) if sku_key else None,
    }


def extract_metric_facts(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    identity = product_identity(row)
    codes = system_codes(row)
    facts: List[Dict[str, Any]] = []
    for metric_code in METRIC_ALIASES:
        raw_value = pick(row, metric_code)
        if raw_value in {None, ""}:
            continue
        value = metric_value(row, metric_code)
        facts.append({
            "metricCode": metric_code,
            "metricValue": value,
            "rawValue": raw_value,
            "displayValue": format_metric(metric_code, value),
            "identity": identity,
            "systemCodes": codes,
            "catalogVersion": CATALOG_VERSION,
        })
    return facts


def recognized_fields(headers: Iterable[Any]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    for header in headers:
        canonical = canonical_field(header)
        if canonical:
            result.append({"sourceField": str(header), "canonicalField": canonical})
    return result
