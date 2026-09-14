import base64
import json
from copy import deepcopy
import pytest
from src.repositories import sqlite_repository as repo
from src.services import v2611_step_workspace_service as steps
from src.services import v269_evaluation_plane_service as evaluation
from tests.test_v269b_evaluation_plane import compiled_review_package, runtime_source

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(repo,'DB_PATH',tmp_path/'steps.sqlite')
    monkeypatch.setattr(repo,'_WAL_INITIALIZED',False)
    package=compiled_review_package()
    with repo.connect() as conn:
        conn.execute('CREATE TABLE v269_system_reviews (task_id TEXT PRIMARY KEY,payload TEXT)')
        conn.execute('INSERT INTO v269_system_reviews VALUES(?,?)',('task',json.dumps(package)));conn.commit()
    return package


def command(view):
    node=view['steps'][0]['node']
    return {'commandId':'cmd-1','graphHash':view['graphHash'],'nodeKey':node['nodeKey'],'nodeHash':node['nodeHash'],'summary':'实际执行记录','attachments':[{'name':'evidence.txt','base64':base64.b64encode(b'evidence').decode()}]}


def test_records_replay_attachments_and_revision(db):
    before=steps.read_workspace('task');body=command(before)
    with repo.connect() as conn:assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2611_step_records'").fetchone()
    after=steps.submit_step('task',body,'operator')
    assert after['allStepsSubmitted'] and after['headHash']!=before['headHash']
    assert steps.submit_step('task',body,'operator')==after
    record=after['steps'][0]['records'][0]
    assert 'base64' not in record['attachments'][0]
    a=steps.attachment('task',record['recordHash'],record['attachments'][0]['contentHash'])
    assert base64.b64decode(a['base64'])==b'evidence'
    with pytest.raises(ValueError,match='COMMAND_REUSED'):steps.submit_step('task',{**body,'summary':'changed'},'operator')
    revised=deepcopy(db);revised['OperationGraph']['graphHash']='revised';revised['OperationGraph']['nodes'][0]['nodeHash']='new-node'
    with repo.connect() as conn:conn.execute('UPDATE v269_system_reviews SET payload=?',(json.dumps(revised),));conn.commit()
    view=steps.read_workspace('task')
    assert not view['allStepsSubmitted'] and view['steps'][0]['records']==after['steps'][0]['records']
    with pytest.raises(ValueError,match='STALE_STEP'):steps.submit_step('task',{**body,'commandId':'new'},'operator')


def test_limits_and_missing_task(db):
    body=command(steps.read_workspace('task'))
    with pytest.raises(ValueError,match='ATTACHMENT_ENCODING'):steps.submit_step('task',{**body,'attachments':[{'name':'a','base64':'!'}]},'operator')
    with pytest.raises(ValueError,match='TASK_GRAPH'):steps.read_workspace('missing')


def test_formula_binding_and_missing_expected_value():
    standard=evaluation.freeze_evaluation_standard()
    changed=deepcopy(standard);changed['contract']['metrics']['agent2.actual_delta']['formula']='actual_value + baseline_value';changed['contractHash']=steps.digest(changed['contract'])
    with pytest.raises(ValueError,match='formula_calculator_binding'):evaluation.evaluate_execution_result(runtime_source(),{},frozen_standard=changed)
    result=evaluation.evaluate_execution_result(runtime_source(),{'actualValue':3,'baselineValue':2,'metricUnit':'ratio'},frozen_standard=standard)
    metrics={m['metricId']:m for m in result['metrics']}
    assert metrics['agent2.prediction_accuracy']['missingReason']=='EXPECTED_VALUE_MISSING'
    assert metrics['agent2.actual_delta']['value']==1


def test_review_return_resubmit_and_stale_receipt(db):
    first=steps.submit_step('task',command(steps.read_workspace('task')),'operator')
    record=first['steps'][0]['records'][0]
    review={'commandId':'review-1','nodeKey':record['nodeKey'],'recordHash':record['recordHash'],'decision':'return','note':'请补充凭证'}
    returned=steps.review_step('task',review,'operator')
    assert returned['steps'][0]['status']=='returned' and not returned['allStepsSubmitted']
    assert steps.review_step('task',review,'operator')==returned
    new=steps.submit_step('task',{**command(returned),'commandId':'cmd-2'},'operator')
    with pytest.raises(ValueError,match='STALE_STEP_REVIEW'):steps.review_step('task',{**review,'commandId':'review-2'},'operator')
    approved=steps.review_step('task',{**review,'commandId':'review-3','decision':'approve','recordHash':new['steps'][0]['records'][-1]['recordHash']},'operator')
    assert approved['steps'][0]['status']=='completed'


