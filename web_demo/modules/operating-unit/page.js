(function () {
  const s = (value) => AppShell.escape(value ?? "");
  const operationTabs = [
    ["business-products", "商品"],
    ["business-competitors", "竞品"],
    ["business-listing", "上新"],
    ["business-traffic", "流量"],
  ];
  let latestStoreRows = [];

  function hero(title, syncState = {}) {
    const side = syncState?.label || "数据已同步";
    return `<section class="unit-hero operating-hero"><div><h2>${s(title)}</h2></div><div class="unit-hero-side"><strong>${s(side)}</strong></div></section>`;
  }

  function metricCard(item) {
    return `<article class="card unit-metric-card"><h3>${s(item.label)}</h3><strong>${s(item.value)}</strong></article>`;
  }

  function tabs() {
    return `<section class="page-section operating-module-section"><div class="section-header"><h3>经营模块</h3><span class="status-badge">入口</span></div><div class="quick-actions operating-simple-actions">${operationTabs.map(([route, label]) => `<button data-operation-route="${s(route)}"><strong>${s(label)}</strong></button>`).join("")}</div></section>`;
  }

  function tagList(tags, cls = "") {
    const items = Array.isArray(tags) && tags.length ? tags : ["—"];
    return `<div class="store-row-tags ${s(cls)}">${items.map((tag) => `<em>${s(tag)}</em>`).join("")}</div>`;
  }

  function rowByStoreId(storeId) {
    return latestStoreRows.find((row) => String(row.storeId || row.displayName || row.storeName || "") === String(storeId || ""));
  }

  function storeRow(row) {
    const taskCount = Number(row.activeTaskCount || 0);
    const storeName = row.displayName || row.storeName || "店铺";
    const storeId = row.storeId || storeName;
    const action = `<div class="operating-store-buttons"><button type="button" data-store-products="${s(storeId)}">查看商品</button>${taskCount > 0 ? `<button type="button" data-store-task="${s(storeId)}">查看任务</button>` : ""}</div>`;
    return `<article class="operating-store-card ${s(row.level || "watch")}">
      <div class="operating-store-main">
        <div>
          <strong>${s(storeName)}</strong>
          <span>${s(row.platform || "平台")} · 商品 ${s(row.productCount ?? 0)}</span>
        </div>
        ${tagList([row.storeWeightTag || "常规店铺"], "weight")}
      </div>
      <div class="operating-store-meta">
        <div><span>经营标签</span>${tagList(row.businessTags || row.riskTags)}</div>
        <div><span>商品状态</span>${tagList(row.productRoleTags)}</div>
      </div>
      <div class="operating-store-action">
        <span>执行任务</span>
        <strong>${taskCount}</strong>
        ${action}
      </div>
    </article>`;
  }

  function judgmentCard(judgment) {
    if (!judgment) return "";
    return `<section class="page-section operating-judgment-section"><div class="section-header"><h3>${s(judgment.title || "经营判断")}</h3><span class="status-badge">店铺标签</span></div><article class="operating-judgment-card"><strong>${s(judgment.mainRisk || "常规观察")}</strong><p>${s(judgment.summary || "等待下一轮数据同步。")}</p></article></section>`;
  }

  window.OperatingUnitPage = {
    route: "operating-unit",
    title: "经营",
    async render() {
      const payload = await AppApi.operatingUnit();
      if (!payload?.hasData) return `${hero("暂无数据", payload?.syncState || { label: "等待数据" })}${tabs()}`;
      const metrics = (payload.metrics || []).slice(0, 4);
      latestStoreRows = payload.storeRows || [];
      return `${hero(payload.unitName || "经营单元", payload.syncState)}
        ${tabs()}
        <section class="kpi-grid unit-metrics operating-metrics">${metrics.map(metricCard).join("")}</section>
        <section class="page-section unit-store-section operating-store-section"><div class="section-header"><h3>店铺经营状态</h3><span class="status-badge">一店一行</span></div><div class="operating-store-list">${latestStoreRows.map(storeRow).join("")}</div></section>
        ${judgmentCard(payload.operatingJudgment)}`;
    },
    mount(ctx) {
      ctx.delegate("[data-operation-route]", "click", (_event, target) => {
        const route = target.dataset.operationRoute;
        AppRouter.navigate(route, route === "business-products" ? { fromStore: false } : null);
      });
      ctx.delegate("[data-store-task]", "click", (_event, target) => AppRouter.navigate("business-actions", { storeId: target.dataset.storeTask }));
      ctx.delegate("[data-store-products]", "click", (_event, target) => {
        const row = rowByStoreId(target.dataset.storeProducts) || {};
        AppRouter.navigate("business-products", {
          fromStore: true,
          storeId: row.storeId || target.dataset.storeProducts,
          storeName: row.displayName || row.storeName || target.dataset.storeProducts,
          platform: row.platform || "平台",
          productCount: row.productCount || 0,
          storeWeightTag: row.storeWeightTag || "常规店铺",
          businessTags: row.businessTags || row.riskTags || [],
          productRoleTags: row.productRoleTags || [],
          activeTaskCount: row.activeTaskCount || 0,
        });
      });
    },
  };
})();
