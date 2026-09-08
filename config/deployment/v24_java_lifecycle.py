"""Run the sealed Java readiness service and Python API in one systemd lifecycle.

Called only after the existing Root Verifier admits the release. No global Java,
downloads, authority transfer, or extra systemd unit is introduced here.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


MODE = "READY_NO_AUTHORITY"


def runtime_contract(root: Path) -> dict:
    manifest = json.loads((root / "release/release-manifest.json").read_text())
    contract = json.loads((root / "runtime/java/runtime-contract.json").read_text())
    if contract["sourceCommit"] != manifest["sourceCommit"]:
        raise ValueError("Java sourceCommit differs from release")
    if contract["enforcementMode"] != MODE or contract["productionMutationAllowed"] is not False:
        raise ValueError("Java readiness authority boundary mismatch")
    if contract["jarPath"] != "runtime/java/v24-production-authority.jar":
        raise ValueError("Unexpected Java entrypoint")
    files = contract["runtimeFiles"]
    actual = {p.relative_to(root).as_posix() for p in (root / "runtime/java").rglob("*")
              if p.is_file() and p.name != "runtime-contract.json"}
    expected = {item["path"] for item in files}
    if actual != expected or len(expected) != len(files):
        raise ValueError("Java runtime file set mismatch")
    for item in files:
        path = root / item["path"]
        if not path.resolve().is_relative_to(root / "runtime/java"):
            raise ValueError("Java runtime path escapes bundle")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Java runtime hash mismatch: " + item["path"])
    java = root / "runtime/java/jre/bin/java"
    version = subprocess.run([str(java), "-version"], capture_output=True, text=True,
                             timeout=10, check=True)
    if contract["javaRuntimeVersion"] not in version.stdout + version.stderr:
        raise ValueError("Java runtime version mismatch")
    return {"sourceCommit": manifest["sourceCommit"], "releaseHash": manifest["releaseHash"],
            "contractHash": contract["contractHash"]}


def free_port(port: int) -> int:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return sock.getsockname()[1]


def ready(port: int) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/readyz")
        response = connection.getresponse()
        if response.status != 200:
            return False
        status = json.loads(response.read(65536))
        return (status.get("schema") == "v24.production-authority.status.v1"
                and status.get("ready") is True and status.get("mode") == MODE
                and "authorityGeneration" in status and status["authorityGeneration"] is None
                and status.get("productionMutationAllowed") is False
                and status.get("deploymentAuthorityTransferAllowed") is False
                and status.get("legacyRemovalAllowed") is False)
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def stop(children: list[subprocess.Popen]) -> None:
    # Each child owns a process group, including any uvicorn worker descendants.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while any(child.poll() is None for child in children) and time.monotonic() < deadline:
        time.sleep(0.05)
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def supervise(root: Path, port: int, command: list[str], *, preflight: bool = False,
              timeout: float = 20) -> int:
    identity = runtime_contract(root)
    # Refuse occupied ports before starting; never reuse an older Java listener.
    port = free_port(0 if preflight else port)
    env = dict(os.environ, V24_AUTHORITY_MODE=MODE, V24_AUTHORITY_HOST="127.0.0.1",
               V24_AUTHORITY_PORT=str(port), AI_BOOTSTRAP_PYTHON=sys.executable,
               V24_LIVE_MIRROR_ENABLED="1",
               V24_MIRROR_SOURCE_COMMIT=identity["sourceCommit"],
               V24_MIRROR_RELEASE_HASH=identity["releaseHash"],
               V24_MIRROR_CONTRACT_HASH=identity["contractHash"])
    children: list[subprocess.Popen] = []
    interrupted = False

    def terminate(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        java = subprocess.Popen(["/bin/bash", str(root / "scripts/start_v24_authority.sh")],
                                cwd=root, env=env, start_new_session=True)
        children.append(java)
        deadline = time.monotonic() + timeout
        while not interrupted and time.monotonic() < deadline:
            if java.poll() is not None:
                raise RuntimeError("Sealed Java exited before readiness")
            if ready(port):
                time.sleep(0.1)
                if java.poll() is not None:
                    raise RuntimeError("Sealed Java exited during readiness")
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Java readiness interrupted or timed out")
        print(json.dumps(dict(identity, schema="v24.deployment-java-readiness.v1",
                              mode=MODE, port=port, javaPid=java.pid, verified=True,
                              preflight=preflight)), flush=True)
        if preflight:
            return 0
        if interrupted:
            return 1
        if not command:
            raise ValueError("Python API command is required")
        api = subprocess.Popen(command, cwd=root, env=env, start_new_session=True)
        children.append(api)
        while not interrupted:
            if java.poll() is not None or api.poll() is not None:
                return 1
            time.sleep(0.2)
        return 0
    finally:
        stop(children)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--port", type=int, default=39024)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Java port must be between 1024 and 65535")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return supervise(args.root.resolve(), args.port, command, preflight=args.preflight)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("V24 Java lifecycle blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
