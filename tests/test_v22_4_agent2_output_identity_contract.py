import json
import pytest
from src.services import agent_token_runtime_v22520_service as token
from src.services.agent2_action_draft_core_v225_service import _build_messages


def entry(n=1):
    return {'package': {'packageId': f'PKG-{n}', 'actionFamily': 'roas_scale',
                        'executionLock': {'evidenceStatus': 'sufficient',
                            'selectedOperatingRoute': 'roas', 'selectedActionFamily': 'roas_scale',
                            'primaryProblemNode': 'roas', 'primaryAction': 'scale',
                            'primaryOwner': 'operator',
                            'primaryExecutionTarget': {'targetType': 'product', 'targetId': f'P{n}'},
                            'decisiveFacts': [{'metric': 'roas', 'value': 4}]}},
            'descriptor': {'packageId': f'PKG-{n}', 'itemExecutionId': f'EXE-{n}',
                           'inputContentHash': f'sha256:{n:064x}',
                           'inputArtifactRef': f'ART-{n}', 'executionHash': f'{n:064x}'},
            'claim': {'claimId': f'CLAIM-{n}'}}


def test_real_prompt_has_one_consistent_transport_contract():
    entries = [entry(1), entry(2)]
    messages, original = _build_messages('DV-1', [e['package'] for e in entries])
    result, payload = token._inject_exact_contract(messages, entries)
    system = result[0]['content']
    assert '每个plan只返回packageId' not in system
    assert '状态和执行身份' not in system
    assert '系统不会替你补写传输身份' in system
    assert payload['outputContract']['requiredPlanFields'] == ['packageId', 'itemExecutionId', 'inputContentHash']
    assert payload['outputContract']['systemFillsMissingIdentity'] is False
    for package, e in zip(payload['packages'], entries):
        for key in ('itemExecutionId', 'inputContentHash'):
            assert package[key] == e['descriptor'][key]
    assert json.loads(result[-1]['content']) == payload
    assert 'outputContract' not in original
    assert 'outputContract' not in json.loads(messages[-1]['content'])


@pytest.mark.parametrize('channel', ['familyPayload', 'missingData', 'conflictReasons', 'rejectedReason'])
def test_all_business_channels_still_require_exact_identity(channel):
    e = entry()
    raw = {'packageId': 'PKG-1', channel: {'detail': 'test'}}
    assert token._raw_match([raw], e)[1] == 'contract_invalid_identity'
    raw.update(itemExecutionId=e['descriptor']['itemExecutionId'], inputContentHash=e['descriptor']['inputContentHash'])
    assert token._raw_match([raw], e)[1] == 'exact'
    raw['inputContentHash'] = entry(2)['descriptor']['inputContentHash']
    assert token._raw_match([raw], e)[1] == 'contract_invalid_identity'


def test_batch_sends_contract_and_never_completes_unidentified_output(monkeypatch):
    e = entry()
    monkeypatch.setattr(token, 'create_batch_manifest', lambda **kw: {'batchManifestRef': 'ART-M', 'batchManifestHash': 'HASH'})
    def gateway(**kw):
        request = json.loads(kw['messages'][-1]['content'])
        assert request['outputContract']['version'] == token.AGENT_TOKEN_RUNTIME_VERSION
        assert kw['prompt_version'] == '22.5.20.1'
        return {'plans': [{'packageId': 'PKG-1', 'familyPayload': {'operations': []}}]}, {}
    monkeypatch.setattr(token, 'call_json_exact_artifact', gateway)
    stored = []
    def store(**kw):
        stored.append(kw['provider_payload'])
        return {'artifactId': 'ART-RAW'}
    monkeypatch.setattr(token, 'store_raw_batch_output', store)
    monkeypatch.setattr(token, 'finalize_batch', lambda **kw: kw)
    def forbidden(*a, **kw):
        pytest.fail('unidentified output was accepted or rebound')
    monkeypatch.setattr(token, 'complete_execution', forbidden)
    monkeypatch.setattr(token, 'store_item_output', forbidden)
    accepted, outcomes, diagnostic, _ = token._execute_batch([e], data_version='DV-1', provider={})
    assert accepted == {}
    assert outcomes == {'EXE-1': 'contract_invalid_identity'}
    assert diagnostic['rawBatchOutputRef'] == 'ART-RAW'
    assert 'itemExecutionId' not in stored[0]['plans'][0]
