(function () {
  const VERSION = "22.5.10";
  const DEFAULT_VIEW_KEY = "operator-center";
  const memory = new Map();
  const inFlight = new Map();

  function userId() {
    return "competition_operator";
  }

  function storageKey(hash) {
    return `view-artifact:${VERSION}:${userId()}:${hash}`;
  }

  function readImmutable(hash) {
    if (!hash) return null;
    if (memory.has(hash)) return memory.get(hash);
    try {
      const raw = localStorage.getItem(storageKey(hash));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (parsed?.contentHash !== hash) {
        localStorage.removeItem(storageKey(hash));
        return null;
      }
      memory.set(hash, parsed.payload);
      return parsed.payload;
    } catch (error) {
      return null;
    }
  }

  function writeImmutable(hash, payload) {
    if (!hash || payload === undefined) return payload;
    memory.set(hash, payload);
    try {
      localStorage.setItem(storageKey(hash), JSON.stringify({ version: VERSION, contentHash: hash, payload }));
    } catch (error) {
      // Immutable Artifact cache is an acceleration layer only. Quota failure never
      // changes the business result and does not trigger old-view fallback.
    }
    return payload;
  }

  async function fetchJson(path, timeoutMs = 6000, options = {}) {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort("hash_view_timeout"), timeoutMs) : null;
    try {
      const response = await fetch(path, {
        method: "GET",
        signal: controller?.signal,
        cache: options.cache || "default",
        headers: {
          Accept: "application/json",
          ...(options.noStore ? { "Cache-Control": "no-cache", Pragma: "no-cache" } : {}),
        },
      });
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try { detail = (await response.json())?.detail || detail; } catch (error) {}
        throw new Error(detail);
      }
      return await response.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  function headPath(viewKey, dataVersion = "") {
    const query = new URLSearchParams();
    if (dataVersion) query.set("dataVersion", dataVersion);
    // Head is a mutable pointer. A nonce prevents intermediary/browser reuse while
    // immutable manifest/module documents remain content-hash cacheable.
    query.set("_headNonce", String(Date.now()));
    return `/api/view/head/${encodeURIComponent(viewKey)}?${query.toString()}`;
  }

  async function fetchHead(viewKey = DEFAULT_VIEW_KEY, dataVersion = "") {
    return fetchJson(headPath(viewKey, dataVersion), 5000, { cache: "no-store", noStore: true });
  }

  async function immutableArtifact(ref, expectedHash, viewKey = DEFAULT_VIEW_KEY) {
    const cached = readImmutable(expectedHash);
    if (cached !== null) return cached;
    const key = `${userId()}::${ref}::${expectedHash}`;
    if (inFlight.has(key)) return inFlight.get(key);
    const promise = fetchJson(`/api/view/artifacts/${encodeURIComponent(ref)}?viewKey=${encodeURIComponent(viewKey)}`, 8000)
      .then((document) => {
        if (expectedHash && document?.contentHash !== expectedHash) {
          throw new Error(`view_artifact_hash_mismatch:${ref}`);
        }
        return writeImmutable(document?.contentHash || expectedHash, document?.payload);
      })
      .finally(() => inFlight.delete(key));
    inFlight.set(key, promise);
    return promise;
  }

  async function moduleView(moduleKey, options = {}) {
    const viewKey = options.viewKey || DEFAULT_VIEW_KEY;
    const dataVersion = options.dataVersion || "";
    const head = await fetchHead(viewKey, dataVersion);
    if (!head?.manifestRef || !head?.manifestHash) {
      throw new Error(`view_manifest_not_ready:${viewKey}:${head?.status || "empty"}`);
    }
    const manifest = await immutableArtifact(head.manifestRef, head.manifestHash, viewKey);
    if (manifest?.scopeKey !== `${viewKey}::${userId()}`) {
      throw new Error(`view_manifest_scope_mismatch:${viewKey}`);
    }
    if (head?.status === "ready" && head?.runtimeStateHash && manifest?.runtimeStateHash !== head.runtimeStateHash) {
      throw new Error(`view_manifest_runtime_hash_mismatch:${viewKey}`);
    }
    const module = manifest?.modules?.[moduleKey];
    if (!module?.artifactRef || !module?.contentHash) {
      throw new Error(`view_module_missing:${viewKey}:${moduleKey}`);
    }
    const payload = await immutableArtifact(module.artifactRef, module.contentHash, viewKey);
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      return {
        ...payload,
        viewArtifact: {
          version: VERSION,
          viewKey,
          moduleKey,
          dataVersion: manifest?.dataVersion,
          runtimeStateHash: manifest?.runtimeStateHash || head?.runtimeStateHash || null,
          observedRuntimeStateHash: head?.observedRuntimeStateHash || null,
          manifestHash: head.manifestHash,
          moduleHash: module.contentHash,
          displayMode: head.displayMode || "current",
          pendingDataVersion: head.pendingDataVersion || null,
          pendingRuntimeStateHash: head.pendingRuntimeStateHash || null,
        },
      };
    }
    return payload;
  }

  function fallback(error, value) {
    return {
      ...value,
      ready: false,
      hashViewError: error?.message || String(error || "Hash视图读取失败"),
      hashViewVersion: VERSION,
    };
  }

  function filterProducts(payload, params = {}) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const filtered = params.storeId ? items.filter((item) => String(item?.storeId || "") === String(params.storeId)) : items;
    const limit = Math.max(1, Number(params.limit || 300));
    return { ...payload, items: filtered.slice(0, limit), count: Math.min(filtered.length, limit), ready: Boolean(filtered.length) };
  }

  function filterTasks(payload, params = {}) {
    const source = Array.isArray(payload?.items) ? payload.items : Array.isArray(payload?.tasks) ? payload.tasks : [];
    const filtered = params.status ? source.filter((item) => String(item?.status || item?.workflowStatus || "") === String(params.status)) : source;
    const limit = Math.max(1, Number(params.limit || 80));
    const items = filtered.slice(0, limit);
    return { ...payload, items, tasks: items, count: items.length, ready: Boolean(items.length) };
  }

  function install() {
    const api = window.AppApi;
    if (!api) return false;
    api.hashViewVersion = VERSION;
    api.viewHead = (viewKey = DEFAULT_VIEW_KEY, dataVersion = "") => fetchHead(viewKey, dataVersion);
    api.dashboardView = () => moduleView("dashboard").catch((error) => fallback(error, { counts: {}, topTasks: [] }));
    api.productView = (params = {}) => moduleView("products", { dataVersion: params.dataVersion || "" }).then((payload) => filterProducts(payload, params)).catch((error) => fallback(error, { items: [], count: 0 }));
    api.taskView = (params = {}) => moduleView("tasks", { dataVersion: params.dataVersion || "" }).then((payload) => filterTasks(payload, params)).catch((error) => fallback(error, { items: [], tasks: [], count: 0 }));
    api.systemStatusView = () => moduleView("systemStatus").catch((error) => fallback(error, { item: null }));
    api.dataLineView = () => moduleView("dataLine").catch((error) => fallback(error, { headline: "等待数据接入", lineStatus: "waiting", stations: [] }));
    api.pipelineLive = (dataVersion = "", limit = 40) => moduleView("pipeline", { dataVersion }).then((payload) => ({ ...payload, items: (payload?.items || []).slice(0, Math.max(1, Number(limit || 40))) })).catch((error) => fallback(error, { stages: [], items: [], summary: {}, headline: "等待数据接入", snapshotStatus: "hash_view_unavailable" }));
    return true;
  }

  if (!install()) {
    window.addEventListener("DOMContentLoaded", install, { once: true });
  }
})();