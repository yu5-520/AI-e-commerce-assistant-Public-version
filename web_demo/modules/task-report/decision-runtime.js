(function () {
  let report = null;
  let agent = null;
  const s = (v) => AppShell.escape(v ?? "");
  const a = (v) => Array.isArray(v) ? v.filter(Boolean) : [];

  function draft() { return a(agent?.taskDrafts)[0] || agent?.taskDraft || {}; }
  function plan() { const d = draft(); return d.actionPlan || agent?.actionPlan || {}; }
  function busy(node, text) { node.dataset.oldText = node.dataset.oldText || node.textContent; node.disabled = true; node.textContent = text; }
  function recover(node) { node.disabled = false; node.textContent = node.dataset.oldText || "重试"; }

  function evidence() {
    const p = plan();
    const rows = [...a(p.readonlyEvidence), ...a(report?.evidence)].filter((x) => x && x.label && x.value !== undefined).slice(0, 8);
    if (!rows.length) return "";
    return `<section class="page-section"><div class="section-header"><h3>数据证据</h3></div><div class="kpi-grid report-metrics">${rows.map((x) => `<article class="card report-metric-card"><h3>${s(x.label)}</h3><strong>${s(x.value)}</strong></article>`).join("")}</div></section>`;
  }

  function sourceTrace() {
    const rows = a(report?.sourceTrace).filter((x) => x && x.value !== undefined && x.value !== "");
    if (!rows.length) return "";
    return `<section class="page-section alert-evidence-section"><div class="section-header"><h3>来源链路</h3></div><div class="alert-kv-grid">${rows.map((x) => `<article><span>${s(x.label)}</span><strong>${s(x.value)}</strong></article>`).join("")}</div></section>`;
  }

  function field(x) {
    const key = s(x.key);
    if (x.type === "select") return `<label class="decision-input"><span>${s(x.label)}</span><select data-supp="${key}"><option value="">请选择</option>${a(x.options).map((o) => `<option value="${s(o)}">${s(o)}</option>`).join("")}</select></label>`;
    if (x.type === "textarea") return `<label class="decision-input full"><span>${s(x.label)}</span><textarea data-supp="${key}" rows="3"></textarea></label>`;
    return `<label class="decision-input"><span>${s(x.label)}${x.unit ? `（${s(x.unit)}）` : ""}</span><input data-supp="${key}" type="${s(x.type || "text")}" /></label>`;
  }

  function actionSteps(actions) {
    return a(actions).slice(0, 5).map((item, index) => `<article><span>${index + 1}</span><strong>${s(item)}</strong></article>`).join("");
  }

  function pathCard(x, recommended, i) {
    const checked = x.pathId === recommended || (!recommended && i === 0) ? "checked" : "";
    return `<label class="decision-action-path"><input type="radio" name="decisionPath" value="${s(x.pathId)}" ${checked} /><div class="decision-path-tags"><span>${s(x.pathName)}</span><span>${s(x.businessGoal)}</span></div><div class="decision-action-steps">${actionSteps(x.actions)}</div></label>`;
  }

  function taskDraft() {
    const d = draft();
    const p = plan();
    if (!agent || !d.title) return "";
    const paths = a(p.decisionPaths);
    const fields = a(p.supplementSchema);
    const sourceKey = `${agent.module || "product"}:${agent.entityId || report?.entityId}:0`;
    return `<section class="page-section task-draft-section decision-task-section"><div class="section-header"><h3>任务草案</h3></div><article class="task-draft-card decision-main-card"><div class="task-draft-head compact"><div><span class="action-package-label">${s(p.problemLabel || d.riskDomain || "经营任务")}</span><h4>${s(d.title)}</h4></div><span class="status-badge">${s(d.priority || "待确认")}</span></div><div class="task-draft-meta"><article><span>风险域</span><strong>${s(d.riskDomain || "经营")}</strong></article><article><span>截止时间</span><strong>${s(d.deadline || "待确认")}</strong></article><article><span>来源</span><strong>${s(d.sourceModule || report?.sourceModule || "模块")}</strong></article><article><span>进入待办</span><strong>默认处理中</strong></article></div></article>${paths.length ? `<section class="decision-path-section"><div class="section-header"><h3>选择行动顺序</h3></div><div class="decision-action-path-list">${paths.map((x, i) => pathCard(x, p.recommendedPathId, i)).join("")}</div></section>` : ""}${fields.length ? `<section class="decision-supplement-section"><div class="section-header"><h3>补充信息</h3></div><div class="decision-input-grid">${fields.map(field).join("")}</div></section>` : ""}<div class="report-actions decision-actions"><button type="button" data-agent-task="${s(sourceKey)}">确认加入任务清单</button></div></section>`;
  }

  async function loadAgent() {
    try {
      if (report?.taskId) return await AppApi.moduleAgent("task", report.taskId, "breakdown");
      if (report?.module && report?.entityId) return await AppApi.moduleAgent(report.module, report.entityId, "analysis");
    } catch (err) { console.warn("decision report agent load failed", err); }
    return null;
  }

  function payload() {
    const selectedPathId = document.querySelector('input[name="decisionPath"]:checked')?.value || "";
    const operatorSupplement = {};
    document.querySelectorAll("[data-supp]").forEach((node) => { operatorSupplement[node.dataset.supp] = node.value; });
    return { selectedPathId, operatorSupplement, reviewPlan: plan().reviewPlan || {} };
  }

  function render() {
    if (!report) return `<section class="page-section"><h3>报告加载失败</h3></section>`;
    const metrics = [["来源模块", report.sourceModule, report.sourceRoute], ["对象 ID", report.entityId, report.module], ["任务状态", report.taskStatus || "候选预警", ""], ["当前视角", report.viewer?.roleName || "默认", ""]];
    return `<section class="report-hero"><div><p class="eyebrow">TASK REPORT · ${s(report.sourceModule)}</p><h2>${s(report.title)}</h2><p>${s(report.warningSummary)}</p></div><div class="report-hero-side"><span>风险等级</span><strong>${s(report.riskLevel)}</strong><small>${s(report.taskStatus || report.reportType)}</small></div></section><section class="kpi-grid report-metrics">${metrics.map(([x,y,z]) => AppShell.metricCard(x,y,z)).join("")}</section>${sourceTrace()}${evidence()}${taskDraft()}<section class="page-section"><div class="report-actions"><button type="button" data-back>返回</button>${report.taskId ? `<button type="button" data-open-task="${s(report.taskId)}">查看待办任务</button>` : `<button type="button" data-source="${s(report.sourceRoute)}">返回来源模块</button>`}</div></section>`;
  }

  window.TaskReportPage = {
    route: "task-report",
    title: "详情报告",
    async render(ctx) {
      const state = ctx?.state || {};
      if (state.alertId) report = await AppApi.alertReport(state.alertId);
      else if (state.taskId) report = await AppApi.taskReport(state.taskId);
      else if (state.module && state.entityId) report = await AppApi.candidateReport(state.module, state.entityId);
      agent = await loadAgent();
      return render();
    },
    mount(ctx) {
      ctx.delegate("[data-back]", "click", () => AppRouter.navigate(report?.taskId ? "business-actions" : (report?.sourceRoute || "dashboard")));
      ctx.delegate("[data-source]", "click", (_, node) => AppRouter.navigate(node.dataset.source || "dashboard"));
      ctx.delegate("[data-open-task]", "click", (_, node) => AppTaskActions.openTodoTask(node.dataset.openTask));
      ctx.delegate("[data-agent-task]", "click", async (_, node) => {
        const [module, entityId, draftIndex] = (node.dataset.agentTask || "").split(":");
        busy(node, "创建中");
        const body = { draftIndex: Number(draftIndex || 0), mode: agent?.mode || "analysis", ...payload() };
        const result = await AppApi.post(`/api/modules/agents/${encodeURIComponent(module)}/${encodeURIComponent(entityId)}/tasks`, null, body);
        if (result?.task?.id) AppTaskActions.openTodoTask(result.task.id);
        else { recover(node); node.insertAdjacentHTML("afterend", `<span class="inline-action-error">创建失败，请重试</span>`); }
      });
    },
  };
})();
