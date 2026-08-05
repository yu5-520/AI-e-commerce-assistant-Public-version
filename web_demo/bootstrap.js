(function installTaskDetailPayloadGuardV23211() {
  const VERSION = "23.2.11";
  const MISSING_MARKER = "__taskDetailProjectionMissingV23211";
  const api = window.AppApi = window.AppApi || {};
  const originalFetch = typeof window.fetch === "function" ? window.fetch.bind(window) : null;
  const originalTaskReport = typeof api.taskReport === "function" ? api.taskReport.bind(api) : null;

  function object(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function nonEmptyObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0;
  }

  function requestPath(input) {
    const raw = typeof input === "string" ? input : input?.url || "";
    try {
      return new URL(raw, window.location?.href || "http://localhost/").pathname;
    } catch (error) {
      return String(raw).split("?")[0].split("#")[0];
    }
  }

  function isTaskDetailRequest(input) {
    return /^\/api\/view\/tasks\/[^/]+$/.test(requestPath(input));
  }

  function projectionCandidates(payload) {
    const item = object(payload?.item);
    const report = Object.keys(object(item.taskDetailReport)).length ? object(item.taskDetailReport) : object(payload?.taskDetailReport);
    const related = Object.keys(object(item.relatedTask)).length
      ? object(item.relatedTask)
      : Object.keys(object(payload?.relatedTask)).length
        ? object(payload.relatedTask)
        : item;
    return [
      payload?.taskMetricEvidenceProjection,
      item.taskMetricEvidenceProjection,
      report.taskMetricEvidenceProjection,
      object(report.taskPlan).taskMetricEvidenceProjection,
      related.taskMetricEvidenceProjection,
      object(related.taskPlan).taskMetricEvidenceProjection,
    ];
  }

  function injectMissingProjection(payload) {
    if (!payload || typeof payload !== "object" || payload.ready === false) return false;
    if (projectionCandidates(payload).some(nonEmptyObject)) return false;
    payload.taskMetricEvidenceProjection = {
      [MISSING_MARKER]: true,
      evidenceStatus: "evidence_missing",
      taskExecutableFromEvidence: false,
    };
    return true;
  }

  async function guardedFetch(input, init) {
    const response = await originalFetch(input, init);
    if (!response?.ok || !isTaskDetailRequest(input)) return response;

    let payload;
    try {
      payload = await response.clone().json();
    } catch (error) {
      return response;
    }
    if (!injectMissingProjection(payload)) return response;

    const headers = new Headers(response.headers || {});
    headers.set("content-type", "application/json; charset=utf-8");
    return new Response(JSON.stringify(payload), {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }

  function hasMissingMarker(value) {
    return Boolean(object(value)[MISSING_MARKER]);
  }

  function replaceMarkedProjection(container, key = "taskMetricEvidenceProjection") {
    if (!container || typeof container !== "object" || !hasMissingMarker(container[key])) return false;
    container[key] = {};
    return true;
  }

  function normalizeGuardedReport(report) {
    if (!report || typeof report !== "object") return report;
    let guarded = false;
    guarded = replaceMarkedProjection(report) || guarded;
    guarded = replaceMarkedProjection(report.taskDetailReport) || guarded;
    guarded = replaceMarkedProjection(report.relatedTask) || guarded;
    guarded = replaceMarkedProjection(report.taskDetailReport?.taskPlan) || guarded;
    guarded = replaceMarkedProjection(report.relatedTask?.taskPlan) || guarded;

    report.frontendTaskReadModelVersion = VERSION;
    if (report.relatedTask && typeof report.relatedTask === "object") {
      report.relatedTask.frontendTaskReadModelVersion = VERSION;
    }
    if (!guarded) return report;

    delete report.taskMetricEvidenceProjectionVersion;
    report.taskEvidenceStatus = report.taskEvidenceStatus || "evidence_missing";
    report.taskEvidenceExecutable = false;
    report.evidenceExecutionBlocked = true;
    return report;
  }

  function clearEmptyHttpStatus(error) {
    const diagnosticStatus = Number(error?.frontendDiagnostic?.httpStatus);
    if (!Number.isFinite(diagnosticStatus) || diagnosticStatus <= 0) {
      if (error?.frontendDiagnostic) delete error.frontendDiagnostic.httpStatus;
    }
    const errorStatus = Number(error?.httpStatus);
    if (!Number.isFinite(errorStatus) || errorStatus <= 0) delete error.httpStatus;
    return error;
  }

  if (originalFetch) window.fetch = guardedFetch;
  if (originalTaskReport) {
    api.taskReport = async function guardedTaskReport(...args) {
      try {
        return normalizeGuardedReport(await originalTaskReport(...args));
      } catch (error) {
        throw clearEmptyHttpStatus(error);
      }
    };
  }

  api.taskReadModelVersion = VERSION;
  window.TaskDetailPayloadGuardVersion = VERSION;
})();

(async function () {
  const ASSET_VERSION = "23.2.11";
  const V10_MAIN_NAV = ["dashboard", "data-check", "operating-unit", "business-actions", "business-report", "accounts", "system-status"];
  const OPERATOR_NAV = ["dashboard", "data-check", "operating-unit", "business-actions", "business-report", "accounts"];
  const INTERNAL_TO_V10_NAV = new Map([
    ["store-overview", "operating-unit"], ["executive-cockpit", "dashboard"], ["people-overview", "business-actions"],
    ["task-command", "business-actions"], ["manager-tasks", "business-actions"], ["manager-dispatch", "business-actions"],
    ["manager-review", "business-actions"], ["manager-modules", "operating-unit"], ["manager-retrospective", "business-report"],
    ["manager-reports", "business-report"], ["business-products", "operating-unit"], ["business-competitors", "operating-unit"],
    ["business-listing", "operating-unit"], ["business-traffic", "operating-unit"], ["trend-center", "operating-unit"],
    ["weight-center", "operating-unit"], ["tenant-config", "system-status"], ["config-audit", "system-status"],
    ["release-governance", "system-status"], ["release-alerts", "system-status"], ["feedback-flywheel", "business-report"],
    ["task-report", "business-actions"], ["task-submit", "business-actions"],
  ]);
  const PAGE_MANIFEST = [
    ["dashboard", "总览", "DashboardPage", "dashboard/page.js"],
    ["data-check", "AI 经营链路", "ReportPage", "report/page.js"],
    ["operating-unit", "经营", "OperatingUnitPage", "operating-unit/page.js"],
    ["business-products", "商品档案", "ProductPage", "product/page.js"],
    ["business-competitors", "竞品信号", "CompetitorPage", "competitor/page.js"],
    ["business-listing", "上新测试", "ListingPage", "listing/page.js"],
    ["business-traffic", "流量趋势", "TrafficPage", "traffic/page.js"],
    ["business-actions", "任务", "TodoPage", "todo/page.js"],
    ["task-report", "任务报告", "TaskReportPage", "task-report/page.js"],
    ["task-submit", "提交任务", "TaskSubmitPage", "task-submit/page.js"],
    ["business-report", "日志", "LogPage", "log/page.js"],
    ["accounts", "账号", "AccountPage", "account/page.js"],
    ["role-console", "权限入口", "RoleConsolePage", "account/page.js"],
    ["system-status", "系统状态", "SystemStatusPage", "system-status/page.js"],
  ];

  function compressedRoute(route) { return INTERNAL_TO_V10_NAV.get(route) || route; }
  function visibleModulesFor(account) {
    const role = account?.currentUser?.roleId;
    if (role === "operator") return OPERATOR_NAV;
    if (["owner", "manager", "finance", "observer"].includes(role)) return V10_MAIN_NAV;
    const base = account?.currentUser?.visibleModules || V10_MAIN_NAV;
    const compressed = base.map(compressedRoute).filter((route) => V10_MAIN_NAV.includes(route));
    return Array.from(new Set(compressed.length ? compressed : V10_MAIN_NAV));
  }
  function setApiBadge() {
    const badge = document.getElementById("apiModeBadge");
    if (!badge) return;
    const source = window.AppApi?.status?.source;
    const ok = source === "server";
    badge.textContent = ok ? "后端正常" : source === "unknown" ? "接口检测中" : "接口异常";
    badge.title = window.AppApi?.failureSummary?.() || "接口状态未知";
    badge.classList.toggle("warning", !ok && source !== "unknown");
  }

  PAGE_MANIFEST.forEach(([route, title, globalName, file]) => {
    AppRouter.registerLazy({ route, title, globalName, src: `/web_demo/modules/${file}?v=${ASSET_VERSION}` });
  });

  function applyNavigationScope(account) {
    const visible = new Set(visibleModulesFor(account));
    document.querySelectorAll(".nav a[data-route]").forEach((link) => { link.hidden = !!visible.size && !visible.has(link.dataset.route); });
  }
  function renderAccountSwitcher(account) {
    const select = document.getElementById("accountSwitcher");
    if (!select || !account?.users) return;
    const currentId = account.currentUser?.id || AppApi.getCurrentUserId();
    select.innerHTML = account.users.map((user) => `<option value="${AppShell.escape(user.id)}" ${user.id === currentId ? "selected" : ""}>${AppShell.escape(user.displayName || user.name)} · ${AppShell.escape(user.positionTitle || user.roleName)}</option>`).join("");
    applyNavigationScope(account);
    select.onchange = async () => {
      select.disabled = true;
      try {
        await AppApi.switchAccount(select.value);
        const nextAccount = await AppApi.accounts();
        renderAccountSwitcher(nextAccount);
        const active = compressedRoute(AppRouter.routeFromHash());
        const allowed = new Set(visibleModulesFor(nextAccount));
        if (allowed.size && !allowed.has(active)) AppRouter.navigate("dashboard");
        else AppRouter.schedule("account-switch");
      } finally {
        setApiBadge();
        select.disabled = false;
      }
    };
  }

  window.addEventListener("api-client-error", setApiBadge);
  window.addEventListener("api-client-status", setApiBadge);
  AppRouter.start();
  setApiBadge();

  try {
    renderAccountSwitcher(await AppApi.accounts());
  } catch (error) {
    console.error("[bootstrap] account projection unavailable", error);
  } finally {
    setApiBadge();
  }
})();
