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

  window.TaskReportPage = {
    route: "task-report",
    title: "任务详情",
    async render(ctx = {}) {
      const taskId = routeTaskId(ctx);
      lastReport = null;
      if (!taskId) return missingTaskView();
      const report = await loadReport(taskId);
      return `${renderHero(report)}${renderProductObject(report)}${renderAgentJudgment(report)}${renderTaskMetricEvidence(report)}${renderSteps(report)}${renderAutoReview(report)}${renderLifecycleActions(report, taskId)}`;
    },
    mount(ctx) {
      ctx.delegate("[data-back-task-list]", "click", () => AppRouter.navigate("business-actions"));
      ctx.delegate("[data-accept-task]", "click", async (event, target) => { const taskId = target.getAttribute("data-accept-task"); target.disabled = true; try { await AppApi.acceptTask(taskId); AppRouter.schedule("accept-task-report", { taskId }); } finally { target.disabled = false; } });
      ctx.delegate("[data-submit-task]", "click", (event, target) => AppRouter.navigate("task-submit", { taskId: target.getAttribute("data-submit-task") }));
      ctx.delegate("[data-open-product]", "click", (event, target) => { const state = JSON.parse(target.getAttribute("data-open-product") || "{}"); AppRouter.navigate("business-products", state); });
    },
  };
})();
