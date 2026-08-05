(function () {
  const ACCOUNT_KEY = "ai_ecommerce_v442_current_user_id";
  const API_CLIENT_VERSION = "22.5.5";
  const status = { source: "unknown", failures: [], lastImportSync: null, lastError: null };
  const memoryCache = new Map();
  const revalidateInFlight = new Map();
  let account = null;

  function getCurrentUserId() { return localStorage.getItem(ACCOUNT_KEY) || "U001"; }
  function setCurrentUserId(userId) { localStorage.setItem(ACCOUNT_KEY, userId || "U001"); }
  function currentUser() { return account?.currentUser || null; }
  function currentPermissions() { return currentUser()?.permissions || []; }
  function can(permission) { return currentPermissions().includes(permission); }
  function failureSummary() { if (!status.failures.length) return "所有模块接口请求正常。"; return status.failures.slice(-5).map((item) => `${item.path}: ${item.message}`).join("\n"); }
  function setServerHealthy(path = "") { status.source = "server"; status.lastError = null; window.dispatchEvent(new CustomEvent("api-client-status", { detail: { source: status.source, path } })); }
  function isAbortError(error) { return error?.name === "AbortError" || /route_cleanup|route_replaced|external_abort|route_request_aborted/.test(String(error?.message || error || "")); }
  function recordFailure(path, error) { if (isAbortError(error)) return; const message = error?.message || String(error || "接口异常"); status.source = "error"; status.lastError = { path, message, at: Date.now() }; status.failures.push(status.lastError); window.dispatchEvent(new CustomEvent("api-client-error", { detail: status.lastError })); console.error(`[api-client] request failed for ${path}`, error); }
  function buildQuery(params = {}) { const query = new URLSearchParams(); Object.entries(params || {}).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") query.set(key, value); }); return query.toString() ? `?${query.toString()}` : ""; }
  async function parseError(response) { let detail = ""; try { const payload = await response.json(); detail = payload?.detail || payload?.message || ""; } catch (error) { detail = ""; } return detail || `${response.status} ${response.statusText}`; }

  function cacheKey(path) { return `${API_CLIENT_VERSION}::${getCurrentUserId()}::${path}`; }
  function clearApiCaches() {
    memoryCache.clear();
    revalidateInFlight.clear();
    try {
      for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
        const key = sessionStorage.key(index);
        if (key && key.startsWith("api-cache:")) sessionStorage.removeItem(key);
      }
    } catch (error) {}
  }
  function rememberCache(path, payload) { if (!path || payload === undefined) return payload; const entry = { payload, at: Date.now(), version: API_CLIENT_VERSION }; memoryCache.set(cacheKey(path), entry); try { sessionStorage.setItem(`api-cache:${cacheKey(path)}`, JSON.stringify(entry)); } catch (error) {} return payload; }
  function cacheEntry(path, maxAgeMs = 30000) { const key = cacheKey(path); const hit = memoryCache.get(key); if (hit && hit.version === API_CLIENT_VERSION && Date.now() - Number(hit.at || 0) <= maxAgeMs) return hit; try { const raw = sessionStorage.getItem(`api-cache:${key}`); if (!raw) return null; const parsed = JSON.parse(raw); if (parsed?.version === API_CLIENT_VERSION && Date.now() - Number(parsed.at || 0) <= maxAgeMs) { memoryCache.set(key, parsed); return parsed; } sessionStorage.removeItem(`api-cache:${key}`); } catch (error) {} return null; }
  function cached(path, maxAgeMs = 30000) { return cacheEntry(path, maxAgeMs)?.payload ?? null; }
  function fingerprint(payload) { try { return JSON.stringify(payload); } catch (error) { return String(payload); } }
  function cachePayload(payload, meta = {}) { if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload; return { ...payload, cacheState: meta.cacheState || "fresh", cacheAgeMs: meta.cacheAgeMs || 0, refreshing: Boolean(meta.refreshing) }; }
  function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

  async function request(path, _fallback = null, options = {}) {
    const method = options.method || "GET";
    const timeoutMs = Number(options.timeoutMs || (method === "GET" ? 4500 : 20000));
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const externalSignal = options.signal || null;
    let timedOut = false;
    const forwardAbort = () => { if (controller && !controller.signal.aborted) controller.abort(externalSignal?.reason || "external_abort"); };
    if (externalSignal?.aborted) forwardAbort();
    else externalSignal?.addEventListener?.("abort", forwardAbort, { once: true });
    const timer = controller ? setTimeout(() => { timedOut = true; controller.abort("request_timeout"); }, timeoutMs) : null;
    try {
      const response = await fetch(path, { method, signal: controller?.signal || externalSignal || undefined, headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": getCurrentUserId() }, body: options.body ? JSON.stringify(options.body) : undefined });
      if (!response.ok) throw new Error(await parseError(response));
      const payload = await response.json();
      setServerHealthy(path);
      if (method === "GET") rememberCache(path, payload);
      return payload;
    } catch (error) {
      if (timedOut) { const timeoutError = new Error(`请求超时：${path}`); timeoutError.name = "TimeoutError"; recordFailure(path, timeoutError); throw timeoutError; }
      if (!isAbortError(error)) recordFailure(path, error);
      throw error;
    } finally {
      if (timer) clearTimeout(timer);
      externalSignal?.removeEventListener?.("abort", forwardAbort);
    }
  }

  function revalidate(path, options = {}) {
    const key = cacheKey(path);
    if (revalidateInFlight.has(key)) return revalidateInFlight.get(key);
    const before = fingerprint(cached(path, Number.MAX_SAFE_INTEGER));
    const promise = request(path, null, { ...options, signal: null })
      .then((payload) => { if (fingerprint(payload) !== before) window.dispatchEvent(new CustomEvent("api-cache-updated", { detail: { path, payload, version: API_CLIENT_VERSION } })); return payload; })
      .catch((error) => { if (!isAbortError(error)) console.warn(`[api-client] background refresh failed for ${path}`, error); return null; })
      .finally(() => revalidateInFlight.delete(key));
    revalidateInFlight.set(key, promise);
    return promise;
  }

  async function optionalRequest(path, fallback = {}, options = {}) {
    const maxStaleAgeMs = Number(options.maxStaleAgeMs ?? options.maxAgeMs ?? 30000);
    const freshForMs = Number(options.freshForMs || 5000);
    const entry = cacheEntry(path, maxStaleAgeMs);
    if (entry) {
      const age = Date.now() - Number(entry.at || 0);
      if (age > freshForMs) revalidate(path, { ...options, maxStaleAgeMs });
      return cachePayload(entry.payload, { cacheState: age <= freshForMs ? "fresh" : "stale", cacheAgeMs: age, refreshing: age > freshForMs });
    }
    try { return await request(path, null, { ...options, signal: options.signal || window.AppRouter?.currentSignal?.() || null }); }
    catch (error) { if (isAbortError(error)) throw error; return { ...fallback, ready: false, optionalError: error?.message || String(error || "接口异常"), optionalPath: path, stale: false }; }
  }

  async function uploadRequest(path, file, fields = {}) {
    try {
      const form = new FormData(); form.append("file", file); Object.entries(fields || {}).forEach(([key, value]) => form.append(key, value));
      const response = await fetch(path, { method: "POST", headers: { Accept: "application/json", "X-Mock-User-Id": getCurrentUserId() }, body: form });
      if (!response.ok) throw new Error(await parseError(response));
      setServerHealthy(path); return await response.json();
    } catch (error) { recordFailure(path, error); throw error; }
  }

  async function loadAccount() { account = await optionalRequest("/api/accounts", account || { currentUser: { id: getCurrentUserId(), roleId: "operator", roleName: "运营" }, users: [] }, { timeoutMs: 2500, freshForMs: 15000, maxStaleAgeMs: 300000 }); return account; }
  async function applyAccountMutation(path, body) { const result = await request(path, null, { method: "POST", body }); account = result?.account || (await loadAccount()); window.dispatchEvent(new CustomEvent("mock-account-change", { detail: { account } })); return result; }
  function clearViewState() { ["manager_task_state_v241", "manager_task_sort_v241", "manager_selected_task_v241", "owner_review_state", "owner_dashboard_state"].forEach((key) => localStorage.removeItem(key)); }
  function clearClientRuntime() { clearViewState(); clearApiCaches(); if (window.AppMockData) { window.AppMockData.products = []; window.AppMockData.competitors = []; window.AppMockData.listings = []; window.AppMockData.traffic = []; window.AppMockData.reportGroups = []; window.AppMockData.reportDetails = {}; window.AppMockData.recentAlerts = []; } status.lastImportSync = null; window.AppTaskStore?.hydrate?.([], [], [], {}); }
  function dataVersionFromImport(result = {}) { return result?.dataVersion || result?.syncState?.latestDataVersion || result?.operatingUnitSnapshotSync?.syncState?.latestDataVersion || result?.pipelineSync?.dataVersions?.[0] || result?.results?.find?.((item) => item?.dataVersion)?.dataVersion || ""; }
  function rememberImportSync(result) { status.lastImportSync = result?.taskGeneration || result?.pipelineSync || result?.importDiagnostics || null; window.dispatchEvent(new CustomEvent("v148-import-queued", { detail: { result, sync: status.lastImportSync } })); return result; }
  function productFormatters() { return { money(value) { return value === null || value === undefined || value === "" || value === "—" || value === "未识别" ? "未识别" : String(value).startsWith("¥") ? String(value) : `¥${value}`; }, percent(value) { return value === null || value === undefined || value === "" || value === "—" || value === "未识别" ? "未识别" : String(value).includes("%") ? String(value) : `${value}%`; } }; }

  const api = {
    status, failureSummary, getCurrentUserId, setCurrentUserId, currentUser, currentPermissions, can, productFormatters, clearApiCaches, version: API_CLIENT_VERSION,
    dashboard: () => request("/api/modules/dashboard"),
    dashboardView: () => optionalRequest("/api/view/dashboard", { ready: false }, { timeoutMs: 3500, maxStaleAgeMs: 30000 }),
    operatingUnit: (params = {}) => optionalRequest(`/api/modules/operating-unit${buildQuery(params)}`, { items: [], ready: false }, { timeoutMs: 3500, maxStaleAgeMs: 30000 }),
    productView: (params = {}) => optionalRequest(`/api/view/products${buildQuery(params)}`, { items: [], ready: false }, { timeoutMs: 3500, maxStaleAgeMs: 30000 }),
    productDetail: (productId, params = {}) => optionalRequest(`/api/view/products/${encodeURIComponent(productId)}${buildQuery(params)}`, { ready: false }, { timeoutMs: 4500, maxStaleAgeMs: 30000 }),
    taskView: (params = {}) => optionalRequest(`/api/view/tasks${buildQuery({ limit: 80, ...params })}`, { items: [], ready: false }, { timeoutMs: 3500, maxStaleAgeMs: 15000 }),
    systemStatusView: () => optionalRequest("/api/view/system-status", { ready: false }, { timeoutMs: 3500, maxStaleAgeMs: 15000 }),
    dataLineView: () => optionalRequest("/api/view/data-line", { ready: false, headline: "等待数据接入", lineStatus: "waiting", stations: [], formalTaskCount: 0, observeOnlyCount: 0 }, { timeoutMs: 3500, maxStaleAgeMs: 15000 }),
    pipelineLive: (dataVersion = "", limit = 40) => optionalRequest(`/api/view/pipeline-live${buildQuery({ dataVersion, limit })}`, { ready: false, stages: [], items: [], summary: {}, headline: "等待数据接入", snapshotStatus: "client_fallback" }, { timeoutMs: 6500, maxStaleAgeMs: 15000, freshForMs: 3000 }),
    refreshReadModel: (dataVersion = "") => api.post(`/api/view/refresh${dataVersion ? `?dataVersion=${encodeURIComponent(dataVersion)}` : ""}`, null, {}),
    pipelineStages: (dataVersion = "") => optionalRequest(`/api/pipeline/stages${dataVersion ? `?dataVersion=${encodeURIComponent(dataVersion)}` : ""}`, { stages: [] }, { timeoutMs: 2500, maxStaleAgeMs: 15000 }),
    rebuildOperatingSnapshot: (dataVersion = "") => api.post(`/api/modules/operating-unit/snapshot/rebuild${dataVersion ? `?dataVersion=${encodeURIComponent(dataVersion)}` : ""}`, null, {}),
    generateTasksStation: (dataVersion, body = {}) => api.post(`/api/pipeline/data-versions/${encodeURIComponent(dataVersion)}/tasks/generate`, null, body),
    snapshotTaskHandoff: (dataVersion, body = {}) => api.post("/api/station-handoffs/snapshot-task", null, { dataVersion, ...body }),
    stationHandoffs: (dataVersion = "") => optionalRequest(`/api/station-handoffs${dataVersion ? `?dataVersion=${encodeURIComponent(dataVersion)}` : ""}`, { items: [] }, { timeoutMs: 2500, maxStaleAgeMs: 15000 }),
    accounts: loadAccount,
    me: () => request("/api/accounts/me"),
    switchAccount: async (userId) => { const previousUserId = getCurrentUserId(); const switched = await request("/api/accounts/switch", null, { method: "POST", body: { userId } }); const nextUserId = switched?.currentUser?.id || userId || previousUserId; setCurrentUserId(nextUserId); clearApiCaches(); account = switched?.account || (await loadAccount()); await api.refreshTaskState().catch(() => null); window.dispatchEvent(new CustomEvent("mock-account-change", { detail: { account } })); return account; },
    updateUserRole: (userId, roleId) => applyAccountMutation(`/api/accounts/users/${encodeURIComponent(userId)}/role`, { roleId }),
    updateUserStores: (userId, storeIds) => applyAccountMutation(`/api/accounts/users/${encodeURIComponent(userId)}/stores`, { storeIds }),
    updateStoreAssignment: (storeId, primaryOperatorId, reviewerId = "U002") => applyAccountMutation(`/api/accounts/store-assignments/${encodeURIComponent(storeId)}`, { primaryOperatorId, reviewerId }),
    updateRolePermissions: (roleId, permissions) => applyAccountMutation(`/api/accounts/roles/${encodeURIComponent(roleId)}/permissions`, { permissions }),
    product: (params = {}) => optionalRequest(`/api/modules/product${buildQuery(params)}`, { products: [], items: [] }, { timeoutMs: 3500, maxStaleAgeMs: 30000 }),
    competitor: () => optionalRequest("/api/modules/competitor", { items: [] }, { timeoutMs: 3500 }),
    listing: () => optionalRequest("/api/modules/listing", { items: [] }, { timeoutMs: 3500 }),
    traffic: () => optionalRequest("/api/modules/traffic", { items: [] }, { timeoutMs: 3500 }),
    report: () => optionalRequest("/api/modules/report", { hasData: false, reportGroups: [] }, { timeoutMs: 2500 }),
    trendCenter: (limit = 30) => optionalRequest(`/api/trends/summary?limit=${encodeURIComponent(limit)}`, { items: [] }, { timeoutMs: 3000 }),
    metricEvidence: (body = {}) => api.post("/api/trends/metric-evidence", null, body),
    taskSop: (body = {}) => api.post("/api/trends/task-sop", null, body),
    agents: () => optionalRequest("/api/modules/agents", { items: [] }, { timeoutMs: 3500 }),
    moduleAgent: (module, id, mode = "analysis") => request(`/api/modules/agents/${encodeURIComponent(module)}/${encodeURIComponent(id)}?mode=${encodeURIComponent(mode)}`),
    cycleAgent: (target = "日报") => request(`/api/modules/agents/cycle/${encodeURIComponent(target)}`),
    createAgentTask: (module, id, draftIndex = 0, mode = "analysis") => api.post(`/api/modules/agents/${encodeURIComponent(module)}/${encodeURIComponent(id)}/tasks`, null, { draftIndex, mode }),
    generateTaskCandidates: (body = {}) => api.post("/api/modules/agents/tasks/generate", null, body),
    taskPlaybook: (taskId, preferredStyle = "") => request(`/api/modules/agents/tasks/${encodeURIComponent(taskId)}/playbook${preferredStyle ? `?preferred_style=${encodeURIComponent(preferredStyle)}` : ""}`),
    creativeAgent: (productId, body = {}) => api.post(`/api/modules/agents/creative/${encodeURIComponent(productId)}`, null, body),
    createCreativeTask: (productId, body = {}) => api.post(`/api/modules/agents/creative/${encodeURIComponent(productId)}/tasks`, null, body),
    feedbackFlywheel: () => optionalRequest("/api/modules/feedback-flywheel", { items: [] }, { timeoutMs: 3500 }),
    feedbackCycle: (target = "日报", limit = 8) => optionalRequest(`/api/modules/feedback-flywheel/cycle/${encodeURIComponent(target)}?limit=${encodeURIComponent(limit)}`, { items: [] }, { timeoutMs: 3500 }),
    draftFeedbackCycle: (target = "日报", body = {}) => api.post(`/api/modules/feedback-flywheel/cycle/${encodeURIComponent(target)}/draft`, null, body),
    ragMemory: () => optionalRequest("/api/modules/rag-memory", { items: [] }, { timeoutMs: 3500 }),
    ragCases: (params = {}) => optionalRequest(`/api/modules/rag-memory/cases${buildQuery(params)}`, { items: [] }, { timeoutMs: 3500 }),
    ragSearch: (params = {}) => optionalRequest(`/api/modules/rag-memory/search${buildQuery(params)}`, { items: [] }, { timeoutMs: 3500 }),
    draftTaskMemory: (taskId, body = {}) => api.post(`/api/modules/rag-memory/feedback/tasks/${encodeURIComponent(taskId)}`, null, body),
    approveRagCase: (caseId, body = {}) => api.post(`/api/modules/rag-memory/cases/${encodeURIComponent(caseId)}/approve`, null, body),
    rejectRagCase: (caseId, body = {}) => api.post(`/api/modules/rag-memory/cases/${encodeURIComponent(caseId)}/reject`, null, body),
    v3Summary: () => request("/api/data/v3-summary"),
    v3Alerts: () => request("/api/data/alerts?active_only=true"),
    reportTemplates: () => request("/api/data/templates"),
    dataSourceConnections: () => optionalRequest("/api/data/source-connections", { sources: [], degraded: true, rule: "辅助数据源接口未接通，不阻塞数据主页面。" }, { timeoutMs: 1800 }),
    metricFactsSummary: () => optionalRequest("/api/data/metric-facts/summary", { items: [] }, { timeoutMs: 3000 }),
    dataGapSummary: () => optionalRequest("/api/data/data-gaps/summary", { items: [] }, { timeoutMs: 3000 }),
    importDiagnostics: (dataVersion = "") => optionalRequest(`/api/data/import-diagnostics${dataVersion ? `?dataVersion=${encodeURIComponent(dataVersion)}` : ""}`, { ready: false }, { timeoutMs: 3000 }),
    previewReportRows: (datasetName, rows, fieldMapping = {}, sourceSystem = "manual") => api.post("/api/data/preview", null, { datasetName, rows, fieldMapping, sourceSystem }),
    confirmReportImport: async (datasetName, rows, fieldMapping = {}, sourceSystem = "manual") => rememberImportSync(await api.post("/api/data/import/confirm", null, { datasetName, rows, fieldMapping, sourceSystem, autoCreateTasks: false })),
    uploadReportFile: async (file, datasetName = "auto", sourceSystem = "manual_upload") => rememberImportSync(await uploadRequest("/api/data/upload/confirm", file, { dataset_name: datasetName, source_system: sourceSystem, auto_create_tasks: "false" })),
    previewUploadFile: async (file, datasetName = "auto", sourceSystem = "manual_upload") => uploadRequest("/api/data/upload/preview", file, { dataset_name: datasetName, source_system: sourceSystem }),
    importMockAlerts: async () => rememberImportSync(await api.post("/api/data/import/mock-alerts", null, {})),
    syncDataSource: async (sourceId) => rememberImportSync(await api.post(`/api/data/source-connections/${encodeURIComponent(sourceId)}/sync`, null, {})),
    importReportRows: async (datasetName, rows) => rememberImportSync(await api.post("/api/data/import/report", null, { datasetName, rows, autoCreateTasks: false })),
    dbStatus: () => optionalRequest("/api/system/db-status", { ready: false }, { timeoutMs: 2500 }),
    isolation: () => optionalRequest("/api/system/isolation", { ready: false }, { timeoutMs: 2500 }),
    resetRuntimeData: async (includeAuditLogs = true, scope = "demo") => { const result = await api.post(`/api/system/reset-runtime-data?confirm=true&scope=${encodeURIComponent(scope || "demo")}&include_audit_logs=${includeAuditLogs ? "true" : "false"}`, null, {}); clearClientRuntime(); window.dispatchEvent(new CustomEvent("v2092-demo-reset", { detail: result })); return result; },
    resetLegacyRuntimeOnce: () => api.post("/api/system/reset-legacy-runtime-once", null, {}),
    refreshAfterDataImport: async (result = {}) => {
      rememberImportSync(result); clearApiCaches();
      const dataVersion = dataVersionFromImport(result);
      const detail = { result, dataVersion, readModel: null, operatingUnit: null, pipeline: null, pipelineLive: null, taskState: null, dataLine: null, fastReturn: true, refreshMode: "v22_5_5_projection_refresh_after_upload" };
      window.dispatchEvent(new CustomEvent("v208-read-model-refresh", { detail }));
      (async () => {
        await sleep(1200);
        try { detail.readModel = await api.refreshReadModel(dataVersion); clearApiCaches(); window.dispatchEvent(new CustomEvent("v208-read-model-refresh", { detail })); }
        catch (error) { detail.readModelError = error?.message || String(error || "读模型刷新失败"); window.dispatchEvent(new CustomEvent("v208-read-model-refresh", { detail })); }
        await sleep(900);
        const [pipelineLive, dataLine, taskState] = await Promise.allSettled([api.pipelineLive(dataVersion, 40), api.dataLineView(), api.refreshTaskState()]);
        detail.pipelineLive = pipelineLive.value || null; detail.dataLine = dataLine.value || null; detail.taskState = taskState.value || null; detail.fastReturn = false;
        window.dispatchEvent(new CustomEvent("v208-read-model-refresh", { detail }));
      })();
      return { ...detail, rule: "V22.5.5导入后失效旧缓存，写侧生成读模型；页面只读取当前dataVersion投影。" };
    },
    refreshTaskState: async () => { const taskView = await api.taskView({ limit: 80 }).catch(() => ({ items: [] })); const tasks = taskView?.items || taskView?.tasks || []; window.AppTaskStore?.hydrate?.(tasks, [], [], taskView); return taskView; },
    post: (path, _fallback = null, body = {}) => request(path, null, { method: "POST", body }),
  };

  window.AppApi = api;
})();
