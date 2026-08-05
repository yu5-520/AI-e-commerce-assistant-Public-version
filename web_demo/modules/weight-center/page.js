(function () {
  const s = (value) => AppShell.escape(value ?? "-");
  async function fetchJson(path) {
    const response = await fetch(path, { method: "GET", headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() } });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }
  async function get(path, params = {}) { const q = new URLSearchParams({ limit: "120" }); Object.entries(params).forEach(([k, v]) => { if (v) q.set(k, v); }); return fetchJson(`${path}?${q.toString()}`); }
  async function postAndRefresh(path, label) { const result = await AppApi.post(path, null, {}); window.alert(`生成${label} ${result.createdCount || 0} 条`); AppRouter.schedule(`${label}-generated`); }

  const loadSnapshots = () => get("/api/architecture/v8/weight-snapshots");
  const loadScores = () => get("/api/architecture/v8/weight-scores");
  const loadContext = () => get("/api/architecture/v8/context-weights");
  const loadCross = () => get("/api/architecture/v8/cross-validations");
  const loadGroups = (status = "") => get("/api/architecture/v8/weight-task-groups", { group_status: status });
  const loadApprovals = (status = "") => get("/api/architecture/v8/weight-approvals", { approval_status: status });
  const loadExecutions = (status = "") => get("/api/architecture/v8/weight-executions", { execution_status: status });
  const loadReviews = (effectiveness = "") => get("/api/architecture/v8/weight-execution-reviews", { effectiveness });

  const generateSnapshots = () => postAndRefresh("/api/architecture/v8/weight-snapshots/generate", "权重快照");
  const generateScores = () => postAndRefresh("/api/architecture/v8/weight-scores/generate", "权重评分");
  const generateContext = () => postAndRefresh("/api/architecture/v8/context-weights/generate", "上下文修正");
  const generateCross = () => postAndRefresh("/api/architecture/v8/cross-validations/generate", "交叉验证");
  const generateGroups = () => postAndRefresh("/api/architecture/v8/weight-task-groups/generate", "权重任务组");
  const generateApprovals = () => postAndRefresh("/api/architecture/v8/weight-approvals/generate", "审批流");
  const generateExecutions = () => postAndRefresh("/api/architecture/v8/weight-executions/generate", "执行回写记录");
  const generateReviews = () => postAndRefresh("/api/architecture/v8/weight-execution-reviews/generate", "执行复盘");

  async function decideApproval(approvalId, decision) {
    const response = await fetch(`/api/architecture/v8/weight-approvals/${encodeURIComponent(approvalId)}/decide`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
      body: JSON.stringify({ decision, note: `V8.9 ${decision}` }),
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    AppRouter.schedule("weight-approval-decided");
  }
  async function submitFeedback(executionId) {
    const response = await fetch(`/api/architecture/v8/weight-executions/${encodeURIComponent(executionId)}/feedback`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
      body: JSON.stringify({
        actualActions: [{ action: "人工已执行权重任务组动作", status: "done" }],
        resultMetrics: { roiDelta: 0.03, riskDelta: -0.05, complaintDelta: -0.02 },
        evidenceRefs: { screenshot: "demo-evidence", note: "V8.9 demo feedback" },
        note: "执行结果已人工回写，进入复盘。",
      }),
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    AppRouter.schedule("weight-feedback-submitted");
  }

  function metric(label, value, note) { return AppShell.metricCard(label, value, note || "V8.9"); }
  function typeName(type) { return { product: "商品", store: "店铺", operator: "运营" }[type] || type; }
  function approvalStatusName(value) { return { pending: "待审批", approved: "已通过", rejected: "已拒绝", evidence_review: "证据复核", returned_for_evidence: "退回复核", human_review: "人工复核", reviewed: "已复核" }[value] || value; }
  function gateName(value) { return { locked: "锁定", ready_for_execution: "待执行层", blocked: "阻断", human_review_only: "仅人工复核", feedback_required: "待回写", review_pending: "待复盘" }[value] || value; }
  function groupStatusName(value) { return { pending_approval: "待审批", evidence_review: "证据复核", human_review_draft: "人工复核草案", approved_pending_execution: "已批待执行", execution_feedback_required: "待执行回写", feedback_submitted: "已回写", rejected: "已拒绝", human_reviewed: "人工已复核" }[value] || value; }
  function effectivenessName(value) { return { effective: "有效", worse: "恶化", uncertain: "待观察" }[value] || value; }
  function nextDecisionName(value) { return { keep_or_restore: "保留/恢复", escalate_review: "升级复核", continue_observation: "继续观察" }[value] || value; }
  function countBlock(title, data = {}, labeler = (v) => v) { const rows = Object.entries(data); return `<section class="page-section report-section"><div class="section-header"><h3>${s(title)}</h3><span class="status-badge">${rows.length}</span></div><div class="version-alert-list">${rows.map(([key, value]) => `<article class="version-alert-row"><strong>${s(labeler(key))}</strong><span>${s(value)}</span><small>V8.9 指标</small></article>`).join("") || `<div class="log-empty">暂无数据。</div>`}</div></section>`; }

  function executionCard(item) {
    const canFeedback = item.executionStatus === "awaiting_feedback";
    return `<article class="report-card"><div><h3>${s(item.objectName || item.objectId)} · ${s(item.groupName)}</h3><p>${s(typeName(item.objectType))} · ${s(item.executionStatus)} · ${s(gateName(item.executionGate))}</p><div class="report-meta"><span class="status-badge">${s(item.executionId)}</span><span>${s(item.taskGroupId)}</span><span>执行人 ${s(item.executorId || "待回写")}</span></div><p>V8.9 只记录人工执行结果，不直接调用外部平台执行。</p></div><div class="report-actions">${canFeedback ? `<button type="button" data-submit-feedback="${s(item.executionId)}">提交回写</button>` : `<span class="status-badge">${s(gateName(item.executionGate))}</span>`}</div></article>`;
  }
  function reviewCard(item) { return `<article class="report-card"><div><h3>${s(item.objectName || item.objectId)} · ${s(effectivenessName(item.effectiveness))}</h3><p>${s(typeName(item.objectType))} · ${s(nextDecisionName(item.nextDecision))} · RAG候选：${item.ragMemoryCandidate ? "是" : "否"}</p><div class="report-meta"><span class="status-badge">${s(item.reviewStatus)}</span><span>${s(item.executionId)}</span><span>${s(item.taskGroupId)}</span></div><p>${s(item.reviewSummary)}</p></div><div class="report-actions"><span class="status-badge">复盘</span></div></article>`; }
  function approvalCard(item) { const canDecide = ["pending", "evidence_review", "human_review"].includes(item.approvalStatus); return `<article class="report-card"><div><h3>${s(item.objectName || item.objectId)} · ${s(item.groupName)}</h3><p>${s(typeName(item.objectType))} · ${s(approvalStatusName(item.approvalStatus))} · ${s(gateName(item.executionGate))}</p><div class="report-meta"><span class="status-badge">${s(item.priority)}</span><span>审批 ${s(item.approvalRole)}</span><span>${s(item.taskGroupId)}</span></div></div><div class="report-actions">${canDecide ? `<button type="button" data-approval-approve="${s(item.approvalId)}">通过</button><button type="button" data-approval-reject="${s(item.approvalId)}">拒绝</button><button type="button" data-approval-review="${s(item.approvalId)}">退回复核</button>` : `<span class="status-badge">${s(gateName(item.executionGate))}</span>`}</div></article>`; }
  function groupCard(item) { return `<article class="report-card"><div><h3>${s(item.objectName || item.objectId)} · ${s(item.groupName)}</h3><p>${s(typeName(item.objectType))} · ${s(groupStatusName(item.groupStatus))} · ${s(item.priority)}</p><div class="report-meta"><span class="status-badge">任务 ${s(item.taskCount)}</span><span>审批 ${s(item.approvalRole)}</span><span>${s(item.finalIntensityLevel)}</span></div></div><div class="report-actions"><span class="status-badge">任务组</span></div></article>`; }
  function smallCard(title, subtitle, meta) { return `<article class="report-card"><div><h3>${s(title)}</h3><p>${s(subtitle)}</p><div class="report-meta"><span class="status-badge">${s(meta)}</span></div></div></article>`; }

  window.WeightCenterPage = {
    route: "weight-center",
    title: "权重中心",
    async render() {
      const [snapshots, scores, context, cross, groups, approvals, executions, reviews] = await Promise.all([loadSnapshots(), loadScores(), loadContext(), loadCross(), loadGroups(), loadApprovals(), loadExecutions(), loadReviews()]);
      const executionRows = executions?.executions || [];
      const reviewRows = reviews?.reviews || [];
      const approvalRows = approvals?.approvals || [];
      const taskGroups = groups?.taskGroups || [];
      return `<section class="report-hero report-hero-clean"><div><p class="eyebrow">WEIGHT CENTER · V8.9</p><h2>执行回写与复盘</h2></div><div class="report-hero-side"><span>当前阶段</span><strong>审批通过 → 执行回写 → 复盘沉淀</strong><small>不自动执行外部动作</small></div></section>
      <section class="kpi-grid report-metrics">${metric("执行记录", executions?.executionCount || 0, "feedback")}${metric("待回写", executions?.byExecutionStatus?.awaiting_feedback || 0, "pending")}${metric("已回写", executions?.byExecutionStatus?.feedback_submitted || 0, "done")}${metric("复盘", reviews?.reviewCount || 0, "review")}</section>
      <section class="report-preview-grid">${countBlock("执行状态", executions?.byExecutionStatus || {})}${countBlock("复盘效果", reviews?.byEffectiveness || {}, effectivenessName)}${countBlock("下一决策", reviews?.byNextDecision || {}, nextDecisionName)}</section>
      <section class="page-section report-section"><div class="section-header"><div><h3>执行回写</h3><p>审批通过后生成执行回写记录。这里记录人工实际执行结果，不直接操作平台。</p></div><div class="report-actions"><button type="button" data-generate-execution>生成执行记录</button><button type="button" data-filter-execution="awaiting_feedback">待回写</button><button type="button" data-filter-execution="feedback_submitted">已回写</button><button type="button" data-filter-execution="">全部</button></div></div><div class="execution-card-list report-card-list">${executionRows.map(executionCard).join("") || `<div class="log-empty">暂无执行记录。先审批通过任务组，再生成执行记录。</div>`}</div></section>
      <section class="page-section report-section"><div class="section-header"><div><h3>执行复盘</h3><p>执行回写后生成复盘，复盘只作为 RAG 案例候选，不自动改写标准线。</p></div><div class="report-actions"><button type="button" data-generate-review>生成复盘</button><button type="button" data-filter-review="effective">有效</button><button type="button" data-filter-review="worse">恶化</button><button type="button" data-filter-review="uncertain">待观察</button><button type="button" data-filter-review="">全部</button></div></div><div class="review-card-list report-card-list">${reviewRows.map(reviewCard).join("") || `<div class="log-empty">暂无执行复盘。</div>`}</div></section>
      <section class="page-section report-section"><div class="section-header"><div><h3>审批流</h3></div><div class="report-actions"><button type="button" data-generate-approval>生成审批流</button></div></div><div class="approval-card-list report-card-list">${approvalRows.slice(0, 10).map(approvalCard).join("") || `<div class="log-empty">暂无审批流。</div>`}</div></section>
      <section class="page-section report-section"><div class="section-header"><div><h3>交叉任务组</h3></div><div class="report-actions"><button type="button" data-generate-group>生成任务组</button></div></div><div class="group-card-list report-card-list">${taskGroups.slice(0, 10).map(groupCard).join("") || `<div class="log-empty">暂无任务组。</div>`}</div></section>
      <section class="report-preview-grid">${smallCard("快照", snapshots?.snapshotCount || 0, "V8.0")}${smallCard("评分", scores?.scoreCount || 0, "V8.4")}${smallCard("上下文", context?.adjustmentCount || 0, "V8.5")}${smallCard("验证", cross?.validationCount || 0, "V8.6")}</section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-generate-weight]", "click", () => generateSnapshots());
      ctx.delegate("[data-generate-score]", "click", () => generateScores());
      ctx.delegate("[data-generate-context]", "click", () => generateContext());
      ctx.delegate("[data-generate-cross]", "click", () => generateCross());
      ctx.delegate("[data-generate-group]", "click", () => generateGroups());
      ctx.delegate("[data-generate-approval]", "click", () => generateApprovals());
      ctx.delegate("[data-generate-execution]", "click", () => generateExecutions());
      ctx.delegate("[data-generate-review]", "click", () => generateReviews());
      ctx.delegate("[data-approval-approve]", "click", (_, node) => decideApproval(node.dataset.approvalApprove, "approve"));
      ctx.delegate("[data-approval-reject]", "click", (_, node) => decideApproval(node.dataset.approvalReject, "reject"));
      ctx.delegate("[data-approval-review]", "click", (_, node) => decideApproval(node.dataset.approvalReview, "return_review"));
      ctx.delegate("[data-submit-feedback]", "click", (_, node) => submitFeedback(node.dataset.submitFeedback));
      ctx.delegate("[data-filter-execution]", "click", async (_, node) => { const data = await loadExecutions(node.dataset.filterExecution || ""); document.querySelector(".execution-card-list").innerHTML = (data.executions || []).map(executionCard).join("") || `<div class="log-empty">当前筛选暂无执行记录。</div>`; });
      ctx.delegate("[data-filter-review]", "click", async (_, node) => { const data = await loadReviews(node.dataset.filterReview || ""); document.querySelector(".review-card-list").innerHTML = (data.reviews || []).map(reviewCard).join("") || `<div class="log-empty">当前筛选暂无复盘。</div>`; });
    },
  };
})();