"""V22.4 system routes with layered V22.5 Agent and interface status."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

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
from src.services.account_service import current_user, user_id_from_headers
from src.services.agent_pipeline_governance_v213_service import runtime_governance_summary
from src.services.agent_pipeline_item_worker_v2010_service import agent_pipeline_status
from src.services.agent_runtime_hard_interface_v2255_service import (
    agent_runtime_hard_interface_status,
)
from src.services.data_identity_service import data_identity
from src.services.release_identity_service import release_identity
from src.services.station_agent_worker_v2255_service import run_worker_tick, worker_status
from src.services.system_service import clear_runtime_data as clear_runtime_store
from src.services.system_service import get_db_status

router = APIRouter(prefix="/api/system", tags=["system"])


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def request_context_meta(request: Request) -> Dict[str, Any]:
    user_id = request_user_id(request)
    user = current_user(user_id)
    return {
        "userId": user_id,
        "roleId": user.get("roleId") or user.get("role_id") or "operator",
        "tenantId": user.get("tenantId") or user.get("tenant_id") or "demo_tenant",
        "source": "release_sealed_system_route",
        "version": VERSION,
        "interfaceDocumentationVersion": INTERFACE_DOCUMENTATION_VERSION,
    }


def _worker_release_hash(background: Dict[str, Any]) -> str | None:
    direct = background.get("releaseIdentity")
    if isinstance(direct, dict) and direct.get("releaseHash"):
        return str(direct["releaseHash"])
    config = background.get("config")
    if isinstance(config, dict):
        nested = config.get("releaseIdentity")
        if isinstance(nested, dict) and nested.get("releaseHash"):
            return str(nested["releaseHash"])
    return None


def _layered_contract_status() -> Dict[str, Any]:
    return {
        "publicApiVersion": VERSION,
        "stateMachineVersion": THREE_AGENT_PIPELINE_VERSION,
        "agent1InputSemanticVersion": AGENT1_INPUT_SEMANTIC_VERSION,
        "agent1InputSchema": AGENT1_INPUT_SCHEMA_VERSION,
        "hashDirectedArtifactRuntimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "interfaceDocumentationVersion": INTERFACE_DOCUMENTATION_VERSION,
        "apiSelfDescriptionVersion": API_SELF_DESCRIPTION_VERSION,
        "canonicalInterfaceDocument": CANONICAL_INTERFACE_DOCUMENT,
        "stableWorkerImport": STABLE_WORKER_IMPORT,
        "activeWorkerImplementation": ACTIVE_WORKER_IMPLEMENTATION,
        "stableHardInterfaceFacade": STABLE_HARD_INTERFACE_FACADE,
        "activeAgent1TokenImplementation": ACTIVE_AGENT1_TOKEN_IMPLEMENTATION,
        "executionIndex": AGENT_EXECUTION_INDEX_NAME,
        "batchManifestContract": AGENT_BATCH_MANIFEST_CONTRACT,
        "frontendViewManifestContract": FRONTEND_VIEW_MANIFEST_CONTRACT,
        "batchItemIdentity": "itemExecutionId+inputContentHash",
        "agent1MaximumItemsPerProviderCall": 8,
        "onlyTrueMissingItemsRetry": True,
        "legacyItemCacheOwnsBusinessReplay": False,
        "cachedOutputRebindingAllowed": False,
        "secondProjectionAppliedToMaterializedAgent1Input": False,
        "crossDataVersionViewFallbackAllowed": False,
    }


@router.get("/release-identity")
def release_identity_view(
    verifyContent: bool = Query(
        default=False,
        description=(
            "Recompute every sealed file hash. The default fast path returns the "
            "startup-verified cached identity for health checks."
        ),
    ),
) -> Dict[str, Any]:
    identity = release_identity(verify_content=verifyContent)
    background = worker_status(include_queue=False)
    worker_hash = _worker_release_hash(background)
    active_hash = identity.get("releaseHash")
    identity.update(
        verificationDepth="content" if verifyContent else "startup_verified_cache",
        contentVerificationRequested=verifyContent,
        workerReleaseHash=worker_hash,
        workerReleaseMatch=bool(
            identity.get("verified") and active_hash and worker_hash == active_hash
        ),
        workerProcessId=(background.get("state") or {}).get("processId"),
        workerRuntimeRoot=(background.get("state") or {}).get("runtimeRoot"),
        backgroundWorkerVersion=background.get("version"),
        threeAgentPipelineVersion=THREE_AGENT_PIPELINE_VERSION,
        stateMachineVersion=THREE_AGENT_PIPELINE_VERSION,
        hashDirectedArtifactRuntimeVersion=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        interfaceDocumentationVersion=INTERFACE_DOCUMENTATION_VERSION,
        canonicalInterfaceDocument=CANONICAL_INTERFACE_DOCUMENT,
    )
    return identity


@router.get("/data-identity")
def data_identity_view(contentHash: bool = Query(default=False)) -> Dict[str, Any]:
    result = data_identity(include_content_hash=contentHash)
    active_release = release_identity(verify_content=False)
    result.update(
        productVersion=VERSION,
        runtimeMode=RUNTIME_MODE,
        activeReleaseHash=active_release.get("releaseHash"),
        releaseMatch=bool(
            result.get("releaseHash")
            and result.get("releaseHash") == active_release.get("releaseHash")
        ),
        runtimeContracts=_layered_contract_status(),
    )
    return result


@router.get("/db-status")
def db_status() -> Dict[str, Any]:
    result = get_db_status()
    if isinstance(result, dict):
        result["version"] = VERSION
        result["productVersion"] = VERSION
        result["runtimeMode"] = RUNTIME_MODE
        result["runtimeVersions"] = runtime_versions()
        result["runtimeContracts"] = _layered_contract_status()
        result["releaseIdentity"] = release_identity(verify_content=False)
        result["dataIdentity"] = data_identity(include_content_hash=False)
        result["runtimeGovernance"] = runtime_governance_summary()
        result["agentRuntimeIntegrity"] = agent_runtime_hard_interface_status()
    return result


@router.get("/agent-pipeline-status")
def agent_pipeline_status_view(dataVersion: str | None = None) -> Dict[str, Any]:
    background = worker_status(include_queue=False)
    state = background.get("state") if isinstance(background, dict) else {}
    selected = dataVersion or (
        state.get("lastSelectedDataVersion") if isinstance(state, dict) else None
    )
    identity = release_identity(verify_content=False)
    worker_hash = _worker_release_hash(background)
    hard = agent_runtime_hard_interface_status()
    result = agent_pipeline_status(selected)
    result.update(
        version=VERSION,
        routeVersion=VERSION,
        productVersion=VERSION,
        contractVersion=VERSION,
        publicApiVersion=VERSION,
        runtimeMode=RUNTIME_MODE,
        threeAgentPipelineVersion=THREE_AGENT_PIPELINE_VERSION,
        stateMachineVersion=THREE_AGENT_PIPELINE_VERSION,
        agent1InputSemanticVersion=AGENT1_INPUT_SEMANTIC_VERSION,
        agent1InputSchema=AGENT1_INPUT_SCHEMA_VERSION,
        hashDirectedArtifactRuntimeVersion=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        interfaceDocumentationVersion=INTERFACE_DOCUMENTATION_VERSION,
        apiSelfDescriptionVersion=API_SELF_DESCRIPTION_VERSION,
        canonicalInterfaceDocument=CANONICAL_INTERFACE_DOCUMENT,
        executionIndex=AGENT_EXECUTION_INDEX_NAME,
        batchManifestContract=AGENT_BATCH_MANIFEST_CONTRACT,
        frontendViewManifestContract=FRONTEND_VIEW_MANIFEST_CONTRACT,
        cachedOutputRebindingAllowed=False,
        runtimeVersions=runtime_versions(),
        runtimeContracts=_layered_contract_status(),
        releaseIdentity=identity,
        dataIdentity=data_identity(include_content_hash=False),
        sourceCommit=identity.get("sourceCommit"),
        releaseHash=identity.get("releaseHash"),
        workerReleaseHash=worker_hash,
        workerReleaseMatch=bool(
            identity.get("verified")
            and identity.get("releaseHash")
            and worker_hash == identity.get("releaseHash")
        ),
        requestedDataVersion=dataVersion,
        selectedRunnableDataVersion=selected,
        backgroundWorker=background,
        runtimeGovernance=runtime_governance_summary(),
        agentRuntimeIntegrity=hard,
        canonicalActionField="activeActionContract",
        agent1RuntimeSource=(
            "artifactRefs.agent1InputRef.v3+inputContentHash+executionHash"
        ),
        agent2RuntimeSource="artifactRefs.agent2DraftInputRef+inputContentHash",
        agent3RuntimeSource="artifactRefs.agent3SopInputRef+inputContentHash",
        agent1ExecutionMode="hash_directed_artifact_once_then_reference_only",
        batchItemIdentity="itemExecutionId+inputContentHash",
        maximumAgent1ItemsPerProviderCall=8,
        onlyTrueMissingItemsRetry=True,
        legacyItemCacheOwnsBusinessReplay=False,
        secondProjectionAppliedToMaterializedAgent1Input=False,
        pipelineStages={
            "agent1": [
                "agent1_pending",
                "agent1_running",
                "agent1_completed",
                "observed_soft_gate",
                "agent1_output_invalid",
                "agent1_failed",
            ],
            "agent2Draft": [
                "action_pack_ready",
                "agent2_draft_input_invalid",
                "agent2_running",
                "agent2_draft_ready",
                "agent2_draft_output_invalid",
                "agent2_draft_failed",
            ],
            "agent3Sop": [
                "agent3_sop_running",
                "agent3_sop_ready",
                "agent3_sop_output_invalid",
                "agent3_sop_failed",
            ],
            "taskMapping": ["task_mapped", "task_mapping_failed"],
            "taskPool": ["task_admitted"],
        },
        unprojectedProviderInputAllowed=False,
        fallbackAllowed=False,
        routeRule=(
            "The transport system resolves one exact input Artifact per item; Agent1 "
            "uses itemExecutionId+inputContentHash inside up-to-eight-item batches; "
            "Agent2 and Agent3 remain inside the V22.5.5 execution lock; deterministic "
            "mapping admits tasks."
        ),
    )
    return result


@router.post("/run-agent-pipeline-tick")
def run_agent_pipeline_tick_view(
    request: Request,
    limit: int = Query(default=8, ge=1, le=40),
) -> Dict[str, Any]:
    identity = release_identity(verify_content=False)
    result = run_worker_tick(
        worker_id=f"manual-release-sealed-{request_user_id(request)}",
        limit=limit,
    )
    result.update(
        version=VERSION,
        routeVersion=VERSION,
        productVersion=VERSION,
        contractVersion=VERSION,
        publicApiVersion=VERSION,
        runtimeMode=RUNTIME_MODE,
        threeAgentPipelineVersion=THREE_AGENT_PIPELINE_VERSION,
        stateMachineVersion=THREE_AGENT_PIPELINE_VERSION,
        agent1InputSemanticVersion=AGENT1_INPUT_SEMANTIC_VERSION,
        agent1InputSchema=AGENT1_INPUT_SCHEMA_VERSION,
        hashDirectedArtifactRuntimeVersion=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        interfaceDocumentationVersion=INTERFACE_DOCUMENTATION_VERSION,
        canonicalInterfaceDocument=CANONICAL_INTERFACE_DOCUMENT,
        executionIndex=AGENT_EXECUTION_INDEX_NAME,
        batchManifestContract=AGENT_BATCH_MANIFEST_CONTRACT,
        cachedOutputRebindingAllowed=False,
        runtimeContracts=_layered_contract_status(),
        releaseIdentity=identity,
        dataIdentity=data_identity(include_content_hash=False),
        agentRuntimeIntegrity=agent_runtime_hard_interface_status(),
        routeRule=(
            "Manual execution uses the same single release-sealed Worker, exact "
            "Artifact execution index and V22.5.5 pipeline state machine. It cannot "
            "bypass projection, Hash identity, proof, lease or Token contracts."
        ),
    )
    return result


@router.get("/isolation")
def backend_isolation(request: Request) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "productVersion": VERSION,
        "runtimeMode": RUNTIME_MODE,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "stateMachineVersion": THREE_AGENT_PIPELINE_VERSION,
        "hashDirectedArtifactRuntimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "interfaceDocumentationVersion": INTERFACE_DOCUMENTATION_VERSION,
        "canonicalInterfaceDocument": CANONICAL_INTERFACE_DOCUMENT,
        "status": "release_data_system_agent_and_artifact_boundaries_hard",
        "currentContext": request_context_meta(request),
        "releaseIdentity": release_identity(verify_content=False),
        "dataIdentity": data_identity(include_content_hash=False),
        "runtimeContracts": _layered_contract_status(),
        "agentRuntimeIntegrity": agent_runtime_hard_interface_status(),
        "rule": (
            "Every release, data generation, system, Agent stage, exact execution "
            "and frontend view has one versioned identity and one allowed owner."
        ),
    }


def _clear_runtime_data(
    confirm: bool,
    include_audit_logs: bool,
    reason: str = "manual_reset",
    scope: str = "demo",
) -> Dict[str, Any]:
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to clear generated runtime data.",
        )
    result = clear_runtime_store(
        include_audit_logs=include_audit_logs,
        reason=reason,
        scope=scope,
    )
    if isinstance(result, dict):
        result["version"] = VERSION
        result["productVersion"] = VERSION
        result["contractVersion"] = VERSION
        result["runtimeMode"] = RUNTIME_MODE
        result["threeAgentPipelineVersion"] = THREE_AGENT_PIPELINE_VERSION
        result["stateMachineVersion"] = THREE_AGENT_PIPELINE_VERSION
        result["hashDirectedArtifactRuntimeVersion"] = (
            HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION
        )
        result["interfaceDocumentationVersion"] = INTERFACE_DOCUMENTATION_VERSION
        result["canonicalInterfaceDocument"] = CANONICAL_INTERFACE_DOCUMENT
        result["runtimeContracts"] = _layered_contract_status()
        result["releaseIdentity"] = release_identity(verify_content=False)
        result["dataIdentity"] = data_identity(include_content_hash=False)
        result["runtimeGovernance"] = runtime_governance_summary()
        result["agentRuntimeIntegrity"] = agent_runtime_hard_interface_status()
    return result


@router.post("/reset-runtime-data")
def reset_runtime_data(
    confirm: bool = Query(default=False),
    include_audit_logs: bool = Query(default=True),
    scope: str = Query(default="demo"),
) -> Dict[str, Any]:
    return _clear_runtime_data(
        confirm=confirm,
        include_audit_logs=include_audit_logs,
        reason=f"manual_release_runtime_reset_scope_{scope or 'demo'}",
        scope=scope,
    )
