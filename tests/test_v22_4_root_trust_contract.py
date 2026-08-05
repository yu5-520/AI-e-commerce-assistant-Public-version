from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WRAPPER = "scripts/deploy_release.sh"
DEPLOY_CORE = "src/deployment/deploy_release_core_v22516.sh"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_root_verifier_install_is_fail_closed() -> None:
    installer = _text("scripts/install_release_verifier.sh")
    for marker in (
        "AI_RELEASE_VERIFIER_ROTATE",
        "AI_RELEASE_VERIFIER_EXPECTED_OLD_SHA256",
        "target and hash record must exist together",
        "ordinary release deployment cannot rotate root trust",
        "Expected old verifier SHA256 does not match",
        "Verifier changed during installation",
        'mv -f "$TEMP_TARGET" "$TARGET"',
        'mv -f "$TEMP_HASH" "$HASH_FILE"',
    ):
        assert marker in installer
    assert '[ ! -L "$SOURCE" ]' in installer
    assert '[ ! -L "$TARGET" ]' in installer
    assert '[ ! -L "$HASH_FILE" ]' in installer


def test_deploy_uses_the_same_verifier_as_root_trust() -> None:
    wrapper = _text(DEPLOY_WRAPPER)
    deploy = _text(DEPLOY_CORE)
    assert DEPLOY_CORE in wrapper
    for marker in (
        "CANDIDATE_VERIFIER",
        "CANDIDATE_VERIFIER_HASH",
        'CANDIDATE_VERIFIER_HASH" = "$EXPECTED_VERIFIER_HASH',
        "Release bundle verifier differs from the root-pinned verifier",
        "complete an explicit root trust rotation before deployment",
        "Pinned root verifier must not be a symlink",
        "Release bundle verifier must not be a symlink",
    ):
        assert marker in deploy
    assert deploy.index("CANDIDATE_VERIFIER_HASH") < deploy.index(
        "Verify release DNA with the pinned root verifier"
    )


def test_python36_bootstrap_contract_is_attested() -> None:
    checker = _text("scripts/check_python36_bootstrap.py")
    policy = _text("release/release-policy.json")
    workflow = _text(".github/workflows/release-hash-seal.yml")
    assert "feature_version=PY36" in checker
    assert "scripts/check_python36_bootstrap.py" in policy
    assert "python36BootstrapCompatibilityRequired" in policy
    assert "scripts/check_python36_bootstrap.py" in workflow
    assert "python36BootstrapChecked" in workflow
