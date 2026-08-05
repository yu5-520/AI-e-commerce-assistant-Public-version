(function () {
  const s = (value) => AppShell.escape(value);
  const moduleSignals = [
    { module: "商品", abnormal: 3, task: 2, owner: "运营 B", route: "business-products" },
    { module: "竞品", abnormal: 1, task: 0, owner: "运营 A", route: "business-competitors" },
    { module: "上新", abnormal: 2, task: 1, owner: "运营 A", route: "business-listing" },
    { module: "流量", abnormal: 4, task: 2, owner: "运营 A", route: "business-traffic" },
    { module: "库存", abnormal: 2, task: 1, owner: "数据财务", route: "inventory-center" },
    { module: "售后", abnormal: 3, task: 2, owner: "运营 B", route: "service-center" },
  ];

  function hero() {
    const taskCount = moduleSignals.reduce((sum, item) => sum + item.task, 0);
    return `<section class="manager-hero manager-module-hero"><div><h2>经营模块</h2></div><div class="manager-hero-side"><strong>${s(taskCount)}</strong></div></section>`;
  }

  function metricGrid() {
    const abnormalCount = moduleSignals.filter((item) => item.abnormal > 0).length;
    const taskCount = moduleSignals.reduce((sum, item) => sum + item.task, 0);
    const top = moduleSignals.reduce((a, b) => (a.abnormal > b.abnormal ? a : b), moduleSignals[0]);
    return `<section class="kpi-grid manager-metrics manager-module-metrics">${[["异常模块", abnormalCount], ["模块任务", taskCount], ["最高异常", top.module], ["入口", moduleSignals.length]].map(([label, value]) => `<article class="card manager-module-metric"><h3>${s(label)}</h3><strong>${s(value)}</strong></article>`).join("")}</section>`;
  }

  function moduleCards() {
    return `<div class="manager-module-grid">${moduleSignals.map((item) => `<button type="button" class="manager-module-card" data-module-go="${s(item.route)}"><div><strong>${s(item.module)}</strong><span>异常 ${s(item.abnormal)}</span></div><dl><dt>任务</dt><dd>${s(item.task)}</dd><dt>负责人</dt><dd>${s(item.owner)}</dd></dl></button>`).join("")}</div>`;
  }

  window.ManagerModulesPage = {
    route: "manager-modules",
    title: "经营模块",
    render() {
      return `${hero()}${metricGrid()}<section class="page-section manager-section manager-module-section"><div class="section-header"><h3>模块入口</h3></div>${moduleCards()}</section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-module-go]", "click", (_, node) => AppRouter.navigate(node.dataset.moduleGo));
    },
  };
})();
