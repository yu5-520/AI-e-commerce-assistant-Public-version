(function () {
  let lastReport = null;
  const s = (value) => AppShell.escape(value ?? "");
  const ENGINEERING = [
    /relationConfidence\s*(?:=|为|仅)?\s*[0-9.]+/ig,
    /candidateSignal\s*(?:=|为)?\s*(?:true|false)/ig,
    /routeSignalStrength\s*(?:=|为)?\s*\w+/ig,
    /metricSignalConfidence\s*(?:=|为)?\s*\w+/ig,
    /taskActionLevel\s*(?:=|为)?\s*\w+/ig,
    /future_trend_forecast_action_mapping/ig,
    /context_driven_flexible_sop/ig,
  ];
  const LEGACY_FALLBACK = [
    /补齐后重新运行/i,
    /缺失数据或动作方案/i,
    /动作族数据补包站/i,
    /Agent2动作方案站/i,
    /任务映射站/i,
    /补齐【/i,
    /重新运行动作族/i,
    /系统生成异常/i,
    /action_plan_missing_data/i,
    /data_evidence_task/i,
  ];
  const BUSINESS_LABELS = {
    pay_as_primary: "以付费流量作为本轮主增长路径",
    revenue_scale_opportunity: "当前商品具备小幅放量机会",
    paid_efficiency_signal: "重点验证付费流量效率",
    title_image_test: "标题与主图差异化测试",
    roas_scale: "ROAS 小步放量",
    roas_guard: "ROAS 止损收缩",
    activity_apply: "平台活动报名",
    platform_activity: "平台活动报名",
    conversion_repair: "转化链路修复",
    similar_product_test: "同类商品对照测试",
    service_repair: "售后体验修复",
  };
  const DIRECTION_BY_FAMILY = {
    title_image_test: "开展标题主图差异化测试",
    roas_scale: "分阶段提高高效计划预算",
    roas_guard: "收缩低效投放并守住ROI",
    platform_activity: "报名平台活动承接增长",
    activity_apply: "报名平台活动承接增长",
    conversion_repair: "修复详情页与转化链路",
    similar_product_test: "开展同类商品对照测试",
    service_repair: "修复售后体验与信任承接",
  };

  function arr(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
  function obj(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function task() { return lastReport?.relatedTask || {}; }
  function detail(report) { return report?.taskDetailReport || task().taskDetailReport || {}; }
  function plan(report) { return detail(report).taskPlan || task().taskPlan || task().taskCard || {}; }
  function taskIdOf(report) { return report?.taskId || report?.id || report?.relatedTask?.id || report?.relatedTask?.taskId || task().id || task().taskId || ""; }
  function routeTaskId(ctx = {}) {
    const state = ctx.state || {};
    const hashState = AppRouter.stateFromHash?.() || {};
    return state.taskId || state.task_id || state.id || hashState.taskId || hashState.task_id || hashState.id || "";
  }
  function statusOf(report) { return String(report?.taskStatus || task().status || task().workflowStatus || "待接收"); }
  function isSubmitted(report) { return /已提交|等待系统自动复盘|复盘|已完成|已归档|已写入/.test(statusOf(report)); }
  function isLegacyFallback(value) { const text = String(value ?? ""); return LEGACY_FALLBACK.some((rx) => rx.test(text)); }

  function dedupeClauses(value) {
    const parts = String(value ?? "").split(/[，,；;]/).map((item) => item.trim()).filter(Boolean);
    const seen = new Set();
    const result = [];
    parts.forEach((item) => {
      const key = item.replace(/\s+/g, "").replace(/[。.!！]+$/g, "");
      if (!key || seen.has(key)) return;
      seen.add(key);
      result.push(item);
    });
    return result.join("，");
  }

  function clean(value) {
    let text = String(value ?? "").trim();
    ENGINEERING.forEach((rx) => { text = text.replace(rx, ""); });
    text = text.replace(/[,，；;]\s*[,，；;]+/g, "，").replace(/\s+/g, " ").replace(/^[,，；;\s]+|[,，；;\s]+$/g, "");
    return dedupeClauses(text);
  }

  function displayText(value, fallback = "") {
    const raw = clean(value);
    if (!raw) return fallback;
    if (BUSINESS_LABELS[raw]) return BUSINESS_LABELS[raw];
    if (/^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(raw)) return fallback;
    return raw;
  }

  function textOf(item) {
    if (!item || typeof item === "string") return clean(item || "");
    return clean(item.title || item.action || item.summary || item.text || item.value || item.reason || "");
  }

  function agent(report) { return report?.agentOperatingJudgment || detail(report).agentOperatingJudgment || report?.agentJudgment || detail(report).agentJudgment || task().agentOperatingJudgment || task().agentJudgment || {}; }
  function operatorView(report) { const p = plan(report); return p.operatorJudgmentView || report?.operatorJudgmentView || detail(report).operatorJudgmentView || task().operatorJudgmentView || {}; }
  function productCards(report) {
    const cards = arr(report?.productActionCards || detail(report).productActionCards || report?.affectedProducts || task().productActionCards || task().affectedProducts);
    if (cards.length) return cards;
    const p = report?.productIdentity || detail(report).productIdentity || task().productIdentity;
    return p ? [p] : [];
  }
  function productIdentity(report) { return report?.productIdentity || detail(report).productIdentity || task().productIdentity || (productCards(report)[0] || {}); }
  function actionFamily(report) {
    const p = plan(report);
    return p.selectedActionFamily || p.actionFamily || p.matrixDispatch?.selectedActionFamily || p.agentJudgmentTrace?.matrixDispatch?.selectedActionFamily || task().selectedActionFamily || task().actionFamily || "";
  }
  function taskMetricEvidence(report) {
    const p = plan(report);
    return obj(
      report?.taskMetricEvidenceProjection ||
      detail(report).taskMetricEvidenceProjection ||
      p.taskMetricEvidenceProjection ||
      task().taskMetricEvidenceProjection
    );
  }
  function taskEvidenceExecutable(report) {
    const evidence = taskMetricEvidence(report);
    return evidence.evidenceStatus === "ready" && evidence.taskExecutableFromEvidence !== false && arr(evidence.metricDefinitions).length > 0 && arr(evidence.recentSnapshots).length >= 2;
  }

  function conciseDirection(value) {
    const text = displayText(value, "");
    if (!text || text.length > 24 || /[，,。；;：:]/.test(text)) return "";
    return text;
  }

  function operatingConclusion(report) {
    const p = plan(report);
    const view = operatorView(report);
    const a = agent(report);
    for (const value of [view.operatingConclusion, p.operatingConclusion, view.selectedDirection, p.selectedDirection, a.operatingConclusion]) {
      const concise = conciseDirection(value);
      if (concise) return concise;
    }
    return DIRECTION_BY_FAMILY[actionFamily(report)] || "按经营判断执行本轮验证";
  }

  function basisText(item) {
    if (typeof item === "string") return clean(item);
    if (!item || typeof item !== "object") return "";
    return clean(item.summary || item.fact || item.reason || item.finding || item.text || item.value || "");
  }

  function judgmentBasis(report, conclusion) {
    const p = plan(report);
    const view = operatorView(report);
    const a = agent(report);
    const source = [...arr(view.judgmentBasis), ...arr(p.judgmentBasis), ...arr(a.evidenceFacts), view.judgmentBasisText, view.displayReason, a.primaryBusinessSignal, a.primaryOperatingGap, a.businessHypothesis];
    const seen = new Set();
    const result = [];
    const conclusionKey = clean(conclusion).replace(/[，,。.!！；;\s]+/g, "");
    source.forEach((item) => {
      const text = basisText(item);
      const key = text.replace(/[，,。.!！；;\s]+/g, "");
      if (!text || !key || key === conclusionKey || seen.has(key)) return;
      seen.add(key);
      result.push(text);
    });
    if (!result.length) {
      const fallback = displayText(p.reason || p.businessHypothesis, "Agent 已结合商品变化、店铺状态和动作参数完成经营判断。");
      if (fallback) result.push(fallback);
    }
    return result.slice(0, 3);
  }

  function executionSop(report) {
    const fromBackend = arr(report?.operatorExecutionSop || detail(report).operatorExecutionSop || task().operatorExecutionSop).map(textOf).filter((item) => item && !isLegacyFallback(item));
    if (fromBackend.length) return fromBackend;
    return arr(report?.operatorSopSteps || report?.sopSteps || detail(report).sopSteps || task().sopSteps).map(textOf).filter((item) => item && !isLegacyFallback(item));
  }

  function renderHero(report) {
    const p = productIdentity(report);
    const title = report.title || task().title || p.productTitle || p.title || "任务详情";
    const subtitle = [p.productTitle || p.title, p.storeName || p.store || task().storeName || task().store, p.platform || task().platform].filter(Boolean).join(" · ");
    return `<section class="report-hero task-report-hero"><div><p class="task-report-kicker">经营任务</p><h2>${s(title)}</h2><p class="task-report-context">${s(subtitle || "查看经营判断、任务参考数据与执行方案")}</p></div><div class="task-report-status-card"><span>当前状态</span><strong>${s(statusOf(report))}</strong><small>${s(plan(report).executionDeadline || plan(report).deadline || "按任务时效执行")}</small></div></section>`;
  }

  function renderProductObject(report) {
    const p = productIdentity(report);
    if (!p || !(p.productId || p.systemProductCode || p.productTitle || p.title)) return `<section class="page-section"><div class="section-header"><h3>任务对象</h3><span class="status-badge">商品未绑定</span></div><p>当前任务缺少商品身份，不能进入正式执行。</p></section>`;
    const state = { productId: p.productId, productObjectId: p.productObjectId || p.productId, storeId: p.storeId || "", storeName: p.storeName || p.store || "", platformItemId: p.platformItemId || "", dataVersion: report?.dataVersion || task().dataVersion || "" };
    const rows = [["商品", p.productTitle || p.title || p.shortTitle || p.productId], ["店铺", p.storeName || p.store || "经营单元"], ["平台", p.platform || task().platform || "经营平台"], ["SKU", p.skuId || p.specification || "未标注"]];
    return `<section class="page-section task-object-section"><div class="section-header"><h3>任务对象</h3><span class="status-badge">商品已绑定</span></div><div class="task-object-grid">${rows.map(([label, value]) => `<article><span>${s(label)}</span><strong>${s(value)}</strong></article>`).join("")}</div><button type="button" class="task-inline-link" data-open-product='${s(JSON.stringify(state))}'>查看完整商品档案</button></section>`;
  }

  function renderAgentJudgment(report) {
    const p = plan(report);
    const view = operatorView(report);
    const conclusion = operatingConclusion(report);
    const basis = judgmentBasis(report, conclusion);
    const focus = displayText(view.executionFocus || view.testFocus || p.executionFocus || p.testGoal || p.creativeStrategy || p.agent2ActionPlan?.differentiationReason, "围绕本次核心经营信号执行验证");
    const stopLoss = displayText(view.riskBoundary || p.actionParameterPack?.stopLossCondition || p.agent2ActionPlan?.budgetPlan?.stopLossCondition || p.agent2ActionPlan?.executionParameters?.rollbackCondition || p.primaryRisk, "触发SOP中的停止条件时暂停动作，并恢复至执行前参数。");
    return `<section class="page-section decision-section"><div class="section-header"><h3>经营判断</h3><span class="status-badge">Agent结论</span></div><div class="task-decision-grid task-judgment-grid"><article class="task-decision-card conclusion"><span>经营结论</span><strong>${s(conclusion)}</strong></article><article class="task-decision-card basis"><span>判断依据</span><ul class="task-judgment-basis">${basis.map((item) => `<li>${s(item)}</li>`).join("")}</ul></article><article class="task-decision-card"><span>执行重点</span><p>${s(focus)}</p></article><article class="task-decision-card"><span>风险边界</span><p>${s(stopLoss)}</p></article></div></section>`;
  }

  function renderTaskMetricEvidence(report) {
    const evidence = taskMetricEvidence(report);
    const status = evidence.evidenceStatus || "evidence_missing";
    if (!taskEvidenceExecutable(report)) {
      const title = status === "baseline_only" ? "任务仅有一份有效快照" : "任务参考数据没有完成冻结";
      const reason = status === "baseline_only"
        ? "正式变化任务至少需要两次有效商品观测。当前记录只能作为基线，不能支撑执行SOP。"
        : "当前任务已经存在，但没有保存生成任务时实际引用的指标与快照。系统不会再把证据缺失误写成“基线/无变化”，该任务暂不可执行。";
      return `<section class="page-section task-evidence-blocked"><div class="section-header"><h3>任务参考数据</h3><span class="status-badge">${s(status === "baseline_only" ? "快照不足" : "证据缺失")}</span></div><strong>${s(title)}</strong><p>${s(reason)}</p></section>`;
    }
    const definitions = arr(evidence.metricDefinitions);
    const snapshots = arr(evidence.recentSnapshots);
    const windowInfo = obj(evidence.referenceWindow);
    const usageByCode = Object.fromEntries(definitions.map((item) => [item.code, item.taskUsage || "任务判断参考"]));
    const completeness = Math.round(Number(windowInfo.dataCompleteness || 0) * 100);
    return window.MetricSnapshotTable?.render?.({
      title: "任务参考数据",
      badge: `${snapshots.length} 次冻结比对`,
      definitions,
      snapshots,
      showUsage: true,
      usageByCode,
      summaryCards: [
        { label: "参考指标", value: definitions.length },
        { label: "参考起点", value: windowInfo.startBusinessDate || "—" },
        { label: "任务业务日", value: windowInfo.endBusinessDate || "—" },
        { label: "数据完整度", value: `${completeness}%`, note: "任务创建时已冻结" },
      ],
      rule: "这里只展示本任务实际引用的指标与任务生成时冻结的有效快照；商品后续上传的新报表不会改写本任务的生成依据。",
    }) || `<section class="page-section task-evidence-blocked"><div class="section-header"><h3>任务参考数据</h3><span class="status-badge">组件未加载</span></div><p>任务证据已经存在，但表格组件没有成功加载，当前任务暂不可执行。</p></section>`;
  }

  function creativePlan(report) {
    const p = plan(report);
    const trace = obj(p.agentJudgmentTrace);
    const agent2 = obj(trace.agent2ActionPlan || p.agent2ActionPlan || detail(report).agent2ActionPlan || task().agent2ActionPlan);
    for (const value of [p.creativeTestPlan, agent2.creativeTestPlan, detail(report).creativeTestPlan, task().creativeTestPlan]) if (value && typeof value === "object" && Array.isArray(value.groups) && value.groups.length) return value;
    return {};
  }
  function field(o, keys) { for (const key of keys) { const value = o?.[key]; if (value !== undefined && value !== null && String(value).trim() !== "") return value; } return ""; }
  function groupName(group, index) { return group.groupName || group.variantName || `${String.fromCharCode(65 + index)}组`; }
  function structurePairs(structure) {
    const st = obj(structure);
    const pairs = [["场景", field(st, ["scene", "background", "setting", "usageScene", "scenario"])], ["商品呈现", field(st, ["foreground", "productPosition", "product", "productDisplay", "mainSubject"])], ["视觉重点", field(st, ["focus", "highlight", "sellingPoint", "coreSellingPoint", "visualFocus"])], ["画面文案", field(st, ["copy", "textOverlay", "imageText", "mainText", "headline"])], ["画面构图", field(st, ["composition", "layout", "structure"])], ["目标", field(st, ["visualGoal", "goal", "purpose"])]];
    const result = pairs.filter(([, value]) => String(value || "").trim()).map(([label, value]) => [label, String(value)]);
    return result.length ? result : Object.entries(st).filter(([, value]) => String(value || "").trim()).slice(0, 6).map(([key, value]) => [key, String(value)]);
  }
  function renderCreativeGroup(group, index) {
    const title = group.fullTitle || group.title || group.headline || "标题待补齐";
    const words = arr(group.testFocusWords || group.focusWords || group.keywords).map(String).filter(Boolean);
    const pairs = structurePairs(group.mainImageStructure || group.imageStructure || group.imagePlan || {});
    return `<article class="creative-test-card"><div class="creative-test-card-head"><span>${s(groupName(group, index))}</span><strong>${s(group.testTheme || group.theme || group.direction || "标题主图测试")}</strong></div><div class="creative-title-line"><em>标题</em><p>${s(title)}</p></div><div class="creative-structure"><em>主图结构</em>${pairs.length ? `<dl>${pairs.map(([label, value]) => `<div><dt>${s(label)}</dt><dd>${s(clean(value))}</dd></div>`).join("")}</dl>` : `<p>主图结构待补齐</p>`}</div>${words.length ? `<div class="creative-focus-words"><em>测试重点词</em><p>${words.map((x) => `<span>${s(x)}</span>`).join("")}</p></div>` : ""}</article>`;
  }
  function renderCreativeTestPlan(report) {
    const p = plan(report);
    const creative = creativePlan(report);
    const groups = arr(creative.groups).slice(0, 5);
    if (!groups.length) return "";
    const view = operatorView(report);
    const objective = displayText(creative.testObjective || view.testFocus || p.testGoal, "通过标题词与主图表达测试修复流量承接。");
    const metrics = arr(p.reviewMetrics || creative.reviewMetrics || p.agent2ActionPlan?.reviewMetrics).slice(0, 6);
    const duration = creative.testDurationDays || p.actionParameterPack?.testDurationDays || 3;
    return `<section class="page-section action-plan-section creative-sop-section"><div class="section-header"><h3>执行SOP</h3><span class="status-badge">标题主图测试</span></div><div class="creative-test-objective"><strong>测试目标</strong><p>${s(objective)}</p></div><div class="creative-test-grid">${groups.map(renderCreativeGroup).join("")}</div><div class="creative-test-rules"><strong>统一测试参数</strong><div><span>周期：${s(duration)}天</span><span>变量：只测试标题词与主图表达差异</span><span>保持一致：预算、入口、人群和时间窗口</span><span>复盘指标：${s(metrics.length ? metrics.join("、") : "点击率、点击量、转化率、支付金额")}</span></div></div></section>`;
  }

  function renderSteps(report) {
    if (!taskEvidenceExecutable(report)) return `<section class="page-section task-evidence-blocked"><div class="section-header"><h3>执行SOP</h3><span class="status-badge">证据未就绪</span></div><p>任务参考数据未形成至少两次冻结快照，系统不会把当前方案标记为可执行，也不会开放提交入口。</p></section>`;
    if (actionFamily(report) === "title_image_test") return renderCreativeTestPlan(report) || `<section class="page-section task-evidence-blocked"><div class="section-header"><h3>执行SOP</h3><span class="status-badge">创意方案缺失</span></div><p>标题主图任务没有完整的2至5组方案，当前不可执行。</p></section>`;
    const list = executionSop(report);
    if (!list.length) return `<section class="page-section"><div class="section-header"><h3>执行SOP</h3><span class="status-badge">方案不可用</span></div><p>当前任务没有可执行的 Agent2 动作方案，不能进入提交环节。</p></section>`;
    return `<section class="page-section action-plan-section"><div class="section-header"><h3>执行SOP</h3><span class="status-badge">可执行方案</span></div><ol class="action-step-list">${list.map((item) => `<li>${s(item)}</li>`).join("")}</ol></section>`;
  }

  function renderAutoReview(report) {
    const review = report?.autoReviewPlan || detail(report).autoReviewPlan || task().autoReviewPlan || {};
    const lines = arr(review.displayLines || review.lines || []);
    if (!lines.length) return "";
    return `<section class="page-section"><div class="section-header"><h3>系统自动复盘</h3><span class="status-badge">自动</span></div><div class="report-card-list compact-report-list">${lines.map((line, index) => `<article class="report-card compact"><strong>${index + 1}. ${s(clean(line))}</strong></article>`).join("")}</div></section>`;
  }
  function actionCodes(report) { return arr(report?.visibleTaskActions || report?.availableActions || task().visibleTaskActions || task().availableActions).map((item) => String(typeof item === "string" ? item : item.action || item.code || item.key || item.type || item.id || "").toLowerCase()).filter(Boolean); }
  function lifecycleMode(report) {
    const status = statusOf(report);
    const codes = actionCodes(report);
    const has = (pattern) => codes.some((code) => pattern.test(code));
    if (isSubmitted(report)) return "submitted";
    if (!taskEvidenceExecutable(report)) return "evidence_blocked";
    if (has(/split|dispatch|approve|review/) || /待拆分|待派发|主管审批|待审批|待复核|待主管/.test(status)) return "manager";
    if (has(/accept|receive/) || /待接收/.test(status)) return "accept";
    if (has(/submit|complete|finish/) || /执行中|处理中|已接收|待提交|执行任务/.test(status)) return "submit";
    return "read";
  }
  function renderLifecycleActions(report, routeTaskIdValue) {
    const id = taskIdOf(report) || routeTaskIdValue;
    const mode = lifecycleMode(report);
    const copy = { submitted: "执行痕迹已提交，等待系统按后续报表自动复盘。", evidence_blocked: "任务参考数据缺失或快照不足，当前不能接收、执行或提交。", manager: "当前任务处于主管拆分、派发或审批阶段，尚未进入运营提交环节。", accept: "接收任务后进入执行阶段，完成 SOP 后再提交执行痕迹。", submit: "请先完成上方 SOP，再进入提交页上传执行痕迹。", read: "当前状态暂无可执行动作。" }[mode];
    const primary = mode === "accept" ? `<button type="button" data-accept-task="${s(id)}">接收任务</button>` : mode === "submit" ? `<button type="button" data-submit-task="${s(id)}">提交执行结果</button>` : "";
    return `<section class="page-section task-action-dock"><div><span>任务下一步</span><strong>${s(copy)}</strong></div><div class="report-actions"><button type="button" class="secondary" data-back-task-list>返回任务列表</button>${primary}</div></section>`;
  }

  function missingTaskView() { return `<section class="page-section"><div class="section-header"><h2>任务详情</h2><span class="status-badge">路由缺少任务ID</span></div><p>当前地址没有携带 taskId，系统不会猜测或打开其他任务。</p><div class="report-actions"><button type="button" data-back-task-list>返回任务列表</button></div></section>`; }
  function renderKnowledgeReuse(audit) {
    const summary = audit?.reuseSummary || {};
    const events = arr(audit?.reuseEvents);
    const proofLabels = { VERIFIED: "检索证据与知识命中已核对", INVALID_EVIDENCE: "检索证据校验失败", REVISION_NOT_MATCHED: "该知识版本不在命中集合", NOT_RECORDED: "未找到检索记录", NOT_CHECKED: "未校验检索记录" };
    const labels = { success: "记录为成功", failure: "记录为失败", neutral: "记录为中性" };
    if (!events.length) return `<p>尚无通过校验的后续复用结果。</p>`;
    const rate = summary.successRate == null ? "未计算" : `${(summary.successRate * 100).toFixed(2)}%`;
    return `<details><summary>后续复用记录：${s(summary.total)} 条 · 成功占比 ${s(rate)}</summary>
      <p>统计范围：当前展示且通过校验的记录，最多 100 条。中性结果计入分母。</p>
      ${summary.truncated ? "<p>记录超过展示上限，以下占比只代表当前样本。</p>" : ""}
      ${summary.invalidRecordCount ? `<p>${s(summary.invalidRecordCount)} 条无效或来源未验证的记录已排除。</p>` : ""}
      <details><summary>查看计算公式与依据</summary><p>成功数 ÷（成功数 + 失败数 + 中性数）</p>
        <pre>${s(JSON.stringify({ formula: summary.formula, version: summary.formulaVersion, inputs: summary.inputs, eventHashes: summary.eventHashes }, null, 2))}</pre></details>
      <p>其中 ${s(summary.verifiedRetrievalCount ?? 0)} 条已核对检索回执与知识命中。结果记录不代表 RAG 的因果提升；索引内容与当前索引头见逐条证明；生产生效仍需另行验证。后续任务关联表示其冻结输入引用了该检索，不代表复用结果由该任务产生。</p>
      ${events.map(event => `<details><summary>${s(labels[event.outcome] || "未知结果")} · ${s(event.createdAt)}</summary>
        <p>知识版本：${s(event.revisionId)}</p><p>引用的检索回执：${s(event.retrievalReceiptHash)}</p>
        <p class="sop-evidence-hash">事件校验依据：${s(event.eventHash)}</p>
        <p>${s(proofLabels[event.retrievalReceiptVerification] || "未校验检索记录")}</p>
        ${event.retrievalProof ? `<details><summary>检索数据与计算依据</summary>
          <p>候选 ${s(event.retrievalProof.candidateCount)} · 可用 ${s(event.retrievalProof.eligibleCount)} · 选中 ${s(event.retrievalProof.matchedCount)} · 耗时 ${s(event.retrievalProof.latencyMs)} 毫秒</p>
          <p>选中占比 = 选中数 ÷ 可用数（不等同召回率）。可用数为零时不计算。</p>
          <p>索引核对：${s(({ VERIFIED: "索引清单与知识内容已核对", INVALID_EVIDENCE: "索引证据校验失败", REVISION_CONTENT_MISMATCH: "知识版本内容不匹配", NOT_RECORDED: "未找到索引清单" })[event.retrievalProof.indexManifestVerification] || "未校验")}</p>
          ${event.retrievalProof.indexProof ? `<p>${s(({ CURRENT_DATABASE_HEAD: "与当前数据库索引头一致", HISTORICAL_MANIFEST: "检索引用的是历史索引", HEAD_NOT_RECORDED: "未记录当前索引头" })[event.retrievalProof.indexProof.headRelation])}；此结果不证明生产启用状态。</p>` : ""}
          <details><summary>后续任务关联：${s(({ VERIFIED: "已核对", INVALID_EVIDENCE: "关联证据损坏", NOT_RECORDED: "未记录" })[event.retrievalProof.subsequentTaskBinding] || "未校验")}</summary>
            <p>来源为任务保存时冻结的输入回执。最多展示 20 条；历史任务未记录时不补造关联。</p>
            ${event.taskBindingsTruncated ? "<p>关联超过上限，当前仅展示部分记录。</p>" : ""}
            <pre>${s(JSON.stringify(event.taskBindings || [], null, 2))}</pre></details>
          <pre>${s(JSON.stringify(event.retrievalProof, null, 2))}</pre></details>` : ""}</details>`).join("")}
    </details>`;
  }

  function renderSopEvidence(report) {
    const evidence = report?.sopEvidence || {};
    const valueText = (value) => value == null ? "未记录" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
    const cards = arr(evidence.cards);
    const kinds = { FACT: "原始观测", DERIVED: "系统计算", PLAN: "计划参数", DECISION: "决策记录", PRESET: "初始化方法／预设" };
    return `<div class="sop-evidence" aria-label="SOP数据与决策依据"><h4>数据与决策依据</h4>
      <p>点击数据或计划查看来源；未记录的依据不会事后补写。</p>
      ${cards.length ? cards.map((card) => `<details class="sop-evidence-card"><summary>${s(card.label)} · ${s(kinds[card.kind] || "记录")}：${s(valueText(card.value).slice(0, 100))}</summary>
        <pre>${s(valueText(card.value))}</pre>
        ${card.formula ? `<p>计算公式：<code>${s(card.formula)}</code></p>` : ""}
        ${card.reason ? `<p>${s(card.reason)}</p>` : ""}
        ${card.inputs ? `<p>冻结数据来源</p><pre>${s(valueText(card.inputs))}</pre>` : ""}
        ${card.actor ? `<p>制定者：${s(card.actor)} · 节点：${s(card.nodeKey || "未记录")}</p>` : ""}
        ${card.sourceHash ? `<details><summary>查看校验依据</summary><p class="sop-evidence-hash">${s(card.sourceHash)}</p></details>` : ""}
      </details>`).join("") : `<p role="status">当前任务没有已验证的决策记录。历史任务不会补造依据。</p>`}
      <details><summary>评测数据与计算公式</summary>
        <p>以下读取已保存的评测；未观测的数据保留缺失状态。</p>
        ${arr(evidence.evaluation?.items).length ? arr(evidence.evaluation.items).map(item => `<details class="sop-evidence-card"><summary>${s(item.metricId)}：${s(valueText(item.value))} ${s(item.unit || "")}</summary>
          <p>公式：<code>${s(item.formula || "未记录")}</code> · 版本 ${s(item.metricVersion)}</p>
          <p>样本量：${s(item.sampleCount)} · 缺失原因：${s(item.missingReason || "无")}</p>
          <pre>${s(valueText({ inputs: item.formulaInputs, window: item.observationWindow, limits: item.interpretationLimits, sourceHash: item.sourceHash }))}</pre>
        </details>`).join("") : "<p>尚无已持久化评测结果。</p>"}
      </details>
      <details><summary>知识引用与审核回流</summary><pre>${s(valueText(evidence.knowledge || {}))}</pre><p>${s(evidence.knowledgeEffect || "尚无对照评测证据")}</p><p>${s(evidence.knowledgeAudit?.status === "RECORDED" ? "已记录任务关联知识版本与审核事件" : evidence.knowledgeAudit?.status === "INVALID_EVIDENCE" ? "部分审核证据校验失败" : "未记录审核回流结果")}</p><pre>${s(valueText({ revisions: evidence.knowledgeAudit?.revisions || [], reviewEvents: evidence.knowledgeAudit?.events || [] }))}</pre>${renderKnowledgeReuse(evidence.knowledgeAudit)}</details>
      <details><summary>运营复核与系统审核</summary>
        <p>运营复核是人工记录；系统审核结果以本任务保存的回执为准。复核记录保存不等于生命周期转换成功。</p>
        <p>仅显示最近保留的 10 条记录；旧记录缺少冻结回执时不补造证明。</p>
        <pre>${s(valueText(evidence.operatorReviews || { status: "NOT_RECORDED" }))}</pre>
      </details>
      <details><summary>修订节点前后差异</summary>
        ${evidence.revision ? `<p>变化 ${s(evidence.revision.changedCount)} 个 · 未变化 ${s(evidence.revision.unchangedCount)} 个节点。</p>
          <p>变化数 = 前后节点哈希不相等的节点数量。以下验证内容及范围一致性，授权来源和生产接管需单独验证。</p>
          ${arr(evidence.revision.nodes).map(node => `<details><summary>${s(node.nodeKey)} · ${node.changed ? "已变化" : "未变化"}</summary><pre>${s(valueText(node))}</pre></details>`).join("")}
          <details><summary>修订与审核引用</summary><pre>${s(valueText({ scopeHash: evidence.revision.scopeHash, reviewHash: evidence.revision.reviewHash, parentGraphHash: evidence.revision.parentGraphHash, targetGraphHash: evidence.revision.targetGraphHash, receiptHash: evidence.revision.receiptHash }))}</pre></details>` : "<p>未记录经过验收的修订结果。</p>"}
      </details>
      <details><summary>证据完整性</summary><p>${arr(evidence.missing).length ? "部分依据缺失或校验未通过" : arr(evidence.receipts).length ? "已记录证据校验通过" : "尚未记录证据回执"}</p><pre>${s(valueText(evidence.receipts || []))}</pre></details>
    </div>`;
  }

  function renderStepsWithEvidence(report) {
    const html = renderSteps(report);
    const end = html.lastIndexOf("</section>");
    return end < 0 ? html : html.slice(0, end) + renderSopEvidence(report) + html.slice(end);
  }

  async function loadReport(taskId) {
    if (!taskId) {
      const error = new Error("missing_task_id");
      error.frontendDiagnostic = { route: "task-report", taskId: "", stage: "task_report_load", requestPath: "", errorName: error.name, errorMessage: error.message, timestamp: new Date().toISOString() };
      throw error;
    }
    try {
      const report = await AppApi.taskReport(taskId, { forceNetwork: true, timeoutMs: 7000 });
      lastReport = report || {};
      return lastReport;
    } catch (error) {
      const previous = error?.frontendDiagnostic && typeof error.frontendDiagnostic === "object" ? error.frontendDiagnostic : {};
      error.frontendDiagnostic = {
        ...previous,
        route: "task-report",
        taskId,
        stage: previous.stage || "task_report_load",
        requestPath: previous.requestPath || `/api/view/tasks/${encodeURIComponent(taskId)}`,
        errorName: previous.errorName || error?.name || "Error",
        errorMessage: previous.errorMessage || error?.message || String(error),
        timestamp: previous.timestamp || new Date().toISOString(),
      };
      throw error;
    }
  }

  let workspace = null, selectedStep = "", stepNotice = "";
  const stepDrafts = new Map(), workspaceCache = new Map();
  const stepUrl = id => `/api/ops/tasks/${encodeURIComponent(id)}/steps`;
  async function loadWorkspace(id) {
    const cached = workspaceCache.get(id);
    const response = await fetch(stepUrl(id), {cache:"no-cache", headers:cached ? {"If-None-Match":`"${cached.headHash}"`} : {}});
    if (response.status===304 && cached) return cached;
    const body=await response.json();
    if(!response.ok) throw new Error(body.detail || "步骤暂不可用");
    workspaceCache.set(id,body); return body;
  }
  function draftKey(node) {return `${workspace.taskId}:${workspace.graphHash}:${node.nodeHash}`;}
  function currentStep() {return workspace?.steps.find(x=>x.node.nodeKey===selectedStep) || workspace?.steps[0];}
  function captureStep() {
    const step=currentStep(), form=document.querySelector("#step-result-form");
    if(step && form) {const old=stepDrafts.get(draftKey(step.node)) || {}; stepDrafts.set(draftKey(step.node),{...old,summary:form.querySelector("textarea").value,files:form.querySelector("input[type=file]").files.length ? Array.from(form.querySelector("input[type=file]").files) : (old.files || [])});}
  }
  function stageWorkspace() {
    if(!workspace) return `<section class="page-section"><p role="status">${s(stepNotice || "当前任务没有可用的图谱步骤")}</p></section>`;
    const selected=currentStep(); if(!selected)return `<section class="page-section">尚无执行步骤</section>`;
    const n=selected.node, draft=stepDrafts.get(draftKey(n)) || {};
    const history=selected.records.map(r=>`<article class="step-record"><strong>${s(r.submittedAt)}</strong><span>${r.graphHash===workspace.graphHash && r.nodeHash===n.nodeHash ? "已提交 · 待验收" : "历史版本"}</span><p>${s(r.summary)}</p>${r.attachments.map(a=>`<a href="${s(stepUrl(workspace.taskId)+"/attachment?recordHash="+encodeURIComponent(r.recordHash)+"&contentHash="+encodeURIComponent(a.contentHash))}" download="${s(a.name)}">${s(a.name)} · ${s(a.size)} B</a>`).join("")}</article>`).join("");
    return `<section class="page-section step-workspace"><nav class="step-tabs" aria-label="执行步骤">${workspace.steps.map((step,i)=>`<button type="button" data-step-key="${s(step.node.nodeKey)}" aria-current="${step===selected ? "step" : "false"}"><span>${i+1}</span>${s(step.node.title || step.node.nodeKey)}<small>${step.status==="submitted" ? "已提交" : "待执行"}</small></button>`).join("")}</nav>
      <div class="step-current"><div class="section-header"><h3>${s(n.title || n.nodeKey)}</h3><span>${s(n.owner)}</span></div><p class="step-instruction">${s(n.instruction)}</p><dl><dt>执行对象</dt><dd>${s(valueText(n.executionObject))}</dd></dl>
      <details><summary>验收与停止条件</summary><p>${s(valueText(n.acceptanceActions))}</p><p>${s(valueText(n.stopConditionRefs))}</p><p>回滚：${s(valueText(n.rollback))}</p></details>
      <details><summary>数据与方案依据</summary>${renderSopEvidence(lastReport)}</details>
      <form id="step-result-form"><label>执行记录<textarea required maxlength="10000" rows="3" placeholder="记录本步骤的实际操作与结果">${s(draft.summary || "")}</textarea></label><label>上传凭证<input type="file" multiple /></label><small>最多 5 个文件，合计 2 MB${draft.files?.length ? " · 已选择 "+draft.files.length+" 个文件" : ""}</small><button type="submit">${selected.status==="submitted" ? "补交记录" : "提交本步骤"}</button></form><p role="status">${s(stepNotice)}</p>
      <details ${history ? "open" : ""}><summary>操作记录 · ${selected.records.length}</summary>${history || "尚无提交"}</details></div></section>`;
  }
  function paintWorkspace(){const el=document.querySelector("#stage-workspace");if(el)el.innerHTML=stageWorkspace();}
  async function submitCurrentStep(event) {
    event.preventDefault();captureStep();const step=currentStep();if(!step)return;
    const draft=stepDrafts.get(draftKey(step.node));const button=event.target.querySelector("button[type=submit]");button.disabled=true;
    try {
      const files=draft.files || [];if(files.length>5 || files.reduce((n,f)=>n+f.size,0)>2*1024*1024)throw new Error("附件最多 5 个，合计 2 MB");
      const attachments=await Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,base64:String(reader.result).split(",")[1]});reader.onerror=reject;reader.readAsDataURL(file);}))); 
      const body={commandId:draft.commandId || crypto.randomUUID(),graphHash:workspace.graphHash,nodeKey:step.node.nodeKey,nodeHash:step.node.nodeHash,summary:draft.summary,attachments};
      const signature=JSON.stringify({summary:body.summary,attachments});
      if(draft.signature && draft.signature!==signature)body.commandId=crypto.randomUUID();
      draft.signature=signature;draft.commandId=body.commandId;const response=await fetch(stepUrl(workspace.taskId),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw new Error(result.detail || "提交失败");
      workspace=result;workspaceCache.set(workspace.taskId,result);stepDrafts.delete(draftKey(step.node));stepNotice="记录已保存，等待验收";paintWorkspace();
    } catch(error) {stepNotice=error.message || "提交失败，请重试";paintWorkspace();} finally {button.disabled=false;}
  }

  window.TaskReportPage = {
    route: "task-report",
    title: "任务详情",
    async render(ctx = {}) {
      const taskId = routeTaskId(ctx);
      lastReport = null;
      if (!taskId) return missingTaskView();
      const report = await loadReport(taskId);
      captureStep();stepNotice="";
      try {workspace=await loadWorkspace(taskId);} catch(error){workspace=null;stepNotice=error.message;}
      return `${renderHero(report)}${lifecycleMode(report)==="accept" ? `<button type="button" data-accept-task="${s(taskId)}">接收任务</button>` : ""}<details class="page-section"><summary>经营数据与诊断</summary>${renderProductObject(report)}${renderAgentJudgment(report)}${renderTaskMetricEvidence(report)}</details><div id="stage-workspace">${stageWorkspace()}</div><button type="button" data-finish-steps="${s(taskId)}">提交全部步骤验收</button><details class="page-section"><summary>效果评测</summary>${renderAutoReview(report)}</details>`;
    },
    mount(ctx) {
      ctx.delegate("[data-step-key]", "click", (event,target)=>{captureStep();selectedStep=target.getAttribute("data-step-key");stepNotice="";paintWorkspace();});
      ctx.delegate("#step-result-form", "submit", submitCurrentStep);
      ctx.delegate("[data-finish-steps]", "click", async (event,target)=>{
        if(!workspace?.allStepsSubmitted){stepNotice="请先提交每个当前版本步骤";paintWorkspace();return;}
        target.disabled=true;
        try {const result=await AppApi.submitTask(workspace.taskId,{summary:"阶段执行记录已提交",stepHeadHash:workspace.headHash});if(result.ok===false)throw new Error(result.error || "提交失败");stepNotice="已提交任务验收";paintWorkspace();}
        catch(error){stepNotice=error.message;paintWorkspace();}finally{target.disabled=false;}
      });
      ctx.delegate("[data-back-task-list]", "click", () => AppRouter.navigate("business-actions"));
      ctx.delegate("[data-accept-task]", "click", async (event, target) => { const taskId = target.getAttribute("data-accept-task"); target.disabled = true; try { await AppApi.acceptTask(taskId); AppRouter.schedule("accept-task-report", { taskId }); } finally { target.disabled = false; } });
      ctx.delegate("[data-submit-task]", "click", (event, target) => AppRouter.navigate("task-submit", { taskId: target.getAttribute("data-submit-task") }));
      ctx.delegate("[data-open-product]", "click", (event, target) => { const state = JSON.parse(target.getAttribute("data-open-product") || "{}"); AppRouter.navigate("business-products", state); });
    },
  };
})();
