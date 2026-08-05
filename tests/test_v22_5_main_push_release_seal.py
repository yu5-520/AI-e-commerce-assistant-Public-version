from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-hash-seal.yml"
TRANSPORT_WRAPPER = ROOT / "scripts" / "deploy_github_artifact.sh"
TRANSPORT_CORE = ROOT / "src" / "deployment" / "deploy_github_artifact_core_v22516.sh"


def test_pull_request_is_validation_only() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "pr-validation:" in source
    assert "if: github.event_name == 'pull_request'" in source
    assert "ref: ${{ github.event.pull_request.head.sha }}" in source
    pr_section, seal_section = source.split("  seal-release:", 1)
    assert "actions/upload-artifact" not in pr_section
    assert "actions/upload-artifact@v4" in seal_section


def test_release_artifact_is_main_push_only() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in source
    assert 'test "$GITHUB_EVENT_NAME" = "push"' in source
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in source
    assert 'test "$GITHUB_REF_NAME" = "main"' in source
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in source
    assert "'releaseEvent': os.environ['GITHUB_EVENT_NAME']" in source
    assert "'releaseBranch': os.environ['GITHUB_REF_NAME']" in source
    assert "'deployableArtifact': True" in source
    assert "'pullRequestMergeArtifact': False" in source
    assert "name: release-${{ github.sha }}" in source


def test_transport_requires_formal_main_push_identity() -> None:
    wrapper = TRANSPORT_WRAPPER.read_text(encoding="utf-8")
    source = TRANSPORT_CORE.read_text(encoding="utf-8")
    assert TRANSPORT_CORE.relative_to(ROOT).as_posix() in wrapper
    assert "AI_RELEASE_SOURCE_COMMIT" in wrapper
    markers = (
        'FORMAL_BRANCH="main"',
        'FORMAL_WORKFLOW_PATH=".github/workflows/release-hash-seal.yml"',
        'Only main push artifacts are deployable',
        'expected_name = "release-" + requested_sha',
        'RUN_URL="$API_BASE/repos/$REPOSITORY/actions/runs/$RUN_ID"',
        '"event": "push"',
        '"status": "completed"',
        '"conclusion": "success"',
        '"head_branch": branch',
        '"head_sha": requested_sha',
        '"path": expected_path',
        'releaseEvent is not push',
        'releaseRef is not refs/heads/main',
        'releaseBranch is not main',
        'pullRequestMergeArtifact is not false',
        'Artifact workflow run is not a successful push-to-main release seal',
        'Sealed bundle is not a formal main-push release',
    )
    for marker in markers:
        assert marker in source, marker
