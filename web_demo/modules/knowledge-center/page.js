(function () {
  const s = (value) => AppShell.escape(value ?? "-");

  async function loadOverview() {
    const response = await fetch("/api/system/knowledge-center/overview", {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`RAG知识中心接口异常：${response.status}`);
    return response.json();
  }

  function shortHash(value) {
    const raw = String(value || "").replace(/^sha256:/, "");
    return raw ? `${raw.slice(0, 12)}…${raw.slice(-6)}` : "-";
  }

  function pct(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "待采样";
    return `${(Number(value) * 100).toFixed(1)}%`;
  }

  function metric(label, value, note) {
    return `<article class="kc-metric"><span>${s(label)}</span><strong>${s(value)}</strong><small>${s(note)}</small></article>`;
  }

  function stateLabel(state) {
    return ({
      pending_review: "待审核",
      active: "有效",
      stale: "已过期",
      re_review: "待复审",
      superseded: "已替代",
      deprecated: "已弃用",
      archived: "已归档",
      rejected: "已拒绝",
    })[state] || state || "未知";
  }

  function renderHealth(health = {}) {
    const states = health.states || {};
    const order = ["active", "pending_review", "re_review", "stale", "superseded", "deprecated", "archived", "rejected"];
    return `<section class="kc-card"><div class="kc-card-head"><div><p class="eyebrow">KNOWLEDGE HEALTH</p><h3>知识生命周期</h3></div><span class="kc-chip">${s(health.totalRevisionStateCount || 0)} revisions</span></div><div class="kc-state-grid">${order.map((key) => `<article><strong>${s(states[key] || 0)}</strong><span>${s(stateLabel(key))}</span></article>`).join("")}</div></section>`;
  }

  function renderIndex(index = {}) {
    const head = index.head || {};
    return `<section class="kc-card"><div class="kc-card-head"><div><p class="eyebrow">INDEX MANIFEST</p><h3>当前知识索引</h3></div><span class="kc-chip">${s(index.indexVersion || "未构建")}</span></div><div class="kc-kv">
      <div><span>Manifest Hash</span><code title="${s(index.manifestHash)}">${s(shortHash(index.manifestHash))}</code></div>
      <div><span>Knowledge Snapshot</span><code title="${s(index.knowledgeSnapshotHash)}">${s(shortHash(index.knowledgeSnapshotHash))}</code></div>
      <div><span>Active Revisions</span><strong>${s(index.activeCardCount ?? index.cardCount ?? 0)}</strong></div>
      <div><span>Previous Head</span><code>${s(shortHash(head.previousManifestHash))}</code></div>
      <div><span>Rollback Pin</span><strong>${head.rollbackPinned ? "已固定" : "未固定"}</strong></div>
      <div><span>Index Engine</span><strong>${s(index.indexEngine || "sqlite_structured_v1")}</strong></div>
    </div></section>`;
  }

  function renderRetrievalMetrics(snapshot = {}) {
    const m = snapshot.metrics || {};
    return `<section class="kc-card kc-span-2"><div class="kc-card-head"><div><p class="eyebrow">V25.13 · RETRIEVAL OBSERVABILITY</p><h3>RAG量化</h3></div><span class="kc-chip">${s(m.observationCount || 0)} receipts</span></div><div class="kc-metric-grid">
      ${metric("Zero-hit", pct(m.zeroHitRate), "无召回率")}
      ${metric("平均候选", m.averageCandidateCount ?? "待采样", "生命周期过滤前")}
      ${metric("平均有效", m.averageEligibleCount ?? "待采样", "治理过滤后")}
      ${metric("平均命中", m.averageMatchedCount ?? "待采样", "最终Revision")}
      ${metric("P50延迟", m.latencyMsP50 == null ? "待采样" : `${m.latencyMsP50} ms`, "Retrieval")}
      ${metric("P95延迟", m.latencyMsP95 == null ? "待采样" : `${m.latencyMsP95} ms`, "Retrieval")}
    </div><p class="kc-note">Hit@K 与 MRR 必须绑定 EvalSet 才有意义，不用生产流量的无标签数据伪造准确率。</p></section>`;
  }

  function renderEval(evalSets = [], evalRuns = []) {
    const latest = evalRuns[0] || {};
    const metrics = latest.metrics || {};
    return `<section class="kc-card kc-span-2"><div class="kc-card-head"><div><p class="eyebrow">V25.14 · EVAL AUTHORITY</p><h3>Eval 与回归</h3></div><span class="kc-chip">${s(evalSets.length)} EvalSets · ${s(evalRuns.length)} Runs</span></div><div class="kc-eval-grid">
      <div><span>最新角色</span><strong>${s(latest.runRole || "暂无")}</strong></div>
      <div><span>Hit@3</span><strong>${pct(metrics.hitAt3)}</strong></div>
      <div><span>MRR</span><strong>${metrics.mrr == null ? "待评测" : s(metrics.mrr)}</strong></div>
      <div><span>Zero-hit</span><strong>${pct(metrics.zeroHitRate)}</strong></div>
      <div><span>Stale Leak</span><strong>${s(metrics.staleLeakCount ?? 0)}</strong></div>
      <div><span>Superseded Leak</span><strong>${s(metrics.supersededLeakCount ?? 0)}</strong></div>
    </div><div class="kc-table-wrap"><table class="kc-table"><thead><tr><th>EvalSet</th><th>版本</th><th>Cases</th><th>Hash</th></tr></thead><tbody>${evalSets.length ? evalSets.slice(0, 8).map((item) => `<tr><td>${s(item.evalSetId)}</td><td>${s(item.evalSetVersion)}</td><td>${s((item.cases || []).length)}</td><td><code>${s(shortHash(item.evalSetHash))}</code></td></tr>`).join("") : `<tr><td colspan="4">暂无 EvalSet。先建立人工标注集，再谈 Hit@K。</td></tr>`}</tbody></table></div></section>`;
  }

  function renderRevisions(items = []) {
    return `<section class="kc-card kc-span-2"><div class="kc-card-head"><div><p class="eyebrow">IMMUTABLE REVISIONS</p><h3>知识资产</h3></div><span class="kc-chip">最近 ${s(items.length)} 条</span></div><div class="kc-table-wrap"><table class="kc-table"><thead><tr><th>状态</th><th>Case</th><th>Revision</th><th>来源任务</th><th>Content Hash</th></tr></thead><tbody>${items.length ? items.map((item) => `<tr><td><span class="kc-state kc-${s(item.lifecycleState)}">${s(stateLabel(item.lifecycleState))}</span></td><td>${s(item.caseId)}</td><td><code>${s(shortHash(item.revisionId))}</code></td><td>${s(item.sourceTaskId)}</td><td><code>${s(shortHash(item.contentHash))}</code></td></tr>`).join("") : `<tr><td colspan="5">当前没有知识 Revision。</td></tr>`}</tbody></table></div></section>`;
  }

  function renderGovernance(governance = {}) {
    const checks = [
      ["前端直接改数据库", governance.directDatabaseMutationAllowed === false ? "禁止" : "异常"],
      ["Active原地编辑", governance.activeRevisionInPlaceEditAllowed === false ? "禁止" : "异常"],
      ["回滚权限", governance.rollbackAuthority || "V25.12_INDEX_HEAD"],
      ["EvalSet权限", governance.evalSetAuthority || "V25.14_IMMUTABLE_EVAL_SET"],
      ["物理RAG替换", governance.physicalRagProviderReplaced === false ? "否" : "异常"],
      ["新Agent Runtime", governance.newAgentRuntimeIntroduced === false ? "否" : "异常"],
    ];
    return `<section class="kc-card"><div class="kc-card-head"><div><p class="eyebrow">GOVERNANCE BOUNDARY</p><h3>治理边界</h3></div></div><div class="kc-boundary">${checks.map(([label, value]) => `<div><span>${s(label)}</span><strong>${s(value)}</strong></div>`).join("")}</div></section>`;
  }

  window.KnowledgeCenterPage = {
    route: "knowledge-center",
    title: "RAG知识中心",
    async render() {
      const data = await loadOverview();
      return `<section class="kc-hero"><div><p class="eyebrow">V25.13—V25.15 · RAG KNOWLEDGE CENTER</p><h2>中文 RAG 知识中心</h2><p>把知识 Revision、Index Manifest、Retrieval Receipt、量化指标与 BASE/TARGET Eval 放到同一个可追溯操作面。</p></div><div class="kc-hero-hash"><span>Current Manifest</span><code>${s(shortHash(data.index?.manifestHash))}</code><button type="button" data-kc-refresh>刷新知识状态</button></div></section>
      <section class="kc-grid">
        ${renderIndex(data.index || {})}
        ${renderGovernance(data.governance || {})}
        ${renderHealth(data.knowledgeHealth || {})}
        ${renderRetrievalMetrics(data.retrievalMetrics || {})}
        ${renderEval(data.evalSets || [], data.evalRuns || [])}
        ${renderRevisions(data.recentRevisions || [])}
      </section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-kc-refresh]", "click", () => AppRouter.schedule("knowledge-center-refresh"));
    },
  };
})();
