"""Whole-plan permission evaluation under existing server-owned policy tables."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from src.services import v269_semantic_graph_service as g
from src.services import action_authority_v214_service as authority
from src.services import v269_input_migration_service as migration
from tests.test_v22_4_v269_semantic_graph import decision_raw, plan_node


def fixture():
    facts={'fact:1':{'value':0,'unit':'ratio'},'budget:a':{'value':100,'unit':'CNY'},'budget:b':{'value':100,'unit':'CNY'}}
    decision=g.compile_graph('DecisionGraph',decision_raw(),evidence_refs=list(facts))
    nodes=[plan_node('P1','DA1'),plan_node('P2','DA2')]
    for node,target in zip(nodes,['a','b']):
        node['parameters']={'operationPlan':{'operations':[{'operationType':'budget_update',
            'target':{'id':target,'type':'ad_plan'},'currentValue':{'budget':100},
            'targetValue':{'budget':160},'currentValueRef':'budget:'+target}]}}
    def compile(nodes):
        return g.compile_graph('PlanGraph',{'nodes':nodes},upstream=decision,evidence_refs=list(facts),fact_values=facts)
    policy={'source':'existing_operator_action_authority','enabled':True,'singleAdjustmentLimit':100,
        'dailyAdjustmentLimit':200,'rolling24hLimit':200,'ownerApprovalLimit':300,
        'roasChangeRateLimit':0.2,'minimumTargetRoas':1,'usedToday':0,'usedRolling24h':0}
    return compile(nodes),facts,policy,nodes,compile


def test_combined_limits_and_recomputable_receipt():
    plan,facts,policy,_,_=fixture()
    result=g.evaluate_plan_authority(plan,facts,policy)
    assert result['decision']=='manager_approval_required'
    assert result['reasons']==['COMBINED_SINGLE_LIMIT']
    assert result['resourceUsage']['totalAdjustmentAmount']==120
    assert result['resourceUsage']['formula']=='sum(abs(targetBudget - currentBudget))'
    assert not result['reservationCreated']
    for row in result['resourceUsage']['actions']:
        operation=row['operations'][0]
        assert operation['absoluteDelta']==abs(operation['targetValue']-facts[operation['sourceRef']]['value'])
    assert result['receiptHash']==g.digest({k:v for k,v in result.items() if k!='receiptHash'})
    policy.update(singleAdjustmentLimit=200,usedToday=90,usedRolling24h=90)
    assert g.evaluate_plan_authority(plan,facts,policy)['reasons']==['COMBINED_DAILY_LIMIT','COMBINED_ROLLING_LIMIT']
    policy['ownerApprovalLimit']=110
    assert g.evaluate_plan_authority(plan,facts,policy)['decision']=='owner_approval_required'


@pytest.mark.parametrize('mutation,error',[
    (lambda op:op.update(adjustmentAmount=1),'operation_amount_mismatch'),
    (lambda op:op.update(currentValue={'budget':1}),'operation_baseline_fact_mismatch'),
    (lambda op:op.update(currentValue=[]),'operation_value_invalid'),
    (lambda op:op.update(currentValueRef=[]),'operation_baseline_ref_required'),
    (lambda op:op.update(target={'id':'a','type':[]}),'operation_target_type_invalid'),
    (lambda op:op.update(operationType='uncompiled_operation'),'operation_type_not_compiled'),
])
def test_untrusted_operation_data_fails_closed(mutation,error):
    _,facts,_,nodes,compile=fixture()
    mutation(nodes[0]['parameters']['operationPlan']['operations'][0])
    with pytest.raises(ValueError,match=error):g.plan_resource_usage(compile(nodes),facts)


def test_duplicate_resources_and_unscoped_budget_rejected():
    _,facts,_,nodes,compile=fixture()
    nodes[1]['parameters']=deepcopy(nodes[0]['parameters'])
    with pytest.raises(ValueError,match='cross_action_resource_conflict'):g.plan_resource_usage(compile(nodes),facts)
    nodes[1]['parameters']={'budget':20}
    with pytest.raises(ValueError,match='unscoped_budget_not_authorized'):g.plan_resource_usage(compile(nodes),facts)


def test_existing_authority_is_source_and_legacy_fields_cannot_override():
    plan,facts,policy,_,_=fixture()
    source={'semanticContractVersion':'26.9.0','productId':'p','storeId':'s','PlanGraph':plan,'factValues':facts}
    operator={k:v for k,v in policy.items() if k not in {'source','usedToday','usedRolling24h'}}
    store={'enabled':True,'budgetLimitMultiplier':1,'roasChangeMultiplier':1,'ownerApprovalMultiplier':1}
    with patch.object(migration,'verify_task_execution_chain',return_value={'provenanceVerified':True,'permissionGranted':False}), \
         patch.object(authority,'_operator_for_store',return_value='server-operator') as assigned, \
         patch.object(authority,'get_operator_authority',return_value=operator), \
         patch.object(authority,'get_store_policy',return_value=store), \
         patch.object(authority.legacy,'_usage',return_value={'usedToday':0,'usedRolling24h':0}):
        result=authority.authorize_decision(source)
        poisoned={**source,**{k:'poison' for k in g.contract()['legacyReadForbidden']},
            'taskPlan':{'assignedOperatorId':'attacker','selectedActionFamily':'roas_scale'}}
        assert authority.authorize_decision(poisoned)==result
        assigned.assert_called_with('s',{})
        assert result['decision']=='manager_approval_required'
        assert result['evaluation']['resourceUsage']['totalAdjustmentAmount']==120
        applied=authority.apply_authorization_to_decision(source)
        assert applied['decision']=='graph_authorization_pending'
        assert 'taskPlan' not in applied
        bad={**source,'factValues':{}}
        assert authority.authorize_decision(bad)['decision']==authority.AUTHORIZATION_DATA_MISSING


def test_authority_rejects_unaccepted_graph_without_legacy_fallback():
    plan,facts,_,_,_=fixture()
    with patch.object(authority,'_operator_for_store',return_value='server-operator'):
        result=authority.authorize_decision({'semanticContractVersion':'26.9.0','productId':'p','storeId':'s',
            'PlanGraph':plan,'factValues':facts,'executionLock':{'lockedActionFamily':'roas_scale'}})
    assert result['decision']==authority.AUTHORIZATION_DATA_MISSING
    assert result['reason']=='v269_task_execution_refs_required'
