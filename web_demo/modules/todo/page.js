(function () {
  let notice = "";
  const s = (value) => AppShell.escape(value ?? "");

  function taskPlan(task) { return (task.taskDetailReport || {}).taskPlan || task.taskPlan || task.taskCard || {}; }
  function responsibility(task) { return task.taskResponsibility || taskPlan(task).taskResponsibility || "operator_growth"; }
  function responsibilityLabel(task) {
    const map = { operator_growth: "运营增长", system_watch: "系统观察" };
    return map[responsibility(task)] || "运营任务";
  }

  function visibleTaskQueue(tasks) {
    return tasks.filter((task) => !["backend_tag", "store_product_tag", "observe_candidate", "candidate_only", "report_seed_only", "merged_duplicate", "system_watch"].includes(task.queueType) && task.displayState !== "backend_only" && task.taskType !== "observation_task" && task.decision !== "system_watch");
  }

  function isIsoTime(value) { return /\d{4}-\d{2}-\d{2}T/.test(String(value || "")); }
  function parseDeadlineMinutes(text) {
    const value = String(text || "6小时内");
    if (isIsoTime(value)) return 360;
    const n = Number((value.match(/\d+(?:\.\d+)?/) || [6])[0]);
    if (value.includes("分钟")) return n;
    if (value.includes("小时")) return n * 60;
    if (value.includes("天")) return n * 1440;
    if (value.includes("周")) return n * 10080;
    if (value.includes("今日")) return 720;
    return 360;
  }
  function deadlineMinutes(task) { return Number(task.deadlineMinutes || task.taskCard?.deadlineMinutes || taskPlan(task).deadlineMinutes || parseDeadlineMinutes(task.executionDeadline || task.deadline || task.timeBucket)); }
  function dueAtMs(task) {
    const raw = task.deadlineAt || task.dueAt || task.taskCard?.deadlineAt;
    if (raw) {
      const t = Date.parse(raw);
      if (Number.isFinite(t)) return t;
    }
    const created = Date.parse(task.createdAt || task.updatedAt || "");
    const base = Number.isFinite(created) ? created : Date.now();
    return base + deadlineMinutes(task) * 60 * 1000;
  }
  function remainingMinutes(task) { return Math.ceil((dueAtMs(task) - Date.now()) / 60000); }
  function durationText(minutes) {
    const abs = Math.max(0, Math.abs(minutes));
    if (abs < 60) return `${abs}分钟`;
    if (abs < 1440) {
      const hours = Math.floor(abs / 60);
      const mins = abs % 60;
      return `${hours}小时${mins ? `${mins}分` : ""}`;
    }
    const days = Math.floor(abs / 1440);
    const hours = Math.floor((abs % 1440) / 60);
    return `${days}天${hours ? `${hours}小时` : ""}`;
  }
  function countdownParts(task) {
    const minutes = remainingMinutes(task);
    return { state: minutes < 0 ? "已超时" : "剩余时间", duration: durationText(minutes), overdue: minutes < 0 };
  }
  function dueDateText(task) {
    const value = dueAtMs(task);
    if (!Number.isFinite(value)) return "";
    const date = new Date(value);
    const month = date.getMonth() + 1;
    const day = date.getDate();
    const hour = String(date.getHours()).padStart(2, "0");
    const minute = String(date.getMinutes()).padStart(2, "0");
    return `截止 ${month}月${day}日 ${hour}:${minute}`;
  }
  function priorityRank(task) { return { 高: 1, 中: 2, 低: 3 }[task.priority] || 9; }
  function riskRank(task) { return { high: 1, danger: 1, medium: 2, warning: 2, low: 3, good: 3 }[task.riskLevel || task.priorityLevel] || 9; }
  function sortTasks(tasks) { return [...tasks].sort((a, b) => remainingMinutes(a) - remainingMinutes(b) || deadlineMinutes(a) - deadlineMinutes(b) || priorityRank(a) - priorityRank(b) || riskRank(a) - riskRank(b) || String(a.createdAt || "").localeCompare(String(b.createdAt || ""))); }

  function reasonFamily(task) {
    const plan = taskPlan(task);
    if (plan.actionType === "traffic_gap_growth_test") return "流量缺口";
    const text = [task.riskDomain, task.reason, task.task, task.actionType, (task.taskDetailReport || {}).warningSummary].join(" ");
    if (text.includes("库存") || text.includes("补货") || text.includes("可售")) return "库存信号";
    if (text.includes("点击") || text.includes("素材") || text.includes("主图")) return "点击素材";
    if (text.includes("转化") || text.includes("详情") || text.includes("评价") || text.includes("客服")) return "转化承接";
    if (text.includes("广告") || text.includes("预算") || text.includes("投放") || text.includes("人群") || text.includes("关键词") || text.includes("ROAS")) return "投放效率";
    if (text.includes("退款") || text.includes("售后")) return "售后退款";
    return task.riskDomain || "经营判断";
  }
  function lifecycleLabel(task) {
    const lifecycle = task.taskLifecycle || {};
    return lifecycle.stageLabel || lifecycle.stage || (task.taskLayer === "manager_dispatch" ? "待拆分派发" : task.taskLayer === "manager_approval" ? "待主管审批" : "待运营处理");
  }
  function lifecycleNext(task) {
    const value = (task.taskLifecycle || {}).nextExpected || task.displayStatus || task.workflowStatus || task.status || "";
    return /查看详情|详情/.test(String(value)) ? "" : String(value);
  }
  function actionDecision(task) {
    const gate = task.actionAuthorization || task.v1282ActionGate || task.v127ActionGate || task.v126ActionGate || {};
    const map = { auto_execute: "运营执行", manager_approval_required: "主管审批", owner_approval_required: "老板确认" };
    return map[gate.decision] || (task.taskLayer === "manager_approval" || task.taskLayer === "manager_dispatch" ? "主管处理" : "运营执行");
  }
  function isManagerTask(task) { return ["manager_approval", "manager_dispatch"].includes(task.taskLayer) || ["主管审批", "主管处理", "老板确认"].includes(actionDecision(task)); }
  function publicActionLabel(task) { return reasonFamily(task); }

  function metrics(tasks) {
    const managerTasks = tasks.filter(isManagerTask);
    const operatorTasks = tasks.filter((task) => !isManagerTask(task));
    const overdue = tasks.filter((task) => remainingMinutes(task) < 0);
    const within6 = tasks.filter((task) => remainingMinutes(task) >= 0 && remainingMinutes(task) <= 360);
    const within12 = tasks.filter((task) => remainingMinutes(task) > 360 && remainingMinutes(task) <= 720);
    return [
      ["本轮任务", tasks.length, "当前队列"],
      [managerTasks.length ? "待主管处理" : "待运营执行", managerTasks.length || operatorTasks.length, managerTasks.length ? "审批与派发" : "执行处理"],
      ["6小时内", within6.length, "优先窗口"],
      ["6–12小时", within12.length, "当日安排"],
      ["已超时", overdue.length, "立即处理"],
    ];
  }

  function openTaskReport(taskId) { AppRouter.navigate("task-report", { taskId }); }
  function openTaskSubmit(taskId) { AppRouter.navigate("task-submit", { taskId }); }
  function primaryAction(task) {
    const visible = Array.isArray(task.visibleTaskActions) ? task.visibleTaskActions : [];
    const primary = task.primaryTaskAction || visible.find((item) => item?.primary) || visible[0] || null;
    if (!primary) return null;
    const actionName = String(primary.action || "");
    const roleId = AppApi.currentUser?.()?.roleId || "operator";
    if (roleId === "operator" && (actionName.includes("review") || actionName.includes("recap") || ["approve", "reject"].includes(actionName))) return null;
    return primary;
  }
  function actionButton(task) {
    const action = primaryAction(task);
    if (!action) return "";
    const id = s(task.id);
    if (action.action === "accept") return `<button type="button" class="primary" data-accept="${id}">接收任务</button>`;
    if (action.action === "submit" || action.action === "supplement") return `<button type="button" class="primary" data-submit-page="${id}">提交任务</button>`;
    if (action.action === "approve" || action.action === "reject" || action.action === "review") return `<button type="button" class="primary" data-task-report="${id}">复核处理</button>`;
    if (action.action === "confirm") return `<button type="button" class="primary" data-task-report="${id}">确认任务</button>`;
    return "";
  }
  function actionButtons(task) {
    const id = s(task.id);
    const primary = actionButton(task);
    if (!primary) return `<button type="button" class="primary todo-detail-primary" data-task-report="${id}">${isManagerTask(task) ? "查看并处理" : "查看详情"}</button>`;
    return `${primary}<button type="button" class="secondary" data-task-report="${id}">详情</button>`;
  }

  function executionBrief(task) {
    const plan = taskPlan(task);
    const report = task.taskDetailReport || {};
    const sop = task.operatorExecutionSop || report.operatorExecutionSop || plan.operatorExecutionSop || task.sopSteps || [];
    if (Array.isArray(sop) && sop.length) return String(sop[0]);
    const direction = task.operatorJudgmentView?.selectedDirection || report.operatorJudgmentView?.selectedDirection || plan.operatorJudgmentView?.selectedDirection;
    if (direction) return String(direction);
    const reason = task.reason || plan.reason || report.reason;
    if (reason) return String(reason);
    return `${publicActionLabel(task)}任务，进入详情查看执行参数与处理步骤。`;
  }

  function statusSummary(task) {
    const stage = lifecycleLabel(task);
    const next = lifecycleNext(task);
    if (!next || next === stage || next === "待处理") return stage;
    return `${stage} · ${next}`;
  }

  function row(task, index, focusTaskId = "") {
    const focused = focusTaskId && task.id === focusTaskId;
    const batch = task.batchTask ? `${s(task.affectedProductCount)}个商品` : (task.affectedProductCount ? `${s(task.affectedProductCount)}个商品` : "");
    const product = task.productIdentity || (task.productActionCards || [])[0] || {};
    const title = task.title || task.productTitle || product.productTitle || "经营任务";
    const productLine = product.productTitle || task.productTitle || task.productShort || "任务商品";
    const countdown = countdownParts(task);
    const classes = [focused ? "focused-task" : "", countdown.overdue ? "is-overdue" : "", isManagerTask(task) ? "is-manager-task" : "is-operator-task"].filter(Boolean).join(" ");
    return `<article class="todo-queue-row todo-queue-row-v2020 ${classes}" data-task-card="${s(task.id)}">
      <div class="todo-time-rail countdown-time-rail">
        <span class="todo-sequence">${String(index + 1).padStart(2, "0")}</span>
        <strong>${s(countdown.state)}</strong>
        <b>${s(countdown.duration)}</b>
        <small>${s(dueDateText(task))}</small>
      </div>
      <div class="todo-queue-main">
        <div class="todo-title-line"><strong>${s(title)}</strong>${batch ? `<em>${batch}</em>` : ""}</div>
        <p class="todo-task-brief">${s(executionBrief(task))}</p>
        <span class="todo-product-meta">${s(task.store || task.storeName || "任务池")} · ${s(task.platform || "经营单元")} · ${s(productLine)}</span>
        <div class="todo-compact-tags"><em class="priority-${s(task.priority || "中")}">${s(task.priority || "中")}</em><em>${s(responsibilityLabel(task))}</em><em>${s(publicActionLabel(task))}</em></div>
      </div>
      <div class="todo-queue-side">
        <div class="todo-queue-status"><strong>${s(actionDecision(task))}</strong><span>${s(statusSummary(task))}</span></div>
        <div class="todo-actions v106-minimal-actions">${actionButtons(task)}</div>
      </div>
    </article>`;
  }

  async function refresh(message) { await AppApi.refreshTaskState(); notice = message; AppRouter.schedule("todo-refresh"); }
  function applyTransitionResult(result) { const task = result?.task || result; if (task?.id) window.AppTaskStore?.upsert?.(task); window.dispatchEvent(new CustomEvent("v1211-task-transition", { detail: result })); return task; }
  function focusTask(taskId) { if (!taskId) return; requestAnimationFrame(() => { const card = document.querySelector(`[data-task-card="${CSS.escape(taskId)}"]`); if (!card) return; card.scrollIntoView({ behavior: "smooth", block: "center" }); card.classList.add("focused-task"); setTimeout(() => card.classList.remove("focused-task"), 1800); }); }

  window.TodoPage = {
    route: "business-actions",
    title: "待办",
    async render(ctx) {
      const focusTaskId = ctx?.state?.focusTaskId || "";
      let taskState = null;
      try { taskState = await AppApi.refreshTaskState(); } catch (error) { console.error("[todo] refresh task state failed", error); }
      const freshItems = Array.isArray(taskState?.items) ? taskState.items : [];
      const active = freshItems.length ? freshItems : AppTaskStore.listActiveTasks();
      const tasks = sortTasks(visibleTaskQueue(active));
      const user = AppApi.currentUser?.() || {};
      const apiError = taskState?.optionalError || AppApi.status?.lastError?.message || "";
      const empty = apiError ? `任务接口异常：${apiError}` : "当前账号没有需要立即处理的本轮经营任务。观察项进入后台等待下一份报表，不进入任务池。";
      const managerCount = tasks.filter(isManagerTask).length;
      const queueTitle = managerCount ? "主管处理队列" : "执行队列";
      return `<section class="todo-toolbar"><div><p class="eyebrow">TASK CENTER · V20.24</p><h2>任务处理</h2><p>当前以 ${s(user.roleName || "默认账号")} 查看任务。</p></div></section>${notice ? AppShell.notice("操作结果", notice) : ""}<section class="kpi-grid todo-metrics">${metrics(tasks).map(([x,y,z]) => AppShell.metricCard(x,y,z)).join("")}</section><section class="page-section todo-list-section"><div class="section-header"><h3>${queueTitle}</h3><span class="status-badge">${tasks.length} 个本轮任务</span></div><div class="todo-queue-list">${tasks.length ? tasks.map((task, index) => row(task, index, focusTaskId)).join("") : `<div class="todo-empty">${s(empty)}</div>`}</div></section>`;
    },
    mount(ctx) {
      focusTask(ctx.state?.focusTaskId);
      ctx.delegate("[data-task-report]", "click", (_, node) => openTaskReport(node.dataset.taskReport));
      ctx.delegate("[data-submit-page]", "click", (_, node) => openTaskSubmit(node.dataset.submitPage));
      ctx.delegate("[data-accept]", "click", async (_, node) => { const result = await AppApi.acceptTodo(node.dataset.accept, { note: "已接收运营增长任务" }); applyTransitionResult(result); await refresh("任务已接收，进入提交执行痕迹阶段。"); });
    },
  };
})();