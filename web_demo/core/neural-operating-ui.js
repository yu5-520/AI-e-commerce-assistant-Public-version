(function () {
  const VERSION = "21.8.0";
  const ROUTE_STAGE = new Map([
    ["dashboard", { route: "dashboard", stage: "central", label: "经营中枢" }],
    ["data-check", { route: "data-check", stage: "sensed", label: "数据感知" }],
    ["operating-unit", { route: "operating-unit", stage: "interpreted", label: "经营判断" }],
    ["business-products", { route: "operating-unit", stage: "interpreted", label: "商品经营" }],
    ["business-competitors", { route: "operating-unit", stage: "interpreted", label: "竞品判断" }],
    ["business-listing", { route: "operating-unit", stage: "interpreted", label: "上新判断" }],
    ["business-traffic", { route: "operating-unit", stage: "interpreted", label: "流量判断" }],
    ["business-actions", { route: "business-actions", stage: "action_ready", label: "任务传导" }],
    ["task-report", { route: "business-actions", stage: "executing", label: "任务执行" }],
    ["task-submit", { route: "business-actions", stage: "review_pending", label: "执行回流" }],
    ["business-report", { route: "business-report", stage: "learned", label: "经营记忆" }],
    ["system-status", { route: "system-status", stage: "health", label: "链路健康" }],
  ]);
  const STAGE_COPY = {
    central: "系统正在汇总经营信号、任务与执行状态",
    sensed: "报表变化进入感知层，等待形成经营判断",
    interpreted: "多项经营信号正在汇聚为可执行方向",
    action_ready: "经营判断已经转化为按时效排序的任务",
    executing: "当前动作已进入人工执行节点",
    review_pending: "执行痕迹已回流，等待验证与自动复盘",
    learned: "执行结果正在形成可复用的经营记忆",
    health: "检查感知、判断、传导、回流与沉淀是否通畅",
  };

  let projection = null;
  let lastFingerprint = "";
  let refreshTimer = null;

  function text(value) { return String(value ?? "").replace(/\s+/g, " ").trim(); }
  function routeName() { return window.AppRouter?.routeFromHash?.() || "dashboard"; }
  function currentStage() { return ROUTE_STAGE.get(routeName()) || ROUTE_STAGE.get("dashboard"); }
  function nodeFor(route) { return (projection?.routeNodes || []).find((item) => item.route === route) || {}; }
  function signalCount(stage) {
    const counts = projection?.signalCounts || {};
    if (stage === "central") return Number(counts.actionReady || 0) + Number(counts.executing || 0) + Number(counts.reviewPending || 0) + Number(counts.blocked || 0);
    if (["actionReady", "action_ready"].includes(stage)) return Number(counts.actionReady || 0);
    if (["reviewPending", "review_pending"].includes(stage)) return Number(counts.reviewPending || 0);
    if (["executing", "learned", "sensed", "interpreted"].includes(stage)) return Number(counts[stage] || 0);
    if (stage === "health") return Number(counts.blocked || 0);
    return 0;
  }
  function fingerprint(value) {
    try { return JSON.stringify(value); } catch (error) { return String(value || ""); }
  }
  function pulse() {
    const shell = document.querySelector(".app-shell");
    if (!shell) return;
    shell.classList.remove("neural-pulse-active");
    void shell.offsetWidth;
    shell.classList.add("neural-pulse-active");
    window.setTimeout(() => shell.classList.remove("neural-pulse-active"), 950);
  }
  function navState(route, node) {
    if (route === currentStage().route) return "active";
    if (route === "system-status" && Number(node.count || 0) > 0) return "attention";
    if (route === "business-report" && Number(node.count || 0) > 0) return "learned";
    if (Number(node.count || 0) > 0) return "ready";
    return "idle";
  }
  function decorateNavigation() {
    document.querySelectorAll(".nav a[data-route]").forEach((link) => {
      const route = link.dataset.route;
      const node = nodeFor(route);
      const count = Number(node.count || 0);
      link.dataset.neuralState = navState(route, node);
      if (count > 0) link.dataset.neuralCount = String(count);
      else delete link.dataset.neuralCount;
      link.title = text(node.label || link.textContent || route);
    });
  }
  function renderTopbar() {
    const first = document.querySelector(".topbar > div:first-child");
    if (!first) return;
    let status = first.querySelector(".neural-route-status");
    if (!status) {
      status = document.createElement("div");
      status.className = "neural-route-status";
      status.innerHTML = "<i></i><strong></strong><span></span>";
      first.appendChild(status);
    }
    const stage = currentStage();
    const routeNode = nodeFor(stage.route);
    const count = Number(routeNode.count ?? signalCount(stage.stage));
    status.dataset.tone = stage.stage === "health" && count > 0 ? "attention" : stage.stage === "learned" ? "learned" : "signal";
    status.querySelector("strong").textContent = stage.label;
    status.querySelector("span").textContent = `${STAGE_COPY[stage.stage] || "经营信号正在流转"}${count > 0 ? ` · ${count}` : ""}`;
    const view = document.getElementById("appView");
    if (view) view.dataset.neuralStage = stage.stage;
  }
  function render() {
    decorateNavigation();
    renderTopbar();
  }
  async function fetchProjection({ pulseOnChange = false } = {}) {
    try {
      const response = await fetch("/api/modules/neural-operating", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const next = await response.json();
      const nextFingerprint = fingerprint({ signalCounts: next.signalCounts, health: next.health, operatorProfile: next.operatorProfile });
      const changed = Boolean(lastFingerprint && nextFingerprint !== lastFingerprint);
      projection = next;
      lastFingerprint = nextFingerprint;
      render();
      if (pulseOnChange && changed) pulse();
      window.dispatchEvent(new CustomEvent("neural-operating-updated", { detail: { projection, version: VERSION } }));
      return projection;
    } catch (error) {
      console.warn("[neural-operating-ui] projection unavailable", error);
      render();
      return null;
    }
  }
  function scheduleRefresh(reason = "event") {
    if (refreshTimer) window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null;
      fetchProjection({ pulseOnChange: reason !== "route" });
    }, 180);
  }

  window.addEventListener("hashchange", () => {
    render();
    scheduleRefresh("route");
  });
  window.addEventListener("api-cache-updated", () => scheduleRefresh("cache"));
  window.addEventListener("v148-import-queued", () => scheduleRefresh("import"));
  window.addEventListener("task-state-changed", () => scheduleRefresh("task"));
  window.addEventListener("load", render, { once: true });

  window.NeuralOperatingUI = {
    version: VERSION,
    projection: () => projection,
    refresh: () => fetchProjection({ pulseOnChange: true }),
    render,
  };

  render();
  fetchProjection();
})();
