"""Adversarial V26.7 cases; no provider/network needed."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
import pytest

ROOT = Path(__file__).resolve().parents[1]
# Mirror existing V26 pure-module gate; avoid initializing unrelated live services.
for name, path in [('src', ROOT/'src'), ('src.services', ROOT/'src/services')]:
    if name not in sys.modules:
        module = types.ModuleType(name); module.__path__ = [str(path)]; sys.modules[name] = module

def load(name):
    fullname = 'src.services.' + name
    if fullname in sys.modules: return sys.modules[fullname]
    spec = importlib.util.spec_from_file_location(fullname, ROOT/'src/services'/f'{name}.py')
    module = importlib.util.module_from_spec(spec); sys.modules[fullname] = module; spec.loader.exec_module(module)
    return module

field = load('v26_field_authority_contract_service')
evidence = load('v26_sop_evidence_service')
lineage = load('v26_node_edge_lineage_service')
accept = load('v26_revision_acceptance_service')

@pytest.mark.parametrize('header,value', [('plan.daily_budget', float('inf')), ('plan.target_roas', float('nan')), ('plan.parameter_pack', {'budget': float('nan')})])
def test_nonfinite_plan_rejected(header, value):
    with pytest.raises(field.FieldAuthorityViolation): field.V26FieldAuthorityContract().assert_write('agent2', header, value)


def test_cycle_and_budget_fail_closed():
    nodes = [{'nodeKey': 'a'}, {'nodeKey': 'b'}]
    with pytest.raises(ValueError, match='cycle'):
        lineage._validate_graph_budget(nodes, [{'sourceKey': 'a', 'targetKey': 'b', 'relation':'depends_on'}, {'sourceKey':'b','targetKey':'a','relation':'depends_on'}])
    with pytest.raises(ValueError, match='budget'):
        lineage._validate_graph_budget([{'nodeKey':str(i)} for i in range(65)], [])


def test_semantic_identity_ignores_execution_but_not_business():
    a = {'graphHash':'a','sourceExecutionIdentity':{'dataVersion':'1','storeId':'s'},'authorityHeaders':{'plan.daily_budget':10}}
    b = deepcopy(a); b['sourceExecutionIdentity']['dataVersion']='2'; b['graphHash']='b'
    assert lineage.semantic_graph_identity(a) == lineage.semantic_graph_identity(b)
    b['authorityHeaders']['plan.daily_budget']=11
    assert lineage.semantic_graph_identity(a) != lineage.semantic_graph_identity(b)


def test_metric_evidence_zero_and_tamper():
    p={'recentSnapshots':[{'snapshotId':'a','metrics':{'roas':0}}, {'snapshotId':'b','metrics':{'roas':2}}]}
    receipt = evidence.freeze_metric_evidence(p)
    assert evidence.verified(receipt)
    assert receipt['cards'][-1]['formula']=='current - previous'
    assert receipt['cards'][-1]['inputs'][0]['value']==0
    assert evidence.public_evidence({}, receipt)['missing']==['decisions:not_recorded']
    receipt['cards'][-1]['value']=999
    assert evidence.public_evidence({}, receipt)['cards']==[]


def test_missing_history_never_fabricates_rationale():
    result=evidence.public_evidence(None,None)
    assert result['cards']==[] and result['privateReasoningExposed'] is False


def test_graph_label_cannot_replace_content():
    graph={'nodes': [], 'edges': []};graph['graphHash']=evidence.digest(graph)
    accept.verify_graph(graph)
    graph['nodes']=[{'nodeKey':'forged','nodeHash':'sha256:fake'}]
    with pytest.raises(ValueError, match='graph_content'): accept.verify_graph(graph)


def test_public_dto_preserves_evidence_without_prompt():
    dto=load('public_task_dto_service')
    receipt=evidence.seal({'cards':[{'label':'预算','kind':'PLAN','value':0,'prompt':'secret'}], 'prompt':'secret'})
    response=dto.project_task_detail({'taskId':'t','taskPlan':{'sopDecisionEvidence':receipt}})
    assert response['sopEvidence']['cards'][0]['value']==0
    assert 'secret' not in str(response['sopEvidence'])


def test_task_scoped_rag_audit_and_corrupt_event(monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE rag_knowledge_revisions(revision_id,content_hash,content_json,source_task_id,source_recap_hash,previous_revision_id,created_at);
    CREATE TABLE rag_knowledge_review_events(event_hash,revision_id,decision,reviewer_id,reason,before_hash,after_hash,migration,created_at);''')
    import json
    content={'sourceTaskId':'t','summary':'approved knowledge'}
    db.execute('INSERT INTO rag_knowledge_revisions VALUES(?,?,?,?,?,?,?)',('r',evidence.digest(content),json.dumps(content),'t','recap',None,'2026-09-11'))
    db.execute('INSERT INTO rag_knowledge_revisions VALUES(?,?,?,?,?,?,?)',('other',evidence.digest(content),json.dumps(content),'other-task','recap',None,'2026-09-11'))
    db.execute('INSERT INTO rag_knowledge_review_events VALUES(?,?,?,?,?,?,?,?,?)',('forged','r','approved','u','ok','a','b',0,'2026-09-11'))
    @contextmanager
    def connect(): yield db
    repo=types.ModuleType('src.repositories.sqlite_repository'); repo.connect=connect
    monkeypatch.setitem(sys.modules, 'src.repositories.sqlite_repository', repo)
    result=evidence.read_task_knowledge_audit('t')
    assert [r['revisionId'] for r in result['revisions']]==['r']
    assert result['events']==[] and result['status']=='INVALID_EVIDENCE'
