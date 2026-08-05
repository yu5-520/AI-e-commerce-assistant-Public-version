(function () {
  let notice = "";
  const s = (value) => AppShell.escape(value ?? "");
  function taskButton(item) {
    const task = AppTaskActions.findOpenTask(item);
    return task ? `<button type="button" data-open-task="${s(task.id)}" class="ghost">查看任务</button><button type="button" data-task-report="${s(task.id)}">任务报告</button>` : `<button type="button" data-candidate-report="traffic:${s(item.id)}">查看趋势</button>`;
  }
  function alertCell(item) {
    const state = item.alertState || {};
    if (!state.activeAlertCount) return "";
    return `<div class="traffic-number-cell danger"><span>执行信号</span><strong>${s(state.activeAlertCount)}</strong><small>${s(state.highestPriority || "待处理")}</small></div>`;
  }
  function row(item) {
    return `<article class="traffic-row"><div class="traffic-title-cell"><div class="traffic-thumb">${s(item.imageLabel)}</div><div class="traffic-title-block"><strong>${s(item.title)}</strong><small>${s(item.platform)} · ${s(item.store)}</small><span>${s(item.channel)} · ${s(item.source)}</span></div></div><div class="traffic-metric-strip"><div class="traffic-number-cell"><span>曝光 / 点击</span><strong>${s(item.exposure)}</strong><small>CTR ${s(item.ctr)}</small></div><div class="traffic-number-cell ${Number(item.roi) < 1.2 ? "danger" : "warning"}"><span>ROI</span><strong>${s(item.roi)}</strong><small>转化 ${s(item.conversion)}</small></div><div class="traffic-number-cell ${AppShell.statusClass(item.statusLevel)}"><span>退款率</span><strong>${s(item.refundRate)}</strong><small>${s(item.status)}</small></div>${alertCell(item)}</div><div class="traffic-actions">${taskButton(item)}</div></article>`;
  }
  window.TrafficPage = { route: "business-traffic", title: "流量趋势", render() { return `<section class="traffic-toolbar"><div><p class="eyebrow">TRAFFIC TRENDS · V11.1</p><h2>流量趋势列表</h2><p>流量模块先看曝光、点击、转化、ROI、自然/付费流量结构；只有高风险高时效事项才进入任务栏。</p></div></section>${notice ? AppShell.notice("操作结果", notice) : ""}<section class="page-section traffic-list-section"><div class="section-header"><h3>流量趋势</h3><span class="status-badge">${AppMockData.traffic.length} 个对象</span></div><div class="traffic-card-list">${AppMockData.traffic.map(row).join("")}</div></section>`; }, mount(ctx) { ctx.delegate("[data-open-task]", "click", (_, node) => AppTaskActions.openTodoTask(node.dataset.openTask)); ctx.delegate("[data-task-report]", "click", (_, node) => AppTaskActions.openTaskReport(node.dataset.taskReport)); ctx.delegate("[data-candidate-report]", "click", (_, node) => { const [module, id] = node.dataset.candidateReport.split(":"); AppTaskActions.openCandidateReport(module, id); }); ctx.addCleanup(AppTaskStore.subscribe(() => AppRouter.schedule("task-store"))); } };
})();