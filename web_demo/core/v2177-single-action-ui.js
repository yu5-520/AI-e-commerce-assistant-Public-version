(function () {
  const VERSION = "21.7.7";
  const PLAN_FIELDS = [
    "creativeTestPlan",
    "budgetPlan",
    "activityPlan",
    "conversionRepairPlan",
    "similarProductPlan",
  ];
  const FAMILY_FIELD = {
    title_image_test: "creativeTestPlan",
    roas_scale: "budgetPlan",
    roas_guard: "budgetPlan",
    platform_activity: "activityPlan",
    activity_apply: "activityPlan",
    conversion_repair: "conversionRepairPlan",
    service_repair: "conversionRepairPlan",
    similar_product_test: "similarProductPlan",
  };

  function obj(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arr(value) {
    return Array.isArray(value) ? value.filter(Boolean) : [];
  }

  function clone(value) {
    try {
      if (typeof structuredClone === "function") return structuredClone(value);
    } catch (_) {
      // JSON fallback below.
    }
    try {
      return JSON.parse(JSON.stringify(value ?? {}));
    } catch (_) {
      return value && typeof value === "object" ? { ...value } : {};
    }
  }

  function firstText(values) {
    for (const value of values) {
      const text = String(value ?? "").trim();
      if (text) return text;
    }
    return "";
  }

  function reportFamily(report) {
    const detail = obj(report?.taskDetailReport);
    const related = obj(report?.relatedTask);
    const taskPlan = obj(detail.taskPlan || related.taskPlan || report?.taskPlan);
    const contract = obj(
      report?.activeActionContract ||
      detail.activeActionContract ||
      taskPlan.activeActionContract ||
      related.activeActionContract,
    );
    const agent2 = obj(
      report?.agent2ActionPlan ||
      detail.agent2ActionPlan ||
      taskPlan.agent2ActionPlan ||
      related.agent2ActionPlan,
    );
    return firstText([
      contract.activeActionFamily,
      taskPlan.selectedActionFamily,
      taskPlan.actionFamily,
      agent2.actionFamily,
      related.selectedActionFamily,
      related.actionFamily,
      report?.selectedActionFamily,
      report?.actionFamily,
    ]);
  }

  function findAgent2Plan(report) {
    const detail = obj(report?.taskDetailReport);
    const related = obj(report?.relatedTask);
    const taskPlan = obj(detail.taskPlan || related.taskPlan || report?.taskPlan);
    const candidates = [
      report?.agent2ActionPlan,
      detail.agent2ActionPlan,
      taskPlan.agent2ActionPlan,
      related.agent2ActionPlan,
      report?.plan,
      detail.plan,
      related.plan,
    ];
    return candidates.map(obj).find((value) => Object.keys(value).length) || {};
  }

  function stripCrossFamilyFields(value, family, seen = new WeakSet(), depth = 0) {
    if (!value || typeof value !== "object" || depth > 18) return value;
    if (seen.has(value)) return value;
    seen.add(value);

    const allowed = FAMILY_FIELD[family];
    if (!Array.isArray(value)) {
      PLAN_FIELDS.forEach((field) => {
        if (field !== allowed && Object.prototype.hasOwnProperty.call(value, field)) {
          delete value[field];
        }
      });
      Object.values(value).forEach((child) => stripCrossFamilyFields(child, family, seen, depth + 1));
    } else {
      value.forEach((child) => stripCrossFamilyFields(child, family, seen, depth + 1));
    }
    return value;
  }

  function activeContract(report, family, plan) {
    const existing = obj(
      report?.activeActionContract ||
      report?.taskDetailReport?.activeActionContract ||
      report?.relatedTask?.activeActionContract,
    );
    const familyField = FAMILY_FIELD[family];
    const taskPlan = obj(report?.taskDetailReport?.taskPlan || report?.relatedTask?.taskPlan || report?.taskPlan);
    const sop = arr(
      existing?.activeSopPlan?.operatorActionSteps ||
      report?.operatorExecutionSop ||
      report?.taskDetailReport?.operatorExecutionSop ||
      report?.relatedTask?.operatorExecutionSop ||
      taskPlan.operatorExecutionSop ||
      taskPlan.sopSteps ||
      plan.operatorActionSteps,
    );
    return {
      version: VERSION,
      activeActionFamily: family,
      activeOperationPlan: obj(existing.activeOperationPlan || plan.operationPlan || taskPlan.operationPlan),
      activeFamilyPlan: obj(existing.activeFamilyPlan || (familyField ? plan[familyField] || taskPlan[familyField] : {})),
      activeSopPlan: {
        ...obj(existing.activeSopPlan),
        operatorActionSteps: sop,
        executionSteps: arr(existing?.activeSopPlan?.executionSteps || plan.executionSteps || taskPlan.executionSteps),
        decisionBranches: arr(existing?.activeSopPlan?.decisionBranches || plan.decisionBranches || taskPlan.decisionBranches),
        submissionEvidence: arr(existing?.activeSopPlan?.submissionEvidence || plan.submissionEvidence || taskPlan.submissionEvidence),
        reviewMetrics: arr(existing?.activeSopPlan?.reviewMetrics || plan.reviewMetrics || taskPlan.reviewMetrics),
      },
      activeAuthority: obj(
        existing.activeAuthority ||
        report?.actionAuthorization ||
        report?.authorizationDecision ||
        report?.taskDetailReport?.actionAuthorization ||
        report?.relatedTask?.actionAuthorization,
      ),
      supportingCoordination: arr(existing.supportingCoordination || plan.crossDepartmentActions || taskPlan.crossDepartmentActions),
      source: existing.source || "v21.7.7_client_single_action_projection",
    };
  }

  function attachContract(target, contract) {
    if (!target || typeof target !== "object" || Array.isArray(target)) return;
    target.activeActionContract = contract;
    target.activeActionFamily = contract.activeActionFamily;
  }

  function sanitizeTaskReport(raw) {
    const report = clone(raw || {});
    const family = reportFamily(report);
    if (!family) return report;

    stripCrossFamilyFields(report, family);
    const plan = findAgent2Plan(report);
    const contract = activeContract(report, family, plan);

    attachContract(report, contract);
    attachContract(report.taskDetailReport, contract);
    attachContract(report.relatedTask, contract);
    attachContract(report.taskPlan, contract);
    attachContract(report.taskDetailReport?.taskPlan, contract);
    attachContract(report.relatedTask?.taskPlan, contract);

    const canonicalSteps = arr(contract.activeSopPlan?.operatorActionSteps);
    if (canonicalSteps.length) {
      report.operatorExecutionSop = canonicalSteps;
      if (report.taskDetailReport) report.taskDetailReport.operatorExecutionSop = canonicalSteps;
      if (report.relatedTask) report.relatedTask.operatorExecutionSop = canonicalSteps;
    }

    report.singleActionUiProjection = {
      version: VERSION,
      activeActionFamily: family,
      removedCrossFamilyPlanFields: PLAN_FIELDS.filter((field) => field !== FAMILY_FIELD[family]),
    };
    return report;
  }

  if (!window.AppApi || typeof window.AppApi.taskReport !== "function") return;
  const originalTaskReport = window.AppApi.taskReport.bind(window.AppApi);
  window.AppApi.taskReport = async function (...args) {
    return sanitizeTaskReport(await originalTaskReport(...args));
  };

  window.V2177SingleActionUi = {
    version: VERSION,
    sanitizeTaskReport,
  };
})();
