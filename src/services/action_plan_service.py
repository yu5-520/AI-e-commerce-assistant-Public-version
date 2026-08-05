"""Problem-type decision path service.

ActionPlan remains the internal Agent contract. The operator-facing output is a
DecisionTaskDraft: imported-data evidence, supplement fields, action-first paths,
and review metadata for the todo lifecycle.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

ACTION_PLAN_VERSION = "5.0.7"

FORBIDDEN_ACTIONS = [
    "不直接改价",
    "不直接投放",
    "不直接退款",
    "不直接发布商品",
    "不直接回写 ERP / CRM / 店铺后台",
]

PROBLEM_LABELS = {
    "low_ctr_low_conversion": "点击率 / 转化率下降",
    "low_roi_high_refund": "低 ROI / 高退款",
    "low_inventory_activity": "库存承接风险",
    "competitor_signal_to_test": "竞品差评 / 机会点",
    "detail_page_conversion": "详情页承接不足",
    "report_data_anomaly": "报表数据质量异常",
    "listing_test_path": "上新测试路径",
    "general_operation": "经营异常",
}


def _text(item: Dict[str, Any] | None) -> str:
    if not item:
        return ""
    keys = ["title", "productTitle", "productShort", "riskDomain", "taskType", "taskSignal", "task", "reason", "status", "statusLevel", "suggestion", "nextStep", "risk", "refundRate", "roi", "conversion", "ctr", "inventoryStatus", "afterSales", "badReview", "opportunity", "testType", "testPlan", "sourceModule", "source"]
    values = [str(item.get(key)) for key in keys if item.get(key) is not None]
    values.extend(str(value) for value in item.get("judgmentTags") or [])
    return " ".join(values)


def infer_action_problem_type(item: Dict[str, Any] | None, *, source_module: str | None = None, fallback: str = "general_operation") -> str:
    text = _text(item)
    source = source_module or str((item or {}).get("sourceModule") or "")
    if any(word in text for word in ["库存", "补货", "缺货", "安全库存", "活动流量", "待补货", "爆品"]):
        return "low_inventory_activity"
    if any(word in text for word in ["点击", "CTR", "ctr", "主图", "标题", "素材", "创意"]):
        return "low_ctr_low_conversion"
    if any(word in text for word in ["转化率", "详情页", "承接", "落地页", "首屏卖点"]):
        return "detail_page_conversion"
    if any(word in text for word in ["ROI", "ROAS", "roi", "退款", "售后", "客服", "材质", "尺寸", "安装"]):
        return "low_roi_high_refund"
    if "competitor" in source.lower() or "竞品" in source or any(word in text for word in ["竞品", "差评", "机会点", "跟价"]):
        return "competitor_signal_to_test"
    if "listing" in source.lower() or "上新" in source or any(word in text for word in ["上新", "测款", "新品", "打样"]):
        return "listing_test_path"
    if "report" in source.lower() or "报表" in source or any(word in text for word in ["字段", "同步", "导入", "ERP", "CRM", "数据版本", "归属"]):
        return "report_data_anomaly"
    return fallback


def _product_name(item: Dict[str, Any] | None) -> str:
    item = item or {}
    return str(item.get("productShort") or item.get("shortName") or item.get("sourceName") or item.get("targetProduct") or item.get("title") or item.get("name") or item.get("id") or "经营对象")


def _field(key: str, label: str, field_type: str = "text", *, options: List[str] | None = None, required: bool = False, unit: str | None = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"key": key, "label": label, "type": field_type, "required": required}
    if options:
        item["options"] = options
    if unit:
        item["unit"] = unit
    return item


def _path(path_id: str, name: str, goal: str, fit: List[str], actions: List[str], do_not_do: List[str], review: List[str], required: List[str], risk: str) -> Dict[str, Any]:
    return {"pathId": path_id, "pathName": name, "businessGoal": goal, "fitConditions": fit, "actions": actions, "doNotDo": do_not_do, "reviewMetrics": review, "requiredSupplementFields": required, "risk": risk}


def _package(package_id: str, name: str, target_metric: str, diagnosis: str, actions: List[str], submit: List[str], evidence: List[str], acceptance: List[str], failure: List[str], review: List[str], fit: List[str], risk: str, duration: str = "24-48 小时") -> Dict[str, Any]:
    return {"packageId": package_id, "packageName": name, "targetMetric": target_metric, "diagnosis": diagnosis, "operatorAction": actions, "submitMetrics": submit, "evidenceRequired": evidence, "acceptanceCriteria": acceptance, "failureThreshold": failure, "reviewFocus": review, "fitCondition": fit, "risk": risk, "testDuration": duration}


def _readonly_evidence(problem_type: str, item: Dict[str, Any]) -> List[Dict[str, Any]]:
    keys = [("dataVersion", "数据版本"), ("latestDataVersion", "数据版本"), ("stock", "当前库存"), ("safetyStock", "安全库存"), ("refundRate", "退款率"), ("roi", "ROI"), ("roas", "ROAS"), ("ctr", "点击率"), ("conversion", "转化率"), ("orders", "订单"), ("sales", "销售额"), ("count", "记录数"), ("recordCount", "记录数"), ("store", "店铺"), ("platform", "平台")]
    seen = set()
    evidence: List[Dict[str, Any]] = []
    for key, label in keys:
        value = item.get(key)
        if key not in seen and value not in [None, "", []]:
            evidence.append({"label": label, "value": value, "source": "imported_report"})
            seen.add(key)
    for row in item.get("evidence") or []:
        if row.get("label") and row.get("value") is not None:
            evidence.append({"label": row.get("label"), "value": row.get("value"), "source": "alert_event"})
    return evidence[:8]


def _inventory_contract(name: str) -> Dict[str, Any]:
    fields = [_field("can_emergency_transfer", "是否可以紧急调货", "select", options=["可以", "不可以", "待确认"], required=True), _field("transfer_quantity", "最多可调货数量", "number", unit="件"), _field("transfer_source", "调货来源", "select", options=["其他仓", "其他店", "供应商", "线下库存", "无"], required=True), _field("can_compress_lead_time", "是否可以压缩补货周期", "select", options=["可以", "不可以", "待确认"], required=True), _field("earliest_arrival", "最早到货时间", "date"), _field("supplier_extra_capacity", "供应商可追加产能", "number", unit="件"), _field("replacement_sku", "可替代 SKU", "text"), _field("selected_path_note", "选择原因", "textarea")]
    paths = [_path("hot_supply_acceleration", "爆品承接加速", "保增长", ["订单或活动流量正在放大", "毛利和售后风险可承接", "调货、补货或加产有空间"], ["紧急调货或分批补货", "保留核心活动流量", "与供应商确认加急产能", "同步库存和发货承诺", "按日记录库存消耗"], ["不立即停投", "不退出有效活动", "不把流量切给无法承接的商品"], ["是否断货", "爆品周期是否延续", "加急成本是否被利润覆盖", "退款率是否稳定"], ["can_emergency_transfer", "transfer_quantity", "can_compress_lead_time", "earliest_arrival", "supplier_extra_capacity"], "供应链承接不实会放大缺货和售后风险。"), _path("supply_limit_loss_control", "供应链不足控量止损", "控风险", ["无法调货", "补货周期无法压缩", "缺货退款风险高"], ["限制活动或预算", "设置限购或控制订单进入速度", "优先保障高利润渠道", "客服提前同步发货预期"], ["不继续追高流量", "不扩大预算", "不承诺无法保证的发货时间"], ["缺货投诉是否下降", "退款率是否下降", "店铺评分是否稳定", "放弃流量损失是否可接受"], ["can_emergency_transfer", "can_compress_lead_time", "earliest_arrival", "selected_path_note"], "控量过慢会拖累评分，控量过急会损失有效流量。"), _path("replacement_sku_redirect", "替代商品承接", "转承接", ["主商品库存不足", "有替代 SKU 或同类商品", "替代商品库存和供应链可承接"], ["降低主商品曝光", "将资源切到替代商品", "调整关联推荐", "客服引导替代款"], ["不把全部流量压在缺货商品上", "不强推无库存替代款"], ["替代商品转化率", "整体成交是否保住", "主商品退款风险是否下降"], ["replacement_sku", "transfer_quantity", "selected_path_note"], "替代商品承接不当会损失转化并增加咨询成本。")]
    return {"commonActions": [], "supplementSchema": fields, "decisionPaths": paths, "recommendedPathId": "hot_supply_acceleration"}


def _traffic_contract(name: str) -> Dict[str, Any]:
    fields = [_field("budget_adjustable", "预算是否可调整", "select", options=["可调整", "不可调整", "待确认"], required=True), _field("campaign_constraint", "平台活动/达人/直播约束", "textarea"), _field("mis_targeting", "是否存在关键词/人群误投", "select", options=["是", "否", "待确认"]), _field("competitor_price_change", "竞品是否突然降价", "select", options=["是", "否", "未知"]), _field("replacement_product", "可转移承接商品", "text"), _field("selected_path_note", "选择原因", "textarea")]
    paths = [_path("growth_continue", "继续放量承接", "吃增长", ["ROI 可接受", "库存和售后能承接", "活动约束要求持续承接"], ["保留有效渠道", "扩大高 ROI 人群或关键词", "同步监控库存和退款"], ["不盲目全渠道加预算", "不忽略库存承接"], ["ROAS", "订单增量", "退款率", "库存消耗"], ["budget_adjustable", "campaign_constraint"], "放量必须受库存和售后承接约束。"), _path("budget_loss_control", "缩预算控损", "控损耗", ["ROI 下降但仍有测试价值", "可识别低效渠道"], ["收缩低效渠道", "保留可验证测试流量", "记录预算调整前后指标"], ["不继续扩大低效预算", "不直接清空所有测试流量"], ["花费下降", "ROAS 回稳", "有效点击占比"], ["budget_adjustable", "mis_targeting"], "过度缩量会丢失判断样本。"), _path("traffic_redirect", "流量转移", "转承接", ["当前商品承接差", "存在替代商品", "流量仍有价值"], ["把预算或入口切到替代商品", "调整关联推荐", "客服引导替代款"], ["不把流量继续压在承接弱商品上", "不让替代商品无库存承接"], ["替代转化率", "整体成交", "退款率"], ["replacement_product", "selected_path_note"], "承接对象不清会造成流量浪费。"), _path("pause_review", "停投复盘", "止损", ["ROI 失控", "退款或库存风险高", "无法解释转化下滑"], ["暂停高风险投放", "锁定掉点环节", "等待下一轮数据复盘"], ["不继续投放高风险渠道", "不在未归因前换多个变量"], ["损耗是否停止", "退款是否回落", "复盘是否定位原因"], ["selected_path_note"], "停投后必须复盘原因，否则无法沉淀经验。")]
    return {"commonActions": [], "supplementSchema": fields, "decisionPaths": paths, "recommendedPathId": "budget_loss_control"}


def _competitor_contract(name: str) -> Dict[str, Any]:
    fields = [_field("has_same_product", "是否有同类商品", "select", options=["有", "没有", "待确认"], required=True), _field("price_room", "是否有价格调整空间", "select", options=["有", "没有", "待确认"]), _field("proof_advantage", "可证明优势", "textarea"), _field("supply_advantage", "供应链优势", "textarea"), _field("selected_path_note", "选择原因", "textarea")]
    paths = [_path("price_follow", "价格跟随", "缩小价格劣势", ["价格差距是核心问题", "毛利允许"], ["小幅跟随价格", "同步复核毛利", "观察转化变化"], ["不无底线降价", "不牺牲毛利追销量"], ["转化率", "毛利率", "订单量"], ["price_room"], "价格跟随会压缩利润。"), _path("selling_point_offset", "卖点错位", "避开正面对抗", ["不能打价格", "有材质/功能/服务优势"], ["重排标题主图卖点", "突出可证明差异", "小流量测试"], ["不正面攻击竞品", "不虚构优势"], ["点击率", "转化率", "咨询关键词"], ["proof_advantage"], "卖点必须可证明。"), _path("bad_review_counter", "差评反打", "把竞品问题转成自己的卖点", ["竞品差评集中", "自己能解决该痛点"], ["整理差评痛点", "生成不点名反向卖点", "测试主图/详情页表达"], ["不抄竞品", "不直接攻击竞品"], ["点击率", "转化率", "差评关键词变化"], ["proof_advantage", "has_same_product"], "差评反打必须合规。"), _path("give_up_follow", "放弃跟进", "避免无效消耗", ["毛利/供应链/转化都不支持"], ["记录放弃原因", "保持观察", "转向其他机会"], ["不继续投入素材和预算", "不强行跟价"], ["机会成本", "后续竞品变化"], ["selected_path_note"], "放弃不是失败，是避免无效投入。")]
    return {"commonActions": [], "supplementSchema": fields, "decisionPaths": paths, "recommendedPathId": "bad_review_counter"}


def _listing_contract(name: str) -> Dict[str, Any]:
    fields = [_field("has_spot_inventory", "是否有现货", "select", options=["有", "没有", "待确认"], required=True), _field("supplier_quote", "供应商报价", "text"), _field("has_material", "是否有素材图", "select", options=["有", "没有", "待确认"]), _field("test_budget", "预计测试预算", "number", unit="元"), _field("category_limit", "平台类目限制", "textarea"), _field("selected_path_note", "选择原因", "textarea")]
    paths = [_path("light_test", "轻量测款", "低成本验证点击和收藏", ["有现货", "有素材", "风险低"], ["上架轻量测试链接", "控制预算", "观察点击收藏"], ["不大批量备货", "不一次改多个变量"], ["点击率", "收藏加购", "询单"], ["has_spot_inventory", "has_material", "test_budget"], "轻量测款不能承诺大规模交付。"), _path("small_batch_listing", "小批量上新", "控制库存风险", ["供应链可承接", "需求不确定"], ["小批量备货", "有限预算测试", "设置复盘周期"], ["不满仓铺货", "不跳过利润复核"], ["转化率", "售后", "周转"], ["supplier_quote", "test_budget"], "小批量仍需复核售后和周转。"), _path("review_gap_entry", "竞品差评切入", "用差异化卖点测试", ["竞品痛点明确", "自己能解决"], ["围绕痛点设计标题主图", "准备证明素材", "小范围测试"], ["不虚构能力", "不攻击竞品"], ["点击率", "转化率", "咨询关键词"], ["has_material", "selected_path_note"], "差异化必须可验证。"), _path("delay_listing", "暂缓上新", "避免盲目铺货", ["供应链/素材/利润/类目不成熟"], ["记录暂缓原因", "补齐供应商和素材", "等待下一轮机会"], ["不强行上架", "不为上新而上新"], ["补齐进度", "机会复查时间"], ["category_limit", "selected_path_note"], "暂缓要明确下一次复查条件。")]
    return {"commonActions": [], "supplementSchema": fields, "decisionPaths": paths, "recommendedPathId": "light_test"}


def _report_contract(name: str) -> Dict[str, Any]:
    fields = [_field("missing_fields", "缺失字段", "textarea"), _field("wrong_store_scope", "是否有店铺归属错误", "select", options=["有", "没有", "待确认"]), _field("wrong_product_mapping", "是否有商品 ID / SKU 映射错误", "select", options=["有", "没有", "待确认"]), _field("need_rollback", "是否需要回滚当前数据版本", "select", options=["需要", "不需要", "待确认"]), _field("selected_path_note", "处理说明", "textarea")]
    paths = [_path("field_reupload_fix", "字段补传修正", "修正已入库数据", ["字段缺失", "字段映射不完整"], ["补齐缺失字段", "重新上传修正版报表", "保留旧版本追溯记录", "重新生成模块投影"], ["不阻断已入库数据", "不手动改库表"], ["字段完整率", "模块投影是否恢复", "受影响任务是否更新"], ["missing_fields", "selected_path_note"], "修正前的已入库版本只能作为暂态，不应沉淀为经验。"), _path("scope_mapping_fix", "归属映射修正", "修正店铺和商品切片", ["店铺归属异常", "商品 ID / SKU 映射错误"], ["核对店铺归属", "修正商品 ID / SKU 映射", "刷新账号可见范围", "重新生成相关模块任务"], ["不让错误归属继续派发任务", "不绕过账号权限"], ["归属正确率", "账号可见范围", "相关任务是否同步"], ["wrong_store_scope", "wrong_product_mapping"], "归属错误会影响账号权限和任务派发。"), _path("version_rollback", "版本回滚", "恢复可信数据版本", ["导入污染", "错误版本已影响模块"], ["回滚到上一可信数据版本", "标记受影响任务", "重新刷新总览和模块", "记录回滚原因"], ["不继续基于错误版本决策", "不删除追溯日志"], ["回滚成功", "受影响任务数", "模块数据是否恢复"], ["need_rollback", "selected_path_note"], "回滚会影响已生成任务，需要保留链路。"), _path("quality_mark_observe", "异常标记观察", "保留版本并标记风险", ["数据可用但存在异常值", "暂不需要补传或回滚"], ["标记异常字段", "限制扩大任务派发", "等待下一轮报表校验", "记录观察结论"], ["不把异常数据写入正式经验", "不扩大高风险任务"], ["异常字段是否持续", "下一轮报表是否修复", "任务是否需要调整"], ["selected_path_note"], "观察数据不能替代正式复核结论。")]
    return {"commonActions": [], "supplementSchema": fields, "decisionPaths": paths, "recommendedPathId": "field_reupload_fix"}


def _general_contract(name: str) -> Dict[str, Any]:
    fields = [_field("known_external_factor", "系统外部影响因素", "textarea"), _field("selected_path_note", "选择原因", "textarea")]
    paths = [_path("small_scope_verify", "小范围验证", "先确认问题归因", ["问题类型不明确"], ["只改一个变量", "记录前后变化", "等待复盘"], ["不做大动作", "不同时改多个变量"], ["异常是否改善", "归因是否更清楚"], ["known_external_factor"], "通用路径只能用于过渡，后续要沉淀明确问题类型。")]
    return {"commonActions": [], "supplementSchema": fields, "decisionPaths": paths, "recommendedPathId": "small_scope_verify"}


def _contract(problem_type: str, item: Dict[str, Any]) -> Dict[str, Any]:
    if problem_type == "low_inventory_activity":
        return _inventory_contract(_product_name(item))
    if problem_type in {"low_roi_high_refund", "low_ctr_low_conversion", "detail_page_conversion"}:
        return _traffic_contract(_product_name(item)) if problem_type == "low_roi_high_refund" else _listing_contract(_product_name(item))
    if problem_type == "competitor_signal_to_test":
        return _competitor_contract(_product_name(item))
    if problem_type == "listing_test_path":
        return _listing_contract(_product_name(item))
    if problem_type == "report_data_anomaly":
        return _report_contract(_product_name(item))
    return _general_contract(_product_name(item))


def action_plan_for_problem(problem_type: str, *, item: Dict[str, Any] | None = None, source_module: str | None = None, rag_items: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    item = deepcopy(item or {})
    if not problem_type or problem_type == "general_operation":
        problem_type = infer_action_problem_type(item, source_module=source_module, fallback=problem_type or "general_operation")
    contract = _contract(problem_type, item)
    decision_paths = contract["decisionPaths"]
    selected_path = next((path for path in decision_paths if path["pathId"] == contract.get("recommendedPathId")), decision_paths[0])
    package = _package(f"DP-{selected_path['pathId']}", selected_path["pathName"], " / ".join(selected_path["reviewMetrics"][:2]) or "复盘指标", f"{_product_name(item)}需要选择“{selected_path['pathName']}”经营路径，并补充系统不知道的现实变量。", selected_path["actions"], selected_path["reviewMetrics"], selected_path["requiredSupplementFields"], [f"路径目标：{selected_path['businessGoal']}", *selected_path["fitConditions"][:2]], [selected_path["risk"]], [*selected_path["reviewMetrics"], "路径是否有效"], selected_path["fitConditions"], selected_path["risk"])
    return {
        "version": ACTION_PLAN_VERSION,
        "problemType": problem_type,
        "problemLabel": PROBLEM_LABELS.get(problem_type, PROBLEM_LABELS["general_operation"]),
        "actionPlanType": selected_path["pathName"],
        "diagnosis": package["diagnosis"],
        "readonlyEvidence": _readonly_evidence(problem_type, item),
        "commonActions": [],
        "supplementSchema": contract["supplementSchema"],
        "decisionPaths": decision_paths,
        "recommendedPathId": selected_path["pathId"],
        "reviewPlan": {"nextDataTrigger": "任务提交复核 / 下一轮报表导入", "reviewMetrics": selected_path["reviewMetrics"], "selectedPathRequired": True},
        "recommendedPackage": package,
        "executionPackages": [package],
        "executionSteps": selected_path["actions"],
        "evidenceRequired": [field["label"] for field in contract["supplementSchema"] if field.get("required")],
        "submitMetrics": selected_path["reviewMetrics"],
        "acceptanceCriteria": [f"已选择主路径：{selected_path['pathName']}", "已提交执行证据", "下一轮数据可复盘"],
        "failureThreshold": [selected_path["risk"]],
        "reviewFocus": selected_path["reviewMetrics"],
        "ragReferences": [case.get("caseId") for case in rag_items or []],
        "boundary": "ActionPlan 是 Agent 内部工程包；前端展示动作顺序，不展示复盘指标和工程处理包。",
        "forbiddenActions": FORBIDDEN_ACTIONS,
    }
