#!/usr/bin/env bash
set -euo pipefail

EXPECTED_SOURCE_COMMIT="778bdd91b3577a3c38c8f8ad011556f523b36221"
EXPECTED_ARTIFACT_ZIP_SHA256="91ce9c24657b858241ef7fc0f6c770dd9b1727da0e09829569f185b787220738"
SOURCE_ZIP="/tmp/competition-authorized-deploy-v2/release-candidate.zip"
STATE_ROOT="/var/lib/ai-ecommerce-authorized-deploy-v2"
WORK_ROOT="$STATE_ROOT/work"
RECEIPT_ROOT="$STATE_ROOT/receipts"
SELF_PATH="/usr/local/sbin/ai-competition-authorized-deploy-v2"
SELF_HASH_FILE="/etc/ai-ecommerce-assistant/competition-authorized-deploy-v2.sha256"
LOCK_FILE="/run/lock/ai-competition-authorized-deploy-v2.lock"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

[ "$#" -eq 0 ] || fail "This controller accepts no command-line arguments"
[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Root execution is required"
[ -f "$SELF_PATH" ] && [ ! -L "$SELF_PATH" ] || fail "Installed root controller is missing or unsafe"
[ -f "$SELF_HASH_FILE" ] && [ ! -L "$SELF_HASH_FILE" ] || fail "Root controller hash record is missing or unsafe"
[ "$(stat -c '%u:%g' "$SELF_PATH")" = "0:0" ] || fail "Root controller must be owned by root:root"
SELF_MODE="$(stat -c '%a' "$SELF_PATH")"
case "$SELF_MODE" in
  755|750|700) ;;
  *) fail "Root controller permissions are too broad: $SELF_MODE" ;;
esac
EXPECTED_SELF_HASH="$(tr -d '[:space:]' < "$SELF_HASH_FILE")"
ACTUAL_SELF_HASH="$(sha256sum "$SELF_PATH" | awk '{print $1}')"
[ "$EXPECTED_SELF_HASH" = "$ACTUAL_SELF_HASH" ] || fail "Root controller integrity check failed"

select_python() {
  local candidate version
  for candidate in \
    /opt/python/3.11.9/bin/python3.11 \
    /opt/ai-runtime/python/3.11.9/bin/python3.11 \
    /opt/ai-runtime/python/current/bin/python3.11
  do
    [ -x "$candidate" ] || continue
    version="$("$candidate" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
    [ "$version" = "3.11.9" ] || continue
    printf '%s\n' "$candidate"
    return 0
  done
  return 1
}

PYTHON="$(select_python)" || fail "Pinned Python 3.11.9 is required"
[ -f "$SOURCE_ZIP" ] && [ ! -L "$SOURCE_ZIP" ] || fail "Expected sealed artifact ZIP is missing: $SOURCE_ZIP"

install -d -o root -g root -m 0755 "$STATE_ROOT" "$RECEIPT_ROOT"
install -d -o root -g root -m 0700 "$WORK_ROOT"
install -d -o root -g root -m 0755 "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "Another authorized deployment is already running"

RUN_ROOT="$(mktemp -d "$WORK_ROOT/run.XXXXXXXX")"
cleanup() {
  rm -rf "$RUN_ROOT"
}
trap cleanup EXIT INT TERM

ROOT_ZIP="$RUN_ROOT/release-candidate.zip"
ARTIFACT_DIR="$RUN_ROOT/artifact"
CANDIDATE_DIR="$RUN_ROOT/candidate"
mkdir -p "$ARTIFACT_DIR" "$CANDIDATE_DIR"

# Copy first, then hash the root-private copy. This closes the runner-writable
# /tmp TOCTOU window before any candidate bytes are executed as root.
install -o root -g root -m 0600 "$SOURCE_ZIP" "$ROOT_ZIP"
ACTUAL_ZIP_SHA256="$(sha256sum "$ROOT_ZIP" | awk '{print $1}')"
[ "$ACTUAL_ZIP_SHA256" = "$EXPECTED_ARTIFACT_ZIP_SHA256" ] || fail "Sealed artifact ZIP SHA256 mismatch"

"$PYTHON" - "$ROOT_ZIP" "$ARTIFACT_DIR" <<'PY'
import pathlib
import stat
import sys
import zipfile

