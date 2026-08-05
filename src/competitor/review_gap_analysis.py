from __future__ import annotations

from collections import Counter
from typing import Dict, List


def _split_pipe(value: object) -> List[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def analyze_review_gaps(competitors: List[Dict[str, str]]) -> Dict[str, object]:
    """Find opportunity gaps from competitor bad-review keywords."""
    counter: Counter[str] = Counter()
    for row in competitors:
        counter.update(_split_pipe(row.get("bad_review_keywords")))
        if row.get("after_sales_issue"):
            counter.update(_split_pipe(row.get("after_sales_issue")))

    top_gaps = [keyword for keyword, _count in counter.most_common(8)]
    opportunity_map = []
    for keyword in top_gaps:
        if "尺码" in keyword or "袖长" in keyword:
            opportunity_map.append("补充尺码表、身高体重建议和客服尺码引导")
        elif "面料" in keyword or "闷热" in keyword or "防晒" in keyword:
            opportunity_map.append("主图和详情页避免过度承诺，补充面料真实体验说明")
        elif "色差" in keyword:
            opportunity_map.append("补充实拍图和不同光线下颜色说明")
        elif "物流" in keyword:
            opportunity_map.append("上新前确认发货周期和库存真实性")
        else:
            opportunity_map.append(f"围绕“{keyword}”补充承接说明或售后预案")

    return {
        "top_bad_review_keywords": top_gaps,
        "opportunity_actions": list(dict.fromkeys(opportunity_map)),
        "insight": "差评不是只用于避坑，也可以反推出主图、详情页、SKU 和客服 SOP 的差异化机会。",
    }
