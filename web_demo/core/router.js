(function () {
  const routes = new Map();
  const lazyRoutes = new Map();
  const loadingScripts = new Map();
  const aliases = new Map();
  let current = null;
  let scheduled = false;
  let renderToken = 0;
  let pendingState = {};
  const ROUTE_ERROR_STORAGE_KEY = "frontend-last-route-error-v23210";

  function parseHash() {
    const raw = location.hash.replace(/^#/, "") || "dashboard";
    const separator = raw.indexOf("?");
    const rawRoute = separator >= 0 ? raw.slice(0, separator) : raw;
    const query = separator >= 0 ? raw.slice(separator + 1) : "";
    const state = {};
    const params = new URLSearchParams(query);
    params.forEach((value, key) => { if (key) state[key] = value; });
    return { raw, rawRoute: rawRoute || "dashboard", state };
  }

  function serializableState(state = {}) {
    const result = {};
    Object.entries(state || {}).forEach(([key, value]) => {
      if (!key || value === undefined || value === null || value === "") return;
      if (["string", "number", "boolean"].includes(typeof value)) result[key] = String(value);
    });
    return result;
  }

  function buildHash(route, state = {}) {
    const target = aliases.get(route) || route || "dashboard";
    const params = new URLSearchParams(serializableState(state));
    const query = params.toString();
    return query ? `${target}?${query}` : target;
  }

  function rawRouteFromHash() { return parseHash().rawRoute; }
  function stateFromHash() { return parseHash().state; }
  function routeFromHash() { const raw = rawRouteFromHash(); return aliases.get(raw) || raw; }
  function hasRoute(route) { return routes.has(route) || lazyRoutes.has(route); }
  function routeMeta(route) { return routes.get(route) || lazyRoutes.get(route) || {}; }
  function hrefFor(route, state = {}) { return `#${buildHash(route, state)}`; }
  function isAbortError(error) { return error?.name === "AbortError" || /route_request_aborted|route_cleanup|route_replaced|external_abort/.test(String(error?.message || error || "")); }

  function safeDiagnosticText(value, limit = 320) {
    const text = String(value ?? "").replace(/[\r\n\t]+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  }

  function routeDiagnostic(error, route, state = {}, reason = "route") {
    const source = error?.frontendDiagnostic && typeof error.frontendDiagnostic === "object" ? error.frontendDiagnostic : {};
    const statusValue = source.httpStatus ?? error?.httpStatus;
    const readyValue = source.responseReady ?? error?.responseReady;
    return {
      route: safeDiagnosticText(route, 80),
      taskId: safeDiagnosticText(source.taskId || error?.taskId || state?.taskId, 120),
      stage: safeDiagnosticText(source.stage || error?.stage || "route_render", 80),
      requestPath: safeDiagnosticText(source.requestPath || error?.requestPath, 240),
      httpStatus: Number.isFinite(Number(statusValue)) ? Number(statusValue) : null,
      responseReady: typeof readyValue === "boolean" ? readyValue : null,
      errorName: safeDiagnosticText(source.errorName || error?.name || "Error", 80),
      errorMessage: safeDiagnosticText(source.errorMessage || error?.message || String(error || "页面读取失败"), 320),
      timestamp: safeDiagnosticText(source.timestamp || new Date().toISOString(), 64),
      retryReason: safeDiagnosticText(reason, 80),
    };
  }

  function storeRouteDiagnostic(diagnostic) {
    window.__LAST_ROUTE_ERROR__ = diagnostic;
    try { sessionStorage.setItem(ROUTE_ERROR_STORAGE_KEY, JSON.stringify(diagnostic)); } catch (error) {}
    return diagnostic;
  }

  function clearRouteDiagnostic(route, taskId = "") {
    const currentError = window.__LAST_ROUTE_ERROR__;
    if (!currentError || currentError.route !== route || (taskId && currentError.taskId && currentError.taskId !== taskId)) return;
    window.__LAST_ROUTE_ERROR__ = null;
    try { sessionStorage.removeItem(ROUTE_ERROR_STORAGE_KEY); } catch (error) {}
  }

  function diagnosticRows(diagnostic) {
    const labels = {
      route: "路由",
      taskId: "任务 ID",
      stage: "失败阶段",
      requestPath: "请求路径",
      httpStatus: "HTTP 状态",
      responseReady: "ready 状态",
      errorName: "错误名称",
      errorMessage: "错误信息",
      timestamp: "发生时间",
    };
    return Object.entries(labels)
      .map(([key, label]) => {
        const value = diagnostic?.[key];
        if (value === undefined || value === null || value === "") return "";
        return `<div><dt>${AppShell.escape(label)}</dt><dd>${AppShell.escape(String(value))}</dd></div>`;
      })
      .join("");
  }

  function diagnosticCopyText(diagnostic) {
    const allowed = ["route", "taskId", "stage", "requestPath", "httpStatus", "responseReady", "errorName", "errorMessage", "timestamp"];
    const value = {};
    allowed.forEach((key) => { if (diagnostic?.[key] !== undefined && diagnostic?.[key] !== null && diagnostic?.[key] !== "") value[key] = diagnostic[key]; });
    return JSON.stringify(value, null, 2);
  }

  async function copyDiagnostic(diagnostic) {
    const text = diagnosticCopyText(diagnostic);
    if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }

  function errorView(error, diagnostic) {
    const timeout = error?.name === "TimeoutError" || /timeout|超时/i.test(String(error?.message || error || ""));
    const title = timeout ? "读取时间过长" : "暂时无法读取";
    const message = timeout ? "当前数据没有在预期时间内返回。页面不会继续空等，可重新读取或返回上一页。" : "当前页面数据读取失败，已有业务数据不会被本地模拟内容替代。";
    return `<section class="route-error-state"><div class="route-error-icon">!</div><h2>${AppShell.escape(title)}</h2><p>${AppShell.escape(message)}</p><div class="report-actions"><button type="button" data-router-retry>重新读取</button><button type="button" class="secondary" data-router-back>返回</button></div><details class="route-error-diagnostics"><summary>查看错误诊断</summary><dl>${diagnosticRows(diagnostic)}</dl><div class="route-error-copy-row"><button type="button" class="secondary" data-router-copy>复制诊断</button><span data-router-copy-status aria-live="polite"></span></div></details></section>`;
  }

  function bars(count, className = "loading-line") { return Array.from({ length: count }, (_, index) => `<i class="${className}" style="--loading-index:${index}"></i>`).join(""); }
  function loadingView(title = "", route = "") {
    if (route === "task-report" || route === "task-submit") {
      return `<section class="task-detail-loading" aria-label="任务详情加载中"><div class="task-detail-loading-hero"><div>${bars(1, "loading-title")}${bars(1, "loading-subtitle")}</div><div class="loading-pill"></div></div><div class="task-detail-loading-grid"><article>${bars(1, "loading-card-title")}${bars(4)}</article><article>${bars(1, "loading-card-title")}${bars(3)}</article><article class="wide">${bars(1, "loading-card-title")}${bars(5)}</article></div></section>`;
    }
    return `<section class="module-loading" aria-label="页面加载中"><div class="module-loading-hero">${bars(1, "loading-title")}${bars(1, "loading-subtitle")}</div><div class="module-loading-metrics">${Array.from({ length: 4 }, () => `<article>${bars(1, "loading-card-title")}${bars(1, "loading-metric")}</article>`).join("")}</div><div class="module-loading-panel">${bars(1, "loading-card-title")}${bars(5)}</div></section>`;
  }

  function createContext(route, token, state = {}) {
    const cleanup = [];
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    let cleaned = false;
    return {
      route,
      token,
      state,
      signal: controller?.signal,
      isCurrent: () => !cleaned && token === renderToken && routeFromHash() === route,
      abort(reason = "route_replaced") { if (controller && !controller.signal.aborted) controller.abort(reason); },
      on(selector, type, handler, options) { document.querySelectorAll(selector).forEach((node) => { node.addEventListener(type, handler, options); cleanup.push(() => node.removeEventListener(type, handler, options)); }); },
      delegate(selector, type, handler, root = AppShell.view()) { if (!root) return; const wrapped = (event) => { const target = event.target.closest(selector); if (!target || !root.contains(target)) return; handler(event, target); }; root.addEventListener(type, wrapped); cleanup.push(() => root.removeEventListener(type, wrapped)); },
      addCleanup(fn) { if (typeof fn === "function") cleanup.push(fn); },
      cleanup() {
        if (cleaned) return;
        cleaned = true;
        if (controller && !controller.signal.aborted) controller.abort("route_cleanup");
        while (cleanup.length) { try { cleanup.pop()(); } catch (error) { console.error("[router] cleanup error", error); } }
      },
    };
  }

  function loadScript(src) {
    if (!src) return Promise.reject(new Error("Lazy route missing src"));
    if (loadingScripts.has(src)) return loadingScripts.get(src);
    const existed = document.querySelector(`script[data-lazy-page="${src}"]`);
    if (existed?.dataset.loaded === "1") return Promise.resolve();
    const promise = new Promise((resolve, reject) => {
      const script = existed || document.createElement("script");
      script.src = src;
      script.async = false;
      script.dataset.lazyPage = src;
      script.onload = () => { script.dataset.loaded = "1"; resolve(); };
      script.onerror = () => reject(new Error(`页面文件加载失败：${src}`));
      if (!existed) document.body.appendChild(script);
    });
    loadingScripts.set(src, promise);
    return promise;
  }

  async function resolvePage(route) {
    if (routes.has(route)) return routes.get(route);
    const lazy = lazyRoutes.get(route);
    if (!lazy) return null;
    await loadScript(lazy.src);
    const page = window[lazy.globalName];
    if (!page || !page.route) throw new Error(`页面模块未注册：${route}`);
    register(page);
    return page;
  }

  async function renderNow(reason = "route") {
    scheduled = false;
    const requested = routeFromHash();
    const route = hasRoute(requested) ? requested : "dashboard";
    const token = ++renderToken;
    const state = { ...stateFromHash(), ...(pendingState || {}) };
    const preserveRenderedView = ["cache-revalidated", "refresh"].includes(reason) && current?.route === route && current?.status === "mounted";
    pendingState = {};

    if (current?.page?.unmount) { try { current.page.unmount(current.ctx); } catch (error) { console.error("[router] unmount error", error); } }
    current?.ctx?.cleanup?.();
    current = null;

    AppShell.setActive(route);
    AppShell.setTitle(routeMeta(route).title || "");
    if (!preserveRenderedView) AppShell.setView(loadingView(routeMeta(route).title || "", route));

    const ctx = createContext(route, token, state);
    current = { route, page: null, ctx, reason, status: "loading" };
    window.dispatchEvent(new CustomEvent("app-route-loading", { detail: { route, reason, token, state, preserveRenderedView } }));

    try {
      const page = await resolvePage(route) || await resolvePage("dashboard");
      if (!ctx.isCurrent()) return;
      current.page = page;
      AppShell.setTitle(page.title || routeMeta(route).title || "总览");
      if (!preserveRenderedView && typeof page.loadingHtml === "function") AppShell.setView(page.loadingHtml(ctx) || loadingView(page.title || "", route));

      const html = await page.render(ctx);
      if (!ctx.isCurrent()) return;
      AppShell.setView(html || "");
      if (page.mount) page.mount(ctx);
      current.status = "mounted";
      clearRouteDiagnostic(route, state?.taskId || "");
      window.dispatchEvent(new CustomEvent("app-route-mounted", { detail: { route, reason, token, state } }));
    } catch (error) {
      if (isAbortError(error) || !ctx.isCurrent()) return;
      console.error("[router] render error", error);
      const diagnostic = storeRouteDiagnostic(routeDiagnostic(error, route, state, reason));
      current.status = "error";
      AppShell.setView(errorView(error, diagnostic));
      document.querySelector("[data-router-retry]")?.addEventListener("click", () => {
        if (route === "task-report" && diagnostic.taskId) window.AppApi?.clearTaskDetailCache?.(diagnostic.taskId);
        schedule("error-retry", state);
      }, { once: true });
      document.querySelector("[data-router-back]")?.addEventListener("click", () => history.back(), { once: true });
      document.querySelector("[data-router-copy]")?.addEventListener("click", async () => {
        const status = document.querySelector("[data-router-copy-status]");
        try { await copyDiagnostic(diagnostic); if (status) status.textContent = "已复制"; }
        catch (copyError) { if (status) status.textContent = "复制失败，请截图保存"; }
      });
      window.dispatchEvent(new CustomEvent("app-route-error", { detail: { route, reason, token, state, diagnostic } }));
    }
  }

  function schedule(reason = "route", state = null) {
    if (state) pendingState = { ...pendingState, ...state };
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => renderNow(reason));
  }

  function register(page) { if (!page || !page.route) throw new Error("Route page requires route"); routes.set(page.route, page); }
  function registerLazy(page) { if (!page || !page.route || !page.src || !page.globalName) throw new Error("Lazy route requires route/src/globalName"); lazyRoutes.set(page.route, page); }

  function navigate(route, state = null) {
    if (!route) return;
    const target = aliases.get(route) || route;
    const nextState = serializableState(state || {});
    const nextHash = buildHash(target, nextState);
    const currentHash = location.hash.replace(/^#/, "");
    pendingState = { ...nextState };
    if (currentHash === nextHash) schedule("same-route", nextState);
    else location.hash = nextHash;
  }

  function replace(route, state = null) {
    const nextHash = buildHash(route, state || {});
    history.replaceState(null, "", `${location.pathname}${location.search}#${nextHash}`);
    schedule("replace-route", state || {});
  }

  function currentSignal() { return current?.ctx?.signal || null; }
  function currentContext() { return current?.ctx || null; }

  function start() {
    window.addEventListener("hashchange", () => schedule("hashchange"));
    window.addEventListener("api-cache-updated", () => schedule("cache-revalidated"));
    document.getElementById("refreshBtn")?.addEventListener("click", () => schedule("refresh"));
    const parsed = parseHash();
    const target = routeFromHash();
    if (target !== parsed.rawRoute) location.hash = buildHash(target, parsed.state);
    else schedule("start");
  }

  window.AppRouter = { register, registerLazy, start, navigate, replace, schedule, routeFromHash, stateFromHash, hasRoute, hrefFor, buildHash, currentSignal, currentContext };
})();
