(function () {
  const s = (value) => AppShell.escape(value);
  const routes = {
    "store-overview": "店群总览",
    "task-command": "人员总览",
    "profit-budget": "供投财务",
    "org-efficiency": "组织效率",
    "review-audit": "复盘审计",
  };
  function hasData() { const v3 = window.AppMockData?.v3 || {}; return Boolean(v3.latestDataVersion || v3.activeAlertCount || (window.AppMockData.products || []).length || (window.AppMockData.traffic || []).length || AppTaskStore.listTasks().length); }
  function hero(title, value = "V5") { return `<section class="report-hero realtime-hero"><div><h2>${s(title)}</h2></div><div class="report-hero-side"><strong>${s(value)}</strong></div></section>`; }
  function metric(label, value) { return `<article class="card realtime-metric"><h3>${s(label)}</h3><strong>${s(value)}</strong></article>`; }
  function taskCard(task) { return `<article class="chain-card"><header><strong>${s(task.taskType || task.title)}</strong></header><p>${s(task.productShort || task.productTitle || task.productId || "任务")}</p><footer>${s(task.status || "待处理")}</footer></article>`; }
  function renderRoute(route) {
    const title = routes[route] || "总览";
    if (!hasData()) return hero("暂无数据", "V5");
    const v3 = window.AppMockData?.v3 || {};
    const tasks = AppTaskStore.listTasks();
    const metrics = [["数据版本", v3.latestDataVersion || "—"], ["预警", v3.activeAlertCount || 0], ["商品", (window.AppMockData.products || []).length], ["任务", tasks.length]];
    return `${hero(title, v3.latestDataVersion || "已导入")}<section class="kpi-grid report-metrics realtime-metrics">${metrics.map(([a,b]) => metric(a,b)).join("")}</section>${tasks.length ? `<section class="page-section realtime-section"><div class="section-header"><h3>任务</h3></div><div class="supply-grid">${tasks.map(taskCard).join("")}</div></section>` : ""}`;
  }
  function page(route) { return { route, title: routes[route], async render() { return renderRoute(route); } }; }
  window.StoreOverviewPage = page("store-overview");
  window.TaskCommandPage = page("task-command");
  window.ProfitBudgetPage = page("profit-budget");
  window.OrgEfficiencyPage = page("org-efficiency");
  window.ReviewAuditPage = page("review-audit");
})();
