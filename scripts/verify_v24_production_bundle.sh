#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${V24_VERIFY_PORT:-39025}"
BUILD_PYTHON="${V24_BUILD_PYTHON:-$ROOT_DIR/.venv/bin/python}"
LOG_FILE="${TMPDIR:-/tmp}/v24-production-authority-$$.log"
PID=""

cleanup() {
  if [ -n "$PID" ]; then
    kill "$PID" >/dev/null 2>&1 || true
    wait "$PID" >/dev/null 2>&1 || true
  fi
  rm -f "$LOG_FILE"
}
trap cleanup EXIT INT TERM

V24_JAVA_HOME="${V24_JAVA_HOME:-${JAVA_HOME:-}}" V24_SOURCE_COMMIT="${V24_SOURCE_COMMIT:?V24_SOURCE_COMMIT is required}" V24_BUILD_PYTHON="$BUILD_PYTHON" bash "$ROOT_DIR/scripts/build_v24_production_bundle.sh"

JRE="$ROOT_DIR/runtime/java/jre/bin/java"
JAR="$ROOT_DIR/runtime/java/v24-production-authority.jar"
CONTRACT="$ROOT_DIR/runtime/java/runtime-contract.json"

[ -x "$JRE" ]
[ -s "$JAR" ]
[ -s "$CONTRACT" ]

# V26.3 stays inside the existing sealed Java authority bundle. This executable proof
# must pass before the production readiness service is even started, so an invalid
# pre-Agent admission contract cannot enter the Release Seal path.
"$JRE" -cp "$JAR" com.zcentury.v24.V263PreAgentAdmissionMain
printf 'V26_3_PRE_AGENT_ADMISSION_GATE=PASS\n'

# V26.4 extends the same sealed Java authority surface through post-observation System
# Review. Clear success must settle without Agent compute; only deterministic breach or
# non-deterministic legacy review criteria may re-enter Agent1.
"$JRE" -cp "$JAR" com.zcentury.v24.V264SystemReviewMain
printf 'V26_4_SYSTEM_REVIEW_GATE=PASS\n'

V24_AUTHORITY_MODE=READY_NO_AUTHORITY V24_AUTHORITY_HOST=127.0.0.1 V24_AUTHORITY_PORT="$PORT" "$JRE"   -Xms64m   -Xmx256m   -XX:MaxMetaspaceSize=128m   -XX:ActiveProcessorCount=2   -jar "$JAR" >"$LOG_FILE" 2>&1 &
PID=$!

READY=0
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error "http://127.0.0.1:$PORT/readyz"       >"${TMPDIR:-/tmp}/v24-ready-$$.json"; then
    READY=1
    break
  fi
  sleep 1
done
[ "$READY" = "1" ] || {
  cat "$LOG_FILE" >&2 || true
  exit 1
}

ROOT_DIR="$ROOT_DIR" PORT="$PORT" V24_SOURCE_COMMIT="$V24_SOURCE_COMMIT" "$BUILD_PYTHON" - <<'PY'
import json
import os
import urllib.request
from pathlib import Path

root = Path(os.environ["ROOT_DIR"])
port = os.environ["PORT"]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/authority/status", timeout=5) as response:
    status = json.load(response)
contract = json.loads((root / "runtime/java/runtime-contract.json").read_text(encoding="utf-8"))

assert status["schema"] == "v24.production-authority.status.v1"
assert status["version"] == "24.21.0"
assert status["mode"] == "READY_NO_AUTHORITY"
assert status["ready"] is True
assert status["authorityGeneration"] is None
assert status["productionMutationAllowed"] is False
assert status["deploymentAuthorityTransferAllowed"] is False
assert status["legacyRemovalAllowed"] is False
assert len(status["candidateAuthorities"]) == 5
assert set(status["owners"].values()) <= {
    "PYTHON_BASH_PRODUCTION",
    "PYTHON_PRODUCTION",
    "BASH_SYSTEMD_PRODUCTION",
    "DISABLED",
}
assert contract["javaRuntimeVersion"] == "17.0.20.1+1"
assert contract["enforcementMode"] == "READY_NO_AUTHORITY"
assert contract["productionMutationAllowed"] is False
assert contract["sourceCommit"] == os.environ["V24_SOURCE_COMMIT"]
print(json.dumps({
    "schema": "v24.production-authority-bundle.verification.v1",
    "verified": True,
    "mode": status["mode"],
    "candidateAuthorityCount": len(status["candidateAuthorities"]),
    "runtimeTreeSha256": contract["runtimeTreeSha256"],
    "jarSha256": contract["jarSha256"],
}, sort_keys=True))
PY

printf 'V24_PRODUCTION_BUNDLE_GATE=PASS\n'