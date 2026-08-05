(function () {
  function arr(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
  function s(value) { return window.AppShell?.escape?.(value ?? "") ?? String(value ?? ""); }
  function metricValue(value, definition = {}) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return s(value);
    if (definition.kind === "money") return `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
    if (definition.kind === "percent") {
      const percent = Math.abs(number) <= 1 ? number * 100 : number;
      return `${percent.toFixed(Math.abs(percent) >= 10 ? 1 : 2)}%`;
    }
    if (definition.kind === "integer") return Math.round(number).toLocaleString("zh-CN");
    return number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }
  function deltaValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const sign = number > 0 ? "+" : "";
    return `${sign}${(number * 100).toFixed(1)}%`;
  }
  function deltaClass(value) {
    const number = Number(value);
    return !Number.isFinite(number) || Math.abs(number) < 0.0001 ? "flat" : number > 0 ? "up" : "down";
  }
  function dateLabel(value) {
    const text = String(value || "—");
    return text.length >= 10 ? text.slice(5, 10) : text;
  }
  function render(options = {}) {
    const definitions = arr(options.definitions);
    const snapshots = arr(options.snapshots);
    const title = options.title || "数据快照";
    const badge = options.badge || `${snapshots.length} 次比对`;
    const summaryCards = arr(options.summaryCards);
    const usageByCode = options.usageByCode || {};
    const showUsage = Boolean(options.showUsage);
    const groups = new Map();
    definitions.forEach((definition) => {
      const group = definition.group || "任务指标";
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(definition);
    });
    if (!definitions.length || !snapshots.length) return "";
    const className = showUsage ? " has-usage" : "";
    const headers = snapshots.map((item, index) => `<div class="product-snapshot-period"><strong>${s(dateLabel(item.businessDate))}</strong><small>${index === snapshots.length - 1 ? "任务时点" : s(item.dataVersion || "有效快照")}</small></div>`).join("");
    const usageHeader = showUsage ? `<div class="task-metric-usage"><strong>任务用途</strong><small>为何参考该指标</small></div>` : "";
    const rows = Array.from(groups.entries()).map(([group, items]) => `<div class="product-snapshot-group"><h4>${s(group)}</h4>${items.map((definition) => {
      const cells = snapshots.map((snapshot) => {
        const value = snapshot.metrics?.[definition.code];
        const delta = snapshot.changes?.[definition.code];
        return `<div class="product-snapshot-cell"><strong>${s(metricValue(value, definition))}</strong><small class="snapshot-delta ${deltaClass(delta)}">${s(deltaValue(delta))}</small></div>`;
      }).join("");
      const usage = showUsage ? `<div class="task-metric-usage"><strong>${s(usageByCode[definition.code] || definition.taskUsage || "任务判断参考")}</strong><small>${s(definition.evidenceRole || "supporting_signal")}</small></div>` : "";
      return `<div class="product-snapshot-row${className}" style="--snapshot-count:${snapshots.length}"><div class="product-snapshot-metric"><strong>${s(definition.label)}</strong><small>${s(definition.code)}</small></div>${cells}${usage}</div>`;
    }).join("")}</div>`).join("");
    return `<section class="page-section product-detail-section product-trend-section task-evidence-section"><div class="section-header"><h3>${s(title)}</h3><span class="status-badge">${s(badge)}</span></div>${summaryCards.length ? `<div class="product-trend-summary">${summaryCards.map((item) => `<div><span>${s(item.label)}</span><strong>${s(item.value)}</strong>${item.note ? `<small>${s(item.note)}</small>` : ""}</div>`).join("")}</div>` : ""}<div class="product-snapshot-scroll"><div class="product-snapshot-head${className}" style="--snapshot-count:${snapshots.length}"><div><strong>指标</strong><small>未更新显示为—</small></div>${headers}${usageHeader}</div>${rows}</div>${options.rule ? `<div class="product-trend-rule">${s(options.rule)}</div>` : ""}</section>`;
  }
  window.MetricSnapshotTable = { render, metricValue, deltaValue, deltaClass, dateLabel };
})();
