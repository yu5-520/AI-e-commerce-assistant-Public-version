from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import (
    accounts,
    action_authority,
    approvals,
    artifacts_ops,
    audit,
    data_import,
    frontend_views,
    health,
    modules,
    ops,
    stations,
    system,
    task_lifecycle_stations,
    task_pool,
    task_snapshots,
)
from src.repositories.artifact_repository import ensure_artifact_tables
from src.runtime_version import (
    ACTIVE_AGENT1_TOKEN_IMPLEMENTATION,
    ACTIVE_WORKER_IMPLEMENTATION,
    AGENT1_INPUT_SCHEMA_VERSION,
    AGENT1_INPUT_SEMANTIC_VERSION,
    AGENT_BATCH_MANIFEST_CONTRACT,
    AGENT_EXECUTION_INDEX_NAME,
    API_SELF_DESCRIPTION_VERSION,
    CANONICAL_INTERFACE_DOCUMENT,
    FRONTEND_VIEW_MANIFEST_CONTRACT,
    HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
    INTERFACE_DOCUMENTATION_VERSION,
    RUNTIME_MODE,
    STABLE_HARD_INTERFACE_FACADE,
    STABLE_WORKER_IMPORT,
    THREE_AGENT_PIPELINE_VERSION,
    VERSION,
    runtime_versions,
)
from src.services.action_authority_v214_service import ensure_action_authority_tables
from src.services.agent_input_contract_v225_service import (
    AGENT1_MAX_BATCH_CHARS,
    AGENT1_MAX_ITEM_CHARS,
    AGENT2_MAX_BATCH_CHARS,
    AGENT2_MAX_ITEM_CHARS,
    AGENT3_MAX_BATCH_CHARS,
    AGENT3_MAX_ITEM_CHARS,
)
from src.services.agent_runtime_hard_interface_v2255_service import (
    agent_runtime_hard_interface_status,
    startup_agent_runtime_hard,
)
from src.services.artifact_storage_service import ensure_artifact_storage
from src.services.llm_gateway_v196_service import ensure_llm_cache_table, provider_runtime_config
from src.services.observed_signal_repair_v226_service import repair_misclassified_observations_v226
from src.services.pipeline_item_service import ensure_pipeline_item_tables
from src.services.pipeline_payload_retirement_service import (
    migrate_pipeline_payloads_to_artifacts,
    payload_retirement_status,
)
from src.services.release_identity_service import assert_release_identity, release_identity
from src.services.station_agent_worker_v2255_service import (
    start_station_queue_worker,
    stop_station_queue_worker,
)
from src.services.station_registry_service import registry_summary
from src.services.station_truth_repair_v225_service import repair_fake_completed_station_runs
from src.services.task_detail_snapshot_v2024_service import backfill_task_detail_snapshots

ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DEMO_DIR = ROOT_DIR / "web_demo"
WEB_INDEX_FILE = WEB_DEMO_DIR / "index.html"

app = FastAPI(title="AI ERP Operating Advisor API", version=VERSION)


