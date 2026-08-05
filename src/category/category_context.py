from __future__ import annotations

from typing import Any, Dict

from src.category.category_profile_loader import load_category_profile
from src.category.category_rules import build_category_risk_rules, suggest_category_next_steps


def build_category_context(
    category_id: str = "home_living_goods",
    operating_unit: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the category context injected into the main workflow.

    In product logic, `category_id` should normally come from ERP-based
    operating-unit inference instead of a hardcoded demo category.
    """
    profile = load_category_profile(category_id)
    return {
        "category_profile": profile,
        "operating_unit": operating_unit or {},
        "category_rules": build_category_risk_rules(profile),
        "next_steps": suggest_category_next_steps(profile),
        "integration_status": "Category context loaded from ERP-inferred operating unit; diagnosis rules are still lightweight MVP rules.",
    }
