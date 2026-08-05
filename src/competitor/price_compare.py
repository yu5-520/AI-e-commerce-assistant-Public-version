from __future__ import annotations

from typing import Dict, List

from src.data_loader.load_mock_data import to_number


def compare_price_band(product: Dict[str, object], competitors: List[Dict[str, str]]) -> Dict[str, object]:
    """Compare one product's sale/activity price against same-category competitors."""
    competitor_prices = [to_number(row.get("activity_price")) for row in competitors]
    competitor_prices = [price for price in competitor_prices if price > 0]

    sale_price = to_number(product.get("sale_price"))
    activity_price = to_number(product.get("activity_price"))
    reference_price = activity_price or sale_price

    if not competitor_prices:
        return {
            "reference_price": reference_price,
            "competitor_min": 0,
            "competitor_avg": 0,
            "competitor_max": 0,
            "position": "unknown",
            "insight": "缺少竞品价格数据，无法判断价格带位置。",
        }

    competitor_min = min(competitor_prices)
    competitor_max = max(competitor_prices)
    competitor_avg = sum(competitor_prices) / len(competitor_prices)

    if reference_price < competitor_min:
        position = "below_market"
        insight = "当前活动价低于同类目竞品低位，需要确认利润和品质预期。"
    elif reference_price > competitor_max:
        position = "above_market"
        insight = "当前价格高于同类目竞品高位，需要更强卖点、材质或品牌背书。"
    elif reference_price <= competitor_avg:
        position = "mainstream_low"
        insight = "当前价格处于同类目主流偏低区间，可重点测试点击和转化承接。"
    else:
        position = "mainstream_high"
        insight = "当前价格处于同类目主流偏高区间，需要强化主图卖点和评价承接。"

    return {
        "reference_price": round(reference_price, 2),
        "competitor_min": round(competitor_min, 2),
        "competitor_avg": round(competitor_avg, 2),
        "competitor_max": round(competitor_max, 2),
        "position": position,
        "insight": insight,
    }
