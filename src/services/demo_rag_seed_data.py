"""Demo/MVP baseline RAG seed data.

These are not temporary mock rows. They are the structured operation experience
baseline used before the formal vector database is introduced. A future vector
RAG layer should index these cards instead of replacing them.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

DEMO_RAG_SEED_VERSION = "10.11.0"
DEMO_RAG_MIN_SEED_COUNT = 24

CATEGORY_PROFILES: List[Dict[str, Any]] = [
    {
        "profileId": "CAT-home_living_goods",
        "memoryType": "category_profile",
        "categoryId": "home_living_goods",
        "categoryName": "家居生活商品",
        "platformHints": {
            "淘宝": ["搜索关键词覆盖", "尺寸和材质可信度", "详情承接和评价证据"],
            "拼多多": ["价格带", "套装数量", "到手价值", "低价竞品冲击"],
            "抖音小店": ["痛点场景", "短视频首屏冲击", "达人承接", "评论区疑虑"],
        },
        "riskFocus": ["尺寸理解偏差", "安装预期", "材质承诺", "库存承接", "退款率", "店铺评分"],
        "creativeFocus": ["容量可视化", "使用前后对比", "场景收纳", "尺寸标注", "材质证据"],
        "qualityScore": 0.92,
    },
    {
        "profileId": "CAT-seasonal_apparel",
        "memoryType": "category_profile",
        "categoryId": "seasonal_apparel",
        "categoryName": "季节服饰商品",
        "platformHints": {"淘宝": ["搜索词季节性", "尺码覆盖", "面料承诺"], "拼多多": ["价格带", "退换率", "评价晒图"], "抖音小店": ["上身效果", "场景痛点", "主播承诺边界"]},
        "riskFocus": ["尺码偏差", "面料预期", "季节窗口", "库存尾货", "退款率"],
        "creativeFocus": ["上身对比", "面料细节", "场景穿搭", "尺码建议"],
        "qualityScore": 0.86,
    },
]


def card(
    case_id: str,
    case_type: str,
    problem_type: str,
    title: str,
    judgment: str,
    actions: List[str],
    conditions: List[str],
    result: str,
    *,
    level: str = "L3",
    effective: bool = True,
    quality: float = 0.86,
    platform: str = "通用",
    category_id: str = "home_living_goods",
    operator_style: str = "稳健型",
    not_applicable: List[str] | None = None,
    evidence: List[str] | None = None,
    cross_checks: List[str] | None = None,
    source: str = "demo_rag_seed",
) -> Dict[str, Any]:
    return {
        "caseId": case_id,
        "caseType": case_type,
        "level": level,
        "status": "seed_approved",
        "categoryId": category_id,
        "platform": platform,
        "storeId": "global",
        "problemType": problem_type,
        "operatorStyle": operator_style,
        "title": title,
        "initialJudgment": judgment,
        "effectiveActions": actions,
        "applicableConditions": conditions,
        "notApplicableConditions": not_applicable or ["指标组合不同", "缺少样本量", "目标从增长切换为清库存"],
        "resultSummary": result,
        "evidenceRequired": evidence or ["导入数据版本", "处理前后指标", "截图或报表记录"],
        "crossValidationRules": cross_checks or [],
        "qualityScore": quality,
        "effective": effective,
        "source": source,
        "seedVersion": DEMO_RAG_SEED_VERSION,
        "vectorUpgradePath": "正式上线后对 title、initialJudgment、actions、conditions、resultSummary 和 crossValidationRules 做向量化索引。",
    }


SPECS: List[Dict[str, Any]] = [
    {"id": "PLAYBOOK-low_roi_high_refund", "type": "problem_playbook", "problem": "low_roi_high_refund", "title": "低 ROI + 高退款先查承接，不先放大预算", "judgment": "ROI 低同时退款率高时，问题常常不是单纯流量，而是商品承接、详情页承诺或售后预期。", "actions": ["暂停扩大预算", "复查退款原因", "核对详情页承诺", "统一客服话术", "观察 24 小时退款率变化"], "conditions": ["点击率正常", "退款率高", "退款原因集中", "库存足够"], "result": "优先控制损耗，再判断是否换素材或继续测试。", "quality": 0.88, "cross": ["ROI × 退款率 × 点击率 × 转化率", "退款原因 × 详情页承诺 × 客服话术"]},
    {"id": "PLAYBOOK-roi_low_click_high_conversion_gap", "type": "problem_playbook", "problem": "low_roi_high_refund", "title": "点击不差但 ROI 低时先查承接页和价格带", "judgment": "点击率不差而 ROI 下滑，通常是承接、价格、评价或退款预期在拖累成交质量。", "actions": ["核对详情页首屏卖点", "对比竞品价格带", "查看评价差评词", "保留小预算验证承接改动"], "conditions": ["CTR 正常或偏高", "转化率下降", "退款率或咨询异常"], "result": "把问题从投流侧拆到承接侧，避免误砍有效流量。", "quality": 0.84, "cross": ["CTR × CVR × ROI", "价格带 × 评价 × 退款率"]},
    {"id": "XVAL-roi_refund_cross_check", "type": "cross_validation_rule", "problem": "low_roi_high_refund", "title": "ROI 下降必须和退款、转化、点击交叉验证", "judgment": "单看 ROI 容易误判，必须确认是流量质量问题、承接问题，还是售后问题。", "actions": ["先拆分 CTR、CVR、退款率", "再看花费和订单", "最后决定缩预算、换素材或改详情"], "conditions": ["ROI 下降", "导入数据包含点击、转化、退款或订单"], "result": "交叉验证后再生成任务，任务强度更稳定。", "quality": 0.91, "cross": ["ROI × CTR × CVR × 退款率 × 花费"]},
    {"id": "PLAYBOOK-inventory_activity_risk", "type": "problem_playbook", "problem": "low_inventory_activity", "title": "库存接近安全线时先确认补货周期", "judgment": "活动流量会放大库存风险，库存不足时不应只看转化数据。", "actions": ["确认供应商补货周期", "估算活动消耗", "限制活动流量", "设置下架或限量边界"], "conditions": ["库存接近安全线", "活动或推广流量上升", "供应周期不确定"], "result": "防止爆单后缺货、退款和评分受损。", "quality": 0.86, "cross": ["库存 × 销量 × 活动流量 × 补货周期"]},
    {"id": "PLAYBOOK-high_inventory_low_sales", "type": "problem_playbook", "problem": "low_inventory_activity", "title": "高库存低动销不要先补流量，先判断货品角色", "judgment": "库存高但动销低，不一定适合继续投流，可能需要清库存、组合销售或降权观察。", "actions": ["确认商品角色", "复查近 7 日销量", "测试组合/套装", "判断是否降权到清库存路径"], "conditions": ["库存高", "销量低", "点击或转化没有明显优势"], "result": "避免把广告费继续压在低动销库存上。", "quality": 0.83, "cross": ["库存量 × 7日销量 × 毛利 × 商品角色"]},
    {"id": "XVAL-inventory_weight_role_cross_check", "type": "cross_validation_rule", "problem": "low_inventory_activity", "title": "库存任务要结合店铺权重和商品角色", "judgment": "高权重店铺的主推款库存风险，要比测试店铺的普通款更强处理。", "actions": ["识别店铺权重", "识别商品角色", "计算库存承接", "决定强处理或常规观察"], "conditions": ["存在库存预警", "商品归属店铺明确"], "result": "让库存任务强度跟随店铺权重，不一刀切。", "quality": 0.92, "cross": ["库存风险 × 店铺权重 × 商品角色 × 任务强度"]},
    {"id": "PLAYBOOK-low_ctr_material_test", "type": "problem_playbook", "problem": "low_ctr_low_conversion", "title": "CTR 低先测标题主图，不直接判商品废", "judgment": "点击率低通常先看标题、主图、人群和素材，不应直接下架或大幅砍预算。", "actions": ["保留小预算", "换主图方向", "测试关键词/人群", "记录曝光和点击样本量"], "conditions": ["曝光足够", "CTR 低", "退款率未异常"], "result": "先确认流量入口问题，再判断商品承接。", "quality": 0.84, "cross": ["曝光 × CTR × 素材变量 × 人群"]},
    {"id": "PLAYBOOK-high_ctr_low_conversion", "type": "problem_playbook", "problem": "detail_page_conversion", "title": "点击高转化低要查首屏承接和评价证据", "judgment": "主图吸引了点击但转化低，常见问题是首屏承接不一致、价格带不顺或评价证据不足。", "actions": ["核对主图承诺和详情页首屏", "补充尺寸/材质证据", "检查评价区疑虑", "做小样本详情页改动测试"], "conditions": ["CTR 高", "CVR 低", "跳失或咨询集中"], "result": "把高点击流量接住，而不是盲目换流量。", "quality": 0.87, "cross": ["CTR × CVR × 详情页首屏 × 评价疑虑"]},
    {"id": "XVAL-click_conversion_split", "type": "cross_validation_rule", "problem": "low_ctr_low_conversion", "title": "点击和转化要分层归因", "judgment": "CTR 低是入口问题，CTR 高 CVR 低是承接问题，两类任务不能混成一个模板。", "actions": ["先判断 CTR", "再判断 CVR", "再选择素材测试或详情承接路径"], "conditions": ["有点击率和转化率", "需要生成商品任务"], "result": "任务会从模板复查变成问题类型处理。", "quality": 0.9, "cross": ["CTR × CVR × 曝光量 × 样本量"]},
    {"id": "PLAYBOOK-competitor_size_bad_review", "type": "problem_playbook", "problem": "competitor_signal_to_test", "title": "竞品差评集中在尺寸时，卖点要反向强化尺寸标注", "judgment": "竞品尺寸差评是可转化机会，不一定要跟价，先把自己的尺寸理解成本降下来。", "actions": ["提炼竞品差评词", "补尺寸图和对比图", "客服话术同步尺寸建议", "观察咨询和转化变化"], "conditions": ["竞品差评集中", "自身商品有明确尺寸优势"], "result": "把差评变成自己的测试卖点。", "quality": 0.86, "cross": ["竞品差评 × 自身卖点 × 详情页证据"]},
    {"id": "PLAYBOOK-competitor-low-price-not-follow-blindly", "type": "problem_playbook", "problem": "competitor_signal_to_test", "title": "竞品低价冲击先看毛利空间，不盲目跟价", "judgment": "竞品降价不等于必须跟价，先判断毛利、安全库存和可证明优势。", "actions": ["核算毛利空间", "对比套装/赠品结构", "突出材质或售后证据", "必要时只跟局部入口"], "conditions": ["竞品降价", "自身毛利或供应链优势不确定"], "result": "避免价格战把利润和履约一起打穿。", "quality": 0.82, "cross": ["竞品价格 × 毛利 × 库存 × 可证明优势"]},
    {"id": "PLAYBOOK-listing-click-high-conversion-low", "type": "problem_playbook", "problem": "listing_test_path", "title": "新品点击高转化低时保留测试，先补承接", "judgment": "新品有点击说明入口可能成立，转化低时先验证详情页、价格和评价承接。", "actions": ["保留测试流量", "补首屏卖点", "加材质/尺寸证据", "观察 24-48 小时转化"], "conditions": ["新品", "CTR 高", "CVR 低", "样本量未充分"], "result": "避免过早判死有潜力新品。", "quality": 0.83, "cross": ["新品阶段 × CTR × CVR × 样本量"]},
    {"id": "PLAYBOOK-listing-click-low-material-first", "type": "problem_playbook", "problem": "listing_test_path", "title": "新品点击低先换素材，不急着下架", "judgment": "新品点击低先处理标题、主图和场景表达，不能只按第一轮数据判死。", "actions": ["换主图和标题", "保留小预算样本", "拆分人群测试", "记录每轮素材变量"], "conditions": ["新品", "CTR 低", "曝光达到基础样本"], "result": "用低成本测试找到入口表达。", "quality": 0.81, "cross": ["新品 × 曝光 × CTR × 素材变量"]},
    {"id": "PLAYBOOK-report-erp-platform-mismatch", "type": "problem_playbook", "problem": "report_data_anomaly", "title": "ERP 商品数和平台商品数不一致，先生成数据复核任务", "judgment": "数据源不一致时不能直接生成经营动作，要先确认字段、口径和店铺归属。", "actions": ["核对商品 ID 映射", "确认店铺归属", "检查重复 SKU", "标记不可直接决策字段"], "conditions": ["ERP 与平台数据不一致", "商品/订单/库存口径冲突"], "result": "先保证数据可信，再生成经营任务。", "quality": 0.89, "cross": ["ERP × 平台后台 × 店铺归属 × 商品ID"]},
    {"id": "PLAYBOOK-report-crm-delay-refund-weight-down", "type": "problem_playbook", "problem": "report_data_anomaly", "title": "CRM 售后延迟时退款判断要降权", "judgment": "售后数据延迟会放大误判，退款任务需要标记数据时效，不应直接强处理。", "actions": ["检查 CRM 更新时间", "对比平台退款数据", "标记退款指标置信度", "等待或补拉售后数据"], "conditions": ["CRM 延迟", "退款率突然波动", "平台售后数据未同步"], "result": "避免用延迟数据误杀商品或误调预算。", "quality": 0.84, "cross": ["CRM 更新时间 × 平台售后 × 退款率波动"]},
    {"id": "XVAL-report-cross-source-trust", "type": "cross_validation_rule", "problem": "report_data_anomaly", "title": "报表异常必须先做数据源交叉可信度判断", "judgment": "ERP、CRM、广告后台、平台后台任何一方缺失时，经营动作都应降低强度或转为复核任务。", "actions": ["识别缺失数据源", "标记字段置信度", "生成数据复核任务", "禁止直接回写 ERP/CRM"], "conditions": ["导入或同步数据不完整", "字段映射不确定", "来源冲突"], "result": "把数据问题和经营问题分层，减少脏数据驱动错误动作。", "quality": 0.93, "cross": ["ERP × CRM × 广告后台 × 平台后台 × 字段映射"]},
    {"id": "NEG-low_ctr_direct_budget_cut", "type": "negative_case", "problem": "low_ctr_low_conversion", "title": "点击率低时直接砍预算可能掩盖素材问题", "judgment": "点击率低通常先看标题、主图、人群和素材，不应直接归因到商品承接。", "actions": ["保留小预算", "换主图方向", "测试人群", "再判断商品承接"], "conditions": ["点击率低", "曝光足够", "退款率未异常"], "result": "作为避坑边界召回，不作为默认建议。", "level": "L4", "effective": False, "quality": 0.78, "style": "失败案例", "source": "demo_negative_case"},
    {"id": "NEG-inventory-low-stop-all-traffic", "type": "negative_case", "problem": "low_inventory_activity", "title": "库存低直接停掉所有流量可能丢掉有效增长窗口", "judgment": "库存低要分辨主推款、替代款和补货周期，不能一刀切停流量。", "actions": ["先算补货周期", "保留可承接入口", "把高风险入口限量", "同步客服发货预期"], "conditions": ["库存低", "仍有补货或替代 SKU", "ROI 可承接"], "result": "作为库存风险避坑边界。", "level": "L4", "effective": False, "quality": 0.8, "style": "失败案例", "source": "demo_negative_case"},
    {"id": "NEG-roi-low-kill-product", "type": "negative_case", "problem": "low_roi_high_refund", "title": "ROI 低直接判商品废会丢失承接修复机会", "judgment": "ROI 低可能是素材、人群、详情页、退款预期或价格带问题，不能直接判死商品。", "actions": ["拆 CTR/CVR/退款", "看样本量", "小步测试承接", "再决定降权"], "conditions": ["ROI 低", "样本量不足或指标组合不完整"], "result": "作为 ROI 误判边界召回。", "level": "L4", "effective": False, "quality": 0.79, "style": "失败案例", "source": "demo_negative_case"},
    {"id": "PLATFORM-pdd-price-package-check", "type": "platform_rule", "problem": "competitor_signal_to_test", "title": "拼多多先看价格带和套装数量", "judgment": "拼多多场景中低价冲击经常来自套装数量和到手价，不只看单件标价。", "actions": ["核对到手价", "对比套装数量", "判断赠品结构", "再决定跟价或强调价值"], "conditions": ["平台为拼多多", "存在低价竞品"], "result": "避免只按标价做错误跟价。", "platform": "拼多多", "quality": 0.82, "cross": ["到手价 × 套装数量 × 毛利 × 评价"]},
    {"id": "PLATFORM-douyin-first-screen-proof", "type": "platform_rule", "problem": "detail_page_conversion", "title": "抖音小店要看首屏痛点和评论疑虑", "judgment": "抖音流量冲动强，首屏承诺和评论疑虑会显著影响承接。", "actions": ["复查短视频首屏", "提炼评论疑虑", "补场景证明", "同步详情页承接"], "conditions": ["平台为抖音小店", "点击或观看不差但成交弱"], "result": "让内容入口和商品承接一致。", "platform": "抖音小店", "quality": 0.83, "cross": ["首屏痛点 × 评论疑虑 × 详情承接 × 退款"]},
    {"id": "PLATFORM-taobao-search-detail-match", "type": "platform_rule", "problem": "low_ctr_low_conversion", "title": "淘宝搜索词和详情承接要一致", "judgment": "淘宝搜索流量更依赖关键词匹配，标题引来的需求必须在详情页被承接。", "actions": ["核对搜索词", "调整标题关键词", "补详情页对应证据", "观察搜索转化"], "conditions": ["平台为淘宝", "搜索流量占比高", "点击或转化异常"], "result": "减少标题引流和详情承接错位。", "platform": "淘宝", "quality": 0.82, "cross": ["搜索词 × 标题 × 详情页证据 × 转化率"]},
    {"id": "ACCEPTANCE-inventory-task-evidence", "type": "acceptance_rule", "problem": "low_inventory_activity", "title": "库存任务验收必须提交补货和调货证据", "judgment": "库存类任务没有补货周期、可调货数量和下架边界，就不能判定完成。", "actions": ["提交补货周期", "提交调货数量", "提交限量或下架边界", "提交 24 小时库存变化"], "conditions": ["库存承接任务", "需要总管复核"], "result": "让任务验收能回流成可靠经验。", "quality": 0.9, "cross": ["补货周期 × 调货数量 × 库存变化 × 发货承诺"]},
    {"id": "ACCEPTANCE-roi-task-evidence", "type": "acceptance_rule", "problem": "low_roi_high_refund", "title": "ROI 任务验收必须提交调整前后指标", "judgment": "ROI 类任务没有调整前后花费、订单、退款率和 ROI，就不能沉淀为有效经验。", "actions": ["提交调整前后 ROI", "提交花费和订单", "提交退款率变化", "说明动作变量"], "conditions": ["ROI 或投流任务", "需要复盘入库"], "result": "把动作和结果绑定，避免 RAG 学到口号。", "quality": 0.91, "cross": ["动作变量 × ROI × 花费 × 订单 × 退款率"]},
    {"id": "ACCEPTANCE-creative-task-evidence", "type": "acceptance_rule", "problem": "low_ctr_low_conversion", "title": "主图标题任务验收必须有样本量和点击率变化", "judgment": "素材任务没有曝光、点击率和素材版本记录，就不能判断有效。", "actions": ["提交素材版本", "提交曝光量", "提交点击率变化", "保留对照组"], "conditions": ["主图标题测试", "点击率异常"], "result": "防止素材测试无法复盘。", "quality": 0.88, "cross": ["素材版本 × 曝光 × CTR × 样本量"]},
]


def _build_cards() -> List[Dict[str, Any]]:
    return [
        card(
            item["id"],
            item["type"],
            item["problem"],
            item["title"],
            item["judgment"],
            item["actions"],
            item["conditions"],
            item["result"],
            level=item.get("level", "L3"),
            effective=item.get("effective", True),
            quality=item.get("quality", 0.86),
            platform=item.get("platform", "通用"),
            category_id=item.get("category", "home_living_goods"),
            operator_style=item.get("style", "稳健型"),
            cross_checks=item.get("cross", []),
            source=item.get("source", "demo_rag_seed"),
        )
        for item in SPECS
    ]


DEMO_RAG_SEED_CARDS: List[Dict[str, Any]] = _build_cards()


def seed_cards() -> List[Dict[str, Any]]:
    return deepcopy(DEMO_RAG_SEED_CARDS)


def category_profiles() -> List[Dict[str, Any]]:
    return deepcopy(CATEGORY_PROFILES)
