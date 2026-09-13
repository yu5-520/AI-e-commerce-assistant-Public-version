"""V26.9 canonical graph compiler. No provider calls, legacy reads or authority issuance.

Candidate contract implementation; activation requires consumer migration. Caller
supplies system-owned facts, permission scope and upstream sealed graphs.
"""
from copy import deepcopy
import json
import hashlib
import math
from pathlib import Path

def digest(value):
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def seal(value):
    return {**deepcopy(value), 'receiptHash':digest(value)}


def contract():
    path = Path(__file__).resolve().parents[2] / 'config/v26_field_authority_contract.json'
    return json.loads(path.read_text())['businessSemanticContract']


def require(condition, reason):
    if not condition:
        raise ValueError('v269_' + reason)


def strings(value):
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value) and len(value) == len(set(value))


def finite(value):
    if isinstance(value, float):
        require(math.isfinite(value), 'nonfinite_value')
    elif isinstance(value, dict):
        for child in value.values(): finite(child)
    elif isinstance(value, list):
        for child in value: finite(child)


def _safety_rule(rule, affected_metrics):
    """Validate the one deterministic safety-rule language shared with Java Review.

    Agent2 may author only explicit metric thresholds. Prose or richer objects are not
    silently interpreted here or later by Java; callers must use a non-ready output
    channel when a business constraint cannot be represented deterministically.
    """
    require(isinstance(rule,dict) and set(rule)=={'metric','comparator','value'}, 'plan_safety_rule_shape')
    require(isinstance(rule['metric'],str) and rule['metric'] in affected_metrics, 'plan_safety_metric')
    require(rule['comparator'] in {'GTE','LTE'}, 'plan_safety_comparator')
    require(type(rule['value']) in (int,float) and math.isfinite(rule['value']), 'plan_safety_value')


def _validate_plan_safety(node):
    affected=set(node['affectedMetrics'])
    guard=node['guard']
    require(isinstance(guard,dict), 'plan_guard_shape')
    for rule_id,rule in guard.items():
        require(isinstance(rule_id,str) and rule_id.strip(), 'plan_guard_id')
        _safety_rule(rule,affected)
    risk=node['riskBoundary']
    require(isinstance(risk,list), 'plan_risk_boundary_shape')
    for rule in risk:
        _safety_rule(rule,affected)
    criteria=node['acceptanceCriteria']
    require(isinstance(criteria,list) and criteria, 'review_criteria_required')
    for rule in criteria:
        require(isinstance(rule,dict) and set(rule)=={'metric','constraint'}, 'acceptance_criterion_shape')
        require(rule['metric'] in affected and rule['constraint']=='expectedRange', 'acceptance_criterion_not_compiled')


def index(graph):
    require(isinstance(graph, dict), 'graph_required')
    c=contract()
    require(graph.get('contractVersion')==c['version'] and graph.get('contractHash')==digest(c), 'graph_contract_mismatch')
    require(graph.get('graphHash') == digest({k:v for k,v in graph.items() if k != 'graphHash'}), 'graph_hash_mismatch')
    require(set(graph)=={'kind','contractVersion','contractHash','upstreamGraphHash','nodes','edges','graphHash'}, 'graph_field_authority')
    require(graph['kind'] in {'DecisionGraph','PlanGraph','OperationGraph'}, 'graph_kind_invalid')
    nodes = graph.get('nodes')
    require(isinstance(nodes, list) and 0<len(nodes)<=c['limits']['maxNodes'], 'nodes_required')
    require(isinstance(graph.get('edges'),list) and len(graph['edges'])<=c['limits']['maxEdges'],'graph_budget')
    require(len(json.dumps(graph,ensure_ascii=False).encode())<=c['limits']['maxGraphBytes']+20000,'graph_budget')
    result = {}
    for node in nodes:
        require(isinstance(node, dict) and isinstance(node.get('nodeKey'), str), 'node_invalid')
        require(node['nodeKey'] not in result, 'duplicate_node')
        require(node.get('nodeHash') == digest({k:v for k,v in node.items() if k != 'nodeHash'}), 'node_hash_mismatch')
        result[node['nodeKey']] = node
    for edge in graph['edges']:
        require(isinstance(edge,dict) and set(edge)=={'sourceRef','targetRef','relation'},'edge_shape')
        require(edge['sourceRef'] in result and edge['targetRef'] in result,'edge_ref_invalid')
    acyclic(result,graph['edges'])
    if graph['kind']=='DecisionGraph':
        raw={'nodes':[{k:v for k,v in n.items() if k!='nodeHash'} for n in nodes],'edges':graph['edges']}
        rebuilt=compile_graph('DecisionGraph',raw,evidence_refs={ref for n in nodes for ref in n.get('evidenceRefs',[])})
        require(rebuilt==graph,'graph_semantic_mismatch')
    return result


