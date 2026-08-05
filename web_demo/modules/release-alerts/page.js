(function () {
  const s = (value) => AppShell.escape(value ?? "-");

  async function loadAlerts() {
    const response = await fetch("/api/architecture/v7/release-alerts?limit=200", { method: "GET", headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() } });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  async function generateAlerts(createTasks = false) {
    const result = await AppApi.post("/api/architecture/v7/release-alerts/generate", null, { createTasks });
    window.alert(`生成预警 ${result.generatedCount || 0} 条，治理任务 ${result.createdTaskCount || 0} 条`);
    AppRouter.schedule("release-alerts-generated");
  }

  function metric(label, value, note) { return AppShell.metricCard(label, value, note || "V7.5"); }
  function severityClass(value) { return value === "高" ? "danger" : value === "中" ? "warning" : "good"; }

  function alertCard(item, canTasks) {
    return `<article class="report-card"><div><h3>${s(item.title)}</h3><p>${s(item.flagKey)} · ${s(item.alertType)} · ${s(item.status)}</p><div class="report-meta"><span class="status-badge ${severityClass(item.severity)}">${s(item.severity)}风险</span><span>${s(item.alertId)}</span></div><p>${s(item.suggestion)}</p></div><div class="report-actions"><button type="button" data-open-release>发布治理</button><button type="button" data-open-config-audit>配置审计</button>${canTasks ? `<button type="button" data-generate-task>生成任务</button>` : ""}</div></article>`;
  }

  function countBlock(title, data = {}) {
    const rows = Object.entries(data);
    return `<section class="page-section report-section"><div class="section-header"><h3>${s(title)}</h3><span class="status-badge">${rows.length}</span></div><div class="version-alert-list">${rows.map(([key, value]) => `<article class="version-alert-row"><strong>${s(key)}</strong><span>${s(value)}</span><small>发布预警指标</small></article>`).join("") || `<div class="log-empty">暂无数据。</div>`}</div></section>`;
  }

  window.ReleaseAlertsPage = {
    route: "release-alerts",
    title: "发布预警",
    async render() {
      const data = await loadAlerts();
      const alerts = data?.alerts || [];
      return `<section class="report-hero report-hero-clean"><div><p class="eyebrow">RELEASE ALERTS · V7.5</p><h2>发布治理预警</h2></div><div class="report-hero-side"><span>当前阶段</span><strong>异常预警与治理任务</strong><small>灰度 / 回滚 / 角色覆盖</small></div></section>
      <section class="kpi-grid report-metrics">${metric("预警数", data?.alertCount || 0, "alerts")}${metric("高风险", data?.bySeverity?.高 || 0, "high")}${metric("中风险", data?.bySeverity?.中 || 0, "medium")}${metric("可生成任务", data?.canGenerateTasks ? "是" : "否", data?.roleId || "role")}</section>
      <section class="report-preview-grid">${countBlock("风险等级", data?.bySeverity || {})}${countBlock("预警类型", data?.byType || {})}</section>
      <section class="page-section report-section"><div class="section-header"><div><h3>预警列表</h3><p>发布治理异常会在这里集中展示，可生成治理任务进入任务中心。</p></div><div class="report-actions"><button type="button" data-generate-alerts>扫描预警</button><button type="button" data-generate-alert-tasks>扫描并生成任务</button></div></div><div class="report-card-list">${alerts.map((item) => alertCard(item, data?.canGenerateTasks)).join("") || `<div class="log-empty">暂无预警。点击扫描预警。</div>`}</div></section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-generate-alerts]", "click", () => generateAlerts(false));
      ctx.delegate("[data-generate-alert-tasks]", "click", () => generateAlerts(true));
      ctx.delegate("[data-generate-task]", "click", () => generateAlerts(true));
      ctx.delegate("[data-open-release]", "click", () => AppRouter.navigate("release-governance"));
      ctx.delegate("[data-open-config-audit]", "click", () => AppRouter.navigate("config-audit"));
    },
  };
})();