from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF_UPDATE_WORKFLOW = ROOT / ".github/workflows/v23.1-requirement-self-update.yml"
RUNTIME_INPUT_MATERIALIZER = ROOT / ".tmp/v2327/materialize_agent2_runtime_input.py"
REVOKED_PREFLIGHT_MATERIALIZER = ROOT / ".tmp/v2327/materialize_agent2_revoked_preflight.py"


def test_v2327_materializers_are_not_final_runtime_dependencies() -> None:
    """The one-shot code generators must be consumed and removed before merge."""

    assert not RUNTIME_INPUT_MATERIALIZER.exists()
    assert not REVOKED_PREFLIGHT_MATERIALIZER.exists()

    workflow = SELF_UPDATE_WORKFLOW.read_text(encoding="utf-8")
    assert "materialize-v2327-agent2-input-isolation" not in workflow
    assert ".tmp/v2327/materialize_agent2_" not in workflow