def acyclic(keys, edges):
    parents = {key: [] for key in keys}
    for edge in edges:
        if edge['relation'] in {'depends_on', 'enables', 'causes', 'sequence'}:
            parents[edge['targetRef']].append(edge['sourceRef'])
    active, done = set(), set()
    def visit(key):
        require(key not in active, 'dependency_cycle')
        if key in done: return
        active.add(key)
        for parent in parents[key]: visit(parent)
        active.remove(key); done.add(key)
    for key in sorted(keys): visit(key)


def compile_graph(kind, raw, *, upstream=None, evidence_refs=(), fact_values=None):
    c = contract()
    allowed = {'DecisionGraph': {'JudgementNode', 'DecisionActionNode'},
               'PlanGraph': {'PlanActionNode'}, 'OperationGraph': {'OperationStage'}}
    require(kind in allowed and isinstance(raw, dict), 'graph_kind_invalid')
    require(set(raw) <= {'nodes', 'edges'}, 'unknown_graph_field')
    nodes, edges = raw.get('nodes'), raw.get('edges', [])
    require(isinstance(nodes, list) and nodes and isinstance(edges, list), 'graph_collections_required')
    require(len(nodes) <= c['limits']['maxNodes'] and len(edges) <= c['limits']['maxEdges'], 'graph_budget')
    finite(raw)
    require(len(json.dumps(raw, ensure_ascii=False).encode()) <= c['limits']['maxGraphBytes'], 'graph_budget')
    parent = index(upstream) if upstream is not None else {}
    expected_parent = {'PlanGraph': 'DecisionGraph', 'OperationGraph': 'PlanGraph'}
    if kind in expected_parent:
        require(upstream is not None and upstream.get('kind') == expected_parent[kind], 'upstream_kind_mismatch')
    built = {}
    for raw_node in nodes:
        require(isinstance(raw_node, dict) and raw_node.get('kind') in allowed[kind], 'node_kind_invalid')
        spec = c['nodeFields'][raw_node['kind']]
        require(set(spec['required']) <= set(raw_node) <= set(spec['required'] + spec['optional']), 'node_field_authority')
        key = raw_node['nodeKey']
        require(isinstance(key, str) and key.strip() and key not in built, 'duplicate_or_invalid_key')
        node = deepcopy(raw_node)
        if kind == 'DecisionGraph':
            require(strings(node['evidenceRefs']) and set(node['evidenceRefs']) <= set(evidence_refs), 'evidence_ref_invalid')
            require(node['evidenceRefs'], 'evidence_required')
            for field in ('confidence', 'priority'):
                if field in node:
                    require(type(node[field]) in (int, float) and 0 <= node[field] <= 1, 'weight_invalid')
            if node['kind'] == 'JudgementNode':
                require(isinstance(node['reasoning'], str) and node['reasoning'].strip(), 'reasoning_required')
            else:
                require(node['actionFamily'] in c['actionFamilyDomains'], 'action_family_unregistered')
                require(isinstance(node['actionType'], str) and node['actionType'].strip(), 'action_type_required')
                require(strings(node['judgementRefs']) and node['judgementRefs'], 'judgement_refs_required')
        elif kind == 'PlanGraph':
            decision = parent.get(node['decisionActionRef'], {})
            require(decision.get('kind') == 'DecisionActionNode', 'decision_action_ref_invalid')
            require(strings(node['judgementRefs']) and set(node['judgementRefs']) == set(decision['judgementRefs']), 'judgement_reselection')
            require(strings(node['affectedMetrics']) and node['affectedMetrics'], 'affected_metrics_required')
            require(isinstance(node['parameters'], dict) and isinstance(node['guard'], dict), 'plan_parameters_invalid')
            require(isinstance(node['baseline'], dict) and set(node['affectedMetrics']) == set(node['baseline']), 'baseline_missing')
            expected = node['expectedOutcome']
            require(isinstance(expected, dict) and set(expected) == set(node['affectedMetrics']), 'expected_metrics_mismatch')
            for metric, value in expected.items():
                require(isinstance(value, dict) and set(value) == {'expectedValue','expectedRange','expectedDelta'}, 'expected_shape')
                base = node['baseline'][metric]
                require(isinstance(base, dict) and set(base) == {'value','unit','sourceRef'}, 'baseline_shape')
                require(isinstance(base['unit'], str) and base['unit'] and base['sourceRef'] in evidence_refs, 'baseline_provenance')
                require((fact_values or {}).get(base['sourceRef']) == {'value':base['value'],'unit':base['unit']}, 'baseline_fact_mismatch')
                require(all(type(x) in (int,float) for x in (base['value'],value['expectedValue'],value['expectedDelta'])), 'expected_numeric')
                bounds = value['expectedRange']
                require(isinstance(bounds,list) and len(bounds)==2 and all(type(x) in (int,float) for x in bounds), 'expected_range')
                require(bounds[0] <= value['expectedValue'] <= bounds[1], 'expected_range')
                require(math.isclose(value['expectedValue']-base['value'], value['expectedDelta'], rel_tol=1e-9, abs_tol=1e-9), 'expected_delta_mismatch')
            window=node['reviewWindow']
            require(isinstance(window,dict) and set(window)=={'durationSeconds'} and type(window['durationSeconds']) is int and window['durationSeconds']>0, 'review_window_invalid')
            _validate_plan_safety(node)
            node['actionFamily'] = decision['actionFamily']
        else:
            require(strings(node['planActionRefs']) and node['planActionRefs'] and set(node['planActionRefs']) <= set(parent), 'plan_refs_invalid')
            require(all(isinstance(node[x],str) and node[x].strip() for x in ('instruction','owner','executionObject','rollback')), 'operation_text_required')
            require(type(node['sequence']) is int and node['sequence'] >= 0, 'sequence_invalid')
            require(strings(node['stopConditionRefs']), 'stop_refs_invalid')
            guards = {ref + ':' + guard for ref in node['planActionRefs'] for guard in parent[ref]['guard']}
            require(set(node['stopConditionRefs']) <= guards, 'stop_guard_not_authorized')
            require(isinstance(node['acceptanceActions'],list) and node['acceptanceActions'], 'acceptance_actions_required')
        built[key] = node
    if kind == 'DecisionGraph':
        for node in built.values():
            if node['kind'] == 'DecisionActionNode':
                require(all(built.get(ref,{}).get('kind') == 'JudgementNode' for ref in node['judgementRefs']), 'judgement_ref_invalid')
    elif kind == 'PlanGraph':
        refs=[n['decisionActionRef'] for n in built.values()]
        require(len(refs)==len(set(refs)), 'duplicate_plan_action')
    else:
        require(set(parent)=={ref for n in built.values() for ref in n['planActionRefs']}, 'operation_coverage')
    sealed_edges=[]
    for edge in edges:
        require(isinstance(edge,dict) and set(edge)=={'sourceRef','targetRef','relation'}, 'edge_shape')
        require(edge['sourceRef'] in built and edge['targetRef'] in built and edge['sourceRef']!=edge['targetRef'], 'edge_ref_invalid')
        require(edge['relation'] in (c['decisionRelations'] if kind=='DecisionGraph' else ['depends_on','conflicts_with','sequence']), 'edge_relation_invalid')
        if kind=='DecisionGraph':
            require([built[edge['sourceRef']]['kind'],built[edge['targetRef']]['kind']] in c['decisionEdgeTypes'][edge['relation']], 'edge_type_invalid')
        require(edge not in sealed_edges, 'duplicate_edge')
        sealed_edges.append(deepcopy(edge))
    acyclic(built, sealed_edges)
    result={'kind':kind, 'contractVersion':c['version'], 'contractHash':digest(c),
            'upstreamGraphHash':upstream['graphHash'] if upstream else None,
            'nodes':[{**n,'nodeHash':digest(n)} for _,n in sorted(built.items())],
            'edges':sorted(sealed_edges,key=lambda e:(e['sourceRef'],e['relation'],e['targetRef']))}
    result['graphHash']=digest(result)
    return result


