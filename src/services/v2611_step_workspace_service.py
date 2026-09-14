"""Version-bound step records; graph authority remains the admitted task snapshot."""
import base64
import hashlib
import json
from datetime import datetime, timezone
from src.repositories import sqlite_repository as repo
from src.services.v269_experience_store_service import digest

VERSION='26.11.0'


def _tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS v2611_step_contents (content_hash TEXT NOT NULL, task_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,content_hash))')
    conn.execute('CREATE TABLE IF NOT EXISTS v2611_step_reviews (task_id TEXT NOT NULL, command_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,command_id))')
    conn.execute('CREATE TABLE IF NOT EXISTS v2611_step_records (task_id TEXT NOT NULL, command_id TEXT NOT NULL, record_hash TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,command_id))')


def _graph(conn,task_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v269_system_reviews'").fetchone():raise ValueError('TASK_GRAPH_NOT_AVAILABLE')
    row=conn.execute('SELECT payload FROM v269_system_reviews WHERE task_id=?',(task_id,)).fetchone()
    if not row:raise ValueError('TASK_GRAPH_NOT_AVAILABLE')
    graph=json.loads(row['payload'])['OperationGraph']
    return graph


def _view(conn,task_id):
    graph=_graph(conn,task_id)
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2611_step_records'").fetchone()
    records=[json.loads(r['payload']) for r in conn.execute('SELECT payload FROM v2611_step_records WHERE task_id=? ORDER BY rowid',(task_id,))] if exists else []
    for record in records:
        if record.get('recordHash')!=digest({k:v for k,v in record.items() if k!='recordHash'}):raise ValueError('STEP_RECORD_HASH_MISMATCH')
    public=[{k:v for k,v in r.items() if k!='attachments'}|{'attachments':[{k:v for k,v in a.items() if k!='base64'} for a in r['attachments']]} for r in records]
    has_reviews=conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2611_step_reviews'").fetchone()
    reviews=[json.loads(r['payload']) for r in conn.execute('SELECT payload FROM v2611_step_reviews WHERE task_id=? ORDER BY rowid',(task_id,))] if has_reviews else []
    for r in reviews:
        if r.get('receiptHash')!=digest({k:v for k,v in r.items() if k!='receiptHash'}):raise ValueError('STEP_REVIEW_HASH_MISMATCH')
    steps=[]
    for n in sorted(graph['nodes'],key=lambda n:(n.get('sequence',0),n['nodeKey'])):
        if n['kind']!='OperationStage':continue
        history=[r for r in public if r['nodeKey']==n['nodeKey']]
        current=[r for r in history if r['graphHash']==graph['graphHash'] and r['nodeHash']==n['nodeHash']]
        related=[r for r in reviews if current and r['recordHash']==current[-1]['recordHash']]
        status=('completed' if related[-1]['decision']=='approve' else 'returned') if related else ('submitted' if current else 'pending')
        body={'node':n,'records':history,'reviews':[r for r in reviews if r['nodeKey']==n['nodeKey']],'status':status}
        steps.append({**body,'contentHash':digest(body)})
    body={'version':VERSION,'taskId':task_id,'graphHash':graph['graphHash'],'steps':steps,'allStepsSubmitted':bool(steps) and all(s['status'] in ('submitted','completed') for s in steps)}
    return {**body,'headHash':digest(body)}


def read_workspace(task_id):
    with repo.connect() as conn:return _view(conn,task_id)


def submit_step(task_id,body,actor):
    command=body.get('commandId');text=body.get('summary','')
    if not isinstance(command,str) or not 1<=len(command)<=128:raise ValueError('COMMAND_ID_REQUIRED')
    if not isinstance(text,str) or not 1<=len(text.strip())<=10000:raise ValueError('STEP_RESULT_REQUIRED')
    files=body.get('attachments',[])
    if not isinstance(files,list) or len(files)>5:raise ValueError('ATTACHMENT_LIMIT')
    attachments=[];total=0
    for a in files:
        if not isinstance(a,dict):raise ValueError('ATTACHMENT_ENCODING')
        name=a.get('name');encoded=a.get('base64','')
        if not isinstance(name,str) or not 1<=len(name)<=255 or '/' in name or '\\' in name:raise ValueError('ATTACHMENT_NAME')
        if not isinstance(encoded,str) or len(encoded)>2800000:raise ValueError('ATTACHMENT_LIMIT')
        try:raw=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError):raise ValueError('ATTACHMENT_ENCODING')
        total+=len(raw)
        if total>2*1024*1024:raise ValueError('ATTACHMENT_LIMIT')
        attachments.append({'name':name,'size':len(raw),'contentHash':'sha256:'+hashlib.sha256(raw).hexdigest(),'base64':encoded})
    material={'taskId':task_id,'commandId':command,'graphHash':body.get('graphHash'),'nodeKey':body.get('nodeKey'),'nodeHash':body.get('nodeHash'),'summary':text.strip(),'actor':actor,'attachments':attachments}
    identity=digest(material)
    with repo.connect() as conn:
        _tables(conn);conn.execute('BEGIN IMMEDIATE')
        graph=_graph(conn,task_id)
        old=conn.execute('SELECT payload FROM v2611_step_records WHERE task_id=? AND command_id=?',(task_id,command)).fetchone()
        if old:
            if json.loads(old['payload'])['identityHash']!=identity:raise ValueError('COMMAND_REUSED_WITH_DIFFERENT_CONTENT')
        else:
            nodes={n['nodeKey']:n for n in graph['nodes'] if n['kind']=='OperationStage'}
            node=nodes.get(body.get('nodeKey'))
            if graph['graphHash']!=body.get('graphHash') or not node or node['nodeHash']!=body.get('nodeHash'):raise ValueError('STALE_STEP_VERSION')
            record={**material,'identityHash':identity,'submittedAt':datetime.now(timezone.utc).isoformat(),'reviewStatus':'NOT_REVIEWED'}
            record['recordHash']=digest(record)
            conn.execute('INSERT INTO v2611_step_records VALUES(?,?,?,?)',(task_id,command,record['recordHash'],repo.dumps(record)))
        view=_view(conn,task_id);_persist_view(conn,view);conn.commit();return view


