"""V26.9 migration at existing input/identity seams; no parallel Agent runner."""
from copy import deepcopy
import math
from src.services import v269_semantic_graph_service as graphs

VERSION='26.9.0'
COMMON={'semanticContractVersion','packageId','productId','storeId','dataVersion',
        'knowledgeContext','inputContract','revisionScope','parentGraph'}
FIELDS={'agent1': COMMON | {'BusinessFacts','evidenceRefs','correlationId','signalId'},
        'agent2': COMMON | {'DecisionGraph','actionAdmission','partition','factValues'},
        'agent3': COMMON | {'PlanGraph'}}


def uses_graph_contract(source):
    return isinstance(source,dict) and (source.get('semanticContractVersion') is not None
        or 'DecisionGraph' in source or 'PlanGraph' in source)


def knowledge_identity(value):
    graphs.require(isinstance(value,dict) and set(value)=={'headHash','retrievalPolicyHash','records'},'knowledge_context_shape')
    graphs.require(isinstance(value['retrievalPolicyHash'],str) and value['retrievalPolicyHash'] and isinstance(value['records'],list),'knowledge_context_invalid')
    body={k:v for k,v in value.items() if k!='headHash'}
    graphs.require(value['headHash']==graphs.digest(body),'knowledge_head_mismatch')
    return value['headHash']


def validate_payload(agent,payload):
    graphs.require(agent in FIELDS and isinstance(payload,dict),'input_agent_invalid')
    graphs.require(set(payload)<=FIELDS[agent],'input_unknown_field')
    graphs.require(payload.get('semanticContractVersion')==VERSION,'input_contract_version')
    for key in ('packageId','productId','storeId'):
        graphs.require(isinstance(payload.get(key),str) and payload[key].strip(),'input_identity_missing')
    if agent=='agent1':
        graphs.require(isinstance(payload.get('BusinessFacts'),dict) and payload['BusinessFacts'],'facts_required')
        graphs.require(graphs.strings(payload.get('evidenceRefs')) and payload['evidenceRefs'],'evidence_required')
        graphs.finite(payload['BusinessFacts'])
    else:
        graphs.graph_input(agent,payload)
    knowledge_identity(payload.get('knowledgeContext'))
    input_contract=payload.get('inputContract')
    graphs.require(isinstance(input_contract,dict) and input_contract.get('semanticContractHash')==graphs.digest(graphs.contract()),'input_contract_hash')
    if 'revisionScope' in payload or 'parentGraph' in payload:
        parent=payload.get('parentGraph');graphs.index(parent)
        expected_kind={'agent1':'DecisionGraph','agent2':'PlanGraph','agent3':'OperationGraph'}[agent]
        graphs.require(parent.get('kind')==expected_kind,'revision_parent_kind')
        from src.services.v26_revision_acceptance_service import verify_revision_result
        verify_revision_result(parent,parent,payload.get('revisionScope',{}),graph_kind=expected_kind[:-5])
    if agent=='agent2':
        expected=graphs.partition_actions(payload['DecisionGraph'],payload.get('actionAdmission',{}))
        graphs.require(payload.get('partition') in expected,'input_partition_mismatch')
        facts=payload.get('factValues')
        graphs.require(isinstance(facts,dict),'input_facts_required')
        for key,value in facts.items():
            graphs.require(isinstance(key,str) and isinstance(value,dict) and set(value)=={'value','unit'},'input_fact_shape')
            graphs.require(type(value['value']) in (int,float) and isinstance(value['unit'],str) and value['unit'],'input_fact_type')
            graphs.finite(value)
    return payload


