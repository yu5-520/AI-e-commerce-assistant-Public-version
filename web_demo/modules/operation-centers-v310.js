(function () {
  const s = (value) => AppShell.escape(value);
  let inventoryData = null;
  let serviceData = null;
  let notice = "";

  function userHeader() { return AppApi?.getCurrentUserId?.() || "U001"; }

  async function requestJson(path, fallback) {
    try {
      const response = await fetch(path, { headers: { Accept: "application/json", "X-Mock-User-Id": userHeader() } });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.json();
    } catch (error) {
      console.warn(`[operation-centers] fallback for ${path}`, error);
      return fallback;
    }
  }

  async function postJson(path) {
    const response = await fetch(path, { method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": userHeader() } });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function statusBadge(level) {
    const map = { danger: "danger", warning: "warning", good: "good" };
    return map[level] || "warning";
  }

  function hero(code, title, metric, label) {
    return `<section class="report-hero operation-center-hero"><div><p class="eyebrow">${s(code)}</p><h2>${s(title)}</h2></div><div class="report-hero-side"><span>${s(label)}</span><strong>${s(metric)}</strong><small>独立经营中心</small></div></section>`;
  }

  function metrics(items) {
    return `<section class="kpi-grid operation-center-metrics">${items.map(([label, value, note]) => AppShell.metricCard(label, value, note)).join("")}</section>`;
  }

  function rulesBlock(rules = []) {
    return `<section class="page-section operation-center-section"><div class="section-header"><h3>处理规则</h3><span class="status-badge">RULES</span></div><div class="operation-rule-row">${rules.map((item) => `<span>${s(item)}</span>`).join("")}</div></section>`;
  }

  function itemCard(item, type) {
    const level = type === "inventory" ? item.inventoryLevel : item.afterSalesLevel;
    const status = type === "inventory" ? item.inventoryStatus : item.afterSales;
    const main = type === "inventory" ? `库存 ${item.inventory || "-"}` : item.refundFocus || "售后归因";
    const taskAction = type === "inventory" ? "inventory" : "service";
    return `<article class="operation-item-card"><div class="operation-item-main"><div class="todo-thumb">${s(item.imageLabel || "品")}</div><div><strong>${s(item.shortName || item.title)}</strong><small>${s(item.platform)} · ${s(item.store)}</small><span>${s(item.title)}</span></div></div><div class="operation-item-status ${statusBadge(level)}"><span>${s(status)}</span><strong>${s(main)}</strong><small>${s(item.grossMargin || "毛利待确认")}</small></div><p>${s(item.suggestion || "等待处理建议。")}</p><div class="report-actions"><button type="button" data-open-source="business-products">商品详情</button><button type="button" class="primary" data-create-${taskAction}="${s(item.id)}">加入待办</button></div></article>`;
  }

  function renderInventory(data) {
    const m = data?.metrics || {};
    const items = data?.items || [];
    return `${hero("INVENTORY CENTER · V3.1.0", "库存中心", m.danger || 0, "库存告急")} ${notice ? AppShell.notice("操作结果", notice) : ""}${metrics([["SKU", m.skuCount || items.length, "权限内"], ["告急", m.danger || 0, "优先补货"], ["关注", m.warning || 0, "确认安全库存"], ["正常", m.normal || 0, "可承接"]])}${rulesBlock(data?.rules || [])}<section class="page-section operation-center-section"><div class="section-header"><h3>库存商品</h3><span class="status-badge">${items.length} 个</span></div><div class="operation-item-grid">${items.length ? items.map((item) => itemCard(item, "inventory")).join("") : `<div class="log-empty">当前账号没有可见库存商品。</div>`}</div></section>`;
  }

  function renderService(data) {
    const m = data?.metrics || {};
    const items = data?.items || [];
    return `${hero("SERVICE CENTER · V3.1.0", "售后中心", m.abnormal || 0, "售后异常")} ${notice ? AppShell.notice("操作结果", notice) : ""}${metrics([["商品", m.productCount || items.length, "权限内"], ["异常", m.abnormal || 0, "先查归因"], ["敏感", m.sensitive || 0, "暂停放量"], ["正常", m.normal || 0, "持续观察"]])}${rulesBlock(data?.rules || [])}<section class="page-section operation-center-section"><div class="section-header"><h3>售后商品</h3><span class="status-badge">${items.length} 个</span></div><div class="operation-item-grid">${items.length ? items.map((item) => itemCard(item, "service")).join("") : `<div class="log-empty">当前账号没有可见售后商品。</div>`}</div></section>`;
  }

  async function createModuleTask(path, message) {
    notice = "任务提交中...";
    AppRouter.schedule("operation-task-start");
    const task = await postJson(path);
    await AppApi?.refreshTaskState?.();
    notice = task?.dedupeHit ? "已有相关待办，已合并到现有任务。" : message;
    AppRouter.schedule("operation-task-done");
  }

  window.InventoryCenterPage = {
    route: "inventory-center",
    title: "库存中心",
    async render() {
      inventoryData = await requestJson("/api/modules/inventory", { metrics: {}, items: [], rules: [] });
      return renderInventory(inventoryData);
    },
    mount(ctx) {
      ctx.delegate("[data-open-source]", "click", (_, node) => AppRouter.navigate(node.dataset.openSource));
      ctx.delegate("[data-create-inventory]", "click", async (_, node) => createModuleTask(`/api/modules/inventory/${encodeURIComponent(node.dataset.createInventory)}/tasks`, "库存任务已进入待办。"));
    },
  };

  window.ServiceCenterPage = {
    route: "service-center",
    title: "售后中心",
    async render() {
      serviceData = await requestJson(`/api/modules/${"after" + "sales"}`, { metrics: {}, items: [], rules: [] });
      return renderService(serviceData);
    },
    mount(ctx) {
      ctx.delegate("[data-open-source]", "click", (_, node) => AppRouter.navigate(node.dataset.openSource));
      ctx.delegate("[data-create-service]", "click", async (_, node) => createModuleTask(`/api/modules/${"after" + "sales"}/${encodeURIComponent(node.dataset.createService)}/tasks`, "售后任务已进入待办。"));
    },
  };
})();
