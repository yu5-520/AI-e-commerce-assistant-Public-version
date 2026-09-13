"""V26.9.A graph orchestration inside the existing pipeline worker.

This is not a second worker. The registered hard runtime calls these deterministic
stage helpers when the current pipeline item already carries the 26.9 contract.
Agent1's DecisionGraph is admitted and partitioned by the system, each partition gets
an exact persisted Agent2 input Artifact, and only a complete merge advances to Agent3.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import v269_input_migration_service as migration
from src.services import v269_semantic_graph_service as graphs
from src.services.agent_input_contract_v225_service import AGENT2_DRAFT_INPUT_SCHEMA
from src.services.artifact_transport_service import inspect_artifact, store_artifact
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.agent_runtime_contract_v225_service import payload_from_row

VERSION = "26.9.0"
AGENT1_COMPLETED_STAGE = "agent1_completed"
AGENT2_DRAFT_READY_STAGE = "agent2_draft_ready"
GRAPH_HOLD_STAGE = "observed_soft_gate"


def _rows(data_version: str | None, limit: int = 8) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM pipeline_items
               WHERE COALESCE(data_version,'')=COALESCE(?,'')
                 AND current_stage=? AND status IN ('queued','ready','retry')
               ORDER BY priority ASC,updated_at ASC LIMIT ?""",
            (data_version, AGENT1_COMPLETED_STAGE, max(1, min(20, int(limit)))),
        ).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        try:
            payload=payload_from_row(item)
        except Exception:
            continue
        if migration.uses_graph_contract(payload):
            result.append(item)
    return result


def pending_graph_agent1_count(data_version: str | None) -> int:
    return len(_rows(data_version, 100000))


def _source_ref(item: Dict[str, Any]) -> str:
    refs=artifact_refs_from_row(item)
    candidate=str(refs.get('agent1Ref') or item.get('payload_artifact_ref') or refs.get('currentStageRef') or '')
    graphs.require(candidate.startswith('ART-'),'agent1_graph_source_ref_missing')
    return candidate


def _source_hash(ref: str) -> str:
    metadata=inspect_artifact(ref)
    value=str(metadata.get('contentHash') or metadata.get('content_hash') or '')
    graphs.require(bool(value),'graph_source_hash_missing')
    return value


def _store_agent2_input(item: Dict[str, Any], envelope: Dict[str, Any], *, source_ref: str, partition_hash: str) -> str:
    artifact=store_artifact(
        artifact_type=AGENT2_DRAFT_INPUT_SCHEMA,
        value=envelope,
        schema_version=VERSION,
        tenant_id=item.get('tenant_id'),
        store_id=item.get('store_id'),
        product_id=item.get('product_id'),
        data_version=item.get('data_version'),
        created_by='v269_pipeline_orchestration_service',
        parent_refs=[source_ref],
        metadata={
            'pipelineItemId':item.get('item_id'),
            'semanticContractVersion':VERSION,
            'partitionHash':partition_hash,
            'fallbackAllowed':False,
        },
    )
    return str(artifact['artifactId'])