def project_input(agent,source,*,source_ref,source_content_hash):
    from src.services import agent_input_contract_v225_service as envelopes
    graphs.require(source.get('semanticContractVersion',VERSION)==VERSION,'input_contract_version')
    payload={key:deepcopy(source[key]) for key in FIELDS[agent] if key in source}
    payload['semanticContractVersion']=VERSION
    # Rebuild this system contract; do not copy policy hashes derived from old locks.
    payload['inputContract']={'semanticContractHash':graphs.digest(graphs.contract()),
        'fallbackAllowed':False,'policyContextHash':graphs.digest({
            'semanticContractHash':graphs.digest(graphs.contract()),
            'admissionHash':source.get('actionAdmission',{}).get('receiptHash') if agent=='agent2' else None})}
    validate_payload(agent,payload)
    if agent=='agent1':
        from src.services import agent_input_contract_v2258_service as envelopes
    schema=envelopes.AGENT1_INPUT_SCHEMA if agent=='agent1' else (envelopes.AGENT2_DRAFT_INPUT_SCHEMA if agent=='agent2' else envelopes.AGENT3_SOP_INPUT_SCHEMA)
    return envelopes.build_projection_envelope(schema=schema,payload=payload,
        source_artifact_refs=[source_ref],source_content_hash=source_content_hash)


def semantic_identity(agent,package,descriptor):
    # Ignore legacy top-level fields deliberately, while validating the selected input.
    selected={key:deepcopy(package[key]) for key in FIELDS[agent] if key in package}
    validate_payload(agent,selected)
    config={key:descriptor.get(key) for key in ('provider','model','generationParametersHash','promptVersion')}
    graphs.require(all(isinstance(config[key],str) and config[key] for key in ('provider','model','generationParametersHash','promptVersion')),'model_identity_missing')
    knowledge=selected['knowledgeContext']
    source=({'facts':selected['BusinessFacts'],'evidenceRefs':selected['evidenceRefs']} if agent=='agent1'
        else selected['DecisionGraph' if agent=='agent2' else 'PlanGraph'])
    base=graphs.semantic_identity(agent,source=source,rag_head=knowledge['headHash'],
        business_scope={key:selected[key] for key in ('productId','storeId')},
        model_config_hash=graphs.digest(config),retrieval_policy_hash=knowledge['retrievalPolicyHash'])
    # Agent2 cache scope also includes the system-admitted partition and frozen facts.
    # Otherwise separate calls over the same whole graph would alias each other.
    effective=graphs.digest({'graphSemanticHash':base,
        'partitionHash':selected['partition']['receiptHash'] if agent=='agent2' else None,
        'factValues':selected.get('factValues'), 'revisionScope':selected.get('revisionScope'),
        'parentGraphHash':selected.get('parentGraph',{}).get('graphHash')})
    return {'schema':'v269.'+agent+'.semantic_identity.v1','version':VERSION,
        'semanticHash':effective,'semanticInputHash':effective,'semanticContractHash':graphs.digest(graphs.contract()),
        'batchCompatibilityHash':graphs.digest({'model':config,'contract':graphs.digest(graphs.contract()),'knowledgeHead':knowledge['headHash']}),
        'cacheEligible':True,'cachedChannel':{'agent1':'DecisionGraph','agent2':'PlanGraph','agent3':'OperationGraph'}[agent],
        'cacheStatus':'CURRENT_GRAPH_REVALIDATION_REQUIRED','crossProductReuseAllowed':False}


