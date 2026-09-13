"""Exercise installed transport, validators, identity consumers and provider fence."""
import unittest
from unittest.mock import patch
from copy import deepcopy
from src.services import v269_semantic_graph_service as graphs
from src.services import v269_input_migration_service as migration
from src.services import agent_input_transport_v225_service as transport
from src.services import agent_input_contract_v225_service as inputs
from src.services import agent_token_runtime_v22520_service as agent2
from src.services import agent3_runtime_v23215_service as agent3
from src.services import agent_token_runtime_v225_service as active_runtime
from tests.test_v22_4_v269_semantic_graph import decision_raw, plan_node


class InputMigrationTests(unittest.TestCase):
    def setUp(self):
        self.decision=graphs.compile_graph('DecisionGraph',decision_raw(),evidence_refs=['fact:1'])
        self.admission=graphs.admit_actions(self.decision,['DA1','DA2'])
        self.parts=graphs.partition_actions(self.decision,self.admission)
        knowledge={'retrievalPolicyHash':'policy','records':[]}
        self.knowledge={**knowledge,'headHash':graphs.digest(knowledge)}
        self.source={'semanticContractVersion':'26.9.0','packageId':'pkg','productId':'p','storeId':'s',
            'DecisionGraph':self.decision,'actionAdmission':self.admission,'partition':self.parts[0],
            'factValues':{'fact:1':{'value':0,'unit':'ratio'}},'knowledgeContext':self.knowledge}
        self.descriptor={'provider':'test-provider','model':'test-model','generationParametersHash':'generation','promptVersion':'26.9.0'}

    def envelope(self, source=None):
        return transport.compile_agent2_draft_envelope(source or self.source,source_ref='ART-input',source_content_hash='source-hash')

    def test_installed_agent2_transport_isolates_old_fields(self):
        base=self.envelope()
        source={**self.source, **{key:'poison' for key in graphs.contract()['legacyReadForbidden']}}
        self.assertEqual(base,self.envelope(source))
        self.assertTrue(inputs.validate_agent_input_envelope(base)['ok'])
        self.assertNotIn('v26JudgementGraph',base['payload'])
        self.assertNotIn('lockedActionFamily',base['payload'])
        source=dict(self.source);source.pop('DecisionGraph');source['lockedActionFamily']='roas_scale'
        with self.assertRaisesRegex(ValueError,'graph_required'):self.envelope(source)

    def test_validator_rejects_resealed_legacy_payload_and_mutated_partition(self):
        envelope=self.envelope();envelope['payload']['lockedActionFamily']='roas_scale'
        envelope['projectedContentHash']=inputs.content_hash(envelope['payload'])
        self.assertFalse(inputs.validate_agent_input_envelope(envelope)['ok'])
        bad=deepcopy(self.source);bad['partition']['actionKeys']=['DA1']
        with self.assertRaisesRegex(ValueError,'partition_mismatch'):self.envelope(bad)

    def test_actual_agent2_identity_uses_partition_and_ignores_legacy(self):
        e=self.envelope();p=e['payload']
        a=agent2.build_agent2_semantic_identity(e,self.descriptor,p)
        b=agent2.build_agent2_semantic_identity(e,self.descriptor,{**p,'lockedActionFamily':'poison','primaryAction':'poison'})
        self.assertEqual(a,b)
        other={**self.source,'partition':self.parts[1]};oe=self.envelope(other)
        self.assertNotEqual(a['semanticHash'],agent2.build_agent2_semantic_identity(oe,self.descriptor,oe['payload'])['semanticHash'])
        self.assertTrue(a['cacheEligible'])
        with patch.object(agent2, 'resolve_input_binding', side_effect=ValueError('binding_required')):
            with self.assertRaisesRegex(ValueError,'binding_required'):
                agent2._entry(e,provider={'provider':'test','model':'test'})

    def test_agent3_transport_identity_and_execution_fence(self):
        plan=graphs.compile_graph('PlanGraph',{'nodes':[plan_node('P1','DA1')]},upstream=self.decision,
            evidence_refs=['fact:1'],fact_values={'fact:1':{'value':0,'unit':'ratio'}})
        source={'semanticContractVersion':'26.9.0','packageId':'pkg','productId':'p','storeId':'s',
            'PlanGraph':plan,'knowledgeContext':self.knowledge,'lockedActionFamily':'poison'}
        envelope=transport.compile_agent3_sop_envelope(source,source_ref='ART-plan',source_content_hash='plan-hash')
        self.assertTrue(inputs.validate_agent_input_envelope(envelope)['ok'])
        identity=agent3.build_agent3_semantic_identity(envelope,self.descriptor,envelope['payload'])
        self.assertEqual(identity['cachedChannel'],'OperationGraph')
        self.assertTrue(identity['cacheEligible'])
        with patch.object(agent3.hash_runtime, '_binding_descriptor', side_effect=ValueError('binding_required')):
            with self.assertRaisesRegex(ValueError,'binding_required'):agent3._entry(envelope)
        source['knowledgeContext']={**self.knowledge,'records':[{'different':'record'}]}
        with self.assertRaisesRegex(ValueError,'knowledge_head_mismatch'):
            transport.compile_agent3_sop_envelope(source,source_ref='ART-plan',source_content_hash='plan-hash')

    def test_source_graph_budget_and_contract_version_still_enforced(self):
        source={**self.source,'semanticContractVersion':'unsupported'}
        with self.assertRaisesRegex(ValueError,'contract_version'):self.envelope(source)
        envelope=self.envelope();envelope['payload']['knowledgeContext']['records']=['large'*5000]
        body={k:v for k,v in envelope['payload']['knowledgeContext'].items() if k!='headHash'}
        envelope['payload']['knowledgeContext']['headHash']=graphs.digest(body)
        envelope['projectedContentHash']=inputs.content_hash(envelope['payload'])
        self.assertIn('projection_item_budget_exceeded',inputs.validate_agent_input_envelope(envelope)['errors'])

    def test_installed_provider_prompts_and_output_acceptance(self):
        package=self.envelope()['payload']
        messages,payload=agent2._build_messages('d',[package])
        self.assertEqual(payload['outputContract']['exactlyOneBusinessChannel'][0],'PlanGraph')
        self.assertNotIn('immutableContext',payload['packages'][0])
        key=package['partition']['actionKeys'][0]
        raw={'packageId':'pkg','PlanGraph':{'nodes':[plan_node('P1',key)]}}
        output=agent2._normalize_draft(raw,package,{})
        self.assertEqual(output['draftStatus'],'draft_ready')
        self.assertNotIn('familyPayload',output)
        self.assertNotIn('v26ActionGraph',output)
        self.assertEqual(output['PlanGraph']['nodes'][0]['decisionActionRef'],key)
        for bad in ({**raw,'lockedActionFamily':'roas_guard'},
                    {**raw,'missingData':['missing']},
                    {**raw,'PlanGraph':{'nodes':[plan_node('P1','DA1' if key=='DA2' else 'DA2')]}}):
            with self.assertRaises(ValueError):agent2._normalize_draft(bad,package,{})
        source={k:v for k,v in self.source.items() if k in migration.COMMON}
        source['PlanGraph']=output['PlanGraph']
        envelope=transport.compile_agent3_sop_envelope(source,source_ref='ART-plan',source_content_hash='plan-hash')
        package3=envelope['payload']
        _,payload3=agent3.core._build_messages('d',[package3])
        self.assertEqual(payload3['outputContract']['exactlyOneBusinessChannel'][0],'OperationGraph')
        operation={'nodes':[{'kind':'OperationStage','nodeKey':'O1','planActionRefs':['P1'],
            'instruction':'执行方案参数','owner':'运营','executionObject':'商品p','sequence':0,
            'rollback':'恢复原设置','stopConditionRefs':['P1:stop'],'acceptanceActions':['核验记录']}]}
        sop=agent3.core._normalize_sop({'packageId':'pkg','OperationGraph':operation},package3,{})
        self.assertEqual(sop['sopStatus'],'sop_ready')
        self.assertNotIn('lockedActionFamily',sop)
        self.assertEqual(sop['OperationGraph']['upstreamGraphHash'],output['PlanGraph']['graphHash'])
        bad=deepcopy(operation);bad['nodes'][0]['parameters']={'budget':999}
        with self.assertRaisesRegex(ValueError,'node_field_authority'):
            agent3.core._normalize_sop({'packageId':'pkg','OperationGraph':bad},package3,{})

    def test_graph_execution_keeps_exact_identity_acceptance(self):
        package=self.envelope()['payload'];key=package['partition']['actionKeys'][0]
        descriptor={**self.descriptor,'packageId':'pkg','itemExecutionId':'exec-1','inputContentHash':'input-1',
            'executionHash':'execution-hash','businessPartition':package['partition']['domain']}
        entry={'package':package,'descriptor':descriptor,'claim':{'claimId':'claim'}}
        def gateway(**kwargs):
            import json
            request=json.loads(kwargs['messages'][-1]['content'])
            self.assertEqual(request['outputContract']['exactlyOneBusinessChannel'][0],'PlanGraph')
            self.assertEqual(request['packages'][0]['itemExecutionId'],'exec-1')
            return {'plans':[{'packageId':'pkg','itemExecutionId':'exec-1','inputContentHash':'input-1',
                'PlanGraph':{'nodes':[plan_node('P1',key)]}}]},{}
        with patch.multiple(agent2,create_batch_manifest=lambda **kw:{},call_json_exact_artifact=gateway,
                store_raw_batch_output=lambda **kw:{'artifactId':'ART-raw'},
                store_item_output=lambda **kw:{'artifactId':'ART-output','contentHash':'output-hash'},
                complete_execution=lambda *a,**kw:{},finalize_batch=lambda **kw:{}):
            outputs,outcomes,_,_=agent2._execute_batch([entry],data_version='d',provider=self.descriptor)
        self.assertEqual(outcomes,{'exec-1':'exact'})
        self.assertEqual(outputs['pkg']['PlanGraph']['nodes'][0]['decisionActionRef'],key)
        self.assertEqual(outputs['pkg']['inputContentHash'],'input-1')

    def test_agent1_exact_decision_graph_and_cache_revalidation(self):
        from src.services import agent_input_transport_v2258_service as transport1
        from src.services import real_product_judgment_agent_v2259_service as core1
        from src.services import agent_token_runtime_hash_exact_v2259_service as runtime1
        source={k:v for k,v in self.source.items() if k in migration.COMMON}
        source.update(BusinessFacts={'cpc':{'current':2,'previous':1}},evidenceRefs=['fact:1'])
        envelope=transport1.compile_agent1_envelope(source,source_ref='ART-facts',source_content_hash='facts-hash')
        self.assertTrue(inputs.validate_agent_input_envelope(envelope)['ok'])
        identity=runtime1.build_agent1_semantic_identity(envelope,self.descriptor)
        self.assertEqual(identity['cachedChannel'],'DecisionGraph')
        descriptor={**self.descriptor,'itemExecutionId':'exec-1','inputContentHash':'hash-1','semanticHash':identity['semanticHash']}
        product={**envelope['payload'],'_hashExecution':descriptor}
        _,prompt=core1._build_messages('d',[product],{})
        self.assertNotIn('primaryAction',prompt['products'][0])
        raw={'itemExecutionId':'exec-1','inputContentHash':'hash-1','DecisionGraph':decision_raw()}
        outputs,diagnostics=core1._normalize_judgments({'judgments':[raw]},[product],'d')
        self.assertEqual(diagnostics['exactHashMatchedCount'],1)
        self.assertEqual(outputs[0]['DecisionGraph'],self.decision)
        self.assertNotIn('v26JudgementGraph',outputs[0])
        invalid={**raw,'inputContentHash':'other'}
        outputs_bad,diagnostics=core1._normalize_judgments({'judgments':[invalid]},[product],'d')
        self.assertEqual(outputs_bad,[])
        self.assertEqual(len(diagnostics['inputContentHashMismatches']),1)
        rebound=runtime1._rebind_semantic_output(outputs[0],descriptor=descriptor,product=product,
            source={'execution':{'execution_hash':'old'},'outputArtifactRef':'ART-old'})
        self.assertEqual(rebound['DecisionGraph'],outputs[0]['DecisionGraph'])
        changed=deepcopy(product);changed['evidenceRefs']=['different']
        with self.assertRaisesRegex(ValueError,'evidence_ref'):
            runtime1._rebind_semantic_output(outputs[0],descriptor=descriptor,product=changed,
                source={'execution':{'execution_hash':'old'},'outputArtifactRef':'ART-old'})

    def test_plan_cache_rebinds_only_revalidated_graph(self):
        package=self.envelope()['payload'];key=package['partition']['actionKeys'][0]
        output=agent2._normalize_draft({'packageId':'pkg','PlanGraph':{'nodes':[plan_node('P1',key)]}},package,{})
        source={'graphOutput':output,'execution':{'execution_hash':'old'},'outputArtifactRef':'ART-old'}
        entry={'package':{**package,'packageId':'new-pkg'},'descriptor':{'semanticHash':'semantic'}}
        rebound=agent2._rebind_semantic_family_payload(source,entry=entry)
        self.assertEqual(rebound['packageId'],'new-pkg')
        self.assertEqual(rebound['PlanGraph'],output['PlanGraph'])
        self.assertEqual(rebound['agent2ApiCallCount'],0)
        self.assertNotIn('familyPayload',rebound)
        changed=deepcopy(entry);changed['package']['factValues']['fact:1']['value']=1
        with self.assertRaisesRegex(ValueError,'baseline_fact_mismatch'):
            agent2._rebind_semantic_family_payload(source,entry=changed)
        old={'familyPayload':{'anything':'old'}}
        with self.assertRaisesRegex(ValueError,'cache_contract_mismatch'):
            agent2._rebind_semantic_family_payload(old,entry=entry)

    def test_sop_formula_and_scoped_revision_keep_protected_edges(self):
        from src.services.v26_revision_acceptance_service import freeze_revision_acceptance
        from src.services.v26_sop_evidence_service import verified
        package=self.envelope()['payload'];key=package['partition']['actionKeys'][0]
        plan=agent2._normalize_draft({'packageId':'pkg','PlanGraph':{'nodes':[plan_node('P1',key)]}},package,{})['PlanGraph']
        evidence=migration.freeze_graph_evidence({'PlanGraph':plan,'knowledgeContext':self.knowledge},{})
        self.assertTrue(verified(evidence))
        derived=[c for c in evidence['cards'] if c['status']=='RECOMPUTED']
        self.assertEqual(derived[0]['value'],2)
        self.assertEqual(derived[0]['formula'],'expectedValue - baseline.value')
        hashes={n['nodeKey']:n['nodeHash'] for n in self.decision['nodes']}
        body={'parentDecisionGraphHash':self.decision['graphHash'],
            'reopenDecisionNodeHashes':[hashes['DA1']],
            'preservedDecisionNodeHashes':[hashes['J1'],hashes['DA2']]}
        scope={**body,'revisionHash':graphs.digest(body)}
        raw=decision_raw();raw['nodes'][1]['priority']=0.8
        target=graphs.compile_graph('DecisionGraph',raw,evidence_refs=['fact:1'])
        receipt=freeze_revision_acceptance(self.decision,target,scope,graph_kind='Decision')
        self.assertEqual(receipt['changedCount'],1)
        change=next(n for n in receipt['nodes'] if n['changed'])
        self.assertEqual(change['fields'][0]['field'],'priority')
        raw['edges']=raw['edges'][:1]
        target=graphs.compile_graph('DecisionGraph',raw,evidence_refs=['fact:1'])
        with self.assertRaisesRegex(ValueError,'preserved_edge_changed'):
            freeze_revision_acceptance(self.decision,target,scope,graph_kind='Decision')

    def test_graph_rag_routing_uses_partition_not_legacy_lock(self):
        from src.services.agent_hash_routed_rag_bridge_v1_service import build_agent_rag_route, AGENT2_RAG_STAGE
        package=self.envelope()['payload']
        route=build_agent_rag_route(package,stage=AGENT2_RAG_STAGE,snapshot={'query':'legacy'})
        self.assertIn('action_family:title_image_test',route['routeTags'])
        self.assertNotIn('action_family:roas_guard',route['routeTags'])
        self.assertEqual(route,build_agent_rag_route(package,stage=AGENT2_RAG_STAGE,snapshot={'query':'poison'}))

    def test_rehashed_invalid_decision_or_admission_is_rejected(self):
        bad=deepcopy(self.decision);bad['nodes'][0]['priority']=9
        bad['nodes'][0]['nodeHash']=graphs.digest({k:v for k,v in bad['nodes'][0].items() if k!='nodeHash'})
        bad['graphHash']=graphs.digest({k:v for k,v in bad.items() if k!='graphHash'})
        with self.assertRaises(ValueError):graphs.index(bad)
        body={k:v for k,v in self.admission.items() if k!='receiptHash'};body['admitted']=['DA2']
        with self.assertRaisesRegex(ValueError,'admission_semantics_mismatch'):
            graphs.partition_actions(self.decision,graphs.seal(body))

    def test_existing_artifact_ledger_plan_sop_and_semantic_replay(self):
        import json
        import tempfile
        from pathlib import Path
        from src.repositories import sqlite_repository as db
        from src.services import artifact_storage_service as storage
        from src.services.artifact_transport_service import store_artifact
        from src.services import agent_token_runtime_hash_exact_v2259_service as runtime1
        calls=[]
        def decision_gateway(**kwargs):
            payload=json.loads(kwargs['messages'][-1]['content']);calls.append('agent1')
            return {'judgments':[{'itemExecutionId':p['itemExecutionId'],'inputContentHash':p['inputContentHash'],
                'DecisionGraph':decision_raw()} for p in payload['products']]},{'providerCallExecuted':True,'providerRequestId':'decision-request'}
        def plan_gateway(**kwargs):
            payload=json.loads(kwargs['messages'][-1]['content']);calls.append('agent2')
            return {'plans':[{'packageId':p['packageId'],'itemExecutionId':p['itemExecutionId'],
                'inputContentHash':p['inputContentHash'],'PlanGraph':{'nodes':[
                    plan_node('P'+k,k) for k in p['partition']['actionKeys']]}}
                for p in payload['packages']]},{'providerCallExecuted':True,'providerRequestId':'plan-request'}
        def sop_gateway(**kwargs):
            payload=json.loads(kwargs['messages'][-1]['content']);calls.append('agent3')
            return {'sops':[{'packageId':p['packageId'],'itemExecutionId':p['itemExecutionId'],
                'inputContentHash':p['inputContentHash'],'OperationGraph':{'nodes':[
                    {'kind':'OperationStage','nodeKey':'O1','planActionRefs':[n['nodeKey'] for n in p['PlanGraph']['nodes']],
                    'instruction':'按冻结方案执行','owner':'运营','executionObject':'商品p','sequence':0,
                    'rollback':'恢复原设置','stopConditionRefs':[n['nodeKey']+':stop' for n in p['PlanGraph']['nodes']],
                    'acceptanceActions':['核验执行记录']}]}}
                for p in payload['packages']]},{'providerCallExecuted':True,'providerRequestId':'sop-request'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(db,'DB_PATH',root/'ledger.sqlite'),patch.object(db,'_WAL_INITIALIZED',False),\
                 patch.object(storage,'ARTIFACT_ROOT',root/'artifacts'),\
                 patch.object(runtime1,'provider_runtime_config',return_value=self.descriptor),\
                 patch.object(runtime1,'call_json_exact_artifact',side_effect=decision_gateway),\
                 patch.object(agent2,'provider_runtime_config',return_value=self.descriptor),\
                 patch.object(agent2,'call_json_exact_artifact',side_effect=plan_gateway),\
                 patch.object(agent3.hash_runtime,'provider_runtime_config',return_value=self.descriptor),\
                 patch.object(agent3,'call_json',side_effect=sop_gateway):
                parent=store_artifact(artifact_type='test.facts',value={'facts':'frozen'})
                def persist(agent,source):
                    envelope=migration.project_input(agent,source,source_ref=parent['artifactId'],source_content_hash=parent['contentHash'])
                    store_artifact(artifact_type=envelope['schema'],value=envelope,
                        store_id='s',product_id='p',parent_refs=[parent['artifactId']])
                    return envelope
                source1={k:v for k,v in self.source.items() if k in migration.COMMON}
                source1.update(BusinessFacts={'cpc':{'current':2,'previous':1}},evidenceRefs=['fact:1'])
                judgments,summary=runtime1.run_agent1_projected_inputs([persist('agent1',source1)],data_version='d')
                self.assertEqual(len(judgments),1,summary)
                self.assertEqual(judgments[0]['DecisionGraph'],self.decision)
                envelopes=[persist('agent2',{**self.source,'packageId':'pkg-'+p['domain'],'partition':p}) for p in self.parts]
                outputs,summary=agent2.run_agent2_draft_projected_inputs(envelopes,data_version='d')
                self.assertEqual(len(outputs),2,summary)
                plan=graphs.merge_plans(self.decision,self.admission,self.parts,[
                    {'partitionHash':o['partitionHash'],'plan':migration.model_graph_body(o['PlanGraph'])}
                    for o in outputs.values()],evidence_refs=['fact:1'],fact_values=self.source['factValues'])
                source3={k:v for k,v in self.source.items() if k in migration.COMMON};source3['PlanGraph']=plan
                envelope3=persist('agent3',source3)
                sops,summary=agent3.run_agent3_sop_projected_inputs([envelope3],data_version='d')
                self.assertEqual(len(sops),1,summary)
                mapping=graphs.map_task(self.decision,self.admission,plan,sops['pkg']['OperationGraph'])
                self.assertEqual(len(mapping['planActionKeys']),2)
                before=len(calls)
                replay_envelopes=[persist('agent2',{**e['payload'],'packageId':e['payload']['packageId']+'-new'}) for e in envelopes]
                rebound,summary=agent2.run_agent2_draft_projected_inputs(replay_envelopes,data_version='d2')
                self.assertEqual(len(rebound),2,summary)
                self.assertTrue(all(o['semanticResultCacheHit'] for o in rebound.values()),summary)
                rebound3,summary=agent3.run_agent3_sop_projected_inputs([persist('agent3',{**source3,'packageId':'new-sop'})],data_version='d2')
                self.assertEqual(len(rebound3),1,summary)
                self.assertTrue(rebound3['new-sop']['semanticResultCacheHit'],summary)
                self.assertEqual(len(calls),before)
                self.assertNotEqual(rebound3['new-sop']['outputArtifactRef'],sops['pkg']['outputArtifactRef'])
