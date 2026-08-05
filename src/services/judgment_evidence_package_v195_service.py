"""V19.13 judgment package station wrapper.

Carries Agent 1 operating judgment and action-family lock into packages. Agent 2
will later generate action plans; task mapping only assembles SOP.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, loads
from src.services.judgment_evidence_package_v174_service import product_judgment_package_station_v174
from src.services.operating_judgment_brief_v195_service import enrich_and_save_operating_judgment_briefs
import src.services.dual_agent_product_task_service as base

JUDGMENT_EVIDENCE_PACKAGE_V195_VERSION = "19.13"


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _raw_agent1_index(data_version: str | None) -> Dict[Tuple[str, str], Dict[str, Any]]:
    if not data_version:
        return {}
    base.ensure_dual_agent_tables()
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    with connect() as conn:
        rows = conn.execute("SELECT payload FROM agent_product_judgments_v15 WHERE data_version = ? ORDER BY created_at ASC", (data_version,)).fetchall()
    for row in rows:
        item = _load(row["payload"])
        product_id = str(item.get("productId") or "")
        if not product_id:
            continue
        agent1 = item.get("agent1OperatingJudgment") if isinstance(item.get("agent1OperatingJudgment"), dict) else {}
        family = item.get("selectedActionFamilyHint") or agent1.get("selectedActionFamily")
        route = item.get("selectedOperatingRoute") or agent1.get("selectedOperatingRoute") or "operator_growth_task"
        if not agent1:
            agent1 = {"stage": "agent1_operating_route_judgment", "displayInDetail": True, "selectedOperatingRoute": route, "selectedActionFamily": family, "primaryBusinessSignal": item.get("finding"), "primaryOperatingGap": item.get("businessHypothesis") or item.get("finding"), "businessHypothesis": item.get("businessHypothesis") or item.get("finding"), "routeLock": {"locked": True, "selectedOperatingRoute": route, "lockReason": item.get("finding")}, "actionFamilyLock": {"locked": True, "selectedActionFamily": family, "lockReason": item.get("businessHypothesis") or item.get("finding")}}
        key = (str(item.get("storeId") or "GLOBAL"), product_id)
        if key not in index:
            index[key] = {"agent1OperatingJudgment": agent1, "selectedActionFamilyHint": family, "selectedOperatingRoute": route, "businessHypothesis": agent1.get("businessHypothesis"), "routeLock": agent1.get("routeLock"), "actionFamilyLock": agent1.get("actionFamilyLock"), "sourceJudgmentId": item.get("judgmentId")}
    return index


def _attach_agent1_locks(data_version: str | None, packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index = _raw_agent1_index(data_version)
    if not index:
        return packages
    enriched: List[Dict[str, Any]] = []
    for package in packages:
        key = (str(package.get("storeId") or "GLOBAL"), str(package.get("productId") or ""))
        agent1 = index.get(key) or index.get(("GLOBAL", key[1]))
        if not agent1:
            enriched.append(package)
            continue
        item = dict(package)
        item["agent1OperatingJudgment"] = agent1.get("agent1OperatingJudgment")
        item["selectedActionFamilyHint"] = agent1.get("selectedActionFamilyHint")
        item["selectedOperatingRoute"] = agent1.get("selectedOperatingRoute")
        item["businessHypothesis"] = agent1.get("businessHypothesis")
        item["routeLock"] = agent1.get("routeLock")
        item["actionFamilyLock"] = agent1.get("actionFamilyLock")
        item["agent1JudgmentSource"] = "agent1_operating_judgment_v1913"
        item["agent1JudgmentId"] = agent1.get("sourceJudgmentId")
        enriched.append(item)
    base._save_packages(enriched)
    return enriched


def product_judgment_package_station_v195(data_version: str | None, **kwargs: Any) -> Dict[str, Any]:
    base_result = product_judgment_package_station_v174(data_version=data_version, **kwargs)
    if base_result.get("baselineMode") == "first_report":
        base_result["version"] = JUDGMENT_EVIDENCE_PACKAGE_V195_VERSION
        base_result["operatorJudgmentBriefSkipped"] = True
        base_result["rule"] = "V19.13 first report remains baseline-only."
        return base_result

    brief_result = enrich_and_save_operating_judgment_briefs(data_version)
    packages = _attach_agent1_locks(data_version, brief_result.get("packages") or base_result.get("packages") or [])
    candidate_count = sum(1 for item in packages if item.get("taskCandidateAllowed") or item.get("candidateSignal"))
    lock_count = sum(1 for item in packages if isinstance(item.get("actionFamilyLock"), dict) and item.get("selectedActionFamilyHint"))
    by_family: Dict[str, int] = {}
    for item in packages:
        family = str(item.get("selectedActionFamilyHint") or "missing")
        by_family[family] = by_family.get(family, 0) + 1
    base_result.update({"version": JUDGMENT_EVIDENCE_PACKAGE_V195_VERSION, "packageMode": "agent1_operating_judgment_plus_action_family_lock", "operatorJudgmentBrief": {"briefCount": brief_result.get("briefCount", 0), "capacityConstraintCount": brief_result.get("capacityConstraintCount", 0), "allowedActionFamilyCoverage": brief_result.get("allowedActionFamilyCoverage", 0)}, "agent1RouteLockCount": lock_count, "bySelectedActionFamily": by_family, "productJudgmentPackageCount": len(packages), "candidatePackageCount": candidate_count, "packages": packages[:20], "productJudgmentPackageRef": f"agent1_operating_judgment_package:{data_version or 'latest'}", "outputRef": f"agent1_operating_judgment_package:{data_version or 'latest'}", "rule": "V19.13: package carries Agent1 judgment lock; Agent2 creates action plan; mapping assembles SOP."})
    return base_result