def station_mainline() -> dict[str, Any]:
    registry = registry_summary()
    hard = agent_runtime_hard_interface_status()
    identity = release_identity(verify_content=False)
    return {
        "version": VERSION,
        "productVersion": VERSION,
        "contractVersion": VERSION,
        "publicApiVersion": VERSION,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "stateMachineVersion": THREE_AGENT_PIPELINE_VERSION,
        "agent1InputSemanticVersion": AGENT1_INPUT_SEMANTIC_VERSION,
        "agent1InputSchema": AGENT1_INPUT_SCHEMA_VERSION,
        "hashDirectedArtifactRuntimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "interfaceDocumentationVersion": INTERFACE_DOCUMENTATION_VERSION,
        "apiSelfDescriptionVersion": API_SELF_DESCRIPTION_VERSION,
        "canonicalInterfaceDocument": CANONICAL_INTERFACE_DOCUMENT,
        "mode": RUNTIME_MODE,
        "runtimeVersions": runtime_versions(),
        "releaseIdentity": identity,
        "agentRuntimeIntegrity": hard,
        "runtimeUnit": {
            "batchBoundary": "businessDataVersion",
            "streamingUnit": "pipelineItem",
            "stableWorkerImport": STABLE_WORKER_IMPORT,
            "activeWorkerImplementation": ACTIVE_WORKER_IMPLEMENTATION,
            "stableHardInterfaceFacade": STABLE_HARD_INTERFACE_FACADE,
            "activeAgent1TokenImplementation": ACTIVE_AGENT1_TOKEN_IMPLEMENTATION,
            "automaticAgentEntryOwner": "station_agent_worker_v2255_service",
            "agentRuntimeEntry": (
                "agent_runtime_hard_interface_v2255_service."
                "run_agent_pipeline_tick_hard"
            ),
            "stateMachineVersion": THREE_AGENT_PIPELINE_VERSION,
            "activeHardInterfaceVersion": hard.get("version"),
            "hashExecutionRuntimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
            "agentExecutionMode": "hash_directed_artifact_once_then_reference_only",
            "runtimeSource": "pipeline_items.artifact_refs_json",
            "artifactTransport": "exact_immutable_artifact_hash_reference_only",
            "agentInputTransport": "persisted_projection_artifact_only",
            "agent1InputSchema": AGENT1_INPUT_SCHEMA_VERSION,
            "tokenRuntime": "agent_token_runtime_hash_exact_v2259",
            "executionIndex": AGENT_EXECUTION_INDEX_NAME,
            "batchManifestContract": AGENT_BATCH_MANIFEST_CONTRACT,
            "frontendViewManifestContract": FRONTEND_VIEW_MANIFEST_CONTRACT,
            "releaseSource": "release.manifest.v1",
            "releaseAddress": identity.get("releaseHash"),
            "pipelinePayloadWriteMode": "artifact_ref_only",
            "stationCompletionMode": "real_adapter_and_business_contract_and_artifact",
            "stationDownstreamInput": "upstream_ART_reference",
            "runtimeReceiptStoredAsBusinessArtifact": False,
            "duplicateCompletedGateAllowed": False,
            "stringFallbackRefAllowed": False,
            "legacyPayloadRole": "retired",
            "legacyPayloadRuntimeFallbackAllowed": False,
            "legacySignalPoolFallbackAllowed": False,
            "invalidExistingRefFallbackAllowed": False,
            "legacyItemCacheOwnsBusinessReplay": False,
            "cachedBusinessOutputRebindingAllowed": False,
            "secondProjectionAppliedToMaterializedAgent1Input": False,
            "alternateRuntimeAllowed": False,
            "agent1PendingIsRunnable": True,
            "agent1RuntimeSource": (
                "artifactRefs.agent1InputRef.v3+inputContentHash+executionHash"
            ),
            "agent2RuntimeSource": (
                "artifactRefs.agent2DraftInputRef+inputContentHash"
            ),
            "agent3RuntimeSource": (
                "artifactRefs.agent3SopInputRef+inputContentHash"
            ),
            "fullSignalReadByAgentAllowed": False,
            "fullCapabilityReadByAgentAllowed": False,
            "fullUpstreamArtifactReadByAgent3Allowed": False,
            "unprojectedProviderInputAllowed": False,
            "agent1ClaimMode": "finite_execution_hash_lease_native",
            "agent2DraftClaimMode": "finite_sqlite_lease_native",
            "agent3ClaimMode": "finite_sqlite_lease_with_claim_ownership",
            "scoreCanBlockAgent1": False,
            "executionLockContract": "one_problem_one_action_one_owner_one_target",
            "fullAgent1DiagnosisDownstream": False,
            "agent1BatchItemIdentity": "itemExecutionId+inputContentHash",
            "agent1MaximumItemsPerProviderCall": 8,
            "onlyTrueMissingItemsRetry": True,
        },
        "mainline": [
            "真实报表与事实命名空间",
            "商品快照、最近五份直接比较与历史趋势",
            "统一经营证据合同",
            "商品信号业务制品signalRef",
            "传输系统编译并持久化agent1InputRef v3",
            "Hash校验agent1InputRef并生成executionHash和itemExecutionId",
            "Agent1最多8商品微批次；逐Hash验收并拆分单商品输出Artifact",
            "证据充分时锁定唯一主问题、主动作、责任人与执行对象，否则观察沉淀",
            "动作能力、权限与数字制品capabilityRef",
            "传输系统编译agent2DraftInputRef",
            "Agent2只读取executionLock与能力边界，生成垂直类目和平台化动作草案",
            "传输系统编译agent3SopInputRef",
            "Agent3只读取agent3SopInputRef结合公司RAG生成最终SOP",
            "确定性任务映射，不增加业务步骤",
            "任务池、生命周期与自动复盘",
            "业务变化物化前端模块Artifact并原子切换Manifest Head",
            "审核后经验回流",
        ],
        "contracts": {
            "release": "sourceCommit + releaseHash identify the only deployable file set",
            "observe": "actionFamily=null and stops before Agent2",
            "act": "one evidence-backed problem, action, owner and execution target; one immutable action family",
            "executionLock": "Agent1 full diagnosis is audit-only; Agent2 receives one canonical execution lock",
            "pipelineInput": "current hard-stage input ref is the only Agent runtime input",
            "agent1Input": (
                "signalRef is audit-only; Agent1 consumes agent_input.agent1.v3 "
                "from agent1InputRef"
            ),
            "agent1ExecutionIdentity": (
                "itemExecutionId+inputContentHash match the exact executionHash"
            ),
            "agent1Batch": (
                "up to eight independently addressed input Artifacts per Provider call"
            ),
            "agent1Retry": (
                "only a raw itemExecutionId omission is true missing; Hash and output "
                "contract errors do not retry"
            ),
            "agentResultReplay": (
                "executionHash resolves one immutable accepted output Artifact"
            ),
            "cachedOutputRebinding": "forbidden",
            "agent2Input": "capabilityRef is audit-only; Agent2 consumes agent2DraftInputRef",
            "agent3Input": "Agent2 draft Artifact is audit source; Agent3 consumes agent3SopInputRef",
            "agent2": "vertical and platform detail inside the Agent1 execution lock; no re-diagnosis, second target, final SOP or lifecycle state",
            "agent3": "company-aware SOP inside Agent1 lock, Agent2 draft and authority boundaries",
            "taskMapping": "deterministic Agent3 projection with compilerAddedStepCount=0",
            "tokenRuntime": "validated persisted projection Artifacts only; no second lossy projection",
            "frontendView": (
                "View Head manifestHash and module contentHash drive incremental transfer and rendering"
            ),
            "stationTruth": "failed adapter never becomes completed queue state or business Artifact",
            "businessArtifact": "runtime receipt and business output are stored separately",
            "evidence": "one builder and one validator own operatingEvidenceGraph.v1",
            "signalAdmission": "meaningful or structural evidence enters Agent1; score only orders throughput",
            "agent1Lease": "execution_hash, claim_id, lease_expires_at and owner are written before Provider calls",
            "agent2DraftLease": "bounded lease and item-level Provider proof",
            "agent3Lease": "bounded lease, claim ownership and stale-result rejection",
            "pipelineReadModel": "stable node codes; batch stations, current product states and historical completions use separate count bases",
            "legacyPayload": "retired after one-time Artifact migration",
            "capabilityCompiler": "facts, executable objects, permissions and numeric limits only",
            "task": "one Agent3 SOP item equals one idempotent admission attempt",
            "publicTaskDto": "operator fields only; no Provider proof or internal package",
            "canonicalActionField": "activeActionContract",
            "canonicalInterfaceDocument": CANONICAL_INTERFACE_DOCUMENT,
            "fallbackAllowed": False,
        },
        "tokenBudgets": {
            "agent1ItemChars": AGENT1_MAX_ITEM_CHARS,
            "agent1BatchChars": AGENT1_MAX_BATCH_CHARS,
            "agent2DraftItemChars": AGENT2_MAX_ITEM_CHARS,
            "agent2DraftBatchChars": AGENT2_MAX_BATCH_CHARS,
            "agent3SopItemChars": AGENT3_MAX_ITEM_CHARS,
            "agent3SopBatchChars": AGENT3_MAX_BATCH_CHARS,
            "overBudgetBehavior": "deterministic_split_or_fail_before_provider",
        },
        "registry": registry,
        "rule": (
            "V22.4 owns the hash-sealed release; V22.5.5 owns the evidence-backed "
            "three-Agent state machine; V22.5.9 owns exact Artifact execution and views."
        ),
    }


