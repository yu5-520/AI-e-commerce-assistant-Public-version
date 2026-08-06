#!/usr/bin/env python3
"""Deploy a verified competition package as an isolated ECS candidate.

This command deliberately does **not** switch the production ``current``
symlink, restart a production service, load production ``.env`` files, or reuse
the production SQLite database. It materializes an immutable application tree,
redirects writable paths to a candidate-only state directory, starts the public
FastAPI entrypoint on loopback, records HTTP smoke evidence, and stops it.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from verify_competition_runtime_package import (  # noqa: E402
    MANIFEST_PATH,
    verify_archive,
)


SCHEMA = "competition.ecs_candidate_smoke.v1"
REQUIRED_IMPORTS = ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "openpyxl")
WRITABLE_NAMES = ("data", "logs", "outputs")


class CandidateDeploymentError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_manifest(archive: Path) -> dict[str, Any]:
    with tarfile.open(archive, mode="r:gz") as handle:
        member = handle.getmember(MANIFEST_PATH)
        extracted = handle.extractfile(member)
        if extracted is None:
            raise CandidateDeploymentError("PACKAGE_MANIFEST_UNREADABLE")
        value = json.loads(extracted.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise CandidateDeploymentError("PACKAGE_MANIFEST_OBJECT_REQUIRED")
    return value


def current_target(deploy_root: Path) -> str | None:
    current = deploy_root / "current"
    if not current.exists() and not current.is_symlink():
        return None
    try:
        return str(current.resolve(strict=False))
    except OSError:
        return os.path.realpath(current)


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, mode="r:gz") as handle:
        for member in handle.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or "." in name.parts:
                raise CandidateDeploymentError(f"UNSAFE_ARCHIVE_PATH:{member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise CandidateDeploymentError(f"UNSAFE_ARCHIVE_MEMBER:{member.name}")
            target = (destination / name.as_posix()).resolve()
            if root != target and root not in target.parents:
                raise CandidateDeploymentError(f"ARCHIVE_PATH_ESCAPES_DESTINATION:{member.name}")
        handle.extractall(destination)


def make_candidate_writable_boundaries(app_root: Path, state_root: Path) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    for name in WRITABLE_NAMES:
        target = state_root / name
        target.mkdir(parents=True, exist_ok=True)
        link = app_root / name
        if link.is_symlink():
            if link.resolve(strict=False) != target.resolve(strict=False):
                raise CandidateDeploymentError(f"WRITABLE_LINK_TARGET_MISMATCH:{name}")
            continue
        if link.exists():
            if link.is_dir() and not any(link.iterdir()):
                link.rmdir()
            else:
                raise CandidateDeploymentError(f"PACKAGE_WRITABLE_PATH_NOT_EMPTY:{name}")
        link.symlink_to(target, target_is_directory=True)


def make_application_read_only(app_root: Path) -> None:
    paths = sorted(app_root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for path in paths:
        if path.is_symlink():
            continue
        if path.is_file():
            current_mode = path.stat().st_mode
            path.chmod(0o555 if current_mode & 0o111 else 0o444)
        elif path.is_dir():
            path.chmod(0o555)
    app_root.chmod(0o555)


def candidate_python_paths(deploy_root: Path, explicit: str | None) -> list[Path]:
    raw: list[str] = []
    if explicit:
        raw.append(explicit)
    raw.extend(
        [
            str(deploy_root / "current" / ".venv" / "bin" / "python"),
            str(deploy_root / "shared" / ".venv" / "bin" / "python"),
            str(deploy_root / "shared" / "venv" / "bin" / "python"),
            str(deploy_root / ".venv" / "bin" / "python"),
            "/opt/ai-runtime/python/current/bin/python3.11",
            "/opt/ai-runtime/python/3.11.9/bin/python3.11",
            "/opt/python/3.11.9/bin/python3.11",
        ]
    )
    raw.extend(glob.glob(str(deploy_root / "releases" / "*" / ".venv" / "bin" / "python")))
    raw.extend(glob.glob("/opt/**/.venv/bin/python", recursive=True))
    raw.append(sys.executable)

    resolved: list[Path] = []
    seen: set[str] = set()
    for value in raw:
        if not value:
            continue
        path = Path(value).expanduser()
        try:
            key = str(path.resolve(strict=False))
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file() and os.access(path, os.X_OK):
            resolved.append(path)
    return resolved


def inspect_runtime_python(path: Path) -> dict[str, Any]:
    command = [
        str(path),
        "-c",
        (
            "import importlib,json,platform,sys;"
            f"mods={list(REQUIRED_IMPORTS)!r};"
            "missing=[];"
            "[(missing.append(name) if importlib.util.find_spec(name) is None else None) for name in mods];"
            "print(json.dumps({'pythonVersion':platform.python_version(),'executable':sys.executable,'missing':missing},sort_keys=True));"
            "raise SystemExit(0 if sys.version_info[:3]==(3,11,9) and not missing else 1)"
        ),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=20)
    output = (result.stdout or result.stderr or "").strip()
    try:
        details = json.loads(output.splitlines()[-1]) if output else {}
    except json.JSONDecodeError:
        details = {"raw": output[:1000]}
    return {
        "path": str(path),
        "usable": result.returncode == 0,
        "returnCode": result.returncode,
        **details,
    }


def select_runtime_python(deploy_root: Path, explicit: str | None) -> tuple[Path, list[dict[str, Any]]]:
    inspections: list[dict[str, Any]] = []
    for path in candidate_python_paths(deploy_root, explicit):
        details = inspect_runtime_python(path)
        inspections.append(details)
        if details.get("usable") is True:
            return Path(str(details.get("executable") or path)), inspections
    raise CandidateDeploymentError(
        "PINNED_APPLICATION_PYTHON_NOT_FOUND:" + json.dumps(inspections, ensure_ascii=False)
    )


def choose_loopback_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                handle.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise CandidateDeploymentError(f"NO_FREE_CANDIDATE_PORT:{preferred}-{preferred + 19}")


def http_probe(port: int, path: str, timeout: float = 4.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(65536)
            text = body.decode("utf-8", errors="replace")
            parsed: Any = None
            content_type = response.headers.get("Content-Type", "")
            if "json" in content_type.lower():
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            return {
                "path": path,
                "url": url,
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "contentType": content_type,
                "bodySha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                "bodyPreview": text[:500],
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(65536)
        return {
            "path": path,
            "url": url,
            "ok": False,
            "status": exc.code,
            "bodyPreview": body.decode("utf-8", errors="replace")[:500],
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "path": path,
            "url": url,
            "ok": False,
            "status": None,
            "error": f"{type(exc).__name__}:{exc}",
        }


def wait_for_candidate(process: subprocess.Popen[Any], port: int, timeout: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_results: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CandidateDeploymentError(f"CANDIDATE_PROCESS_EXITED:{process.returncode}")
        health = http_probe(port, "/api/health", timeout=2.0)
        version = http_probe(port, "/api/version", timeout=2.0)
        root = http_probe(port, "/", timeout=2.0)
        last_results = [health, version, root]
        if all(item.get("ok") is True for item in last_results):
            return last_results
        time.sleep(1.0)
    raise CandidateDeploymentError(
        "CANDIDATE_HTTP_TIMEOUT:" + json.dumps(last_results, ensure_ascii=False)
    )


def stop_process(process: subprocess.Popen[Any]) -> dict[str, Any]:
    if process.poll() is not None:
        return {"alreadyExited": True, "returnCode": process.returncode}
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=20)
        return {"terminated": True, "returnCode": process.returncode}
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        return {"killed": True, "returnCode": process.returncode}


def deploy_candidate(
    *,
    archive: Path,
    deploy_root: Path,
    expected_source_commit: str,
    preferred_port: int,
    explicit_python: str | None,
    attestation_path: Path,
    startup_timeout: float,
) -> dict[str, Any]:
    started_at = time.time()
    deploy_root.mkdir(parents=True, exist_ok=True)
    before_current = current_target(deploy_root)
    archive_verification = verify_archive(archive, expected_source_commit)
    manifest = read_manifest(archive)
    source_commit = str(manifest.get("sourceCommit") or "")
    manifest_hash = str(manifest.get("manifestHash") or "")
    candidate_id = f"{source_commit[:12]}-{manifest_hash.removeprefix('sha256:')[:12]}"
    candidates_root = deploy_root / "competition-candidates"
    target = candidates_root / candidate_id
    app_root = target / "app"
    state_root = target / "state"
    log_path = state_root / "candidate-uvicorn.log"
    process: subprocess.Popen[Any] | None = None
    runtime_inspections: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "verified": False,
        "sourceCommit": source_commit,
        "candidateId": candidate_id,
        "candidateRoot": str(target),
        "archive": str(archive),
        "archiveVerification": archive_verification,
        "manifestHash": manifest_hash,
        "runtimeHash": manifest.get("runtimeHash"),
        "lineageGraphHash": manifest.get("lineageGraphHash"),
        "productionCurrentBefore": before_current,
        "productionCurrentAfter": None,
        "productionCurrentUnchanged": False,
        "productionServiceRestarted": False,
        "productionSymlinkSwitched": False,
        "productionEnvironmentLoaded": False,
        "productionDatabaseReused": False,
        "candidateOnlyWritableState": True,
        "startedAtEpoch": started_at,
        "errors": [],
    }

    try:
        candidates_root.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing_manifest_path = app_root / MANIFEST_PATH
            if not existing_manifest_path.is_file():
                raise CandidateDeploymentError(f"EXISTING_CANDIDATE_INCOMPLETE:{target}")
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            if existing_manifest.get("manifestHash") != manifest_hash:
                raise CandidateDeploymentError(f"EXISTING_CANDIDATE_IDENTITY_MISMATCH:{target}")
            shutil.rmtree(state_root, ignore_errors=True)
        else:
            temporary = candidates_root / f".{candidate_id}.tmp-{os.getpid()}"
            shutil.rmtree(temporary, ignore_errors=True)
            safe_extract(archive, temporary / "app")
            temporary.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, target)

        state_root.mkdir(parents=True, exist_ok=True)
        make_candidate_writable_boundaries(app_root, state_root)
        make_application_read_only(app_root)

        runtime_python, runtime_inspections = select_runtime_python(deploy_root, explicit_python)
        report["runtimePython"] = str(runtime_python)
        report["runtimePythonInspections"] = runtime_inspections
        port = choose_loopback_port(preferred_port)
        report["loopbackPort"] = port

        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(app_root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "AI_RELEASE_ROOT": str(app_root),
                "AI_RELEASE_MANIFEST": str(state_root / "competition-candidate-no-legacy-manifest.json"),
                "AI_RELEASE_REQUIRED": "0",
                "ARTIFACT_ROOT": str(state_root / "data" / "artifacts"),
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(port),
                "APP_WORKERS": "1",
                "APP_RELOAD": "false",
            }
        )
        for sensitive in (
            "DATABASE_URL",
            "SQLALCHEMY_DATABASE_URI",
            "REDIS_URL",
            "AI_REGISTRY_RECEIPT_ROOT",
        ):
            environment.pop(sensitive, None)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                [
                    str(runtime_python),
                    "-m",
                    "uvicorn",
                    "src.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                ],
                cwd=state_root,
                env=environment,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            report["candidatePid"] = process.pid
            probes = wait_for_candidate(process, port, startup_timeout)
            report["httpProbes"] = probes
            health_json = probes[0].get("json") if probes else None
            version_json = probes[1].get("json") if len(probes) > 1 else None
            if not isinstance(health_json, dict) or health_json.get("ok") is not True:
                raise CandidateDeploymentError("HEALTH_PAYLOAD_INVALID")
            if not isinstance(version_json, dict):
                raise CandidateDeploymentError("VERSION_PAYLOAD_INVALID")
            if health_json.get("version") != "22.4.0":
                raise CandidateDeploymentError(
                    f"PRODUCT_VERSION_MISMATCH:{health_json.get('version')}:22.4.0"
                )
            if health_json.get("releaseVerified") is not False:
                raise CandidateDeploymentError("CANDIDATE_LEGACY_RELEASE_IDENTITY_SHOULD_BE_UNSEALED")
            report["candidateProcessStop"] = stop_process(process)
            process = None

        after_current = current_target(deploy_root)
        report["productionCurrentAfter"] = after_current
        report["productionCurrentUnchanged"] = before_current == after_current
        if not report["productionCurrentUnchanged"]:
            raise CandidateDeploymentError(
                f"PRODUCTION_CURRENT_CHANGED:{before_current}:{after_current}"
            )
        report["candidateLogSha256"] = (
            "sha256:" + hashlib.sha256(log_path.read_bytes()).hexdigest()
            if log_path.is_file()
            else None
        )
        report["completedAtEpoch"] = time.time()
        report["durationSeconds"] = round(report["completedAtEpoch"] - started_at, 3)
        report["verificationHash"] = canonical_hash(
            {
                key: value
                for key, value in report.items()
                if key not in {"verificationHash", "verified", "errors"}
            }
        )
        report["verified"] = True
        write_json(target / "candidate-attestation.json", report)
        write_json(attestation_path, report)
        return report
    except Exception as exc:
        if process is not None:
            try:
                report["candidateProcessStop"] = stop_process(process)
            except Exception as stop_exc:
                report.setdefault("errors", []).append(
                    f"PROCESS_STOP_FAILED:{type(stop_exc).__name__}:{stop_exc}"
                )
        report.setdefault("errors", []).append(f"{type(exc).__name__}:{exc}")
        report["runtimePythonInspections"] = runtime_inspections
        report["productionCurrentAfter"] = current_target(deploy_root)
        report["productionCurrentUnchanged"] = (
            report["productionCurrentBefore"] == report["productionCurrentAfter"]
        )
        report["candidateLogTail"] = (
            log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            if log_path.is_file()
            else ""
        )
        report["completedAtEpoch"] = time.time()
        report["durationSeconds"] = round(report["completedAtEpoch"] - started_at, 3)
        if target.exists():
            write_json(target / "candidate-attestation.json", report)
        write_json(attestation_path, report)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy and smoke an isolated ECS competition candidate.")
    parser.add_argument("archive")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--deploy-root", default="/opt/ai-ecommerce-assistant")
    parser.add_argument("--port", type=int, default=39080)
    parser.add_argument("--runtime-python", default=None)
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    parser.add_argument("--attestation", default="dist/competition-candidate/candidate-attestation.json")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = deploy_candidate(
        archive=Path(args.archive).expanduser().resolve(),
        deploy_root=Path(args.deploy_root).expanduser().resolve(),
        expected_source_commit=args.source_commit.strip(),
        preferred_port=args.port,
        explicit_python=args.runtime_python,
        attestation_path=Path(args.attestation).expanduser().resolve(),
        startup_timeout=args.startup_timeout,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition ECS candidate smoke failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