def provider_messages(agent, data_version, packages):
    """Compile from graph inputs before any legacy prompt or RAG projection runs."""
    import json
    graphs.require(packages and all(uses_graph_contract(p) for p in packages),'mixed_provider_contracts')
    for package in packages:
        validate_payload(agent,package)
    field='PlanGraph' if agent=='agent2' else 'OperationGraph'
    kinds=['PlanActionNode'] if agent=='agent2' else ['OperationStage']
    payload={'dataVersion':data_version,'version':VERSION,'packages':deepcopy(packages),
        'nodeContract':{k:graphs.contract()['nodeFields'][k] for k in kinds},
        'outputContract':{'collection':'plans' if agent=='agent2' else 'sops',
            'requiredIdentity':['packageId','itemExecutionId','inputContentHash'],
            'exactlyOneBusinessChannel':[field,'missingData','conflictReasons','rejectedReason'],
            'graphFields':['nodes','edges'],'modelAuthoredHashesAllowed':False}}
    responsibility=(
        '把系统 partition.actionKeys 中的每个 DecisionAction 恰好方案化一次。不得新增、替换或遗漏动作；'
        'decisionActionRef 和 judgementRefs 必须引用原图。baseline 的数值和单位必须来自 factValues，'
        'expectedDelta = expectedValue - baseline.value。参数、预算、预期区间、复核窗口和验收标准由本层给出。'
        'guard 的每个值以及 riskBoundary 的每一项只能使用 {metric, comparator, value}，其中 comparator 仅允许 GTE 或 LTE，'
        'metric 必须属于 affectedMetrics，value 必须是有限数值；acceptanceCriteria 只能使用 {metric, constraint:"expectedRange"}。'
        '无法确定性表达的安全条件不得编造阈值，必须使用 missingData、conflictReasons 或 rejectedReason 明确返回缺口。'
        '拆分和合并由系统完成；禁止自行调用其他 Agent。'
        if agent=='agent2' else
        '仅把 PlanGraph 转为执行阶段，每个阶段绑定 planActionRefs，并完整覆盖方案动作。'
        '只能制定执行步骤、负责人、对象、顺序、回滚和验收动作。不得改写预算、目标、参数或扩大授权。'
        'stopConditionRefs 只能使用引用方案中已有的 nodeKey:guardKey。')
    prompt=('你是 Agent '+agent[-1]+'，遵守 V26.9 唯一业务语义合同。'+responsibility+
        '知识仅可来自本项 knowledgeContext.records。输出严格 JSON；逐字回传每项执行身份，禁止跨项复制。'
        '业务正文只能使用 outputContract 中一个通道。图只写 nodes、edges，节点严格遵守 nodeContract。'
        '不得生成 hash、运行状态、旧 primary 字段、executionLock 或 lockedActionFamily。')
    return [{'role':'system','content':prompt},{'role':'user','content':json.dumps(payload,ensure_ascii=False,sort_keys=True)}],payload


def normalize_output(agent, raw, package, proof=None):
    """Accept model-owned graph body; status and immutable bindings are system-owned."""
    validate_payload(agent,package)
    field='PlanGraph' if agent=='agent2' else 'OperationGraph'
    channels={field,'missingData','conflictReasons','rejectedReason'}
    graphs.require(isinstance(raw,dict) and set(raw)<=channels|{'packageId','itemExecutionId','inputContentHash'},'output_field_authority')
    graphs.require(raw.get('packageId')==package['packageId'],'output_package_mismatch')
    active=[key for key in channels if key in raw]
    graphs.require(len(active)==1,'output_channel_conflict')
    channel=active[0]
    result={key:deepcopy(package[key]) for key in ('packageId','productId','storeId')}
    result.update({k:raw[k] for k in ('itemExecutionId','inputContentHash') if k in raw})
    result.update(semanticContractVersion=VERSION,outcomeChannel=channel,fallbackAllowed=False,
        contractValidation={'passed':True,'missing':[]},semanticContractMissing=[])
    if channel!=field:
        value=raw[channel]
        graphs.require((isinstance(value,str) and value.strip()) if channel=='rejectedReason'
            else (graphs.strings(value) and value),'output_reason_required')
        result[channel]=deepcopy(value)
        suffix={'missingData':'missing_data','conflictReasons':'conflict','rejectedReason':'rejected'}[channel]
    else:
        upstream=package['DecisionGraph' if agent=='agent2' else 'PlanGraph']
        graph=graphs.compile_graph(field,raw[field],upstream=upstream,
            evidence_refs=package.get('factValues',{}),fact_values=package.get('factValues'))
        if agent=='agent2':
            graphs.require({n['decisionActionRef'] for n in graph['nodes']}==set(package['partition']['actionKeys']),'partition_action_coverage')
            result['partitionHash']=package['partition']['receiptHash']
        result[field]=graph
        if package.get('revisionScope'):
            from src.services.v26_revision_acceptance_service import freeze_revision_acceptance
            result['revisionAcceptanceEvidence']=freeze_revision_acceptance(package['parentGraph'],graph,
                package['revisionScope'],graph_kind=field[:-5])
        suffix='ready'
    if agent=='agent2':
        result.update(draftStatus='draft_'+suffix,systemComputedDraftStatus=True,agent2DraftExecutionProof=deepcopy(proof or {}))
    else:
        result.update(sopStatus='sop_'+suffix,agent3ExecutionProof=deepcopy(proof or {}))
    if agent=='agent3' and field in result:
        result['sopDecisionEvidence']=freeze_graph_evidence(package,result)
    return result


