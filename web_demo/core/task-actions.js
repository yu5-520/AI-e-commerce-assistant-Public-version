(function () {
  const data = () => window.AppMockData || {};
  const store = () => window.AppTaskStore || {};

  function notifyTask(task, name) {
    return task?.dedupeHit ? `${name}已有相关待办，已合并到现有任务。` : `${name}已进入统一任务池。`;
  }

  async function syncAfter(serverTask) {
    if (serverTask?.task && store().upsert) store().upsert(serverTask.task);
    else if (serverTask?.id && store().upsert) store().upsert(serverTask);
    await window.AppApi?.refreshTaskState?.();
    return serverTask?.task || serverTask;
  }

  function identityFromItem(item = {}) {
    return {
      ...item,
      dedupeKey: item.suggestedTaskKey || item.dedupeKey,
      suggestedTaskKey: item.suggestedTaskKey || item.dedupeKey,
      activeTaskId: item.activeTaskId,
      id: item.id || item.objectId || item.productId || item.entityId || item.activeTaskId,
      productId: item.productId || item.id,
      entityId: item.entityId || item.productId || item.id,
      storeId: item.storeId,
      storeName: item.storeName || item.store,
      title: item.title || item.productTitle || item.shortName,
    };
  }

  function fallbackFindOpenTask(identity = {}) {
    const list = typeof store().listActiveTasks === "function" ? store().listActiveTasks() : [];
    const ids = new Set([identity.dedupeKey, identity.suggestedTaskKey, identity.activeTaskId, identity.id, identity.productId, identity.entityId, identity.storeId, identity.storeName, identity.title].map((v) => String(v || "").trim()).filter(Boolean));
    if (!ids.size) return null;
    return list.find((task) => [task.id, task.taskId, task.dedupeKey, task.suggestedTaskKey, task.productId, task.entityId, task.productShort, task.productTitle, task.title, task.store, task.storeName, ...(task.storeIds || []), ...(task.visibleStoreIds || [])].map((v) => String(v || "").trim()).some((v) => ids.has(v))) || null;
  }

  function findOpenTask(item = {}) {
    const identity = identityFromItem(item);
    if (identity.activeTaskId) return { id: identity.activeTaskId, activeTaskId: identity.activeTaskId };
    if (typeof store().findOpenTask === "function") return store().findOpenTask(identity);
    return fallbackFindOpenTask(identity);
  }

  function openTodoTask(taskOrId) {
    const taskId = typeof taskOrId === "string" ? taskOrId : taskOrId?.id || taskOrId?.activeTaskId;
    if (!taskId) return false;
    window.AppRouter?.navigate?.("business-actions", { focusTaskId: taskId });
    return true;
  }

  function openTaskReport(taskOrId) {
    const taskId = typeof taskOrId === "string" ? taskOrId : taskOrId?.id || taskOrId?.activeTaskId;
    if (!taskId) return false;
    window.AppRouter?.navigate?.("task-report", { taskId });
    return true;
  }

  function openCandidateReport(module, entityId) {
    if (!module || !entityId) return false;
    window.AppRouter?.navigate?.("task-report", { module, entityId });
    return true;
  }

  function openAlertReport(alertId) {
    if (!alertId) return false;
    window.AppRouter?.navigate?.("task-report", { alertId });
    return true;
  }

  async function createTaskFromReport(module, entityId) {
    const actions = { product: createProductTask, competitor: createCompetitorTask, listing: createListingTask, traffic: createTrafficTask, report: createReportTask };
    const action = actions[module];
    if (!action || !entityId) return null;
    return action(entityId);
  }

  function buttonLabel(item = {}) { return findOpenTask(item) ? "已在任务清单" : "加入任务清单"; }
  function buttonClass(item = {}) { return findOpenTask(item) ? "ghost" : ""; }
  function productIdentity(product) { return identityFromItem(product); }

  async function createProductTask(productId) {
    const product = (data().products || []).find((item) => item.id === productId) || { shortName: "该商品" };
    const task = await syncAfter(await window.AppApi?.createProductTask?.(productId));
    return { task, message: notifyTask(task, product.shortName || product.title || "该商品") };
  }

  async function createCompetitorTask(id) {
    const item = (data().competitors || []).find((row) => row.id === id) || { targetProduct: "该竞品" };
    const task = await syncAfter(await window.AppApi?.createCompetitorTask?.(id));
    return { task, message: notifyTask(task, item.targetProduct) };
  }

  async function createListingTask(id) {
    const item = (data().listings || []).find((row) => row.id === id) || { testType: "该上新测试" };
    const task = await syncAfter(await window.AppApi?.createListingTask?.(id));
    return { task, message: notifyTask(task, item.testType) };
  }

  async function createTrafficTask(id) {
    const item = (data().traffic || []).find((row) => row.id === id) || { channel: "该流量测试" };
    const task = await syncAfter(await window.AppApi?.createTrafficTask?.(id));
    return { task, message: notifyTask(task, item.channel) };
  }

  async function createReportTask(id) {
    const card = (data().reportGroups || []).flatMap((group) => group.reports || []).find((report) => report.id === id) || { name: "该报表" };
    const task = await syncAfter(await window.AppApi?.createReportTask?.(id));
    return { task, message: notifyTask(task, card.name) };
  }

  window.AppTaskActions = { ...(window.AppTaskActions || {}), createProductTask, createCompetitorTask, createListingTask, createTrafficTask, createReportTask, createTaskFromReport, productIdentity, identityFromItem, findOpenTask, openTodoTask, openTaskReport, openCandidateReport, openAlertReport, buttonLabel, buttonClass };
})();
