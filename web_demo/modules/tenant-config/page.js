(function () {
  const s = (value) => AppShell.escape(value ?? "-");

  async function loadConfig() {
    return AppApi.post ? await fetchConfig() : null;
  }

  async function fetchConfig() {
    const response = await fetch("/api/architecture/v7/tenant-config", {
      method: "GET",
      headers: { Accept: "application/json", "X-Mock-User-Id": AppApi.getCurrentUserId() },
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function metric(label, value, note) {
    return AppShell.metricCard(label, value, note || "V7.2");
  }

  function roleText(flag) {
    const roles = flag.allowedRoles || [];
    return roles.length ? roles.join(" / ") : "全部角色";
  }

  function flagCard(flag, consoleActions = {}) {
    const canToggle = !!consoleActions.canToggleFeatureFlag;
    const canRollout = !!consoleActions.canUpdateRollout;
    return `<article class="report-card"><div><h3>${s(flag.name)}</h3><p>${s(flag.flagKey)} · ${s(flag.stage)} · ${s(roleText(flag))}</p><div class="report-meta"><span class="status-badge ${flag.enabledForContext ? "good" : "warning"}">${flag.enabledForContext ? "当前可用" : "当前不可用"}</span><span>${flag.enabled ? "开关已启用" : "开关已暂停"}</span><span>灰度 ${s(flag.rolloutPercentage)}%</span></div><p>${s(flag.reason || "按租户、角色和灰度规则判断。")}</p></div><div class="report-actions">${canToggle ? `<button type="button" data-toggle-flag="${s(flag.flagKey)}" data-next-enabled="${flag.enabled ? "false" : "true"}">${flag.enabled ? "暂停" : "启用"}</button><button type="button" data-stage-flag="${s(flag.flagKey)}">阶段</button>` : `<span class="status-badge">只读</span>`}${canRollout ? `<button type="button" data-rollout-flag="${s(flag.flagKey)}">灰度</button>` : ""}</div></article>`;
  }

  function rolloutRow(rule) {
    return `<article class="version-alert-row"><strong>${s(rule.flagKey)}</strong><span>${s(rule.ruleId)} · ${s(rule.status)} · ${s(rule.percentage)}%</span><small>${s((rule.roleIds || []).join(" / ") || "全部角色")} · ${s(rule.rule || "灰度规则")}</small></article>`;
  }

  async function toggleFlag(flagKey, enabled) {
    const name = window.prompt("功能名称", flagKey) || flagKey;
    const stage = window.prompt("阶段 stable / beta / internal", enabled ? "stable" : "beta") || "beta";
    await AppApi.post(`/api/architecture/v7/feature-flags/${encodeURIComponent(flagKey)}`, null, { name, enabled, stage, allowedRoles: ["owner", "manager"] });
    AppRouter.schedule("tenant-config-toggle");
  }

  async function updateStage(flagKey) {
    const stage = window.prompt("阶段 stable / beta / internal", "beta");
    if (!stage) return;
    const enabled = window.confirm("保持启用这个功能？");
    await AppApi.post(`/api/architecture/v7/feature-flags/${encodeURIComponent(flagKey)}`, null, { name: flagKey, enabled, stage, allowedRoles: ["owner", "manager"] });
    AppRouter.schedule("tenant-config-stage");
  }

  async function updateRollout(flagKey) {
    const percentage = Number(window.prompt("灰度比例 0-100", "100") || 0);
    const status = window.prompt("规则状态 active / paused", percentage > 0 ? "active" : "paused") || "active";
    const roles = (window.prompt("开放角色，用逗号分隔", "owner,manager") || "owner,manager").split(",").map((item) => item.trim()).filter(Boolean);
    await AppApi.post(`/api/architecture/v7/feature-flags/${encodeURIComponent(flagKey)}/rollout`, null, { percentage, status, roleIds: roles, rule: "前端配置中心更新灰度规则" });
    AppRouter.schedule("tenant-config-rollout");
  }

  window.TenantConfigPage = {
    route: "tenant-config",
    title: "配置中心",
    async render() {
      const data = await loadConfig();
      const config = data?.config || {};
      const flags = data?.featureFlags || [];
      const rollouts = data?.rolloutRules || [];
      const actions = data?.consoleActions || {};
      return `<section class="report-hero report-hero-clean"><div><p class="eyebrow">TENANT CONFIG · V7.2</p><h2>租户配置中心</h2></div><div class="report-hero-side"><span>当前阶段</span><strong>功能开关操作</strong><small>启用 / 暂停 / 灰度</small></div></section>
      <section class="kpi-grid report-metrics">
        ${metric("租户版本", config.edition || config.plan || "demo", "SaaS配置")}
        ${metric("启用模块", (config.enabledModules || []).length, "模块")}
        ${metric("功能开关", `${data?.enabledForContextCount || 0}/${data?.featureFlagCount || 0}`, "当前可用")}
        ${metric("灰度规则", rollouts.length, "规则")}
      </section>
      <section class="page-section report-section"><div class="section-header"><div><h3>功能开关</h3><p>SaaS能力按租户、角色和灰度规则开放，不再硬编码全量开放。</p></div><span class="status-badge">${s(data?.roleId)}</span></div><div class="report-card-list">${flags.map((flag) => flagCard(flag, actions)).join("") || `<div class="log-empty">暂无功能开关。</div>`}</div></section>
      <section class="page-section report-section"><div class="section-header"><h3>灰度规则</h3><span class="status-badge">${rollouts.length}</span></div><div class="version-alert-list">${rollouts.map(rolloutRow).join("") || `<div class="log-empty">暂无灰度规则。</div>`}</div></section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-toggle-flag]", "click", (_, node) => toggleFlag(node.dataset.toggleFlag, node.dataset.nextEnabled === "true"));
      ctx.delegate("[data-stage-flag]", "click", (_, node) => updateStage(node.dataset.stageFlag));
      ctx.delegate("[data-rollout-flag]", "click", (_, node) => updateRollout(node.dataset.rolloutFlag));
    },
  };
})();