def admit_actions(decision, allowed_action_keys):
    nodes=index(decision)
    require(decision.get('kind')=='DecisionGraph', 'decision_graph_required')
    actions={k:n for k,n in nodes.items() if n['kind']=='DecisionActionNode'}
    require(set(allowed_action_keys) <= set(actions), 'permission_unknown_action')
    require(isinstance(allowed_action_keys,(list,tuple,set)) and all(isinstance(k,str) for k in allowed_action_keys),'permission_scope_invalid')
    admitted=set(allowed_action_keys)
    reasons={k:'PERMISSION_NOT_GRANTED' for k in actions if k not in admitted}
    for edge in decision['edges']:
        if edge['relation']=='conflicts_with' and edge['sourceRef'] in admitted and edge['targetRef'] in admitted:
            reasons[edge['sourceRef']]=reasons[edge['targetRef']]='CONFLICT_REQUIRES_RESOLUTION'
    admitted-=set(reasons)
    changed=True
    while changed:
        changed=False
        for edge in decision['edges']:
            if edge['relation'] in ('depends_on','enables') and edge['targetRef'] in admitted and edge['sourceRef'] in actions and edge['sourceRef'] not in admitted:
                admitted.remove(edge['targetRef']);reasons[edge['targetRef']]='DEPENDENCY_NOT_ADMITTED';changed=True
    return seal({'schema':'v269.action_admission.v1','decisionGraphHash':decision['graphHash'],
                 'admitted':sorted(admitted,key=lambda k:(-actions[k]['priority'],k)), 'deferred':reasons,
                 'allowedActionKeys':sorted(set(allowed_action_keys)), 'policyHash':digest(contract()['admission'])})