def model_graph_body(graph):
    graphs.index(graph)
    return {'nodes':[{k:deepcopy(v) for k,v in node.items()
        if k!='nodeHash' and not (graph['kind']=='PlanGraph' and k=='actionFamily')}
        for node in graph['nodes']], 'edges':deepcopy(graph['edges'])}


def accepted_graph_cache(agent, descriptor, artifact_type):
    """Use the existing accepted execution index; no second cache authority/store."""
    from src.repositories.sqlite_repository import connect, loads
    from src.services.hash_directed_artifact_runtime_v2259_service import ensure_hash_directed_runtime_tables, accepted_execution
    from src.services.artifact_transport_service import validate_artifact
    if descriptor.get('semanticCacheEligible') is not True:
        return None
    ensure_hash_directed_runtime_tables()
    with connect() as conn:
        rows=conn.execute("""SELECT * FROM artifact_execution_index_v2259
            WHERE stage=? AND status='accepted' AND accepted_output_ref IS NOT NULL
            AND metadata_json LIKE ? ORDER BY updated_at DESC LIMIT 64""",
            (descriptor['stage'],'%'+descriptor['semanticHash']+'%')).fetchall()
    field='PlanGraph' if agent=='agent2' else 'OperationGraph'
    for row in rows:
        record=dict(row)
        if record['execution_hash']==descriptor['executionHash'] or record.get('reusable')==0:
            continue
        metadata=loads(record.get('metadata_json') or '{}')
        if not isinstance(metadata,dict) or any(metadata.get(k)!=descriptor.get(k) for k in
            ('semanticHash','semanticContractHash','semanticIdentitySchema','storeId','productId')):
            continue
        if metadata.get('semanticCacheEligible') is not True:
            continue
        replay=accepted_execution(record['execution_hash'])
        if not replay:
            continue
        validation=validate_artifact(replay['outputArtifactRef'],expected_type=artifact_type)
        if validation.get('ok') is not True or validation.get('contentHash')!=replay.get('outputContentHash'):
            continue
        value=replay.get('output',{})
        output=value.get('output',{}) if isinstance(value,dict) else {}
        if output.get('semanticContractVersion')!=VERSION or output.get('outcomeChannel')!=field:
            continue
        if output.get('draftStatus' if agent=='agent2' else 'sopStatus')!=('draft_ready' if agent=='agent2' else 'sop_ready'):
            continue
        try:
            graphs.index(output.get(field))
        except ValueError:
            continue
        return {'execution':record,'outputArtifactRef':replay['outputArtifactRef'],
            'outputContentHash':replay['outputContentHash'],'graphOutput':output}
    return None


