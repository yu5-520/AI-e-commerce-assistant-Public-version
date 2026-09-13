import unittest
from copy import deepcopy
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('v269_graph', Path(__file__).resolve().parents[1] / 'src/services/v269_semantic_graph_service.py')
g=importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def decision_raw():
    return {'nodes':[
        {'kind':'JudgementNode','nodeKey':'J1','reasoning':'CPC上涨','evidenceRefs':['fact:1'],'confidence':0.9},
        {'kind':'DecisionActionNode','nodeKey':'DA1','judgementRefs':['J1'],'actionType':'reduce_inefficient_spend','actionFamily':'roas_guard','priority':0.86,'confidence':0.8,'evidenceRefs':['fact:1']},
        {'kind':'DecisionActionNode','nodeKey':'DA2','judgementRefs':['J1'],'actionType':'test_creative','actionFamily':'title_image_test','priority':0.72,'confidence':0.8,'evidenceRefs':['fact:1']}],
        'edges':[{'sourceRef':'J1','targetRef':'DA1','relation':'supports'},{'sourceRef':'DA1','targetRef':'DA2','relation':'enables'}]}


def plan_node(key, ref):
    return {'kind':'PlanActionNode','nodeKey':key,'decisionActionRef':ref,'judgementRefs':['J1'],
        'parameters':{'budget':0},'baseline':{'roas':{'value':0,'unit':'ratio','sourceRef':'fact:1'}},
        'expectedOutcome':{'roas':{'expectedValue':2,'expectedRange':[1,3],'expectedDelta':2}},
        'reviewWindow':{'durationSeconds':86400},'affectedMetrics':['roas'],
        'guard':{'stop':{'metric':'roas','comparator':'GTE','value':0}},
        'riskBoundary':[],'acceptanceCriteria':[{'metric':'roas','constraint':'expectedRange'}]}


