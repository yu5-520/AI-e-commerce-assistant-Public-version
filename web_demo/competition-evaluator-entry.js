(() => {
  "use strict";

  const SAMPLE_REPORTS = [
    {
      index: "01",
      label: "第1期脱敏经营报表",
      note: "评委样例 · XLSX",
      href: "/api/data/sample-reports/1.xlsx",
      filename: "AI经营参谋_脱敏样例_第1期.xlsx",
    },
    {
      index: "02",
      label: "第2期脱敏经营报表",
      note: "评委样例 · XLSX",
      href: "/api/data/sample-reports/2.xlsx",
      filename: "AI经营参谋_脱敏样例_第2期.xlsx",
    },
    {
      index: "03",
      label: "第3期脱敏经营报表",
      note: "评委样例 · XLSX",
      href: "/api/data/sample-reports/3.xlsx",
      filename: "AI经营参谋_脱敏样例_第3期.xlsx",
    },
  ];

  let scheduled = false;

  function sampleMarkup() {
    return `<div data-competition-samples>
      <div class="competition-evaluator-intro">
        <div>
          <strong>三期脱敏经营报表</strong>
          <p>按顺序下载并逐份上传 XLSX。样例会进入现有真实报表上传、Excel 解析、证据构建与 Agent 业务链路，不走 Demo 捷径。</p>
        </div>
        <span class="status-badge">3 份 · XLSX · 已脱敏</span>
      </div>
      <div class="competition-sample-grid">
        ${SAMPLE_REPORTS.map((item) => `<article class="competition-sample-card">
          <small>${item.index}</small>
          <strong>${item.label}</strong>
          <span>${item.note}</span>
          <a href="${item.href}" download="${item.filename}">下载报表</a>
        </article>`).join("")}
      </div>
    </div>`;
  }

  function findSourceSection(root) {
    const tagged = root.querySelector('[data-competition-enterprise-sources="true"]');
    if (tagged) return tagged;
    return Array.from(root.querySelectorAll("section")).find((section) => {
      const title = section.querySelector(":scope > .section-header h3, :scope > header h3, h3");
      return title?.textContent?.trim() === "数据源";
    }) || null;
  }

  function enhanceReportPage() {
    const root = document.querySelector("#appView");
    if (!root) return;

    const upload = root.querySelector(".compact-upload-section");
    const live = root.querySelector("[data-report-live]");
    if (!upload || !live) return;

    upload.dataset.competitionEvaluatorEntry = "true";
    if (root.firstElementChild !== upload) root.insertBefore(upload, root.firstElementChild);

    const uploadHeader = upload.querySelector(":scope > .section-header");
    const uploadTitle = uploadHeader?.querySelector("h3");
    const uploadBadge = uploadHeader?.querySelector(".status-badge");
    if (uploadTitle && uploadTitle.textContent.trim() !== "评委快速测试") uploadTitle.textContent = "评委快速测试";
    if (uploadBadge && ["备用入口", "等待上传"].includes(uploadBadge.textContent.trim())) uploadBadge.textContent = "真实链路";

    if (!upload.querySelector("[data-competition-samples]")) {
      const input = upload.querySelector("[data-manual-file-input]");
      if (input) {
        input.setAttribute("accept", ".xlsx,.xlsm,.xls,.csv,.json");
        input.insertAdjacentHTML("afterend", sampleMarkup());
      } else uploadHeader?.insertAdjacentHTML("afterend", sampleMarkup());
    }

    const sourceSection = findSourceSection(root);
    if (sourceSection) {
      sourceSection.dataset.competitionEnterpriseSources = "true";
      const sourceHeader = sourceSection.querySelector(":scope > .section-header");
      const sourceTitle = sourceHeader?.querySelector("h3");
      const sourceBadge = sourceHeader?.querySelector(".status-badge");
      if (sourceTitle && sourceTitle.textContent.trim() !== "企业数据源接入") sourceTitle.textContent = "企业数据源接入";
      if (sourceBadge && sourceBadge.textContent.trim() !== "正式部署可配置") sourceBadge.textContent = "正式部署可配置";
      if (!sourceSection.querySelector("[data-competition-enterprise-note]")) {
        sourceHeader?.insertAdjacentHTML(
          "afterend",
          '<p class="competition-enterprise-note" data-competition-enterprise-note>ERP、CRM、平台后台 API 与广告后台 API 属于正式部署扩展能力；比赛评测无需配置，直接使用上方脱敏报表即可完成真实链路测试。</p>',
        );
      }
      if (root.lastElementChild !== sourceSection) root.appendChild(sourceSection);
    }
  }

  function scheduleEnhance() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      enhanceReportPage();
    });
  }

  const observer = new MutationObserver(scheduleEnhance);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("hashchange", scheduleEnhance);
  window.addEventListener("api-cache-updated", scheduleEnhance);
  window.addEventListener("v104-import-sync", scheduleEnhance);
  document.addEventListener("DOMContentLoaded", scheduleEnhance, { once: true });
  scheduleEnhance();
})();