def rebind_graph_cache(agent, source, entry):
    field='PlanGraph' if agent=='agent2' else 'OperationGraph'
    output=source.get('graphOutput',{})
    graphs.require(output.get('semanticContractVersion')==VERSION,'cache_contract_mismatch')
    graph=output.get(field)
    raw={'packageId':entry['package']['packageId'],field:model_graph_body(graph),
        **{k:entry['descriptor'][k] for k in ('itemExecutionId','inputContentHash') if k in entry['descriptor']}}
    rebound=normalize_output(agent,raw,entry['package'])
    graphs.require(rebound[field]==graph,'cache_graph_revalidation_mismatch')
    rebound.update(semanticResultCacheHit=True,cachedOutputRebound=True,semanticReplayValidated=True,
        semanticHash=entry['descriptor']['semanticHash'],semanticCacheContractVersion=VERSION,
        semanticCacheSourceExecutionHash=source['execution']['execution_hash'],
        semanticCacheSourceOutputRef=source['outputArtifactRef'],semanticCachedChannel=field,
        providerCallExecutedForCurrentResult=False)
    rebound[agent+'ApiCallCount']=0
    if agent=='agent3':
        rebound['agent3SemanticCacheSeedEligible']=False
    return rebound


def normalize_decision(raw, product):
    selected={k:deepcopy(product[k]) for k in FIELDS['agent1'] if k in product}
    validate_payload('agent1',selected)
    graph=graphs.compile_graph('DecisionGraph',raw.get('DecisionGraph'),evidence_refs=selected['evidenceRefs'])
    result={**{k:deepcopy(selected[k]) for k in ('packageId','productId','storeId')},
        'semanticContractVersion':VERSION,'DecisionGraph':graph,
        'decisionType':'act' if any(n['kind']=='DecisionActionNode' for n in graph['nodes']) else 'observe',
        'fallbackAllowed':False}
    if selected.get('revisionScope'):
        from src.services.v26_revision_acceptance_service import freeze_revision_acceptance
        result['revisionAcceptanceEvidence']=freeze_revision_acceptance(selected['parentGraph'],graph,
            selected['revisionScope'],graph_kind='Decision')
    return result


def decision_messages(data_version, products):
    import json
    packages=[]
    for product in products:
        selected={k:deepcopy(product[k]) for k in FIELDS['agent1'] if k in product}
        validate_payload('agent1',selected)
        execution=product.get('_hashExecution',{})
        graphs.require(all(execution.get(k) for k in ('itemExecutionId','inputContentHash')),'execution_identity_missing')
        packages.append({**selected,**{k:execution[k] for k in ('itemExecutionId','inputContentHash')}})
    payload={'dataVersion':data_version,'version':VERSION,'products':packages,
        'nodeContract':{k:graphs.contract()['nodeFields'][k] for k in ('JudgementNode','DecisionActionNode')},
        'edgeTypes':graphs.contract()['decisionEdgeTypes']}
    prompt=('你是 Agent1，输出经营诊断、因果判断、候选动作与优先级。只依据每项 BusinessFacts、evidenceRefs 和 knowledgeContext。'
        '禁止写计划预算、目标值、执行参数、SOP 或旧 primary/lock 字段。关系只能来自 edgeTypes，'
        'depends_on 的 sourceRef 是前置节点，targetRef 是依赖它的节点。因果推断须说明证据与不确定性。'
        '输出严格 JSON 顶层 judgments 数组，每项只有 itemExecutionId、inputContentHash、DecisionGraph。'
        '逐字回传执行身份。DecisionGraph 只含 nodes 和 edges；节点严格遵守 nodeContract；不得生成 hash。'
        '没有足够证据支持动作时可以只返回判断节点。')
    return [{'role':'system','content':prompt},{'role':'user','content':json.dumps(payload,ensure_ascii=False,sort_keys=True)}],payload


