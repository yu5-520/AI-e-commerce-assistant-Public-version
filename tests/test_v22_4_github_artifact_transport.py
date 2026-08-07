from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "deploy_github_artifact.sh"
CORE = ROOT / "src" / "deployment" / "deploy_github_artifact_core_v22516.sh"


def _wrapper_source() -> str:
    return WRAPPER.read_text(encoding="utf-8")


def _core_source() -> str:
    return CORE.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    marker = f"{name}() {{"
    assert marker in source
    remainder = source.split(marker, 1)[1]
    return remainder.split("\n}", 1)[0]


def test_github_artifact_transport_has_valid_bash_syntax() -> None:
    for path in (WRAPPER, CORE):
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_github_artifact_transport_never_deploys_a_mutable_git_tree() -> None:
    wrapper = _wrapper_source()
    source = _core_source()
    combined = wrapper + "\n" + source

    assert CORE.relative_to(ROOT).as_posix() in wrapper
    assert not re.search(r"(?m)^[ \t]*(git fetch|git pull|git reset)\b", combined)
    assert "origin/main" not in combined
    assert "deployCurrentBranchDirectly=true" not in combined

    for required in (
        "https://api.github.com",
        "actions/artifacts?per_page=100",
        "archive_download_url",
        "workflow_run",
        "head_sha",
        "AI_RELEASE_SOURCE_COMMIT",
        "release-manifest.json",
        "sourceCommit",
        "install_release_verifier.sh",
        "deploy_release.sh",
    ):
        assert required in combined


def test_github_token_is_not_forwarded_to_artifact_object_storage() -> None:
    source = _core_source()
    api_body = _function_body(source, "api_to_file")
    public_body = _function_body(source, "public_to_file")
    redirect_body = _function_body(source, "download_artifact_zip")

    assert 'Authorization: Bearer $TOKEN' in source
    assert '"${API_HEADERS[@]}"' in api_body
    assert '"${API_HEADERS[@]}"' in redirect_body
    assert "--max-redirs 0" in redirect_body
    assert '"${API_HEADERS[@]}"' not in public_body
    assert "Authorization" not in public_body
    assert "--location" in public_body
    assert "redirect must use HTTPS" in public_body


def test_transport_binds_artifact_sha_to_sealed_manifest_before_deploy() -> None:
    wrapper = _wrapper_source()
    source = _core_source()
    manifest_check = '[ "$MANIFEST_COMMIT" = "$RESOLVED_COMMIT" ]'
    deploy_call = 'bash "$CANDIDATE_DIR/scripts/deploy_release.sh"'

    assert CORE.relative_to(ROOT).as_posix() in wrapper
    assert manifest_check in source
    assert deploy_call in source
    assert source.index(manifest_check) < source.index(deploy_call)
    assert "GitHub artifact ZIP digest mismatch" in source
    assert 'BUNDLE_ROOT="$ARTIFACT_DIR/ci-artifacts"' in source
    assert 'formal-release-${SOURCE_COMMIT}-*.tar.gz' in source
    assert "Expected exactly one formal-release-${SOURCE_COMMIT}-*.tar.gz in ci-artifacts" in source
