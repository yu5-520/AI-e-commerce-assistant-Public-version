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

  async function loadExperience() {
    const response=await fetch("/api/ops/experience-overview",{cache:"no-cache"});
    if(!response.ok)throw Error("经验数据暂不可用");
    return response.json();
  }
  function renderExperience(data) {
    if(!data)return '<section class="kc-card"><h3>运行经验</h3><p role="status">经验数据暂不可用，请刷新重试。</p></section>';
    const labels={candidate:"待审核",approved:"已审核",enabled:"已启用",disabled:"已停用",superseded:"已替代",seed:"初始化"};
    const domains={experience_knowledge:"经验知识",decision_patterns:"判断模式",strategy_outcomes:"方案结果",operation_patterns:"执行经验",evaluation_results:"评测结果"};
    const groups=data.groups || [];
    return `<section class="kc-card kc-span-2"><div class="kc-card-head"><h3>运行经验</h3><span>${s(data.totalCount)} 条</span></div>${!data.initialized?'<p>经验库尚未初始化。</p>':""}<div class="kc-state-grid">${Object.entries(labels).map(([key,label])=>{const selected=groups.filter(g=>g.status===key),count=selected.reduce((n,g)=>n+g.count,0);return `<details><summary><strong>${s(count)}</strong> ${label}</summary><p>条数 = 当前状态下各经验域记录数之和。</p>${selected.map(g=>`<p>${s(domains[g.domain] || g.domain)} · ${g.sourceType==="seed"?"初始化来源":"运行来源"}：${s(g.count)}</p>`).join("") || '<p>暂无记录</p>'}</details>`;}).join("")}</div><details><summary>最近经验与来源任务</summary><div class="kc-table-wrap"><table class="kc-table"><thead><tr><th>经验域</th><th>状态</th><th>来源</th><th>任务</th></tr></thead><tbody>${(data.recent || []).map(item=>`<tr><td><details><summary>${s(domains[item.domain] || item.domain)}</summary><code>${s(item.experienceId)}</code><p>${s(item.sourceHash)}</p></details></td><td>${s(labels[item.status] || item.status)}</td><td>${item.sourceType==="seed"?"初始化":"运行"}</td><td>${item.sourceType==="runtime" && item.sourceTaskId?`<button data-kc-task="${s(item.sourceTaskId)}">查看任务</button>`:"—"}</td></tr>`).join("") || '<tr><td colspan="4">暂无经验记录</td></tr>'}</tbody></table></div><p>最近 ${s(data.recentLimit)} 条；状态计数覆盖全部记录。初始化来源不代表真实执行成果。</p></details><details><summary>计数与版本依据</summary><code>${s(data.countFormula)}</code><p>${s(data.contentHash)}</p></details></section>`;
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
      const results=await Promise.allSettled([loadOverview(),loadExperience()]);
      const data=results[0].status==="fulfilled"?results[0].value:null;
      const experience=results[1].status==="fulfilled"?results[1].value:null;
      return `<section class="kc-hero"><div><p class="eyebrow">V26.11</p><h2>知识与经验</h2><p>查看可用经验、审核进度与任务来源。</p></div><button type="button" data-kc-refresh>刷新</button></section><section class="kc-grid">${renderExperience(experience)}</section><details class="page-section"><summary>知识检索与评测明细</summary>${data?`<section class="kc-grid">${renderHealth(data.knowledgeHealth || {})}${renderRetrievalMetrics(data.retrievalMetrics || {})}${renderEval(data.evalSets || [],data.evalRuns || [])}${renderRevisions(data.recentRevisions || [])}<details class="kc-card"><summary>索引与治理依据</summary>${renderIndex(data.index || {})}${renderGovernance(data.governance || {})}</details></section>`:'<p role="status">检索数据暂不可用，请刷新重试。</p>'}</details>`;
    },
    mount(ctx) {
      ctx.delegate("[data-kc-task]", "click", (_,node)=>AppRouter.navigate("task-report",{taskId:node.dataset.kcTask}));
      ctx.delegate("[data-kc-refresh]", "click", () => AppRouter.schedule("knowledge-center-refresh"));
    },
  };
})();