def freeze_graph_evidence(package, output):
    """Freeze public decision records and recomputable PLAN deltas, never hidden reasoning."""
    cards=[]
    labels={'reasoning':'判断依据','evidenceRefs':'引用证据','judgementRefs':'判断引用',
        'priority':'动作优先级','confidence':'置信度','decisionActionRef':'候选动作引用',
        'parameters':'方案参数','baseline':'冻结基线','expectedOutcome':'预期结果',
        'reviewWindow':'观察窗口','guard':'保护条件','acceptanceCriteria':'验收标准',
        'planActionRefs':'方案引用','instruction':'执行步骤','owner':'负责人','rollback':'回滚动作'}
    for field in ('DecisionGraph','PlanGraph','OperationGraph'):
        graph=output.get(field) or package.get(field)
        if graph is None:continue
        graphs.index(graph)
        actor={'DecisionGraph':'Agent1','PlanGraph':'Agent2','OperationGraph':'Agent3'}[field]
        for node in graph['nodes']:
            for key,label in labels.items():
                if key not in node:continue
                cards.append({'label':label,'field':key,'value':deepcopy(node[key]),
                    'kind':'PLAN' if field=='PlanGraph' else 'DECISION','nodeKey':node['nodeKey'],
                    'nodeHash':node['nodeHash'],'sourceHash':graph['graphHash'],'actor':actor,
                    'status':'RECORDED','formula':None})
            if field=='PlanGraph':
                for metric,expectation in node['expectedOutcome'].items():
                    base=node['baseline'][metric]
                    delta=expectation['expectedValue']-base['value']
                    graphs.require(math.isclose(delta,expectation['expectedDelta'],rel_tol=1e-9,abs_tol=1e-9),'evidence_delta_mismatch')
                    cards.append({'label':metric+'预期变化量','field':'expectedDelta','kind':'DERIVED',
                        'value':delta,'unit':base['unit'],'status':'RECOMPUTED',
                        'formula':'expectedValue - baseline.value','formulaVersion':'expected_delta.v1',
                        'inputs':[{'field':'baseline.value','value':base['value'],'sourceRef':base['sourceRef']},
                            {'field':'expectedValue','value':expectation['expectedValue'],'sourceHash':graph['graphHash']}],
                        'nodeKey':node['nodeKey'],'nodeHash':node['nodeHash'],'sourceHash':graph['graphHash']})
    return graphs.seal({'schema':'v26.sop_evidence.v1','version':VERSION,'source':'structured_graph_output',
        'executionIdentity':{k:output[k] for k in ('itemExecutionId','inputContentHash','productId','storeId') if k in output},
        'cards':cards,'knowledge':{k:deepcopy(package.get('knowledgeContext',{}).get(k)) for k in ('headHash','retrievalPolicyHash')},
        'revision':deepcopy(output.get('revisionAcceptanceEvidence')),'knowledgeEffect':'NOT_EVALUATED',
        'feedbackStatus':'NOT_RECORDED','modelReasoning':'structured_decision_record_only','onClickProviderCall':False})