def partition_actions(decision, admission):
    require(admission.get('receiptHash')==digest({k:v for k,v in admission.items() if k!='receiptHash'}), 'admission_hash')
    require(admission['decisionGraphHash']==decision['graphHash'], 'admission_parent')
    require(admission==admit_actions(decision,admission.get('allowedActionKeys',[])), 'admission_semantics_mismatch')
    nodes=index(decision); groups={}
    for key in admission['admitted']:
        require(nodes.get(key,{}).get('kind')=='DecisionActionNode','admission_node')
        domain=contract()['actionFamilyDomains'][nodes[key]['actionFamily']]
        groups.setdefault(domain,[]).append(key)
    require(len(groups)<=contract()['limits']['maxPartitions'], 'partition_budget')
    return [seal({'domain':domain,'decisionGraphHash':decision['graphHash'],
                  'admissionHash':admission['receiptHash'],'actionKeys':sorted(keys)}) for domain,keys in sorted(groups.items())]


def semantic_identity(agent, *, source, rag_head, business_scope, model_config_hash, retrieval_policy_hash):
    require(agent in ('agent1','agent2','agent3'), 'agent_invalid')
    require(all(isinstance(x,str) and x for x in (rag_head,model_config_hash,retrieval_policy_hash)), 'identity_context_missing')
    require(isinstance(business_scope,dict) and business_scope, 'business_scope_missing')
    if agent!='agent1':
        index(source)
        require(source['kind']==('DecisionGraph' if agent=='agent2' else 'PlanGraph'), 'identity_graph_kind')
        material=source['graphHash']
    else:
        require(isinstance(source,dict) and set(source)<= {'facts','evidenceRefs'}, 'facts_surface_invalid')
        require('facts' in source, 'facts_required')
        material=source
    return digest({'agent':agent,'source':material,'ragHead':rag_head,'businessScope':business_scope,
                   'modelConfigHash':model_config_hash,'retrievalPolicyHash':retrieval_policy_hash,
                   'contractHash':digest(contract())})


