"""Check the actual task mapping -> public DTO seam retains a frozen receipt."""
from src.services.v26_sop_evidence_service import digest, freeze_decision_evidence
from src.services.pipeline_task_mapping_v225_service import _compile_current_task_mapping_decision
from src.services.public_task_dto_service import project_task_detail


def test_frozen_decision_survives_real_task_mapping_and_public_projection():
    node={'nodeKey':'A1','authorityHeaders':{'plan.daily_budget':0,'plan.strategy_summary':'以预算审批为准'},'nodeHash':'node'}
    graph={'actor':'agent2','nodes':[node],'edges':[]};graph['graphHash']=digest(graph)
    draft={'v26ActionGraph':graph}
    sop={'finalTaskTitle':'投流计划','inputContentHash':'input','productId':'P1','storeId':'S1'}
    sop['sopDecisionEvidence']=freeze_decision_evidence({'agent2ActionDraft':draft},sop)
    mapped=_compile_current_task_mapping_decision({'agent2ActionDraft':draft,'agent3Sop':sop},{'taskPlan':{'title':'投流计划'}})
    response=project_task_detail({'taskId':'T1','taskPlan':mapped['taskPlan']})
    assert next(card for card in response['sopEvidence']['cards'] if card['label']=='每日预算')['value']==0
    assert response['sopEvidence']['receipts'][0]['hash']==sop['sopDecisionEvidence']['receiptHash']
    assert response['sopEvidence']['privateReasoningExposed'] is False


def test_agent3_transport_preserves_sealed_graph_and_rejects_tampering(monkeypatch):
    from copy import deepcopy
    import pytest
    from src.services import agent_input_transport_v225_service as transport
    monkeypatch.setattr(transport, 'build_agent3_company_context', lambda source: {'companyOperatingPolicySnapshot': {'version': 'test'}, 'companySopRagSnapshot': {'version': 'test'}})
    node = {'nodeKey': 'A1', 'authorityHeaders': {'plan.parameter_pack': {
        'objective': None, 'missing': [], 'zero': 0, 'enabled': False,
        'items': list(range(25))}}}
    node['nodeHash'] = digest(node)
    graph = {'schema': 'v26.action_graph.v1', 'nodes': [node], 'edges': []}
    graph['graphHash'] = digest(graph)
    source = {'packageId': 'PKG-1', 'productId': 'P1', 'storeId': 'S1',
              'lockedActionFamily': 'roas_scale', 'agent2ActionDraft': {'v26ActionGraph': graph}}
    envelope = transport.compile_agent3_sop_envelope(source, source_ref='ART-source', source_content_hash='sha256:source')
    projected = envelope['payload']['agent2ActionDraft']['v26ActionGraph']
    assert projected == graph
    assert projected is not graph
    assert freeze_decision_evidence(envelope['payload'], {})['receiptHash']
    corrupt = deepcopy(source)
    corrupt['agent2ActionDraft']['v26ActionGraph']['nodes'][0]['authorityHeaders']['plan.parameter_pack']['zero'] = 1
    with pytest.raises(ValueError, match='revision_graph_content_mismatch'):
        transport.compile_agent3_sop_envelope(corrupt, source_ref='ART-source', source_content_hash='sha256:source')


def test_existing_agent3_projection_with_damaged_graph_is_not_reused(monkeypatch):
    from src.services import agent_input_transport_v225_service as transport
    monkeypatch.setattr(transport, 'artifact_refs_from_row', lambda row: {'agent3SopInputRef': 'ART-old'})
    monkeypatch.setattr(transport, 'validate_artifact', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(transport, 'assert_agent_input_envelope', lambda *a, **k: None)
    monkeypatch.setattr(transport, 'resolve_artifact', lambda *a: {
        'payload': {'agent2ActionDraft': {'v26ActionGraph': {'graphHash': 'sha256:invalid'}}},
        'sourceArtifactRefs': ['ART-source'], 'sourceContentHash': 'source',
        'projectionAudit': {'transportDeduplicated': True, 'transportVersion': transport.AGENT_INPUT_TRANSPORT_VERSION}})
    assert transport._existing({}, ref_key='agent3SopInputRef', schema=transport.AGENT3_SOP_INPUT_SCHEMA,
                               source_ref='ART-source', source_hash='source') is None
