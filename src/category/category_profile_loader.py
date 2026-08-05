from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[2]
CATEGORY_PROFILE_DIR = ROOT_DIR / "knowledge_base" / "category_profiles"

CATEGORY_PROFILE_FILES = {
    "home_living_goods": "home_living_goods.md",
    "家居生活商品": "home_living_goods.md",
    "sun_protection_clothing": "sun_protection_clothing.md",
    "防晒服": "sun_protection_clothing.md",
}

CATEGORY_PROFILE_META = {
    "home_living_goods": {
        "category_name": "家居生活商品",
        "summary": "由 ERP 商品结构推断出的家居日用、收纳、健康家居和生活场景商品经营单元。",
    },
    "sun_protection_clothing": {
        "category_name": "防晒服 / 防晒商品样板",
        "summary": "季节性强、价格带明显、退换货和尺码问题较高的防晒商品样板。",
    },
}


def _extract_heading_section(content: str, heading: str) -> str:
    """Extract a markdown section by heading text."""
    pattern = rf"^##\s+\d+\.\s+{re.escape(heading)}\s*$"
    match = re.search(pattern, content, flags=re.MULTILINE)
    if not match:
        return ""

    start = match.end()
    next_heading = re.search(r"^##\s+\d+\.\s+", content[start:], flags=re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def _extract_bullets(section: str) -> List[str]:
    values: List[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            values.append(stripped[2:].strip("。 "))
    return values


def _extract_text_block(section: str) -> List[str]:
    blocks = re.findall(r"```text\n(.*?)\n```", section, flags=re.DOTALL)
    values: List[str] = []
    for block in blocks:
        for line in block.splitlines():
            stripped = line.strip()
            if stripped:
                values.append(stripped)
    return values


def _resolve_profile_id(category_id: str) -> str:
    if category_id in {"家居生活商品", "home_living_goods"}:
        return "home_living_goods"
    if category_id in {"防晒服", "sun_protection_clothing"}:
        return "sun_protection_clothing"
    return "home_living_goods"


def load_category_profile(category_id: str = "home_living_goods") -> Dict[str, Any]:
    """Load one operating-unit profile from knowledge_base/category_profiles."""
    profile_id = _resolve_profile_id(category_id)
    filename = CATEGORY_PROFILE_FILES.get(profile_id, CATEGORY_PROFILE_FILES["home_living_goods"])
    path = CATEGORY_PROFILE_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Category profile not found: {path}")

    content = path.read_text(encoding="utf-8")
    meta = CATEGORY_PROFILE_META.get(profile_id, CATEGORY_PROFILE_META["home_living_goods"])

    seasonality_section = _extract_heading_section(content, "核心经营周期")
    price_section = _extract_heading_section(content, "常见价格带")
    selling_points_section = _extract_heading_section(content, "核心卖点")
    image_section = _extract_heading_section(content, "主图表达方向")
    sku_section = _extract_heading_section(content, "SKU 结构建议")
    review_section = _extract_heading_section(content, "高频差评与售后风险")
    competitor_section = _extract_heading_section(content, "竞品比对维度")
    traffic_section = _extract_heading_section(content, "流量测试指标")

    return {
        "category_id": profile_id,
        "category_name": meta["category_name"],
        "source": str(path.relative_to(ROOT_DIR)),
        "summary": meta["summary"],
        "seasonality": _extract_text_block(seasonality_section),
        "price_bands": _extract_text_block(price_section),
        "selling_points": _extract_bullets(selling_points_section),
        "image_expression_rules": _extract_bullets(image_section),
        "sku_structure_rules": _extract_bullets(sku_section),
        "common_return_reasons": _extract_bullets(review_section),
        "competitor_compare_dimensions": _extract_text_block(competitor_section),
        "traffic_test_metrics": _extract_text_block(traffic_section),
        "raw_excerpt": content[:600].replace("\n", " "),
    }
