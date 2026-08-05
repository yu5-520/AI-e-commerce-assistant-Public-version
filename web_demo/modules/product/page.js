(function () {
  let activeId = null;
  let notice = "";
  let currentScope = {};
  let cachedProducts = [];
  const s = (value) => AppShell.escape(value ?? "");

  function status(level) { return AppShell.statusClass(level); }
  function normalizeStoreName(item = {}) { return item.storeName || item.store || item.platform || "未绑定店铺"; }
  function normalizeScope(state = {}) {
    if (Object.prototype.hasOwnProperty.call(state, "fromStore") && !state.fromStore && !state.storeId && !state.storeName) {
      clearScope();
      return currentScope;
    }
    if (state?.fromStore || state?.storeId || state?.storeName) {
      currentScope = {
        fromStore: Boolean(state.fromStore || state.storeId || state.storeName),
        storeId: state.storeId || "",
        storeName: state.storeName || "",
        platform: state.platform || "平台",
        productCount: Number(state.productCount || 0),
        storeWeightTag: state.storeWeightTag || "常规店铺",
        businessTags: Array.isArray(state.businessTags) ? state.businessTags : [],
        productRoleTags: Array.isArray(state.productRoleTags) ? state.productRoleTags : [],
        activeTaskCount: Number(state.activeTaskCount || 0),
      };
    }
    return currentScope;
  }
  function resolveActiveFromState(state = {}) { return state.productObjectId || state.objectId || state.archiveId || state.productId || state.activeId || ""; }
  function clearScope() { currentScope = {}; activeId = null; }
  function scopedParams() { return currentScope?.fromStore ? { storeId: currentScope.storeId } : {}; }
  function known(value) { return value !== null && value !== undefined && value !== "" && value !== "—" && value !== "未识别"; }

  function normalizeProduct(item = {}) {
    const metrics = item.metrics && typeof item.metrics === "object" ? item.metrics : {};
    return {
      ...item,
      id: item.id || item.objectId || item.archiveId || item.productId || item.skuId || item.title || "PRODUCT",
      objectId: item.objectId || item.archiveId || item.id,
      productId: item.productId || item.rawProductId || item.id,
      title: item.title || item.productTitle || item.shortName || item.productId || item.id || "未命名商品",
      platform: item.platform || currentScope.platform || "平台",
      store: normalizeStoreName(item),
      imageLabel: item.imageLabel || "品",
      inventory: item.inventory ?? item.stock ?? metrics.inventory ?? "—",
      inventoryStatus: item.inventoryStatus || item.inventoryState || "库存待确认",
      inventoryLevel: item.inventoryLevel || "watch",
      price: item.avgOrderValue || item.price || "—",
      avgOrderValue: item.avgOrderValue || item.price || metrics.avgOrderValue || "—",
      paymentAmount: item.paymentAmount || metrics.paymentAmount || "—",
      cost: item.costAmount || item.cost || "—",
      costAmount: item.costAmount || item.cost || "—",
      grossProfitAmount: item.grossProfitAmount || item.grossProfit || "—",
      grossMargin: item.grossMargin || item.margin || metrics.grossMargin || "—",
      roi: item.roi || item.roas || metrics.roi || metrics.roas || "—",
      clickRate: item.clickRate || metrics.clickRate || "—",
      conversionRate: item.conversionRate || metrics.conversionRate || "—",
      refundRate: item.refundRate || metrics.refundRate || "—",
      adSpend: item.adSpend || metrics.adSpend || "—",
      organicVisitors: item.organicVisitors || metrics.organicVisitors || "—",
      paidVisitors: item.paidVisitors || metrics.paidVisitors || "—",
      afterSales: item.afterSales || item.afterSalesStatus || "正常",
      afterSalesLevel: item.afterSalesLevel || "good",
      suggestion: item.suggestion || item.reason || "商品档案展示定位、当前事实与时间序列。",
      productPosition: item.productPosition || {},
      metricSections: Array.isArray(item.metricSections) ? item.metricSections : [],
      trafficSourceFacts: Array.isArray(item.trafficSourceFacts) ? item.trafficSourceFacts : [],
      metricFactSummary: item.metricFactSummary || {},
      resolvedReportDate: item.resolvedReportDate || item.metricFactSummary?.dataDate || item.metricDate || "",
    };
  }

  function payloadRows(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.items)) return payload.items;
    if (Array.isArray(payload?.products)) return payload.products;
    return [];
  }

  async function requestJson(path, timeoutMs = 7000) {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort("product_detail_timeout"), timeoutMs) : null;
    try {
      const response = await fetch(path, {
        cache: "no-store",
        headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
        signal: controller?.signal,
      });
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try { detail = (await response.json())?.detail || detail; } catch (error) {}
        throw new Error(detail);
      }
      return await response.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function loadProducts() {
    const payload = await AppApi.productView({ ...scopedParams(), limit: 300 });
    if (payload?.optionalError) throw new Error(payload.optionalError);
    cachedProducts = payloadRows(payload).map(normalizeProduct);
    return cachedProducts;
  }

  async function loadCompositeDetail(productId, fallback = {}) {
    const query = new URLSearchParams();
    if (fallback.storeId || currentScope.storeId) query.set("storeId", fallback.storeId || currentScope.storeId);
    const payload = await requestJson(`/api/modules/product-detail-v2256/${encodeURIComponent(productId)}${query.toString() ? `?${query.toString()}` : ""}`);
    const product = normalizeProduct({ ...fallback, ...(payload.item || {}) });
    return { product, trend: payload.trend || {}, composite: payload };
  }

  function sameProduct(product, id) {
    return id && [product.id, product.objectId, product.archiveId, product.productId, product.rawProductId, product.skuId].map(String).includes(String(id));
  }

  function tagList(product) {
    const tags = [];
    if ((product.sourceDataVersions || []).length <= 1) tags.push("新入库");
    if (product.metricFactSummary?.factCount) tags.push(`事实 ${product.metricFactSummary.factCount}`);
    if (product.alertState?.activeAlertCount) tags.push(`${product.alertState.highestPriority || "中"}风险信号`);
    if (product.inventoryStatus && product.inventoryStatus !== "库存正常") tags.push(product.inventoryStatus);
    if (known(product.roi)) tags.push(`ROI ${product.roi}`);
    if (known(product.grossMargin)) tags.push(`毛利 ${product.grossMargin}`);
    return `<div class="action-chip-list product-chip-list">${(tags.length ? tags : ["待建立趋势线"]).map((tag) => `<span>${s(tag)}</span>`).join("")}</div>`;
  }

  function smallTags(items = []) {
    const tags = Array.isArray(items) && items.length ? items : ["常规观察"];
    return `<div class="product-scope-tags">${tags.map((tag) => `<span>${s(tag)}</span>`).join("")}</div>`;
  }

  function alertBadge(product) {
    const state = product.alertState || {};
    if (!state.activeAlertCount) return "";
    return `<div class="product-number-cell danger"><span>执行信号</span><strong>${s(state.activeAlertCount)}</strong><small>${s(state.highestPriority || "待处理")}</small></div>`;
  }

  function taskButton(item) {
    const task = window.AppTaskActions?.findOpenTask?.(item);
    return task
      ? `<button type="button" data-open-task="${s(task.id)}" class="ghost">查看任务</button><button type="button" data-task-report="${s(task.id)}">任务报告</button>`
      : `<button type="button" data-candidate-module="product" data-candidate-id="${s(item.id)}">任务证据</button>`;
  }

  function scopeHero(rows = []) {
    if (!currentScope?.fromStore) {
      return `<section class="product-archive-hero"><div><p class="eyebrow">PRODUCT ARCHIVE · V22.5.6</p><h2>商品档案</h2><p>展示当前账号可见的商品定位、最新事实与经营趋势。</p></div><div class="product-scope-panel"><span>商品</span><strong>${rows.length}</strong><small>全局商品档案</small></div></section>`;
    }
    return `<section class="product-archive-hero scoped"><div><p class="eyebrow">STORE PRODUCT ARCHIVE · V22.5.6</p><h2>${s(currentScope.storeName || "店铺商品档案")}</h2><p>${s(currentScope.platform || "平台")} · 当前店铺 ${rows.length} 个商品</p>${smallTags([currentScope.storeWeightTag, ...(currentScope.businessTags || [])])}</div><div class="product-scope-panel"><span>执行任务</span><strong>${s(currentScope.activeTaskCount || 0)}</strong><small>${s((currentScope.productRoleTags || [])[0] || "商品状态")}</small><button type="button" class="secondary" data-clear-filter>全部商品</button></div></section>`;
  }

  function renderRow(product) {
    return `<article class="product-row"><div class="product-title-cell"><div class="product-thumb">${s(product.imageLabel || "品")}</div><div class="product-title-block"><strong>${s(product.title)}</strong><small>${s(product.productId)} · ${s(product.platform)} · ${s(product.store || "店铺")}</small>${tagList(product)}</div></div><div class="product-metric-strip"><div class="product-number-cell ${status(product.inventoryLevel)}"><span>库存</span><strong>${s(product.inventory)}</strong><small>${s(product.inventoryStatus)}</small></div><div class="product-number-cell"><span>ROI</span><strong>${s(product.roi)}</strong><small>广告投产</small></div><div class="product-number-cell"><span>转化率</span><strong>${s(product.conversionRate)}</strong><small>支付转化</small></div><div class="product-number-cell ${status(product.afterSalesLevel)}"><span>退款率</span><strong>${s(product.refundRate)}</strong><small>${s(product.afterSales)}</small></div>${alertBadge(product)}</div><div class="product-actions"><button type="button" data-detail="${s(product.productId || product.id)}">商品详情</button>${taskButton(product)}</div></article>`;
  }

  function metricValue(value, definition = {}) {
    if (!known(value)) return "—";
    if (typeof value === "string" && /[¥￥%]/.test(value)) return s(value);
    const number = Number(value);
    if (!Number.isFinite(number)) return s(value);
    if (definition.kind === "money") return `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
    if (definition.kind === "percent") {
      const percent = Math.abs(number) <= 1 ? number * 100 : number;
      return `${percent.toFixed(Math.abs(percent) >= 10 ? 1 : 2)}%`;
    }
    if (definition.kind === "integer") return Math.round(number).toLocaleString("zh-CN");
    return number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }

  function deltaValue(value, suffix = "") {
    if (!known(value)) return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const sign = number > 0 ? "+" : "";
    return `${sign}${(number * 100).toFixed(1)}%${suffix}`;
  }

  function deltaClass(value) {
    const number = Number(value);
    return !Number.isFinite(number) || Math.abs(number) < 0.0001 ? "flat" : number > 0 ? "up" : "down";
  }

  function dateLabel(value) {
    const text = String(value || "—");
    return text.length >= 10 ? text.slice(5, 10) : text;
  }

  function latestSnapshot(trend = {}) {
    const snapshots = Array.isArray(trend.recentSnapshots) ? trend.recentSnapshots : [];
    return snapshots.length ? snapshots[snapshots.length - 1] : null;
  }

  function latestMetric(product, trend, code) {
    const snapshot = latestSnapshot(trend);
    const aliases = {
      roi: ["roi", "roas"],
      grossMargin: ["grossMargin"],
      conversionRate: ["conversionRate"],
      paymentAmount: ["paymentAmount"],
      adSpend: ["adSpend"],
      refundRate: ["refundRate"],
      inventory: ["inventory"],
    };
    for (const key of aliases[code] || [code]) {
      if (known(product[key])) return product[key];
      if (known(snapshot?.metrics?.[key])) return snapshot.metrics[key];
    }
    return "—";
  }

  function quickMetric(label, value, note = "") {
    return `<div><span>${s(label)}</span><strong>${s(value)}</strong>${note ? `<small>${s(note)}</small>` : ""}</div>`;
  }

  function renderDetailHero(product, trend = {}) {
    const latestDate = trend.observationSummary?.latestBusinessDate || product.resolvedReportDate || "";
    return `<section class="product-detail-hero product-detail-hero-v2256"><div class="product-detail-main"><div class="product-thumb large">${s(product.imageLabel || "品")}</div><div><p class="eyebrow">PRODUCT DETAIL · V22.5.6</p><h2>${s(product.title)}</h2><p>${s(product.platform)} · ${s(product.store)} · ${s(product.productId)}${product.skuId ? ` · SKU ${s(product.skuId)}` : ""}</p>${product.link || product.productLink ? `<a href="${s(product.link || product.productLink)}" target="_blank" rel="noreferrer">打开商品链接</a>` : ""}</div></div><div class="product-detail-actions"><button type="button" data-back>返回商品列表</button>${taskButton(product)}</div><div class="product-detail-quick-metrics">${quickMetric("支付金额", metricValue(latestMetric(product, trend, "paymentAmount"), { kind: "money" }), latestDate)}${quickMetric("ROI", metricValue(latestMetric(product, trend, "roi"), { kind: "number" }), "广告投产")}${quickMetric("支付转化", metricValue(latestMetric(product, trend, "conversionRate"), { kind: "percent" }), "最近有效快照")}${quickMetric("退款率", metricValue(latestMetric(product, trend, "refundRate"), { kind: "percent" }), "售后结果")}</div></section>`;
  }

  function renderRecentSnapshots(trend = {}) {
    const snapshots = trend.recentSnapshots || [];
    const summary = trend.observationSummary || {};
    const definitions = trend.metricDefinitions || [];
    if (!trend.ready || !snapshots.length) {
      return `<section class="page-section product-detail-section product-trend-section"><div class="section-header"><h3>最近5次有效数据快照</h3><span class="status-badge">数据不足</span></div><div class="product-trend-empty">${s(trend.error || "当前商品尚未形成可比的有效观测。商品未出现在某份报表时不会被记为0，也不会生成伪快照。")}</div></section>`;
    }
    const groups = new Map();
    definitions.forEach((definition) => {
      if (!groups.has(definition.group)) groups.set(definition.group, []);
      groups.get(definition.group).push(definition);
    });
    const snapshotHeaders = snapshots.map((item, index) => `<div class="product-snapshot-period"><strong>${s(dateLabel(item.businessDate))}</strong><small>${index === snapshots.length - 1 ? "最新" : s(item.dataVersion || "有效快照")}</small></div>`).join("");
    const groupRows = Array.from(groups.entries()).map(([group, items]) => `<div class="product-snapshot-group"><h4>${s(group)}</h4>${items.map((definition) => {
      const cells = snapshots.map((snapshot) => {
        const value = snapshot.metrics?.[definition.code];
        const delta = snapshot.changes?.[definition.code];
        return `<div class="product-snapshot-cell"><strong>${s(metricValue(value, definition))}</strong><small class="snapshot-delta ${deltaClass(delta)}">${s(deltaValue(delta))}</small></div>`;
      }).join("");
      return `<div class="product-snapshot-row" style="--snapshot-count:${snapshots.length}"><div class="product-snapshot-metric"><strong>${s(definition.label)}</strong><small>${s(definition.code)}</small></div>${cells}</div>`;
    }).join("")}</div>`).join("");
    return `<section class="page-section product-detail-section product-trend-section"><div class="section-header"><h3>最近5次有效数据快照</h3><span class="status-badge">${s(snapshots.length)} 次直接比对</span></div><div class="product-trend-summary"><div><span>有效快照</span><strong>${s(summary.validSnapshotCount || 0)}</strong></div><div><span>最新业务日期</span><strong>${s(summary.latestBusinessDate || "—")}</strong></div><div><span>直接比较窗口</span><strong>${s(summary.recentWindowSize || snapshots.length)} 期</strong></div><div><span>历史算法窗口</span><strong>${s(summary.historyAlgorithmWindowCount || 0)} 期</strong></div><div><span>数据完整度</span><strong>${s(Math.round(Number(summary.dataCompleteness || 0) * 100))}%</strong></div></div><div class="product-snapshot-scroll"><div class="product-snapshot-head" style="--snapshot-count:${snapshots.length}"><div><strong>指标</strong><small>未更新显示为—</small></div>${snapshotHeaders}</div>${groupRows}</div></section>`;
  }

  function streakText(feature = {}) {
    const length = Number(feature.streakLength || 0);
    if (!length || !feature.streakDirection) return "无连续方向";
    return `连续${length}期${feature.streakDirection === "up" ? "上升" : "下降"}`;
  }

  function renderTrendCard(definition, feature = {}) {
    const confidence = Math.round(Number(feature.sampleConfidence || 0) * 100);
    return `<article class="product-algorithm-card"><header><div><span>${s(definition.group)}</span><h4>${s(definition.label)}</h4></div><strong>${s(metricValue(feature.current, definition))}</strong></header><div class="product-algorithm-grid"><div><span>较上期</span><strong class="${deltaClass(feature.previousDelta)}">${s(deltaValue(feature.previousDelta))}</strong></div><div><span>环比</span><strong class="${deltaClass(feature.mom)}">${s(deltaValue(feature.mom))}</strong></div><div><span>同比</span><strong class="${deltaClass(feature.yoy)}">${s(deltaValue(feature.yoy))}</strong></div><div><span>最近5期</span><strong class="${deltaClass(feature.slope5)}">${s(deltaValue(feature.slope5, "/期"))}</strong></div><div><span>最近10期</span><strong class="${deltaClass(feature.slope10)}">${s(deltaValue(feature.slope10, "/期"))}</strong></div><div><span>最近30期</span><strong class="${deltaClass(feature.slope30)}">${s(deltaValue(feature.slope30, "/期"))}</strong></div></div><footer><span>波动率 ${s(deltaValue(feature.volatility10))}</span><span>${s(streakText(feature))}</span><span>样本 ${s(feature.sampleCount || 0)}</span><span>置信度 ${s(confidence)}%</span></footer></article>`;
  }

  function renderHistoricalTrends(trend = {}) {
    const definitions = trend.metricDefinitions || [];
    const features = trend.metricTrends || {};
    const usable = definitions.filter((definition) => Number(features[definition.code]?.sampleCount || 0) > 0);
    if (!usable.length) {
      return `<section class="page-section product-detail-section product-trend-section"><div class="section-header"><h3>历史趋势算法</h3><span class="status-badge">等待样本</span></div><div class="product-trend-empty">需要至少两次有效商品观测，才能计算环比、同比和中长期趋势。</div></section>`;
    }
    return `<section class="page-section product-detail-section product-trend-section"><div class="section-header"><h3>历史趋势算法</h3><span class="status-badge">${s(trend.trendState?.label || "趋势可读")}</span></div><div class="product-algorithm-list">${usable.map((definition) => renderTrendCard(definition, features[definition.code] || {})).join("")}</div></section>`;
  }

  function latestSections(product, trend = {}) {
    const existing = (product.metricSections || []).filter((section) => Array.isArray(section?.items) && section.items.length);
    if (existing.length) return existing;
    const snapshot = latestSnapshot(trend);
    const definitions = trend.metricDefinitions || [];
    if (!snapshot) return [];
    const groups = new Map();
    definitions.forEach((definition) => {
      const value = snapshot.metrics?.[definition.code];
      if (!known(value)) return;
      if (!groups.has(definition.group)) groups.set(definition.group, []);
      groups.get(definition.group).push({
        metricCode: definition.code,
        metricName: definition.label,
        displayValue: metricValue(value, definition),
        dataDate: snapshot.businessDate,
      });
    });
    return Array.from(groups.entries()).map(([title, items]) => ({ title, items }));
  }

  function renderLatestFacts(product, trend = {}) {
    const sections = latestSections(product, trend);
    const latestDate = trend.observationSummary?.latestBusinessDate || product.resolvedReportDate || "";
    if (!sections.length) {
      return `<section class="page-section product-detail-section"><div class="section-header"><h3>最新一期经营事实</h3><span class="status-badge">尚未形成快照</span></div><div class="product-trend-empty">当前商品已建档，但还没有可展示的有效经营指标。</div></section>`;
    }
    return `<section class="page-section product-detail-section product-latest-facts"><div class="section-header"><h3>最新一期经营事实</h3><span class="status-badge">${s(latestDate || "最新")}</span></div><div class="product-fact-section-list">${sections.map((section) => `<article class="product-fact-section"><h4>${s(section.title)}</h4><div class="product-fact-grid">${(section.items || []).map((item) => `<div><span>${s(item.metricName)}</span><strong>${s(item.displayValue)}</strong></div>`).join("")}</div></article>`).join("")}</div></section>`;
  }

  function latestTrafficFacts(product) {
    const rows = Array.isArray(product.trafficSourceFacts) ? product.trafficSourceFacts : [];
    const sorted = [...rows].sort((a, b) => String(b.metricDate || b.dataDate || "").localeCompare(String(a.metricDate || a.dataDate || "")));
    const seen = new Set();
    return sorted.filter((item) => {
      const key = String(item.trafficSource || "报表流量");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function renderTrafficFacts(product) {
    const rows = latestTrafficFacts(product);
    if (!rows.length) return "";
    return `<section class="page-section product-detail-section product-traffic-section"><div class="section-header"><h3>流量来源</h3><span class="status-badge">${rows.length} 类</span></div><div class="product-traffic-list">${rows.map((item) => `<article><strong>${s(item.trafficSource || "报表流量")}</strong><span>访客 ${s(item.visitorCount || "—")}</span><span>点击率 ${s(item.clickRate || "—")}</span><span>转化 ${s(item.conversionRate || "—")}</span><span>支付 ${s(item.paymentAmount || "—")}</span><span>ROI ${s(item.roi || "—")}</span></article>`).join("")}</div></section>`;
  }

  function renderPosition(product) {
    const pos = product.productPosition || {};
    const rows = [
      ["系统店铺编码", pos.systemStoreCode], ["系统SPU编码", pos.systemSpuCode], ["系统LINK编码", pos.systemLinkCode], ["系统SKU编码", pos.systemSkuCode],
      ["平台", pos.platform || product.platform], ["店铺", pos.storeName || product.store], ["商品ID", pos.productId || product.productId], ["SKU ID", pos.skuId || product.skuId],
      ["ERP编码", pos.erpProductCode || product.erpProductCode], ["商品链接", pos.productLink || product.productLink || product.link],
    ].filter(([, value]) => value && value !== "—");
    if (!rows.length) return "";
    return `<details class="page-section product-detail-section product-position-details"><summary>商品定位与系统编码</summary><div class="product-position-grid">${rows.map(([label, value]) => `<div><span>${s(label)}</span><strong>${s(value)}</strong></div>`).join("")}</div></details>`;
  }

  function renderDetail(product, trend = {}) {
    return `${renderDetailHero(product, trend)}${notice ? AppShell.notice("操作结果", notice) : ""}${renderRecentSnapshots(trend)}${renderHistoricalTrends(trend)}${renderLatestFacts(product, trend)}${renderTrafficFacts(product)}${renderPosition(product)}`;
  }

  function apiError(error) {
    return `<section class="product-toolbar"><div><p class="eyebrow">PRODUCT ARCHIVE · V22.5.6</p><h2>商品档案</h2></div></section><section class="page-section"><div class="section-header"><h3>接口异常</h3><span class="status-badge">无本地兜底</span></div><p>后端接口没有返回可用数据，页面已停止展示本地模拟业务内容。</p><strong>当前页面接口 ${s(error?.message || error || "请求失败")}</strong></section>`;
  }

  window.ProductPage = {
    route: "business-products",
    title: "商品档案",
    async render(ctx) {
      const state = ctx?.state || {};
      normalizeScope(state);
      const requested = resolveActiveFromState(state);
      if (requested) activeId = requested;
      try { await loadProducts(); } catch (error) { return apiError(error); }
      if (activeId) {
        const preview = cachedProducts.find((item) => sameProduct(item, activeId)) || {
          productId: activeId,
          id: activeId,
          storeId: currentScope.storeId,
          storeName: currentScope.storeName,
          platform: currentScope.platform,
        };
        try {
          const detail = await loadCompositeDetail(preview.productId || activeId, preview);
          return renderDetail(detail.product, detail.trend);
        } catch (error) {
          return apiError(error);
        }
      }
      const rows = cachedProducts;
      const empty = !rows.length;
      return `${scopeHero(rows)}${notice ? AppShell.notice("操作结果", notice) : ""}<section class="page-section product-list-section"><div class="section-header"><h3>${currentScope?.fromStore ? "店铺商品列表" : "商品列表"}</h3><span class="status-badge">${rows.length} 个商品</span></div><div class="product-card-list">${empty ? `<article class="dashboard-empty">当前商品读模型为空，请刷新读模型或导入报表；接口失败不会再伪装成0个商品。</article>` : rows.map(renderRow).join("")}</div></section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-detail]", "click", (_, node) => { activeId = node.dataset.detail; notice = ""; AppRouter.schedule("product-detail"); });
      ctx.delegate("[data-back]", "click", () => { activeId = null; notice = ""; AppRouter.schedule("product-back"); });
      ctx.delegate("[data-clear-filter]", "click", () => { clearScope(); AppRouter.schedule("product-filter-clear", { fromStore: false }); });
      ctx.delegate("[data-open-task]", "click", (_, node) => AppTaskActions.openTodoTask(node.dataset.openTask));
      ctx.delegate("[data-task-report]", "click", (_, node) => AppTaskActions.openTaskReport(node.dataset.taskReport));
      ctx.delegate("[data-candidate-id]", "click", (_, node) => AppTaskActions.openCandidateReport(node.dataset.candidateModule, node.dataset.candidateId));
      ctx.addCleanup(AppTaskStore.subscribe(() => AppRouter.schedule("task-store")));
    },
  };
})();