def test_immutable_step_content_survives_later_submission(db):
    first=steps.submit_step('task',command(steps.read_workspace('task')),'operator')
    original=steps.read_content('task',first['headHash'])
    later=steps.submit_step('task',{**command(first),'commandId':'next','summary':'补充记录'},'operator')
    assert later['headHash']!=first['headHash']
    assert steps.read_content('task',first['headHash'])==original
    assert steps.digest(original)==first['headHash']
    with pytest.raises(ValueError):steps.read_content('another-task',first['headHash'])


def test_task_submission_keeps_step_evidence_and_fails_closed(db,monkeypatch):
    from src.services import task_submission_review_station_service as station
    view=steps.submit_step('task',command(steps.read_workspace('task')),'operator')
    captured=[]
    monkeypatch.setattr(station,'submit_task_evidence',lambda task_id,body,**kw: captured.append(body))
    station.submit_task('task',{'stepHeadHash':view['headHash'],'formFields':{'note':'preserved'}})
    assert captured[0]['formFields']['stepEvidence']==view
    assert captured[0]['formFields']['note']=='preserved'
    with repo.connect() as conn:
        row=conn.execute('SELECT payload FROM v2611_step_records').fetchone()
        bad=json.loads(row['payload']);bad['summary']='tampered'
        conn.execute('UPDATE v2611_step_records SET payload=?',(json.dumps(bad),));conn.commit()
    with pytest.raises(ValueError,match='STEP_RECORD_HASH_MISMATCH'):
        station.submit_task('task',{'stepHeadHash':view['headHash']})
    assert len(captured)==1


def test_shared_content_is_scoped_to_each_task(db):
    with repo.connect() as conn:
        for task in ('one','two'):
            conn.execute('INSERT INTO v269_system_reviews VALUES(?,?)',(task,json.dumps(db)))
        steps._tables(conn)
        for task in ('one','two'):steps._persist_view(conn,steps._view(conn,task))
        conn.commit()
    old=steps.read_workspace('one')['steps'][0]
    assert old['contentHash']==steps.read_workspace('two')['steps'][0]['contentHash']
    for task in ('one','two'):
        steps.submit_step(task,command(steps.read_workspace(task)),'operator')
        assert steps.read_content(task,old['contentHash'])['status']=='pending'


def test_attachment_lookup_before_submissions_and_tamper(db):
    with pytest.raises(ValueError,match='ATTACHMENT_NOT_FOUND'):steps.attachment('task','missing','missing')
    view=steps.submit_step('task',command(steps.read_workspace('task')),'operator')
    record=view['steps'][0]['records'][0]
    with repo.connect() as conn:
        row=conn.execute('SELECT payload FROM v2611_step_records').fetchone()
        bad=json.loads(row['payload']);bad['attachments'][0]['name']='renamed'
        conn.execute('UPDATE v2611_step_records SET payload=?',(json.dumps(bad),));conn.commit()
    with pytest.raises(ValueError,match='STEP_RECORD_HASH_MISMATCH'):
        steps.attachment('task',record['recordHash'],record['attachments'][0]['contentHash'])


def test_public_evidence_preserves_registered_field_without_private_data():
    from src.services.v26_sop_evidence_service import public_evidence, seal
    receipt=seal({'cards':[{'label':'判断引用','field':'judgementRefs','value':['J1'],
        'nodeKey':'P1','privatePrompt':'must not pass'}]})
    card=public_evidence(receipt,None)['cards'][0]
    assert card['field']=='judgementRefs' and card['value']==['J1']
    assert 'privatePrompt' not in card


def test_experience_overview_reads_without_initializing(db):
    from src.services import v269_experience_store_service as store
    before=store.read_experience_overview()
    assert before['initialized'] is False and before['totalCount']==0
    with repo.connect() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v269b_experience_items'").fetchone()
    store.ensure_experience_store()
    store.import_seed()
    result=store.read_experience_overview()
    assert result['initialized'] and result['totalCount']>0
    assert sum(g['count'] for g in result['groups'])==result['totalCount']
    assert all(g['sourceType']=='seed' for g in result['groups'])
    assert all('payload' not in r for r in result['recent'])
    assert store.digest({k:v for k,v in result.items() if k!='contentHash'})==result['contentHash']
