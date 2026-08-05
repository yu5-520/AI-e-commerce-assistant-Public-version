(function () {
  const VERSION = "21.7.7";
  const ROAS_FAMILIES = new Set(["roas_scale", "roas_guard"]);
  const FORBIDDEN_ROAS = /(?:配置|更换|修改|制作|新增|测试|替换|调整).{0,12}(?:标题|主图|创意|素材|定向|人群)|(?:A\s*\/?\s*B|AB)\s*测试|创意方案|标题测试|主图测试/i;
  let lastReport = null;

  function obj(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function arr(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
  function text(value) { return String(value ?? "").replace(/\s+/g, " ").trim(); }
  function escapeHtml(value) {
    return text(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }
  function first(values) {
    for (const value of values) if (value !== undefined && value !== null && text(value) !== "") return value;
    return "";
  }
  function number(value) {
    if (value && typeof value === "object") {
      for (const key of ["budget", "value", "currentBudget", "targetBudget", "dailyBudget"]) {
        const parsed = number(value[key]);
        if (Number.isFinite(parsed)) return parsed;
      }
      return NaN;
    }
    const parsed = Number(String(value ?? "").replace(/[¥,]/g, "").trim());
    return Number.isFinite(parsed) ? parsed : NaN;
  }
  function money(value) {
    const parsed = number(value);
    return Number.isFinite(parsed) ? `¥${parsed.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "";
  }

  function legacyObject(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) return { ...value };
    const source = text(value);
    if (!source.startsWith("{") || !source.endsWith("}")) return {};
    const result = {};
    const pair = /['"]([A-Za-z][A-Za-z0-9_]*)['"]\s*:\s*(?:'([^']*)'|"([^"]*)"|([^,}]+))/g;
    let match;
    while ((match = pair.exec(source))) {
      const raw = first([match[2], match[3], match[4]]);
      result[match[1]] = /^\d+(?:\.\d+)?$/.test(text(raw)) ? Number(raw) : text(raw);
    }
    return result;
  }

  function familyOf(report) {
    const detail = obj(report?.taskDetailReport);
    const related = obj(report?.relatedTask);
    const taskPlan = obj(detail.taskPlan || related.taskPlan || report?.taskPlan);
    const contract = obj(report?.activeActionContract || detail.activeActionContract || related.activeActionContract || taskPlan.activeActionContract);
    const plan = obj(report?.agent2ActionPlan || detail.agent2ActionPlan || related.agent2ActionPlan || taskPlan.agent2ActionPlan);
    return text(first([contract.activeActionFamily, taskPlan.selectedActionFamily, taskPlan.actionFamily, plan.actionFamily, related.actionFamily, report?.actionFamily]));
  }

  function planOf(report) {
    const detail = obj(report?.taskDetailReport);
    const related = obj(report?.relatedTask);
    const taskPlan = obj(detail.taskPlan || related.taskPlan || report?.taskPlan);
    return obj(report?.agent2ActionPlan || detail.agent2ActionPlan || related.agent2ActionPlan || taskPlan.agent2ActionPlan);
  }

  function contractOf(report) {
    const detail = obj(report?.taskDetailReport);
    const related = obj(report?.relatedTask);
    const taskPlan = obj(detail.taskPlan || related.taskPlan || report?.taskPlan);
    return obj(report?.activeActionContract || detail.activeActionContract || related.activeActionContract || taskPlan.activeActionContract);
  }

  function authorityOf(report) {
    const detail = obj(report?.taskDetailReport);
    const related = obj(report?.relatedTask);
    const taskPlan = obj(detail.taskPlan || related.taskPlan || report?.taskPlan);
    const contract = contractOf(report);
    return obj(report?.authorizationDecision || report?.actionAuthorization || detail.authorizationDecision || related.authorizationDecision || taskPlan.authorizationDecision || contract.activeAuthority);
  }

  function normalizeParameters(step) {
    const result = arr(step.parameters).map(text).filter(Boolean);
    const variable = text(first([step.testVariable, step.variable, step.changeVariable]));
    const locked = text(first([step.lockedVariable, step.controlVariable, step.fixedVariable]));
    if (variable) result.push(`本次变量：${variable}`);
    if (locked) result.push(`保持不变：${locked}`);
    return [...new Set(result)];
  }

  function normalizeStep(value, index) {
    const raw = legacyObject(value);
    if (!Object.keys(raw).length) {
      const plain = text(value);
      return { step: index + 1, title: plain, action: plain, parameters: [], successCondition: "", rollbackCondition: "", escalationCondition: "", source: "legacy_plain_text" };
    }
    const action = text(first([raw.action, raw.title, raw.summary, raw.text, raw.instruction]));
    return {
      step: Number(raw.step) || index + 1,
      title: text(first([raw.title, raw.action, raw.summary, raw.text, raw.instruction])),
      action,
      parameters: normalizeParameters(raw),
      successCondition: text(first([raw.successCondition, raw.completionCondition, raw.doneCondition])),
      rollbackCondition: text(first([raw.rollbackCondition, raw.stopLossCondition, raw.failureRollback])),
      escalationCondition: text(first([raw.escalationCondition, raw.manualEscalationCondition, raw.handoffCondition])),
      source: "agent2_structured_step",
    };
  }

  function semanticText(step) { return [step.title, step.action, ...arr(step.parameters)].map(text).join("；"); }
  function allowedStep(step, family) {
    if (!ROAS_FAMILIES.has(family)) return Boolean(text(step.title || step.action));
    const value = semanticText(step);
    if (!value) return false;
    if (value.includes("保持不变") && !/(?:配置|更换|修改|制作|新增|测试|替换|调整)/.test(value)) return true;
    return !FORBIDDEN_ROAS.test(value);
  }

  function budgetContext(report, plan, contract) {
    const authority = authorityOf(report);
    const params = obj(authority.parameters);
    const operationPlan = obj(contract.activeOperationPlan || plan.operationPlan || report?.operationPlan);
    const budgetPlan = obj(contract.activeFamilyPlan || plan.budgetPlan);
    const operation = arr(operationPlan.operations).find((item) => /budget|预算/i.test(text(item?.operationType || item?.type || item?.action))) || {};
    const current = first([number(operation.currentValue), number(operation.currentBudget), number(params.currentBudget), number(budgetPlan.currentBudget)]);
    const executed = first([number(operation.executedTargetValue), number(operation.authorizedTargetValue), number(operation.targetValue), number(params.targetBudget), number(budgetPlan.executedBudget), number(budgetPlan.authorizedBudget), number(budgetPlan.targetBudget)]);
    const recommendations = [number(operation.recommendedTargetValue), number(operation.recommendedBudget), number(budgetPlan.recommendedBudget), number(budgetPlan.recommendedBudgetUpperBound)].filter(Number.isFinite);
    return {
      current: Number.isFinite(Number(current)) ? Number(current) : NaN,
      executed: Number.isFinite(Number(executed)) ? Number(executed) : NaN,
      recommendations,
      rollbackCondition: text(first([operation.rollbackCondition, operation.stopLossCondition])),
    };
  }

  function replaceBudgetText(value, budget) {
    let result = text(value);
    if (!Number.isFinite(budget.executed)) return result;
    budget.recommendations.forEach((candidate) => {
      if (!Number.isFinite(candidate) || Math.abs(candidate - budget.executed) < 0.005) return;
      const variants = [candidate.toFixed(2), candidate.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }), String(candidate)];
      variants.sort((a, b) => b.length - a.length).forEach((variant) => { result = result.split(variant).join(budget.executed.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })); });
    });
    return result;
  }

  function reconcileStep(step, budget) {
    const result = { ...step };
    ["title", "action", "successCondition", "rollbackCondition", "escalationCondition"].forEach((key) => { result[key] = replaceBudgetText(result[key], budget); });
    let parameters = arr(result.parameters).map((value) => replaceBudgetText(value, budget)).filter(Boolean);
    if (/预算|budget/i.test(semanticText(result)) && Number.isFinite(budget.executed)) {
      parameters = parameters.filter((value) => !/(?:新计划|执行|目标).{0,8}预算/.test(value));
      parameters.unshift(`执行预算：${money(budget.executed)}`);
      if (Number.isFinite(budget.current) && Math.abs(budget.current - budget.executed) >= 0.005) parameters.push(`主计划预算保持：${money(budget.current)}`);
    }
    result.parameters = [...new Set(parameters)];
    return result;
  }

  function authorizedBudgetStep(budget) {
    if (!Number.isFinite(budget.executed)) return null;
    const parameters = [`执行预算：${money(budget.executed)}`];
    if (Number.isFinite(budget.current) && Math.abs(budget.current - budget.executed) >= 0.005) parameters.push(`主计划预算保持：${money(budget.current)}`);
    return {
      step: 0,
      title: "设置本轮授权预算",
      action: "按动作权限写入独立计划预算，不修改主计划预算。",
      parameters,
      successCondition: "后台计划预算与授权目标一致。",
      rollbackCondition: budget.rollbackCondition || "预算设置错误时恢复至执行前预算。",
      escalationCondition: "平台限制无法写入预算时提交主管处理。",
      source: "authorized_operation_projection",
    };
  }

  function stopStep(steps, budget) {
    return {
      step: 0,
      title: "设置停止与回滚条件",
      action: "在执行前确认本轮停止条件和恢复路径。",
      parameters: [],
      successCondition: "停止条件、回滚对象和负责人均已确认。",
      rollbackCondition: budget.rollbackCondition || text(first(steps.map((item) => item.rollbackCondition))) || "触发任务止损条件时恢复至执行前参数。",
      escalationCondition: text(first(steps.map((item) => item.escalationCondition))) || "无法回滚或指标异常扩大时提交主管处理。",
      source: "operation_boundary_projection",
    };
  }

  function coordinationOf(report, plan, contract) {
    const source = arr(report?.supportingCoordination || report?.taskDetailReport?.supportingCoordination || contract.supportingCoordination || plan.crossDepartmentActions);
    return source.map((value) => {
      if (typeof value === "string") return { department: "协同部门", deadline: "本任务时限内", action: text(value), requiredResponse: [], operatorFollowUp: "" };
      const item = obj(value);
      return {
        department: text(first([item.department, item.team])) || "协同部门",
        deadline: text(first([item.deadline, item.timeLimit])) || "本任务时限内",
        action: text(first([item.action, item.summary, item.text])),
        requiredResponse: arr(item.requiredResponse).map(text).filter(Boolean),
        operatorFollowUp: text(first([item.operatorFollowUp, item.followUp])),
      };
    }).filter((item) => item.action);
  }

  function attach(container, steps, lines, coordination, contract) {
    if (!container || typeof container !== "object" || Array.isArray(container)) return;
    container.operatorExecutionSteps = steps;
    container.operatorExecutionSop = lines;
    container.sopSteps = lines;
    container.supportingCoordination = coordination;
    container.activeActionContract = contract;
    container.structuredSopProjectionVersion = VERSION;
  }

  function projectReport(raw) {
    const report = raw && typeof raw === "object" ? raw : {};
    const detail = obj(report.taskDetailReport);
    const related = obj(report.relatedTask);
    const taskPlan = obj(detail.taskPlan || related.taskPlan || report.taskPlan);
    const plan = planOf(report);
    const family = familyOf(report);
    let contract = contractOf(report);
    const activeSop = obj(contract.activeSopPlan);
    const source = arr(report.operatorExecutionSteps || detail.operatorExecutionSteps || related.operatorExecutionSteps || taskPlan.operatorExecutionSteps || activeSop.operatorExecutionSteps || plan.operatorActionSteps || report.operatorExecutionSop || detail.operatorExecutionSop || related.operatorExecutionSop);
    const budget = budgetContext(report, plan, contract);
    let discarded = 0;
    let steps = source.map(normalizeStep).filter((step) => {
      const allowed = allowedStep(step, family);
      if (!allowed) discarded += 1;
      return allowed;
    }).map((step) => reconcileStep(step, budget));

    if (ROAS_FAMILIES.has(family)) {
      const budgetStep = authorizedBudgetStep(budget);
      const hasBudget = steps.some((step) => /预算|budget/i.test(semanticText(step)));
      if (budgetStep && (!hasBudget || steps.length < 4)) steps.splice(steps.length ? 1 : 0, 0, budgetStep);
      if (!steps.some((step) => /止损|回滚|停止条件|恢复/.test(semanticText(step)))) steps.splice(Math.max(steps.length - 1, 0), 0, stopStep(steps, budget));
    }
    steps = steps.filter((step) => text(step.title || step.action)).map((step, index) => ({ ...step, step: index + 1 }));
    const lines = steps.map((step) => {
      const parameters = arr(step.parameters).map(text).filter(Boolean).join("；");
      return parameters ? `${step.title}：${parameters}` : step.title;
    });
    const coordination = coordinationOf(report, plan, contract);
    contract = {
      ...contract,
      activeActionFamily: family || contract.activeActionFamily,
      activeSopPlan: { ...activeSop, operatorActionSteps: lines, operatorExecutionSteps: steps },
      supportingCoordination: coordination,
      structuredSopProjectionVersion: VERSION,
    };

    attach(report, steps, lines, coordination, contract);
    attach(detail, steps, lines, coordination, contract);
    attach(related, steps, lines, coordination, contract);
    attach(taskPlan, steps, lines, coordination, contract);
    detail.taskPlan = taskPlan;
    related.taskPlan = taskPlan;
    report.taskDetailReport = detail;
    report.relatedTask = related;
    report.discardedCrossFamilyStepCount = Number(report.discardedCrossFamilyStepCount || 0) + discarded;
    lastReport = report;
    return report;
  }

  function stepCard(step, index) {
    const parameters = arr(step.parameters).map((value) => `<span>${escapeHtml(value)}</span>`).join("");
    const conditions = [
      ["完成标准", step.successCondition],
      ["失败回滚", step.rollbackCondition],
      ["升级处理", step.escalationCondition],
    ].filter(([, value]) => text(value));
    return `<li class="structured-sop-step" data-structured-sop-step>
      <div class="structured-sop-index">${index + 1}</div>
      <div class="structured-sop-body">
        <div class="structured-sop-head"><strong>${escapeHtml(step.title || step.action)}</strong></div>
        ${text(step.action) && text(step.action) !== text(step.title) ? `<p class="structured-sop-action">${escapeHtml(step.action)}</p>` : ""}
        ${parameters ? `<div class="structured-sop-parameters"><em>执行参数</em><div>${parameters}</div></div>` : ""}
        ${conditions.length ? `<dl class="structured-sop-conditions">${conditions.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>` : ""}
      </div>
    </li>`;
  }

  function coordinationSection(items) {
    if (!items.length) return "";
    return `<section class="page-section structured-coordination-section" data-structured-coordination>
      <div class="section-header"><h3>前置协同</h3><span class="status-badge">不计入主操作变量</span></div>
      <div class="structured-coordination-list">${items.map((item) => `<article>
        <div><strong>${escapeHtml(item.department)}</strong><span>${escapeHtml(item.deadline)}</span></div>
        <p>${escapeHtml(item.action)}</p>
        ${arr(item.requiredResponse).length ? `<small>需反馈：${escapeHtml(arr(item.requiredResponse).join("、"))}</small>` : ""}
        ${item.operatorFollowUp ? `<small>运营跟进：${escapeHtml(item.operatorFollowUp)}</small>` : ""}
      </article>`).join("")}</div>
    </section>`;
  }

  function renderDom() {
    if (!lastReport || window.AppRouter?.routeFromHash?.() !== "task-report") return;
    const list = document.querySelector(".action-plan-section .action-step-list");
    const steps = arr(lastReport.operatorExecutionSteps);
    if (list && steps.length && list.dataset.structuredSopRendered !== VERSION) {
      list.classList.add("structured-sop-list");
      list.innerHTML = steps.map(stepCard).join("");
      list.dataset.structuredSopRendered = VERSION;
    }
    const actionSection = document.querySelector(".action-plan-section");
    const coordination = arr(lastReport.supportingCoordination);
    if (actionSection && coordination.length && !document.querySelector("[data-structured-coordination]")) {
      actionSection.insertAdjacentHTML("afterend", coordinationSection(coordination));
    }
  }

  if (!window.AppApi || typeof window.AppApi.taskReport !== "function") return;
  const originalTaskReport = window.AppApi.taskReport.bind(window.AppApi);
  window.AppApi.taskReport = async function (...args) {
    return projectReport(await originalTaskReport(...args));
  };

  const observer = new MutationObserver(renderDom);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("hashchange", () => { lastReport = null; });

  window.V2177StructuredSopUi = { version: VERSION, projectReport, normalizeStep };
})();
