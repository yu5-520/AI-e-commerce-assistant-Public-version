(function () {
  const s = (value) => AppShell.escape(value);
  const state = { storeId: "", operatorId: "", password: "", notice: "" };

  function storeNames(user, stores) {
    const map = new Map((stores || []).map((store) => [store.id, store.name]));
    return (user.storeIds || []).map((id) => map.get(id) || id).join(" / ") || "未授权";
  }

  function optionList(items, selected, labelKey = "name") {
    return items.map((item) => `<option value="${s(item.id)}" ${item.id === selected ? "selected" : ""}>${s(item[labelKey] || item.name)}</option>`).join("");
  }

  function migrationRows(migrations) {
    if (!migrations?.length) return `<article class="migration-row empty"><strong>暂无待生效迁移</strong></article>`;
    return migrations.map((item) => `<article class="migration-row"><strong>${s(item.storeName)}</strong><span>${s(item.oldOperatorName)} → ${s(item.newOperatorName)}</span><em>${s(item.effectiveDate)} 生效</em><b>${s(item.status)}</b></article>`).join("");
  }

  function orgHero(account) {
    return `<section class="report-hero realtime-hero clean-hero"><div><h2>组织效率</h2></div><div class="report-hero-side"><strong>${s((account?.stores || []).length)}</strong></div></section>`;
  }

  function metrics(account) {
    const users = account?.users || [];
    const stores = account?.stores || [];
    const pending = account?.pendingStoreMigrations || [];
    return `<section class="kpi-grid report-metrics realtime-metrics clean-metrics">${[["员工", users.length], ["运营", users.filter((u) => u.roleId === "operator").length], ["店铺", stores.length], ["待生效", pending.length]].map(([label, value]) => `<article class="card realtime-metric"><h3>${s(label)}</h3><strong>${s(value)}</strong></article>`).join("")}</section>`;
  }

  function orgTree(account) {
    const users = account?.users || [];
    const stores = account?.stores || [];
    const owner = users.find((u) => u.roleId === "owner") || {};
    const manager = users.find((u) => u.roleId === "manager") || {};
    const children = users.filter((u) => ["operator", "finance", "observer"].includes(u.roleId));
    return `<section class="page-section org-section"><div class="section-header"><h3>职位关系</h3></div><div class="org-map"><article class="org-node owner"><strong>${s(owner.name || "老板")}</strong><span>${s(owner.roleName || "老板账号")}</span></article><div class="org-line"></div><article class="org-node manager"><strong>${s(manager.name || "店群总管")}</strong><span>${s(manager.roleName || "店群总管账号")}</span></article><div class="org-children">${children.map((user) => `<article class="org-node"><strong>${s(user.name)}</strong><span>${s(user.roleName)}</span></article>`).join("")}</div></div></section>`;
  }

  function storeAssignmentPanel(account) {
    if (!AppApi.can("manage_roles")) {
      return `<section class="page-section org-section"><div class="section-header"><h3>店铺责任</h3></div><div class="permission-muted">当前账号无权调整店铺归属。</div></section>`;
    }
    const stores = account?.stores || [];
    const operators = (account?.users || []).filter((user) => user.roleId === "operator");
    const selectedStore = stores.find((store) => store.id === state.storeId) || stores[0] || {};
    const selectedOperator = operators.find((operator) => operator.id === state.operatorId) || operators.find((operator) => operator.id !== selectedStore.primaryOperatorId) || operators[0] || {};
    if (!state.storeId && selectedStore.id) state.storeId = selectedStore.id;
    if (!state.operatorId && selectedOperator.id) state.operatorId = selectedOperator.id;
    const cards = stores.map((store) => `<article class="org-control-card responsibility-card"><div class="section-header"><h3>${s(store.name)}</h3><span class="status-badge">${s(store.platform)}</span></div><div class="assignment-current"><strong>当前负责人</strong><span>${s(store.primaryOperatorName || "未分配")}</span></div>${store.pendingMigration ? `<div class="pending-migration"><b>待生效</b><span>${s(store.pendingMigration.oldOperatorName)} → ${s(store.pendingMigration.newOperatorName)}</span><em>${s(store.pendingMigration.effectiveDate)}</em></div>` : ""}</article>`).join("");
    return `<section class="page-section org-section"><div class="section-header"><h3>店铺责任</h3><span class="status-badge">次日生效</span></div><div class="org-control-grid assignment-grid">${cards}</div></section>
      <section class="page-section org-section"><div class="section-header"><h3>迁移确认</h3><span class="status-badge">密码验证</span></div>${state.notice ? `<div class="permission-result">${s(state.notice)}</div>` : ""}<div class="migration-form"><label><span>店铺</span><select data-migration-store>${optionList(stores, state.storeId)}</select></label><label><span>新负责人</span><select data-migration-operator>${optionList(operators, state.operatorId)}</select></label><label><span>管理密码</span><input type="password" data-migration-password value="${s(state.password)}" placeholder="admin123" /></label><button type="button" data-schedule-migration>确认迁移，明日生效</button></div><div class="impact-grid"><span>商品数据</span><span>报表数据</span><span>预警归属</span><span>未完成待办</span><span>运营日志</span><span>复盘归属</span></div></section>`;
  }

  function operatorScopePanel(account) {
    const operators = (account?.users || []).filter((user) => user.roleId === "operator");
    const stores = account?.stores || [];
    return `<section class="page-section org-section"><div class="section-header"><h3>运营范围</h3></div><div class="org-control-grid">${operators.map((user) => `<article class="org-control-card"><div class="section-header"><h3>${s(user.name)}</h3><span class="status-badge">运营</span></div><div class="assignment-current"><strong>可见店铺</strong><span>${s(storeNames(user, stores))}</span></div></article>`).join("")}</div></section>`;
  }

  function pendingPanel(account) {
    return `<section class="page-section org-section"><div class="section-header"><h3>待生效迁移</h3><span class="status-badge">${s((account?.pendingStoreMigrations || []).length)}</span></div><div class="migration-list">${migrationRows(account?.pendingStoreMigrations || [])}</div></section>`;
  }

  window.OrgEfficiencyPage = {
    route: "org-efficiency",
    title: "组织效率",
    async render() {
      const account = await AppApi.accounts();
      return `${orgHero(account)}${metrics(account)}${orgTree(account)}${storeAssignmentPanel(account)}${operatorScopePanel(account)}${pendingPanel(account)}`;
    },
    mount(ctx) {
      ctx.delegate("[data-migration-store]", "change", (_, node) => { state.storeId = node.value; AppRouter.schedule("migration-store"); });
      ctx.delegate("[data-migration-operator]", "change", (_, node) => { state.operatorId = node.value; AppRouter.schedule("migration-operator"); });
      ctx.delegate("[data-migration-password]", "input", (_, node) => { state.password = node.value; });
      ctx.delegate("[data-schedule-migration]", "click", async () => {
        if (!state.storeId || !state.operatorId || !state.password) { state.notice = "请选择店铺、新负责人，并输入管理密码。"; AppRouter.schedule("migration-missing"); return; }
        const result = await AppApi.post(`/api/accounts/store-assignments/${encodeURIComponent(state.storeId)}`, null, { primaryOperatorId: state.operatorId, reviewerId: "U002", password: state.password });
        if (result?.migration) {
          state.notice = `${result.migration.storeName} 已提交迁移，${result.migration.effectiveDate} 生效。`;
          state.password = "";
          await AppApi.prefetch();
        } else {
          state.notice = "迁移提交失败，请检查管理密码。";
        }
        AppRouter.schedule("migration-scheduled");
      });
    },
  };
})();
