(function () {
  let notice = "";
  let selectedSource = "ERP";
  let selectedFileName = "";
  let pendingRows = [];
  let previewResult = null;
  let importRecords = null;
  let versionDetailData = null;
  const DETAIL_KEY = "ai_report_data_version_detail";
  const s = (value) => AppShell.escape(value ?? "");
  const sourceOptions = [["ERP", "ERP"], ["CRM", "CRM"], ["platform", "平台后台"], ["ads", "广告后台"], ["manual", "手动表格"]];
  const strategyOptions = [["review", "转人工复核"], ["archive", "保留审计并归档"], ["keep", "保留当前状态"]];

  function userHeader() { return AppApi?.getCurrentUserId?.() || "U001"; }
  function v3() { return AppMockData.v3 || { activeAlertCount: 0, highPriorityAlertCount: 0, taskLinkedAlertCount: 0, latestAlerts: [] }; }
  function shortVersion(value) { const text = String(value || "未导入"); return text.length > 24 ? `${text.slice(0, 24)}...` : text; }
  function rowDate(value) { return String(value || "").replace("T", " ").slice(0, 19); }
  function strategyText(strategy) { return strategy === "archive" ? "归档" : strategy === "keep" ? "保留" : "复核"; }
  function datasetLabel(name) { const map = { auto: "一键导入", products: "商品经营数据", inventory: "库存数据", orders: "订单销售数据", refunds: "售后退款数据", customers: "客户CRM数据" }; return map[name] || name || "报表"; }

  async function requestJson(path, fallback, options = {}) {
    try {
      const response = await fetch(path, { method: options.method || "GET", headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": userHeader() }, body: options.body ? JSON.stringify(options.body) : undefined });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.json();
    } catch (error) {
      console.warn(`[report-runtime] fallback for ${path}`, error);
      return fallback;
    }
  }

  async function loadImportRecords() {
    importRecords = await requestJson("/api/data/import-records", { records: [], rollbacks: [], total: 0, activeCount: 0, rolledBackCount: 0 });
    return importRecords;
  }

  async function loadVersionDetail(dataVersion) {
    versionDetailData = await requestJson(`/api/data/versions/${encodeURIComponent(dataVersion)}/detail`, null);
    return versionDetailData;
  }

  function parseCsv(text) {
    const rows = [];
    let row = [];
    let cell = "";
    let quoted = false;
    const source = String(text || "").replace(/^\uFEFF/, "");
    for (let index = 0; index < source.length; index += 1) {
      const char = source[index];
      const next = source[index + 1];
      if (char === '"' && quoted && next === '"') { cell += '"'; index += 1; continue; }
      if (char === '"') { quoted = !quoted; continue; }
      if (char === "," && !quoted) { row.push(cell.trim()); cell = ""; continue; }
      if ((char === "\n" || char === "\r") && !quoted) {
        if (char === "\r" && next === "\n") index += 1;
        row.push(cell.trim());
        if (row.some((item) => item !== "")) rows.push(row);
        row = [];
        cell = "";
        continue;
      }
      cell += char;
    }
    row.push(cell.trim());
    if (row.some((item) => item !== "")) rows.push(row);
    if (rows.length < 2) return [];
    const headers = rows[0].map((item) => item.trim());
    return rows.slice(1).map((values) => Object.fromEntries(headers.map((key, index) => [key, values[index] ?? ""]))).filter((item) => Object.values(item).some((value) => String(value || "").trim()));
  }

  function uploadPanel() {
    const status = previewResult ? (previewResult.status === "ready" ? "后台已分类" : previewResult.status === "needs_attention" ? "可导入需关注" : "字段不足") : "等待上传";
    return `<section class="page-section report-upload-panel"><div class="section-header"><div><h3>上传新报表</h3><p>前台只选择来源系统；商品、库存、利润、ROI、流量等分类由系统后台完成。</p></div><span class="status-badge">${s(status)}</span></div><div class="report-upload-row"><label class="report-upload-field"><span>来源系统</span><select data-report-source>${sourceOptions.map(([value, label]) => `<option value="${s(value)}" ${value === selectedSource ? "selected" : ""}>${s(label)}</option>`).join("")}</select></label><button type="button" data-upload-report>选择文件并预检</button><button type="button" class="ghost" data-v3-demo>备用：使用示例数据试跑</button><input id="reportFileInput" type="file" accept=".csv,text/csv" data-report-file hidden /></div><small class="report-upload-tip">${s(selectedFileName || "支持 CSV。一份报表可同时包含商品、库存、利润、ROI、流量等字段。")}</small></section>`;
  }

  function v6Metrics() {
    const data = v3();
    const records = importRecords || {};
    return [["新增预警", data.activeAlertCount || 0, "后台路由"], ["高风险", data.highPriorityAlertCount || 0, "优先处理"], ["导入版本", records.total ?? 0, "记录留痕"], ["回滚版本", records.rolledBackCount ?? 0, "测试清理"]].map(([a,b,c]) => AppShell.metricCard(a,b,c)).join("");
  }

  function previewTable(rows) {
    const list = rows || [];
    const headers = Array.from(new Set(list.flatMap((row) => Object.keys(row || {})))).slice(0, 8);
    if (!headers.length) return "";
    return `<div class="report-preview-table"><table><thead><tr>${headers.map((header) => `<th>${s(header)}</th>`).join("")}</tr></thead><tbody>${list.map((row) => `<tr>${headers.map((header) => `<td>${s(row?.[header] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function detectedRoutesBlock() {
    const routes = previewResult?.detectedDatasets || [];
    if (!routes.length) return `<div class="log-empty">暂未识别到后台分类路由。</div>`;
    return `<div class="report-card-list">${routes.map((route) => `<article class="report-card"><div><h3>${s(route.label || datasetLabel(route.datasetName))}</h3><p>${s(route.routeReason || route.alertHint || "后台自动分类")}</p><div class="report-meta"><span>${s(route.datasetName)}</span><span>${s(route.rowCount || previewResult.rowCount || 0)} 行</span>${(route.targetModules || []).map((item) => `<span>${s(item)}</span>`).join("")}</div></div><div class="report-actions"><span class="status-badge ${route.status === "ready" ? "good" : route.status === "needs_attention" ? "warning" : "danger"}">${s(route.status === "ready" ? "可导入" : route.status === "needs_attention" ? "需关注" : "阻断")}</span></div></article>`).join("")}</div>`;
  }

  function previewPanel() {
    if (!previewResult) return "";
    const fields = previewResult.recognizedFields || [];
    const issues = previewResult.issues || [];
    const level = previewResult.status === "ready" ? "good" : previewResult.status === "needs_attention" ? "warning" : "danger";
    return `<section class="page-section report-preview-panel"><div class="section-header"><div><h3>一键导入预检</h3><p>${s(previewResult.message || "字段检查完成")}</p></div><span class="status-badge ${s(level)}">${s(previewResult.label || "一键导入")} · ${s(previewResult.rowCount)} 行</span></div><div class="report-preview-grid"><article><h4>后台分类结果</h4>${detectedRoutesBlock()}</article><article><h4>已识别字段</h4><div class="report-meta">${fields.length ? fields.slice(0, 18).map((item) => `<span>${s(item.label)} ← ${s(item.sourceField)}</span>`).join("") : `<span>暂无字段识别</span>`}</div>${issues.length ? `<ul>${issues.map((item) => `<li class="${s(item.severity)}">${s(item.message)}</li>`).join("")}</ul>` : `<p>字段可路由，确认后由后台写入对应模块。</p>`}</article></div><section class="page-section report-section"><div class="section-header compact"><h3>前 5 行预览</h3><div class="report-actions"><button type="button" class="ghost" data-cancel-preview>重新选择</button><button type="button" data-confirm-import ${previewResult.canImport ? "" : "disabled"}>确认一键导入</button></div></div>${previewTable(previewResult.previewRows || [])}</section></section>`;
  }

  function pipelineBlock() {
    return `<section class="page-section report-section"><div class="section-header"><div><h3>后台处理链路</h3><p>V6.0 不再要求用户手动选择商品/订单/库存报表。</p></div><span class="status-badge">系统运行</span></div><div class="report-preview-grid"><article><h4>一键输入</h4><p>ERP、CRM、平台后台、广告后台数据统一上传。</p></article><article><h4>后台分类</h4><p>系统按字段识别商品、库存、利润、订单、售后、ROI、流量等数据。</p></article><article><h4>模块写入</h4><p>分类结果写入商品中心、趋势中心、任务中心和导入记录。</p></article><article><h4>任务触发</h4><p>现阶段延续预警任务同步；后续 V6.1 接入趋势信号。</p></article></div></section>`;
  }

  function latestAlertsBlock() {
    const alerts = v3().latestAlerts || [];
    if (!alerts.length) return "";
    return `<section class="page-section report-section"><div class="section-header"><h3>最新后台预警</h3><span class="status-badge">${s(alerts.length)} 条</span></div><div class="report-card-list">${alerts.map((alert) => `<article class="report-card"><div><h3>${s(alert.alertType)}</h3><p>${s(alert.entityId)} · ${s(alert.riskDomain)} · ${s(alert.priority)}</p><div class="report-meta"><span>${s(datasetLabel(alert.sourceDataset))}</span><span title="${s(alert.dataVersion)}">${s(shortVersion(alert.dataVersion))}</span><span>${s(alert.storeName || alert.storeId || "未绑定店铺")}</span><span>${s(alert.status)}</span></div></div><div class="report-actions"><button type="button" data-alert-report="${s(alert.alertId)}">证据报告</button>${alert.taskId ? `<button type="button" class="ghost" data-open-task="${s(alert.taskId)}">查看待办</button>` : ""}</div></article>`).join("")}</div></section>`;
  }

  function importRecordRow(record) {
    const rolled = record.versionStatus === "rolled_back";
    const rollbackText = rolled && record.rollback?.taskStrategy ? ` · ${s(strategyText(record.rollback.taskStrategy))}` : "";
    return `<article class="import-record-row ${rolled ? "rolled" : ""}"><div class="import-record-name"><strong>${s(datasetLabel(record.datasetName))}</strong><span title="${s(record.dataVersion)}">${s(shortVersion(record.dataVersion))}</span></div><div class="import-record-stats"><span>行数 ${s(record.rowCount || 0)}</span><span>预警 ${s(record.alertCount || 0)}</span><span>活跃 ${s(record.activeAlertCount || 0)}</span><span>任务 ${s(record.taskCount || 0)}</span></div><div class="import-record-state"><span class="status-badge ${rolled ? "warning" : "good"}">${rolled ? "已回滚" : "生效中"}</span><small>${s(rowDate(record.createdAt))}${rollbackText}</small></div><div class="report-actions"><button type="button" data-version-detail="${s(record.dataVersion)}">详情</button><button type="button" class="ghost" data-delete-version="${s(record.dataVersion)}">删除</button></div></article>`;
  }

  function importRecordsBlock() {
    const records = importRecords?.records || [];
    return `<section class="page-section report-section report-import-records"><div class="section-header"><div><h3>导入记录</h3><p>记录每次后台分类后产生的数据版本、预警和任务。</p></div><div class="report-actions"><button type="button" class="ghost" data-refresh-records>刷新记录</button></div></div><div class="import-record-list compact">${records.length ? records.map(importRecordRow).join("") : `<div class="log-empty">暂无导入记录。上传报表或试跑示例数据后会出现版本记录。</div>`}</div></section>`;
  }

  function metricCards(items) { return `<section class="kpi-grid report-metrics">${items.map(([a,b,c]) => AppShell.metricCard(a,b,c)).join("")}</section>`; }
  function strategySelect(record) { return `<label class="rollback-strategy"><span>关联任务</span><select data-detail-strategy>${strategyOptions.map(([value, label]) => `<option value="${s(value)}" ${record.rollback?.taskStrategy === value ? "selected" : ""}>${s(label)}</option>`).join("")}</select></label>`; }
  function versionAlertsBlock(detail) { const alerts = detail?.alerts || []; return `<section class="page-section report-section"><div class="section-header"><h3>版本预警</h3><span class="status-badge">${alerts.length} 条</span></div><div class="version-alert-list">${alerts.length ? alerts.map((alert) => `<article class="version-alert-row"><strong>${s(alert.alertType || alert.riskDomain || "预警")}</strong><span>${s(alert.entityId)} · ${s(alert.priority)} · ${s(alert.storeName || alert.storeId || "未绑定店铺")}</span><small>${s(alert.status)}${alert.taskId ? ` · 任务 ${s(alert.taskId)}` : ""}</small></article>`).join("") : `<div class="log-empty">当前账号范围内没有该版本预警明细。</div>`}</div></section>`; }
  function tasksBlock(detail) { const tasks = detail?.tasks || []; if (detail?.rollback?.handledTasks?.length) { return `<section class="page-section report-section"><div class="section-header"><h3>关联任务处理</h3><span class="status-badge">${s(strategyText(detail.rollback.taskStrategy))}</span></div><div class="version-alert-list">${detail.rollback.handledTasks.map((task) => `<article class="version-alert-row"><strong>${s(task.title || task.taskId)}</strong><span>${s(task.fromStatus || "-")} → ${s(task.toStatus || "-")}</span><small>${s(task.workflowStatus || task.strategy || "已处理")}</small></article>`).join("")}</div></section>`; } return `<section class="page-section report-section"><div class="section-header"><h3>关联任务</h3><span class="status-badge">${tasks.length}</span></div><div class="version-alert-list">${tasks.length ? tasks.map((task) => `<article class="version-alert-row"><strong>${s(task.title || task.id)}</strong><span>${s(task.status || "-")} · ${s(task.workflowStatus || "-")}</span><small>${s(task.assigneeName || task.assigneeId || "未分配")}</small></article>`).join("") : `<div class="log-empty">该版本暂无可见关联任务。</div>`}</div></section>`; }
  function versionDetail(detail) { const record = detail.record || {}; const canRollback = detail.permissions?.canRollback; const canDelete = detail.permissions?.canDelete !== false; return `<section class="report-detail-hero"><div><p class="eyebrow">DATA VERSION · V6.0</p><h2>${s(datasetLabel(record.datasetName))}</h2></div><div class="report-actions"><button type="button" data-back-report>返回报表中心</button><button type="button" data-copy-version="${s(record.dataVersion)}">复制版本</button>${canRollback ? `<button type="button" data-detail-rollback="${s(record.dataVersion)}">回滚版本</button>` : ""}${canDelete ? `<button type="button" class="ghost" data-delete-version="${s(record.dataVersion)}">删除记录</button>` : ""}</div></section>${notice ? AppShell.notice("操作结果", notice) : ""}${metricCards([["行数", record.rowCount || 0, "导入记录"], ["预警", detail.summary?.alertCount || 0, "该版本"], ["任务", detail.summary?.taskCount || 0, "关联待办"], ["状态", record.versionStatus === "rolled_back" ? "已回滚" : "生效中", datasetLabel(record.datasetName)]])}<section class="page-section report-section"><div class="section-header"><h3>版本处理</h3><span class="status-badge">Demo 管理</span></div><div class="report-preview-grid"><article><h4>数据版本</h4><p>${s(record.dataVersion)}</p><div class="report-meta"><span>${s(datasetLabel(record.datasetName))}</span><span>${s(rowDate(record.createdAt))}</span></div></article><article><h4>测试清理</h4><p>删除记录会清除该版本的导入行、快照、预警和关联活跃任务；适合 Demo 反复导入测试。</p>${canRollback ? strategySelect(record) : ""}</article></div></section>${versionAlertsBlock(detail)}${tasksBlock(detail)}`; }

  async function previewSelectedFile(file) {
    if (!file) { notice = "请选择要导入的 CSV 报表。"; AppRouter.schedule("report-upload-empty"); return; }
    selectedFileName = file.name;
    previewResult = null;
    pendingRows = [];
    notice = `正在预检 ${file.name}。`;
    AppRouter.schedule("report-preview-start");
    const text = await file.text();
    const rows = parseCsv(text);
    if (!rows.length) { notice = "文件没有读取到有效数据。"; AppRouter.schedule("report-upload-invalid"); return; }
    pendingRows = rows;
    previewResult = await AppApi.previewReportRows("auto", rows, {}, selectedSource);
    notice = previewResult?.message || "后台分类预检完成，请确认后一键导入。";
    AppRouter.schedule("report-preview-done");
  }

  async function confirmImport() {
    if (!previewResult || !pendingRows.length) { notice = "请先上传报表并完成字段预检。"; AppRouter.schedule("report-confirm-empty"); return; }
    notice = "正在一键导入，后台执行字段识别和分类路由...";
    AppRouter.schedule("report-confirm-start");
    const result = await AppApi.confirmReportImport("auto", pendingRows, previewResult.fieldMapping || {}, selectedSource);
    await AppApi.refreshAfterDataImport();
    await loadImportRecords();
    notice = result?.message || `导入完成：后台分类 ${result?.routedDatasetCount || 0} 类，生成 ${result?.alertCount || 0} 条预警，${result?.createdTaskCount || 0} 条进入待办。`;
    previewResult = null;
    pendingRows = [];
    selectedFileName = "";
    AppRouter.schedule("report-confirm-done");
  }

  async function rollbackVersion(dataVersion) { const strategy = document.querySelector("[data-detail-strategy]")?.value || "review"; const reason = window.prompt("请输入回滚原因", "上传错表，回滚该数据版本产生的预警。") || "上传错表，回滚该数据版本产生的预警。"; notice = "正在回滚数据版本并处理关联任务..."; AppRouter.schedule("rollback-start"); const result = await requestJson(`/api/data/versions/${encodeURIComponent(dataVersion)}/rollback`, null, { method: "POST", body: { reason, taskStrategy: strategy } }); await AppApi.refreshAfterDataImport(); await loadImportRecords(); await loadVersionDetail(dataVersion); const rollback = result?.rollback; notice = rollback ? `已回滚 ${shortVersion(dataVersion)}：${rollback.affectedAlertCount || 0} 条预警移出看板，${rollback.affectedTaskCount || 0} 个关联任务按 ${strategyText(strategy)} 处理。` : "回滚请求已提交。"; AppRouter.schedule("rollback-done"); }
  async function deleteVersion(dataVersion) { if (!dataVersion) return; const ok = window.confirm(`删除导入记录 ${shortVersion(dataVersion)}？\n该操作会清除该版本导入行、预警和关联活跃任务，适合 Demo 测试清理。`); if (!ok) return; notice = "正在删除导入记录..."; AppRouter.schedule("delete-version-start"); const result = await requestJson(`/api/data/versions/${encodeURIComponent(dataVersion)}?confirm=true`, null, { method: "DELETE", body: { reason: "Demo 阶段删除导入记录，避免测试数据叠加。" } }); await AppApi.refreshAfterDataImport(); await loadImportRecords(); if (versionDetailData?.record?.dataVersion === dataVersion) { versionDetailData = null; localStorage.removeItem(DETAIL_KEY); } notice = result?.message || `已删除 ${shortVersion(dataVersion)}。`; AppRouter.navigate("data-check"); AppRouter.schedule("delete-version-done"); }

  window.ReportPage = { route: "data-check", title: "报表中心", async render() { if (!importRecords) await loadImportRecords(); return `<section class="report-hero report-hero-clean"><div><p class="eyebrow">REPORT CENTER · V6.0</p><h2>ERP / CRM 一键导入</h2></div><div class="report-hero-side"><span>当前流程</span><strong>后台自动分类</strong><small>前台只保留导入记录</small></div></section>${uploadPanel()}${notice ? AppShell.notice("操作结果", notice) : ""}${previewPanel()}<section class="kpi-grid report-metrics">${v6Metrics()}</section>${pipelineBlock()}${latestAlertsBlock()}${importRecordsBlock()}`; }, mount(ctx) { ctx.delegate("[data-report-source]", "change", (_, node) => { selectedSource = node.value || "ERP"; previewResult = null; pendingRows = []; }); ctx.delegate("[data-upload-report]", "click", () => document.getElementById("reportFileInput")?.click()); ctx.delegate("[data-report-file]", "change", async (_, node) => { await previewSelectedFile(node.files?.[0]); node.value = ""; }); ctx.delegate("[data-confirm-import]", "click", () => confirmImport()); ctx.delegate("[data-cancel-preview]", "click", () => { previewResult = null; pendingRows = []; selectedFileName = ""; notice = "已取消本次导入预检。"; AppRouter.schedule("report-preview-cancel"); }); ctx.delegate("[data-v3-demo]", "click", async () => { notice = "正在使用示例报表试跑预警链路..."; AppRouter.schedule("v6-demo-start"); const result = await AppApi.importMockAlerts(); await AppApi.refreshAfterDataImport(); await loadImportRecords(); notice = result?.alertCount || result?.createdTaskCount ? `示例数据已生成 ${result.alertCount || 0} 条预警，${result.createdTaskCount || 0} 条进入待办。` : "示例报表已检查，暂无新增预警。"; AppRouter.schedule("v6-demo-done"); }); ctx.delegate("[data-refresh-records]", "click", async () => { await loadImportRecords(); notice = "导入记录已刷新。"; AppRouter.schedule("record-refresh"); }); ctx.delegate("[data-version-detail]", "click", (_, node) => { localStorage.setItem(DETAIL_KEY, node.dataset.versionDetail); AppRouter.navigate("data-version-detail", { dataVersion: node.dataset.versionDetail }); }); ctx.delegate("[data-delete-version]", "click", (_, node) => deleteVersion(node.dataset.deleteVersion)); ctx.delegate("[data-alert-report]", "click", (_, node) => AppTaskActions.openAlertReport(node.dataset.alertReport)); ctx.delegate("[data-open-task]", "click", (_, node) => AppTaskActions.openTodoTask(node.dataset.openTask)); ctx.addCleanup(AppTaskStore.subscribe(() => AppRouter.schedule("task-store"))); } };
  window.DataVersionDetailPage = { route: "data-version-detail", title: "数据版本详情", async render(ctx) { const dataVersion = ctx.state?.dataVersion || localStorage.getItem(DETAIL_KEY); if (!dataVersion) return `<section class="page-section"><h3>未选择数据版本</h3><button type="button" data-back-report>返回报表中心</button></section>`; localStorage.setItem(DETAIL_KEY, dataVersion); const detail = await loadVersionDetail(dataVersion); if (!detail?.record) return `<section class="page-section"><h3>数据版本不存在</h3><button type="button" data-back-report>返回报表中心</button></section>`; return versionDetail(detail); }, mount(ctx) { ctx.delegate("[data-back-report]", "click", () => AppRouter.navigate("data-check")); ctx.delegate("[data-copy-version]", "click", async (_, node) => { await navigator.clipboard?.writeText(node.dataset.copyVersion || ""); notice = "数据版本号已复制。"; AppRouter.schedule("copy-version"); }); ctx.delegate("[data-detail-rollback]", "click", (_, node) => rollbackVersion(node.dataset.detailRollback)); ctx.delegate("[data-delete-version]", "click", (_, node) => deleteVersion(node.dataset.deleteVersion)); } };
})();