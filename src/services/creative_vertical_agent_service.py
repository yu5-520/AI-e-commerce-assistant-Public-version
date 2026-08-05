"""V5 vertical category creative Agent service.

The creative Agent now reads product facts from ModuleProjection when the caller
does not provide explicit productFacts. It remains advisory-only and never
publishes products, changes live store content, or writes back to ERP / CRM.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.services.account_service import current_user
from src.services.experience_memory_service import list_category_profiles, search_cases
from src.services.module_projection_service import projected_products
from src.services.module_task_service import create_task

CREATIVE_AGENT_VERSION = "5.0.2"
FORBIDDEN_ACTIONS = ["不直接发布商品", "不直接改价", "不直接投放", "不直接生成夸大承诺", "不直接回写 ERP / CRM / 店铺后台"]
DEFAULT_CATEGORY_ID = "home_living_goods"
DEFAULT_SUBMIT_METRICS = ["曝光", "点击率", "转化率", "收藏加购", "退款率", "客服咨询关键词"]

PLATFORM_RULES: Dict[str, Dict[str, Any]] = {
    "淘宝": {"titleFocus": ["搜索关键词覆盖", "材质", "场景", "规格可信度"], "imageFocus": ["容量可视化", "尺寸参照", "使用前后对比"], "trafficTypes": ["搜索流量", "推荐流量"], "avoid": ["标题堆砌无关词", "主图夸大容量", "承诺超出商品事实"]},
    "拼多多": {"titleFocus": ["到手价值", "套装数量", "价格带", "耐用"], "imageFocus": ["数量对比", "家庭囤货场景", "材质耐用展示"], "trafficTypes": ["搜索流量", "活动流量", "推荐流量"], "avoid": ["只强调便宜不解释价值", "过度低价暗示", "忽略售后预期"]},
    "抖音小店": {"titleFocus": ["场景痛点", "短句卖点", "人群需求", "即时改善"], "imageFocus": ["首屏冲击", "前后对比", "真实使用场景"], "trafficTypes": ["内容推荐", "直播承接", "短视频引流"], "avoid": ["静态堆字", "无场景抽象卖点", "过度医疗或功效承诺"]},
    "通用": {"titleFocus": ["商品事实", "核心卖点", "使用场景", "风险边界"], "imageFocus": ["真实展示", "对比表达", "证据可视化"], "trafficTypes": ["搜索流量", "推荐流量"], "avoid": ["夸大承诺", "无证据卖点", "平台敏感词"]},
}


def _viewer(user_id: str | None) -> Dict[str, Any]:
    user = current_user(user_id)
    return {"userId": user.get("id"), "roleId": user.get("roleId"), "roleName": user.get("roleName")}


def _product(product_id: str, body: Dict[str, Any] | None = None, user_id: str | None = None) -> Dict[str, Any] | None:
    body = body or {}
    if body.get("productFacts"):
        item = deepcopy(body["productFacts"])
        item.setdefault("id", product_id)
        return item
    found = next((item for item in projected_products(user_id) if item.get("id") == product_id or item.get("productId") == product_id), None)
    return deepcopy(found) if found else None


def _category_profile(category_id: str) -> Dict[str, Any]:
    for profile in list_category_profiles():
        if profile.get("categoryId") == category_id:
            return deepcopy(profile)
    return {"profileId": f"CAT-{category_id}", "memoryType": "category_profile", "categoryId": category_id, "categoryName": "通用商品类目", "riskFocus": ["承诺可信度", "价格带", "售后预期"], "creativeFocus": ["场景", "卖点", "证据"], "platformHints": {}, "qualityScore": 0.5}


def _platform_rule(platform: str | None) -> Dict[str, Any]:
    return deepcopy(PLATFORM_RULES.get(platform or "通用") or PLATFORM_RULES["通用"])


def _safe_title_base(item: Dict[str, Any], profile: Dict[str, Any], rule: Dict[str, Any]) -> str:
    name = item.get("shortName") or item.get("title") or item.get("productTitle") or item.get("id") or "商品"
    focus = (rule.get("titleFocus") or ["商品事实"])[0]
    return f"{name}｜{focus}｜{profile.get('categoryName', '通用类目')}"


def _title_variants(item: Dict[str, Any], profile: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    base = _safe_title_base(item, profile, rule)
    title_focus = rule.get("titleFocus") or []
    return [
        {"variantId": "TITLE-A", "direction": "搜索覆盖", "title": base, "testMetric": "点击率 / 搜索曝光"},
        {"variantId": "TITLE-B", "direction": "场景表达", "title": f"{item.get('shortName') or item.get('title') or '商品'}｜{(title_focus[1:] or ['场景'])[0]}｜真实使用场景", "testMetric": "点击率 / 转化率"},
        {"variantId": "TITLE-C", "direction": "风险边界", "title": f"{item.get('shortName') or item.get('title') or '商品'}｜先讲事实｜降低售后误解", "testMetric": "退款率 / 咨询关键词"},
    ]


def _image_directions(item: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    focuses = rule.get("imageFocus") or ["真实展示", "对比表达", "证据可视化"]
    return [{"directionId": f"IMG-{index + 1}", "focus": focus, "brief": f"围绕“{item.get('shortName') or item.get('title') or '商品'}”做{focus}，不堆小字，不夸大承诺。"} for index, focus in enumerate(focuses[:3])]


def _selling_points(item: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    points = [item.get("inventoryStatus"), item.get("grossMargin"), item.get("afterSales"), *(profile.get("creativeFocus") or [])]
    return [str(point) for point in points if point not in {None, "", "—"}][:5]


def run_creative_vertical_agent(product_id: str, body: Dict[str, Any] | None = None, user_id: str | None = None) -> Dict[str, Any] | None:
    body = body or {}
    item = _product(product_id, body, user_id)
    if not item:
        return None
    category_id = body.get("categoryId") or item.get("categoryId") or DEFAULT_CATEGORY_ID
    platform = body.get("platform") or item.get("platform") or "通用"
    profile = _category_profile(category_id)
    rule = _platform_rule(platform)
    problem_type = body.get("problemType") or "low_ctr_low_conversion"
    cases = search_cases(query=" ".join([item.get("title") or item.get("shortName") or product_id, platform, profile.get("categoryName") or "类目"]), category_id=category_id, platform=platform, store_id=item.get("storeId") or "global", problem_type=problem_type, effective_only=False, min_quality=0.0, limit=5).get("items") or []
    title_variants = _title_variants(item, profile, rule)
    image_directions = _image_directions(item, rule)
    test_packages = [
        {"packageId": "PKG-TITLE", "packageName": "标题测试包", "titleVariant": title_variants[0], "submitMetrics": DEFAULT_SUBMIT_METRICS, "riskCheck": rule.get("avoid") or []},
        {"packageId": "PKG-IMAGE", "packageName": "主图测试包", "imageDirection": image_directions[0], "submitMetrics": DEFAULT_SUBMIT_METRICS, "riskCheck": rule.get("avoid") or []},
    ]
    return {
        "agentId": f"CREATIVE-V502-{product_id}",
        "agentName": "标题主图垂直类目 Agent",
        "agentVersion": CREATIVE_AGENT_VERSION,
        "productId": product_id,
        "viewer": _viewer(user_id),
        "productFacts": item,
        "categoryProfile": profile,
        "platformRule": rule,
        "retrievedCases": cases,
        "titleVariants": title_variants,
        "imageDirections": image_directions,
        "sellingPointOrder": _selling_points(item, profile),
        "testPackages": test_packages,
        "summary": "已基于导入商品数据、类目经验和平台表达规则生成标题 / 主图测试包。",
        "nextStep": "人工选择测试包后进入统一任务池；Agent 不直接发布商品。",
        "forbiddenActions": FORBIDDEN_ACTIONS,
        "boundary": "创意 Agent 只生成标题、主图和测试任务草案，不直接上架或修改店铺。",
    }


def create_creative_task(product_id: str, body: Dict[str, Any] | None = None, user_id: str | None = None) -> Dict[str, Any] | None:
    body = body or {}
    agent = run_creative_vertical_agent(product_id, body=body, user_id=user_id)
    if not agent:
        return None
    packages = agent.get("testPackages") or []
    package_index = int(body.get("packageIndex", body.get("package_index", 0)) or 0)
    if package_index < 0 or package_index >= len(packages):
        package_index = 0
    selected = packages[package_index]
    item = agent.get("productFacts") or {}
    store_ids = [item.get("storeId")] if item.get("storeId") else []
    task = create_task({
        "entityType": "商品",
        "entityId": product_id,
        "riskDomain": "标题主图",
        "actionType": "测试",
        "sourceModule": "标题主图垂直类目 Agent",
        "source": "创意 Agent",
        "sourceRoute": "business-products",
        "productId": product_id,
        "storeIds": store_ids,
        "visibleStoreIds": store_ids,
        "imageLabel": item.get("imageLabel") or "创",
        "productShort": item.get("shortName") or product_id,
        "productTitle": item.get("title") or product_id,
        "title": f"执行{selected.get('packageName')}：{item.get('shortName') or product_id}",
        "platform": item.get("platform") or "通用",
        "store": item.get("store") or "经营单元",
        "priority": "中",
        "priorityLevel": "warning",
        "deadline": "明天前",
        "taskType": selected.get("packageName") or "创意测试包",
        "taskSignal": "V5 projected product creative package",
        "task": "根据测试包准备标题 / 主图方案，提交指标和风险边界。",
        "reason": agent.get("summary"),
        "judgmentTags": ["标题主图", selected.get("packageName"), item.get("platform") or "通用"],
        "submitMetrics": DEFAULT_SUBMIT_METRICS,
        "agentJudgment": {"status": "advisory_confirmed", "version": CREATIVE_AGENT_VERSION, "selectedPackage": selected, "forbiddenActions": FORBIDDEN_ACTIONS},
    })
    return {"agent": agent, "task": task, "message": "创意测试任务已进入统一任务池。"}
