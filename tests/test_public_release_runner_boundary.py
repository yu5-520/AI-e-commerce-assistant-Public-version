from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _workflow_texts():
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        yield path, path.read_text(encoding="utf-8")
    for path in sorted(WORKFLOW_DIR.glob("*.yaml")):
        yield path, path.read_text(encoding="utf-8")


def test_public_pr_never_targets_self_hosted_runner():
    violations = []
    for path, text in _workflow_texts():
        if "self-hosted" in text and "pull_request:" in text:
            violations.append(path.relative_to(ROOT).as_posix())
    assert not violations, (
        "public pull_request workflows must never target a self-hosted runner: "
        + ", ".join(violations)
    )


def test_qwen_live_evidence_is_trusted_trigger_only():
    path = WORKFLOW_DIR / "competition-qwen-live-evidence.yml"
    text = path.read_text(encoding="utf-8")
    assert "runs-on: self-hosted" in text
    assert "pull_request:" not in text
    assert "push:" in text
    assert "branches: [main]" in text
    assert "workflow_dispatch:" in text


def test_self_hosted_workflows_remain_read_only_at_github_permission_layer():
    violations = []
    for path, text in _workflow_texts():
        if "self-hosted" not in text:
            continue
        if "permissions:\n  contents: read" not in text:
            violations.append(path.relative_to(ROOT).as_posix())
    assert not violations, (
        "self-hosted workflows must keep explicit contents: read permission: "
        + ", ".join(violations)
    )