def merge_plans(decision, admission, partitions, results, *, evidence_refs=(), fact_values=None):
    expected=partition_actions(decision,admission)
    require(partitions==expected, 'partition_identity_mismatch')
    require(isinstance(results,list) and len(results)==len(partitions), 'partition_result_missing')
    by_hash={p['receiptHash']:p for p in partitions}; seen=set(); nodes=[]; edges=[]
    for result in results:
        require(isinstance(result,dict) and set(result)=={'partitionHash','plan'}, 'partition_result_shape')
        key=result['partitionHash']
        require(key in by_hash and key not in seen, 'duplicate_or_unknown_partition')
        seen.add(key)
        plan=compile_graph('PlanGraph',result['plan'],upstream=decision,evidence_refs=evidence_refs,fact_values=fact_values)
        require({n['decisionActionRef'] for n in plan['nodes']}==set(by_hash[key]['actionKeys']), 'partition_action_coverage')
        nodes.extend({k:v for k,v in n.items() if k not in {'nodeHash','actionFamily'}} for n in plan['nodes'])
        edges.extend(plan['edges'])
    require(seen==set(by_hash), 'partition_result_missing')
    # Cross-partition dependency edges are generated by the system from DecisionGraph.
    action_to_plan={n['decisionActionRef']:n['nodeKey'] for n in nodes}
    require(set(action_to_plan)==set(admission['admitted']), 'merged_action_coverage')
    for edge in decision['edges']:
        if edge['relation'] in ('depends_on','enables') and edge['sourceRef'] in action_to_plan and edge['targetRef'] in action_to_plan:
            projected={'sourceRef':action_to_plan[edge['sourceRef']], 'targetRef':action_to_plan[edge['targetRef']], 'relation':'depends_on'}
            if projected not in edges:edges.append(projected)
    return compile_graph('PlanGraph',{'nodes':nodes,'edges':edges},upstream=decision,evidence_refs=evidence_refs,fact_values=fact_values)


def graph_input(agent, source):
    """Allowlist projection: old fields cannot become a fallback or prompt input."""
    field={'agent2':'DecisionGraph','agent3':'PlanGraph'}.get(agent)
    require(field is not None, 'projection_agent_invalid')
    graph=source.get(field)
    index(graph)
    require(graph.get('kind')==field, 'projection_graph_kind')
    return {field:deepcopy(graph)}


def map_task(decision, admission, plan, operation):
    d,p,o=index(decision),index(plan),index(operation)
    require(decision['kind']=='DecisionGraph' and plan['kind']=='PlanGraph' and operation['kind']=='OperationGraph', 'task_graph_kind')
    partition_actions(decision,admission)
    require(plan['upstreamGraphHash']==decision['graphHash'] and operation['upstreamGraphHash']==plan['graphHash'], 'task_graph_lineage')
    require({n['decisionActionRef'] for n in p.values()}==set(admission['admitted']), 'task_action_coverage')
    require({ref for n in o.values() for ref in n['planActionRefs']}==set(p), 'task_operation_coverage')
    return seal({'schema':'v269.task_graph_mapping.v1','DecisionGraphHash':decision['graphHash'],
        'PlanGraphHash':plan['graphHash'],'OperationGraphHash':operation['graphHash'],
        'admissionHash':admission['receiptHash'],'planActionKeys':sorted(p),'operationStageKeys':sorted(o)})


