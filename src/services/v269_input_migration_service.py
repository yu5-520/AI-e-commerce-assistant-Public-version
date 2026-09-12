"""V26.9 migration at existing input/identity seams; no parallel Agent runner."""
from copy import deepcopy
from src.services import v269_semantic_graph_service as graphs

VERSION='26.9.0'
COMMON={'semanticContractVersion','packageId','productId','storeId','dataVersion',
        'knowledgeContext','inputContract'}
FIELDS={'agent2': COMMON | {'DecisionGraph','actionAdmission','partition','factValues'},
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
    graphs.graph_input(agent,payload)
    knowledge_identity(payload.get('knowledgeContext'))
    input_contract=payload.get('inputContract')
    graphs.require(isinstance(input_contract,dict) and input_contract.get('semanticContractHash')==graphs.digest(graphs.contract()),'input_contract_hash')
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
    schema=envelopes.AGENT2_DRAFT_INPUT_SCHEMA if agent=='agent2' else envelopes.AGENT3_SOP_INPUT_SCHEMA
    return envelopes.build_projection_envelope(schema=schema,payload=payload,
        source_artifact_refs=[source_ref],source_content_hash=source_content_hash)


def semantic_identity(agent,package,descriptor):
    # Ignore legacy top-level fields deliberately, while validating the selected input.
    selected={key:deepcopy(package[key]) for key in FIELDS[agent] if key in package}
    validate_payload(agent,selected)
    config={key:descriptor.get(key) for key in ('provider','model','generationParametersHash','promptVersion')}
    graphs.require(all(isinstance(config[key],str) and config[key] for key in ('provider','model','generationParametersHash','promptVersion')),'model_identity_missing')
    knowledge=selected['knowledgeContext'];source=selected['DecisionGraph' if agent=='agent2' else 'PlanGraph']
    base=graphs.semantic_identity(agent,source=source,rag_head=knowledge['headHash'],
        business_scope={key:selected[key] for key in ('productId','storeId')},
        model_config_hash=graphs.digest(config),retrieval_policy_hash=knowledge['retrievalPolicyHash'])
    # Agent2 cache scope also includes the system-admitted partition and frozen facts.
    # Otherwise separate calls over the same whole graph would alias each other.
    effective=graphs.digest({'graphSemanticHash':base,
        'partitionHash':selected['partition']['receiptHash'] if agent=='agent2' else None,
        'factValues':selected.get('factValues')})
    return {'schema':'v269.'+agent+'.semantic_identity.v1','version':VERSION,
        'semanticHash':effective,'semanticInputHash':effective,'semanticContractHash':graphs.digest(graphs.contract()),
        'batchCompatibilityHash':graphs.digest({'model':config,'contract':graphs.digest(graphs.contract()),'knowledgeHead':knowledge['headHash']}),
        'cacheEligible':False,'cachedChannel':'PlanGraph' if agent=='agent2' else 'OperationGraph',
        'cacheStatus':'AWAITING_GRAPH_OUTPUT_CACHE_MIGRATION','crossProductReuseAllowed':False}


def reject_unmigrated_provider(package):
    if uses_graph_contract(package):
        raise ValueError('v269_provider_cutover_not_ready')
