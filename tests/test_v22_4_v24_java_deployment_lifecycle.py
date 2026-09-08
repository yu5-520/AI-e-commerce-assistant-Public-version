"""Exercise process lifecycle failures without installing Java on the test host."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "config/deployment/v24_java_lifecycle.py"
spec = importlib.util.spec_from_file_location("java_lifecycle", HELPER)
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "release"
    java = root / "runtime/java/jre/bin/java"
    java.parent.mkdir(parents=True)
    java.write_text("#!/bin/sh\necho test-runtime >&2\n")
    java.chmod(0o755)
    jar = root / "runtime/java/v24-production-authority.jar"
    jar.write_bytes(b"test jar")
    (root / "release").mkdir()
    (root / "release/release-manifest.json").write_text(json.dumps({
        "sourceCommit": "a" * 40, "releaseHash": "sha256:test"}))
    (root / "scripts").mkdir()
    # Real child process and HTTP endpoint; this is a simulated Java service.
    server = root / "server.py"
    server.write_text('''import http.server, json, os
from pathlib import Path
Path("java.pid").write_text(str(os.getpid()))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        status = dict(schema="v24.production-authority.status.v1", ready=True,
            mode="READY_NO_AUTHORITY", authorityGeneration=None,
            productionMutationAllowed=False, deploymentAuthorityTransferAllowed=False,
            legacyRemovalAllowed=False)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())
    def log_message(self, *args): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["V24_AUTHORITY_PORT"])), Handler).serve_forever()
''')
    (root / "scripts/start_v24_authority.sh").write_text(
        'exec "$AI_BOOTSTRAP_PYTHON" server.py\n')
    entries = [{"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in (java, jar)]
    (root / "runtime/java/runtime-contract.json").write_text(json.dumps({
        "sourceCommit": "a" * 40, "enforcementMode": "READY_NO_AUTHORITY",
        "productionMutationAllowed": False, "javaRuntimeVersion": "test-runtime",
        "jarPath": jar.relative_to(root).as_posix(), "contractHash": "sha256:test",
        "runtimeFiles": entries}))
    return root


def assert_dead(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_preflight_stops_child_and_leaves_port_free(bundle):
    assert lifecycle.supervise(bundle, 39024, [], preflight=True) == 0
    assert_dead(int((bundle / "java.pid").read_text()))


@pytest.mark.parametrize("failure", ["missing_jre", "tampered_jar", "wrong_commit"])
def test_bad_bundle_rejected_before_any_process(bundle, failure):
    if failure == "missing_jre":
        (bundle / "runtime/java/jre/bin/java").unlink()
    elif failure == "tampered_jar":
        (bundle / "runtime/java/v24-production-authority.jar").write_bytes(b"changed")
    else:
        (bundle / "release/release-manifest.json").write_text(json.dumps({"sourceCommit": "b" * 40}))
    with pytest.raises(ValueError):
        lifecycle.supervise(bundle, 39024, [], preflight=True)
    assert not (bundle / "java.pid").exists()


def test_port_collision_does_not_start_or_kill_other_service(bundle):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(OSError):
            lifecycle.supervise(bundle, listener.getsockname()[1], [sys.executable, "-c", "pass"])
        assert listener.fileno() >= 0
    assert not (bundle / "java.pid").exists()


def test_java_start_failure_never_launches_api(bundle):
    (bundle / "scripts/start_v24_authority.sh").write_text("exit 7\n")
    with pytest.raises(RuntimeError, match="exited before"):
        lifecycle.supervise(bundle, lifecycle.free_port(0),
                            [sys.executable, "-c", "open('api.started','w').close()"])
    assert not (bundle / "api.started").exists()


def test_api_exit_stops_java_and_fails_unit(bundle):
    assert lifecycle.supervise(bundle, lifecycle.free_port(0), [sys.executable, "-c", "pass"]) == 1
    assert_dead(int((bundle / "java.pid").read_text()))


def test_admitted_mirror_identity_and_actual_port_reach_python(bundle):
    port = lifecycle.free_port(0)
    code = "import json,os;open('mirror-env.json','w').write(json.dumps({k:v for k,v in os.environ.items() if k.startswith('V24_')}))"
    assert lifecycle.supervise(bundle, port, [sys.executable, "-c", code]) == 1
    env = json.loads((bundle / "mirror-env.json").read_text())
    assert env["V24_AUTHORITY_PORT"] == str(port)
    assert env["V24_LIVE_MIRROR_ENABLED"] == "1"
    assert env["V24_MIRROR_SOURCE_COMMIT"] == "a" * 40
    assert env["V24_MIRROR_RELEASE_HASH"] == "sha256:test"
    assert env["V24_MIRROR_CONTRACT_HASH"] == "sha256:test"


@pytest.mark.parametrize("event", ["java_exit", "systemd_stop"])
def test_java_failure_or_service_stop_terminates_both_children(bundle, event):
    port = lifecycle.free_port(0)
    api = "import os,time;open('api.pid','w').write(str(os.getpid()));time.sleep(60)"
    proc = subprocess.Popen([sys.executable, str(HELPER), "--root", str(bundle),
                             "--port", str(port), "--", sys.executable, "-c", api])
    try:
        deadline = time.monotonic() + 10
        while not (bundle / "api.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (bundle / "api.pid").exists()
        java_pid = int((bundle / "java.pid").read_text())
        api_pid = int((bundle / "api.pid").read_text())
        os.kill(java_pid if event == "java_exit" else proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=10) == (1 if event == "java_exit" else 0)
        assert_dead(java_pid)
        assert_dead(api_pid)
        assert lifecycle.free_port(port) == port
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)


def test_real_sealed_java_preflight_when_release_workflow_has_built_it(tmp_path):
    runtime = ROOT / "runtime/java"
    if not (runtime / "runtime-contract.json").exists():
        pytest.skip("Release Hash Seal builds the pinned JRE before pytest")
    root = tmp_path / "real-bundle"
    shutil.copytree(runtime, root / "runtime/java")
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/start_v24_authority.sh", root / "scripts/start_v24_authority.sh")
    contract = json.loads((runtime / "runtime-contract.json").read_text())
    (root / "release").mkdir()
    (root / "release/release-manifest.json").write_text(json.dumps({
        "sourceCommit": contract["sourceCommit"], "releaseHash": "sha256:preflight-test"}))
    assert lifecycle.supervise(root, 39024, [], preflight=True) == 0
