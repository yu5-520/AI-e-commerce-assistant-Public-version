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
