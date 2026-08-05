(function () {
  function view() { return document.getElementById("appView"); }
  function title() { return document.getElementById("pageTitle"); }
  function escape(value) { return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char])); }
  function setTitle(value) { const el = title(); if (el) el.textContent = value || "总览"; }
  function setView(html) { const el = view(); if (el) el.innerHTML = html || ""; }
  function setActive(route) { document.querySelectorAll(".nav a").forEach((link) => link.classList.toggle("active", link.dataset.route === route)); }
  function notice(titleText, body) { return `<section class="product-notice"><strong>${escape(titleText)}</strong><span>${escape(body)}</span></section>`; }
  function metricCard(label, value, desc = "") { return `<article class="card metric-card"><h3>${escape(label)}</h3><strong>${escape(value)}</strong><span class="card-desc">${escape(desc)}</span></article>`; }
  function statusClass(level) { return level === "danger" ? "danger" : level === "warning" ? "warning" : "good"; }
  function tags(list = []) { return `<div class="dashboard-judgment-tags">${list.slice(0, 4).map((tag) => `<span>${escape(tag)}</span>`).join("")}</div>`; }
  function table(columns = [], rows = []) { return `<div class="report-table-wrap"><table class="report-table"><thead><tr>${columns.map((col) => `<th>${escape(col)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escape(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; }

  window.AppShell = { view, title, escape, setTitle, setView, setActive, notice, metricCard, statusClass, tags, table };
})();
