"""Frozen input association, atomic task persistence and corruption boundaries."""
import sqlite3

import pytest
from src.services import v26_sop_evidence_service as evidence
from src.services import task_detail_snapshot_v2024_service as snapshots
from src.services.company_sop_rag_context_v225_service import build_company_sop_rag_snapshot
from src.services.v25_agent_input_ingress_service import _sanitize_agent2_rag_audit


def retrieval(revision='r1'):
    body = {'schema': 'rag.knowledge_retrieval_receipt.v1', 'queryFingerprint': 'query',
            'matchedRevisionIds': [revision], 'indexManifestHash': 'index'}
    return {**body, 'retrievalReceiptHash': evidence.digest(body).removeprefix('sha256:')}


def task(task_id='target1', receipt=None):
    package = {'companySopRagSnapshot': {'knowledgeRetrievalReceipt': receipt or retrieval()}}
    return {'taskId': task_id, 'taskPlan': {'sopDecisionEvidence': evidence.freeze_decision_evidence(package, {})}}


def event(receipt=None, revision='r1'):
    return {'retrievalReceiptHash': (receipt or retrieval())['retrievalReceiptHash'],
            'revisionId': revision, 'retrievalReceiptVerification': 'VERIFIED', 'retrievalProof': {}}


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    snapshots._ensure_table(conn)
    conn.commit()
    yield conn
    conn.close()


def test_receipt_survives_ingress_company_context_and_freezing():
    original = retrieval()
    audit = _sanitize_agent2_rag_audit({'knowledgeRetrievalReceipt': original})
    context = build_company_sop_rag_snapshot({'ragContextSnapshot': audit})
    frozen = evidence.freeze_decision_evidence({'companySopRagSnapshot': context}, {})
    assert frozen['knowledge']['retrievalReceipts'] == [original]
    original['matchedRevisionIds'].append('later-mutation')
    assert evidence.verified(frozen)
    assert frozen['knowledge']['retrievalReceipts'][0]['matchedRevisionIds'] == ['r1']


def test_real_snapshot_write_is_atomic_idempotent_and_exact(db):
    value = task()
    assert snapshots.upsert_task_detail_snapshot_in_conn(db, value)['stored']
    snapshots.upsert_task_detail_snapshot_in_conn(db, value)
    assert db.execute('SELECT COUNT(*) FROM task_knowledge_bindings_v26').fetchone()[0] == 1
    hit, miss, wrong_revision = event(), event(retrieval('r2')), event(revision='r2')
    evidence.bind_subsequent_tasks(db, [hit, miss, wrong_revision])
    assert hit['taskBindings'][0]['taskId'] == 'target1'
    assert hit['retrievalProof']['subsequentTaskBinding'] == 'VERIFIED'
    assert miss['taskBindings'] == wrong_revision['taskBindings'] == []
    db.rollback()
    for table in ('task_detail_snapshots', 'task_knowledge_bindings_v26', 'task_sop_receipts_v26'):
        assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


@pytest.mark.parametrize('corruption', ['hash', 'receipt', 'orphan'])
def test_corrupt_or_dangling_links_are_not_presented_as_valid(db, corruption):
    evidence.persist_task_knowledge_bindings(db, task())
    if corruption == 'hash':
        db.execute("UPDATE task_knowledge_bindings_v26 SET binding_hash='forged'")
    elif corruption == 'receipt':
        db.execute("UPDATE task_sop_receipts_v26 SET receipt_json='{}'")
    else:
        db.execute('DELETE FROM task_sop_receipts_v26')
    result = event()
    evidence.bind_subsequent_tasks(db, [result])
    assert result['taskBindings'] == []
    assert result['retrievalProof']['subsequentTaskBinding'] == 'INVALID_EVIDENCE'


def test_missing_history_is_not_inferred_and_reads_are_bounded(db):
    evidence.persist_task_knowledge_bindings(db, {'taskId': 'legacy', 'taskPlan': {}})
    empty = event()
    evidence.bind_subsequent_tasks(db, [empty])
    assert empty['retrievalProof']['subsequentTaskBinding'] == 'NOT_RECORDED'
    for i in range(22):
        evidence.persist_task_knowledge_bindings(db, task(f't{i:02}'))
    queries = []
    db.set_trace_callback(queries.append)
    result = event()
    evidence.bind_subsequent_tasks(db, [result, event(revision='other')])
    db.set_trace_callback(None)
    assert len(queries) == 1
    assert len(result['taskBindings']) == 20 and result['taskBindingsTruncated']


def test_invalid_or_oversized_input_receipt_fails_before_snapshot_write(db):
    invalid = retrieval()
    invalid['matchedRevisionIds'].append('forged')
    with pytest.raises(ValueError, match='receipt_invalid'):
        task(receipt=invalid)
    body = {'schema': 'rag.knowledge_retrieval_receipt.v1', 'matchedRevisionIds': [str(i) for i in range(101)]}
    with pytest.raises(ValueError, match='receipt_budget'):
        task(receipt={**body, 'retrievalReceiptHash': evidence.digest(body)})
    assert db.execute('SELECT COUNT(*) FROM task_knowledge_bindings_v26').fetchone()[0] == 0


def test_explicit_context_does_not_inherit_an_unrelated_retrieval_receipt():
    context = build_company_sop_rag_snapshot({'companySopRagSnapshot': {'approvedCaseIds': ['explicit']},
        'ragContextSnapshot': {'knowledgeRetrievalReceipt': retrieval()}})
    assert context['knowledgeRetrievalReceipt'] is None
