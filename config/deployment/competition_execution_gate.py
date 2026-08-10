#!/usr/bin/env python3
"""Competition Runtime Verification Pilot (stdlib only, no application import)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

VERSION = "2026.08.10.6"
DEFAULT_CONFIG = "config/deployment/runtime_verification_pilot_v1.json"
AUTHORITY_CONFIG = "config/deployment/runtime_callable_authority_v1.json"


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def hvalue(value):
    return "sha256:" + hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def hfile(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def load_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("json_object_required:{0}".format(path))
    return value


def source_hashes(root, paths, findings):
    result = {}
    for raw in paths:
        rel = str(raw)
        path = root / rel
        try:
            exists = path.is_file()
        except OSError as exc:
            findings.append("source_file_unreadable:{0}:{1}".format(rel, type(exc).__name__))
            continue
        if not exists:
            findings.append("missing_source_file:{0}".format(rel))
            continue
        result[rel] = hfile(path)
    return result


def callable_authority(root, findings):
    spec_path = root / AUTHORITY_CONFIG
    if not spec_path.is_file():
        findings.append("runtime_callable_authority_projection_missing")
        return {"verified": False, "hash": hvalue({"missing": True}), "scan": []}
    spec = load_object(spec_path)
    scan = []
    bridge = root / "src/services/hard_interface_bridge_v2301_service.py"
    bridge_text = bridge.read_text(encoding="utf-8", errors="replace") if bridge.is_file() else ""
    for item in spec.get("callables") or []:
        owner_rel = str(item.get("ownerPath") or "")
        owner = root / owner_rel
        if not owner.is_file():
            findings.append("callable_owner_missing:{0}".format(owner_rel))
            continue
        owner_text = owner.read_text(encoding="utf-8", errors="replace")
        function = str(item.get("ownerFunction") or "")
        if function and "def {0}(".format(function) not in owner_text:
            findings.append("callable_owner_function_missing:{0}:{1}".format(owner_rel, function))
        for literal in item.get("forbiddenRebinds") or []:
            if str(literal) in bridge_text:
                findings.append("forbidden_runtime_rebind:{0}".format(literal))
        scan.append({
            "callableId": item.get("callableId"),
            "ownerPath": owner_rel,
            "ownerHash": hfile(owner),
        })
    legacy = spec.get("legacyOverlay") or {}
    if legacy.get("mutationAllowed") is False:
        overlay_rel = str(legacy.get("path") or "")
        literal = str(legacy.get("forbiddenMutationLiteral") or "")
        overlay = root / overlay_rel
        text = overlay.read_text(encoding="utf-8", errors="replace") if overlay.is_file() else ""
        if literal and literal in text:
            findings.append("forbidden_contract_mutation:{0}".format(overlay_rel))
    return {
        "verified": not any(
            value.startswith(("callable_", "forbidden_runtime_rebind", "forbidden_contract_mutation"))
            for value in findings
        ),
        "hash": hvalue(scan),
        "scan": scan,
    }


def repository_identity(root, config, expected_commit, findings):
    source_hashes(root, config.get("requiredSourceFiles") or [], findings)
    hashes = source_hashes(root, config.get("sourceIdentityFiles") or [], findings)
    authority = callable_authority(root, findings)

    config_paths = [str(value) for value in (config.get("runtimeConfigFiles") or [])]
    config_hashes = {}
    for rel in config_paths:
        value = hashes.get(rel)
        if value is None:
            path = root / rel
            if path.is_file():
                value = hfile(path)
            else:
                findings.append("config_identity_file_missing:{0}".format(rel))
                continue
        config_hashes[rel] = value
    config_hash = hvalue(config_hashes)

    source = {
        "expectedSourceCommit": expected_commit or None,
        "files": hashes,
        "configHash": config_hash,
        "runtimeCallableAuthorityHash": authority["hash"],
    }
    source_hash = hvalue(source)
    repo_hash = hvalue({
        "schema": "competition.repository_gate.v1",
        "sourceIdentityHash": source_hash,
        "sourceIdentityFileCount": len(hashes),
        "configHash": config_hash,
        "runtimeCallableAuthorityHash": authority["hash"],
    })
    return {
        "sourceIdentityHash": source_hash,
        "repositoryGateHash": repo_hash,
        "configHash": config_hash,
        "configFileHashes": config_hashes,
        "fileHashes": hashes,
        "runtimeCallableAuthority": authority,
    }


def _empty_database_identity(path, quick, marker):
    return {
        "path": str(path),
        "exists": quick not in ("missing",),
        "quickCheck": quick,
        "dataVersion": None,
        "migrationHead": None,
        "migrationHeadSource": None,
        "databaseSchemaHash": hvalue({marker: True}),
        "databaseStateHash": hvalue({marker: True}),
        "state": {},
    }


def _active_data_version(conn, known, database_config):
    table = str((database_config or {}).get("activeDataVersionTable") or "imported_report_rows")
    column = str((database_config or {}).get("activeDataVersionColumn") or "data_version")
    if table not in known or not re.match(r"^[A-Za-z0-9_]+$", table) or not re.match(r"^[A-Za-z0-9_]+$", column):
        return None
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info({0})".format(table)).fetchall()}
    if column not in columns:
        return None
    row = conn.execute(
        "SELECT {0} AS data_version, MAX(rowid) AS last_rowid FROM {1} "
        "WHERE {0} IS NOT NULL AND TRIM({0}) != '' GROUP BY {0} "
        "ORDER BY last_rowid DESC LIMIT 1".format(column, table)
    ).fetchone()
    return str(row["data_version"]) if row and row["data_version"] else None


def _migration_head(conn, known, database_config):
    table = str((database_config or {}).get("migrationTable") or "alembic_version")
    column = str((database_config or {}).get("migrationColumn") or "version_num")
    if table in known and re.match(r"^[A-Za-z0-9_]+$", table) and re.match(r"^[A-Za-z0-9_]+$", column):
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info({0})".format(table)).fetchall()}
        if column in columns:
            row = conn.execute("SELECT {0} FROM {1} ORDER BY rowid DESC LIMIT 1".format(column, table)).fetchone()
            if row and row[0] is not None:
                return str(row[0]), "table:{0}.{1}".format(table, column)
    if (database_config or {}).get("sqliteUserVersionFallback", True):
        row = conn.execute("PRAGMA user_version").fetchone()
        value = int(row[0] if row else 0)
        return "sqlite:user_version:{0}".format(value), "pragma:user_version"
    return None, None


def database_identity(path, tables, findings, database_config=None):
    try:
        exists = path.is_file()
    except OSError as exc:
        findings.append("database_permission_denied:{0}".format(type(exc).__name__))
        return _empty_database_identity(path, "permission_denied", "permissionDenied")
    if not exists:
        findings.append("database_missing:{0}".format(path))
        return _empty_database_identity(path, "missing", "missing")
    try:
        conn = sqlite3.connect("file:{0}?mode=ro".format(path.resolve()), uri=True, timeout=20)
    except Exception as exc:
        findings.append("database_open_failed:{0}".format(type(exc).__name__))
        return _empty_database_identity(path, "open_failed", "openFailed")
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
        schema = [dict(item) for item in schema_rows]
        known = {str(item["name"]) for item in schema_rows if str(item["type"]) == "table"}
        state = {}
        for raw in tables:
            table = str(raw)
            if table not in known or not re.match(r"^[A-Za-z0-9_]+$", table):
                continue
            count = int(conn.execute("SELECT COUNT(*) FROM {0}".format(table)).fetchone()[0])
            columns = {str(item[1]) for item in conn.execute("PRAGMA table_info({0})".format(table)).fetchall()}
            latest = None
            for candidate in ("updated_at", "created_at", "id"):
                if candidate in columns:
                    latest_row = conn.execute("SELECT MAX({0}) FROM {1}".format(candidate, table)).fetchone()
                    latest = latest_row[0] if latest_row else None
                    break
            state[table] = {"count": count, "latest": latest}

        data_version = _active_data_version(conn, known, database_config or {})
        migration_head, migration_source = _migration_head(conn, known, database_config or {})
        state_identity = {
            "dataVersion": data_version,
            "tables": state,
        }
        return {
            "path": str(path),
            "exists": True,
            "quickCheck": quick,
            "dataVersion": data_version,
            "migrationHead": migration_head,
            "migrationHeadSource": migration_source,
            "databaseSchemaHash": hvalue(schema),
            "databaseStateHash": hvalue(state_identity),
            "state": state,
        }
    finally:
        conn.close()


def env_identity(config, env):
    values = {str(key): env.get(str(key)) for key in (config.get("runtimeEnvironmentKeys") or []) if str(key) in env}
    presence = {str(key): bool(env.get(str(key))) for key in (config.get("secretPresenceKeys") or [])}
    return values, presence


def command_output(command, timeout=60):
    try:
        proc = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return proc.stdout.strip(), None
    except Exception as exc:
        return "", type(exc).__name__


def dependency_identity(python):
    version, version_error = command_output([python, "-c", "import platform; print(platform.python_version())"], 15)
    freeze, freeze_error = command_output([python, "-m", "pip", "freeze", "--all"], 60)
    lines = sorted(line.strip() for line in freeze.splitlines() if line.strip())
    return {
        "python": python,
        "pythonVersion": version or None,
        "pipFreezeHash": hvalue(lines),
        "packageCount": len(lines),
        "versionProbeError": version_error,
        "dependencyProbeError": freeze_error,
    }


def proc_env(pid):
    result = {}
    raw = Path("/proc/{0}/environ".format(pid)).read_bytes()
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        result[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return result


def locate_live(config):
    needles = [str(value) for value in ((config.get("runtime") or {}).get("processNeedles") or ["uvicorn", "src.api.main:app"])]
    matches = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            if not all(needle in command for needle in needles):
                continue
            matches.append({
                "pid": int(entry.name),
                "cmdline": command,
                "cwd": str((entry / "cwd").resolve()),
                "exe": str((entry / "exe").resolve()),
                "env": proc_env(int(entry.name)),
            })
        except Exception:
            continue
    matches.sort(key=lambda item: item["pid"], reverse=True)
    return matches[0] if matches else None


def executable(path):
    return bool(path and Path(path).is_file() and os.access(str(path), os.X_OK))


def choose_python(live, fallback):
    if not live:
        return fallback
    env = live.get("env") or {}
    venv = str(env.get("VIRTUAL_ENV") or "").strip()
    if venv:
        for name in ("python", "python3", "python3.11"):
            candidate = str(Path(venv) / "bin" / name)
            if executable(candidate):
                return candidate

    try:
        argv0 = shlex.split(str(live.get("cmdline") or ""))[0]
    except Exception:
        argv0 = ""
    if argv0:
        if "/" in argv0 and executable(argv0):
            return argv0
        process_path = str(env.get("PATH") or "")
        for directory in process_path.split(":"):
            candidate = str(Path(directory) / argv0) if directory else ""
            if executable(candidate):
                return candidate

    process_path = str(env.get("PATH") or "")
    for directory in process_path.split(":"):
        for name in ("python", "python3", "python3.11"):
            candidate = str(Path(directory) / name) if directory else ""
            if executable(candidate):
                return candidate
    if live.get("exe"):
        return str(live["exe"])
    return fallback


def runtime_identity(mode, root, config, deploy_root, findings, config_hash=None):
    live = None
    env = dict(os.environ)
    if mode == "ecs-runtime":
        live = locate_live(config)
        if not live:
            findings.append("live_runtime_process_missing")
        else:
            env = live["env"]
            current = deploy_root / "current"
            current_real = str(current.resolve()) if current.exists() or current.is_symlink() else None
            if (config.get("runtime") or {}).get("currentSymlinkMustMatchLiveCwd", True) and current_real != live["cwd"]:
                findings.append("current_symlink_live_cwd_mismatch:{0}:{1}".format(current_real, live["cwd"]))
            if str(root.resolve()) != live["cwd"]:
                findings.append("gate_root_live_cwd_mismatch:{0}:{1}".format(root.resolve(), live["cwd"]))
    fallback = os.environ.get("AI_RELEASE_PYTHON") or os.environ.get("AI_BOOTSTRAP_PYTHON") or sys.executable
    python = choose_python(live, fallback)
    dependency = dependency_identity(python)
    required = str((config.get("runtime") or {}).get("requiredPythonMajorMinor") or "")
    if required and not str(dependency.get("pythonVersion") or "").startswith(required + "."):
        findings.append("runtime_python_version_mismatch:{0}:{1}".format(required, dependency.get("pythonVersion")))
    if mode == "ecs-runtime" and dependency.get("dependencyProbeError"):
        findings.append("live_dependency_probe_failed:{0}".format(dependency["dependencyProbeError"]))
    values, secret_presence = env_identity(config, env)
    requirements = root / "requirements.lock"
    payload = {
        "mode": mode,
        "python": python,
        "pythonVersion": dependency.get("pythonVersion"),
        "requirementsLockHash": hfile(requirements) if requirements.is_file() else None,
        "dependencyHash": dependency.get("pipFreezeHash"),
        "packageCount": dependency.get("packageCount"),
        "dependencyProbeError": dependency.get("dependencyProbeError"),
        "runtimeEnv": values,
        "runtimeEnvHash": hvalue(values),
        "configHash": config_hash,
        "secretPresence": secret_presence,
        "liveProcess": {
            "pid": live.get("pid") if live else None,
            "cwd": live.get("cwd") if live else None,
            "exe": live.get("exe") if live else None,
            "cmdlineHash": hvalue(live.get("cmdline")) if live else None,
        },
    }
    payload["pythonIdentityHash"] = hvalue({
        "pythonVersion": payload["pythonVersion"],
        "dependencyHash": payload["dependencyHash"],
        "requirementsLockHash": payload["requirementsLockHash"],
    })
    payload["runtimeIdentityHash"] = hvalue({
        "pythonIdentityHash": payload["pythonIdentityHash"],
        "runtimeEnvHash": payload["runtimeEnvHash"],
        "configHash": payload["configHash"],
        "liveProcess": payload["liveProcess"],
    })
    return payload


def _identity_vector(config, args, repository, runtime, database):
    values = {
        "sourceCommit": args.expected_source_commit or None,
        "releaseHash": args.release_hash or None,
        "dataVersion": database.get("dataVersion") if database else None,
        "repositoryGateHash": repository["repositoryGateHash"],
        "pythonIdentityHash": runtime.get("pythonIdentityHash") if runtime else None,
        "dependencyHash": runtime.get("dependencyHash") if runtime else None,
        "runtimeEnvHash": runtime.get("runtimeEnvHash") if runtime else None,
        "configHash": repository.get("configHash"),
        "runtimeCallableAuthorityHash": repository["runtimeCallableAuthority"]["hash"],
        "databaseSchemaHash": database.get("databaseSchemaHash") if database else None,
        "migrationHead": database.get("migrationHead") if database else None,
        "databaseStateHash": database.get("databaseStateHash") if database else None,
    }
    order = [str(value) for value in ((config.get("identity") or {}).get("order") or values.keys())]
    return order, {name: values.get(name) for name in order}


def build_report(args):
    root = Path(args.root).resolve()
    config = load_object(root / args.config)
    findings = []
    if config.get("pilotOnly") is not True:
        findings.append("pilot_contract_not_marked_pilot_only")
    repository = repository_identity(root, config, args.expected_source_commit or "", findings)
    if args.expected_repository_gate_hash and repository["repositoryGateHash"] != args.expected_repository_gate_hash:
        findings.append("repository_gate_hash_mismatch:{0}:{1}".format(args.expected_repository_gate_hash, repository["repositoryGateHash"]))

    runtime = None
    database = None
    if args.mode in ("ecs-candidate", "ecs-runtime"):
        deploy_root = Path(args.deploy_root).resolve()
        runtime = runtime_identity(args.mode, root, config, deploy_root, findings, repository.get("configHash"))
        database_config = config.get("database") or {}
        relative = str(database_config.get("defaultRelativePath") or "shared/logs/product_workbench.sqlite3")
        db_path = Path(args.database).resolve() if args.database else (deploy_root / relative)
        database = database_identity(db_path, database_config.get("stateTables") or [], findings, database_config)

    identity_order, identity_vector = _identity_vector(config, args, repository, runtime, database)
    execution = hvalue([(name, identity_vector.get(name)) for name in identity_order])
    identity_vector["executionIdentityHash"] = execution

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
        "identityOrder": identity_order,
        "identityVector": identity_vector,
        "executionIdentityHash": execution,
        "verified": not findings,
        "findings": findings,
        "generatedAtEpoch": int(time.time()),
    }
    report["gateReportHash"] = hvalue({
        "mode": report["mode"],
        "identityOrder": identity_order,
        "identityVector": identity_vector,
        "verified": report["verified"],
        "findings": findings,
    })
    return report


def main():
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
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
