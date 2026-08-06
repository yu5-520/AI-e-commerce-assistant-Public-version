(function () {
  const API_VERSION = "23.2.10";
  const DETAIL_CACHE_PREFIX = "task-detail-snapshot-v23210:";
  const LEGACY_DETAIL_CACHE_PREFIX = "task-detail-snapshot-";
  const detailInflight = new Map();
  const detailMemoryCache = new Map();

  function safeText(value, limit = 320) {
    const text = String(value ?? "").replace(/[\r\n\t]+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  }

  function annotateTaskDetailError(error, detail = {}) {
    const out = error instanceof Error ? error : new Error(safeText(error) || "task_detail_client_error");
    const previous = out.frontendDiagnostic && typeof out.frontendDiagnostic === "object" ? out.frontendDiagnostic : {};
    const statusValue = detail.httpStatus ?? previous.httpStatus ?? out.httpStatus;
    const readyValue = detail.responseReady ?? previous.responseReady ?? out.responseReady;
    const diagnostic = {
      route: "task-report",
      taskId: safeText(detail.taskId ?? previous.taskId ?? out.taskId, 120),
      stage: safeText(detail.stage ?? previous.stage ?? out.stage ?? "task_detail_unknown", 80),
      requestPath: safeText(detail.requestPath ?? previous.requestPath ?? out.requestPath, 240),
      httpStatus: Number.isFinite(Number(statusValue)) ? Number(statusValue) : null,
      responseReady: typeof readyValue === "boolean" ? readyValue : null,
      errorName: safeText(out.name || "Error", 80),
      errorMessage: safeText(out.message || String(out), 320),
      timestamp: new Date().toISOString(),
    };
    out.frontendDiagnostic = diagnostic;
    out.taskId = diagnostic.taskId;
    out.stage = diagnostic.stage;
    out.requestPath = diagnostic.requestPath;
    out.httpStatus = diagnostic.httpStatus;
    out.responseReady = diagnostic.responseReady;
    return out;
  }

  function pruneLegacyDetailCaches() {
    try {
      Object.keys(sessionStorage)
        .filter((key) => key.startsWith(LEGACY_DETAIL_CACHE_PREFIX) && !key.startsWith(DETAIL_CACHE_PREFIX))
        .forEach((key) => sessionStorage.removeItem(key));
    } catch (error) {}
  }

  pruneLegacyDetailCaches();

  function currentUserId() { return "competition_operator"; }
  function isAbortError(error) { return error?.name === "AbortError" || /route_request_aborted|route_cleanup|route_replaced|external_abort|no_active_detail_consumer/.test(String(error?.message || error || "")); }
  function abortError(message = "request_aborted") { try { return new DOMException(message, "AbortError"); } catch (error) { const out = new Error(message); out.name = "AbortError"; return out; } }
  function markHealthy(path) { const status = window.AppApi?.status; if (status) { status.source = "server"; status.lastError = null; } window.dispatchEvent(new CustomEvent("api-client-status", { detail: { source: "server", path } })); }
  function markFailure(path, error) { if (isAbortError(error)) return; const status = window.AppApi?.status; const item = { path, message: error?.message || String(error || "接口异常"), at: Date.now() }; if (status) { status.source = "error"; status.lastError = item; if (Array.isArray(status.failures)) status.failures.push(item); } window.dispatchEvent(new CustomEvent("api-client-error", { detail: item })); }
  async function parseError(response) { try { const payload = await response.json(); return payload?.detail || payload?.message || `${response.status} ${response.statusText}`; } catch (error) { return `${response.status} ${response.statusText}`; } }

  async function request(path, options = {}) {
    const timeoutMs = Number(options.timeoutMs || 4500);
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const externalSignal = options.signal || null;
    const detailRequest = String(path || "").startsWith("/api/view/tasks/");
    let timedOut = false;
    const forwardAbort = () => { if (controller && !controller.signal.aborted) controller.abort(externalSignal?.reason || "external_abort"); };
    if (externalSignal?.aborted) forwardAbort();
    else externalSignal?.addEventListener?.("abort", forwardAbort, { once: true });
    const timer = controller ? setTimeout(() => { timedOut = true; controller.abort("request_timeout"); }, timeoutMs) : null;
    try {
      const response = await fetch(path, {
        method: options.method || "GET",
        signal: controller?.signal || externalSignal || undefined,
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: options.body ? JSON.stringify(options.body) : undefined,
        cache: detailRequest ? "no-store" : "default",
      });
      if (!response.ok) {
        const httpError = new Error(await parseError(response));
        httpError.name = "HttpError";
        throw detailRequest ? annotateTaskDetailError(httpError, { stage: "task_detail_http", requestPath: path, httpStatus: response.status }) : httpError;
      }
      let payload;
      try {
        payload = await response.json();
      } catch (error) {
        throw detailRequest ? annotateTaskDetailError(error, { stage: "task_detail_json", requestPath: path, httpStatus: response.status }) : error;
      }
      markHealthy(path);
      return payload;
    } catch (error) {
      if (timedOut) {
        const timeoutError = new Error(`任务详情读取超时：${path}`);
        timeoutError.name = "TimeoutError";
        const annotated = detailRequest ? annotateTaskDetailError(timeoutError, { stage: "task_detail_timeout", requestPath: path }) : timeoutError;
        markFailure(path, annotated);
        throw annotated;
      }
      const annotated = detailRequest ? annotateTaskDetailError(error, { stage: error?.frontendDiagnostic?.stage || "task_detail_fetch", requestPath: path }) : error;
      markFailure(path, annotated);
      throw annotated;
    } finally {
      if (timer) clearTimeout(timer);
      externalSignal?.removeEventListener?.("abort", forwardAbort);
    }
  }

  function taskIdOf(taskOrId) { if (typeof taskOrId === "string") return taskOrId; return taskOrId?.id || taskOrId?.taskId || taskOrId?.task_id || taskOrId?.activeTaskId || taskOrId?.taskPoolId || ""; }
  function obj(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function arr(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
  function first(...values) { return values.find((value) => value !== undefined && value !== null && value !== "" && !(Array.isArray(value) && !value.length) && !(typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length)); }
  function pick(source, keys) { const out = {}; const input = obj(source); keys.forEach((key) => { const value = input[key]; if (value !== undefined && value !== null && value !== "" && !(Array.isArray(value) && !value.length) && !(typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length)) out[key] = value; }); return out; }

  const ACTION_AND_EVIDENCE_FIELDS = [
    "taskMetricEvidenceProjection", "taskMetricEvidenceProjectionVersion", "taskEvidenceStatus", "taskEvidenceExecutable", "evidenceExecutionBlocked",
    "metricDigest", "activeActionContract", "singleActionContractVersion", "operationPlan", "agent2ExecutionProof",
    "authorizationDecision", "actionAuthorization", "authorizationVersion", "discardedCrossFamilyFields",
    "operatorExecutionSteps", "supportingCoordination", "structuredSopProjectionVersion",
  ];

  function compactTaskPlan(value) {
    const plan = obj(value);
    return pick(plan, [
      "title", "selectedDirection", "businessHypothesis", "reason", "testGoal", "creativeStrategy", "reviewCycle",
      "operatorJudgmentView", "selectedActionFamily", "actionFamily", "selectedActionFamilyLabel", "testFocus",
      "platformStyleProfile", "inferredVerticalCategory", "verticalCategory", "verticalCategoryProfile",
      "creativeTestPlan", "reviewMetrics", "actionParameterPack", "agent2ActionPlan", "operatorExecutionSop", "operatorActionSteps",
      "executionDeadline", "deadline", "deadlineMinutes", "taskResponsibility", "taskType", "evidenceRequirements",
      ...ACTION_AND_EVIDENCE_FIELDS,
    ]);
  }

  function compactReport(value) {
    const report = obj(value);
    const out = pick(report, [
      "title", "reason", "productIdentity", "productActionCards", "affectedProducts", "systemChangePack",
      "dynamicMetricChanges", "agentOperatingJudgment", "agentJudgment", "operatorJudgmentView", "agent2ActionPlan",
      "creativeTestPlan", "actionParameterPack", "taskCard", "operatorExecutionSop", "operatorSopSteps", "sopSteps",
      "autoReviewPlan", "autoRecapPlan", "reviewMetrics", "taskLifecycle", "evidencePack",
      ...ACTION_AND_EVIDENCE_FIELDS,
    ]);
    const plan = compactTaskPlan(report.taskPlan);
    if (Object.keys(plan).length) out.taskPlan = plan;
    return out;
  }

  function compactRelatedTask(value, id, report, operatorExecutionSop, autoReviewPlan) {
    const related = obj(value);
    const out = pick(related, [
      "title", "taskTitle", "status", "workflowStatus", "displayStatus", "productId", "productTitle", "productIdentity",
      "productActionCards", "affectedProducts", "storeId", "storeName", "store", "platform", "actionType", "taskType",
      "actionFamily", "selectedActionFamily", "riskDomain", "priority", "riskLevel", "taskCard", "taskLifecycle",
      "chainIntegrity", "visibleTaskActions", "availableActions", "primaryTaskAction", "evidence", "evidencePack",
      "completionGate", "evidenceRequirements", "dataVersion", "executionDeadline", "deadline", "deadlineMinutes",
      ...ACTION_AND_EVIDENCE_FIELDS,
    ]);
    const plan = compactTaskPlan(related.taskPlan || report.taskPlan);
    if (Object.keys(plan).length) out.taskPlan = plan;
    out.id = id;
    out.taskId = id;
    out.task_id = id;
    out.operatorExecutionSop = operatorExecutionSop;
    out.sopSteps = arr(related.sopSteps).length ? related.sopSteps : operatorExecutionSop;
    out.autoReviewPlan = autoReviewPlan;
    return normalizeTask(out);
  }

  function normalizeTask(task = {}) {
    const id = taskIdOf(task);
    if (!id) return task;
    const visible = Array.isArray(task.visibleTaskActions) ? task.visibleTaskActions : Array.isArray(task.availableActions) ? task.availableActions : [];
    const actions = visible.length ? visible : [{ action: "detail", label: "详情", primary: true }];
    return { ...task, id, taskId: id, task_id: id, visibleTaskActions: actions, availableActions: Array.isArray(task.availableActions) && task.availableActions.length ? task.availableActions : actions, primaryTaskAction: task.primaryTaskAction || actions.find((item) => item?.primary) || actions[0], frontendTaskReadModelVersion: API_VERSION };
  }

  function normalizeTaskDetail(payload = {}, taskId = "") {
    const item = obj(payload.item);
    const relatedSource = Object.keys(obj(item.relatedTask)).length ? obj(item.relatedTask) : Object.keys(obj(payload.relatedTask)).length ? obj(payload.relatedTask) : item;
    const id = taskIdOf(relatedSource) || item.taskId || item.id || payload.taskId || payload.id || taskId;
    const rawReport = Object.keys(obj(item.taskDetailReport)).length ? obj(item.taskDetailReport) : obj(payload.taskDetailReport);
    const report = compactReport(rawReport);
    const operatorExecutionSop = first(payload.operatorExecutionSop, report.operatorExecutionSop, relatedSource.operatorExecutionSop, item.operatorExecutionSop, item.sopSteps, relatedSource.sopSteps, []);
    const operatorSop = arr(operatorExecutionSop);
    const operatorExecutionSteps = first(payload.operatorExecutionSteps, item.operatorExecutionSteps, report.operatorExecutionSteps, relatedSource.operatorExecutionSteps, []);
    const supportingCoordination = first(payload.supportingCoordination, item.supportingCoordination, report.supportingCoordination, relatedSource.supportingCoordination, []);
    const autoReviewPlan = first(payload.autoReviewPlan, report.autoReviewPlan, relatedSource.autoReviewPlan, item.autoReviewPlan, {});
    const productIdentity = first(payload.productIdentity, item.productIdentity, report.productIdentity, relatedSource.productIdentity, {});
    const systemChangePack = first(payload.systemChangePack, item.systemChangePack, report.systemChangePack, relatedSource.systemChangePack, {});
    const dynamicMetricChanges = first(payload.dynamicMetricChanges, item.dynamicMetricChanges, report.dynamicMetricChanges, relatedSource.dynamicMetricChanges, []);
    const operatorJudgmentView = first(payload.operatorJudgmentView, item.operatorJudgmentView, report.operatorJudgmentView, relatedSource.operatorJudgmentView, {});
    const agentOperatingJudgment = first(payload.agentOperatingJudgment, item.agentOperatingJudgment, report.agentOperatingJudgment, relatedSource.agentOperatingJudgment, {});
    const agentJudgment = first(payload.agentJudgment, item.agentJudgment, report.agentJudgment, relatedSource.agentJudgment, {});
    const autoRecapPlan = first(payload.autoRecapPlan, report.autoRecapPlan, autoReviewPlan, {});
    const taskMetricEvidenceProjection = first(
      payload.taskMetricEvidenceProjection,
      item.taskMetricEvidenceProjection,
      report.taskMetricEvidenceProjection,
      obj(report.taskPlan).taskMetricEvidenceProjection,
      relatedSource.taskMetricEvidenceProjection,
      obj(relatedSource.taskPlan).taskMetricEvidenceProjection,
      {}
    );
    const activeActionContract = first(payload.activeActionContract, item.activeActionContract, report.activeActionContract, obj(report.taskPlan).activeActionContract, relatedSource.activeActionContract, {});
    const operationPlan = first(payload.operationPlan, item.operationPlan, report.operationPlan, obj(report.taskPlan).operationPlan, relatedSource.operationPlan, {});
    const authorizationDecision = first(payload.authorizationDecision, payload.actionAuthorization, item.authorizationDecision, report.authorizationDecision, relatedSource.authorizationDecision, {});
    const metricDigest = first(payload.metricDigest, item.metricDigest, report.metricDigest, obj(report.taskPlan).metricDigest, relatedSource.metricDigest, {});
    const agent2ExecutionProof = first(payload.agent2ExecutionProof, item.agent2ExecutionProof, report.agent2ExecutionProof, relatedSource.agent2ExecutionProof, {});

    const reportPlan = compactTaskPlan(report.taskPlan);
    if (Object.keys(obj(taskMetricEvidenceProjection)).length) reportPlan.taskMetricEvidenceProjection = taskMetricEvidenceProjection;
    if (Object.keys(obj(activeActionContract)).length) reportPlan.activeActionContract = activeActionContract;
    if (Object.keys(obj(operationPlan)).length) reportPlan.operationPlan = operationPlan;
    if (Object.keys(obj(authorizationDecision)).length) reportPlan.authorizationDecision = authorizationDecision;
    if (Object.keys(obj(metricDigest)).length) reportPlan.metricDigest = metricDigest;
    if (Object.keys(reportPlan).length) report.taskPlan = reportPlan;
    report.taskMetricEvidenceProjection = taskMetricEvidenceProjection;
    report.operatorExecutionSteps = arr(operatorExecutionSteps);
    report.supportingCoordination = arr(supportingCoordination);
    report.activeActionContract = activeActionContract;

    const relatedTask = compactRelatedTask(relatedSource, id, report, operatorSop, autoReviewPlan);
    relatedTask.taskMetricEvidenceProjection = taskMetricEvidenceProjection;
    relatedTask.operatorExecutionSteps = arr(operatorExecutionSteps);
    relatedTask.supportingCoordination = arr(supportingCoordination);
    relatedTask.activeActionContract = activeActionContract;
    if (Object.keys(obj(relatedTask.taskPlan)).length) {
      relatedTask.taskPlan.taskMetricEvidenceProjection = taskMetricEvidenceProjection;
      relatedTask.taskPlan.activeActionContract = activeActionContract;
    }

    return {
      version: payload.version || item.version || API_VERSION,
      runtimeVersion: payload.runtimeVersion,
      routeVersion: payload.routeVersion,
      taskReadModelVersion: payload.taskReadModelVersion,
      taskDetailSnapshotVersion: payload.taskDetailSnapshotVersion || payload.snapshotVersion,
      taskMetricEvidenceProjectionVersion: first(payload.taskMetricEvidenceProjectionVersion, item.taskMetricEvidenceProjectionVersion, taskMetricEvidenceProjection.version),
      v19ProductLogicContractVersion: payload.v19ProductLogicContractVersion,
      ready: payload.ready !== false,
      id,
      taskId: id,
      task_id: id,
      dataVersion: first(payload.dataVersion, item.dataVersion, payload.currentDataVersion, relatedTask.dataVersion),
      currentDataVersion: payload.currentDataVersion,
      title: first(payload.title, item.title, relatedTask.title, relatedTask.taskTitle, "任务详情"),
      taskStatus: first(payload.taskStatus, relatedTask.status, relatedTask.workflowStatus, item.status, "待接收"),
      relatedTask,
      taskDetailReport: { ...report, operatorExecutionSop: operatorSop, operatorExecutionSteps: arr(operatorExecutionSteps), supportingCoordination: arr(supportingCoordination), taskMetricEvidenceProjection, activeActionContract, autoReviewPlan, autoRecapPlan },
      productIdentity,
      systemChangePack,
      dynamicMetricChanges,
      taskMetricEvidenceProjection,
      taskEvidenceStatus: first(payload.taskEvidenceStatus, item.taskEvidenceStatus, taskMetricEvidenceProjection.evidenceStatus, "evidence_missing"),
      taskEvidenceExecutable: first(payload.taskEvidenceExecutable, item.taskEvidenceExecutable, taskMetricEvidenceProjection.taskExecutableFromEvidence, false),
      evidenceExecutionBlocked: first(payload.evidenceExecutionBlocked, item.evidenceExecutionBlocked, taskMetricEvidenceProjection.taskExecutableFromEvidence === false, true),
      operatorJudgmentView,
      agentOperatingJudgment,
      agentJudgment,
      agent2ActionPlan: first(payload.agent2ActionPlan, report.agent2ActionPlan, relatedSource.agent2ActionPlan, {}),
      operationPlan,
      agent2ExecutionProof,
      authorizationDecision,
      actionAuthorization: authorizationDecision,
      metricDigest,
      activeActionContract,
      operatorExecutionSop: operatorSop,
      operatorExecutionSteps: arr(operatorExecutionSteps),
      supportingCoordination: arr(supportingCoordination),
      operatorSopSteps: operatorSop,
      sopSteps: operatorSop,
      autoReviewPlan,
      autoRecapPlan,
      taskLifecycle: first(payload.taskLifecycle, item.taskLifecycle, relatedTask.taskLifecycle, {}),
      chainIntegrity: first(payload.chainIntegrity, item.chainIntegrity, relatedTask.chainIntegrity, {}),
      detailDisplayContract: payload.detailDisplayContract,
      snapshotHit: payload.snapshotHit,
      snapshotSource: payload.snapshotSource,
      snapshotUpdatedAt: payload.snapshotUpdatedAt,
      singleTaskDetailEndpoint: "/api/view/tasks/{task_id}",
      frontendTaskReadModelVersion: API_VERSION,
      clientProjection: "v21_7_8_frozen_task_metric_evidence",
    };
  }

  async function taskView(params = {}) {
    const query = new URLSearchParams();
    Object.entries({ limit: 80, ...params }).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== "") query.set(key, value); });
    const payload = await request(`/api/view/tasks${query.toString() ? `?${query.toString()}` : ""}`, { timeoutMs: 3500, signal: window.AppRouter?.currentSignal?.() || undefined });
    const items = (payload.items || payload.tasks || []).map(normalizeTask).filter((item) => item.id || item.taskId);
    return { ...payload, version: payload.version || API_VERSION, items, tasks: items, activeTasks: items, lightweight: payload.lightweight !== false };
  }

  async function refreshTaskState() {
    const payload = await taskView().catch((error) => isAbortError(error) ? ({ items: [], tasks: [], activeTasks: [], aborted: true }) : ({ items: [], tasks: [], activeTasks: [], optionalError: error?.message || "task_view_timeout" }));
    if (!payload.aborted) window.AppTaskStore?.hydrate?.(payload.items || [], [], [], payload);
    return payload;
  }

  function detailKey(taskId) { return `${currentUserId()}::${taskId}`; }
  function cacheStorageKey(taskId) { return `${DETAIL_CACHE_PREFIX}${detailKey(taskId)}`; }
  function clearTaskDetailCache(taskOrId) {
    const taskId = taskIdOf(taskOrId);
    if (!taskId) return "";
    const key = detailKey(taskId);
    const entry = detailInflight.get(key);
    if (entry && !entry.settled && !entry.controller?.signal?.aborted) entry.controller.abort("detail_cache_cleared");
    detailInflight.delete(key);
    detailMemoryCache.delete(key);
    try {
      Object.keys(sessionStorage)
        .filter((storageKey) => storageKey.startsWith(LEGACY_DETAIL_CACHE_PREFIX) && storageKey.endsWith(`::${taskId}`))
        .forEach((storageKey) => sessionStorage.removeItem(storageKey));
    } catch (error) {}
    return taskId;
  }
  function rememberDetail(taskId, payload) { const entry = { payload, at: Date.now() }; detailMemoryCache.set(detailKey(taskId), entry); try { sessionStorage.setItem(cacheStorageKey(taskId), JSON.stringify(entry)); } catch (error) {} return payload; }
  function detailCacheEntry(taskId, maxAgeMs = 86400000) { const key = detailKey(taskId); const memory = detailMemoryCache.get(key); if (memory && Date.now() - Number(memory.at || 0) <= maxAgeMs) return memory; try { const raw = sessionStorage.getItem(cacheStorageKey(taskId)); if (!raw) return null; const parsed = JSON.parse(raw); if (Date.now() - Number(parsed.at || 0) <= maxAgeMs) { detailMemoryCache.set(key, parsed); return parsed; } } catch (error) {} return null; }
  function detailFingerprint(payload) { try { return JSON.stringify(payload); } catch (error) { return String(payload); } }

  function consumeDetail(entry, key, signal) {
    entry.consumers += 1;
    return new Promise((resolve, reject) => {
      let finished = false;
      const finish = (fn, value) => {
        if (finished) return;
        finished = true;
        signal?.removeEventListener?.("abort", onAbort);
        fn(value);
      };
      const onAbort = () => finish(reject, abortError("route_request_aborted"));
      if (signal?.aborted) return onAbort();
      signal?.addEventListener?.("abort", onAbort, { once: true });
      entry.promise.then((value) => finish(resolve, value), (error) => finish(reject, error));
    }).finally(() => {
      entry.consumers = Math.max(0, entry.consumers - 1);
      if (entry.consumers === 0 && !entry.settled && !entry.background) {
        if (!entry.controller.signal.aborted) entry.controller.abort("no_active_detail_consumer");
        if (detailInflight.get(key) === entry) detailInflight.delete(key);
      }
    });
  }

  function startDetailFetch(taskId, options = {}, background = false) {
    const key = detailKey(taskId);
    let entry = detailInflight.get(key);
    if (entry) return entry;
    const controller = new AbortController();
    const previous = detailFingerprint(detailCacheEntry(taskId, Number.MAX_SAFE_INTEGER)?.payload || null);
    entry = { controller, consumers: 0, settled: false, background, promise: null };
    const requestPath = `/api/view/tasks/${encodeURIComponent(taskId)}`;
    entry.promise = request(requestPath, { timeoutMs: Number(options.timeoutMs || 5000), signal: controller.signal })
      .then((payload) => {
        if (payload?.ready === false) {
          throw annotateTaskDetailError(new Error(payload?.reason || "task_detail_snapshot_not_ready"), {
            taskId,
            stage: "task_detail_ready_check",
            requestPath,
            responseReady: false,
          });
        }
        rememberDetail(taskId, payload);
        if (background && detailFingerprint(payload) !== previous) window.dispatchEvent(new CustomEvent("api-cache-updated", { detail: { path: `/api/view/tasks/${taskId}`, taskId, payload, version: API_VERSION } }));
        return payload;
      })
      .finally(() => {
        entry.settled = true;
        if (detailInflight.get(key) === entry) detailInflight.delete(key);
      });
    detailInflight.set(key, entry);
    return entry;
  }

  function sharedTaskDetail(taskId, options = {}) {
    const cached = detailCacheEntry(taskId, Number(options.maxStaleAgeMs || 86400000));
    if (cached && !options.forceNetwork) {
      const age = Date.now() - Number(cached.at || 0);
      if (age > Number(options.freshForMs || 5000)) startDetailFetch(taskId, options, true);
      return Promise.resolve({ ...cached.payload, clientCacheState: age <= 5000 ? "fresh" : "stale", clientCacheAgeMs: age, refreshing: age > 5000 });
    }
    const entry = startDetailFetch(taskId, options, false);
    return consumeDetail(entry, detailKey(taskId), options.signal || window.AppRouter?.currentSignal?.() || null);
  }

  async function taskDetail(taskOrId, options = {}) {
    const taskId = taskIdOf(taskOrId);
    if (!taskId) throw annotateTaskDetailError(new Error("missing_task_id"), { stage: "task_detail_identity" });
    if (options.forceNetwork) clearTaskDetailCache(taskId);
    return sharedTaskDetail(taskId, options);
  }

  async function taskReport(taskOrId, options = {}) {
    const taskId = taskIdOf(taskOrId);
    const requestPath = taskId ? `/api/view/tasks/${encodeURIComponent(taskId)}` : "";
    let payload;
    try {
      payload = await taskDetail(taskId, options);
    } catch (error) {
      throw annotateTaskDetailError(error, { taskId, stage: error?.frontendDiagnostic?.stage || "task_detail_fetch", requestPath });
    }
    try {
      return normalizeTaskDetail(payload, taskId);
    } catch (error) {
      throw annotateTaskDetailError(error, {
        taskId,
        stage: "normalize_task_detail",
        requestPath,
        responseReady: payload?.ready !== false,
      });
    }
  }

  async function acceptTask(taskOrId, body = {}) { const taskId = taskIdOf(taskOrId); if (!taskId) throw new Error("missing_task_id"); detailInflight.delete(detailKey(taskId)); detailMemoryCache.delete(detailKey(taskId)); try { sessionStorage.removeItem(cacheStorageKey(taskId)); } catch (error) {} return request(`/api/task-lifecycle-stations/acceptance/${encodeURIComponent(taskId)}/accept`, { method: "POST", body, timeoutMs: 15000 }); }
  async function submitTask(taskOrId, body = {}) { const taskId = taskIdOf(taskOrId); if (!taskId) throw new Error("missing_task_id"); detailInflight.delete(detailKey(taskId)); detailMemoryCache.delete(detailKey(taskId)); try { sessionStorage.removeItem(cacheStorageKey(taskId)); } catch (error) {} return request(`/api/task-lifecycle-stations/submission/${encodeURIComponent(taskId)}/submit`, { method: "POST", body, timeoutMs: 15000 }); }
  async function pipelineDiagnostics(dataVersion = "") { const query = dataVersion ? `?dataVersion=${encodeURIComponent(dataVersion)}` : ""; return request(`/api/view/pipeline-diagnostics${query}`, { timeoutMs: 4000, signal: window.AppRouter?.currentSignal?.() || undefined }); }

  window.AppApi = { ...(window.AppApi || {}), taskView, refreshTaskState, taskReport, taskDetail, clearTaskDetailCache, annotateTaskDetailError, acceptTask, acceptTodo: acceptTask, submitTask, pipelineDiagnostics, taskIdOf, taskReadModelVersion: API_VERSION };
})();
