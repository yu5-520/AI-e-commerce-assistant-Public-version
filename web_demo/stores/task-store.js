(function () {
  const TASK_KEY = "ai_ecommerce_v251_tasks";
  const LOG_KEY = "ai_ecommerce_v251_logs";
  const EVENT_KEY = "ai_ecommerce_v251_task_events";
  const COUNTER_KEY = "ai_ecommerce_v251_task_counters";
  const doneStatus = new Set(["已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"]);
  const priorityRank = { 高: 1, 中: 2, 低: 3 };
  const storeNameToId = { 家居生活主店: "S001", 家居百货店: "S002", 家居好物号: "S003", 淘宝: "S001", 拼多多: "S002", 抖音小店: "S003" };
  const financeDomains = new Set(["报表", "价格", "流量", "库存", "利润", "财务"]);

  function read(key, fallback) { try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; } catch { return fallback; } }
  function write(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
  function currentUser() { return window.AppApi?.currentUser?.() || null; }
  function storeIdsFromTask(task = {}) { if (Array.isArray(task.storeIds) && task.storeIds.length) return task.storeIds; if (Array.isArray(task.visibleStoreIds) && task.visibleStoreIds.length) return task.visibleStoreIds; return [task.store, task.storeName, task.platform].map((item) => storeNameToId[item]).filter(Boolean); }
  function inferLayer(task = {}) { if (task.taskLayer) return task.taskLayer; const route = task.sourceRoute || ""; const source = `${task.source || ""} ${task.sourceModule || ""}`; if (/老板|复盘|审计|周报|月报|日报/.test(source)) return "manager_dispatch"; if (route === "data-check" || financeDomains.has(task.riskDomain)) return "finance_check"; return "operator_execution"; }
  function buildDedupeKey(task = {}) { if (typeof task === "string") return task; const stores = storeIdsFromTask(task).join("+") || "global"; return task.dedupeKey || task.suggestedTaskKey || [stores, task.entityType, task.entityId || task.productId || task.id, task.riskDomain, task.actionType].filter(Boolean).join(":"); }
  function defaultVisibleRoles(task = {}) { const layer = inferLayer(task); if (layer === "manager_dispatch") return ["manager"]; if (layer === "finance_check") return ["manager", "finance"]; if (["owner_decision", "review_audit", "cycle_draft"].includes(layer)) return ["owner", "manager"]; const roles = ["manager", "operator"]; if (financeDomains.has(task.riskDomain)) roles.push("finance"); return roles; }
  function normalize(task = {}) {
    const priority = task.priority || "中";
    const storeIds = storeIdsFromTask(task);
    const layer = inferLayer(task);
    const item = {
      id: task.id || `A${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`.toUpperCase(),
      status: task.status || (layer === "manager_dispatch" ? "待拆分" : "待接收"),
      workflowStatus: task.workflowStatus || (layer === "manager_dispatch" ? "待拆分" : task.assigneeId ? "待接收" : "待派发"),
      priority,
      priorityLevel: task.priorityLevel || (priority === "高" ? "danger" : priority === "低" ? "good" : "warning"),
      deadline: task.deadline || "本周内",
      timeBucket: task.timeBucket || task.deadline || "本周内",
      source: task.source || task.sourceModule || "系统",
      sourceModule: task.sourceModule || task.source || "系统",
      sourceRoute: task.sourceRoute || "dashboard",
      productRoute: task.productRoute || task.sourceRoute || "business-products",
      todoRoute: task.todoRoute || "business-actions",
      logRoute: task.logRoute || "business-report",
      entityType: task.entityType || "任务",
      entityId: task.entityId || task.productId || task.id,
      riskDomain: task.riskDomain || "通用",
      actionType: task.actionType || "复查",
      taskLayer: layer,
      storeIds,
      visibleStoreIds: task.visibleStoreIds || storeIds,
      visibleRoleIds: task.visibleRoleIds || defaultVisibleRoles({ ...task, taskLayer: layer }),
      visibleUserIds: task.visibleUserIds || [task.assigneeId, task.reviewerId].filter(Boolean),
      ownerRole: task.ownerRole || (layer === "manager_dispatch" ? "manager" : "operator"),
      parentTaskId: task.parentTaskId || null,
      childTaskIds: task.childTaskIds || [],
      sourceType: task.sourceType || task.source || "系统预警",
      recapTarget: task.recapTarget || (layer === "operator_execution" ? "日报" : "周报"),
      agentJudgment: task.agentJudgment || { status: "placeholder", summary: "后续 Agent 在任务详情页补充判断。" },
      judgmentTags: task.judgmentTags || [],
      sourceTrail: task.sourceTrail || [],
      assigneeId: task.assigneeId || null,
      assigneeName: task.assigneeName || "未派发",
      reviewerId: task.reviewerId || null,
      reviewerName: task.reviewerName || "未设置复核人",
      assignedByName: task.assignedByName || "系统预警",
      assignmentNote: task.assignmentNote || "",
      submissionNote: task.submissionNote || "",
      reviewNote: task.reviewNote || "",
      lastEventType: task.lastEventType || null,
      lastEventMessage: task.lastEventMessage || "",
      lastEventAt: task.lastEventAt || null,
      createdAt: task.createdAt || new Date().toISOString(),
      updatedAt: task.updatedAt || task.createdAt || new Date().toISOString(),
      manualOrder: Number.isFinite(task.manualOrder) ? task.manualOrder : Date.now(),
      ...task,
    };
    item.title = item.title || item.productTitle || item.task || item.taskType || "经营任务";
    item.productTitle = item.productTitle || item.title;
    item.productShort = item.productShort || item.shortName || item.productId || "任务";
    item.dedupeKey = buildDedupeKey(item) || item.id;
    return item;
  }
  function sort(tasks) { return [...tasks].sort((a, b) => (priorityRank[a.priority] || 9) - (priorityRank[b.priority] || 9) || (a.manualOrder || 9999) - (b.manualOrder || 9999) || new Date(a.createdAt || 0) - new Date(b.createdAt || 0)); }
  function notify() { window.dispatchEvent(new CustomEvent("task-store-change")); }
  function getTasks() { return read(TASK_KEY, []).map(normalize); }
  function setTasks(tasks) { write(TASK_KEY, tasks.map(normalize)); }
  function getLogs() { return read(LOG_KEY, []); }
  function setLogs(logs) { write(LOG_KEY, logs || []); }
  function getEvents() { return read(EVENT_KEY, []); }
  function setEvents(events) { write(EVENT_KEY, Array.isArray(events) ? events : []); }
  function getCounters() { return read(COUNTER_KEY, {}); }
  function setCounters(counters) { write(COUNTER_KEY, counters || {}); }
  function userStoreOverlap(task, user) { return (task.visibleStoreIds || task.storeIds || []).some((id) => (user.storeIds || []).includes(id)); }
  function visibleToUser(task, user = currentUser()) {
    if (!user) return true;
    const roleId = user.roleId;
    const roleVisible = (task.visibleRoleIds || []).includes(roleId);
    const userVisible = (task.visibleUserIds || []).includes(user.id) || task.assigneeId === user.id || task.reviewerId === user.id;
    const storeVisible = userStoreOverlap(task, user);
    if (roleId === "owner") return ["owner_decision", "review_audit", "cycle_draft"].includes(task.taskLayer) || roleVisible;
    if (roleId === "manager") return roleVisible && ((user.storeGroupIds || []).includes(task.storeGroupId) || storeVisible || !task.storeGroupId);
    if (roleId === "operator") return userVisible || (task.taskLayer === "operator_execution" && storeVisible);
    if (roleId === "finance") return userVisible || roleVisible || financeDomains.has(task.riskDomain);
    if (roleId === "observer") return doneStatus.has(task.status) || roleVisible;
    return false;
  }
  function visibleEvent(event, user = currentUser()) {
    if (!user) return true;
    if (event.actorUserId === user.id) return true;
    if ((event.targetUserIds || []).includes(user.id)) return true;
    if ((event.targetRoleIds || []).includes(user.roleId)) return true;
    return false;
  }
  function projectForUser(task, user = currentUser()) {
    const item = normalize(task);
    if (!user || item.availableActions) return item;
    if (user.roleId === "manager") {
      item.availableActions = ["report", "assign", "pin", "move", "source"];
      if (["已提交", "待复核"].includes(item.status)) item.availableActions.push("review");
      if (["已完成", "已通过", "已归档"].includes(item.status)) item.availableActions.push("write_recap");
    } else if (user.roleId === "operator") {
      item.availableActions = ["report", "source"];
      if ((item.assigneeId === user.id || visibleToUser(item, user)) && ["待接收", "待确认", "已派发"].includes(item.status)) item.availableActions.push("accept");
      if ((item.assigneeId === user.id || visibleToUser(item, user)) && (["处理中", "已退回"].includes(item.status) || item.workflowStatus === "已退回")) item.availableActions.push("submit");
    } else if (user.roleId === "owner") item.availableActions = ["report", "source"];
    else item.availableActions = ["report", "source"];
    item.viewerRoleId = user.roleId;
    item.viewerRoleName = user.roleName;
    item.viewerInsightDepth = user.insightDepth;
    return item;
  }
  function visibleTasks(tasks = getTasks()) { const user = currentUser(); return tasks.map(normalize).filter((task) => visibleToUser(task, user)).map((task) => projectForUser(task, user)); }
  function visibleEvents(events = getEvents()) { const user = currentUser(); return events.filter((event) => visibleEvent(event, user)); }
  function localCounters(tasks = visibleTasks(getTasks()), events = visibleEvents(getEvents())) { return { visibleActive: tasks.filter((t) => !doneStatus.has(t.status)).length, waitingAccept: tasks.filter((t) => ["待接收", "待确认"].includes(t.status)).length, processing: tasks.filter((t) => t.status === "处理中").length, submitted: tasks.filter((t) => ["已提交", "待复核"].includes(t.status)).length, reviewing: tasks.filter((t) => t.status === "待复核").length, returned: tasks.filter((t) => t.workflowStatus === "已退回" || t.status === "已退回").length, waitingRecap: tasks.filter((t) => t.status === "已完成").length, recentEvents: events.length, latestEvent: events[0] || null }; }
  function hydrate(tasks = [], logs = [], events = [], counters = {}) { setTasks(Array.isArray(tasks) ? tasks : []); setLogs(Array.isArray(logs) ? logs : []); setEvents(Array.isArray(events) ? events : []); setCounters(counters || localCounters()); notify(); }
  function createEvent(task, eventType, message) { const event = { id: `E${Date.now().toString(36)}`, taskId: task.id, eventType, eventLabel: message, actorUserId: currentUser()?.id || "local", actorRole: currentUser()?.roleId || "local", actorName: currentUser()?.name || "本地账号", toStatus: task.status, toWorkflowStatus: task.workflowStatus, targetUserIds: [task.assigneeId, task.reviewerId].filter(Boolean), targetRoleIds: task.visibleRoleIds || [], message, createdAt: new Date().toISOString() }; const events = getEvents(); events.unshift(event); setEvents(events.slice(0, 300)); return event; }
  function createTask(input = {}) { const task = normalize(input); const tasks = getTasks(); const index = tasks.findIndex((item) => item.id === task.id || (item.dedupeKey && item.dedupeKey === task.dedupeKey && !doneStatus.has(item.status))); if (index >= 0) tasks[index] = normalize({ ...tasks[index], ...task }); else tasks.push(task); createEvent(task, "task_created", "本地任务已创建并按账号过滤。 "); setTasks(tasks); notify(); return task; }
  function updateTask(taskId, patch = {}, eventType = "task_updated", message = "本地任务状态已同步。") { const tasks = getTasks(); const index = tasks.findIndex((task) => task.id === taskId); if (index < 0) return null; const task = normalize({ ...tasks[index], ...patch, updatedAt: new Date().toISOString() }); tasks[index] = task; const event = createEvent(task, eventType, message); task.event = event; setTasks(tasks); setCounters(localCounters()); notify(); return task; }
  function acceptTask(taskId) { return updateTask(taskId, { status: "处理中", workflowStatus: "处理中", acceptedAt: new Date().toISOString() }, "operator_accepted", "运营已接收任务，总管视图同步为处理中。"); }
  function submitTask(taskId) { return updateTask(taskId, { status: "待复核", workflowStatus: "已提交", submittedAt: new Date().toISOString() }, "operator_submitted", "运营已提交处理结果，总管待复核 +1。"); }
  function reviewTask(taskId, decision = "approve") { return decision === "return" ? updateTask(taskId, { status: "已退回", workflowStatus: "已退回" }, "manager_returned", "总管已退回，运营视图同步为需补充。") : updateTask(taskId, { status: "已完成", workflowStatus: "已归档", completedAt: new Date().toISOString() }, "manager_approved", "总管复核通过，运营和总管同步归档。"); }
  function writeRecap(taskId) { return updateTask(taskId, { status: "已写入复盘", workflowStatus: "已写入复盘", recapWrittenAt: new Date().toISOString() }, "task_written_to_recap", "任务已写入复盘，老板复盘审计入口可见。 "); }
  function completeTask(taskId) { return updateTask(taskId, { status: "已完成", workflowStatus: "已归档", completedAt: new Date().toISOString() }, "task_completed", "任务已完成并归档。 "); }
  function pinTask(taskId) { const min = Math.min(...getTasks().map((task) => task.manualOrder || 9999), 0); return updateTask(taskId, { manualOrder: min - 1 }, "task_pinned", "任务已置顶，相关账号排序同步。 "); }
  function reorderTask(taskId, direction) { const active = sort(visibleTasks(getTasks()).filter((task) => !doneStatus.has(task.status))); const index = active.findIndex((task) => task.id === taskId); const targetIndex = direction === "up" ? index - 1 : index + 1; if (index < 0 || targetIndex < 0 || targetIndex >= active.length) return null; const current = active[index]; const target = active[targetIndex]; const tasks = getTasks(); const currentRef = tasks.find((task) => task.id === current.id); const targetRef = tasks.find((task) => task.id === target.id); if (!currentRef || !targetRef) return null; const currentOrder = currentRef.manualOrder || index + 1; currentRef.manualOrder = targetRef.manualOrder || targetIndex + 1; targetRef.manualOrder = currentOrder; setTasks(tasks); createEvent(currentRef, "task_reordered", "任务顺序已调整，相关账号排序同步。 "); notify(); return currentRef; }
  function resetDemoData() { hydrate([], [], [], {}); }
  window.AppTaskStore = { hydrate, listTasks: () => sort(visibleTasks(getTasks())), listActiveTasks: () => sort(visibleTasks(getTasks()).filter((task) => !doneStatus.has(task.status))), listDashboardTasks: () => sort(visibleTasks(getTasks()).filter((task) => !doneStatus.has(task.status))).slice(0, 5), listLogs: () => getLogs(), listEvents: () => visibleEvents(getEvents()), counters: () => getCounters().visibleActive !== undefined ? getCounters() : localCounters(), createTask, updateTask, acceptTask, submitTask, reviewTask, writeRecap, completeTask, pinTask, reorderTask, findOpenTask(input) { const key = buildDedupeKey(input || {}); return visibleTasks(getTasks()).find((task) => (task.id === input?.activeTaskId || task.dedupeKey === key) && !doneStatus.has(task.status)); }, buildDedupeKey, createLog(log) { const logs = getLogs(); logs.unshift(log); setLogs(logs.slice(0, 200)); notify(); return log; }, resetDemoData, subscribe(handler) { window.addEventListener("task-store-change", handler); return () => window.removeEventListener("task-store-change", handler); } };
  window.OPERATION_TASK_STORE = window.AppTaskStore;
})();