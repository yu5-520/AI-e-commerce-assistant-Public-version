#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${AI_ECOMMERCE_ROOT:-/opt/ai-ecommerce-assistant}"
BUNDLE="${AI_RELEASE_BUNDLE:-${1:-}}"
VERIFIER="${AI_RELEASE_VERIFIER_PATH:-/usr/local/sbin/ai-release-verifier}"
VERIFIER_HASH_FILE="${AI_RELEASE_VERIFIER_HASH_FILE:-/etc/ai-ecommerce-assistant/release-verifier.sha256}"
KEEP_RELEASES="${AI_RELEASE_KEEP_COUNT:-2}"
BACKUP_KEEP_COUNT="${AI_ECOMMERCE_BACKUP_KEEP_COUNT:-1}"
MIN_FREE_BYTES="${AI_DEPLOYMENT_MIN_FREE_BYTES:-536870912}"
RELEASE_IDENTITY_ATTEMPTS="${AI_RELEASE_IDENTITY_ATTEMPTS:-6}"
RELEASE_IDENTITY_TIMEOUT="${AI_RELEASE_IDENTITY_TIMEOUT:-20}"
DATA_IDENTITY_ATTEMPTS="${AI_DATA_IDENTITY_ATTEMPTS:-3}"
DATA_IDENTITY_TIMEOUT="${AI_DATA_IDENTITY_TIMEOUT:-180}"
ROLLBACK_HEALTH_ATTEMPTS="${AI_ROLLBACK_HEALTH_ATTEMPTS:-20}"