def attachment(task_id,record_hash,content_hash):
    with repo.connect() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2611_step_records'").fetchone():raise ValueError('ATTACHMENT_NOT_FOUND')
        row=conn.execute('SELECT payload FROM v2611_step_records WHERE task_id=? AND record_hash=?',(task_id,record_hash)).fetchone()
    if row:
        record=json.loads(row['payload'])
        if record.get('recordHash')!=digest({k:v for k,v in record.items() if k!='recordHash'}):raise ValueError('STEP_RECORD_HASH_MISMATCH')
        for a in record['attachments']:
            if a['contentHash']==content_hash:
                if 'sha256:'+hashlib.sha256(base64.b64decode(a['base64'])).hexdigest()!=content_hash:raise ValueError('ATTACHMENT_HASH_MISMATCH')
                return a
    raise ValueError('ATTACHMENT_NOT_FOUND')



def review_step(task_id,body,actor):
    command=body.get('commandId');decision=body.get('decision');note=body.get('note','')
    if not isinstance(command,str) or not 1<=len(command)<=128:raise ValueError('COMMAND_ID_REQUIRED')
    if decision not in ('approve','return') or not isinstance(note,str) or not 1<=len(note.strip())<=10000:raise ValueError('REVIEW_DECISION_AND_NOTE_REQUIRED')
    material={'taskId':task_id,'commandId':command,'recordHash':body.get('recordHash'),'nodeKey':body.get('nodeKey'),'decision':decision,'note':note.strip(),'actor':actor}
    with repo.connect() as conn:
        _tables(conn);conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT payload FROM v2611_step_reviews WHERE task_id=? AND command_id=?',(task_id,command)).fetchone()
        if old:
            if json.loads(old['payload'])['identityHash']!=digest(material):raise ValueError('COMMAND_REUSED_WITH_DIFFERENT_CONTENT')
        else:
            view=_view(conn,task_id);step=next((x for x in view['steps'] if x['node']['nodeKey']==material['nodeKey']),None)
            current=[r for r in step['records'] if r['graphHash']==view['graphHash'] and r['nodeHash']==step['node']['nodeHash']] if step else []
            if not current or current[-1]['recordHash']!=material['recordHash']:raise ValueError('STALE_STEP_REVIEW')
            receipt={**material,'identityHash':digest(material),'reviewedAt':datetime.now(timezone.utc).isoformat()}
            receipt['receiptHash']=digest(receipt)
            conn.execute('INSERT INTO v2611_step_reviews VALUES(?,?,?)',(task_id,command,repo.dumps(receipt)))
        result=_view(conn,task_id);_persist_view(conn,result);conn.commit();return result



def _persist_view(conn,view):
    for step in view['steps']:
        conn.execute('INSERT OR IGNORE INTO v2611_step_contents VALUES(?,?,?)',(step['contentHash'],view['taskId'],repo.dumps({k:v for k,v in step.items() if k!='contentHash'})))
    conn.execute('INSERT OR IGNORE INTO v2611_step_contents VALUES(?,?,?)',(view['headHash'],view['taskId'],repo.dumps({k:v for k,v in view.items() if k!='headHash'})))


def read_content(task_id,content_hash):
    with repo.connect() as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2611_step_contents'").fetchone()
        row=conn.execute('SELECT payload FROM v2611_step_contents WHERE task_id=? AND content_hash=?',(task_id,content_hash)).fetchone() if exists else None
        if row:
            payload=json.loads(row['payload'])
            if digest(payload)!=content_hash:raise ValueError('STEP_CONTENT_HASH_MISMATCH')
            return payload
        view=_view(conn,task_id)
        if view['headHash']==content_hash:return {k:v for k,v in view.items() if k!='headHash'}
        for step in view['steps']:
            if step['contentHash']==content_hash:return {k:v for k,v in step.items() if k!='contentHash'}
    raise ValueError('STEP_CONTENT_NOT_FOUND')