class SemanticGraphTests(unittest.TestCase):
    def setUp(self):
        self.d=g.compile_graph('DecisionGraph',decision_raw(),evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        self.a=g.admit_actions(self.d,['DA1','DA2'])

    def test_owner_cannot_write_plan_numbers(self):
        for field in ['budget','parameters','lockedActionFamily']:
            raw=decision_raw();raw['nodes'][1][field]=100
            with self.assertRaisesRegex(ValueError,'node_field_authority'):
                g.compile_graph('DecisionGraph',raw,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})

    def test_evidence_weights_and_cycles(self):
        raw=decision_raw();raw['nodes'][1]['evidenceRefs']=['invented']
        with self.assertRaisesRegex(ValueError,'evidence_ref'):
            g.compile_graph('DecisionGraph',raw,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        raw=decision_raw();raw['nodes'][1]['priority']=float('nan')
        with self.assertRaisesRegex(ValueError,'nonfinite'):
            g.compile_graph('DecisionGraph',raw,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        raw=decision_raw();raw['edges'].append({'sourceRef':'DA2','targetRef':'DA1','relation':'depends_on'})
        with self.assertRaisesRegex(ValueError,'dependency_cycle'):
            g.compile_graph('DecisionGraph',raw,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})

    def test_permission_dependency_and_conflict(self):
        self.assertEqual(g.admit_actions(self.d,['DA2'])['admitted'],[])
        raw=decision_raw();raw['edges'].append({'sourceRef':'DA1','targetRef':'DA2','relation':'conflicts_with'})
        decision=g.compile_graph('DecisionGraph',raw,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        result=g.admit_actions(decision,['DA1','DA2'])
        self.assertEqual(result['admitted'],[])
        self.assertEqual(set(result['deferred'].values()),{'CONFLICT_REQUIRES_RESOLUTION'})

    def test_partitions_merge_and_task_mapping(self):
        parts=g.partition_actions(self.d,self.a)
        self.assertEqual([p['domain'] for p in parts],['creative','traffic'])
        results=[{'partitionHash':p['receiptHash'],'plan':{'nodes':[plan_node('P'+key,key) for key in p['actionKeys']]}} for p in parts]
        plan=g.merge_plans(self.d,self.a,parts,list(reversed(results)),evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        self.assertEqual(plan['edges'],[{'sourceRef':'PDA1','targetRef':'PDA2','relation':'depends_on'}])
        raw={'nodes':[{'kind':'OperationStage','nodeKey':'O1','planActionRefs':['PDA1','PDA2'],
            'instruction':'按方案执行','owner':'运营','executionObject':'商品1','sequence':0,'rollback':'恢复原设置',
            'stopConditionRefs':['PDA1:stop'],'acceptanceActions':['核对执行记录']}]}
        operations=g.compile_graph('OperationGraph',raw,upstream=plan)
        task=g.map_task(self.d,self.a,plan,operations)
        self.assertEqual(task['planActionKeys'],['PDA1','PDA2'])
        with self.assertRaisesRegex(ValueError,'partition_result_missing'):
            g.merge_plans(self.d,self.a,parts,results[:1],evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        results[0]['plan']['nodes'][0]['decisionActionRef']='DA1'
        with self.assertRaisesRegex(ValueError,'partition_action_coverage'):
            g.merge_plans(self.d,self.a,parts,results,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})

    def test_plan_cannot_reselect_or_change_delta(self):
        raw={'nodes':[plan_node('P1','missing')]}
        with self.assertRaisesRegex(ValueError,'decision_action_ref_invalid'):
            g.compile_graph('PlanGraph',raw,upstream=self.d,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        raw={'nodes':[plan_node('P1','DA1')]};raw['nodes'][0]['expectedOutcome']['roas']['expectedDelta']=3
        with self.assertRaisesRegex(ValueError,'expected_delta_mismatch'):
            g.compile_graph('PlanGraph',raw,upstream=self.d,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})

    def test_plan_safety_contract_is_structured_and_single_semantic(self):
        raw={'nodes':[plan_node('P1','DA1')]}
        g.compile_graph('PlanGraph',raw,upstream=self.d,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        prose=deepcopy(raw);prose['nodes'][0]['guard']={'stop':'ROI下降时停止'}
        with self.assertRaisesRegex(ValueError,'plan_safety_rule_shape'):
            g.compile_graph('PlanGraph',prose,upstream=self.d,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        unknown=deepcopy(raw);unknown['nodes'][0]['riskBoundary']=[{'metric':'roas','comparator':'EQ','value':1}]
        with self.assertRaisesRegex(ValueError,'plan_safety_comparator'):
            g.compile_graph('PlanGraph',unknown,upstream=self.d,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        criterion=deepcopy(raw);criterion['nodes'][0]['acceptanceCriteria']=[{'metric':'roas','constraint':'manualJudgement'}]
        with self.assertRaisesRegex(ValueError,'acceptance_criterion_not_compiled'):
            g.compile_graph('PlanGraph',criterion,upstream=self.d,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})

    def test_legacy_perturbation_and_missing_graph_no_fallback(self):
        original=g.graph_input('agent2',{'DecisionGraph':self.d})
        source={'DecisionGraph':self.d,**{key:'forged' for key in g.contract()['legacyReadForbidden']}}
        self.assertEqual(original,g.graph_input('agent2',source))
        with self.assertRaisesRegex(ValueError,'graph_required'):
            g.graph_input('agent2',{'lockedActionFamily':'roas_scale'})
        copied=deepcopy(self.d);copied['nodes'][0]['reasoning']='changed'
        with self.assertRaisesRegex(ValueError,'hash_mismatch'):g.index(copied)

    def test_identity_scope_and_knowledge_version(self):
        args={'source':self.d,'rag_head':'head1','business_scope':{'storeId':'s1'},'model_config_hash':'model','retrieval_policy_hash':'policy'}
        a=g.semantic_identity('agent2',**args)
        self.assertNotEqual(a,g.semantic_identity('agent2',**{**args,'rag_head':'head2'}))
        self.assertNotEqual(a,g.semantic_identity('agent2',**{**args,'business_scope':{'storeId':'s2'}}))
        reordered=decision_raw();reordered['nodes'].reverse()
        self.assertEqual(self.d,g.compile_graph('DecisionGraph',reordered,evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}}))


if __name__=='__main__':unittest.main()