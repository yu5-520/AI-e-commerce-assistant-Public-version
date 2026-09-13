"""Initialization provenance, holdout isolation and the real C review/retrieval path."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from scripts.compile_v2610_initialization import compile_bundle
from src.repositories import sqlite_repository as repo
from src.services import v2610_initialization_service as init
from src.services import v269_promotion_gate_service as promotion
from src.services import v269_experience_retrieval_service as retrieval
from src.services import v269_experience_store_service as store

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(repo,'DB_PATH',tmp_path/'test.sqlite')
    monkeypatch.setattr(repo,'_WAL_INITIALIZED',False)
    return tmp_path


def test_bundle_reproduces_source_and_keeps_holdout_out_of_training():
    profile=json.loads((ROOT/'config/v2610_initialization_profile.json').read_text())
    scenario=json.loads((ROOT/profile['sourcePath']).read_text())
    a=compile_bundle(scenario,profile)
    assert a==init.bundle()
    scenario['reports'][2]['rows'][0]['roi']=900
    b=compile_bundle(scenario,profile)
    assert a['bundleHash']!=b['bundleHash']
    assert a['initializationHash']==b['initializationHash']
    assert a['methods']==b['methods']
    assert a['realSampleCount']==0
    unit=a['operatingUnits'][0]
    assert 'roiDefinitionMismatch' in unit
    assert unit['reportedRoi']!=unit['derived']['revenue_ad_spend_ratio']['value']
    assert unit['baseline']['roi']['absoluteDelta']==unit['baseline']['roi']['second']-unit['baseline']['roi']['first']


def test_initialization_is_idempotent_and_does_not_create_outcomes(db):
    first=init.initialize_bundle();second=init.initialize_bundle()
    assert first==second
    with repo.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM v269b_experience_items').fetchone()[0]==len(init.bundle()['methods'])
        assert conn.execute('SELECT COUNT(*) FROM v269b_evaluation_results').fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM v269b_experience_items WHERE lifecycle_status='enabled'").fetchone()[0]==0
    assert all(store.experience_view(experience_id=e)['payload']['sampleCount']==0 for e in first['experienceIds'])


def test_review_enable_retrieve_withdraw_and_replay_use_existing_authority(db):
    receipt=init.initialize_bundle()
    ids=receipt['experienceIds'][:3]
    for eid in ids:
        item=store.experience_view(experience_id=eid)
        agent=item['payload']['agent'];scope=item['payload']['scope']
        query={'category':item['payload']['category']} if agent!='agent3' else {'platform':item['payload']['platform']}
        args={'business_scope':scope}
        assert retrieval.retrieve_experience(agent,query,**args)['matchCount']==0
        gate=promotion.evaluate_promotion_gate(eid)
        assert gate['approvedForPromotion'] and gate['knowledgeKind']=='initialization_method'
        assert gate['sampleCount']==0 and not gate['historicalOutcomeProof']
        promotion.review_candidate(eid,reviewer_id='test-reviewer',decision='approve',rationale='测试审核方法口径')
        assert retrieval.retrieve_experience(agent,query,**args)['matchCount']==0
        promotion.enable_experience(eid,operator_id='test-operator',explicit_operator_intent=True)
        result=retrieval.retrieve_experience(agent,query,**args)
        assert result['resultIds']==[eid]
        assert result['results'][0]['officialEligible']
        assert result['results'][0]['sourceType']=='seed'
        assert retrieval.retrieve_experience(agent,query,business_scope={**scope,'storeId':'other'})['matchCount']==0
        assert retrieval.retrieve_experience(agent,query)['matchCount']==0
        head=result['knowledgeHead']
        promotion.disable_experience(eid,operator_id='test-operator',reason='测试撤回',explicit_operator_intent=True)
        init.initialize_bundle()
        result=retrieval.retrieve_experience(agent,query,**args)
        assert result['matchCount']==0 and result['knowledgeHead']!=head
        assert store.experience_view(experience_id=eid)['lifecycleStatus']=='disabled'


def test_relabelled_or_modified_seed_cannot_be_enabled(db):
    receipt=init.initialize_bundle();eid=receipt['experienceIds'][0]
    with repo.connect() as conn:
        p=json.loads(conn.execute('SELECT payload FROM v269b_experience_items WHERE experience_id=?',(eid,)).fetchone()[0])
        p['sampleCount']=99
        conn.execute('UPDATE v269b_experience_items SET payload=? WHERE experience_id=?',(json.dumps(p),eid));conn.commit()
    gate=promotion.evaluate_promotion_gate(eid)
    assert not gate['approvedForPromotion']
    assert 'SOURCE_TYPE_NOT_RUNTIME' in gate['failures']


def test_initialization_reference_is_frozen_in_sop_and_cache_head(db):
    from src.services import v269_input_migration_service as migration
    receipt=init.initialize_bundle();eid=receipt['experienceIds'][0]
    item=store.experience_view(experience_id=eid);payload=item['payload']
    promotion.review_candidate(eid,reviewer_id='reviewer',decision='approve',rationale='method checked')
    promotion.enable_experience(eid,operator_id='operator',explicit_operator_intent=True)
    base={'retrievalPolicyHash':'policy','records':[]};base={**base,'headHash':store.digest(base)}
    source={**payload['scope'],'BusinessFacts':{'category':payload['category']}}
    # Use a deterministic field query while exercising the existing context rebind.
    from unittest.mock import patch
    with patch.object(retrieval,'derive_queries',return_value=[{'category':payload['category']}]):
        context=retrieval.attach_official_experience_context('agent1',source,base)
        assert context==retrieval.attach_official_experience_context('agent1',source,context)
        evidence=migration.freeze_graph_evidence({'knowledgeContext':context},{})
        assert evidence['cards'][0]['kind']=='PRESET'
        frozen=deepcopy(evidence)
        promotion.disable_experience(eid,operator_id='operator',reason='withdraw',explicit_operator_intent=True)
        after=retrieval.attach_official_experience_context('agent1',source,context)
        assert after['headHash']!=context['headHash']
        assert evidence==frozen


def test_parameter_override_is_scoped_and_cannot_change_formula_authority():
    profile=json.loads((ROOT/'config/v2610_initialization_profile.json').read_text())
    scenario=json.loads((ROOT/profile['sourcePath']).read_text())
    profile['parameterOverrides']['store']={'TB-SH-001':{'minimumRelativeThreshold':0.9}}
    b=compile_bundle(scenario,profile)
    for unit in b['operatingUnits']:
        expected=0.9 if unit['storeId']=='TB-SH-001' else profile['minimumRelativeThreshold']
        assert unit['baseline']['roi']['minimumRelativeThreshold']==expected
    profile['parameterOverrides']['store']['TB-SH-001']['formula']='invented'
    with pytest.raises(ValueError,match='parameter_override_not_registered'):compile_bundle(scenario,profile)


def test_exact_runtime_contains_registered_rag_resources_and_can_install(db, monkeypatch):
    """Exercise file reads from the actual lineage selection, not the source checkout."""
    import shutil
    monkeypatch.syspath_prepend(str(ROOT/"scripts"))
    from scripts.compile_competition_lineage import compile_lineage
    scope=json.loads((ROOT/'config/competition_runtime_scope.json').read_text())
    result=compile_lineage(ROOT,scope=scope,
        source_identity=json.loads((ROOT/'config/competition_source_identity.json').read_text()),
        source_commit='test-initialization-resource-closure')
    assert result['verificationReport']['verified'], result['verificationReport']['findings']
    paths={entry['path'] for entry in result['runtimeFiles']}
    registry=json.loads((ROOT/'config/v23_registry_runtime.json').read_text())
    required={p for name in ('experience_store','experience_promotion')
        for p in registry['modules'][name]['implementationPaths'] if p.startswith('rag/')}
    assert required <= paths
    assert not any(p.startswith(('logs/','data/')) for p in paths)
    app=db/'app'
    for path in required:
        target=app/path;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/path,target)
    for module,names in ((store,('MANIFEST_PATH','SCHEMA_PATH','MIGRATION_PATH','SEED_PATH')),
                         (promotion,('MANIFEST_PATH','MIGRATION_PATH'))):
        for name in names:
            monkeypatch.setattr(module,name,app/getattr(module,name).relative_to(ROOT))
    assert store.manifest()['version']==store.VERSION
    store.ensure_experience_store()
    promotion.ensure_promotion_tables()
    assert init.initialize_bundle()['automaticEnable'] is False


def test_graph_e2e_probe_requires_active_contract_and_preserves_invariants(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    from scripts import run_competition_three_report_e2e_v269 as e2e
    probe=e2e._graph_probe()
    assert probe['verified'] and probe['rolloutStatus']=='active'
    assert all(probe['assertions'].values())
    original=e2e.graphs.contract
    monkeypatch.setattr(e2e.graphs,'contract',lambda:{**original(),'rolloutStatus':'candidate_not_activated'})
    assert not e2e._graph_probe()['verified']


def test_three_sheet_baselines_parameters_and_holdout():
    from scripts.compile_v2610_initialization import digest
    b=init.bundle();p=deepcopy(b['profile'])
    s=json.loads((ROOT/p['sourcePath']).read_text())
    assert len(b['supplementaryBaselines']['店铺经营汇总']['entries'])==3
    assert len(b['supplementaryBaselines']['流量来源明细']['entries'])==150
    p['parameterOverrides']['enterprise']={p['enterpriseId']:{'reviewWindowDays':14}}
    p['parameterOverrides']['store']={'TB-SH-001':{'reviewWindowDays':3,'targetRelativeImprovement':0.1}}
    updated=compile_bundle(s,p)
    for u in updated['operatingUnits']:
        assert u['parameters']['reviewWindowDays']==(3 if u['storeId']=='TB-SH-001' else 14)
    first_hash=updated['initializationHash']
    sh='店铺经营汇总';r=s['reports'][2];headers=r['sheetEvidence'][sh]['headers']
    r['sheetData'][sh][0][headers.index('支付金额')]+=100
    r['sheetEvidence'][sh]['contentHash']=digest([headers,*r['sheetData'][sh]])
    after=compile_bundle(s,p)
    assert after['initializationHash']==first_hash
    assert after['supplementaryBaselines']!=updated['supplementaryBaselines']


def test_frozen_evaluation_survives_current_configuration_change(db,monkeypatch):
    from src.services import v269_evaluation_plane_service as evaluation
    from tests.test_v269b_evaluation_plane import runtime_source
    standard=evaluation.freeze_evaluation_standard()
    inputs={'baselineValue':2.0,'expectedValue':2.4,'actualValue':2.3,'metricUnit':'ratio'}
    v=evaluation.evaluate_execution_result(runtime_source(),inputs,persist=True,frozen_standard=standard)
    monkeypatch.setattr(evaluation,'contract',lambda: (_ for _ in ()).throw(AssertionError('current config read')))
    assert evaluation.evaluate_execution_result(runtime_source(),inputs,frozen_standard=standard)['vectorHash']==v['vectorHash']
    evidence=evaluation.read_task_evaluation_evidence('TASK-EVAL-1')
    assert evidence['status']=='RECORDED' and evidence['contractHash']==standard['contractHash']
    assert all(i['standardSource']=='frozen' for i in evidence['items'])
    corrupt=deepcopy(standard);corrupt['contract']['epsilon']=1
    with pytest.raises(ValueError,match='frozen_standard_hash'):
        evaluation.evaluate_execution_result(runtime_source(),inputs,frozen_standard=corrupt)


def test_initialized_methods_graphs_evaluation_promotion_next_retrieval(db):
    """Synthetic execution stays inside this isolated DB; use production B/C APIs."""
    from tests.test_v269b_evaluation_plane import compiled_review_package
    from src.services import v269_evaluation_plane_service as evaluation
    receipt=init.initialize_bundle()
    for eid in receipt['experienceIds'][:3]:
        promotion.review_candidate(eid,reviewer_id='fixture-reviewer',decision='approve',rationale='synthetic method review')
        promotion.enable_experience(eid,operator_id='fixture-operator',explicit_operator_intent=True)
        payload=store.experience_view(experience_id=eid)['payload']
        assert eid in retrieval.retrieve_experience(payload['agent'],({'platform':payload['platform']} if payload['agent']=='agent3' else {'category':payload['category']}),business_scope=payload['scope'])['resultIds']
    package=compiled_review_package()
    package['evaluationStandard']=evaluation.freeze_evaluation_standard()
    recorded=evaluation.record_system_review_candidate(package,
        target_facts={'roas':{'value':2.3,'unit':'ratio','sourceRef':'fixture:simulated-target'}},
        target_source_content_hash='sha256:'+'9'*64,
        review_receipt={'receiptHash':'sha256:'+'8'*64},review_status='SETTLED')
    assert recorded['strategyExperienceIds']
    eid=recorded['strategyExperienceIds'][0]
    item=store.experience_view(experience_id=eid)
    query={'decisionAction':item['payload']['decisionAction'],'strategyType':item['payload']['strategyType']}
    assert eid not in retrieval.retrieve_experience('agent2',query)['resultIds']
    promotion.review_candidate(eid,reviewer_id='fixture-reviewer',decision='approve',rationale='simulated outcome reviewed')
    promotion.enable_experience(eid,operator_id='fixture-operator',explicit_operator_intent=True)
    result=retrieval.retrieve_experience('agent2',query)
    assert eid in result['resultIds']
    head=result['knowledgeHead']
    promotion.disable_experience(eid,operator_id='fixture-operator',reason='test withdrawal',explicit_operator_intent=True)
    assert retrieval.retrieve_experience('agent2',query)['knowledgeHead']!=head
