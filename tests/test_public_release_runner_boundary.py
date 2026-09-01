from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
PULL_REQUEST_TRIGGER = re.compile(r"(?m)^  pull_request:\s*(?:#.*)?$")


def _workflow_texts():
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        yield path, path.read_text(encoding="utf-8")
    for path in sorted(WORKFLOW_DIR.glob("*.yaml")):
        yield path, path.read_text(encoding="utf-8")


def _targets_self_hosted_runner(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("runs-on:"):
            continue
        indent = len(line) - len(stripped)
        value = stripped.split(":", 1)[1].strip()
        if "self-hosted" in value:
            return True
        if value:
            continue
        for nested in lines[index + 1 :]:
            nested_stripped = nested.lstrip()
            if not nested_stripped or nested_stripped.startswith("#"):
                continue
            nested_indent = len(nested) - len(nested_stripped)
            if nested_indent <= indent:
                break
            if "self-hosted" in nested_stripped:
                return True
    return False


def test_public_pr_never_targets_self_hosted_runner():
    violations = []
    for path, text in _workflow_texts():
        if _targets_self_hosted_runner(text) and PULL_REQUEST_TRIGGER.search(text):
            violations.append(path.relative_to(ROOT).as_posix())
    assert not violations, (
        "public pull_request workflows must never target a self-hosted runner: "
        + ", ".join(violations)
    )


def test_qwen_live_evidence_is_trusted_trigger_only():
    path = WORKFLOW_DIR / "competition-qwen-live-evidence.yml"
    text = path.read_text(encoding="utf-8")
    assert _targets_self_hosted_runner(text)
    assert not PULL_REQUEST_TRIGGER.search(text)
    assert "push:" in text
    assert "branches: [main]" in text
    assert "workflow_dispatch:" in text


def test_self_hosted_workflows_remain_read_only_at_github_permission_layer():
    violations = []
    for path, text in _workflow_texts():
        if not _targets_self_hosted_runner(text):
            continue
        if "permissions:\n  contents: read" not in text:
            violations.append(path.relative_to(ROOT).as_posix())
    assert not violations, (
        "self-hosted workflows must keep explicit contents: read permission: "
        + ", ".join(violations)
    )
