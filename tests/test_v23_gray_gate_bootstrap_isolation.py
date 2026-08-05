from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_release.sh"
RECEIPT_GATE = ROOT / "src" / "services" / "registry_runtime_receipt_v23_service.py"


def test_deploy_gray_gate_executes_receipt_by_file_path() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert '"$GRAY_ROOT/src/services/registry_runtime_receipt_v23_service.py"' in script
    assert "-m src.services.registry_runtime_receipt_v23_service" not in script
    assert 'AI_RELEASE_ROOT="$GRAY_ROOT"' in script


def test_receipt_gate_cli_runs_without_site_packages() -> None:
    completed = subprocess.run(
        [sys.executable, "-S", str(RECEIPT_GATE), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "selected-module gray/production receipt hard gate" in completed.stdout
