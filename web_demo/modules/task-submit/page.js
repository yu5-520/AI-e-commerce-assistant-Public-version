(function () {
  const s = (value) => AppShell.escape(value ?? "");
  let taskId = "";
  let report = null;
  let notice = "";

  function arr(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
  function task() { return report?.relatedTask || {}; }
  function detail() { return report?.taskDetailReport || task().taskDetailReport || {}; }
  function plan() { return detail().taskPlan || task().taskPlan || task().taskCard || {}; }
  function steps() {
    return arr(
      report?.operatorExecutionSop ||
      detail().operatorExecutionSop ||
      task().operatorExecutionSop ||
      report?.operatorSopSteps ||
      report?.sopSteps ||
      detail().sopSteps ||
      task().sopSteps
    );
  }
  function productIdentity() { return report?.productIdentity || detail().productIdentity || task().productIdentity || (task().productActionCards || [])[0] || {}; }
  function routeTaskId(ctx = {}) {
    const state = ctx.state || {};
    const hashState = AppRouter.stateFromHash?.() || {};
    return state.taskId || state.task_id || state.id || hashState.taskId || hashState.task_id || hashState.id || "";
  }

  function evidenceRequirements() {
    const direct = arr(report?.evidencePack || detail().evidencePack || task().evidencePack || task().evidence || task().completionGate?.requiredEvidence);
    const fromPlan = arr(plan().evidenceRequirements || task().evidenceRequirements);
    const result = [...direct, ...fromPlan]
      .map((item) => typeof item === "string" ? { title: item, summary: "按任务要求提交对应执行痕迹" } : item)
      .filter(Boolean);
    const seen = new Set();
    return result.filter((item) => {
      const key = String(item.title || item.label || item.summary || "").trim();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 8);
  }

  function fieldName(label, index) { return `field_${index}_${String(label).replace(/[^\w\u4e00-\u9fa5]/g, "_")}`; }

  function traceFields() {
    const text = [report?.title, report?.warningSummary, task().riskDomain, task().actionType, task().taskType, ...steps().map((item) => item?.title || item?.action || item?.summary || item)].join(" ");
    if (/退款|售后|客服|差评/.test(text)) return ["退款原因Top1", "退款原因Top2", "已修改承诺或话术", "是否暂停SKU放量", "预计复盘时间", "未完成风险"];
    if (/主图|标题|点击|素材|转化|详情页/.test(text)) return ["测试标题数量", "测试主图数量", "对照组链接", "测试周期", "预算变化", "未完成风险"];
    if (/广告|投放|预算|ROAS|ROI|人群|关键词/.test(text)) return ["调整计划名称", "预算调整比例", "保留计划", "暂停计划", "24小时复盘指标", "未完成风险"];
    return ["实际执行动作", "执行数量或比例", "执行时间", "影响范围", "是否需要复核", "未完成风险"];
  }

  function renderHero() {
    const t = task();
    const p = productIdentity();
    const title = report?.title || t.title || p.productTitle || p.title || "提交执行结果";
    const subtitle = [p.productTitle || p.title, p.storeName || p.store || t.storeName || t.store].filter(Boolean).join(" · ");
    const status = report?.taskStatus || t.status || "执行中";
    return `<section class="report-hero task-submit-hero">
      <div>
        <p class="task-report-kicker">提交执行痕迹</p>
        <h2>${s(title)}</h2>
        <p>${s(subtitle || "完成下方执行记录后提交，系统将进入自动复盘链路。")}</p>
      </div>
      <div class="report-hero-side"><span>当前状态</span><strong>${s(status)}</strong><small>${s(t.store || t.storeName || "任务池")}</small></div>
    </section>`;
  }

  function renderProductObject() {
    const p = productIdentity();
    const rows = [
      ["商品", p.productTitle || p.title || p.shortTitle || task().productTitle || "任务商品"],
      ["系统编码", p.systemProductCode || p.productCode || p.productId || task().productId || "未标注"],
      ["店铺", p.storeName || p.store || task().store || task().storeName || "经营单元"],
      ["平台", p.platform || task().platform || "经营平台"],
    ];
    return `<section class="page-section"><div class="section-header"><h3>提交对象</h3><span class="status-badge">任务已绑定</span></div><div class="task-object-grid">${rows.map(([label, value]) => `<article><span>${s(label)}</span><strong>${s(value)}</strong></article>`).join("")}</div></section>`;
  }

  function renderSteps() {
    const list = steps();
    if (!list.length) return `<section class="page-section"><div class="section-header"><h3>执行SOP</h3><span class="status-badge">方案不可用</span></div><p>当前任务没有可执行 SOP，不能提交执行结果。</p></section>`;
    return `<section class="page-section"><div class="section-header"><h3>执行SOP</h3><span class="status-badge">提交前核对</span></div><ol class="action-step-list">${list.map((item) => `<li>${s(item?.title || item?.action || item?.summary || item)}</li>`).join("")}</ol></section>`;
  }

  function renderEvidenceItem(item, index) {
    const title = item.title || item.label || item.metricName || `提交材料 ${index + 1}`;
    const summary = item.summary || item.reason || item.value || item.text || "提交截图、沟通记录或后台凭证，并填写对应说明。";
    return `<article class="report-card compact submit-evidence-item" data-evidence-index="${index}" data-evidence-title="${s(title)}">
      <strong>${index + 1}. ${s(title)}</strong>
      <p>${s(summary)}</p>
      <label class="submit-upload-card"><span>选择截图或凭证</span><input type="file" data-upload-field="${s(title)}" data-upload-index="e${index}" accept="image/*,.pdf,.xlsx,.xls,.csv" /><em data-upload-name="e${index}">未选择文件</em></label>
      <label class="submit-field full"><span>材料说明</span><textarea rows="2" name="evidence_note_${index}" data-evidence-note="${index}" placeholder="说明这份材料对应的执行动作与结果"></textarea></label>
    </article>`;
  }

  function renderEvidenceConditions() {
    const list = evidenceRequirements();
    const items = list.length ? list : [{ title: "关键执行痕迹", summary: "提交任务执行后的关键截图或后台凭证，并说明实际操作结果。" }];
    return `<section class="page-section"><div class="section-header"><h3>提交材料</h3><span class="status-badge">真实执行痕迹</span></div><div class="report-card-list compact-report-list">${items.map(renderEvidenceItem).join("")}</div></section>`;
  }

  function renderTraceFields() {
    const fields = traceFields();
    return `<section class="page-section"><div class="section-header"><h3>运营操作记录</h3><span class="status-badge">结构化填写</span></div><div class="submit-field-grid">${fields.map((label, index) => `<label class="submit-field"><span>${s(label)}</span><textarea rows="2" name="${s(fieldName(label, index))}" data-field-label="${s(label)}" placeholder="填写${s(label)}"></textarea></label>`).join("")}</div></section>`;
  }

  function renderForm() {
    return `<form id="taskSubmitForm" class="task-submit-form">
      ${renderProductObject()}
      ${renderSteps()}
      ${renderEvidenceConditions()}
      ${renderTraceFields()}
      <section class="page-section task-submit-section">
        <div class="section-header"><h3>提交结果</h3><span class="status-badge">提交后自动复盘</span></div>
        <label class="submit-field full"><span>执行总结</span><textarea rows="4" name="summary" required placeholder="概括已完成的 SOP 动作、实际调整值和影响范围"></textarea></label>
        <label class="submit-field full"><span>结果备注</span><textarea rows="3" name="result" placeholder="补充异常情况、未完成事项或需要主管复核的内容"></textarea></label>
        <div class="task-submit-footer">
          <div><span>提交后</span><strong>任务进入等待系统自动复盘，不需要运营填写复盘结论。</strong></div>
          <div class="report-actions"><button type="button" class="secondary" data-open-detail="${s(taskId)}">返回任务详情</button><button type="button" class="secondary" data-back-task>返回任务列表</button><button type="submit">提交执行痕迹</button></div>
        </div>
      </section>
    </form>`;
  }

  function renderPage() {
    if (!taskId) return `<section class="page-section"><div class="section-header"><h3>缺少任务ID</h3><span class="status-badge">路由缺少任务ID</span></div><p>当前地址没有携带 taskId，系统不会沿用上一次打开的任务。</p><button data-back-task>返回任务列表</button></section>`;
    return `${renderHero()}${notice ? AppShell.notice("提交结果", notice) : ""}${renderForm()}`;
  }

  function collectPayload(form) {
    const fields = {};
    form.querySelectorAll("[data-field-label]").forEach((node) => {
      const label = node.dataset.fieldLabel;
      const value = node.value.trim();
      if (label && value) fields[label] = value;
    });

    const evidenceItems = [];
    form.querySelectorAll("[data-evidence-index]").forEach((card) => {
      const index = card.dataset.evidenceIndex;
      const title = card.dataset.evidenceTitle || `材料${index}`;
      const note = form.querySelector(`[data-evidence-note="${CSS.escape(index)}"]`)?.value?.trim() || "";
      const input = form.querySelector(`[data-upload-index="e${CSS.escape(index)}"]`);
      evidenceItems.push({
        title,
        note,
        filename: input?.files?.[0]?.name || "",
        size: input?.files?.[0]?.size || 0,
        type: input?.files?.[0]?.type || "",
      });
    });

    const attachments = Array.from(form.querySelectorAll("[data-upload-field]"))
      .map((input) => ({ field: input.dataset.uploadField, filename: input.files?.[0]?.name || "", size: input.files?.[0]?.size || 0, type: input.files?.[0]?.type || "" }))
      .filter((item) => item.filename);

    const summary = form.summary?.value?.trim() || "";
    const result = form.result?.value?.trim() || "";
    if (!summary) throw new Error("请填写执行总结后再提交。");
    const hasTrace = attachments.length || evidenceItems.some((item) => item.note) || Object.keys(fields).length || result;
    if (!hasTrace) throw new Error("请至少填写一项操作记录、材料说明或结果备注。");

    return {
      summary,
      note: summary,
      result,
      fields,
      formFields: fields,
      operationTrace: fields,
      evidenceItems,
      attachments,
      evidenceLinks: attachments,
      action: "执行痕迹提交",
      taskAction: task().actionType || task().taskType || "运营执行",
      enterRecap: true,
      operatorManualRecapRequired: false,
    };
  }

  window.TaskSubmitPage = {
    route: "task-submit",
    title: "提交执行痕迹",
    async render(ctx) {
      taskId = routeTaskId(ctx);
      notice = "";
      report = taskId ? await AppApi.taskReport(taskId) : null;
      return renderPage();
    },
    mount(ctx) {
      ctx.delegate("[data-back-task]", "click", () => AppRouter.navigate("business-actions", taskId ? { focusTaskId: taskId } : null));
      ctx.delegate("[data-open-detail]", "click", (_, node) => AppRouter.navigate("task-report", { taskId: node.dataset.openDetail }));
      ctx.delegate("[data-upload-field]", "change", (_, node) => {
        const label = document.querySelector(`[data-upload-name="${CSS.escape(node.dataset.uploadIndex || "")}"]`);
        if (label) label.textContent = node.files?.[0]?.name || "未选择文件";
      });
      ctx.delegate("#taskSubmitForm", "submit", async (event, form) => {
        event.preventDefault();
        const button = form.querySelector('button[type="submit"]');
        const original = button.textContent;
        button.disabled = true;
        button.textContent = "提交中";
        try {
          const payload = collectPayload(form);
          const result = await AppApi.submitTask(taskId, payload);
          if (result?.task?.id) window.AppTaskStore?.upsert?.(result.task);
          await AppApi.refreshTaskState().catch(() => null);
          AppRouter.navigate("business-actions", { focusTaskId: taskId });
        } catch (error) {
          notice = error?.message || "提交失败，请检查执行记录后重试。";
          button.disabled = false;
          button.textContent = original;
          AppShell.setView(renderPage());
          window.TaskSubmitPage.mount(ctx);
        }
      });
    },
  };
})();