def _finish(item: Dict[str, Any], *, stage: str, status: str, payload: Dict[str, Any], output_ref: str) -> Dict[str, Any]:
    envelope=build_item_envelope(
        data_version=item.get('data_version') or payload.get('dataVersion'),
        item_id=item.get('item_id'),
        product_id=item.get('product_id') or payload.get('productId'),
        store_id=item.get('store_id') or payload.get('storeId'),
        signal_id=item.get('signal_id') or payload.get('signalId'),
        package_id=item.get('package_id') or payload.get('packageId'),
        action_family=None,
        route=None,
        input_ref=f"pipeline_items:{item.get('current_stage')}:{item.get('item_id')}",
        output_ref=output_ref,
        stage=stage,
        artifact_refs=artifact_refs_from_row(item),
    )
    envelope=upsert_pipeline_item(
        envelope,
        stage=stage,
        status=status,
        priority=int(item.get('priority') or 50),
        output_ref=output_ref,
        payload=payload,
    )
    artifact_ref=str(envelope.get('payloadArtifactRef') or '')
    if stage==AGENT2_DRAFT_READY_STAGE and artifact_ref.startswith('ART-'):
        attach_pipeline_artifact_ref(str(item.get('item_id')),'agent2DraftRef',artifact_ref,make_current=True)
    record_pipeline_item_event(
        envelope,
        station_id='v269_graph_partition_merge_station',
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    return envelope


def _registered_allowed_actions(decision: Dict[str, Any]) -> List[str]:
    """System permission set for A: only registered action-family nodes are eligible.

    Financial/operational limits are intentionally not granted here; whole-plan runtime
    authorization happens after Agent2. Enterprise action restrictions can later narrow
    this explicit set without changing Agent1 semantics.
    """
    nodes=graphs.index(decision)
    domains=graphs.contract()['actionFamilyDomains']
    return sorted(
        key for key,node in nodes.items()
        if node.get('kind')=='DecisionActionNode' and node.get('actionFamily') in domains
    )


def run_agent2_graph_partition_microbatch(
    data_version: str | None,
    *,
    batch_size: int = 2,
) -> Dict[str, Any]:
    from src.services.agent_token_runtime_v225_service import run_agent2_draft_projected_inputs

    items=_rows(data_version,max(1,min(8,int(batch_size or 2))))
    merged=held=failed=0
    details=[]
    for item in items:
        try:
            package=dict(payload_from_row(item))
            decision=package.get('DecisionGraph');graphs.index(decision)
            fact_values=deepcopy((package.get('BusinessFacts') or {}).get('factValues') or {})
            knowledge=deepcopy(package.get('knowledgeContext') or {})
            source_ref=_source_ref(item);source_hash=_source_hash(source_ref)
            allowed=_registered_allowed_actions(decision)
            admission=graphs.admit_actions(decision,allowed)
            partitions=graphs.partition_actions(decision,admission)
            if not partitions:
                held+=1
                _finish(
                    item,stage=GRAPH_HOLD_STAGE,status='observed',
                    output_ref=f"v269_no_admitted_action:{item.get('item_id')}",
                    payload={**package,'actionAdmission':admission,'taskAdmissionAllowed':False,
                        'holdReason':'NO_ACTION_ADMITTED','fallbackAllowed':False,
                        'legacyBusinessSemanticsUsed':False},
                )
                continue
            envelopes=[];partition_sources=[];input_refs=[]
            base_package=str(package.get('packageId') or item.get('item_id') or '')
            for partition in partitions:
                part_source={
                    'semanticContractVersion':VERSION,
                    'packageId':base_package+':'+partition['receiptHash'][-12:],
                    'productId':package['productId'],'storeId':package['storeId'],
                    'dataVersion':package.get('dataVersion') or data_version,
                    'knowledgeContext':knowledge,
                    'DecisionGraph':decision,'actionAdmission':admission,
                    'partition':partition,'factValues':fact_values,
                }
                envelope=migration.project_input(
                    'agent2',part_source,source_ref=source_ref,source_content_hash=source_hash
                )
                input_ref=_store_agent2_input(item,envelope,source_ref=source_ref,partition_hash=partition['receiptHash'])
                # Resolve persisted bytes, ensuring the exact-hash runtime sees exactly
                # what was registered rather than a mutable in-memory surrogate.
                from src.services.artifact_transport_service import resolve_artifact
                persisted=resolve_artifact(input_ref)
                envelopes.append(persisted)
                partition_sources.append(part_source)
                input_refs.append(input_ref)
            outputs,provider=run_agent2_draft_projected_inputs(
                envelopes,data_version=data_version,max_items_per_call=max(1,len(envelopes))
            )
            results=[];execution_hashes=[];errors=[]
            for partition,source,input_ref in zip(partitions,partition_sources,input_refs):
                output=outputs.get(source['packageId'])
                if not isinstance(output,dict):
                    errors.append(partition['receiptHash']+':NO_OUTPUT');continue
                if output.get('draftStatus')!='draft_ready' or not isinstance(output.get('PlanGraph'),dict):
                    errors.append(partition['receiptHash']+':'+str(output.get('outcomeChannel') or output.get('draftStatus') or 'NOT_READY'));continue
                if output.get('partitionHash')!=partition['receiptHash']:
                    errors.append(partition['receiptHash']+':PARTITION_HASH_MISMATCH');continue
                execution_hash=str(output.get('executionHash') or '')
                if not execution_hash:
                    errors.append(partition['receiptHash']+':EXECUTION_HASH_MISSING');continue
                execution_hashes.append(execution_hash)
                results.append({'partitionHash':partition['receiptHash'],'plan':migration.model_graph_body(output['PlanGraph'])})
            if errors:
                failed+=1
                _finish(
                    item,stage=AGENT1_COMPLETED_STAGE,status='retry',
                    output_ref=f"v269_agent2_partition_incomplete:{item.get('item_id')}",
                    payload={**package,'actionAdmission':admission,'partitionErrors':errors,
                        'provider':provider,'taskAdmissionAllowed':False,'fallbackAllowed':False,
                        'legacyBusinessSemanticsUsed':False},
                )
                details.append({'itemId':item.get('item_id'),'status':'partition_incomplete','errors':errors})
                continue
            plan=graphs.merge_plans(
                decision,admission,partitions,results,
                evidence_refs=package.get('evidenceRefs') or [],fact_values=fact_values,
            )
            graph_refs=deepcopy(package.get('graphExecutionRefs') or {})
            graphs.require(isinstance(graph_refs.get('agent1'),str) and graph_refs['agent1'],'agent1_execution_ref_missing')
            graph_refs['agent2']=execution_hashes
            merged_payload={
                'semanticContractVersion':VERSION,
                'packageId':base_package,'productId':package['productId'],'storeId':package['storeId'],
                'dataVersion':package.get('dataVersion') or data_version,
                'knowledgeContext':knowledge,'evidenceRefs':deepcopy(package.get('evidenceRefs') or []),
                'BusinessFacts':deepcopy(package.get('BusinessFacts') or {}),'factValues':fact_values,
                'DecisionGraph':decision,'actionAdmission':admission,'PlanGraph':plan,
                'graphExecutionRefs':graph_refs,
                'partitionReceipts':deepcopy(partitions),
                'partitionProviderSummary':provider,
                'taskAdmissionAllowed':False,
                'fallbackAllowed':False,'legacyBusinessSemanticsUsed':False,
            }
            _finish(
                item,stage=AGENT2_DRAFT_READY_STAGE,status='queued',
                output_ref=f"v269_plan_graph_ready:{item.get('item_id')}:{plan['graphHash']}",
                payload=merged_payload,
            )
            merged+=1
            details.append({'itemId':item.get('item_id'),'status':'plan_graph_ready',
                'partitionCount':len(partitions),'PlanGraphHash':plan['graphHash']})
        except Exception as exc:
            failed+=1
            try:
                package=dict(payload_from_row(item))
                _finish(
                    item,stage=AGENT1_COMPLETED_STAGE,status='retry',
                    output_ref=f"v269_agent2_orchestration_failed:{item.get('item_id')}",
                    payload={**package,'reason':str(exc)[:800],'taskAdmissionAllowed':False,
                        'fallbackAllowed':False,'legacyBusinessSemanticsUsed':False},
                )
            except Exception:
                pass
            details.append({'itemId':item.get('item_id'),'status':'failed','error':str(exc)[:500]})
    return {
        'version':VERSION,'dataVersion':data_version,'ran':bool(items),'selectedItemCount':len(items),
        'planGraphReadyCount':merged,'heldItemCount':held,'failedItemCount':failed,
        'details':details,'secondWorkerCreated':False,'partialTaskAdmission':False,
        'fallbackAllowed':False,
    }


__all__=[
    'VERSION','pending_graph_agent1_count','run_agent2_graph_partition_microbatch'
]