def verify_task_execution_chain(package):
    """Rebuild task lineage from accepted immutable artifacts, not model proof flags.

    This proves accepted execution provenance. It does not issue permissions or
    reserve resources; task admission still needs the system authority transaction.
    """
    from src.services.hash_directed_artifact_runtime_v2259_service import accepted_execution
    from src.services.artifact_transport_service import resolve_artifact, validate_artifact
    refs=package.get('graphExecutionRefs')
    graphs.require(isinstance(refs,dict) and set(refs)=={'agent1','agent2','agent3'},'task_execution_refs_required')
    graphs.require(isinstance(refs['agent2'],list) and 0<len(refs['agent2'])<=graphs.contract()['limits']['maxPartitions'],'task_partition_execution_refs')
    all_refs=[refs['agent1'],*refs['agent2'],refs['agent3']]
    graphs.require(all(isinstance(ref,str) and ref for ref in all_refs) and len(set(all_refs))==len(all_refs),'task_execution_refs_invalid')
    stages={'agent1':'product_judgment_agent','agent2':'action_plan_judgment_agent','agent3':'task_mapping_agent'}
    receipts=[]
    def read(agent,execution_hash):
        accepted=accepted_execution(execution_hash)
        graphs.require(isinstance(accepted,dict),'task_execution_not_accepted')
        record=accepted['execution'];wrapper=accepted['output']
        graphs.require(record['stage']==stages[agent],'task_execution_stage_mismatch')
        validation=validate_artifact(accepted['outputArtifactRef'])
        graphs.require(validation.get('ok') is True and validation.get('contentHash')==accepted['outputContentHash'],'task_output_artifact_hash')
        graphs.require(isinstance(wrapper,dict) and wrapper.get('executionHash')==execution_hash
            and wrapper.get('inputArtifactRef')==record['input_artifact_ref']
            and wrapper.get('inputContentHash')==record['input_content_hash']
            and wrapper.get('itemExecutionId')==record['item_execution_id'],'task_execution_binding_mismatch')
        validation=validate_artifact(record['input_artifact_ref'],expected_type=record['input_schema'])
        graphs.require(validation.get('ok') is True and validation.get('contentHash')==record['input_content_hash'],'task_input_artifact_hash')
        envelope=resolve_artifact(record['input_artifact_ref'])
        from src.services.agent_input_contract_v225_service import validate_agent_input_envelope
        graphs.require(validate_agent_input_envelope(envelope).get('ok') is True,'task_input_envelope_invalid')
        payload=envelope['payload'];validate_payload(agent,payload)
        graphs.require(all(payload.get(k)==package.get(k) and isinstance(package.get(k),str) and package[k]
            for k in ('productId','storeId')),'task_execution_business_scope')
        output=wrapper.get('output')
        graphs.require(isinstance(output,dict) and output.get('semanticContractVersion')==VERSION,'task_output_contract')
        receipts.append({'agent':agent,'executionHash':execution_hash,'inputArtifactRef':record['input_artifact_ref'],
            'inputContentHash':record['input_content_hash'],'outputArtifactRef':accepted['outputArtifactRef'],
            'outputContentHash':accepted['outputContentHash']})
        return payload,output
    source1,result1=read('agent1',refs['agent1'])
    decision=normalize_decision({'DecisionGraph':model_graph_body(result1.get('DecisionGraph'))},source1)['DecisionGraph']
    graphs.require(decision==package.get('DecisionGraph'),'task_decision_graph_mismatch')
    admission=package.get('actionAdmission')
    parts=graphs.partition_actions(decision,admission)
    results=[]
    for ref in refs['agent2']:
        source,output=read('agent2',ref)
        graphs.require(source['DecisionGraph']==decision and source['actionAdmission']==admission,'task_partition_parent_mismatch')
        graphs.require(source['factValues']==package.get('factValues'),'task_partition_facts_mismatch')
        graphs.require(output.get('draftStatus')=='draft_ready' and output.get('partitionHash')==source['partition']['receiptHash'],'task_partition_output_invalid')
        results.append({'partitionHash':source['partition']['receiptHash'],'plan':model_graph_body(output.get('PlanGraph'))})
    plan=graphs.merge_plans(decision,admission,parts,results,evidence_refs=source1['evidenceRefs'],fact_values=package.get('factValues'))
    graphs.require(plan==package.get('PlanGraph'),'task_plan_graph_mismatch')
    source3,result3=read('agent3',refs['agent3'])
    graphs.require(source3['PlanGraph']==plan and result3.get('sopStatus')=='sop_ready','task_operation_parent_mismatch')
    operation=graphs.compile_graph('OperationGraph',model_graph_body(result3.get('OperationGraph')),upstream=plan)
    graphs.require(operation==package.get('OperationGraph'),'task_operation_graph_mismatch')
    mapping=graphs.map_task(decision,admission,plan,operation)
    return graphs.seal({'schema':'v269.task_execution_chain.v1','productId':package['productId'],
        'storeId':package['storeId'],'mapping':mapping,'executions':sorted(receipts,key=lambda r:(r['agent'],r['executionHash'])),
        'provenanceVerified':True,'permissionGranted':False})
