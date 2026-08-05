(function () {
  const s = (value) => AppShell.escape(value ?? "");
  const PRIORITY_WEIGHT = { 高: 0, 紧急: 0, 中: 1, 低: 2 };

  function arr(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
  function text(value) { return " ".concat(value ?? "").trim().replace(/\s+/g, " "); }
  function todayLabel() {
    try {
      return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date());
    } catch (error) {
      return "今日经营";
    }
  }
  function isEngineeringCopy(value) {
    const valueText = text(value);
    return /frontend_read_model|read_model|snapshot|Agent判断|快照重算|接口|projection|pipeline|runtime|worker/i.test(valueText);
  }
  function cleanBusinessCopy(value, fallback = "") {
    const valueText = text(value);
    return !valueText || isEngineeringCopy(valueText) ? fallback : valueText;
  }
  function statusBucket(task) {
    const status = text(task.status);
    if (/完成|归档|已写入/.test(status)) return "completed";
    if (/待复核|已提交|待审核|复核/.test(status)) return "review";
    if (/执行中|处理中|已接收|待提交/.test(status)) return "processing";
    if (/超时|逾期|阻塞|失败/.test(status) || task.overdue) return "overdue";
    return "pending";
  }
  function priorityLevel(task) {
    const priority = text(task.priority || task.priorityLevel);
    return /高|紧急|danger/.test(priority) ? "danger" : /低|success/.test(priority) ? "success" : "warning";
  }
  function normalizeTask(source, rank = 1) {
    const task = source || {};
    const card = task.taskCard || {};
    const detail = task.taskDetailReport || {};
    const identity = task.productIdentity || detail.productIdentity || card.productIdentity || {};
    const id = task.id || task.taskId || card.id || card.taskId || "";
    return {
      rank,
      id,
      title: cleanBusinessCopy(card.title || task.title || identity.productTitle || task.productTitle || task.productShort || task.productId || task.entityId, "经营任务"),
      subtitle: cleanBusinessCopy(card.subtitle || detail.warningSummary || task.subtitle || task.reason || task.riskDomain || task.taskType, "查看经营判断与执行方案"),
      status: cleanBusinessCopy(task.workflowStatus || task.status || card.status, "待处理"),
      deadline: cleanBusinessCopy(task.deadline || card.deadline || task.executionDeadline, "按任务时效执行"),
      priority: cleanBusinessCopy(task.priority || card.priority, "中"),
      priorityLevel: priorityLevel(task),
      overdue: Boolean(task.overdue),
      storeName: cleanBusinessCopy(identity.storeName || task.storeName || task.store, ""),
      platform: cleanBusinessCopy(identity.platform || task.platform, ""),
      assigneeName: cleanBusinessCopy(task.assigneeName || card.assigneeName, "运营"),
    };
  }
  function taskKey(task) { return task.id || `${task.title}::${task.subtitle}`; }
  function normalizeTasks(payload) {
    const workbench = payload?.todayWorkbench || {};
    const raw = arr(payload?.taskQueue).length
      ? arr(payload.taskQueue)
      : arr(workbench.allVisibleTasks).length
        ? arr(workbench.allVisibleTasks)
        : arr(workbench.todayPriorityTasks);
    const seen = new Set();
    return raw.map((item, index) => normalizeTask(item, index + 1)).filter((task) => {
      const key = taskKey(task);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    }).sort((a, b) => {
      const priority = (PRIORITY_WEIGHT[a.priority] ?? 1) - (PRIORITY_WEIGHT[b.priority] ?? 1);
      if (priority) return priority;
      return a.rank - b.rank;
    }).map((task, index) => ({ ...task, rank: index + 1 }));
  }
  function countsFromTasks(tasks) {
    const counts = { pending: 0, processing: 0, review: 0, overdue: 0, completed: 0, highRisk: 0 };
    tasks.forEach((task) => {
      const bucket = statusBucket(task);
      counts[bucket] = (counts[bucket] || 0) + 1;
      if (task.priorityLevel === "danger") counts.highRisk += 1;
    });
    return counts;
  }
  function countsFromPayload(payload, tasks) {
    const counts = countsFromTasks(tasks);
    const neural = payload?.neuralOperating?.signalCounts || {};
    const apply = (target, source) => {
      const value = Number(neural[source]);
      if (Number.isFinite(value)) counts[target] = value;
    };
    apply("pending", "actionReady");
    apply("processing", "executing");
    apply("review", "reviewPending");
    apply("overdue", "blocked");
    apply("completed", "learned");
    return counts;
  }
  function metricNumber(payload, label) {
    const item = arr(payload?.metrics).find((entry) => text(entry.label) === label);
    const number = Number(item?.value);
    return Number.isFinite(number) ? number : 0;
  }
  function localFallback() {
    const tasks = AppTaskStore.listActiveTasks().filter((task) => task.displayState !== "backend_only" && !["backend_tag", "store_product_tag", "observe_candidate"].includes(task.queueType));
    const user = AppApi.currentUser?.() || {};
    return {
      hasData: Boolean(tasks.length),
      taskQueue: tasks,
      latestImport: { label: "最新经营数据", status: "待同步", totalRows: 0, importedCount: 0, affectedModules: [] },
      metrics: [],
      operatorProfile: {
        displayName: user.displayName || user.name || "运营伙伴",
        positionTitle: user.positionTitle || user.roleName || "经营成员",
        tenureDays: 0,
        completedTaskCount: 0,
        level: 1,
        levelName: "经营入门",
        experience: 0,
        nextLevelExperience: 120,
        progressPercent: 0,
      },
      neuralOperating: { signalCounts: {}, routeNodes: [] },
      todayWorkbench: { todayPriorityTasks: tasks, allVisibleTasks: tasks, latestReportResult: null },
    };
  }
  function renderPulseGraphic() {
    return `<div class="operating-pulse" aria-hidden="true"><span></span><span></span><span></span><i></i></div>`;
  }
  function operatorProfile(payload) {
    const user = AppApi.currentUser?.() || {};
    const profile = payload?.operatorProfile || payload?.neuralOperating?.operatorProfile || {};
    return {
      displayName: cleanBusinessCopy(profile.displayName || user.displayName || user.name, "运营伙伴"),
      positionTitle: cleanBusinessCopy(profile.positionTitle || user.positionTitle || user.roleName, "经营成员"),
      tenureDays: Number(profile.tenureDays || 0),
      completedTaskCount: Number(profile.completedTaskCount || 0),
      level: Number(profile.level || 1),
      levelName: cleanBusinessCopy(profile.levelName, "经营入门"),
      experience: Number(profile.experience || 0),
      nextLevelExperience: Number(profile.nextLevelExperience || 120),
      experienceForNextLevel: Number(profile.experienceForNextLevel || 0),
      progressPercent: Math.max(0, Math.min(100, Number(profile.progressPercent || 0))),
    };
  }
  function renderExperience(profile) {
    return `<div class="dashboard-growth"><div class="dashboard-growth-head"><div><span>LV${s(profile.level)}</span><strong>${s(profile.levelName)}</strong></div><em>${s(profile.experience.toLocaleString("zh-CN"))} / ${s(profile.nextLevelExperience.toLocaleString("zh-CN"))}</em></div><div class="dashboard-growth-track"><i style="width:${s(profile.progressPercent)}%"></i></div><small>${profile.experienceForNextLevel > 0 ? `距离下一等级还需 ${profile.experienceForNextLevel.toLocaleString("zh-CN")} 经营经验` : "当前等级已完成"}</small></div>`;
  }
  function renderHero(payload) {
    const profile = operatorProfile(payload);
    return `<section class="dashboard-home-hero dashboard-profile-hero">${renderPulseGraphic()}<div class="dashboard-home-hero-copy"><span class="dashboard-home-date">${s(todayLabel())}</span><p>AI 运营中心</p><h2>欢迎回来，${s(profile.displayName)}</h2><div class="dashboard-profile-meta"><span>${s(profile.positionTitle)}</span><span>在职第 ${s(profile.tenureDays)} 天</span><span>累计完成 ${s(profile.completedTaskCount)} 项经营任务</span></div>${renderExperience(profile)}</div><div class="dashboard-neural-visual" aria-hidden="true"><span class="node sensed"></span><span class="node interpreted"></span><span class="node active"></span><span class="node learned"></span><i></i></div></section>`;
  }
  function renderStatusBand(counts) {
    const items = [["待执行", counts.pending], ["处理中", counts.processing], ["待复核", counts.review], ["已超时", counts.overdue]];
    return `<section class="dashboard-status-band">${items.map(([label, value]) => `<article><span>${s(label)}</span><strong>${s(value)}</strong></article>`).join("")}</section>`;
  }
  function renderTaskRow(task) {
    return `<article class="dashboard-execution-row"><div class="dashboard-execution-time"><strong>${String(task.rank).padStart(2, "0")}</strong><span></span><small>${s(task.deadline)}</small></div><div class="dashboard-execution-main"><div class="dashboard-execution-title"><strong>${s(task.title)}</strong><span class="dashboard-priority ${s(task.priorityLevel)}">${s(task.priority)}</span></div><p>${s(task.subtitle)}</p><div class="dashboard-execution-meta"><span>${s(task.assigneeName)}</span><span>${s(task.status)}</span>${task.storeName ? `<span>${s(task.storeName)}</span>` : ""}</div></div><button type="button" data-open-task="${s(task.id)}">查看详情</button></article>`;
  }
  function dataSyncText(report) {
    return cleanBusinessCopy(report?.summary, report?.status === "已同步" ? "最新经营数据已同步" : "等待最新经营数据");
  }
  function reminderItems(tasks, counts, report) {
    const coordination = tasks.filter((task) => /仓储|补货|协同|库存/.test(`${task.title}${task.subtitle}`)).length;
    return [
      { label: "经营风险", value: counts.highRisk ? `${counts.highRisk} 个任务需要优先关注` : "当前没有高风险执行任务", tone: counts.highRisk ? "danger" : "success" },
      { label: "协同等待", value: coordination ? `${coordination} 个任务涉及跨部门确认` : "当前没有等待中的部门协同", tone: coordination ? "warning" : "neutral" },
      { label: "复核队列", value: counts.review ? `${counts.review} 个结果等待复核` : "当前没有待复核结果", tone: counts.review ? "warning" : "neutral" },
      { label: "数据状态", value: dataSyncText(report), tone: "signal" },
    ];
  }
  function renderReminderPanel(tasks, counts, report) {
    const total = counts.pending + counts.processing + counts.review + counts.overdue + counts.completed;
    return `<section class="dashboard-insight-panel"><div class="dashboard-section-heading"><div><span>AI 经营提醒</span><h3>系统持续关注</h3></div><small>只保留需要注意的信息</small></div><div class="dashboard-reminder-list">${reminderItems(tasks, counts, report).map((item) => `<article><i class="${s(item.tone)}"></i><div><span>${s(item.label)}</span><strong>${s(item.value)}</strong></div></article>`).join("")}</div><div class="dashboard-progress-line"><div><span>累计闭环进度</span><strong>${s(counts.completed)} / ${s(total)}</strong></div><span><i style="width:${Math.round((counts.completed / Math.max(1, total)) * 100)}%"></i></span></div></section>`;
  }
  function renderQueue(tasks) {
    return `<section class="dashboard-execution-panel"><div class="dashboard-section-heading"><div><span>今日执行顺序</span><h3>按优先级推进</h3></div><button type="button" class="secondary" data-open-tasks>全部任务</button></div><div class="dashboard-execution-list">${tasks.length ? tasks.slice(0, 5).map(renderTaskRow).join("") : `<div class="dashboard-home-list-empty"><strong>当前没有待执行任务</strong><span>新的经营动作会在报表更新后进入这里。</span></div>`}</div></section>`;
  }
  function renderFootprint(payload, tasks, counts) {
    const stores = metricNumber(payload, "店铺");
    const products = metricNumber(payload, "商品");
    const neural = payload?.neuralOperating?.signalCounts || {};
    const activeSignals = Number(neural.actionReady || 0) + Number(neural.executing || 0) + Number(neural.reviewPending || 0) + Number(neural.blocked || 0);
    const items = [
      ["经营店铺", stores || "—"],
      ["有效商品", products || "—"],
      ["活跃信号", activeSignals],
      ["已沉淀", Number(neural.learned || counts.completed || 0)],
      ["链路状态", payload?.neuralOperating?.health?.status === "attention" ? "需要关注" : "正常"],
    ];
    return `<section class="dashboard-footprint"><div><span>经营神经概览</span><strong>数据、判断、动作与记忆正在同一条链路中流转</strong></div>${items.map(([label, value]) => `<article><span>${s(label)}</span><strong>${s(value)}</strong></article>`).join("")}</section>`;
  }
  function renderDashboard(payload) {
    const tasks = normalizeTasks(payload);
    const counts = countsFromPayload(payload, tasks);
    const report = payload?.todayWorkbench?.latestReportResult || payload?.latestImport || {};
    return `<div class="dashboard-home">${renderHero(payload)}${renderStatusBand(counts)}<section class="dashboard-home-grid">${renderQueue(tasks)}${renderReminderPanel(tasks, counts, report)}</section>${renderFootprint(payload, tasks, counts)}</div>`;
  }

  window.DashboardPage = {
    route: "dashboard",
    title: "总览",
    async render() {
      const payload = await AppApi.dashboard().catch(() => null) || localFallback();
      return renderDashboard(payload);
    },
    mount(ctx) {
      ctx.delegate("[data-open-task]", "click", (_, node) => node.dataset.openTask ? AppTaskActions.openTodoTask(node.dataset.openTask) : AppRouter.navigate("business-actions"));
      ctx.delegate("[data-open-tasks]", "click", () => AppRouter.navigate("business-actions"));
      ctx.addCleanup(AppTaskStore.subscribe(() => AppRouter.schedule("task-store")));
    },
  };
})();
