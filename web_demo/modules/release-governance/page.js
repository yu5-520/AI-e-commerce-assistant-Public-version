(function () {
  const s = (value) => AppShell.escape(value ?? "-");

  async function loadRelease() {
    const response = await fetch("/api/architecture/v7/release-governance", {
      method: "GET",
      headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function metric(label, value, note) { return AppShell.metricCard(label, value, note || "V7.4"); }
  function statusText(status) { return { full_release: "全量", gray_release: "灰度", rollback_watch: "回滚观察", paused: "暂停", enabled_without_rollout: "待配置" }[status] || status; }
  function tone(status) { return status === "full_release" ? "good" : status === "rollback_watch" ? "danger" : status === "gray_release" ? "warning" : "neutral"; }

  function itemCard(item) {
    const auditTotal = Object.values(item.auditCounts || {}).reduce((total, value) => total + Number(value || 0), 0);
    return `<article class="report-card"><div><h3>${s(item.name)}</h3><p>${s(item.flagKey)} · ${s(item.stage)} · ${s(item.governanceNote)}</p><div class="report-meta"><span class="status-badge ${tone(item.releaseStatus)}">${s(statusText(item.releaseStatus))}</span><span>灰度 ${s(item.rolloutPercentage)}%</span><span>角色 ${s((item.activeRoles || []).join(" / ") || "未覆盖")}</span><span>回滚 ${s(item.rollbackCount || 0)}</span></div><p>规则 ${s(item.ruleCount)} · 激活 ${s(item.activeRuleCount)} · 审计 ${s(auditTotal)}</p></div><div class="report-actions"><button type="button" data-open-tenant-config>配置</button><button type="button" data-open-config-audit>审计</button></div></article>`;
  }

  function countRows(title, data = {}) {
    const rows = Object.entries(data);
    return `<article class="page-section report-section"><div class="section-header"><h3>${s(title)}</h3><span class="status-badge">${rows.length}</span></div><div class="version-alert-list">${rows.length ? rows.map(([key, value]) => `<article class="version-alert-row"><strong>${s(key)}</strong><span>${s(value)}</span><small>发布治理指标</small></article>`).join("") : `<div class="log-empty">暂无数据。</div>`}</div></article>`;
  }

  window.ReleaseGovernancePage = {
    route: "release-governance",
    title: "发布治理",
    async render() {
      const data = await loadRelease();
      const summary = data?.summary || {};
      const items = data?.releaseItems || [];
      return `<section class="report-hero report-hero-clean"><div><p class="eyebrow">RELEASE GOVERNANCE · V7.4</p><h2>SaaS 发布治理看板</h2></div><div class="report-hero-side"><span>当前阶段</span><strong>灰度覆盖与回滚风险</strong><small>功能开关发布状态</small></div></section><section class="kpi-grid report-metrics">${metric("功能数", summary.featureCount || 0, "feature")}${metric("已启用", summary.enabledCount || 0, "enabled")}${metric("灰度规则", summary.rolloutRuleCount || 0, "rollout")}${metric("回滚次数", summary.rollbackCount || 0, "rollback")}</section><section class="report-preview-grid">${countRows("发布状态", data?.statusCounts || {})}${countRows("阶段分布", data?.stageCounts || {})}${countRows("角色覆盖", data?.roleCoverage || {})}</section><section class="page-section report-section"><div class="section-header"><div><h3>功能发布列表</h3><p>按功能开关汇总启用状态、灰度比例、角色覆盖、审计量和回滚次数。</p></div><span class="status-badge">${items.length}</span></div><div class="report-card-list">${items.map(itemCard).join("") || `<div class="log-empty">暂无功能发布数据。</div>`}</div></section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-open-tenant-config]", "click", () => AppRouter.navigate("tenant-config"));
      ctx.delegate("[data-open-config-audit]", "click", () => AppRouter.navigate("config-audit"));
    },
  };
})();