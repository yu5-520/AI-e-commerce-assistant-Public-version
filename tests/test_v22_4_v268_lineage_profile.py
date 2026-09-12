"""Run the workflow's actual shell selector against overlapping update paths."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


@pytest.mark.parametrize('changed,event,requested,expected', [
    (['config/v23_registry_runtime.json', 'src/services/v25_agent_input_ingress_service.py'], 'push', 'governance_bootstrap', 'v26_field_authority'),
    (['src/services/v25_agent_input_ingress_service.py'], 'push', 'governance_bootstrap', 'v25_phase3_knowledge_ingress'),
    (['config/v23_registry_runtime.json', 'src/services/v25_agent_input_ingress_service.py'], 'workflow_dispatch', 'v25_phase3_knowledge_ingress', 'v25_phase3_knowledge_ingress'),
    ([], 'push', 'governance_bootstrap', 'governance_bootstrap'),
])
def test_actual_workflow_profile_selection(tmp_path, changed, event, requested, expected):
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/competition-registry-lineage.yml').read_text()
    script = textwrap.dedent(workflow.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
    selector = script[script.index('PROFILE="$LINEAGE_UPDATE_PROFILE"'):]
    # Stop before workspace materialization; execute every profile-selection branch.
    selector = selector[:selector.index('printf \'LINEAGE_UPDATE_PROFILE=')]
    base, target = tmp_path / 'base', tmp_path / 'target'
    base.mkdir(); target.mkdir()
    policies = ['governance/v26-field-authority-update-policy.json',
                'governance/v25/competition-lineage-update-policy-phase3.json']
    for name in changed + policies:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('target')
        if name in policies:
            old = base / name
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_text('target')
    env = dict(os.environ, TARGET_EXTRACTED=str(target), BASE_EXTRACTED=str(base),
               LINEAGE_UPDATE_PROFILE=requested, LINEAGE_UPDATE_POLICY='requested-policy', GITHUB_EVENT_NAME=event)
    result = subprocess.run(['bash', '-eu', '-c', selector + '\nprintf "%s" "$PROFILE"'], env=env,
                            text=True, capture_output=True, check=True)
    assert result.stdout == expected
