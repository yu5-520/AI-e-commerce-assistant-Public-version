(function () {
  const s = (value) => AppShell.escape(value ?? "");
  let accountNotice = "";

  function ensureAccountCss() {
    if (document.querySelector('link[data-account-ui="1"]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/web_demo/account-ui.css?v=21.8.0";
    link.dataset.accountUi = "1";
    document.head.appendChild(link);
  }

  const accountProfile = {
    avatar: "参",
    nickname: "经营参谋账号",
    accountNo: "ACC-ERP-0001",
    phone: "未绑定",
    email: "未绑定",
    status: "启用",
    password: "建议更新",
    twoFactor: "未开启",
    devices: "1 台设备在线",
  };

  const bindings = [
    ["微信", "未绑定", "消息提醒"],
    ["企业微信", "未绑定", "组织通知"],
    ["淘宝商家授权", "未绑定", "店铺数据"],
    ["拼多多商家授权", "未绑定", "店铺数据"],
    ["抖音小店授权", "未绑定", "店铺数据"],
    ["ERP 数据授权", "未绑定", "商品 / 订单 / 库存"],
  ];
  const securityItems = [["登录密码", accountProfile.password, "登录安全"], ["手机号", accountProfile.phone, "找回账号"], ["邮箱", accountProfile.email, "系统通知"], ["二次验证", accountProfile.twoFactor, "企业安全"], ["登录设备", accountProfile.devices, "设备管理"]];
  const noticeItems = [["日报提醒", "开启", "经营日报"], ["周报提醒", "开启", "复盘报告"], ["任务提醒", "开启", "任务流转"], ["审计提醒", "开启", "异常复核"]];

  function infoItem(label, value) { return `<article><span>${s(label)}</span><strong>${s(value)}</strong></article>`; }
  function settingRow([title, status, desc], action = "设置") { return `<article class="account-setting-row"><div><strong>${s(title)}</strong><small>${s(desc)}</small></div><span>${s(status)}</span><button type="button" class="secondary" data-account-action data-label="${s(action)}">${s(action)}</button></article>`; }
  function actionButton(label, variant = "") { return `<button type="button" class="${s(variant)}" data-account-action data-label="${s(label)}">${s(label)}</button>`; }
  function dataScopeLabel(user) {
    const role = user?.roleId;
    const map = { owner: "全部经营单元", manager: "负责经营单元", operator: "店铺切片", finance: "财务经营数据", observer: "授权摘要" };
    return map[role] || "未返回数据范围";
  }
  function dataScopeNote(user) {
    const role = user?.roleId;
    const map = { owner: "看全局汇总，不表示账号绑定店铺数据", manager: "由经营单元与店铺归属动态计算", operator: "只读负责范围内的经营对象", finance: "只读财务与经营指标口径", observer: "只读脱敏进度和摘要" };
    return map[role] || "请检查 /api/accounts 返回的 currentUser";
  }
  function compactPermissions(user) {
    const names = Array.isArray(user?.permissionNames) ? user.permissionNames.filter(Boolean) : [];
    return names.length ? names.slice(0, 4).join("、") : "未返回权限";
  }
  function roleTag(user) {
    const role = user?.roleId;
    const map = { owner: "老板", manager: "总管", operator: "运营", finance: "财务", observer: "观察" };
    return map[role] || "未知";
  }
  function assertAccountPayload(payload) {
    if (!payload || !payload.currentUser || !payload.currentUser.id) {
      throw new Error("账号接口未返回 currentUser；已禁止本地模拟账号兜底。请检查 /api/accounts、APP_ENV 或账号隔离配置。");
    }
  }
  function displayName(user, profile = null) { return profile?.displayName || user?.displayName || user?.name || user?.id || "经营成员"; }
  function positionTitle(user, profile = null) { return profile?.positionTitle || user?.positionTitle || user?.roleName || user?.roleId || "经营成员"; }
  function formatNumber(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString("zh-CN") : "0";
  }
  async function loadGrowthProfile(current) {
    const cached = window.NeuralOperatingUI?.projection?.();
    if (cached?.operatorProfile?.userId === current?.id) return cached.operatorProfile;
    try {
      const response = await fetch("/api/modules/neural-operating", {
        headers: { Accept: "application/json", "X-Mock-User-Id": current?.id || AppApi.getCurrentUserId() },
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return (await response.json())?.operatorProfile || null;
    } catch (error) {
      console.warn("[account] growth projection unavailable", error);
      return null;
    }
  }
  function testAccountCard(user, currentId) {
    const active = user.id === currentId;
    return `<button type="button" class="account-switch-card ${active ? "active" : ""}" data-switch-user="${s(user.id)}" ${active ? "disabled" : ""}>
      <span>${s(roleTag(user))}</span><strong>${s(displayName(user))}</strong><em>${s(positionTitle(user))}</em>
      <small>${s(dataScopeLabel(user))} · ${s(dataScopeNote(user))}</small><b>${active ? "当前身份" : "切换测试"}</b>
    </button>`;
  }
  function currentIdentityBlock(current, profile) {
    const level = `LV${Number(profile?.level || 1)} ${profile?.levelName || "经营入门"}`;
    return `<section class="account-current-block">
      <article><span>当前登录身份</span><strong>${s(displayName(current, profile))}</strong><small>${s(positionTitle(current, profile))}</small></article>
      <article><span>系统成长等级</span><strong>${s(level)}</strong><small>${s(formatNumber(profile?.experience))} 经营经验</small></article>
      <article><span>数据范围</span><strong>${s(dataScopeLabel(current))}</strong><small>${s(dataScopeNote(current))}</small></article>
      <article><span>权限摘要</span><strong>${s(compactPermissions(current))}</strong><small>岗位与系统等级互不替代</small></article>
    </section>`;
  }
  function growthBlock(profile) {
    if (!profile) return "";
    const progress = Math.max(0, Math.min(100, Number(profile.progressPercent || 0)));
    const nextCopy = Number(profile.experienceForNextLevel || 0) > 0 ? `距离下一等级还需 ${formatNumber(profile.experienceForNextLevel)} 经营经验` : "当前等级已完成";
    return `<section class="page-section account-section account-growth-summary"><div class="section-header"><div><h3>个人经营成长</h3><p>经验来自已经完成、复核和沉淀的真实任务。</p></div><span class="status-badge">LV${s(profile.level)}</span></div><div class="account-growth-head"><div><strong>${s(profile.levelName)}</strong><span>${s(formatNumber(profile.experience))} / ${s(formatNumber(profile.nextLevelExperience))}</span></div><small>在职第 ${s(profile.tenureDays)} 天 · 完成 ${s(profile.completedTaskCount)} 项 · 复核验证 ${s(profile.reviewedTaskCount)} 项</small></div><div class="account-growth-track"><i style="width:${s(progress)}%"></i></div><p class="account-growth-note">${s(nextCopy)}。等级仅用于工作痕迹可视化，不自动改变岗位、权限、薪资或店铺归属。</p></section>`;
  }
  function testSwitcherBlock(payload) {
    const users = Array.isArray(payload?.users) ? payload.users : [];
    const current = payload?.currentUser || {};
    if (!users.length || payload?.demoSwitchDisabled) return "";
    return `<section class="page-section account-section account-switch-section">
      <div class="section-header"><h3>MVP 测试身份</h3><span class="status-badge">仅测试</span></div>
      <p class="account-note">用于快速检查不同角色看到的任务、报表、经营模块和日志范围。正式版本通过登录 / 退出登录切换账号，此处不改变权限配置，也不表示账号绑定具体店铺数据。</p>
      <div class="account-switch-grid">${users.map((user) => testAccountCard(user, current.id)).join("")}</div>
    </section>`;
  }
  async function switchTestUser(userId, node) {
    const oldText = node?.querySelector("b")?.textContent || "切换测试";
    if (node) { node.disabled = true; node.classList.add("is-loading"); const label = node.querySelector("b"); if (label) label.textContent = "切换中"; }
    try {
      const nextAccount = await AppApi.switchAccount(userId);
      await AppApi.refreshTaskState();
      const user = nextAccount?.currentUser;
      accountNotice = `已切换到 ${displayName(user) || userId} · ${positionTitle(user) || "测试身份"}。`;
      window.NeuralOperatingUI?.refresh?.();
      AppRouter.schedule("account-switch");
    } catch (error) {
      accountNotice = `切换失败：${error.message || error}`;
      if (node) { node.disabled = false; node.classList.remove("is-loading"); const label = node.querySelector("b"); if (label) label.textContent = oldText; }
    }
  }

  window.AccountPage = {
    route: "accounts",
    title: "账号",
    async render() {
      ensureAccountCss();
      const payload = await AppApi.accounts();
      assertAccountPayload(payload);
      const current = payload.currentUser;
      const growth = await loadGrowthProfile(current);
      return `<section class="account-hero"><div class="account-avatar">${s(accountProfile.avatar)}</div><div class="account-hero-main"><h2>${s(displayName(current, growth))}</h2><p>${s(positionTitle(current, growth))} · 个人账号、安全与经营成长集中管理。</p></div><div class="account-status-card"><span>系统等级</span><strong>LV${s(growth?.level || 1)} ${s(growth?.levelName || "经营入门")}</strong><em>${s(accountProfile.status)}</em></div></section>
      ${accountNotice ? AppShell.notice("账号切换", accountNotice) : ""}
      ${currentIdentityBlock(current, growth)}
      ${growthBlock(growth)}
      ${testSwitcherBlock(payload)}
      <section class="account-grid">
        <article class="account-info-card"><div class="section-header"><h3>基础信息</h3><span class="status-badge">账号</span></div><div class="account-info-list">${[["内部代称", displayName(current, growth)], ["账号 ID", current.id || accountProfile.accountNo], ["登录账号", current.name], ["公司岗位", positionTitle(current, growth)], ["权限角色", current.roleName], ["手机号", accountProfile.phone], ["邮箱", accountProfile.email]].map(([label, value]) => infoItem(label, value)).join("")}</div></article>
        <article class="account-info-card account-action-card"><div class="section-header"><h3>基础操作</h3><span class="status-badge">操作</span></div><div class="account-action-list">${actionButton("编辑资料")}${actionButton("清除缓存")}${actionButton("退出登录", "secondary")}</div><div class="account-note">真实登录、短信、邮箱和第三方授权在正式版接入。MVP 测试身份不会改动真实权限配置。</div></article>
      </section>
      <section class="page-section account-section"><div class="section-header"><h3>安全设置</h3><span class="status-badge">安全</span></div><div class="account-setting-list">${securityItems.map((item) => settingRow(item)).join("")}</div></section>
      <section class="page-section account-section"><div class="section-header"><h3>绑定与授权</h3><span class="status-badge">授权</span></div><div class="account-setting-list">${bindings.map((item) => settingRow(item, "绑定")).join("")}</div></section>
      <section class="page-section account-section"><div class="section-header"><h3>通知设置</h3><span class="status-badge">通知</span></div><div class="account-setting-list">${noticeItems.map((item) => settingRow(item, "调整")).join("")}</div></section>`;
    },
    mount(ctx) {
      ctx.delegate("[data-switch-user]", "click", (_, node) => switchTestUser(node.dataset.switchUser, node));
      ctx.delegate("[data-account-action]", "click", (_, node) => { const label = node.dataset.label || node.textContent || "设置"; node.textContent = label === "退出登录" ? "正式版接入" : "暂未接入"; node.disabled = true; window.setTimeout(() => { node.disabled = false; node.textContent = label; }, 900); });
    },
  };

  window.RoleConsolePage = {
    route: "role-console",
    title: "权限入口",
    async render() {
      ensureAccountCss();
      return `<section class="report-hero"><div><h2>入口已迁移</h2><p>账号页保留个人成长、登录、安全、绑定和通知。权限与店铺归属在系统权限中管理。</p></div><div class="report-hero-side"><strong>兼容入口</strong></div></section><section class="page-section"><div class="section-header"><h3>权限治理</h3><button type="button" data-system-status>进入系统</button></div></section>`;
    },
    mount(ctx) { ctx.delegate("[data-system-status]", "click", () => AppRouter.navigate("system-status")); },
  };
})();
