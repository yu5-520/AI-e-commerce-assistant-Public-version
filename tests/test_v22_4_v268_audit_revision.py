"""Audit write -> snapshot -> public view and local revision acceptance boundaries."""
from copy import deepcopy
import pytest
from src.services import v26_sop_evidence_service as evidence
from src.services import v26_revision_acceptance_service as revision
from src.services.task_detail_snapshot_v2024_service import build_task_detail_snapshot
from src.services.public_task_dto_service import project_task_detail


def graph(values):
    nodes = []
    for key, value in values.items():
        body = {'nodeKey': key, 'authorityHeaders': {'operation_stage.objective': value}}
        nodes.append({**body, 'nodeHash': evidence.digest(body)})
    body = {'nodes': nodes, 'edges': []}
    return {**body, 'graphHash': evidence.digest(body)}


def scope(parent):
    body = {'reviewHash': 'review', 'parentOperationGraphHash': parent['graphHash'],
            'reopenOperationNodeHashes': [parent['nodes'][0]['nodeHash']],
            'preservedOperationNodeHashes': [parent['nodes'][1]['nodeHash']]}
    return {**body, 'revisionHash': evidence.digest(body)}


def test_actual_review_write_snapshot_dto_and_tamper(monkeypatch):
    from src.services import task_evidence_service as writer
    source = {'taskId': 't1', 'latestEvidenceRecord': {'id': 'submission', 'summary': 'private-submission'},
              'taskPlan': {'sopDecisionEvidence': evidence.freeze_decision_evidence({}, {})}}
    monkeypatch.setattr(writer, '_task_or_none', lambda *a: source)
    monkeypatch.setattr(writer, 'update_task', lambda task_id, patch, **kw: {**source, **patch})
    updated = writer.review_task_evidence('t1', {'decision': 'approve', 'note': 'private-note'})
    receipt = updated['evidenceReviews'][0]['auditReceipt']
    assert receipt['submissionHash'] == evidence.digest(source['latestEvidenceRecord'])
    dto = project_task_detail(build_task_detail_snapshot(updated))
    audit = dto['sopEvidence']['operatorReviews']
    assert audit['status'] == 'RECORDED' and len(audit['records']) == 1
    assert 'private-note' not in str(audit) and 'private-submission' not in str(audit)
    assert audit['records'][0]['lifecycleTransitionVerified'] is False
    assert evidence.public_operator_reviews('other-task', updated['evidenceReviews'])['invalidCount'] == 1
    updated['evidenceReviews'][0]['decision'] = 'reject'
    assert evidence.public_operator_reviews('t1', updated['evidenceReviews'])['records'] == []


def test_missing_history_and_boundaries_are_explicit():
    result = evidence.public_operator_reviews('t1', [{}] * 11)
    assert result['status'] == 'NOT_RECORDED' and result['unsealedCount'] == 10
    assert result['truncated'] and result['retainedHistoryLimit'] == 10
    assert result['systemReviewStatus'] == 'NOT_CONNECTED'


def test_revision_diff_retains_zero_null_and_unchanged_nodes():
    before = graph({'a': 0, 'b': 'keep'})
    after = graph({'a': None, 'b': 'keep'})
    receipt = revision.freeze_revision_acceptance(before, after, scope(before), graph_kind='Operation')
    assert receipt['changedCount'] == receipt['unchangedCount'] == 1
    assert receipt['nodes'][0]['fields'][0]['before'] == 0
    assert receipt['nodes'][0]['fields'][0]['afterPresent'] is True
    frozen = evidence.freeze_decision_evidence({}, {'revisionAcceptanceEvidence': receipt})
    dto = project_task_detail(build_task_detail_snapshot({'taskId': 't', 'taskPlan': {'sopDecisionEvidence': frozen}}))
    assert dto['sopEvidence']['revision']['receiptHash'] == receipt['receiptHash']
    assert dto['sopEvidence']['revision']['authorityOriginVerified'] is False
    receipt['nodes'][0]['afterHash'] = 'tamper'
    frozen = evidence.freeze_decision_evidence({}, {'revisionAcceptanceEvidence': receipt})
    assert 'revision' not in evidence.public_evidence(frozen, None)


def add_edge(g, relation='depends_on_stage'):
    a, b = g['nodes']
    edge = {'sourceKey': b['nodeKey'], 'sourceNodeHash': b['nodeHash'],
            'targetKey': a['nodeKey'], 'targetNodeHash': a['nodeHash'], 'relation': relation}
    g['edges'] = [{**edge, 'edgeHash': evidence.digest(edge)}]
    g['graphHash'] = evidence.digest({k:v for k,v in g.items() if k != 'graphHash'})
    return g


def test_preserved_relationships_cannot_be_rewired_but_reopened_hash_can_change():
    before = add_edge(graph({'a': 'old', 'b': 'keep'}))
    after = add_edge(graph({'a': 'new', 'b': 'keep'}))
    assert revision.verify_revision_result(before, after, scope(before), graph_kind='Operation')['verified']
    after = add_edge(graph({'a': 'new', 'b': 'keep'}), 'new-relation')
    with pytest.raises(ValueError, match='preserved_edge'):
        revision.verify_revision_result(before, after, scope(before), graph_kind='Operation')
    after = graph({'a': 'new', 'b': 'keep'})
    with pytest.raises(ValueError, match='preserved_edge'):
        revision.verify_revision_result(before, after, scope(before), graph_kind='Operation')
    with pytest.raises(ValueError, match='preserved_node'):
        revision.verify_revision_result(before, graph({'a':'new','b':'changed'}), scope(before), graph_kind='Operation')
