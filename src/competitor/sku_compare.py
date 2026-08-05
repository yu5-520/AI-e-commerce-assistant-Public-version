from __future__ import annotations

from typing import Dict, List


def _split_pipe(value: object) -> List[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def summarize_sku_gap(competitors: List[Dict[str, str]]) -> Dict[str, object]:
    """Summarize same-category SKU structures from competitor mock rows."""
    structures = [row.get("sku_structure", "") for row in competitors if row.get("sku_structure")]
    bad_reviews: List[str] = []
    for row in competitors:
        bad_reviews.extend(_split_pipe(row.get("bad_review_keywords")))

    high_frequency_bad_reviews = sorted(set(bad_reviews))
    size_related = [keyword for keyword in high_frequency_bad_reviews if "尺码" in keyword or "袖长" in keyword]

    return {
        "competitor_sku_structures": structures,
        "high_frequency_bad_reviews": high_frequency_bad_reviews,
        "size_related_risk": bool(size_related),
        "insight": "同类目竞品普遍围绕颜色、尺码和版本做 SKU 扩展；若尺码相关差评明显，上新前必须强化尺码表和客服引导。",
    }
