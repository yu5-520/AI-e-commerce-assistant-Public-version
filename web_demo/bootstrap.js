(function installTaskDetailPayloadGuardV23211() {
  const VERSION = "26.11.0";
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
  const ASSET_VERSION = "26.11.0";
  const ASSET_HASHES = {"dashboard/page.js":"a0213e5d1c3cc962d82b0b33ad9412bb5c2fa92f3e508ff737819d467cdf4cd0","report/page.js":"a333eebf6fa1aefc8841758d7b6ca0283b830d2de1b653a5adeab1c87c0944f4","operating-unit/page.js":"7fd3789684fd8dcbd1c3bda7d3382f65f87c69186aec7f5c539b915e8e10c441","product/page.js":"24f74ffcab49ea6996a2d97b82b27e9c41c3754067c92ab64ddde2e651d4945a","competitor/page.js":"851a1cf0042964b59d0f5db92dae0c373820bdcf7f8e8e4e70f19f590b2172e6","listing/page.js":"a1f58fb669071275c4206490f0509d6c2f2ef01a5a4c069d1b17278e265e23da","traffic/page.js":"b34f4e1454b6827bdcc2ffb5c53e35934378495e7d4105857b9c051643cbf97d","todo/page.js":"c9c35bd6b7845020186e3e19055ea6808b5071a5a062e5e93bf525c86ca98d72","task-report/page.js":"15722e13c180360742db5f14c55366de97a68947073a765e735679a61a7e5e77","log/page.js":"eeec2ef4786b165a61048c447f89fa1421bec96ca9a2b4786cc96702d1fc2df6","knowledge-center/page.js":"a457566ed81d6e276890c92a368dc1e858aa79c82edb6426337451812f0fdece","system-status/page.js":"7888b8a58aec723133746a118cc6ef7fab154367707e1cfa2e0d3ee645be0b8c"};
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
    ["task-submit", "步骤工作台", "TaskReportPage", "task-report/page.js"],
    ["business-report", "日志", "LogPage", "log/page.js"],
    ["knowledge-center", "RAG知识中心", "KnowledgeCenterPage", "knowledge-center/page.js"],
    ["system-status", "系统状态", "SystemStatusPage", "system-status/page.js"],
  ];

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
    AppRouter.registerLazy({ route, title, globalName, src: `/web_demo/modules/${file}?v=${ASSET_HASHES[file] || ASSET_VERSION}` });
  });

  window.addEventListener("api-client-error", setApiBadge);
  window.addEventListener("api-client-status", setApiBadge);
  window.CompetitionRuntimeActor = Object.freeze({
    actorId: "competition_operator",
    role: "operator",
    workspaceId: "competition_demo",
    serverInjected: true,
  });
  AppRouter.start();
  setApiBadge();
})();