archive = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive) as zf:
    for info in zf.infolist():
        path = pathlib.PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe zip member: {info.filename}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise SystemExit(f"zip symlink rejected: {info.filename}")
    zf.extractall(dest)
PY

mapfile -t BUNDLES < <(
  find "$ARTIFACT_DIR/ci-artifacts" -maxdepth 1 -type f \
    -name "candidate-release-${EXPECTED_SOURCE_COMMIT}-*.tar.gz" -print | sort
)
[ "${#BUNDLES[@]}" -eq 1 ] || fail "Expected exactly one sealed candidate bundle"
ROOT_BUNDLE="$RUN_ROOT/sealed-candidate.tar.gz"
install -o root -g root -m 0600 "${BUNDLES[0]}" "$ROOT_BUNDLE"

"$PYTHON" - "$ROOT_BUNDLE" "$CANDIDATE_DIR" <<'PY'
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2]).resolve()
with tarfile.open(archive, "r:gz") as tf:
    members = tf.getmembers()
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe tar member: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise SystemExit(f"tar link/device rejected: {member.name}")
    tf.extractall(dest, members=members)
PY

for required in \
  "$CANDIDATE_DIR/release/release-manifest.json" \
  "$CANDIDATE_DIR/release/attestation/test-attestation.json" \
  "$CANDIDATE_DIR/scripts/release_verifier.py" \
  "$CANDIDATE_DIR/scripts/install_release_verifier.sh" \
  "$CANDIDATE_DIR/scripts/runtime_service_resolver.sh" \
  "$CANDIDATE_DIR/scripts/deploy_release.sh"
do
  [ -f "$required" ] && [ ! -L "$required" ] || fail "Required sealed candidate file missing or unsafe: $required"
done

"$PYTHON" "$CANDIDATE_DIR/scripts/release_verifier.py" \
  --root "$CANDIDATE_DIR" \
  --manifest "$CANDIDATE_DIR/release/release-manifest.json" \
  > "$RUN_ROOT/candidate-verification.json"

"$PYTHON" - \
  "$CANDIDATE_DIR/release/release-manifest.json" \
  "$CANDIDATE_DIR/release/attestation/test-attestation.json" \
  "$EXPECTED_SOURCE_COMMIT" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
attestation = json.load(open(sys.argv[2], encoding="utf-8"))
expected = sys.argv[3]
if manifest.get("sourceCommit") != expected:
    raise SystemExit("manifest sourceCommit mismatch")
if attestation.get("sourceCommit") != expected:
    raise SystemExit("attestation sourceCommit mismatch")
PY

# The resolver is now read only from the root-private, hash-pinned candidate copy.
# shellcheck disable=SC1090
source "$CANDIDATE_DIR/scripts/runtime_service_resolver.sh"

DEPLOY_ROOT=""
DEPLOY_SERVICE=""
for candidate_root in /opt/ai-ecommerce-assistant /root/apps/AI-e-commerce-assistant; do
  service="$(resolve_ai_runtime_service "$candidate_root" 2>/dev/null || true)"
  if [ -n "$service" ]; then
    DEPLOY_ROOT="$candidate_root"
    DEPLOY_SERVICE="$service"
    break
  fi
done
[ -n "$DEPLOY_ROOT" ] || fail "Could not resolve the active AI runtime root"
[ -n "$DEPLOY_SERVICE" ] || fail "Could not resolve the active AI runtime service"
DEPLOY_PORT="$(resolve_ai_runtime_port "$DEPLOY_ROOT" "$DEPLOY_SERVICE")"
[[ "$DEPLOY_PORT" =~ ^[0-9]+$ ]] || fail "Resolved runtime port is invalid"

CONFIG_PATH=""
CONFIG_HASH_BEFORE=""
for config_candidate in \
  "$DEPLOY_ROOT/shared/.env" \
  "$DEPLOY_ROOT/.env" \
  /etc/ai-ecommerce-assistant/qwen37-plus.env
do
  if [ -f "$config_candidate" ]; then
    CONFIG_PATH="$config_candidate"
    CONFIG_HASH_BEFORE="$(sha256sum "$CONFIG_PATH" | awk '{print $1}')"
    break
  fi
done

