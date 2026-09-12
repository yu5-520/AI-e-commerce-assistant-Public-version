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


def observation(revisions=('r1',)):
    receipt = {'schema': 'rag.knowledge_retrieval_receipt.v1', 'queryFingerprint': 'q',
               'knowledgeSnapshotHash': 'knowledge', 'indexVersion': 'index-v1',
               'indexManifestHash': 'index-hash', 'retrievalPolicyVersion': 'policy',
               'matchedRevisionIds': list(revisions)}
    receipt_hash = evidence.digest(receipt).removeprefix('sha256:')
    material = {k: v for k, v in receipt.items() if k != 'schema'}
    material.update(schema='rag.retrieval_observation.v1', version='25.13.0', candidateCount=3,
                    eligibleCount=2, matchedCount=len(revisions), filteredLifecycleCount=1,
                    latencyMs=0.0, retrievalReceiptHash=receipt_hash)
    return dict(observation_hash=evidence.digest(material).removeprefix('sha256:'), query_fingerprint='q',
                knowledge_snapshot_hash='knowledge', index_version='index-v1', index_manifest_hash='index-hash',
                retrieval_policy_version='policy', matched_revision_ids_json=json.dumps(list(revisions)),
                retrieval_receipt_hash=receipt_hash, candidate_count=3, eligible_count=2,
                matched_count=len(revisions), filtered_lifecycle_count=1, latency_ms=0.0, recorded_at='2026-09-12')


def test_receipt_and_observation_both_verified_and_membership_required():
    row = observation()
    verified = evidence.verify_retrieval_observation(row, 'r1')
    assert verified['retrievalReceiptVerification'] == 'VERIFIED'
    assert verified['retrievalProof']['selectedShare'] == 0.5
    assert verified['retrievalProof']['latencyMs'] == 0
    assert evidence.verify_retrieval_observation(row, 'other')['retrievalReceiptVerification'] == 'REVISION_NOT_MATCHED'
    row['candidate_count'] = 4
    assert evidence.verify_retrieval_observation(row, 'r1')['retrievalReceiptVerification'] == 'INVALID_EVIDENCE'


def test_retrieval_binding_uses_exact_receipt_and_keeps_missing_distinct(db):
    row = observation()
    db.execute('CREATE TABLE rag_retrieval_observations (' + ','.join(row) + ')')
    db.execute('INSERT INTO rag_retrieval_observations VALUES (' + ','.join('?' for _ in row) + ')', list(row.values()))
    add_event(db, 'r1', 'success', row['retrieval_receipt_hash'])
    add_event(db, 'r1', 'failure', 'missing-receipt')
    result = evidence.read_task_knowledge_audit('t1')
    assert {e['retrievalReceiptVerification'] for e in result['reuseEvents']} == {'VERIFIED', 'NOT_RECORDED'}
    assert result['reuseSummary']['verifiedRetrievalCount'] == 1
    assert result['reuseSummary']['total'] == 2
    assert result['reuseSummary']['successRate'] == 0.5


def test_malformed_observation_is_not_displayed():
    row = observation()
    row['matched_revision_ids_json'] = 'invalid JSON'
    assert evidence.verify_retrieval_observation(row, 'r1')['retrievalReceiptVerification'] == 'INVALID_EVIDENCE'


def index_fixture():
    revision = {'revisionId': 'r1', 'contentHash': 'content-hash'}
    active = [{**revision, 'caseId': 'case1', 'sourceTaskId': 't1'}]
    h = lambda x: evidence.digest(x).removeprefix('sha256:')
    identity = {'knowledgeIndexId': 'competition-knowledge-index', 'indexVersion': 'index1',
                'knowledgeSnapshotHash': h(active), 'sourceRevisionSetHash': h(['r1']),
                'retrievalContractVersion': '25.12.0', 'indexEngine': 'sqlite_structured_v1',
                'activeRevisions': active}
    manifest = {**identity, 'manifestHash': h(identity), 'builtAt': 'unsigned-metadata'}
    row = {'manifest_hash': manifest['manifestHash'], 'manifest_json': json.dumps(manifest)}
    proof = {'indexManifestHash': manifest['manifestHash'], 'indexVersion': 'index1',
             'knowledgeSnapshotHash': identity['knowledgeSnapshotHash'], 'retrievalPolicyVersion': '25.12.0'}
    return revision, manifest, row, proof


def test_index_manifest_recomputes_identity_and_binds_revision_content():
    revision, manifest, row, proof = index_fixture()
    result = evidence.verify_index_manifest(row, proof, revision)
    assert result['indexManifestVerification'] == 'VERIFIED'
    assert result['indexProof']['productionActivationVerified'] is False
    assert result['indexProof']['activeRevisionCount'] == 1
    assert 'activeRevisions' not in result['indexProof']
    bad_revision = {**revision, 'contentHash': 'changed'}
    assert evidence.verify_index_manifest(row, proof, bad_revision)['indexManifestVerification'] == 'REVISION_CONTENT_MISMATCH'
    manifest['activeRevisions'][0]['contentHash'] = 'tampered'
    row['manifest_json'] = json.dumps(manifest)
    assert evidence.verify_index_manifest(row, proof, revision)['indexManifestVerification'] == 'INVALID_EVIDENCE'


def test_index_receipt_binding_cannot_use_another_snapshot():
    revision, _, row, proof = index_fixture()
    proof['knowledgeSnapshotHash'] = 'wrong-snapshot'
    assert evidence.verify_index_manifest(row, proof, revision)['indexManifestVerification'] == 'INVALID_EVIDENCE'


def test_index_head_is_observed_separately_from_historical_manifest(db):
    from copy import deepcopy
    revision, _, row, proof = index_fixture()
    db.execute('CREATE TABLE rag_knowledge_index_manifests(manifest_hash,manifest_json)')
    db.execute('INSERT INTO rag_knowledge_index_manifests VALUES(?,?)', tuple(row.values()))
    db.execute('CREATE TABLE rag_knowledge_index_head(head_key,current_manifest_hash)')
    db.execute('INSERT INTO rag_knowledge_index_head VALUES(?,?)', ('knowledge', row['manifest_hash']))
    tables = {'rag_knowledge_index_manifests', 'rag_knowledge_index_head'}
    def check():
        events = [{'revisionId': 'r1', 'retrievalReceiptVerification': 'VERIFIED', 'retrievalProof': deepcopy(proof)}]
        evidence.bind_index_proofs(db, events, [revision], tables)
        return events[0]['retrievalProof']['indexProof']
    assert check()['headRelation'] == 'CURRENT_DATABASE_HEAD'
    db.execute('UPDATE rag_knowledge_index_head SET current_manifest_hash=?', ('newer-index',))
    assert check()['headRelation'] == 'HISTORICAL_MANIFEST'
    assert check()['productionActivationVerified'] is False
