(function () {
  "use strict";

  const RESET_ENDPOINT = "/api/system/reset-runtime-data?confirm=true&scope=demo&include_audit_logs=true";
  let resetInFlight = false;

  function clearBrowserRuntime() {
    try {
      window.AppApi?.clearApiCaches?.();
    } catch (error) {}
    try {
      ["task_detail_state", "task_submit_state"].forEach((key) => localStorage.removeItem(key));
    } catch (error) {}
    try {
      for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
        const key = sessionStorage.key(index);
        if (key && (key.startsWith("api-cache:") || key.startsWith("task-detail-snapshot-"))) {
          sessionStorage.removeItem(key);
        }
      }
    } catch (error) {}
  }

  async function parseError(response) {
    try {
      const payload = await response.json();
      return payload?.detail || payload?.message || `${response.status} ${response.statusText}`;
    } catch (error) {
      return `${response.status} ${response.statusText}`;
    }
  }

  async function runGenerationReset(target) {
    if (resetInFlight) return;
    if (!window.confirm("清空当前比赛运行数据并切换到新的运行代际？历史审计/缓存会保留，但不会写回当前链路。")) return;

    resetInFlight = true;
    window.__runtimeGenerationResetInFlight = true;
    const oldText = target?.textContent || "清空";
    if (target) {
      target.disabled = true;
      target.textContent = "切换代际中";
    }

    // Unmount the report page first. Its cleanup stops the private pipeline-live poll
    // timer, so Reset no longer competes with the 7s direct polling request.
    try {
      window.AppRouter?.navigate?.("system-status");
    } catch (error) {}

    clearBrowserRuntime();

    try {
      const response = await fetch(RESET_ENDPOINT, {
        method: "POST",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(await parseError(response));
      const payload = await response.json();
      const generation = payload?.runtimeGeneration || payload?.generationTransition?.currentGeneration || {};
      try {
        sessionStorage.setItem("runtime-generation-hash", generation.generationHash || "");
        sessionStorage.setItem("runtime-generation-seq", String(generation.generationSeq || ""));
        sessionStorage.setItem("runtime-generation-reset-state", generation.state || "empty");
      } catch (error) {}
      clearBrowserRuntime();

      // Full reload is intentional: old route closures, poll timers and in-memory read
      // snapshots must not survive a Runtime Generation switch.
      window.location.assign("/#data-check");
    } catch (error) {
      resetInFlight = false;
      window.__runtimeGenerationResetInFlight = false;
      if (target) {
        target.disabled = false;
        target.textContent = oldText;
      }
      window.alert(`清空失败：${error?.message || String(error || "未知错误")}`);
    }
  }

  // Capture phase prevents the legacy report-page delegated handler from also calling
  // resetRuntimeData() followed by refreshAfterDataImport().
  document.addEventListener(
    "click",
    (event) => {
      const target = event.target?.closest?.("[data-reset-demo]");
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      runGenerationReset(target);
    },
    true
  );

  window.RuntimeGenerationResetV1 = {
    version: "1.0.0",
    endpoint: RESET_ENDPOINT,
    run: runGenerationReset,
  };
})();