VERIFIER_TARGET="/usr/local/sbin/ai-release-verifier"
VERIFIER_HASH_FILE="/etc/ai-ecommerce-assistant/release-verifier.sha256"
CANDIDATE_VERIFIER_HASH="$(sha256sum "$CANDIDATE_DIR/scripts/release_verifier.py" | awk '{print $1}')"
TARGET_EXISTS=false
HASH_EXISTS=false
[ -e "$VERIFIER_TARGET" ] && TARGET_EXISTS=true
[ -e "$VERIFIER_HASH_FILE" ] && HASH_EXISTS=true
[ "$TARGET_EXISTS" = "$HASH_EXISTS" ] || fail "Pinned verifier state is incomplete"

if [ "$TARGET_EXISTS" = true ]; then
  OLD_HASH="$(tr -d '[:space:]' < "$VERIFIER_HASH_FILE")"
  ACTUAL_HASH="$(sha256sum "$VERIFIER_TARGET" | awk '{print $1}')"
  [ "$OLD_HASH" = "$ACTUAL_HASH" ] || fail "Existing root verifier hash record does not match installed verifier"
  if [ "$CANDIDATE_VERIFIER_HASH" != "$OLD_HASH" ]; then
    env \
      AI_RELEASE_PYTHON="$PYTHON" \
      AI_RELEASE_VERIFIER_ROTATE=1 \
      AI_RELEASE_VERIFIER_EXPECTED_OLD_SHA256="$OLD_HASH" \
      bash "$CANDIDATE_DIR/scripts/install_release_verifier.sh"
  else
    env AI_RELEASE_PYTHON="$PYTHON" \
      bash "$CANDIDATE_DIR/scripts/install_release_verifier.sh"
  fi
else
  env AI_RELEASE_PYTHON="$PYTHON" \
    bash "$CANDIDATE_DIR/scripts/install_release_verifier.sh"
fi

FINAL_VERIFIER_HASH="$(tr -d '[:space:]' < "$VERIFIER_HASH_FILE")"
[ "$FINAL_VERIFIER_HASH" = "$CANDIDATE_VERIFIER_HASH" ] || fail "Root verifier pin did not converge to the sealed candidate"

set +e
env \
  AI_ECOMMERCE_ROOT="$DEPLOY_ROOT" \
  AI_RELEASE_BUNDLE="$ROOT_BUNDLE" \
  AI_RELEASE_PYTHON="$PYTHON" \
  AI_RELEASE_KEEP_COUNT=2 \
  AI_ECOMMERCE_BACKUP_KEEP_COUNT=1 \
  bash "$CANDIDATE_DIR/scripts/deploy_release.sh" "$ROOT_BUNDLE" \
  2>&1 | tee "$RUN_ROOT/deploy-release.log"
DEPLOY_STATUS=${PIPESTATUS[0]}
set -e
[ "$DEPLOY_STATUS" -eq 0 ] || fail "Sealed candidate deployment failed with exit code $DEPLOY_STATUS"

DEPLOY_SERVICE="$(resolve_ai_runtime_service "$DEPLOY_ROOT")"
DEPLOY_PORT="$(resolve_ai_runtime_port "$DEPLOY_ROOT" "$DEPLOY_SERVICE")"
systemctl is-active --quiet "$DEPLOY_SERVICE" || fail "Resolved runtime service is not active after deployment"

curl --fail --silent --show-error --max-time 15 \
  "http://127.0.0.1:${DEPLOY_PORT}/api/health" \
  > "$RUN_ROOT/live-health.json"
curl --fail --silent --show-error --max-time 30 \
  "http://127.0.0.1:${DEPLOY_PORT}/api/system/release-identity?verifyContent=false" \
  > "$RUN_ROOT/live-release-identity.json"

"$PYTHON" - "$RUN_ROOT/live-release-identity.json" "$EXPECTED_SOURCE_COMMIT" <<'PY'
import json
import sys

identity = json.load(open(sys.argv[1], encoding="utf-8"))
expected = sys.argv[2]
checks = {
    "verified": identity.get("verified") is True,
    "sourceCommit": identity.get("sourceCommit") == expected,
    "workerReleaseMatch": identity.get("workerReleaseMatch") is True,
    "runtimeEnvironmentMatch": identity.get("runtimeEnvironmentMatch") is True,
    "evidenceSemanticVerified": identity.get("evidenceSemanticVerified") is True,
}
failed = [key for key, ok in checks.items() if not ok]
if failed:
    raise SystemExit("live release identity failed: " + ",".join(failed))
