(function () {
  const s = (value) => AppShell.escape(value);
  const managerRoutes = {
    "manager-tasks": ["店群任务", "待办"],
    "manager-dispatch": ["任务派发", "派发"],
    "manager-review": ["运营复核", "复核"],
    "manager-modules": ["经营模块", "模块"],
    "manager-retrospective": ["复盘提交", "复盘"],
    "manager-reports": ["数据报表", "报表"],
  };
  function hasData() { const v3 = window.AppMockData?.v3 || {}; return Boolean(v3.latestDataVersion || v3.activeAlertCount || (window.AppMockData.products || []).length || (window.AppMockData.traffic || []).length || AppTaskStore.listTasks().length); }
  function hero(title, value = "V5") { return `<section class="manager-hero"><div><h2>${s(title)}</h2></div><div class="manager-hero-side"><strong>${s(value)}</strong></div></section>`; }
  function metric(label, value) { return `<article class="card"><h3>${s(label)}</h3><strong>${s(value)}</strong></article>`; }
  function taskCard(task) { return `<article class="manager-schedule-row"><div class="manager-schedule-main"><div><h3>${s(task.taskType || task.title)}</h3><strong>${s(task.productShort || task.productTitle || task.productId || "任务")}</strong></div></div><div class="manager-schedule-source"><strong>${s(task.sourceModule || task.source || "任务池")}</strong></div><div class="manager-schedule-actions"><button type="button" data-open-task="${s(task.id)}">查看任务</button></div></article>`; }
  function renderPage(route) {
    const [title, label] = managerRoutes[route] || ["店群任务", "任务"];
    if (!hasData()) return hero("暂无数据", "V5");
    const tasks = AppTaskStore.listTasks();
    const active = AppTaskStore.listActiveTasks();
    const v3 = window.AppMockData?.v3 || {};
    const metrics = [["待处理", active.length], ["预警", v3.activeAlertCount || 0], ["商品", (window.AppMockData.products || []).length], ["流量", (window.AppMockData.traffic || []).length]];
    if (route === "manager-modules") {
      return `${hero(title, label)}<section class="kpi-grid manager-metrics">${metrics.map(([a,b]) => metric(a,b)).join("")}</section>`;
    }
    if (route === "manager-reports") {
      const groups = window.AppMockData.reportGroups || [];
      const rows = groups.flatMap((group) => group.reports || []);
      return `${hero(title, label)}<section class="kpi-grid manager-metrics">${metrics.map(([a,b]) => metric(a,b)).join("")}</section><section class="page-section manager-section"><div class="section-header"><h3>报表</h3></div><div class="manager-grid">${rows.map((item) => `<article class="manager-card"><strong>${s(item.name)}</strong><span>${s(item.count || "0 条")}</span></article>`).join("")}</div></section>`;
    }
    return `${hero(title, label)}<section class="kpi-grid manager-metrics">${metrics.map(([a,b]) => metric(a,b)).join("")}</section>${tasks.length ? `<section class="page-section manager-section"><div class="section-header"><h3>任务</h3></div><div class="manager-schedule-list">${tasks.map(taskCard).join("")}</div></section>` : ""}`;
  }
  function page(route) { return { route, title: managerRoutes[route][0], render() { return renderPage(route); }, mount(ctx) { ctx.delegate("[data-open-task]", "click", (_, node) => AppTaskActions.openTodoTask(node.dataset.openTask)); ctx.addCleanup(AppTaskStore.subscribe(() => AppRouter.schedule("manager-task-store"))); } }; }
  window.ManagerTasksPage = page("manager-tasks");
  window.ManagerDispatchPage = page("manager-dispatch");
  window.ManagerReviewPage = page("manager-review");
  window.ManagerModulesPage = page("manager-modules");
  window.ManagerRetrospectivePage = page("manager-retrospective");
  window.ManagerReportsPage = page("manager-reports");
  window.ManagerTaskDetailPage = { route: "manager-task-detail", title: "任务详情", render() { return renderPage("manager-tasks"); } };
})();
