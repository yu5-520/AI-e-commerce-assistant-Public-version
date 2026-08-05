(function () {
  const s = (value) => AppShell.escape(value ?? "-");

  async function loadAudits(params = {}) {
    const query = new URLSearchParams();
    if (params.action) query.set("action", params.action);
    if (params.targetKey) query.set("target_key", params.targetKey);
    query.set("limit", String(params.limit || 80));
    const response = await fetch(`/api/architecture/v7/config-audits?${query.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  async function compareAudit(auditId) {
    const response = await fetch(`/api/architecture/v7/config-audits/${encodeURIComponent(auditId)}/compare`, {
      method: "GET",
      headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const data = await response.json();
    const diffText = (data.diff || []).filter((item) => item.changed).slice(0, 8).map((item) => `${item.key}: ${JSON.stringify(item.previous)} → ${JSON.stringify(item.current)}`).join("\n") || "暂无差异";
    window.alert(`变更对比：${data.changedCount || 0} 项\n${diffText}`);
  }

  async function rollbackAudit(auditId) {
    if (!window.confirm("确认回滚到上一个配置状态？回滚也会写入配置审计。")) return;
    const result = await AppApi.post(`/api/architecture/v7/config-audits/${encodeURIComponent(auditId)}/rollback`, null, {});
    if (result?.status !== "rolled_back") window.alert(result?.detail || "回滚失败或没有可回滚版本");
    AppRouter.schedule("config-audit-rollback");
  }

  function metric(label, value, note) { return AppShell.metricCard(label, value, note || "V7.3"); }

  function auditRow(item, canRollback, canCompare) {
    return `<article class="report-card"><div><h3>${s(item.targetKey || item.auditId)}</h3><p>${s(item.action)} · ${s(item.actorUserId)} · ${s(item.createdAt)}</p><div class="report-meta"><span class="status-badge">${s(item.auditId)}</span><span>${s(item.tenantId)}</span><span>${s(item.orgId)}</span></div><p>${s(JSON.stringify(item.payload || {}).slice(0, 180))}</p></div><div class="report-actions">${canCompare ? `<button type="button" data-compare-audit="${s(item.auditId)}">对比</button>` : ""}${canRollback ? `<button type="button" data-rollback-audit="${s(item.auditId)}">回滚</button>` : ""}</div></article>`;
  }

  window.ConfigAuditPage = {
    route: "config-audit",
    title: "配置审计",
    async render() {
      const data = await loadAudits();
      const audits = data?.audits || [];
      return `<section class="report-hero report-hero-clean"><div><p class="eyebrow">CONFIG AUDIT · V7.3</p><h2>配置审计中心</h2></div><div class="report-hero-side"><span>当前阶段</span><strong>搜索 / 对比 / 回滚</strong><small>回滚也进入审计</small></div></section>
      <section class="kpi-grid report-metrics">
        ${metric("审计记录", data?.auditCount || 0, "当前筛选")}
        ${metric("操作类型", Object.keys(data?.byAction || {}).length, "action")}
        ${metric("配置对象", Object.keys(data?.byTarget || {}).length, "target")}
        ${metric("回滚权限", data?.canRollback ? "可回滚" : "只读", data?.roleId || "role")}
      </section>
      <section class="page-section report-section"><div class="section-header"><div><h3>审计记录</h3><p>功能开关、灰度规则和回滚动作都会在这里留痕。</p></div><div class="report-actions"><button type="button" data-filter-action="upsert_feature_flag">功能开关</button><button type="button" data-filter-action="upsert_rollout_rule">灰度规则</button><button type="button" data-filter-action="rollback_config_change">回滚</button><button type="button" data-clear-filter>全部</button></div></div><div class="report-card-list">${audits.map((item) => auditRow(item, data?.canRollback, data?.canCompare)).join("") || `<div class="log-empty">暂无配置审计。先到配置中心启用、暂停或设置灰度。</div>`}</div></section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-compare-audit]", "click", (_, node) => compareAudit(node.dataset.compareAudit));
      ctx.delegate("[data-rollback-audit]", "click", (_, node) => rollbackAudit(node.dataset.rollbackAudit));
      ctx.delegate("[data-filter-action]", "click", async (_, node) => {
        const data = await loadAudits({ action: node.dataset.filterAction });
        const html = data.audits.map((item) => auditRow(item, data.canRollback, data.canCompare)).join("") || `<div class="log-empty">当前筛选暂无记录。</div>`;
        document.querySelector(".report-card-list").innerHTML = html;
      });
      ctx.delegate("[data-clear-filter]", "click", () => AppRouter.schedule("config-audit-clear"));
    },
  };
})();