(function () {
  const s = (value) => AppShell.escape(value ?? "-");

  async function loadJson(path, fallback = null) {
    try {
      const response = await fetch(path, {
        method: "GET",
        headers: { Accept: "application/json"},
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.json();
    } catch (error) {
      console.warn(`[system-status] fallback for ${path}`, error);
      return fallback;
    }
  }

  async function postJson(path, body = null) {
    const response = await fetch(path, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json"},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function pill(text, tone = "neutral") { return `<span class="system-pill ${tone}">${s(text)}</span>`; }
  function metric(label, value, tone = "neutral") { return `<article class="system-metric"><span>${s(label)}</span><strong>${s(value)}</strong>${pill(tone === "good" ? "正常" : tone === "warn" ? "关注" : tone === "danger" ? "阻断" : "状态", tone)}</article>`; }
  function row(title, value, tone = "neutral") { return `<article class="system-layer-row"><div><strong>${s(title)}</strong><span>${s(value)}</span></div>${pill(tone, ["正常", "已签封", "一致"].includes(tone) ? "good" : ["关注", "未签封"].includes(tone) ? "warn" : tone === "阻断" ? "danger" : "neutral")}</article>`; }

  function shortHash(value) {
    const raw = String(value || "");
    return raw ? raw.replace(/^sha256:/, "").slice(0, 16) : "-";
  }

  function statusTone(status) {
    if (["ok", "healthy", "passed", "verified"].includes(status)) return "good";
    if (["warning", "visible_empty", "degraded", "unsealed"].includes(status)) return "warn";
    if (["object_sync_failed", "dirty_runtime_residue", "failed", "verification_failed", "manifest_missing"].includes(status)) return "danger";
    return "neutral";
  }

  function statusLabel(status) {
    if (status === "verified") return "发布签封通过";
    if (status === "unsealed") return "开发态未签封";
    if (status === "manifest_missing") return "缺少发布清单";
    if (status === "verification_failed") return "发布身份不匹配";
    if (status === "object_sync_failed") return "经营对象未入库";
    if (status === "dirty_runtime_residue") return "清空不完整";
    if (status === "visible_empty") return "当前账号不可见";
    if (status === "degraded") return "站点降级";
    if (status === "passed") return "巡检通过";
    if (status === "warning") return "巡检关注";
    if (status === "failed") return "巡检失败";
    return "运行正常";
  }

  function countMatch(identity, verifiedKey, totalKey) {
    const verified = Number(identity?.[verifiedKey] || 0);
    const total = Number(identity?.[totalKey] || 0);
    return total > 0 && verified === total;
  }

  function proofSetClean(identity = {}) {
    return Number(identity.extraRuntimeFileCount || 0) === 0
      && Number(identity.extraAttestedFileCount || 0) === 0
      && Number(identity.extraTestEvidenceFileCount || 0) === 0
      && Number(identity.manifestRuntimeFileOutsidePolicyCount || 0) === 0
      && Number(identity.manifestAttestedFileOutsidePolicyCount || 0) === 0
      && Number(identity.manifestTestEvidenceFileMissingCount || 0) === 0;
  }

  function renderReleaseIdentity(identity = {}) {
    const verified = identity.verified === true;
    const workerMatch = identity.workerReleaseMatch === true;
    const semanticReady = identity.evidenceSemanticVerified === true;
    const buildPythonReady = identity.buildPythonVersion === "3.11.9";
    const runtimePythonReady = identity.runtimePythonCompatible === true
      && identity.runtimePythonVersion === identity.buildPythonVersion;
    const environmentReady = identity.runtimeEnvironmentMatch === true
      && identity.runtimePipFreezeHash
      && identity.runtimePipFreezeHash === identity.pipFreezeHash;
    const exactProofSet = proofSetClean(identity);
    const attestedReady = countMatch(identity, "verifiedAttestedFileCount", "attestedFileCount")
      && Number(identity.extraAttestedFileCount || 0) === 0
      && Number(identity.manifestAttestedFileOutsidePolicyCount || 0) === 0;
    const evidenceReady = semanticReady
      && countMatch(identity, "verifiedTestEvidenceFileCount", "testEvidenceFileCount")
      && identity.testRunHash
      && identity.testRunHash === identity.calculatedTestRunHash
      && Number(identity.extraTestEvidenceFileCount || 0) === 0
      && Number(identity.manifestTestEvidenceFileMissingCount || 0) === 0;
    return `<section class="page-section system-section"><div class="section-header"><h3>Release DNA</h3>${pill(statusLabel(identity.status), statusTone(identity.status))}</div><div class="system-layer-list">
      ${row("Source Commit", identity.sourceCommit || "未签封", verified ? "正常" : "关注")}
      ${row("Release Hash", shortHash(identity.releaseHash), verified ? "已签封" : "未签封")}
      ${row("Manifest Hash", shortHash(identity.manifestHash), verified ? "正常" : "关注")}
      ${row("Test Run Hash", shortHash(identity.testRunHash), evidenceReady ? "正常" : "阻断")}
      ${row("证据语义", semanticReady ? "与Commit和环境一致" : "证据陈旧或跨版本", semanticReady ? "正常" : "阻断")}
      ${row("Dependency Lock", shortHash(identity.dependencyLockHash), identity.dependencyLockHash ? "正常" : "关注")}
      ${row("构建Python", identity.buildPythonVersion || "未记录", buildPythonReady ? "正常" : "阻断")}
      ${row("运行Python", identity.runtimePythonVersion || "未识别", runtimePythonReady ? "正常" : "阻断")}
      ${row("签封依赖Hash", shortHash(identity.pipFreezeHash), identity.pipFreezeHash ? "正常" : "关注")}
      ${row("运行依赖Hash", shortHash(identity.runtimePipFreezeHash), environmentReady ? "一致" : "阻断")}
      ${row("运行环境", environmentReady ? "与Release一致" : "依赖或Python漂移", environmentReady ? "正常" : "阻断")}
      ${row("运行文件", `${identity.verifiedFileCount || 0}/${identity.manifestFileCount || 0}`, verified ? "正常" : "关注")}
      ${row("合同证明文件", `${identity.verifiedAttestedFileCount || 0}/${identity.attestedFileCount || 0}`, attestedReady ? "正常" : "阻断")}
      ${row("灰度证据文件", `${identity.verifiedTestEvidenceFileCount || 0}/${identity.testEvidenceFileCount || 0}`, evidenceReady ? "正常" : "阻断")}
      ${row("运行目录", identity.runtimeRoot || "-", verified ? "正常" : "关注")}
      ${row("禁止文件", identity.forbiddenPathViolationCount ? `${identity.forbiddenPathViolationCount}个违规` : "0个违规", identity.forbiddenPathViolationCount ? "阻断" : "正常")}
      ${row("额外运行文件", identity.extraRuntimeFileCount ? `${identity.extraRuntimeFileCount}个` : "0个", identity.extraRuntimeFileCount ? "阻断" : "正常")}
      ${row("额外合同证明", identity.extraAttestedFileCount ? `${identity.extraAttestedFileCount}个` : "0个", identity.extraAttestedFileCount ? "阻断" : "正常")}
      ${row("额外灰度证据", identity.extraTestEvidenceFileCount ? `${identity.extraTestEvidenceFileCount}个` : "0个", identity.extraTestEvidenceFileCount ? "阻断" : "正常")}
      ${row("证明集合", exactProofSet ? "与Manifest完全一致" : "存在缺失或越权文件", exactProofSet ? "正常" : "阻断")}
      ${row("API / Worker", workerMatch ? "同一Release Hash" : verified ? "不一致" : "未签封", workerMatch ? "一致" : verified ? "阻断" : "关注")}
    </div></section>`;
  }

  function renderHardInterface(version = {}, pipeline = {}) {
    const hard = version.agentHardInterface || pipeline.agentRuntimeIntegrity || {};
    const versions = version.runtimeVersions || pipeline.runtimeVersions || {};
    const background = pipeline.backgroundWorker || {};
    const config = background.config || {};
    const state = background.state || {};
    const sealed = hard.hardInterface === true && hard.fallbackAllowed === false;
    const running = state.running === true;
    return `<section class="page-section system-section"><div class="section-header"><h3>Agent硬接口</h3>${pill(sealed && running ? "运行正常" : "关注", sealed && running ? "good" : "warn")}</div><div class="system-layer-list">
      ${row("执行模式", hard.executionMode || config.agentExecutionMode || "未识别", sealed ? "正常" : "关注")}
      ${row("Agent1输入", hard.agent1RuntimeSource || config.agent1RuntimeSource || pipeline.agent1RuntimeSource || "未识别", /agent1InputRef/.test(hard.agent1RuntimeSource || config.agent1RuntimeSource || pipeline.agent1RuntimeSource || "") ? "正常" : "阻断")}
      ${row("Agent2输入", hard.agent2RuntimeSource || config.agent2RuntimeSource || pipeline.agent2RuntimeSource || "未识别", /agent2InputRef/.test(hard.agent2RuntimeSource || config.agent2RuntimeSource || pipeline.agent2RuntimeSource || "") ? "正常" : "阻断")}
      ${row("传输系统", versions.agentInputTransport || "未识别", versions.agentInputTransport ? "正常" : "关注")}
      ${row("Token系统", versions.agentTokenRuntime || "未识别", versions.agentTokenRuntime ? "正常" : "关注")}
      ${row("后台Worker", running ? `${background.version || "-"} · PID ${state.processId || "-"}` : `${background.version || "-"} · stopped`, running ? "正常" : "阻断")}
      ${row("未投影输入", pipeline.unprojectedProviderInputAllowed === false || hard.unprojectedProviderInputAllowed === false ? "禁止" : "未封口", pipeline.unprojectedProviderInputAllowed === false || hard.unprojectedProviderInputAllowed === false ? "正常" : "阻断")}
      ${row("回退读取", hard.fallbackAllowed === false ? "禁止" : "允许", hard.fallbackAllowed === false ? "正常" : "阻断")}
    </div></section>`;
  }

  function renderStationHealth(health = {}) {
    const stations = health.stations || [];
    return `<section class="page-section system-section"><div class="section-header"><h3>站点巡检</h3>${pill(statusLabel(health.status), statusTone(health.status))}</div><div class="system-layer-list">
      ${stations.map((item) => row(`${item.title || item.stationId}`, `${item.stage || "-"} · ${item.nextStation || "终点"}`, item.status === "healthy" ? "正常" : "关注")).join("") || "<p>暂无站点注册。</p>"}
    </div><div class="dashboard-linked-actions" style="margin-top:16px"><button type="button" data-run-ops-train>运行运维火车</button><button type="button" class="secondary" data-open-stations>查看站点接口</button></div></section>`;
  }

  function renderTableCounts(counts = {}) {
    return Object.entries(counts).map(([name, value]) => row(name, value, value > 0 ? "正常" : "关注")).join("") || "<p>暂无运行态表数据。</p>";
  }

  window.SystemStatusPage = {
    route: "system-status",
    title: "系统状态",
    _opsTrainResult: null,
    async render() {
      const [health, db, stationHealth, version, pipeline, identity] = await Promise.all([
        loadJson("/api/health", {}),
        loadJson("/api/system/db-status", {}),
        loadJson("/api/ops/stations/health", {}),
        loadJson("/api/version", {}),
        loadJson("/api/system/agent-pipeline-status", {}),
        loadJson("/api/system/release-identity", {}),
      ]);
      const apiVersion = version?.version || health?.version || "22.4.0";
      const hard = version?.agentHardInterface || pipeline?.agentRuntimeIntegrity || {};
      const releaseReady = identity?.verified === true && identity?.workerReleaseMatch === true;
      const hardReady = hard?.hardInterface === true && hard?.fallbackAllowed === false;
      const dependencyReady = Boolean(identity?.dependencyLockHash)
        && identity?.runtimePythonCompatible === true
        && identity?.runtimeEnvironmentMatch === true;
      const proofReady = identity?.evidenceSemanticVerified === true
        && countMatch(identity, "verifiedAttestedFileCount", "attestedFileCount")
        && countMatch(identity, "verifiedTestEvidenceFileCount", "testEvidenceFileCount")
        && identity?.testRunHash === identity?.calculatedTestRunHash
        && proofSetClean(identity);
      const tableCounts = db?.tableCounts || db?.runtimeDiagnostics?.tableCounts || {};
      return `<section class="system-hero"><div><p class="eyebrow">SYSTEM STATUS · V22.4</p><h2>系统状态</h2><p>Release Hash定位代码，环境Hash定位Python依赖，证据语义绑定Commit与灰度测试链。</p></div><div class="system-hero-side"><span>当前版本</span><strong>${s(apiVersion)}</strong><small>${s(releaseReady && dependencyReady && proofReady ? "发布DNA一致" : statusLabel(identity?.status))}</small></div></section>
      <section class="system-metric-grid">
        ${metric("Release身份", releaseReady ? "一致" : "待检查", releaseReady ? "good" : identity?.required ? "danger" : "warn")}
        ${metric("运行环境", dependencyReady ? "完全一致" : "待检查", dependencyReady ? "good" : "danger")}
        ${metric("灰度证据", proofReady ? "语义签封" : "待检查", proofReady ? "good" : "danger")}
        ${metric("Agent接口", hardReady ? "已封口" : "待检查", hardReady ? "good" : "warn")}
      </section>
      ${renderReleaseIdentity(identity)}
      ${renderHardInterface(version, pipeline)}
      ${renderStationHealth(stationHealth)}
      <section class="page-section system-section"><div class="section-header"><h3>运行态表计数</h3>${pill(db?.database?.type || "sqlite", "good")}</div><div class="system-layer-list">${renderTableCounts(tableCounts)}</div></section>
      <div class="dashboard-linked-actions"><button type="button" data-system-refresh>刷新身份</button><button type="button" class="secondary" data-clear-runtime>清空演示环境</button></div>`;
    },
    mount(ctx) {
      ctx.delegate("[data-system-refresh]", "click", () => AppRouter.schedule("system-status-refresh"));
      ctx.delegate("[data-open-stations]", "click", () => window.open("/api/stations", "_blank"));
      ctx.delegate("[data-run-ops-train]", "click", async (_, node) => {
        node.disabled = true;
        node.textContent = "巡检中";
        try { this._opsTrainResult = await postJson("/api/ops/train/run", { mode: "contract" }); }
        catch (error) { console.error(error); }
        AppRouter.schedule("ops-train-run");
      });
      ctx.delegate("[data-clear-runtime]", "click", async (_, node) => {
        if (!window.confirm("清空演示运行态？发布代码和Release身份不会被删除。")) return;
        node.disabled = true;
        node.textContent = "清空中";
        try { await window.AppApi?.resetRuntimeData?.(true); }
        catch (error) { console.error(error); }
        AppRouter.schedule("system-clear-runtime");
      });
    },
  };
})();
