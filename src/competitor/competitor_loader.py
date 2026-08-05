from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List

ROOT_DIR = Path(__file__).resolve().parents[2]
CATEGORY_DATA_DIRS = {
    "home_living_goods": ROOT_DIR / "examples" / "category_home_living",
    "sun_protection_clothing": ROOT_DIR / "examples" / "category_sun_protection",
}


def load_mock_competitors(category_id: str = "home_living_goods") -> List[Dict[str, str]]:
    """Load same-operating-unit competitor mock data.

    MVP boundary: this reads manually prepared / mock competitor rows. It does
    not crawl real platform pages or operate on platform accounts.
    """
    data_dir = CATEGORY_DATA_DIRS.get(category_id, CATEGORY_DATA_DIRS["home_living_goods"])
    path = data_dir / "mock_competitors.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing mock competitor dataset: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def competitor_data_source(category_id: str = "home_living_goods") -> str:
    data_dir = CATEGORY_DATA_DIRS.get(category_id, CATEGORY_DATA_DIRS["home_living_goods"])
    return str((data_dir / "mock_competitors.csv").relative_to(ROOT_DIR))
