(function () {
  const s = (value) => window.AppShell?.escape?.(value ?? "") ?? String(value ?? "");
  let loadingTaskId = "";
  let lastRenderedTaskId = "";

  function routeState() {
    const state = window.AppRouter?.stateFromHash?.() || {};
    return {
      route: window.AppRouter?.routeFromHash?.() || "",
      taskId: state.taskId || state.task_id || state.id || "",
    };
  }

  function headers() {
    return {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Mock-User-Id": window.AppApi?.getCurrentUserId?.() || "U001",
    };
  }

  async function request(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
    if (!response.ok) {
      let detail = "";
      try { detail = (await response.json())?.detail || ""; } catch (error) {}
      throw new Error(detail || `${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  function detailFrom(payload = {}) {
    const task = payload.relatedTask || payload.task || payload;
    const report = payload.taskDetailReport || task.taskDetailReport || {};
    const plan = report.taskPlan || task.taskPlan || {};
    const contract = payload.activeActionContract || report.activeActionContract || plan.activeActionContract || task.activeActionContract || {};
    const agent2 = payload.agent2ActionPlan || report.agent2ActionPlan || task.agent2ActionPlan || {};
    const authorization = payload.authorizationDecision || payload.actionAuthorization || report.authorizationDecision || report.actionAuthorization || plan.authorizationDecision || plan.actionAuthorization || task.authorizationDecision || task.actionAuthorization || contract.activeAuthority || {};
    const family = contract.activeActionFamily || plan.selectedActionFamily || plan.actionFamily || agent2.actionFamily || task.actionFamily || payload.actionFamily || "";
    return { task, report, plan, authorization, family };
  }

  function money(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `¥${n.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "未提供";
  }

  function percent(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "未提供";
  }

  function readableNumber(value, fallback = "未配置") {
    if (value === undefined || value === null || value === "") return fallback;
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : String(value);
  }

  function label(decision) {
    return {
      auto_execute: "运营权限内",
      manager_approval_required: "超权限待总管审批",
      owner_approval_required: "重大边界待老板确认",
      authorization_data_missing: "权限参数不完整",
    }[decision] || "沿用现有权限";
  }

  function roasCards(params = {}, limits = {}) {
    const targetRoas = params.targetRoas ?? params.targetROAS;
    const minimumRoas = limits.minimumTargetRoas ?? limits.minimumTargetROAS ?? params.safetyROI;
    const changeRate = Number(params.roasChangeRate);
    const changingRoas = Number.isFinite(changeRate) && Math.abs(changeRate) > 0.0000001;
    const targetStrong = changingRoas
      ? readableNumber(targetRoas, percent(changeRate))
      : "本次不调整";
    const targetSmall = changingRoas
      ? `调整幅度 ${percent(changeRate)}`
      : "保持当前投产设置";
    const safetyStrong = readableNumber(minimumRoas, "未配置");
    const safetySmall = minimumRoas === undefined || minimumRoas === null || minimumRoas === ""
      ? "当前任务未设置ROAS安全线"
      : "低于此值触发止损";
    return [
      ["目标ROAS", targetStrong, targetSmall],
      ["最低安全线", safetyStrong, safetySmall],
    ];
  }

  function genericCards(params = {}, limits = {}) {
    return [
      ["ROAS变动", percent(params.roasChangeRate), `允许 ${percent(limits.roasChangeRateLimit)}`],
      ["安全线", params.targetRoas ?? "未提供", `最低允许 ${limits.minimumTargetRoas ?? "未提供"}`],
    ];
  }

  function authoritySection(taskId, authorization = {}, family = "") {
    const params = authorization.parameters || {};
    const limits = authorization.effectiveLimits || {};
    const decision = authorization.decision || "";
    const triggered = Array.isArray(authorization.triggeredReasons) ? authorization.triggeredReasons : [];
    const variableCards = ["roas_scale", "roas_guard"].includes(family)
      ? roasCards(params, limits)
      : genericCards(params, limits);
    return `<section class="page-section v21-authority-section" data-v21-authority-section>
      <div class="section-header"><h3>动作权限</h3><span class="status-badge">${s(label(decision))}</span></div>
      <div class="v21-authority-grid">
        <article><span>本次调整金额</span><strong>${s(money(params.adjustmentAmount))}</strong><small>当前 ${s(money(params.currentBudget))} → 目标 ${s(money(params.targetBudget))}</small></article>
        <article><span>运营单次额度</span><strong>${s(money(limits.singleAdjustmentLimit))}</strong><small>今日剩余 ${s(money(limits.remainingToday))}</small></article>
        ${variableCards.map(([title, strong, small]) => `<article><span>${s(title)}</span><strong>${s(strong)}</strong><small>${s(small)}</small></article>`).join("")}
      </div>
      <div class="v21-authority-reason"><strong>权限结论</strong><p>${s(authorization.reason || "当前任务没有返回权限计算说明。")}</p>${triggered.length ? `<small>触发项：${s(triggered.join("、"))}</small>` : ""}</div>
      <input type="hidden" value="${s(taskId)}" data-v21-authority-task-id />
    </section>`;
  }

  function managerButtons(taskId, authorization = {}) {
    const role = window.AppApi?.currentUser?.()?.roleId || "";
    if (!["owner", "manager"].includes(role)) return "";
    if (!["manager_approval_required", "owner_approval_required"].includes(authorization.decision)) return "";
    return `<div class="v21-authority-actions" data-v21-authority-actions>
      <button type="button" data-v21-authority-approve="${s(taskId)}">批准原方案</button>
      <button type="button" class="secondary" data-v21-authority-modify="${s(taskId)}">修改后批准</button>
      <button type="button" class="secondary" data-v21-authority-regenerate="${s(taskId)}">退回重生成</button>
      <button type="button" class="secondary" data-v21-authority-reject="${s(taskId)}">拒绝</button>
    </div>`;
  }

  function inject(taskId, payload) {
    const { authorization, family } = detailFrom(payload);
    const dock = document.querySelector(".task-action-dock");
    if (!dock) return false;
    document.querySelector("[data-v21-authority-section]")?.remove();
    dock.insertAdjacentHTML("beforebegin", authoritySection(taskId, authorization, family));
    const actions = dock.querySelector(".report-actions") || dock;
    actions.querySelector("[data-v21-authority-actions]")?.remove();
    const buttons = managerButtons(taskId, authorization);
    if (buttons) actions.insertAdjacentHTML("beforeend", buttons);
    lastRenderedTaskId = taskId;
    return true;
  }

  async function hydrate() {
    const { route, taskId } = routeState();
    if (route !== "task-report" || !taskId || loadingTaskId === taskId) return;
    const dock = document.querySelector(".task-action-dock");
    if (!dock) return;
    if (lastRenderedTaskId === taskId && document.querySelector("[data-v21-authority-section]")) return;
    loadingTaskId = taskId;
    try {
      const payload = await request(`/api/view/tasks/${encodeURIComponent(taskId)}`);
      inject(taskId, payload);
    } catch (error) {
      console.warn("[v21-authority-ui] detail unavailable", error);
    } finally {
      loadingTaskId = "";
    }
  }

  async function decide(taskId, body, node) {
    const oldText = node?.textContent || "处理";
    if (node) { node.disabled = true; node.textContent = "处理中"; }
    try {
      await request(`/api/action-authority/tasks/${encodeURIComponent(taskId)}/decide`, { method: "POST", body: JSON.stringify(body) });
      await window.AppApi?.refreshTaskState?.();
      lastRenderedTaskId = "";
      window.AppRouter?.schedule?.("v21-authority-decision", { taskId });
    } catch (error) {
      window.alert(`权限处理失败：${error.message || error}`);
      if (node) { node.disabled = false; node.textContent = oldText; }
    }
  }

  document.addEventListener("click", (event) => {
    const approve = event.target.closest("[data-v21-authority-approve]");
    if (approve) {
      decide(approve.dataset.v21AuthorityApprove, { decision: "approve_as_is", note: "主管批准原ROAS方案。" }, approve);
      return;
    }
    const modify = event.target.closest("[data-v21-authority-modify]");
    if (modify) {
      const amount = window.prompt("批准后的本次预算调整金额（元）", "5000");
      if (amount === null) return;
      const target = window.prompt("批准后的目标ROAS，可留空沿用原方案", "");
      decide(modify.dataset.v21AuthorityModify, {
        decision: "approve_modified",
        approvedAdjustmentAmount: Number(amount),
        approvedTargetROAS: target === "" ? null : Number(target),
        note: "主管修改额度后批准。",
      }, modify);
      return;
    }
    const regenerate = event.target.closest("[data-v21-authority-regenerate]");
    if (regenerate) {
      const note = window.prompt("填写退回Agent重新生成的原因", "调整金额超出当前授权范围");
      if (note === null) return;
      decide(regenerate.dataset.v21AuthorityRegenerate, { decision: "regenerate", note }, regenerate);
      return;
    }
    const reject = event.target.closest("[data-v21-authority-reject]");
    if (reject) {
      const note = window.prompt("填写拒绝原因", "当前不执行该ROAS调整");
      if (note === null) return;
      decide(reject.dataset.v21AuthorityReject, { decision: "reject", note }, reject);
    }
  });

  const observer = new MutationObserver(() => hydrate());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("hashchange", () => { lastRenderedTaskId = ""; hydrate(); });
  window.addEventListener("api-cache-updated", hydrate);
  window.setTimeout(hydrate, 300);
})();