PY

MODEL_CONFIG_PRESERVED="not-hash-verifiable"
CONFIG_HASH_AFTER=""
if [ -n "$CONFIG_PATH" ]; then
  AFTER_PATH="$CONFIG_PATH"
  if [ ! -f "$AFTER_PATH" ] && [ -f "$DEPLOY_ROOT/shared/.env" ]; then
    AFTER_PATH="$DEPLOY_ROOT/shared/.env"
  fi
  [ -f "$AFTER_PATH" ] || fail "Model configuration disappeared during deployment"
  CONFIG_HASH_AFTER="$(sha256sum "$AFTER_PATH" | awk '{print $1}')"
  [ "$CONFIG_HASH_BEFORE" = "$CONFIG_HASH_AFTER" ] || fail "Model configuration hash changed during deployment"
  MODEL_CONFIG_PRESERVED="true"
fi

TEMP_RECEIPT="$(mktemp -d "$RECEIPT_ROOT/.${EXPECTED_SOURCE_COMMIT}.XXXXXXXX")"
cp "$RUN_ROOT/live-health.json" "$TEMP_RECEIPT/live-health.json"
cp "$RUN_ROOT/live-release-identity.json" "$TEMP_RECEIPT/live-release-identity.json"
cp "$RUN_ROOT/deploy-release.log" "$TEMP_RECEIPT/deploy-release.log"
cp "$RUN_ROOT/candidate-verification.json" "$TEMP_RECEIPT/candidate-verification.json"

"$PYTHON" - \
  "$TEMP_RECEIPT/controller-attestation.json" \
  "$EXPECTED_SOURCE_COMMIT" \
  "$EXPECTED_ARTIFACT_ZIP_SHA256" \
  "$ACTUAL_SELF_HASH" \
  "$DEPLOY_ROOT" \
  "$DEPLOY_SERVICE" \
  "$DEPLOY_PORT" \
  "$MODEL_CONFIG_PRESERVED" \
  "$CONFIG_HASH_BEFORE" \
  "$CONFIG_HASH_AFTER" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

out = pathlib.Path(sys.argv[1])
payload = {
    "schemaVersion": "competition-authorized-deploy-v2-controller/v1",
    "sourceCommit": sys.argv[2],
    "artifactZipSha256": "sha256:" + sys.argv[3],
    "controllerSha256": "sha256:" + sys.argv[4],
    "deployRoot": sys.argv[5],
    "service": sys.argv[6],
    "port": int(sys.argv[7]),
    "modelConfigPreserved": sys.argv[8],
    "modelConfigHashBefore": ("sha256:" + sys.argv[9]) if sys.argv[9] else None,
    "modelConfigHashAfter": ("sha256:" + sys.argv[10]) if sys.argv[10] else None,
    "extraQwenModelCallPerformed": False,
    "verifiedAt": datetime.now(timezone.utc).isoformat(),
}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

chmod 0644 "$TEMP_RECEIPT"/*
FINAL_RECEIPT="$RECEIPT_ROOT/$EXPECTED_SOURCE_COMMIT"
rm -rf "$FINAL_RECEIPT"
mv "$TEMP_RECEIPT" "$FINAL_RECEIPT"
chmod 0755 "$FINAL_RECEIPT"

printf 'AUTHORIZED_DEPLOYMENT_OK=true\n'
printf 'sourceCommit=%s\n' "$EXPECTED_SOURCE_COMMIT"
printf 'artifactZipSha256=sha256:%s\n' "$EXPECTED_ARTIFACT_ZIP_SHA256"
printf 'controllerSha256=sha256:%s\n' "$ACTUAL_SELF_HASH"
printf 'service=%s\nport=%s\n' "$DEPLOY_SERVICE" "$DEPLOY_PORT"
printf 'modelConfigPreserved=%s\n' "$MODEL_CONFIG_PRESERVED"
printf 'receiptDir=%s\n' "$FINAL_RECEIPT"
printf 'extraQwenModelCallPerformed=false\n'