log() { printf '\n=== %s ===\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

select_base_python() {
  local candidate version
  if [ -n "${AI_RELEASE_PYTHON:-}" ]; then
    candidates=("$AI_RELEASE_PYTHON")
  else
    candidates=(python3.11)
  fi
  for candidate in "${candidates[@]}"; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    version="$("$candidate" -c 'import platform; print(platform.python_version())')"
    [[ "$version" == 3.11.* ]] || continue
    command -v "$candidate"
    return 0
  done
  return 1
}

fetch_json_with_retry() {
  local label="$1"
  local url="$2"
  local attempts="$3"
  local timeout="$4"
  local attempt payload

  for attempt in $(seq 1 "$attempts"); do
    if payload="$(curl -fsS --connect-timeout 5 --max-time "$timeout" "$url")"; then
      printf '%s' "$payload"
      return 0
    fi
    printf '%s attempt %s/%s failed; retrying in 2 seconds\n' "$label" "$attempt" "$attempts" >&2
    sleep 2
  done
  return 1
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run deploy_release.sh as root"
[ -n "$BUNDLE" ] || fail "Set AI_RELEASE_BUNDLE or pass the release tar.gz/directory as argument"
[ -e "$BUNDLE" ] || fail "Release bundle not found: $BUNDLE"
[[ "$KEEP_RELEASES" =~ ^[1-9][0-9]*$ ]] || fail "AI_RELEASE_KEEP_COUNT must be a positive integer"
[[ "$BACKUP_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] || fail "AI_ECOMMERCE_BACKUP_KEEP_COUNT must be a positive integer"
[[ "$MIN_FREE_BYTES" =~ ^[0-9]+$ ]] || fail "AI_DEPLOYMENT_MIN_FREE_BYTES must be a non-negative integer"
[[ "$RELEASE_IDENTITY_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || fail "AI_RELEASE_IDENTITY_ATTEMPTS must be a positive integer"
[[ "$RELEASE_IDENTITY_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || fail "AI_RELEASE_IDENTITY_TIMEOUT must be a positive integer"
[[ "$DATA_IDENTITY_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || fail "AI_DATA_IDENTITY_ATTEMPTS must be a positive integer"
[[ "$DATA_IDENTITY_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || fail "AI_DATA_IDENTITY_TIMEOUT must be a positive integer"
[[ "$ROLLBACK_HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || fail "AI_ROLLBACK_HEALTH_ATTEMPTS must be a positive integer"

ROOT_DIR="$(readlink -m "$ROOT_DIR")"
BUNDLE="$(readlink -f "$BUNDLE")"
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
case "$BUNDLE" in
  "$ROOT_DIR"|"$ROOT_DIR"/*) fail "Release bundle must be outside AI_ECOMMERCE_ROOT" ;;
esac
case "$SCRIPT_PATH" in
  "$ROOT_DIR"/*) fail "Run deploy_release.sh from the extracted candidate outside AI_ECOMMERCE_ROOT" ;;
esac

[ -x "$VERIFIER" ] || fail "Root verifier is missing. Run the candidate scripts/install_release_verifier.sh first"
[ -f "$VERIFIER_HASH_FILE" ] || fail "Pinned verifier hash is missing: $VERIFIER_HASH_FILE"
[ ! -L "$VERIFIER" ] || fail "Pinned root verifier must not be a symlink"
[ ! -L "$VERIFIER_HASH_FILE" ] || fail "Pinned verifier hash record must not be a symlink"
EXPECTED_VERIFIER_HASH="$(tr -d '[:space:]' < "$VERIFIER_HASH_FILE")"
ACTUAL_VERIFIER_HASH="$(sha256sum "$VERIFIER" | awk '{print $1}')"
[[ "$EXPECTED_VERIFIER_HASH" =~ ^[0-9a-f]{64}$ ]] || fail "Pinned verifier SHA256 is invalid"
[ "$EXPECTED_VERIFIER_HASH" = "$ACTUAL_VERIFIER_HASH" ] || fail "Root verifier hash mismatch"

mkdir -p "$ROOT_DIR/releases" "$ROOT_DIR/shared"
find "$ROOT_DIR/releases" -mindepth 1 -maxdepth 1 -type d -name '.incoming-*' -exec rm -rf {} + 2>/dev/null || true
INCOMING="$ROOT_DIR/releases/.incoming-$$"
mkdir -p "$INCOMING"
SERVICE=""
PORT=""
SERVICE_WAS_ACTIVE=false
SERVICE_STOPPED_BY_THIS_RUN=false
SWITCHED=false
PREVIOUS_TARGET=""
TARGET=""
TARGET_CREATED_BY_THIS_RUN=false
DROPIN_FILE=""
DROPIN_BACKUP=""
DROPIN_EXISTED=false
LIVE_DB=""
BACKUP_PATH=""
BACKUP_CONTENT_HASH=""
DATA_LINEAGE_PATH=""
LINEAGE_BACKUP=""
LINEAGE_EXISTED=false
DATA_LINEAGE_TOUCHED=false

cleanup() {
  rm -rf "$INCOMING"
  [ -z "$DROPIN_BACKUP" ] || rm -f "$DROPIN_BACKUP"
  [ -z "$LINEAGE_BACKUP" ] || rm -f "$LINEAGE_BACKUP"
}

restore_original_service_binding() {
  if [ -z "$DROPIN_FILE" ]; then return 0; fi
  if [ "$DROPIN_EXISTED" = true ] && [ -n "$DROPIN_BACKUP" ] && [ -f "$DROPIN_BACKUP" ]; then
    cp -p "$DROPIN_BACKUP" "$DROPIN_FILE" || true
  else
    rm -f "$DROPIN_FILE" || true
  fi
}

restore_database_and_lineage() {
  local restored_db=false restored_lineage=false
  if [ -n "$BACKUP_PATH" ] && [ -s "$BACKUP_PATH" ] && [ -n "$LIVE_DB" ]; then
    if python3 - "$BACKUP_PATH" "$LIVE_DB" <<'PY'
import os
import shutil
import sqlite3
import sys

source, target = sys.argv[1:3]
temporary = target + ".rollback.tmp"
for path in (temporary, target + "-wal", target + "-shm"):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
shutil.copy2(source, temporary)
connection = sqlite3.connect(temporary)
try:
    quick = connection.execute("PRAGMA quick_check").fetchone()[0]
finally:
    connection.close()
if quick != "ok":
    raise SystemExit("rollback backup quick_check failed: " + str(quick))
os.replace(temporary, target)
PY
    then
      restored_db=true
    else
      printf 'ROLLBACK_DATABASE_RESTORED=false backup=%s target=%s\n' "$BACKUP_PATH" "$LIVE_DB" >&2
    fi
  fi

  if [ -n "$DATA_LINEAGE_PATH" ]; then
    if [ "$LINEAGE_EXISTED" = true ] && [ -n "$LINEAGE_BACKUP" ] && [ -f "$LINEAGE_BACKUP" ]; then
      cp -p "$LINEAGE_BACKUP" "$DATA_LINEAGE_PATH" && restored_lineage=true || true
    elif [ "$DATA_LINEAGE_TOUCHED" = true ]; then
      rm -f "$DATA_LINEAGE_PATH" && restored_lineage=true || true
    fi
  fi
  printf 'ROLLBACK_DATABASE_RESTORED=%s ROLLBACK_LINEAGE_RESTORED=%s\n' "$restored_db" "$restored_lineage" >&2
}

wait_for_rollback_health() {
  local attempt
  [ -n "$PORT" ] || return 1
  for attempt in $(seq 1 "$ROLLBACK_HEALTH_ATTEMPTS"); do
    if curl -fsS --connect-timeout 3 --max-time 5 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

rollback_on_error() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ] && [ -n "$SERVICE" ]; then
    if [ "$SERVICE_STOPPED_BY_THIS_RUN" = true ] || [ "$SWITCHED" = true ]; then
      systemctl stop "$SERVICE" >/dev/null 2>&1 || true
    fi
    if [ "$SWITCHED" = true ] && [ -n "$PREVIOUS_TARGET" ] && [ -d "$PREVIOUS_TARGET" ]; then
      ln -sfn "$PREVIOUS_TARGET" "$ROOT_DIR/current" || true
    elif [ "$SWITCHED" = true ]; then
      rm -f "$ROOT_DIR/current" || true
      restore_original_service_binding
    fi
    restore_database_and_lineage
    if [ "$SERVICE_WAS_ACTIVE" = true ]; then
      systemctl daemon-reload || true
      if systemctl start "$SERVICE"; then
        if wait_for_rollback_health; then
          printf 'ROLLBACK_SERVICE_RESTORED=true service=%s port=%s\n' "$SERVICE" "$PORT" >&2
        else
          printf 'ROLLBACK_SERVICE_RESTORED=false reason=health_timeout service=%s port=%s\n' "$SERVICE" "$PORT" >&2
          systemctl status "$SERVICE" --no-pager -l >&2 || true
          journalctl -u "$SERVICE" -n 120 --no-pager >&2 || true
        fi
      else
        printf 'ROLLBACK_SERVICE_RESTORED=false reason=systemctl_start_failed service=%s\n' "$SERVICE" >&2
        systemctl status "$SERVICE" --no-pager -l >&2 || true
        journalctl -u "$SERVICE" -n 120 --no-pager >&2 || true
      fi
    fi
  fi
  if [ "$exit_code" -ne 0 ] && [ "$TARGET_CREATED_BY_THIS_RUN" = true ] && [ -n "$TARGET" ] && [ -d "$TARGET" ]; then
    local live_target
    live_target="$(readlink -f "$ROOT_DIR/current" 2>/dev/null || true)"
    if [ "$TARGET" != "$live_target" ] && { [ -z "$PREVIOUS_TARGET" ] || [ "$TARGET" != "$PREVIOUS_TARGET" ]; }; then
      rm -rf "$TARGET" || true
    fi
  fi
  cleanup
  exit "$exit_code"
}
trap rollback_on_error EXIT

log "1. Extract immutable release candidate"
if [ -d "$BUNDLE" ]; then cp -a "$BUNDLE"/. "$INCOMING"/; else tar -xzf "$BUNDLE" -C "$INCOMING"; fi
[ -f "$INCOMING/release/release-manifest.json" ] || fail "release/release-manifest.json missing from bundle"
CANDIDATE_VERIFIER="$INCOMING/scripts/release_verifier.py"
[ -f "$CANDIDATE_VERIFIER" ] || fail "Release bundle verifier is missing"
[ ! -L "$CANDIDATE_VERIFIER" ] || fail "Release bundle verifier must not be a symlink"
CANDIDATE_VERIFIER_HASH="$(sha256sum "$CANDIDATE_VERIFIER" | awk '{print $1}')"
[ "$CANDIDATE_VERIFIER_HASH" = "$EXPECTED_VERIFIER_HASH" ] || fail "Release bundle verifier differs from the root-pinned verifier; complete an explicit root trust rotation before deployment"

log "2. Verify release DNA with the pinned root verifier"
VERIFY_JSON="$(python3 "$VERIFIER" --root "$INCOMING" --manifest "$INCOMING/release/release-manifest.json")" || { printf '%s\n' "$VERIFY_JSON" >&2; fail "Release verification failed"; }
printf '%s\n' "$VERIFY_JSON" | python3 -m json.tool
RELEASE_HASH="$(printf '%s' "$VERIFY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["releaseHash"])')"
SOURCE_COMMIT="$(printf '%s' "$VERIFY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sourceCommit"])')"
PRODUCT_VERSION="$(printf '%s' "$VERIFY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["productVersion"])')"
EXPECTED_RUNTIME_PYTHON="$(printf '%s' "$VERIFY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pythonVersion"])')"
EXPECTED_PIP_FREEZE_HASH="$(printf '%s' "$VERIFY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pipFreezeHash"])')"
RELEASE_DIR_NAME="${RELEASE_HASH#sha256:}"
TARGET="$ROOT_DIR/releases/$RELEASE_DIR_NAME"

log "3. Online cleanup and storage preflight before service downtime"
PREFLIGHT_PATH="$INCOMING/config/deployment/deployment_preflight.py"
[ -f "$PREFLIGHT_PATH" ] || fail "deployment_preflight.py missing from sealed candidate"
PREFLIGHT_JSON="$(python3 "$PREFLIGHT_PATH" \
  --root "$ROOT_DIR" \
  --active-candidate "$INCOMING" \
  --backup-keep-count "$BACKUP_KEEP_COUNT" \
  --min-free-bytes "$MIN_FREE_BYTES")" || {
  printf '%s\n' "$PREFLIGHT_JSON" | python3 -m json.tool || printf '%s\n' "$PREFLIGHT_JSON"
  fail "Deployment storage preflight failed before service downtime"
}
printf '%s\n' "$PREFLIGHT_JSON" | python3 -m json.tool

log "4. Materialize exact release directory"
if [ -e "$TARGET" ]; then
  EXISTING_JSON="$(python3 "$VERIFIER" --root "$TARGET" --manifest "$TARGET/release/release-manifest.json")" || fail "Existing release directory is corrupted: $TARGET"
  EXISTING_HASH="$(printf '%s' "$EXISTING_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["releaseHash"])')"
  [ "$EXISTING_HASH" = "$RELEASE_HASH" ] || fail "Release directory hash collision"
  rm -rf "$INCOMING"; mkdir -p "$INCOMING"
else
  mv "$INCOMING" "$TARGET"; mkdir -p "$INCOMING"
  TARGET_CREATED_BY_THIS_RUN=true
fi

log "5. Build exact shared Python environment before service downtime"
BASE_PYTHON="$(select_base_python)" || fail "Python 3.11 is required; set AI_RELEASE_PYTHON in the server deployment contract"
BASE_PYTHON_VERSION="$("$BASE_PYTHON" -c 'import platform; print(platform.python_version())')"
[ "$BASE_PYTHON_VERSION" = "$EXPECTED_RUNTIME_PYTHON" ] || fail "Release requires Python $EXPECTED_RUNTIME_PYTHON, server provides $BASE_PYTHON_VERSION"
LOCK_FILE="$TARGET/requirements.lock"
[ -f "$LOCK_FILE" ] || fail "Exact dependency lock is missing: $LOCK_FILE"
LOCK_HASH="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
LOCK_HASH_FILE="$ROOT_DIR/shared/.venv/.requirements-lock.sha256"
REBUILD_VENV=false
if [ ! -x "$ROOT_DIR/shared/.venv/bin/python" ]; then
  REBUILD_VENV=true
elif [ "$("$ROOT_DIR/shared/.venv/bin/python" -c 'import platform; print(platform.python_version())')" != "$EXPECTED_RUNTIME_PYTHON" ]; then
  REBUILD_VENV=true
elif [ "$(cat "$LOCK_HASH_FILE" 2>/dev/null || true)" != "$LOCK_HASH" ]; then
  REBUILD_VENV=true
elif ! "$ROOT_DIR/shared/.venv/bin/python" "$TARGET/scripts/check_dependency_lock.py" "$LOCK_FILE" --strict >/dev/null 2>&1; then
  REBUILD_VENV=true
fi
if [ "$REBUILD_VENV" = true ]; then
  rm -rf "$ROOT_DIR/shared/.venv"
  "$BASE_PYTHON" -m venv "$ROOT_DIR/shared/.venv"
  "$ROOT_DIR/shared/.venv/bin/python" -m pip install --disable-pip-version-check -r "$LOCK_FILE"
fi
ENVIRONMENT_JSON="$("$ROOT_DIR/shared/.venv/bin/python" "$TARGET/scripts/check_dependency_lock.py" "$LOCK_FILE" --strict)" || { printf '%s\n' "$ENVIRONMENT_JSON" >&2; fail "Production dependency closure verification failed"; }
printf '%s\n' "$ENVIRONMENT_JSON" | python3 -m json.tool
ACTUAL_RUNTIME_PYTHON="$(printf '%s' "$ENVIRONMENT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pythonVersion"])')"
ACTUAL_PIP_FREEZE_HASH="$(printf '%s' "$ENVIRONMENT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pipFreezeHash"])')"
[ "$ACTUAL_RUNTIME_PYTHON" = "$EXPECTED_RUNTIME_PYTHON" ] || fail "Runtime Python identity mismatch"
[ "$ACTUAL_PIP_FREEZE_HASH" = "$EXPECTED_PIP_FREEZE_HASH" ] || fail "Runtime dependency environment hash mismatch"
printf '%s\n' "$LOCK_HASH" > "$LOCK_HASH_FILE"

log "6. Resolve the one production service"
source "$TARGET/scripts/runtime_service_resolver.sh"
source "$TARGET/scripts/runtime_exclusivity_guard.sh"
SERVICE="$(resolve_ai_runtime_service "$ROOT_DIR")" || fail "No systemd runtime service is bound to $ROOT_DIR"
PORT="$(resolve_ai_runtime_port "$ROOT_DIR" "$SERVICE")"
if systemctl is-active --quiet "$SERVICE"; then SERVICE_WAS_ACTIVE=true; fi
if [ -L "$ROOT_DIR/current" ]; then PREVIOUS_TARGET="$(readlink -f "$ROOT_DIR/current" || true)"; fi
DROPIN_DIR="/etc/systemd/system/${SERVICE}.d"
DROPIN_FILE="$DROPIN_DIR/release-seal.conf"
mkdir -p "$DROPIN_DIR"
if [ -f "$DROPIN_FILE" ]; then DROPIN_EXISTED=true; DROPIN_BACKUP="$(mktemp /tmp/ai-release-seal-dropin.XXXXXX)"; cp -p "$DROPIN_FILE" "$DROPIN_BACKUP"; fi

log "7. Revoke every old runtime owner and forbidden legacy path"
systemctl stop "$SERVICE"
SERVICE_STOPPED_BY_THIS_RUN=true
retire_all_shadow_runtime_units "$ROOT_DIR" "$SERVICE"
retire_stray_repository_runtime_processes "$ROOT_DIR"
retire_forbidden_legacy_paths "$ROOT_DIR" "$TARGET/release/release-manifest.json"

log "8. Migrate shared state and create validated rollback backup"
STATE_MARKER="$ROOT_DIR/shared/.legacy-state-migrated"
if [ ! -f "$STATE_MARKER" ]; then
  for name in data logs outputs artifacts; do
    mkdir -p "$ROOT_DIR/shared/$name"
    if [ -d "$ROOT_DIR/$name" ] && [ ! -L "$ROOT_DIR/$name" ]; then cp -a "$ROOT_DIR/$name"/. "$ROOT_DIR/shared/$name"/; fi
  done
  if [ -f "$ROOT_DIR/.env" ] && [ ! -f "$ROOT_DIR/shared/.env" ]; then cp -p "$ROOT_DIR/.env" "$ROOT_DIR/shared/.env"; fi
  printf 'sourceCommit=%s\nreleaseHash=%s\n' "$SOURCE_COMMIT" "$RELEASE_HASH" > "$STATE_MARKER"
fi
for name in data logs outputs artifacts; do mkdir -p "$ROOT_DIR/shared/$name"; done
[ -f "$ROOT_DIR/shared/.env" ] || touch "$ROOT_DIR/shared/.env"

LIVE_DB="$ROOT_DIR/shared/logs/product_workbench.sqlite3"
DATA_LINEAGE_PATH="$ROOT_DIR/shared/data/release-data-lineage.json"
if [ -f "$DATA_LINEAGE_PATH" ]; then
  LINEAGE_EXISTED=true
  LINEAGE_BACKUP="$(mktemp /tmp/ai-release-data-lineage.XXXXXX)"
  cp -p "$DATA_LINEAGE_PATH" "$LINEAGE_BACKUP"
fi
if [ -f "$LIVE_DB" ]; then
  BACKUP_DIR="$ROOT_DIR/shared/logs/deployment_backups"
  BACKUP_FILENAME="product_workbench-pre-${RELEASE_DIR_NAME:0:16}-$(date +%Y%m%d-%H%M%S).sqlite3"
  mkdir -p "$BACKUP_DIR"
  BACKUP_JSON="$("$ROOT_DIR/shared/.venv/bin/python" "$TARGET/scripts/sqlite_backup_rotate.py" --source "$LIVE_DB" --backup-dir "$BACKUP_DIR" --prefix "product_workbench-pre-" --filename "$BACKUP_FILENAME" --keep "$BACKUP_KEEP_COUNT")"
  printf '%s' "$BACKUP_JSON" | python3 -m json.tool
  BACKUP_PATH="$(printf '%s' "$BACKUP_JSON" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p.get("status")=="completed",p; assert p.get("quickCheck")=="ok",p; print(p["backupPath"])')"
  [ -s "$BACKUP_PATH" ] || fail "Validated SQLite backup was not created"
  BACKUP_IDENTITY_JSON="$("$ROOT_DIR/shared/.venv/bin/python" "$TARGET/scripts/sqlite_data_identity.py" --database "$BACKUP_PATH" --content-hash)" || fail "SQLite backup identity generation failed"
  BACKUP_CONTENT_HASH="$(printf '%s' "$BACKUP_IDENTITY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["contentHash"])')"
fi

log "9. Attach shared state without modifying sealed code"
for name in data logs outputs artifacts; do rm -rf "$TARGET/$name"; ln -s "$ROOT_DIR/shared/$name" "$TARGET/$name"; done
rm -f "$TARGET/.env" "$TARGET/.venv"
ln -s "$ROOT_DIR/shared/.env" "$TARGET/.env"
ln -s "$ROOT_DIR/shared/.venv" "$TARGET/.venv"
find "$TARGET" -xdev -type f -exec chmod a-w {} +
chmod u+w "$ROOT_DIR/shared/.env" || true

log "10. Prepare final SQLite schema before sealing data lineage"
PYTHON="$ROOT_DIR/shared/.venv/bin/python"
SCHEMA_PREP_JSON="$(
  cd "$TARGET" &&
  PYTHONPATH="$TARGET" "$PYTHON" -m \
    src.services.runtime_database_prepare_v22511_service \
    --verify-idempotent
)" || {
  printf '%s\n' "$SCHEMA_PREP_JSON" | python3 -m json.tool || printf '%s\n' "$SCHEMA_PREP_JSON"
  fail "Runtime database schema preparation failed"
}
printf '%s\n' "$SCHEMA_PREP_JSON" | python3 -m json.tool
PREPARED_SCHEMA_HASH="$(printf '%s' "$SCHEMA_PREP_JSON" | "$PYTHON" -c 'import json,sys; p=json.load(sys.stdin); assert p.get("verified") is True,p; assert p.get("idempotent") is True,p; print(p["preparedSchemaHash"])')"
LIVE_IDENTITY_JSON="$("$PYTHON" "$TARGET/scripts/sqlite_data_identity.py" --database "$LIVE_DB")" || fail "Prepared live SQLite identity generation failed"
printf '%s' "$LIVE_IDENTITY_JSON" | PREPARED_SCHEMA_HASH="$PREPARED_SCHEMA_HASH" RELEASE_HASH="$RELEASE_HASH" SOURCE_COMMIT="$SOURCE_COMMIT" BACKUP_PATH="$BACKUP_PATH" BACKUP_CONTENT_HASH="$BACKUP_CONTENT_HASH" DATA_LINEAGE_PATH="$DATA_LINEAGE_PATH" "$PYTHON" -c '
import json,os,sys
from pathlib import Path
live=json.load(sys.stdin)
assert live.get("verified") is True,live
assert live.get("quickCheck")=="ok",live
assert live.get("schemaHash")==os.environ["PREPARED_SCHEMA_HASH"],live
payload={
 "schema":"release.data-lineage.v1",
 "sourceCommit":os.environ["SOURCE_COMMIT"],
 "releaseHash":os.environ["RELEASE_HASH"],
 "databasePath":live.get("databasePath"),
 "schemaHash":live.get("schemaHash"),
 "deploymentStateHash":live.get("stateHash"),
 "backupPath":os.environ.get("BACKUP_PATH") or None,
 "backupContentHash":os.environ.get("BACKUP_CONTENT_HASH") or None,
 "quickCheck":live.get("quickCheck"),
 "schemaPreparationVersion":"22.5.11",
 "schemaPreparedBeforeLineage":True,
 "preparedSchemaHash":os.environ["PREPARED_SCHEMA_HASH"],
}
path=Path(os.environ["DATA_LINEAGE_PATH"])
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
print(json.dumps(payload,ensure_ascii=False,indent=2))
'
DATA_LINEAGE_TOUCHED=true

log "11. Bind systemd to the sealed current release"
cat > "$DROPIN_FILE" <<EOF
[Service]
WorkingDirectory=$ROOT_DIR/current
ExecStart=
ExecStart=/bin/bash $ROOT_DIR/current/scripts/start_server.sh
Environment=AI_RELEASE_REQUIRED=1
Environment=AI_RELEASE_ROOT=$ROOT_DIR/current
Environment=AI_RELEASE_EXPECTED_PYTHON_VERSION=$EXPECTED_RUNTIME_PYTHON
Environment=AI_RELEASE_EXPECTED_PIP_FREEZE_HASH=$EXPECTED_PIP_FREEZE_HASH
Environment=INSTALL_DEPS_ON_START=never
EOF
ln -sfn "$TARGET" "$ROOT_DIR/current"
SWITCHED=true
systemctl daemon-reload
systemctl start "$SERVICE"

log "12. Verify API, Worker, environment, proof set and post-start data lineage"
if ! wait_for_layered_runtime "$PYTHON" "$PORT" "$PRODUCT_VERSION" "$PRODUCT_VERSION" 60; then
  systemctl status "$SERVICE" --no-pager -l || true
  journalctl -u "$SERVICE" -n 200 --no-pager || true
  fail "Release API did not become ready"
fi
assert_one_repository_runtime_unit "$ROOT_DIR" "$SERVICE"
IDENTITY_JSON="$(fetch_json_with_retry \
  "release identity" \
  "http://127.0.0.1:${PORT}/api/system/release-identity?verifyContent=false" \
  "$RELEASE_IDENTITY_ATTEMPTS" \
  "$RELEASE_IDENTITY_TIMEOUT")" || {
  systemctl status "$SERVICE" --no-pager -l || true
  journalctl -u "$SERVICE" -n 200 --no-pager || true
  fail "Cached release identity health check did not become ready"
}
printf '%s' "$IDENTITY_JSON" | RELEASE_HASH="$RELEASE_HASH" SOURCE_COMMIT="$SOURCE_COMMIT" EXPECTED_RUNTIME_PYTHON="$EXPECTED_RUNTIME_PYTHON" EXPECTED_PIP_FREEZE_HASH="$EXPECTED_PIP_FREEZE_HASH" "$PYTHON" -c '
import json,os,sys
v=json.load(sys.stdin)
assert v.get("verified") is True,v
assert v.get("verificationDepth")=="startup_verified_cache",v
assert v.get("contentVerificationRequested") is False,v
assert v.get("evidenceSemanticVerified") is True,v
assert v.get("releaseHash")==os.environ["RELEASE_HASH"],v
assert v.get("sourceCommit")==os.environ["SOURCE_COMMIT"],v
assert v.get("workerReleaseMatch") is True,v
assert v.get("runtimePythonVersion")==os.environ["EXPECTED_RUNTIME_PYTHON"],v
assert v.get("buildPythonVersion")==os.environ["EXPECTED_RUNTIME_PYTHON"],v
assert v.get("pipFreezeHash")==os.environ["EXPECTED_PIP_FREEZE_HASH"],v
assert v.get("runtimePipFreezeHash")==os.environ["EXPECTED_PIP_FREEZE_HASH"],v
assert v.get("runtimeEnvironmentMatch") is True,v
assert v.get("testRunHash")==v.get("calculatedTestRunHash"),v
assert v.get("verifiedFileCount")==v.get("manifestFileCount") and v.get("manifestFileCount",0)>0,v
assert v.get("verifiedAttestedFileCount")==v.get("attestedFileCount") and v.get("attestedFileCount",0)>0,v
assert v.get("verifiedTestEvidenceFileCount")==v.get("testEvidenceFileCount") and v.get("testEvidenceFileCount",0)>0,v
for key in ("extraRuntimeFileCount","extraAttestedFileCount","extraTestEvidenceFileCount","manifestRuntimeFileOutsidePolicyCount","manifestAttestedFileOutsidePolicyCount","manifestTestEvidenceFileMissingCount"): assert v.get(key)==0,v
'
DATA_IDENTITY_JSON="$(fetch_json_with_retry \
  "data identity" \
  "http://127.0.0.1:${PORT}/api/system/data-identity?contentHash=false" \
  "$DATA_IDENTITY_ATTEMPTS" \
  "$DATA_IDENTITY_TIMEOUT")" || {
  systemctl status "$SERVICE" --no-pager -l || true
  journalctl -u "$SERVICE" -n 200 --no-pager || true
  fail "Runtime data identity health check did not become ready"
}
printf '%s' "$DATA_IDENTITY_JSON" | RELEASE_HASH="$RELEASE_HASH" SOURCE_COMMIT="$SOURCE_COMMIT" PREPARED_SCHEMA_HASH="$PREPARED_SCHEMA_HASH" "$PYTHON" -c '
import json,os,sys
v=json.load(sys.stdin)
assert v.get("verified") is True,v
assert v.get("releaseMatch") is True,v
assert v.get("releaseHash")==os.environ["RELEASE_HASH"],v
assert v.get("sourceCommit")==os.environ["SOURCE_COMMIT"],v
assert v.get("schemaMatch") is True,v
assert (v.get("database") or {}).get("schemaHash")==os.environ["PREPARED_SCHEMA_HASH"],v
assert (v.get("lineage") or {}).get("schemaPreparedBeforeLineage") is True,v
assert (v.get("lineage") or {}).get("preparedSchemaHash")==os.environ["PREPARED_SCHEMA_HASH"],v
assert (v.get("database") or {}).get("quickCheck")=="ok",v
print(json.dumps(v,ensure_ascii=False,indent=2))
' || fail "Runtime data identity mismatch after deterministic schema preparation"

log "13. Install repository-owned deployment bootstrap"
BOOTSTRAP_INSTALLER="$TARGET/config/deployment/install_deploy_bootstrap.sh"
[ -f "$BOOTSTRAP_INSTALLER" ] || fail "install_deploy_bootstrap.sh missing from sealed candidate"
AI_DEPLOY_BOOTSTRAP_SOURCE_ROOT="$TARGET" bash "$BOOTSTRAP_INSTALLER"

log "14. Retain only current and previous sealed releases"
mapfile -t RELEASE_DIRS < <(find "$ROOT_DIR/releases" -mindepth 1 -maxdepth 1 -type d ! -name '.incoming-*' -printf '%T@ %p\n' | sort -nr | awk '{print $2}')
for old in "${RELEASE_DIRS[@]:$KEEP_RELEASES}"; do
  [ "$old" = "$(readlink -f "$ROOT_DIR/current")" ] && continue
  [ -n "$PREVIOUS_TARGET" ] && [ "$old" = "$PREVIOUS_TARGET" ] && continue
  rm -rf "$old"
done
if systemctl list-unit-files nginx.service >/dev/null 2>&1; then systemctl reload nginx || true; fi
SERVICE_WAS_ACTIVE=false
SERVICE_STOPPED_BY_THIS_RUN=false
TARGET_CREATED_BY_THIS_RUN=false
trap - EXIT

log "15. Retire the old mutable repository working tree"
retire_legacy_working_tree_after_success "$ROOT_DIR"
cleanup
printf '\nDeployed product=%s commit=%s releaseHash=%s verifierHash=%s python=%s service=%s port=%s\n' "$PRODUCT_VERSION" "$SOURCE_COMMIT" "$RELEASE_HASH" "$EXPECTED_VERIFIER_HASH" "$EXPECTED_RUNTIME_PYTHON" "$SERVICE" "$PORT"
[ -z "$BACKUP_PATH" ] || printf 'Rollback database backup: %s\nBackup content hash: %s\n' "$BACKUP_PATH" "$BACKUP_CONTENT_HASH"
printf 'Prepared schema hash: %s\n' "$PREPARED_SCHEMA_HASH"
printf 'Data lineage: %s\n' "$DATA_LINEAGE_PATH"
printf 'Deployment bootstrap: /usr/local/libexec/ai-ecommerce/deploy-bootstrap\n'
