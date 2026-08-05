(function () {
  const s = (value) => AppShell.escape(value ?? "");
  let lastImportSync = null;
  let pollTimer = null;
  let latestLive = null;
  let uploadState = {
    status: "idle",
    title: "等待上传",
    detail: "选择 Excel / CSV / JSON 报表后，系统会显示上传、解析、入库和队列状态。",
    fileName: "",
    dataVersion: "",
    rows: 0,
  };

  const FALLBACK_SOURCES = [
    { sourceId: "erp", label: "ERP", priority: "primary", displayStatus: "待配置", cadence: "15分钟 / 1小时", actionLabel: "同步" },
    { sourceId: "crm", label: "CRM", priority: "primary", displayStatus: "待配置", cadence: "1小时 / 每日", actionLabel: "同步" },
    { sourceId: "platform", label: "平台后台 API", priority: "primary", displayStatus: "待配置", cadence: "15分钟 / 1小时", actionLabel: "同步" },
    { sourceId: "ads", label: "广告后台 API", priority: "primary", displayStatus: "待配置", cadence: "15分钟 / 每日", actionLabel: "同步" },
    { sourceId: "manual_upload", label: "手动上传", priority: "backup", displayStatus: "备用", cadence: "按需", actionLabel: "上传" },
  ];

  const PRODUCT_FLOW_NODES = [
    { nodeCode: "agent1", label: "Agent1研判" },
    { nodeCode: "action_matrix", label: "动作矩阵" },
    { nodeCode: "agent2_draft", label: "Agent2草案" },
    { nodeCode: "agent3_sop", label: "Agent3 SOP" },
    { nodeCode: "task_mapping", label: "任务映射" },
    { nodeCode: "task_pool", label: "任务池" },
    { nodeCode: "task_loop", label: "任务闭环" },
  ];

  const NODE_CODE_BY_LABEL = new Map([
    ["信号引擎", "signal_engine"],
    ["Agent1 研判", "agent1"],
    ["Agent1研判", "agent1"],
    ["动作矩阵", "action_matrix"],
    ["Agent2 动作草案", "agent2_draft"],
    ["Agent2草案", "agent2_draft"],
    ["Agent3 SOP 生成", "agent3_sop"],
    ["Agent3 SOP", "agent3_sop"],
    ["任务映射", "task_mapping"],
    ["任务池", "task_pool"],
    ["任务闭环", "task_loop"],
  ]);

  function realRecords(payload) {
    if (Array.isArray(payload?.syncRecords)) return payload.syncRecords;
    const groups = Array.isArray(payload?.reportGroups) ? payload.reportGroups : [];
    return groups.flatMap((group) => (group.reports || []).filter((item) => item.latestDataVersion || item.status === "已导入"));
  }

  function latestReport(payload = {}) {
    const records = realRecords(payload);
    const first = records[0] || {};
    const sync = lastImportSync || payload?.v104ImportTaskSync || window.AppApi?.status?.lastImportSync;
    if (!payload?.hasData && !sync && !records.length) return { label: "等待接入", status: "待接入", taskCount: 0, rows: 0 };
    return {
      label: sync?.datasetNames?.join(" / ") || first.name || first.label || payload?.v3?.latestDataVersion || "数据中台",
      status: sync?.status === "completed" ? "已就绪" : first.status || (payload?.v3?.latestDataVersion ? "已就绪" : "待接入"),
      taskCount: sync?.createdTaskCount ?? first.createdTaskCount ?? first.taskCount ?? payload?.recentAlerts?.length ?? 0,
      rows: sync?.rowCount ?? first.rows ?? first.totalRows ?? 0,
    };
  }

  function currentCounts(stage = {}) {
    const current = stage.current || {};
    return {
      queued: Number(current.queued ?? stage.queued ?? 0),
      running: Number(current.running ?? stage.running ?? 0),
      completed: Number(current.completed ?? stage.completed ?? 0),
      failed: Number(current.failed ?? stage.failed ?? 0),
      observed: Number(current.observed ?? stage.observed ?? 0),
      admitted: Number(current.admitted ?? stage.admitted ?? 0),
      historyCompleted: Number(stage.history?.completed ?? stage.historyCompleted ?? 0),
    };
  }

  function nodeCode(stage = {}) {
    return stage.nodeCode || stage.code || NODE_CODE_BY_LABEL.get(stage.label || stage.node || "") || "";
  }

  function normalizedProductStages(live = {}) {
    const source = (live.productStages || live.stages || []).filter((item) => item && typeof item === "object");
    const byCode = new Map(source.map((item) => [nodeCode(item), item]).filter(([code]) => code));
    return PRODUCT_FLOW_NODES.map((definition) => ({
      ...definition,
      ...(byCode.get(definition.nodeCode) || { status: "waiting", current: {}, history: {} }),
      nodeCode: definition.nodeCode,
      label: definition.label,
    }));
  }

  function statusClass(status, counts = {}) {
    if (status === "failed" || status === "attention" || counts.failed) return "is-failed";
    if (status === "running" || counts.running) return "is-running";
    if (status === "queued" || status === "retry" || counts.queued) return "is-queued";
    if (status === "completed" || counts.completed || counts.admitted) return "is-completed";
    if (counts.observed) return "is-observed";
    return "is-waiting";
  }

  function liveStatusText(live = {}) {
    const flow = live.flowStatus || "waiting";
    const snapshot = live.snapshotStatus || "";
    if (live.optionalError) return "接口异常";
    if (snapshot === "replaying" || live.batchState?.status === "retry") return "断点重试";
    if (snapshot === "blocked" || flow === "attention") return "链路阻断";
    if (snapshot === "locked_retrying" || flow === "writing") return "写入中";
    if (flow === "running") return "运行中";
    if (flow === "completed") return "已完成";
    if (flow === "baseline") return "基线完成";
    return "等待数据";
  }

  function compactStat(label, value, note = "", tone = "") {
    return `<div class="report-flow-stat ${s(tone)}"><span>${s(label)}</span><strong>${s(value ?? 0)}</strong>${note ? `<small>${s(note)}</small>` : ""}</div>`;
  }

  function batchNode(stage = {}, index = 0, total = 1) {
    const status = stage.status || "waiting";
    const text = status === "completed" ? "完成" : status === "running" ? "运行" : status === "retry" ? "重试" : status === "failed" ? "失败" : status === "queued" ? "排队" : "等待";
    const attempt = stage.attemptCount ? `${stage.attemptCount}/${stage.maxAttempts || 3}` : "";
    return `<article class="batch-rail-node ${statusClass(status)}" style="--rail-index:${index};--rail-total:${total}"><i></i><strong>${s(stage.label || stage.stationId || "批次站点")}</strong><span>${s(text)}</span>${attempt ? `<small>${s(attempt)}</small>` : ""}</article>`;
  }

  function productNode(stage = {}) {
    const counts = currentCounts(stage);
    const visible = counts.running || counts.queued || counts.failed || counts.completed || counts.admitted || 0;
    const currentText = counts.running ? `运行 ${counts.running}` : counts.queued ? `排队 ${counts.queued}` : counts.failed ? `失败 ${counts.failed}` : counts.completed ? `完成 ${counts.completed}` : counts.admitted ? `入池 ${counts.admitted}` : "当前 0";
    const history = counts.historyCompleted ? `历史 ${counts.historyCompleted}` : "";
    return `<article class="product-flow-node ${statusClass(stage.status, counts)}" data-node-code="${s(stage.nodeCode)}"><i></i><div><strong>${s(stage.label)}</strong><span>${s(currentText)}</span>${history ? `<small>${s(history)}</small>` : ""}</div><b>${s(visible)}</b></article>`;
  }

  function liveItem(item = {}) {
    return `<article class="report-attention-item"><div><strong>${s(item.title || item.productId || "商品包")}</strong><span>${s(item.actionFamily || "未锁定动作")}</span></div><b>${s(item.stageLabel || item.node || item.currentStage)}</b><small>${s(item.bucket === "running" ? "运行中" : item.bucket === "failed" ? "失败" : item.bucket === "completed" ? "已完成" : "排队中")}</small></article>`;
  }

  function pipelineHeadline(summary = {}, live = {}) {
    const total = Number(summary.productTotal ?? summary.totalItems ?? 0);
    const observed = Number(summary.observed ?? summary.observedDeposited ?? 0);
    const actions = Number(summary.actionCandidates ?? 0);
    if (!total) return live.baselineOnly ? "本批基线已建立" : "等待商品进入流水线";
    return `本批处理完成：${total}个商品，观察沉淀${observed}，动作候选${actions}`;
  }

  function pipelineLiveStrip(live, latest) {
    if (live == null) {
      return `<section class="report-flow-card"><div class="report-flow-head"><div><p class="eyebrow">LIVE PIPELINE · V22.5.6</p><h2>正在读取本批处理状态</h2></div><span class="status-badge">加载中</span></div><div class="report-flow-stats">${compactStat("商品", 0)}${compactStat("观察", 0)}${compactStat("动作候选", 0)}${compactStat("运行中", 0)}${compactStat("已入池", latest?.taskCount || 0)}${compactStat("异常", 0)}</div></section>`;
    }
    if (live.optionalError) {
      return `<section class="report-flow-card"><div class="report-flow-head"><div><p class="eyebrow">LIVE PIPELINE · V22.5.6</p><h2>流水线接口异常</h2><p>${s(live.optionalPath || "/api/view/pipeline-live")}</p></div><span class="status-badge danger">接口异常</span></div><div class="product-trend-empty">${s(live.optionalError)}</div></section>`;
    }

    const summary = live.summary || {};
    const productStages = normalizedProductStages(live);
    const batchStages = live.batchStages || live.batchState?.stationJobs || [];
    const items = (live.items || []).slice(0, 8);
    const productTotal = Number(summary.productTotal ?? summary.totalItems ?? 0);
    const observed = Number(summary.observed ?? summary.observedDeposited ?? 0);
    const actionCandidates = Number(summary.actionCandidates ?? 0);
    const productFailed = Number(summary.productFailed ?? summary.failed ?? 0);
    const batchFailed = Number(summary.batchFailed ?? 0);
    const running = productStages.reduce((sum, stage) => {
      const counts = currentCounts(stage);
      return sum + counts.running + counts.queued;
    }, 0);
    const taskAdmitted = Number(summary.taskAdmitted ?? summary.taskMapped ?? 0);
    const errors = productFailed + batchFailed;
    const statusText = liveStatusText(live);
    const batchTotal = Math.max(1, batchStages.length);

    return `<section class="report-flow-card"><div class="report-flow-head"><div><p class="eyebrow">LIVE PIPELINE · V22.5.6</p><h2>${s(pipelineHeadline(summary, live))}</h2><p>${s(live.dataVersion || "暂无数据版本")}</p></div><div class="report-flow-head-actions"><span class="status-badge">${s(statusText)}</span><button type="button" class="secondary" data-open-line>系统</button></div></div><div class="report-flow-stats">${compactStat("商品", productTotal, "本批唯一商品")}${compactStat("观察沉淀", observed, "合法终态", observed ? "is-observed" : "")}${compactStat("动作候选", actionCandidates, "进入Agent链路")}${compactStat("排队/运行", running, "实时状态", running ? "is-running" : "")}${compactStat("已入池", taskAdmitted, "正式任务")}${compactStat("异常", errors, batchFailed ? `批次 ${batchFailed}` : "商品失败", errors ? "is-failed" : "")}</div><section class="report-flow-section"><div class="report-flow-title"><h3>批次链路</h3><span>${batchStages.length ? "报表确定性处理" : "等待批次"}</span></div>${batchStages.length ? `<div class="batch-rail">${batchStages.map((stage, index) => batchNode(stage, index, batchTotal)).join("")}</div>` : `<div class="report-flow-empty">当前没有批次站点状态。</div>`}</section><section class="report-flow-section"><div class="report-flow-title"><h3>商品链路</h3><span>${productTotal}个商品</span></div><div class="product-flow-layout"><article class="product-flow-source ${productTotal ? "is-completed" : "is-waiting"}"><i></i><div><strong>商品信号</strong><span>本批准入</span></div><b>${s(productTotal)}</b></article><div class="product-flow-branches"><article class="observation-terminal ${observed ? "is-observed" : "is-waiting"}"><i></i><div><strong>观察沉淀</strong><span>不生成任务，等待新数据</span></div><b>${s(observed)}</b></article><div class="action-flow-wrap"><div class="action-flow-label"><span>动作候选 ${s(actionCandidates)}</span></div><div class="action-flow-track">${productStages.map(productNode).join("")}</div></div></div></div></section>${items.length ? `<section class="report-flow-section report-attention"><div class="report-flow-title"><h3>需要关注</h3><span>${items.length}项</span></div><div class="report-attention-list">${items.map(liveItem).join("")}</div></section>` : ""}</section>`;
  }

  function sourceCard(item) {
    const isBackup = item.priority === "backup";
    const statusClassName = isBackup ? "warning" : "good";
    const sourceStatus = item.displayStatus === "Standby" ? "待配置" : item.displayStatus || "待配置";
    const actionLabel = item.actionLabel === "Sync" ? "同步" : item.actionLabel === "Upload" ? "上传" : item.actionLabel || (isBackup ? "上传" : "同步");
    const action = isBackup
      ? `<button type="button" class="secondary" data-open-upload>${s(actionLabel)}</button>`
      : `<button type="button" data-source-sync="${s(item.sourceId)}">${s(actionLabel)}</button><button type="button" class="secondary" data-open-source-config="${s(item.sourceId)}">配置</button>`;
    return `<article class="platform-card data-source-card compact-source-card"><div class="platform-head"><div><span class="status-dot ${statusClassName}"></span><strong>${s(item.label)}</strong></div><span>${s(isBackup ? "备用" : "主链路")}</span></div><div class="platform-numbers"><div><small>状态</small><b>${s(sourceStatus)}</b></div><div><small>频率</small><b>${s(item.cadence || "按需")}</b></div></div><div class="task-actions">${action}</div></article>`;
  }

  function uploadStatusClass() {
    if (uploadState.status === "success") return "good";
    if (uploadState.status === "error") return "danger";
    if (["choosing", "uploading", "refreshing"].includes(uploadState.status)) return "warning";
    return "";
  }

  function uploadStatusCard() {
    return `<article class="product-notice" data-upload-status><strong>${s(uploadState.title)}</strong><span>${s(uploadState.detail)}</span>${uploadState.fileName ? `<span>文件：${s(uploadState.fileName)}</span>` : ""}${uploadState.dataVersion ? `<span>数据版本：${s(uploadState.dataVersion)}</span>` : ""}${uploadState.rows ? `<span>读取行数：${s(uploadState.rows)}</span>` : ""}</article>`;
  }

  function uploadSection() {
    return `<section class="page-section v102-main-section compact-upload-section"><div class="section-header"><h3>手动上传</h3><span class="status-badge ${uploadStatusClass()}">${s(uploadState.status === "idle" ? "备用入口" : uploadState.title)}</span></div><input type="file" data-manual-file-input accept=".xlsx,.xlsm,.xls,.csv,.json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv,application/json" style="display:none" />${uploadStatusCard()}<div class="report-record-list"><article class="report-record-row"><strong>Excel / CSV / JSON</strong><span>${s(uploadState.status === "uploading" ? "上传中" : "上传")}</span><span>${s(uploadState.fileName || "手动")}</span><button type="button" class="secondary" data-open-upload ${uploadState.status === "uploading" || uploadState.status === "refreshing" ? "disabled" : ""}>${s(uploadState.status === "uploading" ? "上传中" : uploadState.status === "choosing" ? "选择中" : "上传")}</button></article><article class="report-record-row"><strong>Demo</strong><span>演示</span><span>运行</span><button type="button" class="secondary" data-import-demo>运行</button></article><article class="report-record-row"><strong>Reset</strong><span>演示</span><span>清空</span><button type="button" class="secondary" data-reset-demo>清空</button></article></div></section>`;
  }

  function pageShell() {
    return `<div data-report-live>${pipelineLiveStrip(null, {})}</div><section class="page-section v102-main-section"><div class="section-header"><h3>数据源</h3><span class="status-badge">主链路</span></div><div class="platform-grid">${FALLBACK_SOURCES.filter((item) => item.priority !== "backup").map(sourceCard).join("")}</div></section>${uploadSection()}`;
  }

  function renderLoaded(root, payload, connectionPayload, livePayload) {
    const latest = latestReport(payload || {});
    const sources = connectionPayload?.sources?.length ? connectionPayload.sources : FALLBACK_SOURCES;
    const primarySources = sources.filter((item) => item.priority !== "backup");
    root.innerHTML = `<div data-report-live>${pipelineLiveStrip(livePayload, latest)}</div><section class="page-section v102-main-section"><div class="section-header"><h3>数据源</h3><span class="status-badge">主链路</span></div><div class="platform-grid">${primarySources.map(sourceCard).join("")}</div></section>${uploadSection()}`;
  }

  function setUploadStatus(next) {
    uploadState = { ...uploadState, ...next };
    const root = AppShell.view();
    if (!root) return;
    const section = root.querySelector(".compact-upload-section");
    if (section) section.outerHTML = uploadSection();
  }

  function uploadSummary(result, file) {
    const meta = result?.uploadMeta || {};
    const dataVersion = result?.dataVersion || result?.syncState?.latestDataVersion || result?.pipelineSync?.dataVersions?.[0] || result?.v104ImportTaskSync?.latestDataVersion || "";
    const rows = meta.totalRows ?? result?.rowCount ?? result?.v104ImportTaskSync?.rowCount ?? 0;
    return { title: "上传已提交", detail: "报表已进入数据中台；页面会自动刷新批次与商品链路。", fileName: file?.name || "报表", rows, dataVersion };
  }

  function parseCsv(text) {
    const rows = [];
    let row = [];
    let field = "";
    let inQuotes = false;
    for (let i = 0; i < text.length; i += 1) {
      const c = text[i];
      const next = text[i + 1];
      if (c === '"') {
        if (inQuotes && next === '"') { field += '"'; i += 1; }
        else inQuotes = !inQuotes;
      } else if (c === "," && !inQuotes) {
        row.push(field); field = "";
      } else if ((c === "\n" || c === "\r") && !inQuotes) {
        if (c === "\r" && next === "\n") i += 1;
        row.push(field);
        if (row.some((cell) => String(cell).trim() !== "")) rows.push(row);
        row = []; field = "";
      } else field += c;
    }
    row.push(field);
    if (row.some((cell) => String(cell).trim() !== "")) rows.push(row);
    const headers = (rows.shift() || []).map((header) => String(header || "").trim());
    if (!headers.length) return [];
    return rows.map((values) => {
      const item = {};
      headers.forEach((header, index) => { if (header) item[header] = values[index] ?? ""; });
      return item;
    }).filter((item) => Object.values(item).some((value) => String(value).trim() !== ""));
  }

  async function parseUploadFile(file) {
    const text = await file.text();
    if (/\.json$/i.test(file.name || "")) {
      const payload = JSON.parse(text);
      const rows = Array.isArray(payload) ? payload : payload.rows || payload.data || [];
      if (!Array.isArray(rows)) throw new Error("JSON 需要是数组，或包含 rows/data 数组。");
      return rows.filter((item) => item && typeof item === "object");
    }
    return parseCsv(text);
  }

  async function requestPipelineLive() {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort("pipeline_live_timeout"), 7000) : null;
    try {
      const response = await fetch("/api/view/pipeline-live?limit=100", {
        cache: "no-store",
        headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
        signal: controller?.signal,
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.json();
    } catch (error) {
      return { optionalError: error?.message || String(error || "流水线接口异常"), optionalPath: "/api/view/pipeline-live" };
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function flowIsActive(live = {}) {
    if (["running", "writing"].includes(live.flowStatus) || ["retry", "locked_retrying", "replaying"].includes(live.snapshotStatus)) return true;
    const stages = [...(live.stages || []), ...(live.batchStages || []), ...(live.batchState?.stationJobs || [])];
    return stages.some((stage) => {
      const counts = currentCounts(stage);
      return ["running", "queued", "retry"].includes(stage.status) || counts.running > 0 || counts.queued > 0;
    });
  }

  function stopPoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
  }

  function schedulePoll(ctx, delay) {
    stopPoll();
    pollTimer = setTimeout(() => refreshLiveOnly(ctx), delay);
  }

  async function refreshLiveOnly(ctx) {
    if (!ctx.isCurrent()) return;
    if (document.hidden) {
      schedulePoll(ctx, 5000);
      return;
    }
    latestLive = await requestPipelineLive();
    if (!ctx.isCurrent()) return;
    const container = AppShell.view()?.querySelector("[data-report-live]");
    if (container) container.innerHTML = pipelineLiveStrip(latestLive, {});
    schedulePoll(ctx, flowIsActive(latestLive) ? 2500 : 10000);
  }

  async function hydrate(ctx) {
    const root = AppShell.view();
    if (!root || !ctx.isCurrent()) return;
    const [payload, connectionPayload, livePayload] = await Promise.all([AppApi.report?.(), AppApi.dataSourceConnections?.(), requestPipelineLive()]);
    latestLive = livePayload;
    if (ctx.isCurrent()) {
      renderLoaded(root, payload, connectionPayload, livePayload);
      schedulePoll(ctx, flowIsActive(livePayload) ? 2500 : 10000);
    }
  }

  window.ReportPage = {
    route: "data-check",
    title: "AI 经营链路",
    async render() { return pageShell(); },
    mount(ctx) {
      hydrate(ctx).catch((error) => console.error("[report] async hydrate failed", error));
      ctx.delegate("[data-source-sync]", "click", async (event, target) => {
        const sourceId = target.getAttribute("data-source-sync") || "erp";
        const oldText = target.textContent;
        target.disabled = true;
        target.textContent = "同步中";
        setUploadStatus({ status: "refreshing", title: "数据源同步中", detail: `${sourceId.toUpperCase()} 正在同步。` });
        const result = await AppApi.syncDataSource(sourceId);
        if (!result) {
          target.disabled = false;
          target.textContent = oldText;
          setUploadStatus({ status: "error", title: "接口异常", detail: "数据源同步没有返回可用结果。" });
          return;
        }
        await AppApi.refreshAfterDataImport(result);
        lastImportSync = result?.v104ImportTaskSync || window.AppApi?.status?.lastImportSync || null;
        target.disabled = false;
        target.textContent = oldText;
        setUploadStatus({ status: "success", title: "同步已提交", detail: "数据源同步已进入后台队列。" });
        hydrate(ctx).catch(() => null);
      });
      ctx.delegate("[data-open-source-config]", "click", (event, target) => {
        const sourceId = target.getAttribute("data-open-source-config") || "数据源";
        setUploadStatus({ status: "idle", title: "配置入口", detail: `${sourceId.toUpperCase()} 配置功能待接入。` });
      });
      ctx.delegate("[data-open-upload]", "click", (event, target) => {
        if (target.disabled) return;
        setUploadStatus({ status: "choosing", title: "选择文件中", detail: "请在系统文件选择器中选择 Excel / CSV / JSON 报表。" });
        AppShell.view()?.querySelector("[data-manual-file-input]")?.click();
      });
      ctx.delegate("[data-import-demo]", "click", async (event, target) => {
        target.disabled = true;
        target.textContent = "运行中";
        setUploadStatus({ status: "refreshing", title: "Demo运行中", detail: "演示数据正在导入。" });
        try {
          const result = await AppApi.importMockAlerts();
          await AppApi.refreshAfterDataImport(result);
          lastImportSync = result?.v104ImportTaskSync || window.AppApi?.status?.lastImportSync || null;
          setUploadStatus({ status: "success", title: "Demo已提交", detail: "演示数据已进入后台队列。" });
        } finally {
          target.disabled = false;
          target.textContent = "运行";
          hydrate(ctx).catch(() => null);
        }
      });
      ctx.delegate("[data-reset-demo]", "click", async (event, target) => {
        if (!window.confirm("清空演示数据？")) return;
        const oldText = target.textContent;
        target.disabled = true;
        target.textContent = "清空中";
        setUploadStatus({ status: "refreshing", title: "清空中", detail: "正在清空演示数据。" });
        await AppApi.resetRuntimeData(true);
        lastImportSync = null;
        await AppApi.refreshAfterDataImport({ v104ImportTaskSync: null });
        setUploadStatus({ status: "success", title: "已清空", detail: "演示环境已清空，可以重新上传报表。", fileName: "", rows: 0, dataVersion: "" });
        target.disabled = false;
        target.textContent = oldText;
        hydrate(ctx).catch(() => null);
      });
      ctx.delegate("[data-manual-file-input]", "change", async (event, target) => {
        const file = target.files?.[0];
        if (!file) {
          setUploadStatus({ status: "idle", title: "未选择文件", detail: "没有读取到文件。" });
          return;
        }
        setUploadStatus({ status: "uploading", title: "上传解析中", detail: `${file.name} 正在上传。`, fileName: file.name, rows: 0, dataVersion: "" });
        try {
          let result = await AppApi.uploadReportFile?.(file, "auto", "manual_upload");
          if (!result && /\.(csv|json)$/i.test(file.name || "")) {
            const rows = await parseUploadFile(file);
            if (!rows.length) throw new Error("没有读取到有效数据行。");
            result = await AppApi.confirmReportImport("auto", rows, {}, "manual_upload");
          }
          if (!result) throw new Error("导入接口不可用");
          setUploadStatus({ status: "refreshing", title: "上传成功，刷新链路", detail: "报表已提交，正在刷新批次和商品链路。", ...uploadSummary(result, file) });
          await AppApi.refreshAfterDataImport(result);
          lastImportSync = result?.v104ImportTaskSync || window.AppApi?.status?.lastImportSync || null;
          setUploadStatus({ status: "success", ...uploadSummary(result, file) });
          hydrate(ctx).catch(() => null);
        } catch (error) {
          setUploadStatus({ status: "error", title: "上传失败", detail: error?.message || String(error || "上传失败"), fileName: file.name });
        } finally {
          target.value = "";
        }
      });
      ctx.delegate("[data-open-line]", "click", () => AppRouter.navigate("system-status"));
      const onSync = (event) => {
        lastImportSync = event.detail?.sync || lastImportSync;
        hydrate(ctx).catch(() => null);
      };
      const onVisible = () => {
        if (!document.hidden && ctx.isCurrent()) refreshLiveOnly(ctx).catch(() => null);
      };
      window.addEventListener("v104-import-sync", onSync);
      window.addEventListener("v208-read-model-refresh", onSync);
      window.addEventListener("api-cache-updated", onSync);
      document.addEventListener("visibilitychange", onVisible);
      ctx.addCleanup(() => {
        stopPoll();
        window.removeEventListener("v104-import-sync", onSync);
        window.removeEventListener("v208-read-model-refresh", onSync);
        window.removeEventListener("api-cache-updated", onSync);
        document.removeEventListener("visibilitychange", onVisible);
      });
    },
  };
})();
