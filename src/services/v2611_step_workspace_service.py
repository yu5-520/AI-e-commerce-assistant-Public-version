"""Version-bound step records; graph authority remains the admitted task snapshot."""
import base64
import hashlib
import json
from datetime import datetime, timezone
from src.repositories import sqlite_repository as repo
from src.services.v269_experience_store_service import digest

VERSION='26.11.0'


def _tables(conn):
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
    steps=[]
    for n in sorted(graph['nodes'],key=lambda n:(n.get('sequence',0),n['nodeKey'])):
        if n['kind']!='OperationStage':continue
        history=[r for r in public if r['nodeKey']==n['nodeKey']]
        current=[r for r in history if r['graphHash']==graph['graphHash'] and r['nodeHash']==n['nodeHash']]
        body={'node':n,'records':history,'status':'submitted' if current else 'pending'}
        steps.append({**body,'contentHash':digest(body)})
    body={'version':VERSION,'taskId':task_id,'graphHash':graph['graphHash'],'steps':steps,'allStepsSubmitted':bool(steps) and all(s['status']=='submitted' for s in steps)}
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
        view=_view(conn,task_id);conn.commit();return view


def attachment(task_id,record_hash,content_hash):
    with repo.connect() as conn:
        row=conn.execute('SELECT payload FROM v2611_step_records WHERE task_id=? AND record_hash=?',(task_id,record_hash)).fetchone()
    if row:
        for a in json.loads(row['payload'])['attachments']:
            if a['contentHash']==content_hash:
                if 'sha256:'+hashlib.sha256(base64.b64decode(a['base64'])).hexdigest()!=content_hash:raise ValueError('ATTACHMENT_HASH_MISMATCH')
                return a
    raise ValueError('ATTACHMENT_NOT_FOUND')
