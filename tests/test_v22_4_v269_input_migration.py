"""Exercise installed transport, validators, identity consumers and provider fence."""
import unittest
from copy import deepcopy
from src.services import v269_semantic_graph_service as graphs
from src.services import v269_input_migration_service as migration
from src.services import agent_input_transport_v225_service as transport
from src.services import agent_input_contract_v225_service as inputs
from src.services import agent_token_runtime_v22520_service as agent2
from src.services import agent3_runtime_v23215_service as agent3
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
        self.assertFalse(a['cacheEligible'])
        with self.assertRaisesRegex(ValueError,'provider_cutover_not_ready'):
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
        self.assertFalse(identity['cacheEligible'])
        with self.assertRaisesRegex(ValueError,'provider_cutover_not_ready'):agent3._entry(envelope)
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
