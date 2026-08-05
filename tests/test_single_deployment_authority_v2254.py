from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "config" / "deployment" / "deployment_preflight.py"
DEPLOY_RELEASE = ROOT / "scripts" / "deploy_release.sh"
DEPLOY_CORE = ROOT / "src" / "deployment" / "deploy_release_core_v22516.sh"
INSTALL_BOOTSTRAP = ROOT / "config" / "deployment" / "install_deploy_bootstrap.sh"
POLICY = ROOT / "release" / "release-policy.json"


def _run_preflight(root: Path, candidate: Path, min_free: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PREFLIGHT),
            "--root",
            str(root),
            "--active-candidate",
            str(candidate),
            "--backup-keep-count",
            "1",
            "--min-free-bytes",
            str(min_free),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _create_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO t(value) VALUES ('ok')")
        connection.commit()
    finally:
        connection.close()


def test_preflight_collects_failed_releases_before_downtime(tmp_path: Path) -> None:
    root = tmp_path / "app"
    releases = root / "releases"
    current_release = releases / "current-release"
    failed_release = releases / "failed-release"
    stale_incoming = releases / ".incoming-stale"
    active_candidate = releases / ".incoming-active"
    backup_dir = root / "shared" / "logs" / "deployment_backups"

    for path in (current_release, failed_release, stale_incoming, active_candidate, backup_dir):
        path.mkdir(parents=True, exist_ok=True)
    (failed_release / "payload.bin").write_bytes(b"x" * 128)
    (stale_incoming / "payload.bin").write_bytes(b"x" * 64)
    (active_candidate / "candidate.bin").write_bytes(b"x" * 32)
    (backup_dir / "product_workbench-pre-old.sqlite3").write_bytes(b"x" * 96)
    (root / "current").symlink_to(current_release)
    _create_db(root / "shared" / "logs" / "product_workbench.sqlite3")

    completed = _run_preflight(root, active_candidate, min_free=0)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ready"
    assert payload["serviceDowntimeStarted"] is False
    assert current_release.is_dir()
    assert active_candidate.is_dir()
    assert not failed_release.exists()
    assert not stale_incoming.exists()
    assert not (backup_dir / "product_workbench-pre-old.sqlite3").exists()
    assert payload["removedUnreferencedReleases"]
    assert payload["removedIncoming"]
    assert payload["removedDeploymentBackups"]


def test_preflight_fails_before_service_downtime_when_space_is_insufficient(tmp_path: Path) -> None:
    root = tmp_path / "app"
    candidate = root / "releases" / ".incoming-active"
    candidate.mkdir(parents=True)
    _create_db(root / "shared" / "logs" / "product_workbench.sqlite3")

    completed = _run_preflight(root, candidate, min_free=10**18)
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "failed"
    assert payload["errorType"] == "InsufficientDiskSpaceBeforeServiceDowntime"
    assert payload["serviceDowntimeStarted"] is False
    assert payload["shortageBytes"] > 0


def test_deploy_release_orders_preflight_before_materialize_and_stop() -> None:
    wrapper = DEPLOY_RELEASE.read_text(encoding="utf-8")
    source = DEPLOY_CORE.read_text(encoding="utf-8")

    assert DEPLOY_CORE.relative_to(ROOT).as_posix() in wrapper
    preflight = source.index('log "3. Online cleanup and storage preflight before service downtime"')
    materialize = source.index('log "4. Materialize exact release directory"')
    stop_stage = source.index('log "7. Revoke every old runtime owner and forbidden legacy path"')
    backup = source.index('log "8. Migrate shared state and create validated rollback backup"')

    assert preflight < materialize < stop_stage < backup
    assert 'systemctl stop "$SERVICE"' in source[stop_stage:backup]
    assert "TARGET_CREATED_BY_THIS_RUN=true" in source
    assert 'rm -rf "$TARGET" || true' in source
    assert 'PREFLIGHT_PATH="$INCOMING/config/deployment/deployment_preflight.py"' in source
    assert 'BOOTSTRAP_INSTALLER="$TARGET/config/deployment/install_deploy_bootstrap.sh"' in source


def test_server_wrapper_uses_immutable_libexec_not_current_release() -> None:
    source = INSTALL_BOOTSTRAP.read_text(encoding="utf-8")
    assert "/usr/local/libexec/ai-ecommerce/deploy-bootstrap" in source
    assert "/usr/local/libexec/ai-ecommerce/deployment-preflight" in source
    assert 'exec "$BOOTSTRAP"' in source
    assert '"$ROOT/current/scripts/deploy_github_artifact.sh"' not in source
    assert "/etc/ai-ecommerce-assistant/deployment.env" in source
    assert "/etc/ai-ecommerce-assistant/github-artifact.env" in source


def test_deployment_files_use_existing_pinned_root_policy_scope() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    runtime = set(policy["runtimeGlobs"])
    rules = policy["rules"]

    assert "config/**/*" in runtime
    assert "src/**/*" in runtime
    assert DEPLOY_CORE.relative_to(ROOT).as_posix().startswith("src/")
    assert PREFLIGHT.relative_to(ROOT).as_posix().startswith("config/")
    assert INSTALL_BOOTSTRAP.relative_to(ROOT).as_posix().startswith("config/")
    assert rules["rootVerifierOrdinaryRotationAllowed"] is False
    assert rules["rootVerifierExplicitOldHashRequiredForRotation"] is True
    assert "deploymentPreflightMustRunBeforeServiceDowntime" not in rules
    assert "serverDeployWrapperMayDependOnCurrentRelease" not in rules


def test_new_shell_scripts_are_syntax_valid() -> None:
    for path in (DEPLOY_RELEASE, DEPLOY_CORE, INSTALL_BOOTSTRAP):
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, f"{path}: {completed.stderr}"
