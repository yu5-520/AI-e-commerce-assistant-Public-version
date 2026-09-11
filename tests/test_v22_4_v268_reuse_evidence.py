"""Verify task-scoped RAG reuse statistics against immutable event records."""
import json
import sqlite3
from contextlib import contextmanager

import pytest
from src.services import v26_sop_evidence_service as evidence
from src.repositories import sqlite_repository


@pytest.fixture
def db(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE rag_knowledge_revisions(revision_id,content_hash,content_json,source_task_id,source_recap_hash,previous_revision_id,created_at);
        CREATE TABLE rag_knowledge_reuse_events(event_hash,revision_id,retrieval_receipt_hash,outcome,actor_id,notes,created_at);
    ''')
    for revision, task in [('r1', 't1'), ('r2', 't2')]:
        content = {'sourceTaskId': task}
        conn.execute('INSERT INTO rag_knowledge_revisions VALUES(?,?,?,?,?,?,?)',
                     (revision, evidence.digest(content), json.dumps(content), task, 'recap', None, '2026-09-11'))
    @contextmanager
    def connect():
        yield conn
    monkeypatch.setattr(sqlite_repository, 'connect', connect)
    yield conn
    conn.close()


def add_event(db, revision, outcome, receipt, *, corrupt=False):
    material = {'revisionId': revision, 'retrievalReceiptHash': receipt, 'outcome': outcome,
                'actorId': 'private-actor', 'notes': 'private-note'}
    db.execute('INSERT INTO rag_knowledge_reuse_events VALUES(?,?,?,?,?,?,?)',
               ('forged' if corrupt else evidence.digest(material), revision, receipt, outcome,
                material['actorId'], material['notes'], '2026-09-11'))


def test_scoped_reuse_formula_neutral_denominator_and_tamper(db):
    for outcome in ['success', 'failure', 'neutral']:
        add_event(db, 'r1', outcome, outcome)
    add_event(db, 'r1', 'success', 'corrupt', corrupt=True)
    add_event(db, 'r2', 'success', 'other-task')
    result = evidence.read_task_knowledge_audit('t1')
    summary = result['reuseSummary']
    assert result['status'] == 'INVALID_EVIDENCE'
    assert summary['total'] == 3 and summary['successRate'] == 1 / 3
    assert summary['inputs'] == {'success': 1, 'failure': 1, 'neutral': 1}
    assert summary['invalidRecordCount'] == 1
    assert set(summary['eventHashes']) == {e['eventHash'] for e in result['reuseEvents']}
    assert summary['causalEffectEvaluated'] is False
    assert 'private-actor' not in str(result) and 'private-note' not in str(result)
    assert all(e['retrievalReceiptVerification'] == 'NOT_CHECKED' for e in result['reuseEvents'])


def test_empty_and_zero_success_are_distinct(db):
    empty = evidence.read_task_knowledge_audit('t1')['reuseSummary']
    assert empty['successRate'] is None and empty['total'] == 0
    add_event(db, 'r1', 'failure', 'failed-receipt')
    actual = evidence.read_task_knowledge_audit('t1')['reuseSummary']
    assert actual['successRate'] == 0 and actual['total'] == 1


def test_bounded_sample_explicitly_reports_truncation(db):
    for index in range(101):
        add_event(db, 'r1', 'neutral', str(index))
    result = evidence.read_task_knowledge_audit('t1')
    assert len(result['reuseEvents']) == 100
    assert result['reuseSummary']['truncated'] is True
    assert result['reuseSummary']['total'] == 100
    assert result['reuseSummary']['neutral'] == 100
