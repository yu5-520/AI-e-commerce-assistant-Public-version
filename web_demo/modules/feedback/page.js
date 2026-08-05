(function () {
  const s = (value) => AppShell.escape(value ?? "");
  const list = (items = []) => items.length ? `<ul class="compact-list">${items.map((item) => `<li>${s(item)}</li>`).join("")}</ul>` : `<p class="muted">暂无</p>`;
  const pill = (value) => `<span class="status-badge">${s(value)}</span>`;

  function candidateCard(item) {
    return `<article class="recap-candidate-card">
      <header><div><strong>${s(item.title || item.taskId)}</strong><span>${s(item.problemType)} · ${s(item.riskDomain || "经营问题")}</span></div>${pill(item.qualityHint || "待判断")}</header>
      <p>${s(item.operatorSubmission || "运营提交待补充")}</p>
      <div class="recap-meta"><span>${s(item.store || "经营单元")}</span><span>${s(item.platform || "通用")}</span><span>${s(item.sourceModule || "任务池")}</span></div>
      <footer><button type="button" data-draft-task="${s(item.taskId)}">生成经验草案</button></footer>
    </article>`;
  }

  function memoryCard(item) {
    return `<article class="recap-candidate-card">
      <header><div><strong>${s(item.title || item.caseId)}</strong><span>${s(item.problemType)} · ${s(item.level)} · ${s(item.operatorStyle)}</span></div>${pill(item.status)}</header>
      <p>${s(item.resultSummary || item.initialJudgment || "待补充经验摘要")}</p>
      <div class="recap-meta"><span>质量分 ${s(item.qualityScore)}</span><span>${s(item.platform)}</span><span>${s(item.storeId)}</span></div>
      <footer>${item.status === "pending_review" ? `<button type="button" data-approve-case="${s(item.caseId)}">确认入库</button><button type="button" data-reject-case="${s(item.caseId)}">拒绝</button>` : `<span>${s(item.writeRule || "已进入记忆层")}</span>`}</footer>
    </article>`;
  }

  function metricGrid(metrics = {}) {
    const cards = [
      ["任务总量", metrics.taskTotal ?? 0, "统一任务池"],
      ["已完成", metrics.taskCompleted ?? 0, "可回流"],
      ["经验候选", metrics.learningCandidateCount ?? 0, "待沉淀"],
      ["待复核经验", metrics.memoryPendingReview ?? 0, "需人工确认"],
    ];
    return `<section class="kpi-grid">${cards.map(([a, b, c]) => AppShell.metricCard(a, b, c)).join("")}</section>`;
  }

  async function loadData() {
    const [flywheel, cycle, pendingCases, approvedCases] = await Promise.all([
      AppApi.feedbackFlywheel(),
      AppApi.feedbackCycle("日报", 8),
      AppApi.ragCases({ status: "pending_review", limit: 12 }),
      AppApi.ragCases({ status: "approved", limit: 8 }),
    ]);
    return { flywheel, cycle, pendingCases: pendingCases?.items || [], approvedCases: approvedCases?.items || [] };
  }

  window.FeedbackFlywheelPage = {
    route: "feedback-flywheel",
    title: "经验回流",
    async render() {
      const { flywheel, cycle, pendingCases, approvedCases } = await loadData();
      const metrics = flywheel?.agentEvalMetrics || {};
      const candidates = flywheel?.learningCandidates || [];
      const sections = cycle?.draftSections || [];
      return `<section class="log-toolbar">
        <div><p class="eyebrow">V4.4 FEEDBACK FLYWHEEL</p><h2>经验回流</h2><p>${s(flywheel?.rule || "任务完成后先生成经验卡草案，再由老板 / 总管确认入库。")}</p></div>
        <div class="todo-filter-row"><button type="button" data-refresh-feedback>刷新</button><button type="button" data-draft-cycle>生成日报经验草案</button></div>
      </section>
      ${metricGrid(metrics)}
      <section class="page-section"><div class="section-header"><h3>回流链路</h3>${pill(flywheel?.mode || "feedback")}</div>${list(flywheel?.chain || [])}</section>
      <section class="page-section"><div class="section-header"><h3>日报草案</h3>${pill(cycle?.target || "日报")}</div><div class="recap-candidate-grid">${sections.map((section) => `<article class="recap-candidate-card"><header><strong>${s(section.title)}</strong></header>${list(section.items || [])}</article>`).join("")}</div></section>
      <section class="page-section"><div class="section-header"><h3>学习候选</h3>${pill(`${candidates.length} 项`)}</div><div class="recap-candidate-grid">${candidates.length ? candidates.map(candidateCard).join("") : `<div class="log-empty">暂无学习候选。任务复核通过后会出现。</div>`}</div></section>
      <section class="page-section"><div class="section-header"><h3>待复核经验卡</h3>${pill(`${pendingCases.length} 条`)}</div><div class="recap-candidate-grid">${pendingCases.length ? pendingCases.map(memoryCard).join("") : `<div class="log-empty">暂无待复核经验卡。</div>`}</div></section>
      <section class="page-section"><div class="section-header"><h3>已入库经验</h3>${pill(`${approvedCases.length} 条`)}</div><div class="recap-candidate-grid">${approvedCases.length ? approvedCases.map(memoryCard).join("") : `<div class="log-empty">暂无已批准经验。</div>`}</div></section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-refresh-feedback]", "click", () => AppRouter.schedule("feedback-refresh"));
      ctx.delegate("[data-draft-cycle]", "click", async () => { await AppApi.draftFeedbackCycle("日报", { limit: 6 }); AppRouter.schedule("feedback-cycle-draft"); });
      ctx.delegate("[data-draft-task]", "click", async (_, node) => { await AppApi.draftFeedbackCycle("日报", { taskIds: [node.dataset.draftTask], limit: 1 }); AppRouter.schedule("feedback-task-draft"); });
      ctx.delegate("[data-approve-case]", "click", async (_, node) => { await AppApi.approveRagCase(node.dataset.approveCase, { reason: "经验回流页确认入库" }); AppRouter.schedule("feedback-approve"); });
      ctx.delegate("[data-reject-case]", "click", async (_, node) => { await AppApi.rejectRagCase(node.dataset.rejectCase, { reason: "经验回流页拒绝入库" }); AppRouter.schedule("feedback-reject"); });
    },
  };
})();