def plan_resource_usage(plan, fact_values):
    """Compute execution deltas from explicit operations; model totals grant nothing."""
    nodes=index(plan)
    require(plan['kind']=='PlanGraph','plan_graph_required')
    require(isinstance(fact_values,dict),'operation_facts_required')
    rows=[];resources=set()
    kinds={'budget_update':('budget','CNY'),'bid_update':('bid','CNY'),'target_roas_update':('roas','ratio')}
    for key,node in nodes.items():
        require(node.get('kind')=='PlanActionNode','plan_node_required')
        parameters=node.get('parameters')
        require(isinstance(parameters,dict),'plan_parameters_required')
        operation_plan=parameters.get('operationPlan',{})
        require(isinstance(operation_plan,dict),'operation_plan_invalid')
        operations=operation_plan.get('operations',[])
        require(isinstance(operations,list) and len(operations)<=64,'operations_required')
        if node['actionFamily'] in {'roas_scale','roas_guard'}:
            require(operations,'financial_operations_required')
        # An unscoped budget cannot be treated as an authorized monetary operation.
        require(parameters.get('budget',0)==0,'unscoped_budget_not_authorized')
        amount=0;rate=0;target_roas=None;operation_rows=[]
        for operation in operations:
            require(isinstance(operation,dict) and operation.get('operationType') in kinds,'operation_type_not_compiled')
            kind=operation['operationType'];field,unit=kinds[kind]
            target=operation.get('target',{})
            require(isinstance(target,dict) and isinstance(target.get('id'),str) and target['id'],'operation_target_required')
            require(isinstance(target.get('type','ad_plan'),str) and target.get('type','ad_plan').strip(),'operation_target_type_invalid')
            resource=(target.get('type','ad_plan'),target['id'],kind)
            require(resource not in resources,'cross_action_resource_conflict');resources.add(resource)
            require(isinstance(operation.get('currentValue'),dict) and isinstance(operation.get('targetValue'),dict),'operation_value_invalid')
            current=operation['currentValue'].get(field)
            desired=operation['targetValue'].get(field)
            require(all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in (current,desired)),'operation_value_invalid')
            ref=operation.get('currentValueRef')
            require(isinstance(ref,str) and bool(ref),'operation_baseline_ref_required')
            require(fact_values.get(ref)=={'value':current,'unit':unit},'operation_baseline_fact_mismatch')
            delta=abs(desired-current)
            require(math.isfinite(delta),'operation_delta_overflow')
            if 'adjustmentAmount' in operation:
                require(type(operation['adjustmentAmount']) in (int,float) and math.isclose(operation['adjustmentAmount'],delta,rel_tol=1e-9,abs_tol=1e-9),'operation_amount_mismatch')
            if kind=='budget_update':amount+=delta
            else:
                require(current>0 or desired==0,'operation_rate_baseline_zero')
                rate=max(rate,delta/current if current else 0)
            if kind=='target_roas_update':target_roas=desired if target_roas is None else min(target_roas,desired)
            operation_rows.append({'operationType':kind,'target':deepcopy(target),'sourceRef':ref,
                'unit':unit,'currentValue':current,'targetValue':desired,'absoluteDelta':delta})
        rows.append({'planActionRef':key,'actionFamily':node['actionFamily'],'adjustmentAmount':amount,
            'maxControlChangeRate':rate,'minimumTargetRoas':target_roas,'operations':operation_rows})
    total=sum(r['adjustmentAmount'] for r in rows)
    require(math.isfinite(total),'operation_total_overflow')
    return seal({'schema':'v269.plan_resource_usage.v1','planGraphHash':plan['graphHash'],
        'factValuesHash':digest(fact_values),'actions':rows,'currency':'CNY',
        'totalAdjustmentAmount':total,
        'formula':'sum(abs(targetBudget - currentBudget))'})


def evaluate_plan_authority(plan, fact_values, policy):
    """Pure evaluation under a server-supplied policy snapshot, never a permission issuer."""
    usage=plan_resource_usage(plan,fact_values)
    require(isinstance(policy,dict) and policy.get('source')=='existing_operator_action_authority','permission_policy_source')
    required=('singleAdjustmentLimit','dailyAdjustmentLimit','rolling24hLimit','ownerApprovalLimit',
        'roasChangeRateLimit','minimumTargetRoas','usedToday','usedRolling24h')
    require(all(type(policy.get(k)) in (int,float) and math.isfinite(policy[k]) and policy[k]>=0 for k in required),'permission_limit_invalid')
    require(isinstance(policy.get('enabled'),bool),'permission_enabled_required')
    total=usage['totalAdjustmentAmount'];reasons=[];owner=[]
    if not policy['enabled']:reasons.append('AUTHORITY_DISABLED')
    if total>policy['singleAdjustmentLimit']:reasons.append('COMBINED_SINGLE_LIMIT')
    if total+policy['usedToday']>policy['dailyAdjustmentLimit']:reasons.append('COMBINED_DAILY_LIMIT')
    if total+policy['usedRolling24h']>policy['rolling24hLimit']:reasons.append('COMBINED_ROLLING_LIMIT')
    if policy['ownerApprovalLimit'] and total>policy['ownerApprovalLimit']:owner.append('COMBINED_OWNER_LIMIT')
    for row in usage['actions']:
        if row['maxControlChangeRate']>policy['roasChangeRateLimit']:reasons.append(row['planActionRef']+':CONTROL_RATE_LIMIT')
        if row['minimumTargetRoas'] is not None and row['minimumTargetRoas']<policy['minimumTargetRoas']:owner.append(row['planActionRef']+':ROAS_SAFETY_FLOOR')
    decision='owner_approval_required' if owner else ('manager_approval_required' if reasons else 'auto_execute')
    return seal({'schema':'v269.plan_authority_evaluation.v1','planGraphHash':plan['graphHash'],
        'policyHash':digest(policy),'policy':deepcopy(policy),'resourceUsage':usage,
        'decision':decision,'approvalRequired':decision!='auto_execute','reasons':owner+reasons,
        'authorityOrigin':'SERVER_POLICY_SNAPSHOT','reservationCreated':False})
