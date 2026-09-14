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
