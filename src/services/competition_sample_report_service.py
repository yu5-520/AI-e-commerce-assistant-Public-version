"""Deterministic evaluator XLSX samples for the competition public runtime."""
from __future__ import annotations

import io
from typing import Any, Dict, List

SAMPLE_REPORT_VERSION = "1.0.0"
SAMPLE_HEADERS = [
    "product_id", "store_id", "store_name", "product_name", "category", "platform",
    "stock", "sale_price", "cost_price", "roi", "traffic", "clicks", "ctr",
    "conversion_rate", "gross_margin", "ad_spend", "sales_volume", "revenue",
    "good_review_rate", "bad_review_rate", "refund_rate",
]

SAMPLE_REPORTS: Dict[int, List[Dict[str, Any]]] = {
    1: [
        {"product_id":"COMP-P-CONVERSION","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"轻量通勤双肩包","category":"箱包","platform":"天猫","stock":860,"sale_price":199,"cost_price":82,"roi":3.2,"traffic":12000,"clicks":720,"ctr":0.06,"conversion_rate":0.052,"gross_margin":0.588,"ad_spend":31000,"sales_volume":450,"revenue":89550,"good_review_rate":0.974,"bad_review_rate":0.009,"refund_rate":0.041},
        {"product_id":"COMP-P-OBSERVE","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"基础棉质短袖","category":"服饰","platform":"天猫","stock":1260,"sale_price":79,"cost_price":31,"roi":2.42,"traffic":8200,"clicks":492,"ctr":0.06,"conversion_rate":0.048,"gross_margin":0.608,"ad_spend":11500,"sales_volume":235,"revenue":18565,"good_review_rate":0.968,"bad_review_rate":0.011,"refund_rate":0.036},
        {"product_id":"COMP-P-SCALE","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"户外防晒冲锋衣","category":"户外服饰","platform":"天猫","stock":2140,"sale_price":329,"cost_price":142,"roi":3.85,"traffic":14600,"clicks":934,"ctr":0.064,"conversion_rate":0.046,"gross_margin":0.568,"ad_spend":38000,"sales_volume":430,"revenue":141470,"good_review_rate":0.979,"bad_review_rate":0.008,"refund_rate":0.033},
    ],
    2: [
        {"product_id":"COMP-P-CONVERSION","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"轻量通勤双肩包","category":"箱包","platform":"天猫","stock":760,"sale_price":199,"cost_price":82,"roi":2.55,"traffic":15400,"clicks":1063,"ctr":0.069,"conversion_rate":0.035,"gross_margin":0.588,"ad_spend":43000,"sales_volume":372,"revenue":74028,"good_review_rate":0.967,"bad_review_rate":0.012,"refund_rate":0.048},
        {"product_id":"COMP-P-OBSERVE","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"基础棉质短袖","category":"服饰","platform":"天猫","stock":1190,"sale_price":79,"cost_price":31,"roi":2.46,"traffic":8450,"clicks":507,"ctr":0.06,"conversion_rate":0.047,"gross_margin":0.608,"ad_spend":11800,"sales_volume":238,"revenue":18802,"good_review_rate":0.969,"bad_review_rate":0.011,"refund_rate":0.037},
        {"product_id":"COMP-P-SCALE","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"户外防晒冲锋衣","category":"户外服饰","platform":"天猫","stock":1980,"sale_price":329,"cost_price":142,"roi":4.25,"traffic":17600,"clicks":1197,"ctr":0.068,"conversion_rate":0.051,"gross_margin":0.568,"ad_spend":41000,"sales_volume":555,"revenue":182595,"good_review_rate":0.981,"bad_review_rate":0.007,"refund_rate":0.031},
    ],
    3: [
        {"product_id":"COMP-P-CONVERSION","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"轻量通勤双肩包","category":"箱包","platform":"天猫","stock":705,"sale_price":199,"cost_price":82,"roi":2.08,"traffic":18800,"clicks":1372,"ctr":0.073,"conversion_rate":0.028,"gross_margin":0.588,"ad_spend":54000,"sales_volume":384,"revenue":76416,"good_review_rate":0.961,"bad_review_rate":0.015,"refund_rate":0.054},
        {"product_id":"COMP-P-OBSERVE","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"基础棉质短袖","category":"服饰","platform":"天猫","stock":1145,"sale_price":79,"cost_price":31,"roi":2.44,"traffic":8320,"clicks":499,"ctr":0.06,"conversion_rate":0.0475,"gross_margin":0.608,"ad_spend":11750,"sales_volume":237,"revenue":18723,"good_review_rate":0.969,"bad_review_rate":0.011,"refund_rate":0.0365},
        {"product_id":"COMP-P-SCALE","store_id":"COMP-STORE-1","store_name":"比赛脱敏店铺","product_name":"户外防晒冲锋衣","category":"户外服饰","platform":"天猫","stock":1810,"sale_price":329,"cost_price":142,"roi":4.62,"traffic":20500,"clicks":1456,"ctr":0.071,"conversion_rate":0.054,"gross_margin":0.568,"ad_spend":43500,"sales_volume":646,"revenue":212534,"good_review_rate":0.982,"bad_review_rate":0.007,"refund_rate":0.03},
    ],
}


def sample_report_filename(period: int) -> str:
    if period not in SAMPLE_REPORTS:
        raise ValueError("competition_sample_period_not_found")
    return f"AI经营参谋_脱敏样例_第{period}期.xlsx"


def build_competition_sample_xlsx(period: int) -> bytes:
    if period not in SAMPLE_REPORTS:
        raise ValueError("competition_sample_period_not_found")
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl_required_for_competition_sample_xlsx") from exc

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = f"第{period}期经营数据"
    worksheet.append(SAMPLE_HEADERS)
    for row in SAMPLE_REPORTS[period]:
        worksheet.append([row.get(header, "") for header in SAMPLE_HEADERS])
    buffer = io.BytesIO()
    workbook.save(buffer)
    payload = buffer.getvalue()
    if not payload.startswith(b"PK"):
        raise RuntimeError("competition_sample_xlsx_not_openxml")
    return payload


__all__ = [
    "SAMPLE_REPORT_VERSION",
    "SAMPLE_HEADERS",
    "SAMPLE_REPORTS",
    "sample_report_filename",
    "build_competition_sample_xlsx",
]
