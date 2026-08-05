(function () {
  if (!window.AppApi) return;
  const api = window.AppApi;
  const originalConfirm = api.confirmReportImport;
  const originalMockAlerts = api.importMockAlerts;
  const userId = () => api.getCurrentUserId?.() || "U001";

  async function postJson(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": userId() },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function flattenImportJob(jobResult) {
    const result = jobResult?.result || jobResult || {};
    return { ...(result || {}), importJob: jobResult?.importJob, projectionJobs: jobResult?.projectionJobs || [], taskRepositorySync: result?.taskRepositorySync, importJobVersion: jobResult?.version || "5.1.9" };
  }

  async function syncReportTasks() {
    try {
      return await postJson("/api/data/report-tasks/sync-current", { source: "report_ui_manual_sync" });
    } catch (error) {
      console.warn("[report-task-sync] sync fallback", error);
      return { version: "5.1.9", mode: "report_task_sync_failed", syncedTaskCount: 0, error: error.message };
    }
  }

  api.syncReportTasks = syncReportTasks;

  api.confirmReportImport = async function (datasetName, rows, fieldMapping = {}) {
    try {
      const jobResult = await postJson("/api/data/import-jobs/confirm", { datasetName, rows, fieldMapping, autoCreateTasks: true });
      return flattenImportJob(jobResult);
    } catch (error) {
      console.warn("[report-task-sync] ImportJob confirm fallback", error);
      const result = await originalConfirm.apply(api, [datasetName, rows, fieldMapping]);
      const sync = await syncReportTasks();
      return { ...(result || {}), taskRepositorySync: sync, importJobFallback: true };
    }
  };

  api.importMockAlerts = async function (...args) {
    try {
      const jobResult = await postJson("/api/data/import-jobs/mock-alerts", {});
      return flattenImportJob(jobResult);
    } catch (error) {
      console.warn("[report-task-sync] ImportJob mock fallback", error);
      const result = await originalMockAlerts.apply(api, args);
      const sync = await syncReportTasks();
      return { ...(result || {}), taskRepositorySync: sync, importJobFallback: true };
    }
  };
})();
