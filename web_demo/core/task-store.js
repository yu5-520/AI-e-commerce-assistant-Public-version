(function () {
  let tasks = [];
  let logs = [];
  let events = [];
  let counterState = {};
  const listeners = new Set();

  function clone(value) {
    try { return JSON.parse(JSON.stringify(value)); }
    catch (_) { return value; }
  }

  function notify() {
    listeners.forEach((listener) => {
      try { listener(); }
      catch (error) { console.error("[task-store] listener error", error); }
    });
  }

  function normalizeTask(item = {}) {
    const id = item.id || item.taskId || item.task_id || item.poolEntryId || item.taskSnapshotId || "";
    const card = item.taskCard || {};
    return {
      ...item,
      id,
      taskId: item.taskId || id,
      title: item.title || card.title || item.productTitle || "经营任务",
      deadline: item.deadline || card.deadline || item.timeBucket || "今日内",
      status: item.status || item.workflowStatus || "待处理",
      workflowStatus: item.workflowStatus || item.status || "待处理",
    };
  }

  function normalizeLog(item) {
    return {
      id: item.id || item.eventId || `LOG-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      time: item.time || item.createdAt || item.at || "--:--",
      type: item.type || item.eventLabel || item.eventType || "任务记录",
      title: item.title || item.taskTitle || item.message || "任务记录",
      productId: item.productId || item.entityId || item.taskId || "TASK",
      platform: item.platform || "经营单元",
      store: item.store || item.storeName || "任务池",
      source: item.source || item.sourceModule || item.actorName || "系统",
      status: item.status || item.toStatus || "已记录",
      action: item.action || item.eventLabel || item.eventType || "任务池动作",
      reason: item.reason || item.message || "统一任务池记录。",
      result: item.result || item.toStatus || "已写入日志。",
      route: item.route || item.sourceRoute || "dashboard",
      taskRoute: item.taskRoute || "business-actions",
      level: item.level || "info",
      imageLabel: item.imageLabel || "记",
    };
  }

  function hydrate(nextTasks = [], nextLogs = [], nextEvents = [], nextCounters = {}) {
    tasks = Array.isArray(nextTasks) ? clone(nextTasks).map(normalizeTask).filter((task) => task.id) : [];
    events = Array.isArray(nextEvents) ? clone(nextEvents) : [];
    const eventLogs = events.map(normalizeLog);
    const rawLogs = Array.isArray(nextLogs) ? nextLogs.map(normalizeLog) : [];
    logs = rawLogs.length ? rawLogs : eventLogs;
    counterState = nextCounters && typeof nextCounters === "object" ? clone(nextCounters) : {};
    notify();
    return snapshot();
  }

  function snapshot() {
    return { tasks: listTasks(), activeTasks: listActiveTasks(), logs: listLogs(), events: listEvents(), counters: counters() };
  }

  function listTasks() { return clone(tasks); }
  function listActiveTasks() {
    return tasks.filter((task) => !["已完成", "已归档", "已写入复盘"].includes(task.status) && !["已完成", "已归档"].includes(task.workflowStatus)).map(clone);
  }
  function listLogs() { return clone(logs); }
  function listEvents() { return clone(events); }
  function counters() { return clone(counterState); }

  function upsert(task) {
    const normalized = normalizeTask(task || {});
    if (!normalized.id) return null;
    const index = tasks.findIndex((item) => item.id === normalized.id);
    if (index >= 0) tasks[index] = { ...tasks[index], ...normalized };
    else tasks.unshift(normalized);
    notify();
    return clone(normalized);
  }

  function subscribe(listener) {
    if (typeof listener !== "function") return () => {};
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function normalizeId(value) { return String(value || "").trim(); }
  function candidateIds(entity = {}) {
    const ids = new Set();
    [entity.id, entity.productId, entity.entityId, entity.skuId, entity.storeId, entity.storeName, entity.store, entity.title].forEach((value) => {
      const text = normalizeId(value);
      if (text) ids.add(text);
    });
    return ids;
  }

  function taskMatches(task = {}, entity = {}) {
    const ids = candidateIds(entity);
    if (!ids.size) return false;
    const taskIds = [task.id, task.taskId, task.productId, task.entityId, task.entity_id, task.sourceEntityId, task.productShort, task.productTitle, task.title, task.store, task.storeName, ...(task.storeIds || []), ...(task.visibleStoreIds || [])].map(normalizeId).filter(Boolean);
    return taskIds.some((value) => ids.has(value));
  }

  function findOpenTask(entity) { return listActiveTasks().find((task) => taskMatches(task, entity)) || null; }

  function openTodoTask(taskId) { window.AppRouter?.navigate?.("business-actions", taskId ? { focusTaskId: taskId } : null); }
  function openTaskReport(taskId) { window.AppRouter?.navigate?.("task-report", taskId ? { taskId } : null); }
  function openCandidateReport(module, entityId) { if (!module || !entityId) return; window.AppRouter?.navigate?.("task-report", { module, entityId }); }
  async function createTaskFromReport(module, entityId) {
    if (!window.AppApi) return null;
    const map = { product: window.AppApi.createProductTask, competitor: window.AppApi.createCompetitorTask, listing: window.AppApi.createListingTask, traffic: window.AppApi.createTrafficTask, report: window.AppApi.createReportTask };
    const fn = map[module];
    if (!fn) return null;
    const result = await fn(entityId);
    if (result?.task) upsert(result.task);
    return result;
  }

  const storeApi = { hydrate, snapshot, listTasks, listActiveTasks, listLogs, listEvents, counters, upsert, subscribe, findOpenTask, taskMatches };
  window.AppTaskStore = storeApi;
  window.AppTaskActions = { ...(window.AppTaskActions || {}), findOpenTask, openTodoTask, openTaskReport, openCandidateReport, createTaskFromReport };
})();
