"""V20.28 stable operating policy context for Agent1.

This is deterministic policy context, not dynamic retrieval. It defines task
boundaries, permissions and action-family meaning. Historical experience is
retrieved separately after Agent1 locks the action family.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

OPERATING_POLICY_CONTEXT_VERSION = "20.28"

OPERATING_POLICY_CONTEXT: Dict[str, Any] = {
    "version": OPERATING_POLICY_CONTEXT_VERSION,
    "contextType": "stable_operating_policy_not_dynamic_rag",
    "principles": [
        "单点指标不直接决定任务，优先使用环比、同比、趋势和强关联指标交叉判断。",
        "观察结论不进入任务队列；只有需要运营执行的明确动作才锁定动作族。",
        "库存属于仓储职责。库存和可售天数只能进入capacityConstraints或companyHooks，不能成为运营断流任务。",
        "动作族只定义能力边界，不等于最终SOP；商品级执行方案由真实Agent2生成。",
        "同批商品不得因为同一指标波动就全部选择同一动作族，应结合流量结构、效率缺口、商品角色和平台环境区分。",
        "ROI/ROAS没有明确下降或低于利润安全线证据时，不得选择roas_guard。",
        "动态历史经验不能修改Agent1的职责边界，也不能覆盖事实层。",
    ],
    "familyGuidance": {
        "title_image_test": "点击率、有效曝光或素材承接异常，且问题主要发生在标题主图表达。",
        "roas_scale": "支付增长、ROI安全且需要扩大已验证的付费入口。",
        "roas_guard": "消耗增长未带动支付、ROI下降或低于利润安全线，需要清理低效计划或校准目标。",
        "platform_activity": "自然流量或平台活动窗口存在增长机会，需要用具体权益承接。",
        "conversion_repair": "点击后转化、详情页、评价信任、价格权益或售后承接存在明确证据。",
        "similar_product_test": "需要对照同类商品、链接或变量验证，且其他动作族证据不足。",
    },
    "permissionBoundary": {
        "operator": [
            "标题主图测试",
            "权限内广告计划调整",
            "活动报名与权益配置",
            "详情页承接修复",
            "向仓储发起补货催办",
        ],
        "managerReview": [
            "超预算调整",
            "跨店铺资源迁移",
            "大幅改价",
            "下架",
            "影响店铺主推位或品牌承诺的动作",
        ],
        "forbidden": [
            "自动退款",
            "跨账号越权操作",
            "仅因库存低自动暂停全部广告或断流",
        ],
    },
    "ragBoundary": {
        "dynamicRetrievalStage": "action_pack_ready",
        "dynamicRetrievalOwner": "agent_rag_context_v2028_service",
        "policyCanBeOverriddenByExperience": False,
        "emptyExperienceBlocksTask": False,
    },
}


def build_operating_policy_context() -> Dict[str, Any]:
    """Return one projection-safe policy with all decision boundaries preserved.

    V22.3 Agent Input Transport keeps ``guardrails`` as a compact stable field.
    Mirroring the action-family, permission and RAG boundaries into that field
    prevents policy updates from being omitted from the Agent1 projection hash.
    """
    result = deepcopy(OPERATING_POLICY_CONTEXT)
    result["guardrails"] = {
        "familyGuidance": deepcopy(result["familyGuidance"]),
        "permissionBoundary": deepcopy(result["permissionBoundary"]),
        "ragBoundary": deepcopy(result["ragBoundary"]),
    }
    result["projectionContract"] = {
        "version": "22.3.0.1",
        "requiredStableSections": [
            "principles",
            "familyGuidance",
            "permissionBoundary",
            "ragBoundary",
        ],
        "experienceMayOverridePolicy": False,
    }
    return result
