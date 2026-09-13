"""Versioned initialization on the existing Experience Store; no result fabrication.

Registered bundles own immutable configuration/method membership, not a second
knowledge lifecycle. Review, enable, withdraw and Head continue to belong to C.
"""
from copy import deepcopy
import json
from pathlib import Path
from src.repositories import sqlite_repository as repo
from src.services import v269_experience_store_service as store

ROOT=Path(__file__).resolve().parents[2]
BUNDLE_PATH=ROOT/'config/v2610_initialization_bundle.json'


def bundle():
    value=json.loads(BUNDLE_PATH.read_text())
    store._require(value.get('schema')=='business.initialization.bundle.v2610.v1','initialization_schema')
    store._require(value.get('bundleHash')==store.digest({k:v for k,v in value.items() if k!='bundleHash'}),'initialization_bundle_hash')
    store._require(value.get('realSampleCount')==0 and value.get('automaticEnable') is False,'initialization_no_fake_outcomes')
    for method in value['methods']:
        store._require(method['methodHash']==store.digest(method['payload']),'initialization_method_hash')
    return value


def initialize_bundle():
    """Explicit, retryable installation; never run on an Agent read or HTTP GET."""
    value=bundle();store.ensure_experience_store()
    from src.services.v269_promotion_gate_service import ensure_promotion_tables
    ensure_promotion_tables()
    with repo.connect() as conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS v2610_initialization_bundles (
            bundle_hash TEXT PRIMARY KEY, initialization_hash TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS v2610_initialization_methods (
            experience_id TEXT PRIMARY KEY, bundle_hash TEXT NOT NULL,
            payload_hash TEXT NOT NULL, applicability_hash TEXT NOT NULL,
            FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id),
            FOREIGN KEY(bundle_hash) REFERENCES v2610_initialization_bundles(bundle_hash));''')
        conn.execute('INSERT OR IGNORE INTO v2610_initialization_bundles VALUES(?,?,?,?)',
            (value['bundleHash'],value['initializationHash'],repo.dumps(value),store._now()))
        conn.commit()
    ids=[]
    for method in value['methods']:
        payload=method['payload'];scope=payload['scope']
        applicability={**scope,'category':payload['category'],'platform':payload['platform'],
            'agent':payload['agent'],'knowledgeKind':'initialization_method'}
        source={'sourceTaskId':'seed:'+value['initializationHash'],
            'decisionGraphHash':None,'planGraphHash':None,'operationGraphHash':None,
            'graphContractVersion':'26.9.0','evaluationVersion':store.VERSION,
            'evidenceRefs':[r['contentHash'] for r in value['trainingReports']],
            'businessScope':scope,'sourceType':'seed','sourceVersion':value['version']}
        record=store.record_experience(source=source,domain='experience_knowledge',applicability=applicability,payload=payload)
        eid=record['experienceId']
        with repo.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT OR IGNORE INTO v2610_initialization_methods VALUES(?,?,?,?)',
                (eid,value['bundleHash'],store.digest(payload),store.digest(applicability)))
            # One-time handoff to the existing review lifecycle; never revive withdrawn rows.
            conn.execute("UPDATE v269b_experience_items SET lifecycle_status='candidate' WHERE experience_id=? AND lifecycle_status='seed'",(eid,))
            conn.commit()
        ids.append(eid)
    return {'schema':'business.initialization.receipt.v2610.v1','bundleHash':value['bundleHash'],
        'initializationHash':value['initializationHash'],'experienceIds':ids,'realSampleCount':0,
        'automaticEnable':False,'status':'INITIALIZED_REVIEW_REQUIRED'}


def is_registered_method(conn, item):
    """Verify frozen membership and content, so an arbitrary Seed label grants nothing."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2610_initialization_methods'").fetchone():return False
    row=conn.execute('SELECT m.*,b.payload AS bundle_payload FROM v2610_initialization_methods m JOIN v2610_initialization_bundles b ON b.bundle_hash=m.bundle_hash WHERE m.experience_id=?',(item['experience_id'],)).fetchone()
    if not row:return False
    try:
        p=json.loads(item['payload']);a=json.loads(item['applicability']);b=json.loads(row['bundle_payload'])
        return (b['bundleHash']==row['bundle_hash']==store.digest({k:v for k,v in b.items() if k!='bundleHash'})
            and item['source_type']=='seed' and item['domain']=='experience_knowledge'
            and row['payload_hash']==store.digest(p) and row['applicability_hash']==store.digest(a)
            and any(m['methodHash']==row['payload_hash'] and m['payload']==p for m in b['methods'])
            and p['knowledgeType']=='initialization_method' and p['realSampleCount']==0 and p['sampleCount']==0
            and p['businessSuccessClaim'] is False and p['executionPermissionGranted'] is False)
    except (KeyError,ValueError,TypeError):return False


def method_matches(row, agent, query, business_scope):
    payload=json.loads(row['payload'])
    scope=payload.get('scope',{})
    if payload.get('agent')!=agent or not business_scope or any(business_scope.get(k)!=scope.get(k) for k in ('storeId','productId')):return False
    return all(query[k]==payload.get(k) for k in ('category','platform') if k in query)


def initialization_evidence(records):
    """Read frozen retrieval payloads; no current bundle lookup or recomputation."""
    return [deepcopy(r) for r in records if isinstance(r,dict)
        and isinstance(r.get('payload'),dict) and r['payload'].get('knowledgeType')=='initialization_method']