for route_module in [
    health,
    accounts,
    action_authority,
    approvals,
    artifacts_ops,
    audit,
    data_import,
    frontend_views,
    modules,
    ops,
    stations,
    system,
    task_lifecycle_stations,
    task_pool,
    task_snapshots,
]:
    app.include_router(route_module.router)

if not WEB_DEMO_DIR.is_dir():
    raise RuntimeError(f"Frontend directory is missing: {WEB_DEMO_DIR}")

app.mount("/web_demo", StaticFiles(directory=str(WEB_DEMO_DIR)), name="web_demo")


@app.get("/", include_in_schema=False)
def frontend_index() -> FileResponse:
    if not WEB_INDEX_FILE.exists():
        raise HTTPException(status_code=503, detail="Frontend index file is missing")
    return FileResponse(
        WEB_INDEX_FILE,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/version")
def api_version() -> dict[str, Any]:
    hard = agent_runtime_hard_interface_status()
    identity = release_identity(verify_content=False)
    return {
        "version": VERSION,
        "productVersion": VERSION,
        "contractVersion": VERSION,
        "publicApiVersion": VERSION,
        "runtimeMode": RUNTIME_MODE,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "stateMachineVersion": THREE_AGENT_PIPELINE_VERSION,
        "agent1InputSemanticVersion": AGENT1_INPUT_SEMANTIC_VERSION,
        "agent1InputSchema": AGENT1_INPUT_SCHEMA_VERSION,
        "hashDirectedArtifactRuntimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "interfaceDocumentationVersion": INTERFACE_DOCUMENTATION_VERSION,
        "apiSelfDescriptionVersion": API_SELF_DESCRIPTION_VERSION,
        "canonicalInterfaceDocument": CANONICAL_INTERFACE_DOCUMENT,
        "executionIndex": AGENT_EXECUTION_INDEX_NAME,
        "batchManifestContract": AGENT_BATCH_MANIFEST_CONTRACT,
        "frontendViewManifestContract": FRONTEND_VIEW_MANIFEST_CONTRACT,
        "cachedOutputRebindingAllowed": False,
        "runtimeVersions": runtime_versions(),
        "releaseIdentity": identity,
        "agentHardInterface": hard,
        "stationMainline": station_mainline(),
        "llmProviders": {
            "agent1": provider_runtime_config("product_judgment_agent"),
            "agent2Draft": provider_runtime_config("action_plan_judgment_agent"),
            "agent3Sop": provider_runtime_config("task_mapping_agent"),
        },
        "artifactHub": {
            "enabled": True,
            "mode": "reference_only",
            "pipelinePayloadWriteMode": "artifact_ref_only",
            "agentInputRefs": [
                "agent1InputRef",
                "agent2DraftInputRef",
                "agent3SopInputRef",
            ],
            "agentExecutionRefs": [
                "agentExecutionInputRef",
                "agentExecutionOutputRef",
                "agentRawBatchOutputRef",
                "batchManifestRef",
            ],
            "executionIndex": AGENT_EXECUTION_INDEX_NAME,
            "batchManifestContract": AGENT_BATCH_MANIFEST_CONTRACT,
            "frontendViewManifestContract": FRONTEND_VIEW_MANIFEST_CONTRACT,
            "fullBusinessArtifactsAuditOnly": True,
            "legacyPayloadRole": "retired",
            "legacyPayloadRuntimeFallbackAllowed": False,
            "legacySignalPoolFallbackAllowed": False,
            "legacyItemCacheOwnsBusinessReplay": False,
            "cachedBusinessOutputRebindingAllowed": False,
            "invalidExistingRefFallbackAllowed": False,
            "payloadRetirement": payload_retirement_status(),
            "rawContentPubliclyReturned": False,
        },
        "stationTruth": {
            "version": "22.2.5",
            "operatingEvidenceContract": "21.5.0",
            "realFailureCanBecomeCompleted": False,
            "runtimeReceiptCanBecomeBusinessArtifact": False,
            "duplicateCompletedGateAllowed": False,
            "stringFallbackRefAllowed": False,
            "downstreamReferenceMode": "ART_reference_only",
            "preAgentQueueEndsAt": "product_signal_admission_station",
        },
        "agentFlow": {
            "version": THREE_AGENT_PIPELINE_VERSION,
            "stateMachineVersion": THREE_AGENT_PIPELINE_VERSION,
            "hardInterfaceVersion": THREE_AGENT_PIPELINE_VERSION,
            "activeHardInterfaceVersion": hard.get("version"),
            "hashDirectedArtifactRuntimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
            "activeBusinessPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
            "agent1InputSemanticVersion": AGENT1_INPUT_SEMANTIC_VERSION,
            "agent1InputSchema": AGENT1_INPUT_SCHEMA_VERSION,
            "stableWorkerImport": STABLE_WORKER_IMPORT,
            "activeWorkerImplementation": ACTIVE_WORKER_IMPLEMENTATION,
            "stableHardInterfaceFacade": STABLE_HARD_INTERFACE_FACADE,
            "activeAgent1TokenImplementation": ACTIVE_AGENT1_TOKEN_IMPLEMENTATION,
            "signalAdmissionPolicy": "evidence_trigger_for_agent1_score_for_priority_only",
            "scoreCanBlockAgent1": False,
            "agent1PendingIsRunnable": True,
            "agent1RuntimeSource": (
                "artifactRefs.agent1InputRef.v3+inputContentHash+executionHash"
            ),
            "agent2RuntimeSource": (
                "artifactRefs.agent2DraftInputRef+inputContentHash"
            ),
            "agent3RuntimeSource": (
                "artifactRefs.agent3SopInputRef+inputContentHash"
            ),
            "executionIndex": AGENT_EXECUTION_INDEX_NAME,
            "batchManifestContract": AGENT_BATCH_MANIFEST_CONTRACT,
            "frontendViewManifestContract": FRONTEND_VIEW_MANIFEST_CONTRACT,
            "batchItemIdentity": "itemExecutionId+inputContentHash",
            "maximumAgent1ItemsPerProviderCall": 8,
            "onlyTrueMissingItemsRetry": True,
            "cachedOutputRebindingAllowed": False,
            "legacyItemCacheOwnsBusinessReplay": False,
            "agent1ClaimMode": "finite_execution_hash_lease_native",
            "agent2DraftClaimMode": "finite_sqlite_lease_native",
            "agent3SopClaimMode": "finite_sqlite_lease_with_claim_ownership",
            "unprojectedProviderInputAllowed": False,
            "secondProjectionAppliedToMaterializedAgent1Input": False,
            "fullSignalReadByAgentAllowed": False,
            "fullCapabilityReadByAgentAllowed": False,
            "fullUpstreamArtifactReadByAgent3Allowed": False,
            "fullAgent1DiagnosisDownstream": False,
            "executionLockContract": "one_problem_one_action_one_owner_one_target",
            "legacySignalPoolRead": False,
            "legacySignalPoolWrite": False,
            "pipelineLiveLayers": [
                "batchStations",
                "currentProductItems",
                "historicalCompletions",
            ],
            "batchTokenAddedToProductCount": False,
            "taskMappingMode": "deterministic_agent3_projection_only",
            "compilerAddedStepCount": 0,
        },
        "frontendView": {
            "version": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
            "headRoute": "/api/view/head/{view_key}",
            "artifactRoute": "/api/view/artifacts/{artifact_ref}",
            "refreshRoute": "/api/view/refresh",
            "headIdentity": "manifestHash",
            "moduleIdentity": "contentHash",
            "crossDataVersionFallbackAllowed": False,
        },
        "publicApiBoundary": {
            "taskDtoVersion": "22.2.3",
            "internalAgentPayloadReturned": False,
            "providerProofReturned": False,
            "artifactMetadataRoute": "/api/ops/artifacts",
        },
        "routerMounted": True,
        "frontendStaticMounted": True,
        "frontendStaticPath": "/web_demo",
    }


@app.on_event("startup")
def startup_station_queue_worker() -> None:
    assert_release_identity()
    ensure_artifact_storage()
    ensure_artifact_tables()
    ensure_pipeline_item_tables()
    migrate_pipeline_payloads_to_artifacts(limit=100000, fail_on_unmigrated=True)
    repair_fake_completed_station_runs(limit=5000)
    repair_misclassified_observations_v226(limit=10000)
    ensure_action_authority_tables()
    ensure_llm_cache_table()
    backfill_task_detail_snapshots(limit=300)
    startup_agent_runtime_hard()
    start_station_queue_worker(worker_id="fastapi-release-sealed-v2255-worker")


@app.on_event("shutdown")
def shutdown_station_queue_worker() -> None:
    stop_station_queue_worker()
