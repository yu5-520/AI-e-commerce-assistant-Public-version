#!/usr/bin/env python3
"""Competition Runtime Verification Pilot.

One stdlib-only gate runs in three contexts:

- repository: prove candidate source/governance identity;
- ecs-candidate: re-run the same source gate on ECS and add environment/database identity;
- ecs-runtime: bind the report to the actual uvicorn process, current symlink and live DB.

The pilot intentionally does not import ``src``. Importing the application would mutate
runtime bindings and would make the verifier part of the system it is trying to verify.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

VERSION = "2026.08.10.1"
DEFAULT_CONFIG = "governance/runtime_verification_pilot_v1.json"


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_bytes(stable_json(value).encode("utf-8"))


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("expected_json_object:{0}".format(path))
    return value


def existing_hashes(root: Path, paths: Iterable[str], findings: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for rel in paths:
        path = root / rel
        if not path.is_file():
            findings.append("missing_source_file:{0}".format(rel))
            continue
        result[rel] = file_hash(path)
    return result


def normalized_runtime_authority(root: Path, findings: List[str]) -> Dict[str, Any]:
    spec_path = root / "governance/runtime_callable_authority_v1.json"
    if not spec_path.is_file():
        findings.append("runtime_callable_authority_spec_missing")
        return {"verified": False, "hash": sha256_value({"missing": True})}
    spec = load_json(spec_path)
    scans: List[Dict[str, Any]] = []
    for item in spec.get("callables") or []:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("ownerPath") or "")
        owner_path = root / owner
        if not owner_path.is_file():
            findings.append("callable_owner_missing:{0}".format(owner))
            continue
        owner_text = owner_path.read_text(encoding="utf-8", errors="replace")
        function = str(item.get("ownerFunction") or "")
        if function and "def {0}(".format(function) not in owner_text:
            findings.append("callable_owner_function_missing:{0}:{1}".format(owner, function))
        bridge_path = root / "src/services/hard_interface_bridge_v2301_service.py"
        bridge_text = bridge_path.read_text(encoding="utf-8", errors="replace") if bridge_path.is_file() else ""
        for literal in item.get("forbiddenRebinds") or []:
            if str(literal) and str(literal) in bridge_text:
                findings.append("forbidden_runtime_rebind:{0}".format(literal))
        scans.append({
            "callableId": item.get("callableId"),
            "ownerPath": owner,
            "ownerHash": file_hash(owner_path),
            "forbiddenRebindCount": len(item.get("forbiddenRebinds") or []),
        })
    legacy = spec.get("legacyOverlay") or {}
    if isinstance(legacy, dict) and legacy.get("mutationAllowed") is False:
        rel = str(legacy.get("path") or "")
        literal = str(legacy.get("forbiddenMutationLiteral") or "")
        p = root / rel
        text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        if literal and literal in text:
            findings.append("forbidden_contract_mutation:{0}:{1}".format(rel, literal))
    return {
        "verified": not any(x.startswith(("callable_", "forbidden_runtime_rebind", "forbidden_contract_mutation")) for x in findings),
        "scan": scans,
        "hash": sha256_value(scans),
    }


def repository_identity(root: Path, config: Dict[str, Any], expected_source_commit: str, findings: List[str]) -> Dict[str, Any]:
    required = [str(x) for x in (config.get("requiredSourceFiles") or [])]
    source_files = [str(x) for x in (config.get("sourceIdentityFiles") or [])]
    existing_hashes(root, required, findings)
    hashes = existing_hashes(root, source_files, findings)
    authority = normalized_runtime_authority(root, findings)
    source_identity = {
        "expectedSourceCommit": expected_source_commit or None,
        "files": hashes,
        "runtimeCallableAuthorityHash": authority.get("hash"),
    }
    source_hash = sha256_value(source_identity)
    repository_gate_hash = sha256_value({
        "schema": "competition.repository_gate.v1",
        "sourceIdentityHash": source_hash,
        "requiredSourceFileCount": len(required),
        "sourceIdentityFileCount": len(hashes),
        "runtimeCallableAuthorityHash": authority.get("hash"),
    })
    return {
        "sourceIdentityHash": source_hash,
        "repositoryGateHash": repository_gate_hash,
        "fileHashes": hashes,
        "runtimeCallableAuthority": authority,
    }


def sqlite_connection(path: Path):
    uri = "file:{0}?mode=ro".format(path.resolve())
    return sqlite3.connect(uri, uri=True, timeout=20)


def database_identity(path: Path, state_tables: Iterable[str], findings: List[str]) -> Dict[str, Any]:
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "quickCheck": "missing",
            "databaseSchemaHash": sha256_value({"missing": True}),
            "databaseStateHash": sha256_value({"missing": True}),
            "state": {},
        }
    try:
        conn = sqlite_connection(path)
    except Exception as exc:
        findings.append("database_open_failed:{0}".format(type(exc).__name__))
        return {
            "path": str(path),
            "exists": True,
            "quickCheck": "open_failed",
            "databaseSchemaHash": sha256_value({"openFailed": True}),
            "databaseStateHash": sha256_value({"openFailed": True}),
            "state": {},
        }
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        quick = str(row[0] if row else "missing")
        if quick != "ok":
            findings.append("database_quick_check_failed:{0}".format(quick))
        schema_rows = conn.execute(
            "SELECT type,name,tbl_name,COALESCE(sql,'') AS sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name,sql"
        ).fetchall()
        schema = [dict(r) for r in schema_rows]
        known_tables = {
            str(r["name"])
            for r in schema_rows
            if str(r["type"]) == "table"
        }
        state: Dict[str, Any] = {}
        for table in state_tables:
            table = str(table)
            if table not in known_tables or not re.match(r"^[A-Za-z0-9_]+$", table):
                continue
            count = int(conn.execute("SELECT COUNT(*) FROM {0}".format(table)).fetchone()[0])
            columns = {
                str(r[1])
                for r in conn.execute("PRAGMA table_info({0})".format(table)).fetchall()
            }
            latest = None
            for candidate in ("updated_at", "created_at", "id"):
                if candidate in columns:
                    latest_row = conn.execute(
                        "SELECT MAX({0}) FROM {1}".format(candidate, table)
                    ).fetchone()
                    latest = latest_row[0] if latest_row else None
                    break
            state[table] = {"count": count, "latest": latest}
        return {
            "path": str(path),
            "exists": True,
            "quickCheck": quick,
            "databaseSchemaHash": sha256_value(schema),
            "databaseStateHash": sha256_value(state),
            "state": state,
        }
    finally:
        conn.close()


def filtered_environment(config: Dict[str, Any], env: Dict[str, str]) -> Tuple[Dict[str, Any], Dict[str, bool]]:
    keys = [str(x) for x in (config.get("runtimeEnvironmentKeys") or [])]
    secret_keys = [str(x) for x in (config.get("secretPresenceKeys") or [])]
    values = {key: env.get(key) for key in keys if key in env}
    presence = {key: bool(env.get(key)) for key in secret_keys}
    return values, presence


def pip_freeze_hash(python: str | None) -> Dict[str, Any]:
    if not python:
        return {"python": None, "pipFreezeHash": sha256_value([]), "packageCount": 0, "error": "python_missing"}
    try:
        proc = subprocess.run(
            [python, "-m", "pip", "freeze", "--all"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        lines = sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())
        return {"python": python, "pipFreezeHash": sha256_value(lines), "packageCount": len(lines), "error": None}
    except Exception as exc:
        return {"python": python, "pipFreezeHash": sha256_value([]), "packageCount": 0, "error": type(exc).__name__}


def python_version(python: str | None) -> str | None:
    if not python:
        return None
    try:
        proc = subprocess.run(
            [python, "-c", "import platform; print(platform.python_version())"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        return proc.stdout.strip() or None
    except Exception:
        return None


def read_proc_environ(pid: int) -> Dict[str, str]:
    raw = Path("/proc/{0}/environ".format(pid)).read_bytes()
    result: Dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        result[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return result


def locate_runtime(process_needles: Iterable[str]) -> Dict[str, Any] | None:
    needles = [str(x) for x in process_needles]
    proc = Path("/proc")
    candidates: List[Dict[str, Any]] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            if not all(needle in cmd for needle in needles):
                continue
            cwd = str((entry / "cwd").resolve())
            env = read_proc_environ(int(entry.name))
            exe = str((entry / "exe").resolve())
            candidates.append({"pid": int(entry.name), "cmdline": cmd, "cwd": cwd, "env": env, "exe": exe})
        except Exception:
            continue
    candidates.sort(key=lambda x: x["pid"], reverse=True)
    return candidates[0] if candidates else None


def runtime_python(runtime: Dict[str, Any] | None, fallback: str | None) -> str | None:
    if runtime:
        env = runtime.get("env") or {}
        venv = str(env.get("VIRTUAL_ENV") or "").strip()
        for name in ("python", "python3", "python3.11"):
            p = Path(venv) / "bin" / name if venv else None
            if p and p.is_file() and os.access(str(p), os.X_OK):
                return str(p)
        exe = str(runtime.get("exe") or "").strip()
        if exe:
            return exe
    return fallback


def runtime_identity(
    *,
    mode: str,
    root: Path,
    config: Dict[str, Any],
    deploy_root: Path,
    findings: List[str],
) -> Dict[str, Any]:
    runtime = None
    env = dict(os.environ)
    if mode == "ecs-runtime":
        runtime = locate_runtime((config.get("runtime") or {}).get("processNeedles") or ["uvicorn", "src.api.main:app"])
        if not runtime:
            findings.append("live_runtime_process_missing")
        else:
            env = runtime.get("env") or {}
            current = deploy_root / "current"
            current_real = str(current.resolve()) if current.exists() or current.is_symlink() else None
            live_root = str(runtime.get("cwd") or "")
            if (config.get("runtime") or {}).get("currentSymlinkMustMatchLiveCwd", True) and current_real != live_root:
                findings.append("current_symlink_live_cwd_mismatch:{0}:{1}".format(current_real, live_root))
            if live_root and str(root.resolve()) != live_root:
                findings.append("gate_root_live_cwd_mismatch:{0}:{1}".format(root.resolve(), live_root))
    env_values, secret_presence = filtered_environment(config, env)
    fallback_python = os.environ.get("AI_RELEASE_PYTHON") or os.environ.get("AI_BOOTSTRAP_PYTHON") or sys.executable
    py = runtime_python(runtime, fallback_python)
    version = python_version(py)
    required_version = str((config.get("runtime") or {}).get("requiredPythonMajorMinor") or "")
    if required_version and (not version or not version.startswith(required_version + ".")):
        findings.append("runtime_python_version_mismatch:{0}:{1}".format(required_version, version))
    dependency = pip_freeze_hash(py)
    requirements = root / "requirements.lock"
    requirements_hash = file_hash(requirements) if requirements.is_file() else None
    payload = {
        "mode": mode,
        "python": py,
        "pythonVersion": version,
        "requirementsLockHash": requirements_hash,
        "dependencyHash": dependency.get("pipFreezeHash"),
        "packageCount": dependency.get("packageCount"),
        "dependencyProbeError": dependency.get("error"),
        "runtimeEnv": env_values,
        "runtimeEnvHash": sha256_value(env_values),
        "secretPresence": secret_presence,
        "liveProcess": {
            "pid": runtime.get("pid") if runtime else None,
            "cwd": runtime.get("cwd") if runtime else None,
            "exe": runtime.get("exe") if runtime else None,
            "cmdlineHash": sha256_value(runtime.get("cmdline")) if runtime else None,
        },
    }
    payload["pythonIdentityHash"] = sha256_value({
        "python": payload["python"],
        "pythonVersion": payload["pythonVersion"],
        "dependencyHash": payload["dependencyHash"],
        "requirementsLockHash": payload["requirementsLockHash"],
    })
    payload["runtimeIdentityHash"] = sha256_value({
        "pythonIdentityHash": payload["pythonIdentityHash"],
        "runtimeEnvHash": payload["runtimeEnvHash"],
        "liveProcess": payload["liveProcess"],
    })
    return payload


def build_report(args: argparse.Namespace) -> Dict[str, Any]:
    root = Path(args.root).resolve()
    config_path = root / args.config
    findings: List[str] = []
    if not config_path.is_file():
        raise RuntimeError("pilot_config_missing:{0}".format(config_path))
    config = load_json(config_path)
    if config.get("pilotOnly") is not True:
        findings.append("pilot_contract_not_marked_pilot_only")

    repository = repository_identity(root, config, args.expected_source_commit or "", findings)
    if args.expected_repository_gate_hash and repository["repositoryGateHash"] != args.expected_repository_gate_hash:
        findings.append(
            "repository_gate_hash_mismatch:{0}:{1}".format(
                args.expected_repository_gate_hash,
                repository["repositoryGateHash"],
            )
        )

    runtime: Dict[str, Any] | None = None
    database: Dict[str, Any] | None = None
    if args.mode in ("ecs-candidate", "ecs-runtime"):
        deploy_root = Path(args.deploy_root).resolve()
        runtime = runtime_identity(
            mode=args.mode,
            root=root,
            config=config,
            deploy_root=deploy_root,
            findings=findings,
        )
        db_relative = str((config.get("database") or {}).get("defaultRelativePath") or "shared/logs/product_workbench.sqlite3")
        db_path = Path(args.database).resolve() if args.database else (deploy_root / db_relative).resolve()
        database = database_identity(
            db_path,
            (config.get("database") or {}).get("stateTables") or [],
            findings,
        )

    source_component = {
        "expectedSourceCommit": args.expected_source_commit or None,
        "sourceIdentityHash": repository["sourceIdentityHash"],
        "repositoryGateHash": repository["repositoryGateHash"],
    }
    runtime_component = {
        "runtimeIdentityHash": runtime.get("runtimeIdentityHash") if runtime else None,
        "pythonIdentityHash": runtime.get("pythonIdentityHash") if runtime else None,
        "dependencyHash": runtime.get("dependencyHash") if runtime else None,
        "runtimeEnvHash": runtime.get("runtimeEnvHash") if runtime else None,
        "runtimeCallableAuthorityHash": repository["runtimeCallableAuthority"].get("hash"),
    }
    database_component = {
        "databaseSchemaHash": database.get("databaseSchemaHash") if database else None,
        "databaseStateHash": database.get("databaseStateHash") if database else None,
    }
    execution_hash = sha256_value({
        "source": source_component,
        "runtime": runtime_component,
        "database": database_component,
    })
    report = {
        "schema": "competition.execution_identity_gate.report.v1",
        "version": VERSION,
        "mode": args.mode,
        "pilotOnly": True,
        "root": str(root),
        "expectedSourceCommit": args.expected_source_commit or None,
        "releaseHash": args.release_hash or None,
        "repository": repository,
        "runtime": runtime,
        "database": database,
        "executionIdentityHash": execution_hash,
        "verified": not findings,
        "findings": findings,
        "generatedAtEpoch": int(time.time()),
    }
    report["gateReportHash"] = sha256_value({
        "mode": report["mode"],
        "expectedSourceCommit": report["expectedSourceCommit"],
        "releaseHash": report["releaseHash"],
        "repositoryGateHash": repository["repositoryGateHash"],
        "runtimeIdentityHash": runtime.get("runtimeIdentityHash") if runtime else None,
        "databaseSchemaHash": database.get("databaseSchemaHash") if database else None,
        "databaseStateHash": database.get("databaseStateHash") if database else None,
        "executionIdentityHash": execution_hash,
        "verified": report["verified"],
        "findings": findings,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("repository", "ecs-candidate", "ecs-runtime"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--deploy-root", default="/opt/ai-ecommerce-assistant")
    parser.add_argument("--database")
    parser.add_argument("--expected-source-commit", default="")
    parser.add_argument("--expected-repository-gate-hash", default="")
    parser.add_argument("--release-hash", default="")
    parser.add_argument("--output")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
